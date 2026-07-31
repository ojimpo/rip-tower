"""Tests for per-drive rip reliability reporting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import Drive, RipAttempt


def _attempts(drive_id: str, job_id: str, track_num: int, outcomes: list[str]):
    """One track's attempt chain, e.g. ["timeout", "timeout", "ok"]."""
    return [
        RipAttempt(
            drive_id=drive_id,
            job_id=job_id,
            track_num=track_num,
            attempt=i,
            tool="cd-paranoia",
            outcome=outcome,
            duration_ms=1000,
        )
        for i, outcome in enumerate(outcomes, 1)
    ]


async def _seed(db_session, drive_id: str, chains: list[list[str]]):
    db_session.add(Drive(drive_id=drive_id, name=drive_id, current_path="/dev/sr0"))
    for n, outcomes in enumerate(chains, 1):
        for row in _attempts(drive_id, "job-1", n, outcomes):
            db_session.add(row)
    await db_session.commit()


class TestDriveHealth:
    @pytest.mark.asyncio
    async def test_clean_drive_reports_healthy(self, client, db_session):
        await _seed(db_session, "good-drive", [["ok"]] * 12)

        resp = await client.get("/api/drives/health")
        assert resp.status_code == 200
        h = resp.json()[0]
        assert h["status"] == "healthy"
        assert h["tracks"] == 12
        assert h["clean"] == 12
        assert h["degraded"] == 0
        assert h["clean_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_drive_needing_fallbacks_reports_failing(self, client, db_session):
        """The LT-01 pattern: tracks still succeed, but only after timing out
        and dropping to a degraded pass. That must not read as healthy."""
        chains = [["ok"]] * 6 + [["timeout", "ok"]] * 4 + [["timeout", "timeout", "ok"]] * 2
        await _seed(db_session, "bad-drive", chains)

        h = (await client.get("/api/drives/health")).json()[0]
        assert h["status"] == "failing"
        assert h["clean"] == 6
        assert h["degraded"] == 6
        assert h["timeouts"] == 8
        assert h["failed"] == 0

    @pytest.mark.asyncio
    async def test_unreadable_tracks_count_as_failed(self, client, db_session):
        chains = [["ok"]] * 8 + [["timeout", "timeout", "error"]] * 4
        await _seed(db_session, "dying-drive", chains)

        h = (await client.get("/api/drives/health")).json()[0]
        assert h["failed"] == 4
        assert h["status"] == "failing"

    @pytest.mark.asyncio
    async def test_too_few_samples_is_unknown(self, client, db_session):
        await _seed(db_session, "new-drive", [["ok"]] * 3)

        h = (await client.get("/api/drives/health")).json()[0]
        assert h["status"] == "unknown"
        assert h["tracks"] == 3

    @pytest.mark.asyncio
    async def test_old_attempts_fall_out_of_the_window(self, client, db_session):
        """Health must reflect the drive now, not a bad patch from last year."""
        db_session.add(Drive(drive_id="d1", name="d1", current_path="/dev/sr0"))
        stale = datetime.now(timezone.utc) - timedelta(days=200)
        for n in range(1, 13):
            db_session.add(RipAttempt(
                drive_id="d1", job_id="old-job", track_num=n, attempt=1,
                tool="cd-paranoia", outcome="error", created_at=stale,
            ))
        await db_session.commit()

        h = (await client.get("/api/drives/health")).json()[0]
        assert h["tracks"] == 0
        assert h["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_worst_drive_is_listed_first(self, client, db_session):
        await _seed(db_session, "healthy-one", [["ok"]] * 12)
        for n in range(1, 13):
            for row in _attempts("failing-one", "job-2", n, ["timeout", "ok"]):
                db_session.add(row)
        db_session.add(Drive(drive_id="failing-one", name="failing-one"))
        await db_session.commit()

        names = [d["drive_id"] for d in (await client.get("/api/drives/health")).json()]
        assert names[0] == "failing-one"

    @pytest.mark.asyncio
    async def test_drive_list_includes_health(self, client, db_session):
        await _seed(db_session, "listed", [["ok"]] * 12)

        d = (await client.get("/api/drives")).json()[0]
        assert d["health"]["status"] == "healthy"
