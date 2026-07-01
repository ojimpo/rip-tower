"""Tests for the CDDB (GnuDB) metadata source.

Locks in CDDB protocol handling: single-exact-match (200) responses,
multi-match lists (210/211), continuation-line concatenation, and that
requests go to the configured gnudb_url.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.metadata.sources.cddb import CddbSource


def _identity(track_count: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        disc_id="2e0c3d04",
        track_count=track_count,
        offsets=[150, 20000, 40000],
        leadout=3100,
    )


# ─────────────────────── _parse_query_response ───────────────────────


def test_query_single_exact_match_code_200():
    """A 200 response carries the match inline on the status line."""
    resp = "200 rock 2e0c3d04 Some Artist / Some Album\n"
    matches = CddbSource._parse_query_response(resp, "2e0c3d04")
    assert matches == [("rock", "2e0c3d04")]


def test_query_multi_match_code_211():
    resp = (
        "211 Found inexact matches, list follows (until terminating `.')\n"
        "rock 2e0c3d04 Artist A / Album A\n"
        "misc 2e0c3d05 Artist B / Album B\n"
        ".\n"
    )
    matches = CddbSource._parse_query_response(resp, "2e0c3d04")
    assert matches == [("rock", "2e0c3d04"), ("misc", "2e0c3d05")]


def test_query_no_match_code_202():
    assert CddbSource._parse_query_response("202 No match found\n", "x") == []


def test_query_error_response_returns_empty():
    assert CddbSource._parse_query_response("500 Server error\n", "x") == []
    assert CddbSource._parse_query_response("", "x") == []


# ─────────────────────── _parse_read_response ───────────────────────


def test_read_parses_basic_record():
    resp = (
        "210 rock 2e0c3d04 CD database entry follows (until terminating `.')\n"
        "DISCID=2e0c3d04\n"
        "DTITLE=Some Artist / Some Album\n"
        "DYEAR=2001\n"
        "DGENRE=Rock\n"
        "TTITLE0=Track One\n"
        "TTITLE1=Track Two\n"
        "TTITLE2=Track Three\n"
        ".\n"
    )
    c = CddbSource._parse_read_response(resp, "rock", "2e0c3d04", 3)
    assert c is not None
    assert c["artist"] == "Some Artist"
    assert c["album"] == "Some Album"
    assert c["year"] == "2001"
    assert c["genre"] == "Rock"
    assert json.loads(c["track_titles"]) == ["Track One", "Track Two", "Track Three"]


def test_read_concatenates_continuation_lines():
    """Long DTITLE/TTITLE values are split into repeated KEY= lines that
    must be concatenated — not treated as extra tracks."""
    resp = (
        "210 rock 2e0c3d04\n"
        "DTITLE=Some Artist / A Very Long\n"
        "DTITLE= Album Title\n"
        "TTITLE0=First Half Of A Long\n"
        "TTITLE0= Track Title\n"
        "TTITLE1=Second Track\n"
        ".\n"
    )
    c = CddbSource._parse_read_response(resp, "rock", "2e0c3d04", 2)
    assert c is not None
    assert c["artist"] == "Some Artist"
    assert c["album"] == "A Very Long Album Title"
    assert json.loads(c["track_titles"]) == [
        "First Half Of A Long Track Title",
        "Second Track",
    ]


def test_read_rejects_non_210_response():
    assert CddbSource._parse_read_response("401 Entry not found\n", "rock", "x", 3) is None


# ─────────────────────────── search() ───────────────────────────


@pytest.mark.asyncio
async def test_search_follows_single_exact_match(monkeypatch):
    requests: list[str] = []

    async def fake_request(self, cmd: str) -> str:
        requests.append(cmd)
        if cmd.startswith("cddb query"):
            return "200 rock 2e0c3d04 Some Artist / Some Album\n"
        return (
            "210 rock 2e0c3d04\n"
            "DTITLE=Some Artist / Some Album\n"
            "TTITLE0=One\nTTITLE1=Two\nTTITLE2=Three\n"
            ".\n"
        )

    monkeypatch.setattr(CddbSource, "_cddb_request", fake_request)
    candidates = await CddbSource().search(_identity())

    assert len(candidates) == 1
    assert candidates[0]["artist"] == "Some Artist"
    assert json.loads(candidates[0]["track_titles"]) == ["One", "Two", "Three"]
    assert requests[0].startswith("cddb query 2e0c3d04 3 150 20000 40000 3100")
    assert requests[1] == "cddb read rock 2e0c3d04"


@pytest.mark.asyncio
async def test_search_returns_candidate_per_match(monkeypatch):
    async def fake_request(self, cmd: str) -> str:
        if cmd.startswith("cddb query"):
            return (
                "211 matches follow\n"
                "rock 2e0c3d04 A / X\n"
                "misc 2e0c3d05 B / Y\n"
                ".\n"
            )
        cat = cmd.split(" ")[2]
        return (
            f"210 {cat} discid\n"
            f"DTITLE={'A / X' if cat == 'rock' else 'B / Y'}\n"
            "TTITLE0=t\n.\n"
        )

    monkeypatch.setattr(CddbSource, "_cddb_request", fake_request)
    candidates = await CddbSource().search(_identity(track_count=1))
    assert [(c["artist"], c["album"]) for c in candidates] == [("A", "X"), ("B", "Y")]


@pytest.mark.asyncio
async def test_cddb_request_hits_configured_gnudb_url(monkeypatch):
    """The CDDB endpoint comes from config (GnuDB needs the http scheme)."""
    from backend.metadata.sources import cddb as cddb_mod

    seen: dict = {}

    class _Resp:
        status_code = 200
        content = b"202 no match\n"

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            seen["url"] = url
            return _Resp()

    monkeypatch.setattr(cddb_mod.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        cddb_mod,
        "get_config",
        lambda: SimpleNamespace(
            integrations=SimpleNamespace(gnudb_url="http://gnudb.example")
        ),
    )

    candidates = await CddbSource().search(_identity())
    assert candidates == []
    assert seen["url"].startswith("http://gnudb.example/~cddb/cddb.cgi?")
