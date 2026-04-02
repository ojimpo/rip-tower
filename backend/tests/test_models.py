"""Tests for backend.models — ORM model instantiation and relationships."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import (
    Artwork,
    Drive,
    Job,
    JobMetadata,
    KashidashiCandidate,
    MetadataCandidate,
    Track,
)


class TestDriveModel:
    @pytest.mark.asyncio
    async def test_create_drive(self, db_session):
        drive = Drive(drive_id="usb-001", name="Drive A", current_path="/dev/sr0")
        db_session.add(drive)
        await db_session.commit()

        result = await db_session.get(Drive, "usb-001")
        assert result is not None
        assert result.name == "Drive A"
        assert result.current_path == "/dev/sr0"
        assert result.created_at is not None

    @pytest.mark.asyncio
    async def test_drive_nullable_path(self, db_session):
        drive = Drive(drive_id="usb-002", name="Drive B")
        db_session.add(drive)
        await db_session.commit()

        result = await db_session.get(Drive, "usb-002")
        assert result.current_path is None


class TestJobModel:
    @pytest.mark.asyncio
    async def test_create_job(self, db_session):
        job = Job(id="job-001", status="pending", source_type="owned")
        db_session.add(job)
        await db_session.commit()

        result = await db_session.get(Job, "job-001")
        assert result is not None
        assert result.status == "pending"
        assert result.source_type == "owned"
        assert result.created_at is not None

    @pytest.mark.asyncio
    async def test_job_default_status(self, db_session):
        job = Job(id="job-002")
        db_session.add(job)
        await db_session.commit()

        result = await db_session.get(Job, "job-002")
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_job_with_drive(self, db_session):
        drive = Drive(drive_id="usb-010", name="Test Drive")
        job = Job(id="job-003", drive_id="usb-010", status="pending")
        db_session.add(drive)
        db_session.add(job)
        await db_session.commit()

        result = await db_session.get(Job, "job-003")
        assert result.drive_id == "usb-010"


class TestJobMetadataModel:
    @pytest.mark.asyncio
    async def test_create_metadata(self, db_session):
        job = Job(id="job-meta-001", status="pending")
        db_session.add(job)
        await db_session.flush()

        meta = JobMetadata(
            job_id="job-meta-001",
            artist="Test Artist",
            album="Test Album",
            year=2024,
            genre="Rock",
            disc_number=1,
            total_discs=1,
        )
        db_session.add(meta)
        await db_session.commit()

        result = await db_session.get(JobMetadata, "job-meta-001")
        assert result.artist == "Test Artist"
        assert result.album == "Test Album"
        assert result.year == 2024
        assert result.is_compilation is False
        assert result.needs_review is False

    @pytest.mark.asyncio
    async def test_metadata_defaults(self, db_session):
        job = Job(id="job-meta-002", status="pending")
        db_session.add(job)
        await db_session.flush()

        meta = JobMetadata(job_id="job-meta-002")
        db_session.add(meta)
        await db_session.commit()

        result = await db_session.get(JobMetadata, "job-meta-002")
        assert result.disc_number == 1
        assert result.total_discs == 1
        assert result.is_compilation is False
        assert result.approved is False


class TestTrackModel:
    @pytest.mark.asyncio
    async def test_create_track(self, db_session):
        job = Job(id="job-track-001", status="pending")
        db_session.add(job)
        await db_session.flush()

        track = Track(
            job_id="job-track-001",
            track_num=1,
            title="Track One",
            artist="Artist",
            rip_status="ok",
        )
        db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(
            select(Track).where(Track.job_id == "job-track-001")
        )
        t = result.scalar_one()
        assert t.track_num == 1
        assert t.title == "Track One"
        assert t.rip_status == "ok"
        assert t.encode_status == "pending"

    @pytest.mark.asyncio
    async def test_multiple_tracks(self, db_session):
        job = Job(id="job-track-002", status="pending")
        db_session.add(job)
        await db_session.flush()

        for i in range(1, 4):
            track = Track(job_id="job-track-002", track_num=i, title=f"Track {i}")
            db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(
            select(Track)
            .where(Track.job_id == "job-track-002")
            .order_by(Track.track_num)
        )
        tracks = result.scalars().all()
        assert len(tracks) == 3
        assert [t.track_num for t in tracks] == [1, 2, 3]


class TestMetadataCandidateModel:
    @pytest.mark.asyncio
    async def test_create_candidate(self, db_session):
        job = Job(id="job-cand-001", status="pending")
        db_session.add(job)
        await db_session.flush()

        cand = MetadataCandidate(
            job_id="job-cand-001",
            source="musicbrainz",
            artist="Artist",
            album="Album",
            confidence=90,
        )
        db_session.add(cand)
        await db_session.commit()

        result = await db_session.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-cand-001")
        )
        c = result.scalar_one()
        assert c.source == "musicbrainz"
        assert c.confidence == 90
        assert c.selected is False


class TestArtworkModel:
    @pytest.mark.asyncio
    async def test_create_artwork(self, db_session):
        job = Job(id="job-art-001", status="pending")
        db_session.add(job)
        await db_session.flush()

        art = Artwork(
            job_id="job-art-001",
            source="coverartarchive",
            url="https://example.com/cover.jpg",
            width=500,
            height=500,
        )
        db_session.add(art)
        await db_session.commit()

        result = await db_session.execute(
            select(Artwork).where(Artwork.job_id == "job-art-001")
        )
        a = result.scalar_one()
        assert a.source == "coverartarchive"
        assert a.width == 500
        assert a.selected is False


class TestKashidashiCandidateModel:
    @pytest.mark.asyncio
    async def test_create_kashidashi(self, db_session):
        job = Job(id="job-kashi-001", status="pending")
        db_session.add(job)
        await db_session.flush()

        kc = KashidashiCandidate(
            job_id="job-kashi-001",
            item_id=42,
            title="Test CD",
            artist="Test Artist",
            score=0.95,
            match_type="exact",
        )
        db_session.add(kc)
        await db_session.commit()

        result = await db_session.execute(
            select(KashidashiCandidate).where(KashidashiCandidate.job_id == "job-kashi-001")
        )
        k = result.scalar_one()
        assert k.item_id == 42
        assert k.score == pytest.approx(0.95)
        assert k.matched is False
