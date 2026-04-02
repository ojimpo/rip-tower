"""SQLAlchemy ORM models."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Float,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Drive(Base):
    __tablename__ = "drives"

    drive_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    current_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    album_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("drives.drive_id"), nullable=True
    )
    disc_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    toc_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    source_type: Mapped[str] = mapped_column(Text, default="unknown")
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    job_metadata: Mapped["JobMetadata | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    tracks: Mapped[list["Track"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Track.track_num"
    )
    candidates: Mapped[list["MetadataCandidate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    artworks: Mapped[list["Artwork"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    kashidashi_candidates: Mapped[list["KashidashiCandidate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    drive: Mapped["Drive | None"] = relationship()


class JobMetadata(Base):
    __tablename__ = "job_metadata"

    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id"), primary_key=True
    )
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    album: Mapped[str | None] = mapped_column(Text, nullable=True)
    album_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genre: Mapped[str | None] = mapped_column(Text, nullable=True)
    disc_number: Mapped[int] = mapped_column(Integer, default=1)
    total_discs: Mapped[int] = mapped_column(Integer, default=1)
    is_compilation: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    issues: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="job_metadata")


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), nullable=False)
    track_num: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    rip_status: Mapped[str] = mapped_column(Text, default="pending")
    encode_status: Mapped[str] = mapped_column(Text, default="pending")
    wav_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    encoded_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lyrics_plain: Mapped[str | None] = mapped_column(Text, nullable=True)
    lyrics_synced: Mapped[str | None] = mapped_column(Text, nullable=True)
    lyrics_source: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("job_id", "track_num"),)

    job: Mapped["Job"] = relationship(back_populates="tracks")


class MetadataCandidate(Base):
    __tablename__ = "metadata_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    album: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genre: Mapped[str | None] = mapped_column(Text, nullable=True)
    track_titles: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped["Job"] = relationship(back_populates="candidates")


class Artwork(Base):
    __tablename__ = "artworks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped["Job"] = relationship(back_populates="artworks")


class KashidashiCandidate(Base):
    __tablename__ = "kashidashi_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped["Job"] = relationship(back_populates="kashidashi_candidates")
