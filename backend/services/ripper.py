"""CD ripping — extract audio tracks to WAV.

Uses cd-paranoia as primary, cdda2wav as fallback.
Ported from ~/dev/openclaw-cd-rip/scripts/ripper.py.
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Drive, Track
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


async def rip_disc(job_id: str, drive_id: str, identity) -> None:
    """Rip all tracks from the disc."""
    config = get_config()
    output_dir = Path(config.output.incoming_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_session() as session:
        drive = await session.get(Drive, drive_id)
        if not drive or not drive.current_path:
            raise RuntimeError(f"Drive {drive_id} not connected")
        dev_path = drive.current_path

        tracks = await session.execute(
            select(Track)
            .where(Track.job_id == job_id)
            .order_by(Track.track_num)
        )
        tracks = tracks.scalars().all()

    for track in tracks:
        await _rip_track(job_id, track.track_num, dev_path, output_dir, identity.track_count)


async def _rip_track(
    job_id: str,
    track_num: int,
    dev_path: str,
    output_dir: Path,
    total_tracks: int,
) -> None:
    """Rip a single track with retry and fallback."""
    wav_path = output_dir / f"track{track_num:02d}.wav"

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

    success = False
    degraded = False

    for attempt in range(1, MAX_RETRIES + 1):
        tool = "cd-paranoia" if attempt <= 2 else "cdda2wav"

        if tool == "cd-paranoia":
            cmd = [
                "cd-paranoia",
                f"--force-cdrom-device={dev_path}",
                "--abort-on-skip",
                str(track_num),
                str(wav_path),
            ]
        else:
            cmd = [
                "cdda2wav",
                f"dev={dev_path}",
                f"-t{track_num}",
                str(wav_path),
            ]
            degraded = True

        logger.info("Ripping track %d (attempt %d/%d, %s)", track_num, attempt, MAX_RETRIES, tool)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0 and wav_path.exists():
            success = True
            break

        logger.warning(
            "Track %d attempt %d failed: %s",
            track_num, attempt, stderr.decode().strip()[:200],
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
        await session.commit()

    await broadcast("job:progress", {
        "job_id": job_id,
        "track": track_num,
        "total": total_tracks,
        "percent": int(track_num / total_tracks * 100),
    })
