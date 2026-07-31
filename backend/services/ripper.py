"""CD ripping — extract audio tracks to WAV.

Uses cd-paranoia as primary, cdda2wav as fallback.
Per-track timeout and retry with degraded mode.

Strategy:
  Attempt 1: cd-paranoia (full paranoia)
  Attempt 2: cd-paranoia --never-skip=40 (degraded)
  Attempt 3: cdda2wav fallback (if available)

Ported from ~/dev/openclaw-cd-rip/scripts/ripper.py.
"""

import asyncio
import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Drive, RipAttempt, Track
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)

PER_TRACK_TIMEOUT = 600  # seconds


async def _record_attempt(
    drive_id: str | None,
    job_id: str,
    track_num: int,
    attempt: int,
    tool: str,
    outcome: str,
    started: float,
) -> None:
    """Log one rip attempt for drive health reporting (best-effort)."""
    try:
        async with async_session() as session:
            session.add(RipAttempt(
                drive_id=drive_id,
                job_id=job_id,
                track_num=track_num,
                attempt=attempt,
                tool=tool,
                outcome=outcome,
                duration_ms=int((time.monotonic() - started) * 1000),
            ))
            await session.commit()
    except Exception:
        # Diagnostics must never be able to fail a rip.
        logger.exception("Failed to record rip attempt for job %s", job_id)


async def rip_disc(job_id: str, drive_id: str, identity) -> None:
    """Rip all tracks from the disc."""
    config = get_config()
    output_dir = Path(config.output.incoming_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_session() as session:
        drive = await session.get(Drive, drive_id)
        if not drive or not drive.current_path:
            raise RuntimeError(f"Drive {drive_id} not connected")

        tracks = await session.execute(
            select(Track)
            .where(Track.job_id == job_id)
            .order_by(Track.track_num)
        )
        tracks = tracks.scalars().all()

    total = identity.audio_track_count or len(tracks) or identity.track_count
    for track in tracks:
        # Re-read current_path before each track so a mid-rip USB re-enumeration
        # (e.g. /dev/sr1 -> /dev/sr4 with the same serial) doesn't condemn every
        # remaining track to a stale dev node.
        async with async_session() as session:
            drive = await session.get(Drive, drive_id)
            if not drive or not drive.current_path:
                raise RuntimeError(f"Drive {drive_id} not connected")
            dev_path = drive.current_path
        await _rip_track(
            job_id, track.track_num, dev_path, output_dir, total, drive_id,
        )


async def _rip_track(
    job_id: str,
    track_num: int,
    dev_path: str,
    output_dir: Path,
    total_tracks: int,
    drive_id: str | None = None,
) -> None:
    """Rip a single track with retry and fallback.

    Attempt 1: cd-paranoia full paranoia
    Attempt 2: cd-paranoia --never-skip=40 (degraded)
    Attempt 3: cdda2wav fallback (if available)
    """
    wav_path = output_dir / f"track{track_num:02d}.cdda.wav"

    async with async_session() as session:
        track = await session.execute(
            select(Track).where(Track.job_id == job_id, Track.track_num == track_num)
        )
        track = track.scalar_one()
        track.rip_status = "ripping"
        await session.commit()

    await broadcast("job:progress", {
        "job_id": job_id,
        "track": track_num,
        "total": total_tracks,
        "percent": int((track_num - 1) / total_tracks * 100),
    })

    # Build strategies matching original ripper.py
    strategies: list[tuple[str, list[str]]] = [
        ("cd-paranoia", [
            "cd-paranoia", "-d", dev_path,
            str(track_num), str(wav_path),
        ]),
        ("cd-paranoia_degraded", [
            "cd-paranoia", "-d", dev_path,
            "--never-skip=40",
            str(track_num), str(wav_path),
        ]),
    ]

    # Check if cdda2wav is available
    if shutil.which("cdda2wav"):
        strategies.append(
            ("cdda2wav", [
                "cdda2wav", f"dev={dev_path}",
                f"-t{track_num}", str(wav_path),
            ])
        )

    success = False
    degraded = False

    for attempt, (tool_name, cmd) in enumerate(strategies, 1):
        logger.info(
            "Ripping track %d (attempt %d/%d, %s)",
            track_num, attempt, len(strategies), tool_name,
        )

        from backend.services.pipeline import spawn_tracked, unregister_proc

        started = time.monotonic()
        try:
            proc = await spawn_tracked(
                job_id, *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=PER_TRACK_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("Track %d: timeout with %s", track_num, tool_name)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                wav_path.unlink(missing_ok=True)
                await _record_attempt(
                    drive_id, job_id, track_num, attempt, tool_name,
                    "timeout", started,
                )
                continue
            finally:
                unregister_proc(job_id, proc)

            if proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 0:
                degraded = attempt > 1
                status = "ok" if attempt == 1 else "ok_degraded"
                logger.info("Track %d: %s (%s)", track_num, status, tool_name)
                success = True
                await _record_attempt(
                    drive_id, job_id, track_num, attempt, tool_name, "ok", started,
                )
                break

            logger.warning(
                "Track %d attempt %d failed: %s",
                track_num, attempt, stderr.decode("utf-8", "replace").strip()[:200],
            )
            wav_path.unlink(missing_ok=True)
            await _record_attempt(
                drive_id, job_id, track_num, attempt, tool_name, "error", started,
            )

        except Exception as e:
            logger.warning("Track %d attempt %d error: %s", track_num, attempt, e)
            wav_path.unlink(missing_ok=True)
            await _record_attempt(
                drive_id, job_id, track_num, attempt, tool_name, "error", started,
            )

    # Update track status
    async with async_session() as session:
        track = await session.execute(
            select(Track).where(Track.job_id == job_id, Track.track_num == track_num)
        )
        track = track.scalar_one()
        if success:
            track.rip_status = "ok_degraded" if degraded else "ok"
            track.wav_path = str(wav_path)
        else:
            track.rip_status = "failed"
            logger.error("Track %d: FAILED after all attempts", track_num)
        await session.commit()

    await broadcast("job:progress", {
        "job_id": job_id,
        "track": track_num,
        "total": total_tracks,
        "percent": int(track_num / total_tracks * 100),
    })
