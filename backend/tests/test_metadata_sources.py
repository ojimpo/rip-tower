"""Tests for individual metadata sources (iTunes, MusicBrainz text search, Discogs).

Mocks httpx.AsyncClient.get to avoid network and to lock in the response
parsing & disc-selection logic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.metadata.sources.discogs import DiscogsSource
from backend.metadata.sources.itunes import ItunesSource
from backend.metadata.sources.musicbrainz import MusicBrainzSource


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _MockClient:
    """Drop-in for httpx.AsyncClient that serves canned responses by URL prefix."""

    def __init__(self, responses: dict, **kwargs):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        # Longest-prefix-first so e.g. ".../release/rel-1" beats ".../release/"
        for prefix in sorted(self._responses, key=len, reverse=True):
            if url.startswith(prefix):
                resp = self._responses[prefix]
                if callable(resp):
                    return resp(url, params)
                return resp
        return _Resp(404, {})


def _patch_httpx(monkeypatch, module, responses):
    def _factory(**kwargs):
        return _MockClient(responses)
    monkeypatch.setattr(module.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)


async def _no_sleep(_):
    return None


# ─────────────────────────── iTunes ───────────────────────────


@pytest.mark.asyncio
async def test_itunes_fetches_track_listing_and_picks_target_disc(monkeypatch):
    from backend.metadata.sources import itunes as itunes_mod

    search_resp = _Resp(200, {"results": [{
        "wrapperType": "collection",
        "collectionId": 12345,
        "collectionName": "Test Album",
        "artistName": "Test Artist",
        "primaryGenreName": "Pop",
        "releaseDate": "2022-05-10T00:00:00Z",
        "trackCount": 14,
        "artworkUrl100": "https://example/100x100.jpg",
    }]})
    lookup_resp = _Resp(200, {"results": [
        {"wrapperType": "collection", "collectionName": "Test Album"},
        {"wrapperType": "track", "discNumber": 1, "trackNumber": 1, "trackName": "T1"},
        {"wrapperType": "track", "discNumber": 1, "trackNumber": 2, "trackName": "T2"},
        {"wrapperType": "track", "discNumber": 2, "trackNumber": 1, "trackName": "L1"},
        {"wrapperType": "track", "discNumber": 2, "trackNumber": 2, "trackName": "L2"},
    ]})

    _patch_httpx(monkeypatch, itunes_mod, {
        "https://itunes.apple.com/search": search_resp,
        "https://itunes.apple.com/lookup": lookup_resp,
    })

    src = ItunesSource()
    identity = SimpleNamespace(track_count=2)
    candidates = await src.search(identity, hints={"artist": "Test Artist", "title": "Test Album", "disc_number": 2})

    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    assert titles == ["L1", "L2"]
    # Base 30 + track-listing match 15 = 45 (collection trackCount mismatch is fine
    # — real iTunes data reports total tracks across all discs)
    assert candidates[0]["confidence"] == 45


@pytest.mark.asyncio
async def test_itunes_falls_back_to_disc1_when_target_missing(monkeypatch):
    from backend.metadata.sources import itunes as itunes_mod

    search_resp = _Resp(200, {"results": [{
        "wrapperType": "collection",
        "collectionId": 1,
        "collectionName": "A",
        "artistName": "X",
        "trackCount": 2,
    }]})
    lookup_resp = _Resp(200, {"results": [
        {"wrapperType": "track", "discNumber": 1, "trackNumber": 1, "trackName": "X1"},
        {"wrapperType": "track", "discNumber": 1, "trackNumber": 2, "trackName": "X2"},
    ]})
    _patch_httpx(monkeypatch, itunes_mod, {
        "https://itunes.apple.com/search": search_resp,
        "https://itunes.apple.com/lookup": lookup_resp,
    })

    src = ItunesSource()
    identity = SimpleNamespace(track_count=2)
    candidates = await src.search(identity, hints={"artist": "X", "title": "A", "disc_number": 5})
    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    assert titles == ["X1", "X2"]


@pytest.mark.asyncio
async def test_itunes_empty_hints_returns_nothing(monkeypatch):
    src = ItunesSource()
    candidates = await src.search(SimpleNamespace(track_count=10), hints=None)
    assert candidates == []


# ─────────────────────── MusicBrainz text search ──────────────────────


@pytest.mark.asyncio
async def test_mb_text_search_fetches_tracks_for_top_releases(monkeypatch):
    from backend.metadata.sources import musicbrainz as mb_mod

    search_resp = _Resp(200, {"releases": [
        {
            "id": "rel-1",
            "title": "Album A",
            "artist-credit": [{"name": "Artist A"}],
            "media": [{"track-count": 14}, {"track-count": 13}],
            "date": "2022",
        },
    ]})
    detail_resp = _Resp(200, {
        "title": "Album A",
        "media": [
            {
                "format": "CD",
                "position": 1,
                "track-count": 14,
                "tracks": [
                    {"recording": {"title": f"S{i}"}} for i in range(1, 15)
                ],
            },
            {
                "format": "CD",
                "position": 2,
                "track-count": 13,
                "tracks": [
                    {"recording": {"title": f"L{i}"}} for i in range(1, 14)
                ],
            },
        ],
    })
    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/release/rel-1": detail_resp,
        "https://musicbrainz.org/ws/2/release/": search_resp,
    })

    src = MusicBrainzSource(mode="text_search")
    identity = SimpleNamespace(disc_id=None, track_count=13)
    candidates = await src.search(identity, hints={"title": "Album A", "artist": "Artist A"})

    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    # Should pick disc 2 because track_count matches 13
    assert titles == [f"L{i}" for i in range(1, 14)]
    assert candidates[0]["disc_number"] == 2
    assert candidates[0]["total_discs"] == 2


@pytest.mark.asyncio
async def test_mb_text_search_tiebreaks_same_track_count_by_duration(monkeypatch):
    """A 2-disc set where both discs have the same track count must pick the
    medium whose total duration is closest to the physical disc's leadout —
    otherwise both discs of e.g. 中島みゆき's Singles I/II get tagged as disc 1.
    """
    from backend.metadata.sources import musicbrainz as mb_mod

    # Both media have 10 tracks — only durations distinguish them.
    # Disc 1: ~3120s total, Disc 2: ~3500s total.
    disc1_tracks = [
        {"recording": {"title": f"D1-{i}"}, "length": 312_000} for i in range(1, 11)
    ]
    disc2_tracks = [
        {"recording": {"title": f"D2-{i}"}, "length": 350_000} for i in range(1, 11)
    ]
    search_resp = _Resp(200, {"releases": [
        {
            "id": "rel-x",
            "title": "Singles II",
            "artist-credit": [{"name": "Artist"}],
            "media": [{"track-count": 10}, {"track-count": 10}],
            "date": "1994",
        },
    ]})
    detail_resp = _Resp(200, {
        "title": "Singles II",
        "media": [
            {"format": "CD", "position": 1, "track-count": 10, "tracks": disc1_tracks},
            {"format": "CD", "position": 2, "track-count": 10, "tracks": disc2_tracks},
        ],
    })
    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/release/rel-x": detail_resp,
        "https://musicbrainz.org/ws/2/release/": search_resp,
    })

    src = MusicBrainzSource(mode="text_search")
    # Physical disc 2: leadout ~3502s — should match disc 2, not disc 1
    identity = SimpleNamespace(disc_id=None, track_count=10, total_seconds=3502)
    candidates = await src.search(
        identity, hints={"title": "Singles II", "artist": "Artist"},
    )

    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    assert titles == [f"D2-{i}" for i in range(1, 11)]
    assert candidates[0]["disc_number"] == 2


@pytest.mark.asyncio
async def test_mb_disc_id_mode_uses_toc_submission(monkeypatch):
    """cd-discid produces a CDDB hex disc ID, not a MusicBrainz one — the
    /discid/{id} endpoint rejects it with HTTP 400. We fall back to
    /discid/-?toc=... TOC submission, which is what /api/drives/identify
    already uses successfully. Verify we hit the TOC endpoint and parse
    the response into candidates."""
    from backend.metadata.sources import musicbrainz as mb_mod

    captured: dict = {}

    def toc_responder(url, params):
        captured["url"] = url
        captured["params"] = params
        return _Resp(200, {"releases": [{
            "id": "rel-toc",
            "title": "Some Album",
            "artist-credit": [{"name": "Some Artist"}],
            "date": "2024",
            "media": [{
                "format": "CD",
                "position": 1,
                "track-count": 10,
                "tracks": [
                    {"recording": {"title": f"T{i}"}, "length": 200_000}
                    for i in range(1, 11)
                ],
            }],
        }]})

    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/discid/-": toc_responder,
    })

    src = MusicBrainzSource(mode="disc_id")
    identity = SimpleNamespace(
        disc_id="9d0c2e0a",  # CDDB hex — would 400 on /discid/{id}
        track_count=10,
        offsets=[150, 18000, 36000, 54000, 72000, 90000, 108000, 126000, 144000, 162000],
        leadout=2400,  # seconds
        total_seconds=2400,
    )
    candidates = await src.search(identity, hints=None)

    assert candidates, "expected one MB candidate from TOC submission"
    assert "/ws/2/discid/-" in captured["url"]
    # leadout in MB TOC must be sectors (75/sec), not seconds
    assert "180000" in captured["params"]["toc"]  # 2400 * 75
    assert candidates[0]["artist"] == "Some Artist"
    assert candidates[0]["album"] == "Some Album"
    titles = json.loads(candidates[0]["track_titles"])
    assert titles == [f"T{i}" for i in range(1, 11)]
    evidence = json.loads(candidates[0]["evidence"])
    assert evidence["match"] == "toc_submission"


@pytest.mark.asyncio
async def test_mb_disc_id_excludes_dvd_video_from_total_discs(monkeypatch):
    """A release with 2 CDs + 1 DVD-Video must report total_discs=2, not 3.

    Real case: MB release 20e53edf-…-e6113087c559 has disc1=SHM-CD,
    disc2=SHM-CD, disc3=DVD-Video. The bug was the format filter
    matching only exact "CD", falling back to "all media" when SHM-CD
    didn't match, then counting the DVD-Video bonus disc.
    """
    from backend.metadata.sources import musicbrainz as mb_mod

    def toc_responder(url, params):
        return _Resp(200, {"releases": [{
            "id": "rel-mixed",
            "title": "Mixed Media Release",
            "artist-credit": [{"name": "Some Artist"}],
            "date": "2010",
            "media": [
                {
                    "format": "SHM-CD",
                    "position": 1,
                    "track-count": 10,
                    "tracks": [{"recording": {"title": f"D1-{i}"}} for i in range(1, 11)],
                },
                {
                    "format": "SHM-CD",
                    "position": 2,
                    "track-count": 10,
                    "tracks": [{"recording": {"title": f"D2-{i}"}} for i in range(1, 11)],
                },
                {
                    "format": "DVD-Video",
                    "position": 3,
                    "track-count": 1,
                    "tracks": [{"recording": {"title": "Bonus Footage"}}],
                },
            ],
        }]})

    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/discid/-": toc_responder,
    })

    src = MusicBrainzSource(mode="disc_id")
    identity = SimpleNamespace(
        disc_id="abc12345",
        track_count=10,
        offsets=[150] * 10,
        leadout=2400,
        total_seconds=2400,
    )
    candidates = await src.search(identity, hints=None)

    assert candidates
    assert candidates[0]["total_discs"] == 2, (
        f"DVD-Video bonus disc should not count toward total_discs "
        f"(got {candidates[0]['total_discs']})"
    )


def test_track_entries_va_release_prefixes_artists():
    """A release credited to Various Artists emits "artist / title" per track."""
    from backend.metadata.sources.musicbrainz import _track_entries

    tracks = [
        {"recording": {"title": "Song A"}, "artist-credit": [{"name": "平沢進"}]},
        {"recording": {"title": "Song B"}, "artist-credit": [{"name": "Other Artist"}]},
    ]
    assert _track_entries(tracks, "Various Artists") == [
        "平沢進 / Song A",
        "Other Artist / Song B",
    ]


def test_track_entries_multi_artist_without_dominant_is_compilation():
    """No single performer reaches 60% → treat as compilation even if release
    isn't explicitly credited to Various."""
    from backend.metadata.sources.musicbrainz import _track_entries

    tracks = [
        {"recording": {"title": "T1"}, "artist-credit": [{"name": "A"}]},
        {"recording": {"title": "T2"}, "artist-credit": [{"name": "B"}]},
        {"recording": {"title": "T3"}, "artist-credit": [{"name": "C"}]},
    ]
    assert _track_entries(tracks, "Some Comp Host") == ["A / T1", "B / T2", "C / T3"]


