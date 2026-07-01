"""Tests for the lyrics module's LRCLIB search-fallback validation."""

from __future__ import annotations

import pytest

from backend.metadata import lyrics


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url, params=None):
        return _Resp(200, self._payload)


@pytest.mark.asyncio
async def test_lrclib_search_skips_unrelated_first_hit():
    """The fuzzy search's first hit is a different song — it must be skipped
    in favor of the result that actually matches artist+title."""
    client = _Client([
        {
            "artistName": "Wrong Artist",
            "trackName": "Different Song",
            "plainLyrics": "wrong lyrics",
        },
        {
            "artistName": "米津玄師",
            "trackName": "Lemon",
            "plainLyrics": "correct lyrics",
            "syncedLyrics": None,
        },
    ])
    synced, plain = await lyrics._lrclib_search(client, "米津玄師", "Lemon")
    assert plain == "correct lyrics"
    assert synced is None


@pytest.mark.asyncio
async def test_lrclib_search_returns_none_when_nothing_matches():
    client = _Client([
        {"artistName": "Wrong", "trackName": "Nope", "plainLyrics": "x"},
    ])
    synced, plain = await lyrics._lrclib_search(client, "米津玄師", "Lemon")
    assert synced is None and plain is None


@pytest.mark.asyncio
async def test_lrclib_search_accepts_title_with_tieup_suffix():
    """Containment (canonical title inside an annotated one) still matches."""
    client = _Client([
        {
            "artistName": "Mr.Children",
            "trackName": "himawari (映画「君の膵臓をたべたい」主題歌)",
            "plainLyrics": "lyrics",
        },
    ])
    synced, plain = await lyrics._lrclib_search(client, "Mr.Children", "himawari")
    assert plain == "lyrics"
