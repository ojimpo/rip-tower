"""Tests for artwork invalidation/refresh after a metadata edit."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from backend.metadata import artwork
from backend.models import Artwork, Job, JobMetadata


def _write_image(dir_: Path, name: str) -> Path:
    p = dir_ / name
    p.write_bytes(b"fake-image-bytes")
    return p


@pytest.mark.asyncio
async def test_invalidate_auto_artwork_removes_non_manual_keeps_manual(
    monkeypatch, async_session_maker, tmp_path,
):
    monkeypatch.setattr(artwork, "async_session", async_session_maker)
    monkeypatch.setattr(artwork, "ARTWORK_DIR", tmp_path)

    auto_file = _write_image(tmp_path, "auto.jpg")
    manual_file = _write_image(tmp_path, "manual.jpg")

    async with async_session_maker() as s:
        s.add(Job(id="job-1", drive_id="d", disc_id="x"))
        s.add(Artwork(
            job_id="job-1", source="itunes",
            local_path=str(auto_file), selected=True,
        ))
        s.add(Artwork(
            job_id="job-1", source="manual",
            local_path=str(manual_file), selected=False,
        ))
        await s.commit()

    removed = await artwork.invalidate_auto_artwork("job-1")

    assert removed == 1
    assert not auto_file.exists()       # auto file removed from disk
    assert manual_file.exists()         # manual upload preserved

    async with async_session_maker() as s:
        rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-1")
        )).scalars())
    assert len(rows) == 1
    assert rows[0].source == "manual"


@pytest.mark.asyncio
async def test_refresh_artwork_for_edit_refetches_and_propagates_to_group(
    monkeypatch, async_session_maker, tmp_path,
):
    """Editing the album drops the group's stale auto artwork, fetches fresh art
    for the edited disc, and propagates it to the sibling."""
    monkeypatch.setattr(artwork, "async_session", async_session_maker)
    monkeypatch.setattr(artwork, "ARTWORK_DIR", tmp_path)

    stale_a = _write_image(tmp_path, "stale_a.jpg")
    stale_b = _write_image(tmp_path, "stale_b.jpg")

    async with async_session_maker() as s:
        for jid, stale in (("job-a", stale_a), ("job-b", stale_b)):
            s.add(Job(id=jid, drive_id="d", disc_id=jid, album_group="grp"))
            s.add(JobMetadata(job_id=jid, artist="Right Artist", album="Right Album"))
            s.add(Artwork(
                job_id=jid, source="itunes",
                local_path=str(stale), selected=True,
            ))
        await s.commit()

    # Simulate a fresh external fetch: the edited disc gets a new cover.
    async def _fake_fetch(job_id: str) -> None:
        fresh = _write_image(tmp_path, f"fresh_{job_id}.jpg")
        async with async_session_maker() as s:
            s.add(Artwork(
                job_id=job_id, source="cover_art_archive",
                local_path=str(fresh), width=600, height=600, selected=True,
            ))
            await s.commit()

    monkeypatch.setattr(artwork, "fetch_artwork", _fake_fetch)

    await artwork.refresh_artwork_for_edit("job-a")

    assert not stale_a.exists()
    assert not stale_b.exists()

    async with async_session_maker() as s:
        a_rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-a")
        )).scalars())
        b_rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-b")
        )).scalars())

    # Edited disc has only the fresh cover, selected.
    assert len(a_rows) == 1
    assert a_rows[0].source == "cover_art_archive" and a_rows[0].selected
    # Sibling received the fresh cover by group propagation.
    assert len(b_rows) == 1
    assert b_rows[0].selected
    assert Path(b_rows[0].local_path).exists()


@pytest.mark.asyncio
async def test_refresh_keeps_manual_selection(
    monkeypatch, async_session_maker, tmp_path,
):
    """A manually-chosen cover survives a metadata edit and stays selected."""
    monkeypatch.setattr(artwork, "async_session", async_session_maker)
    monkeypatch.setattr(artwork, "ARTWORK_DIR", tmp_path)

    manual_file = _write_image(tmp_path, "manual.jpg")

    async with async_session_maker() as s:
        s.add(Job(id="job-m", drive_id="d", disc_id="m"))
        s.add(JobMetadata(job_id="job-m", artist="A", album="B"))
        s.add(Artwork(
            job_id="job-m", source="manual",
            local_path=str(manual_file), selected=True,
        ))
        await s.commit()

    async def _fake_fetch(job_id: str) -> None:
        return None  # external fetch finds nothing new

    monkeypatch.setattr(artwork, "fetch_artwork", _fake_fetch)

    await artwork.refresh_artwork_for_edit("job-m")

    assert manual_file.exists()
    async with async_session_maker() as s:
        rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-m")
        )).scalars())
    assert len(rows) == 1
    assert rows[0].source == "manual" and rows[0].selected


def _png_bytes(width: int, height: int) -> bytes:
    from io import BytesIO
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_save_artwork_upserts_per_source(
    monkeypatch, async_session_maker, tmp_path,
):
    """Refetching the same source updates the existing row instead of piling
    up duplicates (re-resolve / post-edit refresh case)."""
    monkeypatch.setattr(artwork, "async_session", async_session_maker)
    monkeypatch.setattr(artwork, "ARTWORK_DIR", tmp_path)

    async with async_session_maker() as s:
        s.add(Job(id="job-up", drive_id="d", disc_id="u"))
        await s.commit()

    await artwork._save_artwork("job-up", "itunes", "http://a", _png_bytes(10, 10))
    await artwork._save_artwork("job-up", "itunes", "http://b", _png_bytes(20, 20))
    # A different source still gets its own row
    await artwork._save_artwork("job-up", "discogs", "http://c", _png_bytes(30, 30))

    async with async_session_maker() as s:
        rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-up")
        )).scalars())

    by_source = {r.source: r for r in rows}
    assert len(rows) == 2
    assert by_source["itunes"].url == "http://b"
    assert by_source["itunes"].width == 20
    assert by_source["discogs"].width == 30


# ───────── _auto_select_best shape preference ─────────


@pytest.mark.asyncio
async def test_auto_select_prefers_square_over_banner(
    monkeypatch, async_session_maker,
):
    """A square cover must beat a non-square banner even when the banner comes
    from a higher-priority source (the 599×362 Discogs case)."""
    monkeypatch.setattr(artwork, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-sq", drive_id="d", disc_id="s"))
        s.add(Artwork(job_id="job-sq", source="discogs", width=599, height=362))
        s.add(Artwork(job_id="job-sq", source="itunes", width=600, height=600))
        await s.commit()

    await artwork._auto_select_best("job-sq")

    async with async_session_maker() as s:
        rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-sq")
        )).scalars())
    selected = [r for r in rows if r.selected]
    assert len(selected) == 1
    assert selected[0].source == "itunes"


@pytest.mark.asyncio
async def test_auto_select_manual_wins_even_when_not_square(
    monkeypatch, async_session_maker,
):
    """A manual upload is a deliberate user choice — shape must not demote it."""
    monkeypatch.setattr(artwork, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-mn", drive_id="d", disc_id="m"))
        s.add(Artwork(job_id="job-mn", source="manual", width=599, height=362))
        s.add(Artwork(
            job_id="job-mn", source="cover_art_archive", width=1200, height=1200,
        ))
        await s.commit()

    await artwork._auto_select_best("job-mn")

    async with async_session_maker() as s:
        rows = list((await s.execute(
            select(Artwork).where(Artwork.job_id == "job-mn")
        )).scalars())
    selected = [r for r in rows if r.selected]
    assert len(selected) == 1
    assert selected[0].source == "manual"


def test_is_squarish_bounds():
    def art(w, h):
        return Artwork(job_id="x", source="itunes", width=w, height=h)

    assert artwork._is_squarish(art(600, 600))
    assert artwork._is_squarish(art(500, 600))       # slightly tall is fine
    assert not artwork._is_squarish(art(599, 362))   # banner
    assert not artwork._is_squarish(art(None, 600))  # unknown can't verify
