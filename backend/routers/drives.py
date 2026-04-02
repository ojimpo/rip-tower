"""Drive management and eject endpoints."""

import asyncio
import logging

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


@router.get("/drives")
async def list_drives(session: AsyncSession = Depends(get_session)):
    """List all known drives with connection status and disc info."""
    from backend.models import Job, JobMetadata

    result = await session.execute(select(Drive).order_by(Drive.created_at))
    drives = result.scalars().all()

    items = []
    for drive in drives:
        # Check if there's an active job on this drive (disc info)
        disc_info = None
        has_disc = drive.current_path is not None
        if drive.current_path:
            active_job = await session.execute(
                select(Job)
                .where(Job.drive_id == drive.drive_id)
                .where(Job.status.notin_(["complete", "error"]))
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
                    "album": meta.album if meta else None,
                    "track_count": track_count,
                }

        # Check if there's an active (non-complete/error) job on this drive
        active_job_result = await session.execute(
            select(Job)
            .where(Job.drive_id == drive.drive_id)
            .where(Job.status.notin_(["complete", "error"]))
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        active_job_for_drive = active_job_result.scalar_one_or_none()

        items.append({
            "drive_id": drive.drive_id,
            "name": drive.name,
            "current_path": drive.current_path,
            "last_seen_at": drive.last_seen_at.isoformat() if drive.last_seen_at else None,
            "has_disc": has_disc,
            "disc_info": disc_info,
            "auto_rip": drive.auto_rip,
            "auto_rip_source_type": drive.auto_rip_source_type,
            "active_job_id": active_job_for_drive.id if active_job_for_drive else None,
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

    return {"status": "ejected", "drive_id": drive_id}