def test_track_entries_single_artist_album_plain():
    """A normal single-artist album returns plain titles (no compilation flip)."""
    from backend.metadata.sources.musicbrainz import _track_entries

    tracks = [
        {"recording": {"title": "T1"}, "artist-credit": [{"name": "平沢進"}]},
        {"recording": {"title": "T2"}, "artist-credit": [{"name": "平沢進"}]},
    ]
    assert _track_entries(tracks, "平沢進") == ["T1", "T2"]


def test_track_entries_lone_guest_does_not_flip_album():
    """One guest feature on an otherwise single-artist album stays a plain album."""
    from backend.metadata.sources.musicbrainz import _track_entries

    tracks = [{"recording": {"title": f"T{i}"}, "artist-credit": [{"name": "平沢進"}]}
              for i in range(9)]
    tracks.append({"recording": {"title": "T9"}, "artist-credit": [{"name": "Guest"}]})
    entries = _track_entries(tracks, "平沢進")
    assert entries == [f"T{i}" for i in range(9)] + ["T9"]


def test_track_entries_joins_collaboration_with_joinphrase():
    from backend.metadata.sources.musicbrainz import _join_artist_credit

    ac = [{"name": "A", "joinphrase": " feat. "}, {"name": "B", "joinphrase": ""}]
    assert _join_artist_credit(ac) == "A feat. B"


