"""Job CRUD, rip trigger, import, metadata operations."""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Job, JobMetadata, Track, MetadataCandidate, Artwork, KashidashiCandidate
from backend.schemas import (
    JobResponse,
    JobDetailResponse,
    JobListResponse,
    RipRequest,
    ImportRequest,
    MetadataUpdateRequest,
    TrackUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


@router.post("/rip", response_model=JobResponse)
async def start_rip(
    request: RipRequest,
    session: AsyncSession = Depends(get_session),
):
    """Start a new ripping job."""
    job_id = str(uuid.uuid4())
    album_group = request.album_group or (
        str(uuid.uuid4()) if request.total_discs and request.total_discs > 1 else None
    )

    job = Job(
        id=job_id,
        album_group=album_group,
        drive_id=request.drive_id,
        status="pending",
        source_type=request.source_type or "unknown",
    )
    session.add(job)
    await session.commit()

    # Trigger pipeline in background
    from backend.services.pipeline import run_pipeline
    import asyncio

    asyncio.create_task(run_pipeline(job_id, request))

    return JobResponse(
        job_id=job_id,
        album_group=album_group,
        url=f"/job/{job_id}",
        status="pending",
    )


@router.post("/import", response_model=JobResponse)
async def import_wav(
    wav_files: list[UploadFile] = File(...),
    source_type: Optional[str] = Form("owned"),
    artist_hint: Optional[str] = Form(None),
    title_hint: Optional[str] = Form(None),
    catalog_hint: Optional[str] = Form(None),
    disc_number: Optional[int] = Form(None),
    total_discs: Optional[int] = Form(None),
    album_group: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """Import WAV files as a new job (skips identifying/ripping)."""
    job_id = str(uuid.uuid4())

    job = Job(
        id=job_id,
        album_group=album_group,
        drive_id=None,
        status="resolving",
        source_type=source_type or "owned",
    )
    session.add(job)

    # Save WAV files and create track records
    from backend.config import get_config

    config = get_config()
    import_dir = Path(config.output.incoming_dir) / job_id
    import_dir.mkdir(parents=True, exist_ok=True)

    for i, wav_file in enumerate(wav_files, 1):
        wav_path = import_dir / f"track{i:02d}.wav"
        content = await wav_file.read()
        wav_path.write_bytes(content)

        track = Track(
            job_id=job_id,
            track_num=i,
            rip_status="ok",
            wav_path=str(wav_path),
        )
        session.add(track)

    await session.commit()

    # Trigger metadata resolution
    from backend.services.pipeline import run_resolve_only
    import asyncio
    from pathlib import Path

    asyncio.create_task(run_resolve_only(job_id, {
        "artist": artist_hint,
        "title": title_hint,
        "catalog": catalog_hint,
    }))

    return JobResponse(
        job_id=job_id,
        album_group=album_group,
        url=f"/job/{job_id}",
        status="resolving",
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """List jobs with optional filters."""
    query = select(Job).order_by(Job.created_at.desc())
    if status:
        query = query.where(Job.status == status)
    if source_type:
        query = query.where(Job.source_type == source_type)

    result = await session.execute(query)
    jobs = result.scalars().all()
    return JobListResponse(jobs=[JobResponse.from_orm(j) for j in jobs])


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full job details including metadata, tracks, candidates, etc."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load related data
    metadata = await session.execute(
        select(JobMetadata).where(JobMetadata.job_id == job_id)
    )
    tracks = await session.execute(
        select(Track).where(Track.job_id == job_id).order_by(Track.track_num)
    )
    candidates = await session.execute(
        select(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
    )
    artworks = await session.execute(
        select(Artwork).where(Artwork.job_id == job_id)
    )
    kashidashi = await session.execute(
        select(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id)
    )

    return JobDetailResponse(
        job=job,
        metadata=metadata.scalar_one_or_none(),
        tracks=tracks.scalars().all(),
        candidates=candidates.scalars().all(),
        artworks=artworks.scalars().all(),
        kashidashi_candidates=kashidashi.scalars().all(),
    )


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a job and its associated files."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Clean up files
    from backend.config import get_config
    from pathlib import Path
    import shutil

    config = get_config()
    incoming = Path(config.output.incoming_dir) / job_id
    if incoming.exists():
        shutil.rmtree(incoming)

    await session.delete(job)
    await session.commit()
    return {"status": "deleted"}


@router.put("/jobs/{job_id}/metadata")
async def update_metadata(
    job_id: str,
    request: MetadataUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Manually edit job metadata."""
    meta = await session.execute(
        select(JobMetadata).where(JobMetadata.job_id == job_id)
    )
    meta = meta.scalar_one_or_none()
    if not meta:
        raise HTTPException(status_code=404, detail="Metadata not found")

    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(meta, field, value)

    await session.commit()
    return {"status": "updated"}


@router.post("/jobs/{job_id}/metadata/approve")
async def approve_metadata(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Approve metadata and proceed to finalizing."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "review":
        raise HTTPException(status_code=400, detail=f"Job is in '{job.status}', not 'review'")

    meta = await session.execute(
        select(JobMetadata).where(JobMetadata.job_id == job_id)
    )
    meta = meta.scalar_one_or_none()
    if meta:
        meta.approved = True
        from datetime import datetime, timezone
        meta.approved_at = datetime.now(timezone.utc)

    job.status = "finalizing"
    await session.commit()

    from backend.services.pipeline import run_finalize
    import asyncio

    asyncio.create_task(run_finalize(job_id))

    return {"status": "finalizing"}


@router.post("/jobs/{job_id}/metadata/apply")
async def apply_metadata(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Re-apply metadata to encoded files (post-complete editing)."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "complete":
        raise HTTPException(status_code=400, detail="Job must be complete to re-apply")

    from backend.services.finalizer import reapply_metadata
    import asyncio

    asyncio.create_task(reapply_metadata(job_id))

    return {"status": "applying"}


@router.post("/jobs/{job_id}/metadata/re-resolve")
async def re_resolve(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Re-run metadata resolution with new hints."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from backend.services.pipeline import run_resolve_only
    import asyncio

    asyncio.create_task(run_resolve_only(job_id, {}))
    return {"status": "re-resolving"}


@router.post("/jobs/{job_id}/re-rip")
async def re_rip(
    job_id: str,
    drive_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Re-rip all tracks."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from backend.services.pipeline import run_re_rip
    import asyncio

    asyncio.create_task(run_re_rip(job_id, drive_id))
    return {"status": "re-ripping"}


@router.post("/jobs/{job_id}/re-rip/{track_num}")
async def re_rip_track(
    job_id: str,
    track_num: int,
    drive_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Re-rip a specific track."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from backend.services.pipeline import run_re_rip_track
    import asyncio

    asyncio.create_task(run_re_rip_track(job_id, track_num, drive_id))
    return {"status": "re-ripping"}


@router.post("/jobs/{job_id}/tracks/{track_num}/upload-wav")
async def upload_wav_replacement(
    job_id: str,
    track_num: int,
    wav_file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload a WAV file to replace a failed track."""
    track = await session.execute(
        select(Track).where(Track.job_id == job_id, Track.track_num == track_num)
    )
    track = track.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    from backend.config import get_config
    from pathlib import Path

    config = get_config()
    wav_dir = Path(config.output.incoming_dir) / job_id
    wav_dir.mkdir(parents=True, exist_ok=True)
    wav_path = wav_dir / f"track{track_num:02d}.wav"

    content = await wav_file.read()
    wav_path.write_bytes(content)

    track.wav_path = str(wav_path)
    track.rip_status = "ok"
    await session.commit()

    return {"status": "replaced", "track_num": track_num}


@router.put("/jobs/{job_id}/candidates/{candidate_id}/select")
async def select_candidate(
    job_id: str,
    candidate_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Select a metadata candidate and update job metadata."""
    # Deselect all candidates for this job
    candidates = await session.execute(
        select(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
    )
    for c in candidates.scalars():
        c.selected = False

    # Select the chosen one
    candidate = await session.get(MetadataCandidate, candidate_id)
    if not candidate or candidate.job_id != job_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.selected = True

    # Update job metadata from selected candidate
    meta = await session.execute(
        select(JobMetadata).where(JobMetadata.job_id == job_id)
    )
    meta = meta.scalar_one_or_none()
    if meta:
        meta.artist = candidate.artist
        meta.album = candidate.album
        meta.year = candidate.year
        meta.genre = candidate.genre
        meta.confidence = candidate.confidence
        meta.source = candidate.source
        meta.source_url = candidate.source_url

    await session.commit()
    return {"status": "selected"}


@router.put("/jobs/{job_id}/tracks/{track_num}")
async def update_track(
    job_id: str,
    track_num: int,
    request: TrackUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update track title, artist, or lyrics."""
    track = await session.execute(
        select(Track).where(Track.job_id == job_id, Track.track_num == track_num)
    )
    track = track.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(track, field, value)

    await session.commit()
    return {"status": "updated"}


@router.post("/jobs/{job_id}/tracks/{track_num}/lyrics/fetch")
async def fetch_track_lyrics(
    job_id: str,
    track_num: int,
    session: AsyncSession = Depends(get_session),
):
    """Fetch lyrics for a specific track."""
    from backend.metadata.lyrics import fetch_lyrics_for_track

    await fetch_lyrics_for_track(job_id, track_num)
    return {"status": "fetched"}


@router.get("/jobs/{job_id}/artworks")
async def list_artworks(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List artwork candidates for a job."""
    artworks = await session.execute(
        select(Artwork).where(Artwork.job_id == job_id)
    )
    return artworks.scalars().all()


@router.post("/jobs/{job_id}/artworks/upload")
async def upload_artwork(
    job_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Upload a custom artwork."""
    from backend.config import get_config
    from pathlib import Path
    from PIL import Image
    import io

    config = get_config()
    art_dir = Path(config.output.incoming_dir) / job_id / "_artwork"
    art_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    img = Image.open(io.BytesIO(content))
    local_path = art_dir / f"manual_{file.filename}"
    img.save(str(local_path), "JPEG")

    artwork = Artwork(
        job_id=job_id,
        source="manual",
        local_path=str(local_path),
        width=img.width,
        height=img.height,
        file_size=len(content),
        selected=False,
    )
    session.add(artwork)
    await session.commit()
    return {"status": "uploaded", "id": artwork.id}


@router.put("/jobs/{job_id}/artworks/{artwork_id}/select")
async def select_artwork(
    job_id: str,
    artwork_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Select an artwork candidate."""
    artworks = await session.execute(
        select(Artwork).where(Artwork.job_id == job_id)
    )
    for a in artworks.scalars():
        a.selected = False

    artwork = await session.get(Artwork, artwork_id)
    if not artwork or artwork.job_id != job_id:
        raise HTTPException(status_code=404, detail="Artwork not found")
    artwork.selected = True

    await session.commit()
    return {"status": "selected"}


@router.get("/jobs/{job_id}/kashidashi")
async def list_kashidashi(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List kashidashi match candidates."""
    candidates = await session.execute(
        select(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id)
    )
    return candidates.scalars().all()


@router.put("/jobs/{job_id}/kashidashi/{candidate_id}/match")
async def match_kashidashi(
    job_id: str,
    candidate_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Confirm a kashidashi match."""
    candidates = await session.execute(
        select(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id)
    )
    for c in candidates.scalars():
        c.matched = False

    candidate = await session.get(KashidashiCandidate, candidate_id)
    if not candidate or candidate.job_id != job_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.matched = True

    await session.commit()
    return {"status": "matched"}


@router.post("/jobs/{job_id}/kashidashi/skip")
async def skip_kashidashi(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Skip kashidashi matching."""
    candidates = await session.execute(
        select(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id)
    )
    for c in candidates.scalars():
        c.matched = False

    await session.commit()
    return {"status": "skipped"}


@router.post("/jobs/{job_id}/re-rip/failed")
async def re_rip_failed(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Re-rip only failed tracks."""
    failed = await session.execute(
        select(Track)
        .where(Track.job_id == job_id, Track.rip_status == "failed")
    )
    track_nums = [t.track_num for t in failed.scalars()]
    if not track_nums:
        return {"status": "no_failed_tracks"}

    from backend.services.pipeline import run_re_rip_track
    import asyncio

    for num in track_nums:
        asyncio.create_task(run_re_rip_track(job_id, num, None))

    return {"status": "re-ripping", "tracks": track_nums}
