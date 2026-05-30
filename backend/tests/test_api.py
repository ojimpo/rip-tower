"""Integration tests for API endpoints using FastAPI TestClient + httpx."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.models import Artwork, Drive, Job, JobMetadata


class TestDrivesAPI:
    """Tests for GET /api/drives."""

    @pytest.mark.asyncio
    async def test_list_drives_empty(self, client):
        resp = await client.get("/api/drives")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_drives_with_data(self, client, db_session):
        drive = Drive(
            drive_id="usb-api-001",
            name="Test Drive",
            current_path="/dev/sr0",
        )
        db_session.add(drive)
        await db_session.commit()

        resp = await client.get("/api/drives")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["drive_id"] == "usb-api-001"
        assert data[0]["name"] == "Test Drive"
        assert data[0]["current_path"] == "/dev/sr0"

    @pytest.mark.asyncio
    async def test_review_job_does_not_block_drive(self, client, db_session):
        """A job parked in review should not claim the drive's active slot —
        the user must be able to swap the disc and start a fresh rip."""
        drive = Drive(
            drive_id="usb-api-review",
            name="Drive-A",
            current_path="/dev/sr0",
        )
        db_session.add(drive)
        db_session.add(Job(
            id="job-review",
            status="review",
            source_type="owned",
            drive_id="usb-api-review",
        ))
        await db_session.commit()

        resp = await client.get("/api/drives")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["active_job_id"] is None
        assert data[0]["active_job_status"] is None

    @pytest.mark.asyncio
    async def test_running_job_blocks_drive(self, client, db_session):
        """Sanity check: a still-running job (e.g. ripping) must claim the
        drive — otherwise we'd let a second rip race the first."""
        drive = Drive(
            drive_id="usb-api-busy",
            name="Drive-B",
            current_path="/dev/sr0",
        )
        db_session.add(drive)
        db_session.add(Job(
            id="job-ripping",
            status="ripping",
            source_type="owned",
            drive_id="usb-api-busy",
        ))
        await db_session.commit()

        resp = await client.get("/api/drives")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["active_job_id"] == "job-ripping"
        assert data[0]["active_job_status"] == "ripping"


class TestJobsAPI:
    """Tests for GET /api/jobs."""

    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client):
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []

    @pytest.mark.asyncio
    async def test_list_jobs_with_data(self, client, db_session):
        job = Job(id="job-api-001", status="pending", source_type="owned")
        db_session.add(job)
        await db_session.commit()

        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["job_id"] == "job-api-001"
        assert data["jobs"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_jobs_filter_by_status(self, client, db_session):
        db_session.add(Job(id="j1", status="pending", source_type="owned"))
        db_session.add(Job(id="j2", status="complete", source_type="owned"))
        await db_session.commit()

        resp = await client.get("/api/jobs?status=complete")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["job_id"] == "j2"


class TestSettingsAPI:
    """Tests for GET/PUT /api/settings."""

    @pytest.mark.asyncio
    async def test_get_settings_defaults(self, client, tmp_path, monkeypatch):
        config_path = tmp_path / "test_config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["output"]["format"] == "flac"
        assert data["general"]["auto_approve_threshold"] == 85

    @pytest.mark.asyncio
    async def test_update_settings(self, client, tmp_path, monkeypatch):
        config_path = tmp_path / "test_config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        new_settings = {
            "general": {
                "auto_approve_threshold": 50,
                "reminder_initial_hours": 6,
                "reminder_interval_hours": 24,
            },
            "output": {
                "format": "opus",
                "quality": 128,
                "music_dir": "/mnt/media/music",
                "incoming_dir": "/mnt/media/audio/_incoming",
                "folder_template": "{artist}/{album}",
                "file_template": "{track_num} {artist} - {title}",
            },
            "integrations": {
                "discord_webhook": "",
                "discogs_token": "",
                "musixmatch_token": "",
                "plex_section_id": None,
                "llm_api_key": "",
                "llm_model": "haiku",
                "kashidashi_url": "http://kashidashi-app-web-1:18080",
            },
        }

        resp = await client.put("/api/settings", json=new_settings)
        assert resp.status_code == 200
        data = resp.json()
        assert data["general"]["auto_approve_threshold"] == 50
        assert data["output"]["format"] == "opus"

        # Verify persistence
        resp2 = await client.get("/api/settings")
        assert resp2.json()["output"]["format"] == "opus"


