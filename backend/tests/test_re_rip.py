"""Tests for re-ripping a track onto a job that is already complete."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.models import Drive, Job, JobMetadata, Track
from backend.services import pipeline


class _Identity:
    def __init__(self, disc_id: str):
        self.disc_id = disc_id


@pytest.mark.asyncio
async def test_re_rip_refuses_a_different_disc(monkeypatch, async_session_maker):
    """Re-rip takes an explicit drive_id so a disc can be moved to a healthier
    drive. If the wrong disc is loaded there, the job's audio must not be
    silently overwritten."""
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Drive(drive_id="d1", name="LT-03", current_path="/dev/sr2"))
        s.add(Job(id="j1", drive_id="d1", disc_id="e411bd11", status="complete"))
        await s.commit()

    async def _wrong_disc(drive_id):
        return _Identity("c60e150f")

    monkeypatch.setattr(
        "backend.services.disc_identity.read_disc_identity_only", _wrong_disc
    )

    with pytest.raises(RuntimeError, match="Wrong disc in drive"):
        await pipeline._verify_disc_matches("j1", "d1")


@pytest.mark.asyncio
async def test_re_rip_accepts_the_matching_disc(monkeypatch, async_session_maker):
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Drive(drive_id="d1", name="LT-03", current_path="/dev/sr2"))
        s.add(Job(id="j1", drive_id="d1", disc_id="e411bd11", status="complete"))
        await s.commit()

    async def _right_disc(drive_id):
        return _Identity("e411bd11")

    monkeypatch.setattr(
        "backend.services.disc_identity.read_disc_identity_only", _right_disc
    )

    await pipeline._verify_disc_matches("j1", "d1")  # must not raise


@pytest.mark.asyncio
async def test_job_without_disc_id_is_not_blocked(monkeypatch, async_session_maker):
    """Imported jobs and jobs whose identify failed have nothing to compare."""
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Drive(drive_id="d1", name="LT-03", current_path="/dev/sr2"))
        s.add(Job(id="j1", drive_id="d1", disc_id=None, status="complete"))
        await s.commit()

    async def _boom(drive_id):
        raise AssertionError("should not read the disc when there is no disc_id")

    monkeypatch.setattr(
        "backend.services.disc_identity.read_disc_identity_only", _boom
    )

    await pipeline._verify_disc_matches("j1", "d1")


@pytest.mark.asyncio
async def test_complete_job_re_rip_reaches_the_library(
    monkeypatch, async_session_maker, tmp_path,
):
    """Regression: a re-rip on a `complete` job encoded into the incoming dir
    and stopped there, so the library kept the old degraded audio while the
    job reported success."""
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    library = tmp_path / "library"
    library.mkdir()
    incoming = tmp_path / "incoming" / "j1"
    incoming.mkdir(parents=True)
    (incoming / "track02.cdda.flac").write_bytes(b"fresh audio")
    (incoming / "track02.cdda.wav").write_bytes(b"scratch wav")

    async with async_session_maker() as s:
        s.add(Job(id="j1", drive_id="d1", status="complete",
                  output_dir=str(library)))
        s.add(JobMetadata(job_id="j1", artist="ゆず", album="A", album_base="A"))
        s.add(Track(job_id="j1", track_num=2, title="栄光の架橋",
                    rip_status="ok", encode_status="ok",
                    encoded_path=str(incoming / "track02.cdda.flac")))
        await s.commit()

    called = {}

    async def _fake_reapply(job_id):
        called["job_id"] = job_id

    monkeypatch.setattr(
        "backend.services.finalizer.reapply_metadata", _fake_reapply
    )

    class _Cfg:
        class output:
            incoming_dir = str(tmp_path / "incoming")

    monkeypatch.setattr("backend.config.get_config", lambda: _Cfg)

    await pipeline._reflect_re_rip_into_library("j1")

    assert called["job_id"] == "j1", "library reflection never ran"
    assert not (incoming / "track02.cdda.wav").exists(), "scratch WAV left behind"


@pytest.mark.asyncio
async def test_incomplete_job_is_left_to_the_normal_pipeline(
    monkeypatch, async_session_maker, tmp_path,
):
    """A job still in review/error finalises through the usual path — the
    reflection step must not move its files early."""
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="j1", drive_id="d1", status="review", output_dir=None))
        await s.commit()

    async def _boom(job_id):
        raise AssertionError("must not reapply metadata for a non-complete job")

    monkeypatch.setattr("backend.services.finalizer.reapply_metadata", _boom)

    await pipeline._reflect_re_rip_into_library("j1")


@pytest.mark.asyncio
async def test_re_rip_failed_can_include_degraded_tracks(
    monkeypatch, client, db_session,
):
    """`ok_degraded` means the track only came off via a retry or the
    cdda2wav fallback. After moving the disc to a better drive those are
    exactly the tracks worth redoing, but the endpoint used to ignore them."""
    db_session.add(Job(id="j1", drive_id="d1", disc_id="e411bd11", status="complete"))
    for n, status in ((1, "ok"), (2, "ok_degraded"), (4, "ok_degraded"), (7, "failed")):
        db_session.add(Track(job_id="j1", track_num=n, rip_status=status))
    await db_session.commit()

    started: list[int] = []

    async def _fake_re_rip(job_id, track_num, drive_id=None):
        started.append(track_num)

    monkeypatch.setattr(
        "backend.services.pipeline.run_re_rip_track", _fake_re_rip
    )

    resp = await client.post("/api/jobs/j1/re-rip/failed")
    assert resp.json()["tracks"] == [7]

    resp = await client.post("/api/jobs/j1/re-rip/failed?include_degraded=true")
    assert resp.json()["tracks"] == [2, 4, 7]
