"""Drive management and eject endpoints."""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Drive

logger = logging.getLogger(__name__)
router = APIRouter(tags=["drives"])


class DriveResponse(BaseModel):
    drive_id: str
    name: str
    current_path: str | None
    last_seen_at: str | None
    auto_rip: bool = False
    auto_rip_source_type: str = "unknown"

    model_config = {"from_attributes": True}


class DriveUpdateRequest(BaseModel):
    name: str | None = None
    auto_rip: bool | None = None
    auto_rip_source_type: str | None = None


async def _find_completed_rip(
    session: AsyncSession,
    disc_id: str | None,
    toc_hash: str | None,
    exclude_job_id: str | None,
):
    """Return the most recent completed Job for this physical disc, if any.

    Prefers an exact toc_hash match (SHA-256 of the raw cd-discid output) and
    only falls back to disc_id when no toc_hash is on hand (e.g. a disc known
    solely from the cached identify). disc_id alone can collide across unrelated
    borrowed CDs — see [[project_disc_swap_recurrence]] — so it's the weaker
    signal, used just so the UI can warn "you've ripped this before".
    """
    from backend.models import Job

    if toc_hash:
        cond = Job.toc_hash == toc_hash
    elif disc_id:
        cond = Job.disc_id == disc_id
    else:
        return None

    query = (
        select(Job)
        .where(cond, Job.status == "complete")
        .order_by(Job.completed_at.desc())
        .limit(1)
    )
    if exclude_job_id:
        query = query.where(Job.id != exclude_job_id)
    return (await session.execute(query)).scalar_one_or_none()


@router.get("/drives")
async def list_drives(session: AsyncSession = Depends(get_session)):
    """List all known drives with connection status and disc info."""
    from backend.models import Job, JobMetadata

    result = await session.execute(select(Drive).order_by(Drive.created_at))
    drives = result.scalars().all()

    items = []
    for drive in drives:
        # Check actual tray/disc status via ioctl
        disc_info = None
        if drive.current_path:
            from backend.services.drive_monitor import get_tray_status, CDS_DISC_OK, CDS_TRAY_OPEN
            tray_status = get_tray_status(drive.current_path)
            has_disc = tray_status == CDS_DISC_OK
            tray_open = tray_status == CDS_TRAY_OPEN
        else:
            has_disc = False
            tray_open = False
        # `review` is intentionally excluded from "active" here so a parked
        # review job doesn't block the drive — the user can swap the disc and
        # start a fresh rip while the previous job stays parked elsewhere.
        _DRIVE_ACTIVE_EXCLUDED = ["complete", "error", "review"]
        # Identity of the disc currently in the drive, used to spot a re-insert
        # of something already ripped. Filled from the active job (exact, has
        # toc_hash) or, failing that, the cached identify (disc_id only).
        disc_disc_id: str | None = None
        disc_toc_hash: str | None = None
        disc_exclude_job_id: str | None = None
        if drive.current_path:
            active_job = await session.execute(
                select(Job)
                .where(Job.drive_id == drive.drive_id)
                .where(Job.status.notin_(_DRIVE_ACTIVE_EXCLUDED))
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            active_job = active_job.scalar_one_or_none()
            if active_job and active_job.disc_id:
                meta = await session.execute(
                    select(JobMetadata).where(JobMetadata.job_id == active_job.id)
                )
                meta = meta.scalar_one_or_none()
                from backend.models import Track
                from sqlalchemy import func as sa_func
                track_count = (await session.execute(
                    select(sa_func.count()).select_from(Track).where(Track.job_id == active_job.id)
                )).scalar() or 0
                disc_info = {
                    "artist": meta.artist if meta else None,
                    "album": (meta.album_base or meta.album) if meta else None,
                    "track_count": track_count,
                }
                disc_disc_id = active_job.disc_id
                disc_toc_hash = active_job.toc_hash
                disc_exclude_job_id = active_job.id

        # Fall back to cached disc info from identify
        if not disc_info and drive.cached_disc_id:
            disc_info = {
                "artist": drive.cached_artist,
                "album": drive.cached_album,
                "track_count": drive.cached_track_count,
            }
            disc_disc_id = drive.cached_disc_id

        # Flag a disc we've already ripped before so the user doesn't re-rip a
        # CD that only *looks* unfamiliar because of a metadata mis-ID (the
        # 総合 Disc2 → "Real Music Box" re-insert, Todoist 6grvQh4Fp8pH8HCm).
        if disc_info:
            prior = await _find_completed_rip(
                session, disc_disc_id, disc_toc_hash, disc_exclude_job_id,
            )
            disc_info["already_ripped"] = prior is not None
            disc_info["ripped_job_id"] = prior.id if prior else None

        # Surface only running jobs as "active" on the drive — review is
        # excluded (see above) so the Rip button stays available.
        active_job_result = await session.execute(
            select(Job)
            .where(Job.drive_id == drive.drive_id)
            .where(Job.status.notin_(_DRIVE_ACTIVE_EXCLUDED))
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        active_job_for_drive = active_job_result.scalar_one_or_none()

        items.append({
            "drive_id": drive.drive_id,
            "name": drive.name,
            "current_path": drive.current_path,
            "last_seen_at": drive.last_seen_at.replace(tzinfo=timezone.utc).isoformat() if drive.last_seen_at else None,
            "has_disc": has_disc,
            "tray_open": tray_open,
            "disc_info": disc_info,
            "auto_rip": drive.auto_rip,
            "auto_rip_source_type": drive.auto_rip_source_type,
            "active_job_id": active_job_for_drive.id if active_job_for_drive else None,
            "active_job_status": active_job_for_drive.status if active_job_for_drive else None,
        })

    return items


@router.put("/drives/{drive_id}", response_model=DriveResponse)
async def update_drive(
    drive_id: str,
    request: DriveUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update drive settings (name, auto_rip, etc.)."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")

    if request.name is not None:
        drive.name = request.name
    if request.auto_rip is not None:
        drive.auto_rip = request.auto_rip
    if request.auto_rip_source_type is not None:
        drive.auto_rip_source_type = request.auto_rip_source_type

    await session.commit()
    return drive


@router.post("/drives/{drive_id}/eject")
async def eject_drive(
    drive_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Eject the CD from a drive."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    if not drive.current_path:
        raise HTTPException(status_code=400, detail="Drive not connected")

    proc = await asyncio.create_subprocess_exec(
        "eject", drive.current_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Eject failed: {stderr.decode().strip()}",
        )

    # Clear cached disc info
    drive.cached_disc_id = None
    drive.cached_artist = None
    drive.cached_album = None
    drive.cached_track_count = None
    await session.commit()

    # Broadcast eject event
    from backend.services.websocket import broadcast
    await broadcast("drive:disc_ejected", {
        "drive_id": drive_id,
        "name": drive.name,
    })

    return {"status": "ejected", "drive_id": drive_id}


@router.post("/drives/{drive_id}/identify")
async def identify_disc(
    drive_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Read disc identity and do a quick metadata lookup without starting a full rip."""
    from backend.services.disc_identify import identify

    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")
    if not drive.current_path:
        raise HTTPException(status_code=400, detail="Drive not connected")

    try:
        info = await identify(drive.current_path)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Save to drive cache
    drive.cached_disc_id = info.disc_id
    drive.cached_artist = info.artist
    drive.cached_album = info.album
    drive.cached_track_count = info.track_count
    await session.commit()

    return {
        "disc_id": info.disc_id,
        "track_count": info.track_count,
        "artist": info.artist,
        "album": info.album,
    }
