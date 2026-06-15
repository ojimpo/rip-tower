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
