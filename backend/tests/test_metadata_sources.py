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
