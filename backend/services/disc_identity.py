"""Disc identification via cd-discid."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from backend.database import async_session
from backend.models import Drive, Job, Track

logger = logging.getLogger(__name__)


@dataclass
class DiscIdentity:
    disc_id: str
    track_count: int
    toc: list[int]
    total_seconds: int


async def read_disc(drive_id: str, job_id: str) -> DiscIdentity:
    """Read disc identity using cd-discid.

    Updates the job with disc_id and creates track records.
    """
    async with async_session() as session:
        drive = await session.get(Drive, drive_id)
        if not drive or not drive.current_path:
            raise RuntimeError(f"Drive {drive_id} not connected")
        dev_path = drive.current_path

    proc = await asyncio.create_subprocess_exec(
        "cd-discid", dev_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"cd-discid failed: {stderr.decode().strip()}")

    parts = stdout.decode().strip().split()
    disc_id = parts[0]
    track_count = int(parts[1])
    toc = [int(x) for x in parts[2:]]
    total_seconds = toc[-1] // 75 if toc else 0

    # Update job and create track records
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.disc_id = disc_id
            job.toc_hash = ":".join(str(x) for x in toc[:track_count])

        for i in range(1, track_count + 1):
            track = Track(
                job_id=job_id,
                track_num=i,
                rip_status="pending",
                encode_status="pending",
            )
            session.add(track)

        await session.commit()

    identity = DiscIdentity(
        disc_id=disc_id,
        track_count=track_count,
        toc=toc,
        total_seconds=total_seconds,
    )
    logger.info("Disc identified: %s (%d tracks)", disc_id, track_count)
    return identity
