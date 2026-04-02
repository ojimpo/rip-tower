"""Integration tests for API endpoints using FastAPI TestClient + httpx."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.models import Drive, Job, JobMetadata


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
