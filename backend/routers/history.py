"""Ripping history and statistics endpoints."""

from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Job, JobMetadata, Track, KashidashiCandidate

router = APIRouter(tags=["history"])


class HistoryItem(BaseModel):
    job_id: str
    artist: str | None
    album: str | None
    source_type: str
    completed_at: str | None

    model_config = {"from_attributes": True}


class HistoryStats(BaseModel):
    total: int
    by_source_type: dict[str, int]


@router.get("/history")
async def get_history(
    source_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Get ripping history with pagination."""
    query = (
        select(Job, JobMetadata)
        .outerjoin(JobMetadata, Job.id == JobMetadata.job_id)
        .where(Job.status == "complete")
        .order_by(Job.completed_at.desc())
    )
    if source_type:
        query = query.where(Job.source_type == source_type)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)

    items = []
    for job, meta in result.all():
        # Track count
        track_count_result = await session.execute(
            select(func.count()).select_from(Track).where(Track.job_id == job.id)
        )
        track_count = track_count_result.scalar() or 0

        # Kashidashi match
        kashi = await session.execute(
            select(KashidashiCandidate)
            .where(KashidashiCandidate.job_id == job.id, KashidashiCandidate.matched == True)
        )
        kashi = kashi.scalar_one_or_none()

        items.append({
            "job_id": job.id,
            "artist": meta.artist if meta else None,
            "album": (meta.album_base or meta.album) if meta else None,
            "source_type": job.source_type,
            "completed_at": job.completed_at.replace(tzinfo=timezone.utc).isoformat() if job.completed_at else None,
            "track_count": track_count or None,
            "artwork_url": None,
            "disc_number": meta.disc_number if meta else None,
            "total_discs": meta.total_discs if meta else None,
            "kashidashi_id": str(kashi.item_id) if kashi else None,
        })

    # Total count for pagination
    total_query = select(func.count()).select_from(Job).where(Job.status == "complete")
    if source_type:
        total_query = total_query.where(Job.source_type == source_type)
    total = (await session.execute(total_query)).scalar() or 0

    return {
        "items": items,
        "total": total,
        "has_more": offset + limit < total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/history/stats", response_model=HistoryStats)
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Get ripping statistics."""
    total_result = await session.execute(
        select(func.count()).select_from(Job).where(Job.status == "complete")
    )
    total = total_result.scalar() or 0

    breakdown_result = await session.execute(
        select(Job.source_type, func.count())
        .where(Job.status == "complete")
        .group_by(Job.source_type)
    )
    by_source = {row[0]: row[1] for row in breakdown_result.all()}

    return HistoryStats(total=total, by_source_type=by_source)
