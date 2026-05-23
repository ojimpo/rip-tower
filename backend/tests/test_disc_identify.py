"""Tests for disc_identify's borrowed-CD reconciliation.

The MB+CDDB lookup itself is exercised against the live network elsewhere;
these tests pin the cross-check that overrides a TOC-collided MB result
when the user is currently borrowing CDs (the Cocco-vs-Harry-Potter case).
"""

from __future__ import annotations

import pytest

from backend.services import disc_identify
from backend.services.disc_identify import _reconcile_with_borrowed


def _borrowed(**overrides) -> dict:
    base = {
        "id": 1,
        "artist": "Cocco",
        "title": "ザ・ベスト盤",
        "metadata_artist": None,
        "metadata_album": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_keeps_mb_result_when_no_borrowed_cds(monkeypatch):
    async def _empty():
        return []
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _empty
    )
    artist, album = await _reconcile_with_borrowed("Some Artist", "Some Album")
    assert artist == "Some Artist"
    assert album == "Some Album"


@pytest.mark.asyncio
async def test_keeps_mb_result_when_it_matches_a_borrowed_cd(monkeypatch):
    async def _items():
        return [_borrowed(artist="Cocco", title="ザ・ベスト盤"),
                _borrowed(id=2, artist="加古隆", title="白い巨塔")]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed("Cocco", "ザ・ベスト盤")
    assert artist == "Cocco"
    assert album == "ザ・ベスト盤"


@pytest.mark.asyncio
async def test_replaces_mb_result_with_sole_borrowed_cd(monkeypatch):
    async def _items():
        return [_borrowed(artist="Cocco", title="ザ・ベスト盤")]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed(
        "Joanne K. Rowling", "Harry Potter und der Feuerkelch"
    )
    assert artist == "Cocco"
    assert album == "ザ・ベスト盤"


@pytest.mark.asyncio
async def test_suppresses_misleading_mb_result_when_pool_is_ambiguous(monkeypatch):
    # MB returns Harry Potter via TOC collision; user has 3 borrowed CDs,
    # none of them are Harry Potter. We can't tell which one this disc is,
    # but at least don't lie to the user with the Harry Potter name.
    async def _items():
        return [
            _borrowed(id=1, artist="Cocco", title="ザ・ベスト盤"),
            _borrowed(id=2, artist="加古隆", title="白い巨塔"),
            _borrowed(id=3, artist="国歌", title="世界の国歌大全集"),
        ]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed(
        "Joanne K. Rowling", "Harry Potter und der Feuerkelch"
    )
    assert artist is None
    assert album is None


@pytest.mark.asyncio
async def test_adopts_sole_borrowed_when_lookup_returned_nothing(monkeypatch):
    async def _items():
        return [_borrowed(artist="Cocco", title="ザ・ベスト盤")]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed(None, None)
    assert artist == "Cocco"
    assert album == "ザ・ベスト盤"


@pytest.mark.asyncio
async def test_leaves_empty_when_lookup_blank_and_pool_ambiguous(monkeypatch):
    async def _items():
        return [
            _borrowed(id=1, artist="Cocco", title="ザ・ベスト盤"),
            _borrowed(id=2, artist="加古隆", title="白い巨塔"),
        ]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed(None, None)
    assert artist is None
    assert album is None


@pytest.mark.asyncio
async def test_falls_through_when_borrowed_fetch_fails(monkeypatch):
    async def _boom():
        raise RuntimeError("kashidashi down")
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _boom
    )
    artist, album = await _reconcile_with_borrowed("Some Artist", "Some Album")
    # On API failure, trust the raw lookup — don't suppress.
    assert artist == "Some Artist"
    assert album == "Some Album"


@pytest.mark.asyncio
async def test_prefers_metadata_fields_over_plain_when_present(monkeypatch):
    # When kashidashi staff entered a free-text artist but the rip pipeline
    # later populated metadata_artist, the corrected value should win.
    async def _items():
        return [_borrowed(
            artist="cocco", title="best",
            metadata_artist="Cocco", metadata_album="ザ・ベスト盤",
        )]
    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items", _items
    )
    artist, album = await _reconcile_with_borrowed(None, None)
    assert artist == "Cocco"
    assert album == "ザ・ベスト盤"
