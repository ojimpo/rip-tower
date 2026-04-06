"""Pipeline orchestration — manages job state transitions.

This is the central coordinator. Each service does its own work,
and the pipeline manages transitions between states.
"""

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select

from backend.database import async_session
from backend.models import Drive, Job, JobMetadata, Track
from backend.schemas import RipRequest
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)

# Per-device locks to prevent concurrent access to the same drive
_device_locks: dict[str, asyncio.Lock] = {}


def _get_device_lock(drive_id: str) -> asyncio.Lock:
    if drive_id not in _device_locks:
        _device_locks[drive_id] = asyncio.Lock()
    return _device_locks[drive_id]


async def _update_status(job_id: str, status: str, error: str | None = None) -> None:
    """Update job status in DB and broadcast via WebSocket."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = status
            if error:
                job.error_message = error
            if status == "complete":
                from datetime import datetime, timezone
                job.completed_at = datetime.now(timezone.utc)
            await session.commit()

    await broadcast("job:status", {"job_id": job_id, "status": status})


async def run_pipeline(job_id: str, request: RipRequest) -> None:
    """Run the full ripping pipeline for a job."""
    try:
        lock = _get_device_lock(request.drive_id)
        async with lock:
            # 1. Identifying
            await _update_status(job_id, "identifying")
            from backend.services.disc_identity import read_disc

            identity = await read_disc(request.drive_id, job_id)

            # 2. Parallel: resolving + ripping
            await _update_status(job_id, "ripping")

            resolve_task = asyncio.create_task(
                _run_resolve(job_id, identity, request.hints, request.force)
            )
            rip_task = asyncio.create_task(
                _run_rip(job_id, request.drive_id, identity)
            )

            await asyncio.gather(resolve_task, rip_task)

            # 3. Encoding
            await _update_status(job_id, "encoding")
            from backend.services.encoder import encode_all

            await encode_all(job_id)

            # 4. Check auto-approve
            await _check_approval(job_id)

    except Exception as e:
        logger.exception("Pipeline failed for job %s", job_id)
        await _update_status(job_id, "error", str(e))
        await broadcast("job:error", {"job_id": job_id, "message": str(e)})


async def _run_resolve(
    job_id: str,
    identity: Any,
    hints: dict | None,
    force: dict | None,
) -> None:
    """Run metadata resolution, artwork, lyrics, and kashidashi matching."""
    from backend.metadata.resolver import resolve

    await resolve(job_id, identity, hints, force)


async def _run_rip(job_id: str, drive_id: str, identity: Any) -> None:
    """Run CD ripping."""
    from backend.services.ripper import rip_disc

    await rip_disc(job_id, drive_id, identity)


async def _check_approval(job_id: str) -> None:
    """Check if the job can be auto-approved based on confidence threshold."""
    from backend.config import get_config

    config = get_config()

    async with async_session() as session:
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

        job = await session.get(Job, job_id)
        if not job:
            return

        # Check album_group — wait for all discs
        if job.album_group:
            group_jobs = await session.execute(
                select(Job).where(Job.album_group == job.album_group)
            )
            group_jobs = group_jobs.scalars().all()
            if any(j.status not in ("encoding", "review", "complete") for j in group_jobs if j.id != job_id):
                # Other discs still in progress — wait
                job.status = "review"
                meta.needs_review = True
                await session.commit()
                await broadcast("job:review", {
                    "job_id": job_id,
                    "reason": "waiting for other discs in group",
                })
                return

        confidence = meta.confidence if meta else 0
        threshold = config.general.auto_approve_threshold

        if confidence and confidence >= threshold:
            # Auto-approve
            job.status = "finalizing"
            if meta:
                meta.approved = True
                from datetime import datetime, timezone
                meta.approved_at = datetime.now(timezone.utc)
            await session.commit()
            await run_finalize(job_id)
        else:
            # Needs review
            job.status = "review"
            if meta:
                meta.needs_review = True
            await session.commit()
            await broadcast("job:review", {
                "job_id": job_id,
                "reason": f"confidence {confidence} < threshold {threshold}",
            })

            # Send Discord notification
            from backend.services.notifier import notify_review
            await notify_review(job_id)

            # Schedule reminder
            from backend.services.notifier import schedule_reminder
            asyncio.create_task(schedule_reminder(job_id))

            # Schedule eject reminder
            await _schedule_eject_reminder(job_id)


async def _schedule_eject_reminder(job_id: str) -> None:
    """Schedule an eject reminder if the job has a drive."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job and job.drive_id:
            from backend.services.notifier import schedule_eject_reminder
            asyncio.create_task(schedule_eject_reminder(job_id, job.drive_id))