@pytest.mark.asyncio
async def test_mb_disc_id_va_compilation_captures_track_artists(monkeypatch):
    """A Various-Artists disc must come back with per-track artists embedded as
    "artist / title" so the sanitizer tags ALBUMARTIST=Various Artists rather
    than the first contributing performer (Todoist 6gp628r73WQ8576F)."""
    from backend.metadata.sources import musicbrainz as mb_mod

    def toc_responder(url, params):
        return _Resp(200, {"releases": [{
            "id": "rel-va",
            "title": "スナックJUJU",
            "artist-credit": [{"name": "Various Artists"}],
            "date": "2019",
            "media": [{
                "format": "CD",
                "position": 1,
                "track-count": 3,
                "tracks": [
                    {"recording": {"title": "T1"}, "artist-credit": [{"name": "平沢進"}]},
                    {"recording": {"title": "T2"}, "artist-credit": [{"name": "JUJU"}]},
                    {"recording": {"title": "T3"}, "artist-credit": [{"name": "椎名林檎"}]},
                ],
            }],
        }]})

    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/discid/-": toc_responder,
    })

    src = MusicBrainzSource(mode="disc_id")
    identity = SimpleNamespace(
        disc_id="e60dda0f", track_count=3,
        offsets=[150, 18000, 36000], leadout=2400, total_seconds=2400,
    )
    candidates = await src.search(identity, hints=None)

    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    assert titles == ["平沢進 / T1", "JUJU / T2", "椎名林檎 / T3"]


