"""Disc identification via cd-discid.

Ported from ~/dev/openclaw-cd-rip/scripts/disc_identity.py.

cd-discid output format:
    DISCID TRACK_COUNT OFFSET1 OFFSET2 ... OFFSETN LEADOUT_SECONDS

Example:
    a40b4d0c 12 150 18627 ... 281557 4003
"""

import asyncio
import hashlib
import json
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
    offsets: list[int]
    leadout: int
    toc_hash: str
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

    raw = stdout.decode().strip()
    if not raw:
        raise RuntimeError(f"cd-discid returned empty output for {dev_path}")

    parts = raw.split()
    if len(parts) < 4:
        raise RuntimeError(f"cd-discid output malformed: {raw}")

    disc_id = parts[0].lower()
    track_count = int(parts[1])
    offsets = [int(x) for x in parts[2:2 + track_count]]
    leadout = int(parts[2 + track_count])

    # SHA-256 hash of raw output for deduplication
    toc_hash = hashlib.sha256(raw.encode()).hexdigest()

    # leadout from cd-discid is already in seconds
    total_seconds = leadout

    # Update job and create track records
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.disc_id = disc_id
            job.toc_hash = toc_hash
            job.disc_total_seconds = total_seconds
            job.disc_offsets = json.dumps(offsets)
            job.disc_leadout = leadout

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
        offsets=offsets,
        leadout=leadout,
        toc_hash=toc_hash,
        total_seconds=total_seconds,
    )
    logger.info("Disc identified: %s (%d tracks)", disc_id, track_count)
    return identity


async def read_disc_identity_only(drive_id: str) -> DiscIdentity:
    """Read disc identity without creating tracks or updating job.

    Used for re-rip where tracks already exist in the DB.
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

    raw = stdout.decode().strip()
    if not raw:
        raise RuntimeError(f"cd-discid returned empty output for {dev_path}")

    parts = raw.split()
    if len(parts) < 4:
        raise RuntimeError(f"cd-discid output malformed: {raw}")

    disc_id = parts[0].lower()
    track_count = int(parts[1])
    offsets = [int(x) for x in parts[2:2 + track_count]]
    leadout = int(parts[2 + track_count])
    toc_hash = hashlib.sha256(raw.encode()).hexdigest()
    total_seconds = leadout

    identity = DiscIdentity(
        disc_id=disc_id,
        track_count=track_count,
        offsets=offsets,
        leadout=leadout,
        toc_hash=toc_hash,
        total_seconds=total_seconds,
    )
    logger.info("Disc identity read: %s (%d tracks)", disc_id, track_count)
    return identity


async def restore_identity(job_id: str) -> DiscIdentity | None:
    """Reconstruct a DiscIdentity from stored Job data.

    Returns a partial identity (no offsets/leadout) that is still sufficient
    for MusicBrainz disc ID lookup, Kashidashi matching, and source filtering.
    Returns None if the job has no stored disc_id.
    """
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if not job or not job.disc_id:
            return None

        result = await session.execute(
            select(Track).where(Track.job_id == job_id)
        )
        track_count = len(result.scalars().all())

    offsets: list[int] = []
    if job.disc_offsets:
        try:
            offsets = list(json.loads(job.disc_offsets))
        except (json.JSONDecodeError, TypeError):
            offsets = []

    identity = DiscIdentity(
        disc_id=job.disc_id,
        track_count=track_count,
        offsets=offsets,
        leadout=job.disc_leadout or 0,
        toc_hash=job.toc_hash or "",
        total_seconds=job.disc_total_seconds or 0,
    )
    logger.info("Restored identity from DB: %s (%d tracks)", job.disc_id, track_count)
    return identity
