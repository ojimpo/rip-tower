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
    fetch_active_borrowed_items,
    kashidashi_match_score,
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


# ───────── kashidashi_match_score (resolver boost helper) ─────────


def test_match_score_strong_match_on_both_fields():
    items = [_item(artist="宮本浩次", title="ROMANCE")]
    item, art, alb = kashidashi_match_score("宮本浩次", "ROMANCE", items)
    assert item is items[0]
    assert art >= 0.6 and alb >= 0.6


def test_match_score_no_match_returns_low_scores():
    items = [_item(artist="宮本浩次", title="ROMANCE")]
    item, art, alb = kashidashi_match_score(
        "Crystal Lewis", "Simply the Best", items,
    )
    # Some non-zero overlap is possible (latin chars) but well under threshold.
    assert art < 0.6 or alb < 0.6


def test_match_score_uses_metadata_fields_when_plain_blank():
    items = [_item(
        artist="", title="",
        metadata_artist="宮本浩次", metadata_album="ROMANCE",
    )]
    item, art, alb = kashidashi_match_score("宮本浩次", "ROMANCE", items)
    assert item is items[0]
    assert art >= 0.6 and alb >= 0.6


def test_match_score_picks_best_across_multiple_items():
    items = [
        _item(id=1, artist="Other", title="Other Album"),
        _item(id=2, artist="宮本浩次", title="ROMANCE"),
        _item(id=3, artist="ポケットビスケッツ", title="THANKS"),
    ]
    item, art, alb = kashidashi_match_score("宮本浩次", "ROMANCE", items)
    assert item["id"] == 2


def test_match_score_handles_empty_candidate():
    items = [_item()]
    item, art, alb = kashidashi_match_score("", "", items)
    assert item is None
    assert art == 0.0 and alb == 0.0


# ───────── fetch_active_borrowed_items ─────────


@pytest.mark.asyncio
async def test_fetch_active_borrowed_items_filters_returned_and_ripped(monkeypatch):
    payload = [
        _item(id=1),
        _item(id=2, returned_at="2026-05-10T00:00:00Z"),
        _item(id=3, ripped_at="2026-05-10T00:00:00Z"),
        _item(id=4),
    ]
    _patch_kashidashi_http(monkeypatch, payload)
    out = await fetch_active_borrowed_items()
    assert sorted(it["id"] for it in out) == [1, 4]


@pytest.mark.asyncio
async def test_fetch_active_borrowed_items_returns_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        kashidashi_mod, "get_config",
        lambda: SimpleNamespace(integrations=SimpleNamespace(kashidashi_url="")),
    )
    out = await fetch_active_borrowed_items()
    assert out == []


# ───────────────── Fix B: script-insensitive (MB-alias) matching ─────────────────


def _candidate(**kw):
    base = {"artist": None, "album": None, "evidence": None, "source_url": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_mb_release_id_from_evidence():
    import json
    c = _candidate(evidence=json.dumps({"mb_release": "abc-123"}))
    assert kashidashi_mod._mb_release_id(c) == "abc-123"


def test_mb_release_id_from_source_url():
    c = _candidate(source_url="https://musicbrainz.org/release/def-456")
    assert kashidashi_mod._mb_release_id(c) == "def-456"


def test_mb_release_id_none():
    assert kashidashi_mod._mb_release_id(_candidate()) is None


def test_is_disc_anchored():
    import json
    assert kashidashi_mod._is_disc_anchored(
        _candidate(evidence=json.dumps({"match": "toc_submission"}))) is True
    assert kashidashi_mod._is_disc_anchored(
        _candidate(evidence=json.dumps({"match": "text_search"}))) is False


@pytest.mark.asyncio
async def test_best_match_direct_same_script(monkeypatch):
    """Same-script match needs no MB alias lookup."""
    called = False

    async def _should_not_call(_rid):
        nonlocal called
        called = True
        return [], []

    monkeypatch.setattr(
        "backend.metadata.sources.musicbrainz.fetch_release_artist_aliases",
        _should_not_call,
    )
    items = [_item(id=5, artist="宮本浩次", title="ROMANCE")]
    cand = _candidate(artist="宮本浩次", album="ROMANCE",
                      evidence='{"match":"toc_submission","mb_release":"r"}')
    item, art, alb = await kashidashi_mod.best_kashidashi_match(cand, items)
    assert item is not None and art >= 0.6 and alb >= 0.6
    assert called is False  # direct match short-circuits before any network call


@pytest.mark.asyncio
async def test_best_match_cross_script_via_alias(monkeypatch):
    """Japanese borrowed record matches an English MB candidate through aliases.

    This is bbx9jqrx: borrowed 'エイミー・ワインハウス / バック・トゥ・ブラック'
    vs MB 'Amy Winehouse / Back to Black' — direct similarity is 0, but the MB
    artist alias bridges them, and the disc-anchored TOC covers the album title.
    """
    async def _aliases(_rid):
        return ["Amy Winehouse", "エイミー・ワインハウス"], []

    monkeypatch.setattr(
        "backend.metadata.sources.musicbrainz.fetch_release_artist_aliases",
        _aliases,
    )
    items = [_item(id=191, artist="エイミー・ワインハウス", title="バック・トゥ・ブラック")]
    cand = _candidate(artist="Amy Winehouse", album="Back to Black",
                      evidence='{"match":"toc_submission","mb_release":"r"}')
    item, art, alb = await kashidashi_mod.best_kashidashi_match(cand, items)
    assert item is not None and item["id"] == 191
    assert art >= 0.6  # matched via katakana alias
    assert alb >= 0.6  # disc-anchored relaxation covers the cross-script title


@pytest.mark.asyncio
async def test_best_match_cross_script_album_not_relaxed_when_unanchored(monkeypatch):
    """Without a disc anchor, a cross-script album title must NOT be relaxed —
    artist alone is not enough to claim the disc."""
    async def _aliases(_rid):
        return ["Amy Winehouse", "エイミー・ワインハウス"], []

    monkeypatch.setattr(
        "backend.metadata.sources.musicbrainz.fetch_release_artist_aliases",
        _aliases,
    )
    items = [_item(id=191, artist="エイミー・ワインハウス", title="バック・トゥ・ブラック")]
    cand = _candidate(artist="Amy Winehouse", album="Back to Black",
                      evidence='{"match":"text_search","mb_release":"r"}')
    _item_res, art, alb = await kashidashi_mod.best_kashidashi_match(cand, items)
    assert art >= 0.6      # artist still bridges via alias
    assert alb < 0.6       # album stays unmatched → resolver won't boost