@pytest.mark.asyncio
async def test_mb_disc_id_mode_no_offsets_returns_empty(monkeypatch):
    """Restored identities from older jobs may lack offsets/leadout; without
    them we can't synthesize a TOC, so skip cleanly rather than 400 the API."""
    from backend.metadata.sources import musicbrainz as mb_mod

    def fail_if_called(url, params):
        raise AssertionError(f"should not have hit MB: {url}")

    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/discid/-": fail_if_called,
    })

    src = MusicBrainzSource(mode="disc_id")
    identity = SimpleNamespace(
        disc_id="abc", track_count=10, offsets=[], leadout=0, total_seconds=0,
    )
    candidates = await src.search(identity, hints={"title": "X", "artist": "Y"})
    assert candidates == []


@pytest.mark.asyncio
async def test_mb_disc_id_mode_skips_text_search(monkeypatch):
    from backend.metadata.sources import musicbrainz as mb_mod

    discid_resp = _Resp(404, {})
    _patch_httpx(monkeypatch, mb_mod, {
        "https://musicbrainz.org/ws/2/discid/": discid_resp,
    })

    src = MusicBrainzSource(mode="disc_id")
    identity = SimpleNamespace(disc_id="abc", track_count=10)
    # Text search hints should be ignored — mode is disc_id only
    candidates = await src.search(identity, hints={"title": "A", "artist": "B"})
    assert candidates == []


