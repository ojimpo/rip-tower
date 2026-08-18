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


# ─────────────────────── TOC verification ───────────────────────


def _read_resp_with_offsets(offsets: list[int]) -> str:
    offset_lines = "".join(f"#\t{o}\n" for o in offsets)
    return (
        "210 rock 2e0c3d04\n"
        "# xmcd\n"
        "#\n"
        "# Track frame offsets:\n"
        f"{offset_lines}"
        "#\n"
        "# Disc length: 3100 seconds\n"
        "#\n"
        "DTITLE=Some Artist / Some Album\n"
        "TTITLE0=One\nTTITLE1=Two\nTTITLE2=Three\n"
        ".\n"
    )


def test_read_frame_exact_offsets_get_verified_confidence():
    """A record whose frame offsets equal the disc's TOC is proven by the
    disc itself — it must outrank MB's fuzzy TOC matches (conf 90)."""
    resp = _read_resp_with_offsets([150, 20000, 40000])
    c = CddbSource._parse_read_response(
        resp, "rock", "2e0c3d04", 3, disc_offsets=[150, 20000, 40000]
    )
    assert c is not None
    assert c["confidence"] == 92
    ev = json.loads(c["evidence"])
    assert ev["match"] == "cddb_exact"
    assert ev["toc_verified"] is True


def test_read_uniform_leadin_shift_still_verified():
    """Different drives report different lead-in gaps; a constant shift
    across every track is still the same disc."""
    resp = _read_resp_with_offsets([150, 20000, 40000])
    c = CddbSource._parse_read_response(
        resp, "rock", "2e0c3d04", 3, disc_offsets=[332, 20182, 40182]
    )
    assert c is not None
    assert c["confidence"] == 92


def test_read_diverging_offsets_keep_baseline_confidence():
    """A disc-ID collision (same discid, wildly different offsets) must not
    get the verified boost."""
    resp = _read_resp_with_offsets([150, 20000, 40000])
    c = CddbSource._parse_read_response(
        resp, "rock", "2e0c3d04", 3, disc_offsets=[150, 24000, 47000]
    )
    assert c is not None
    assert c["confidence"] == 60
    assert "match" not in json.loads(c["evidence"])


def test_read_without_offset_header_keeps_baseline_confidence():
    resp = (
        "210 rock 2e0c3d04\n"
        "DTITLE=Some Artist / Some Album\n"
        "TTITLE0=One\nTTITLE1=Two\nTTITLE2=Three\n"
        ".\n"
    )
    c = CddbSource._parse_read_response(
        resp, "rock", "2e0c3d04", 3, disc_offsets=[150, 20000, 40000]
    )
    assert c is not None
    assert c["confidence"] == 60


def test_parse_frame_offsets_stops_at_end_of_block():
    lines = _read_resp_with_offsets([150, 20000, 40000]).splitlines()
    assert CddbSource._parse_frame_offsets(lines) == [150, 20000, 40000]


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
async def test_search_verifies_toc_against_physical_disc(monkeypatch):
    """search() feeds the identity's offsets into read parsing so a record
    that frame-matches the disc comes back at verified confidence."""

    async def fake_request(self, cmd: str) -> str:
        if cmd.startswith("cddb query"):
            return "200 rock 2e0c3d04 Some Artist / Some Album\n"
        return _read_resp_with_offsets([150, 20000, 40000])

    monkeypatch.setattr(CddbSource, "_cddb_request", fake_request)
    candidates = await CddbSource().search(_identity())

    assert len(candidates) == 1
    assert candidates[0]["confidence"] == 92
    assert json.loads(candidates[0]["evidence"])["match"] == "cddb_exact"


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