class TestHistoryAPI:
    """Tests for GET /api/history/stats."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, client):
        resp = await client.get("/api/history/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["by_source_type"] == {}

    @pytest.mark.asyncio
    async def test_stats_with_completed_jobs(self, client, db_session):
        from datetime import datetime, timezone

        db_session.add(Job(
            id="hist-1", status="complete", source_type="owned",
            completed_at=datetime.now(timezone.utc),
        ))
        db_session.add(Job(
            id="hist-2", status="complete", source_type="rental",
            completed_at=datetime.now(timezone.utc),
        ))
        db_session.add(Job(
            id="hist-3", status="pending", source_type="owned",
        ))
        await db_session.commit()

        resp = await client.get("/api/history/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["by_source_type"]["owned"] == 1
        assert data["by_source_type"]["rental"] == 1

    @pytest.mark.asyncio
    async def test_history_list_empty(self, client):
        resp = await client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["offset"] == 0


class TestArtworkDelete:
    """DELETE /api/jobs/{id}/artworks/{artwork_id}."""

    @pytest.mark.asyncio
    async def test_delete_unselected_artwork_removes_row(
        self, client, db_session, tmp_path,
    ):
        db_session.add(Job(id="art-job", status="review", source_type="owned"))
        f = tmp_path / "extra.jpg"
        f.write_bytes(b"\xff\xd8\xff\xd9")
        unselected = Artwork(
            job_id="art-job", source="discogs",
            local_path=str(f), selected=False,
        )
        selected = Artwork(
            job_id="art-job", source="musicbrainz",
            local_path=None, selected=True,
        )
        db_session.add_all([unselected, selected])
        await db_session.commit()
        await db_session.refresh(unselected)

        resp = await client.delete(f"/api/jobs/art-job/artworks/{unselected.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["was_selected"] is False
        assert not f.exists()  # file unlinked

        listed = await client.get("/api/jobs/art-job/artworks")
        ids = [a["id"] for a in listed.json()]
        assert unselected.id not in ids
        assert selected.id in ids

    @pytest.mark.asyncio
    async def test_delete_selected_promotes_remaining(
        self, client, db_session,
    ):
        db_session.add(Job(id="art-job-2", status="review", source_type="owned"))
        sel = Artwork(job_id="art-job-2", source="musicbrainz", selected=True)
        other = Artwork(
            job_id="art-job-2", source="itunes",
            file_size=1000, selected=False,
        )
        db_session.add_all([sel, other])
        await db_session.commit()
        await db_session.refresh(sel)
        await db_session.refresh(other)

        resp = await client.delete(f"/api/jobs/art-job-2/artworks/{sel.id}")
        assert resp.status_code == 200
        assert resp.json()["was_selected"] is True

        # The remaining artwork should now be selected.
        listed = await client.get("/api/jobs/art-job-2/artworks")
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["id"] == other.id
        assert rows[0]["selected"] is True

    @pytest.mark.asyncio
    async def test_delete_unknown_returns_404(self, client, db_session):
        db_session.add(Job(id="art-job-3", status="review", source_type="owned"))
        await db_session.commit()
        resp = await client.delete("/api/jobs/art-job-3/artworks/99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_wrong_job_returns_404(self, client, db_session):
        db_session.add(Job(id="art-job-4", status="review", source_type="owned"))
        db_session.add(Job(id="art-job-5", status="review", source_type="owned"))
        a = Artwork(job_id="art-job-4", source="musicbrainz", selected=True)
        db_session.add(a)
        await db_session.commit()
        await db_session.refresh(a)
        # Same artwork id but wrong job
        resp = await client.delete(f"/api/jobs/art-job-5/artworks/{a.id}")
        assert resp.status_code == 404


# ───────────────── Fix D: Recent list sorts by confirmation time ─────────────────


@pytest.mark.asyncio
async def test_list_jobs_includes_completed_at(client, db_session):
    """The jobs list must expose completed_at so the dashboard 'Recent' section
    can order by confirmation time rather than creation time."""
    from datetime import datetime

    db_session.add(
        Job(id="job-ca", status="complete", source_type="owned",
            completed_at=datetime(2026, 5, 29, 12, 0, 0))
    )
    await db_session.commit()

    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    summary = resp.json()["jobs"][0]
    assert "completed_at" in summary
    assert summary["completed_at"] is not None