# ─────────────────────────── Discogs ───────────────────────────


@pytest.mark.asyncio
async def test_discogs_fetches_tracklist_and_picks_target_disc(monkeypatch):
    from backend.metadata.sources import discogs as discogs_mod

    # Force token to be set
    cfg = SimpleNamespace(integrations=SimpleNamespace(discogs_token="dummy"))
    monkeypatch.setattr(discogs_mod, "get_config", lambda: cfg)

    search_resp = _Resp(200, {"results": [{
        "id": 999,
        "title": "Artist A - Album B",
        "label": [{"catno": "ABC-123"}],
        "year": "2022",
        "resource_url": "https://api.discogs.com/releases/999",
    }]})
    detail_resp = _Resp(200, {
        "tracklist": [
            {"type_": "heading", "position": "", "title": "CD 1"},
            {"type_": "track", "position": "1-1", "title": "S1"},
            {"type_": "track", "position": "1-2", "title": "S2"},
            {"type_": "heading", "position": "", "title": "CD 2"},
            {"type_": "track", "position": "2-1", "title": "L1"},
            {"type_": "track", "position": "2-2", "title": "L2"},
            {"type_": "track", "position": "2-3", "title": "L3"},
        ],
    })
    _patch_httpx(monkeypatch, discogs_mod, {
        "https://api.discogs.com/database/search": search_resp,
        "https://api.discogs.com/releases/999": detail_resp,
    })

    src = DiscogsSource()
    identity = SimpleNamespace(track_count=3)
    candidates = await src.search(identity, hints={"title": "Album B", "artist": "Artist A"})

    assert candidates
    titles = json.loads(candidates[0]["track_titles"])
    # Track count 3 → disc 2 wins
    assert titles == ["L1", "L2", "L3"]
    assert candidates[0]["disc_number"] == 2
    assert candidates[0]["total_discs"] == 2


@pytest.mark.asyncio
async def test_discogs_no_token_returns_empty(monkeypatch):
    from backend.metadata.sources import discogs as discogs_mod

    cfg = SimpleNamespace(integrations=SimpleNamespace(discogs_token=""))
    monkeypatch.setattr(discogs_mod, "get_config", lambda: cfg)

    src = DiscogsSource()
    candidates = await src.search(SimpleNamespace(track_count=10), hints={"title": "x"})
    assert candidates == []
