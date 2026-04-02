"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


# --- Requests ---


class RipRequest(BaseModel):
    drive_id: str
    source_type: Optional[str] = "unknown"
    hints: Optional[dict[str, str]] = None
    force: Optional[dict[str, Any]] = None
    disc_number: Optional[int] = None
    total_discs: Optional[int] = None
    album_group: Optional[str] = None


class ImportRequest(BaseModel):
    source_type: Optional[str] = "owned"
    hints: Optional[dict[str, str]] = None
    disc_number: Optional[int] = None
    total_discs: Optional[int] = None
    album_group: Optional[str] = None


class MetadataUpdateRequest(BaseModel):
    artist: Optional[str] = None
    album: Optional[str] = None
    album_base: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    disc_number: Optional[int] = None
    total_discs: Optional[int] = None
    is_compilation: Optional[bool] = None


class TrackUpdateRequest(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    lyrics_plain: Optional[str] = None
    lyrics_synced: Optional[str] = None


# --- Responses ---


class JobResponse(BaseModel):
    job_id: str
    album_group: Optional[str] = None
    url: str
    status: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, job) -> "JobResponse":
        return cls(
            job_id=job.id,
            album_group=job.album_group,
            url=f"/job/{job.id}",
            status=job.status,
        )


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class TrackResponse(BaseModel):
    track_num: int
    title: Optional[str] = None
    artist: Optional[str] = None
    rip_status: str
    encode_status: str
    duration_ms: Optional[int] = None
    lyrics_source: Optional[str] = None

    model_config = {"from_attributes": True}


class MetadataCandidateResponse(BaseModel):
    id: int
    source: str
    source_url: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    confidence: Optional[int] = None
    selected: bool = False

    model_config = {"from_attributes": True}


class ArtworkResponse(BaseModel):
    id: int
    source: str
    url: Optional[str] = None
    local_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    selected: bool = False

    model_config = {"from_attributes": True}


class KashidashiCandidateResponse(BaseModel):
    id: int
    item_id: int
    title: Optional[str] = None
    artist: Optional[str] = None
    score: Optional[float] = None
    match_type: Optional[str] = None
    matched: bool = False

    model_config = {"from_attributes": True}


class JobMetadataResponse(BaseModel):
    artist: Optional[str] = None
    album: Optional[str] = None
    album_base: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    disc_number: int = 1
    total_discs: int = 1
    is_compilation: bool = False
    confidence: Optional[int] = None
    source: Optional[str] = None
    needs_review: bool = False
    issues: Optional[str] = None
    approved: bool = False

    model_config = {"from_attributes": True}


class JobDetailResponse(BaseModel):
    job: Any  # Job ORM object
    metadata: Optional[JobMetadataResponse] = None
    tracks: list[TrackResponse] = []
    candidates: list[MetadataCandidateResponse] = []
    artworks: list[ArtworkResponse] = []
    kashidashi_candidates: list[KashidashiCandidateResponse] = []

    model_config = {"from_attributes": True}
