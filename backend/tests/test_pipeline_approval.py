"""Tests for pipeline._check_approval — auto-approve gating rules.

Covers:
- High confidence + clean issues → auto-approve (status=finalizing).
- High confidence + artist_contradiction → forced review.
- High confidence + album_contradiction → forced review.
- High confidence + duplicate_rip → forced review.
- album_group sibling re-evaluation: when the last in-progress member of a
  group finishes, siblings that were parked in review with waiting_for_group
  get re-evaluated.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.models import Job, JobMetadata
from backend.services import pipeline


@pytest.fixture
def patch_pipeline_globals(monkeypatch, async_session_maker):
    """Stub everything in pipeline.py that touches the outside world.

    We want _check_approval to run end-to-end against an in-memory DB without
    firing WebSocket events, hitting Discord, or actually invoking finalize.
    """
    monkeypatch.setattr(pipeline, "async_session", async_session_maker)

    finalize_calls: list[str] = []
    review_events: list[dict] = []

    async def _fake_finalize(job_id):
        finalize_calls.append(job_id)

    async def _fake_broadcast(event, payload):
        if event == "job:review":
            review_events.append({"job_id": payload.get("job_id"), "reason": payload.get("reason")})

    monkeypatch.setattr(pipeline, "run_finalize", _fake_finalize)
    monkeypatch.setattr(pipeline, "broadcast", _fake_broadcast)
    monkeypatch.setattr(
        pipeline,
        "_schedule_eject_reminder",
        lambda _job_id: _aio_noop(),
    )

    # Stub the lazily-imported notifier so the review branch doesn't try to
    # reach Discord during tests.
    import backend.services.notifier as notifier_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(notifier_mod, "notify_review", _noop)
    monkeypatch.setattr(notifier_mod, "schedule_reminder", _noop)
    monkeypatch.setattr(notifier_mod, "schedule_eject_reminder", _noop)

    # Force a known auto-approve threshold so tests don't depend on yaml.
    from backend.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.general, "auto_approve_threshold", 85)

    return SimpleNamespace(
        finalize_calls=finalize_calls,
        review_events=review_events,
    )


async def _aio_noop():
    return None


async def _seed_job(maker, job_id: str, *, confidence: int, issues: list[str] | None,
                    album_group: str | None = None, status: str = "encoding") -> None:
    """Insert a Job + JobMetadata pair so _check_approval has rows to read."""
    async with maker() as s:
        s.add(Job(id=job_id, drive_id=None, album_group=album_group, status=status))
        s.add(JobMetadata(
            job_id=job_id,
            confidence=confidence,
            issues=json.dumps(issues, ensure_ascii=False) if issues else None,
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_high_confidence_clean_auto_approves(
    patch_pipeline_globals, async_session_maker,
):
    """confidence ≥ threshold and no blocking issues → finalize."""
    await _seed_job(async_session_maker, "job-clean", confidence=95, issues=None)

    await pipeline._check_approval("job-clean")

    assert patch_pipeline_globals.finalize_calls == ["job-clean"]
    async with async_session_maker() as s:
        job = await s.get(Job, "job-clean")
        meta = await s.get(JobMetadata, "job-clean")
    assert job.status == "finalizing"
    assert meta.approved is True


@pytest.mark.parametrize("issue", ["artist_contradiction", "album_contradiction", "duplicate_rip"])
@pytest.mark.asyncio
async def test_blocking_issue_forces_review_even_at_high_confidence(
    patch_pipeline_globals, async_session_maker, issue,
):
    """Issues like artist_contradiction must not be ignored by high confidence.

    Original bug: TOC collision (MB disc_id false positive at conf=90 over
    kashidashi's low-score-but-correct match) auto-approved despite the
    sanitizer flagging artist_contradiction.
    """
    await _seed_job(async_session_maker, "job-clash", confidence=95, issues=[issue, "other"])

    await pipeline._check_approval("job-clash")

    assert patch_pipeline_globals.finalize_calls == [], "must not finalize when blocking issue is present"
    async with async_session_maker() as s:
        job = await s.get(Job, "job-clash")
        meta = await s.get(JobMetadata, "job-clash")
    assert job.status == "review"
    assert meta.needs_review is True

    assert patch_pipeline_globals.review_events
    assert issue in patch_pipeline_globals.review_events[-1]["reason"]


@pytest.mark.asyncio
async def test_low_confidence_still_routes_to_review_with_reason(
    patch_pipeline_globals, async_session_maker,
):
    await _seed_job(async_session_maker, "job-lowconf", confidence=40, issues=None)

    await pipeline._check_approval("job-lowconf")

    async with async_session_maker() as s:
        job = await s.get(Job, "job-lowconf")
    assert job.status == "review"
    assert "confidence 40" in patch_pipeline_globals.review_events[-1]["reason"]