async def run_finalize(job_id: str) -> None:
    """Run the finalization step."""
    try:
        await _update_status(job_id, "finalizing")
        from backend.services.finalizer import finalize

        await finalize(job_id)
        await _update_status(job_id, "complete")
        await broadcast("job:complete", {"job_id": job_id})

        from backend.services.notifier import notify_complete
        await notify_complete(job_id)

        # Schedule eject reminder
        await _schedule_eject_reminder(job_id)

    except Exception as e:
        logger.exception("Finalization failed for job %s", job_id)
        await _update_status(job_id, "error", str(e))


async def run_resolve_only(job_id: str, hints: dict) -> None:
    """Run metadata resolution only (for imports and re-resolve).

    After resolving, also encodes tracks so finalize has files to work with.
    """
    try:
        from backend.metadata.resolver import resolve

        await resolve(job_id, None, hints, None)

        # Encode tracks (WAV imports skip the rip+encode pipeline path)
        await _update_status(job_id, "encoding")
        from backend.services.encoder import encode_all

        await encode_all(job_id)

        await _check_approval(job_id)
    except Exception as e:
        logger.exception("Resolve failed for job %s", job_id)
        await _update_status(job_id, "error", str(e))


async def run_re_rip(job_id: str, drive_id: str | None = None) -> None:
    """Re-rip all tracks of a job."""
    try:
        # 1. Get job and determine drive
        async with async_session() as session:
            job = await session.get(Job, job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found")
            effective_drive_id = drive_id or job.drive_id
            if not effective_drive_id:
                raise RuntimeError(f"No drive available for job {job_id}")

        # 2. Get drive's current_path
        async with async_session() as session:
            drive = await session.get(Drive, effective_drive_id)
            if not drive or not drive.current_path:
                raise RuntimeError(f"Drive {effective_drive_id} not connected")

        # 3. Acquire device lock
        lock = _get_device_lock(effective_drive_id)
        async with lock:
            # 4. Update job status to ripping
            await _update_status(job_id, "ripping")

            # 5. Reset all tracks
            async with async_session() as session:
                tracks = await session.execute(
                    select(Track).where(Track.job_id == job_id)
                )
                for t in tracks.scalars():
                    t.rip_status = "pending"
                    t.encode_status = "pending"
                await session.commit()

            # 6. Read disc identity (without creating new tracks)
            from backend.services.disc_identity import read_disc_identity_only

            identity = await read_disc_identity_only(effective_drive_id)

            # 7. Rip all tracks
            await _run_rip(job_id, effective_drive_id, identity)

            # 8. Encode all tracks
            await _update_status(job_id, "encoding")
            from backend.services.encoder import encode_all

            await encode_all(job_id)

            # 9. Check approval
            await _check_approval(job_id)

    except Exception as e:
        logger.exception("Re-rip failed for job %s", job_id)
        await _update_status(job_id, "error", str(e))
        await broadcast("job:error", {"job_id": job_id, "message": str(e)})


async def run_re_rip_track(
    job_id: str, track_num: int, drive_id: str | None = None
) -> None:
    """Re-rip a specific track."""
    try:
        # 1. Get job and determine drive
        async with async_session() as session:
            job = await session.get(Job, job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found")
            effective_drive_id = drive_id or job.drive_id
            if not effective_drive_id:
                raise RuntimeError(f"No drive available for job {job_id}")

        # 2. Get drive path and reset track status
        from backend.config import get_config
        from pathlib import Path

        async with async_session() as session:
            drive = await session.get(Drive, effective_drive_id)
            if not drive or not drive.current_path:
                raise RuntimeError(f"Drive {effective_drive_id} not connected")
            dev_path = drive.current_path

            track = await session.execute(
                select(Track).where(
                    Track.job_id == job_id, Track.track_num == track_num
                )
            )
            track = track.scalar_one_or_none()
            if not track:
                raise RuntimeError(f"Track {track_num} not found for job {job_id}")

            # 3. Reset track status
            track.rip_status = "pending"
            track.encode_status = "pending"

            # Get total track count for progress reporting
            all_tracks = await session.execute(
                select(Track).where(Track.job_id == job_id)
            )
            total_tracks = len(all_tracks.scalars().all())
            await session.commit()

        # 4. Rip just that track
        lock = _get_device_lock(effective_drive_id)
        async with lock:
            config = get_config()
            output_dir = Path(config.output.incoming_dir) / job_id
            output_dir.mkdir(parents=True, exist_ok=True)

            from backend.services.ripper import _rip_track

            await _rip_track(job_id, track_num, dev_path, output_dir, total_tracks)

        # 5. Encode just that track
        from backend.services.encoder import encode_all

        await encode_all(job_id)

    except Exception as e:
        logger.exception("Re-rip track %d failed for job %s", track_num, job_id)
        await _update_status(job_id, "error", str(e))
        await broadcast("job:error", {"job_id": job_id, "message": str(e)})
