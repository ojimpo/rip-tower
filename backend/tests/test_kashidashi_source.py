"""Tests for KashidashiSource — focused on the Phase 1 recency fallback.

The exact-discid and fuzzy paths are exercised indirectly elsewhere; these
tests pin the new behavior where hint-less, candidate-less searches still
surface a recently-borrowed unripped item as a hint for Phase 2.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from backend.metadata.sources import kashidashi as kashidashi_mod
from backend.metadata.sources.kashidashi import (
    KashidashiSource,
    _recency_fallback_candidates,
)


def _item(**overrides):
    base = {
        "id": 1,
        "artist": "L'Arc～en～Ciel",
        "title": "25th L'Anniversary LIVE",
        "metadata_artist": None,
        "metadata_album": None,
        "metadata_track_count": None,
        "rip_discid": None,
        "borrowed_date": datetime.now(timezone.utc).date().isoformat(),
        "returned_at": None,
        "ripped_at": None,
    }
    base.update(overrides)
    return base


def test_recency_fallback_single_item_today():
    items = [_item()]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert len(out) == 1
    # pool_size=1 (base 70) + same-day (+5) = 75
    assert out[0]["confidence"] == 75
    assert out[0]["artist"] == "L'Arc～en～Ciel"
    assert out[0]["album"] == "25th L'Anniversary LIVE"


def test_recency_fallback_pool_size_lowers_confidence():
    today = datetime.now(timezone.utc).date().isoformat()
    items = [_item(id=i, borrowed_date=today) for i in range(1, 6)]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    # 5 items, pool_size > 3 → base 50, all same-day (+5) → 55
    assert len(out) == 5
    assert all(c["confidence"] == 55 for c in out)


def test_recency_fallback_track_count_match_boosts():
    items = [_item(metadata_track_count=11)]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    # 70 (pool=1) + 5 (track_count) + 5 (same-day) = 80
    assert out[0]["confidence"] == 80


def test_recency_fallback_skips_returned_and_ripped():
    items = [
        _item(id=1, returned_at="2026-05-10T00:00:00Z"),
        _item(id=2, ripped_at="2026-05-10T00:00:00Z"),
    ]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert out == []


def test_recency_fallback_skips_blank_items():
    items = [_item(artist=None, title=None, metadata_artist=None, metadata_album=None)]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert out == []


def test_recency_fallback_skips_outside_window():
    old = (datetime.now(timezone.utc).date().replace(day=1)).isoformat()
    # An item borrowed long ago shouldn't appear. Use 2020-01-01 to be safe.
    items = [_item(borrowed_date="2020-01-01")]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert out == []


def test_recency_fallback_uses_metadata_fields_when_present():
    items = [_item(
        artist="Old Artist",
        title="Old Title",
        metadata_artist="Corrected Artist",
        metadata_album="Corrected Album",
    )]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert out[0]["artist"] == "Corrected Artist"
    assert out[0]["album"] == "Corrected Album"


def test_recency_fallback_handles_invalid_borrowed_date():
    items = [_item(borrowed_date="not-a-date")]
    out = _recency_fallback_candidates(items, "http://k", track_count=11)
    assert out == []


# ───────── integration with KashidashiSource.search() ─────────


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _MockClient:
    def __init__(self, payload, **kwargs):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        return _Resp(200, self._payload)


def _patch_kashidashi_http(monkeypatch, payload):
    def _factory(**kwargs):
        return _MockClient(payload)
    monkeypatch.setattr(kashidashi_mod.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(
        kashidashi_mod, "get_config",
        lambda: SimpleNamespace(integrations=SimpleNamespace(kashidashi_url="http://k")),
    )


@pytest.mark.asyncio
async def test_search_falls_back_when_hint_and_match_empty(monkeypatch):
    _patch_kashidashi_http(monkeypatch, [_item()])
    src = KashidashiSource()
    identity = SimpleNamespace(disc_id="a70dc10b", track_count=11)
    out = await src.search(identity, hints=None)
    assert len(out) == 1
    assert out[0]["confidence"] >= 70
    assert "recency_fallback" in out[0]["evidence"]


@pytest.mark.asyncio
async def test_search_skips_fallback_when_hint_provided(monkeypatch):
    """If the caller already has hints, fallback must not fire — the normal
    fuzzy path is responsible for matching. Fuzzy may still emit candidates;
    what matters is that the recency_fallback path does not pile on top."""
    _patch_kashidashi_http(monkeypatch, [_item()])
    src = KashidashiSource()
    identity = SimpleNamespace(disc_id="other", track_count=11)
    out = await src.search(identity, hints={"artist": "X", "title": "Y"})
    assert all("recency_fallback" not in c.get("evidence", "") for c in out)


@pytest.mark.asyncio
async def test_search_skips_fallback_when_exact_discid_matched(monkeypatch):
    """Exact-discid match is the strong path — fallback shouldn't pile on."""
    item_with_disc = _item(rip_discid="a70dc10b", metadata_artist="A", metadata_album="B")
    _patch_kashidashi_http(monkeypatch, [item_with_disc])
    src = KashidashiSource()
    identity = SimpleNamespace(disc_id="a70dc10b", track_count=11)
    out = await src.search(identity, hints=None)
    # Only the exact-match candidate, no fallback duplicate
    assert len(out) == 1
    assert out[0]["confidence"] == 95
