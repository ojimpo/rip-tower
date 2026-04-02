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

    model_config = {"from_attributes": True}


class DriveUpdateRequest(BaseModel):
    name: str


@router.get("/drives", response_model=list[DriveResponse])
async def list_drives(session: AsyncSession = Depends(get_session)):
    """List all known drives with connection status."""
    result = await session.execute(select(Drive).order_by(Drive.created_at))
    return result.scalars().all()


@router.put("/drives/{drive_id}", response_model=DriveResponse)
async def update_drive(
    drive_id: str,
    request: DriveUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Rename a drive."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Drive not found")

    drive.name = request.name
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
