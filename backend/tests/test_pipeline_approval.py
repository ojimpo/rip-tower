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


# ─────────────────────── album_group sibling re-evaluation ───────────────────────


@pytest.mark.asyncio
async def test_group_sibling_parks_with_waiting_for_group_when_others_in_progress(
    patch_pipeline_globals, async_session_maker,
):
    """A group member that finishes ahead of its siblings parks in review and
    marks itself with waiting_for_group so the last sibling can wake it."""
    await _seed_job(
        async_session_maker, "disc-a", confidence=95, issues=None,
        album_group="grp-1", status="encoding",
    )
    await _seed_job(
        async_session_maker, "disc-b", confidence=95, issues=None,
        album_group="grp-1", status="ripping",
    )

    await pipeline._check_approval("disc-a")

    assert patch_pipeline_globals.finalize_calls == [], (
        "must wait for sibling, not finalize"
    )
    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "disc-a")
        job = await s.get(Job, "disc-a")
    assert job.status == "review"
    assert "waiting_for_group" in json.loads(meta.issues)


@pytest.mark.asyncio
async def test_last_sibling_finish_wakes_parked_group_members(
    patch_pipeline_globals, async_session_maker,
):
    """Reproduces Todoist 6gf83JC86P4v7h4m bug #3: a sibling parked with
    waiting_for_group stayed stuck even after the other discs finished.

    Setup: disc-a already parked (status=review, issues=['waiting_for_group']),
    disc-b finishes encoding and runs _check_approval. The last sibling's
    decision should now wake disc-a and let it auto-approve too.
    """
    # disc-a was parked earlier
    async with async_session_maker() as s:
        s.add(Job(id="disc-a", album_group="grp-2", status="review"))
        s.add(JobMetadata(
            job_id="disc-a",
            confidence=95,
            needs_review=True,
            issues=json.dumps(["waiting_for_group"]),
        ))
        # disc-b just finished encoding, about to be evaluated
        s.add(Job(id="disc-b", album_group="grp-2", status="encoding"))
        s.add(JobMetadata(job_id="disc-b", confidence=95))
        await s.commit()

    await pipeline._check_approval("disc-b")

    # Both should now be in finalizing — disc-b directly, disc-a via the
    # sibling wake-up that follows.
    assert sorted(patch_pipeline_globals.finalize_calls) == ["disc-a", "disc-b"]

    async with async_session_maker() as s:
        a = await s.get(Job, "disc-a")
        a_meta = await s.get(JobMetadata, "disc-a")
        b = await s.get(Job, "disc-b")
    assert a.status == "finalizing"
    assert b.status == "finalizing"
    # waiting_for_group must be cleared after re-evaluation succeeds.
    assert not a_meta.issues or "waiting_for_group" not in json.loads(a_meta.issues)


# ────────────────────────── duplicate-rip detection ──────────────────────────


@pytest.mark.asyncio
async def test_detect_duplicate_rip_flags_metadata_when_prior_complete_job_exists(
    patch_pipeline_globals, async_session_maker,
):
    """Reproduces Todoist 6gf83JC86P4v7h4m bug #1: ripping a disc whose TOC
    matches a prior complete job must surface duplicate_rip so the next
    auto-approve gate (Bug A) keeps the user in review."""
    async with async_session_maker() as s:
        s.add(Job(
            id="old-job",
            toc_hash="abc123",
            status="complete",
            output_dir="/music/Artist/Album",
        ))
        s.add(Job(id="new-job", toc_hash="abc123", status="ripping"))
        s.add(JobMetadata(job_id="new-job"))
        await s.commit()

    await pipeline._detect_duplicate_rip("new-job", "abc123")

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "new-job")
    issues = json.loads(meta.issues)
    assert "duplicate_rip" in issues
    assert any(i == "duplicate_of_old-job" for i in issues)
    assert meta.needs_review is True


@pytest.mark.asyncio
async def test_detect_duplicate_rip_no_match_is_noop(
    patch_pipeline_globals, async_session_maker,
):
    """Without a matching prior complete job, no issue is added — the typical
    first-rip case must not be flagged."""
    async with async_session_maker() as s:
        s.add(Job(id="new-job", toc_hash="xyz", status="ripping"))
        s.add(JobMetadata(job_id="new-job"))
        await s.commit()

    await pipeline._detect_duplicate_rip("new-job", "xyz")

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "new-job")
    assert meta.issues is None


@pytest.mark.asyncio
async def test_detect_duplicate_rip_ignores_non_complete_prior(
    patch_pipeline_globals, async_session_maker,
):
    """A prior job with the same TOC but in error/review state isn't a
    duplicate-rip target — the user hasn't actually filed those tracks yet,
    so there's nothing to silently overwrite."""
    async with async_session_maker() as s:
        s.add(Job(id="old-error", toc_hash="dup", status="error"))
        s.add(Job(id="old-review", toc_hash="dup", status="review"))
        s.add(Job(id="new-job", toc_hash="dup", status="ripping"))
        s.add(JobMetadata(job_id="new-job"))
        await s.commit()

    await pipeline._detect_duplicate_rip("new-job", "dup")

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "new-job")
    assert meta.issues is None


@pytest.mark.asyncio
async def test_detect_duplicate_rip_blank_toc_hash_skips(
    patch_pipeline_globals, async_session_maker,
):
    """Older jobs may not have a toc_hash. We must not match every nullhash to
    every prior null-hash job and flag everything as a duplicate."""
    async with async_session_maker() as s:
        s.add(Job(id="old", toc_hash=None, status="complete"))
        s.add(Job(id="new-job", toc_hash=None, status="ripping"))
        s.add(JobMetadata(job_id="new-job"))
        await s.commit()

    await pipeline._detect_duplicate_rip("new-job", None)

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "new-job")
    assert meta.issues is None


@pytest.mark.asyncio
async def test_wake_skips_siblings_without_waiting_for_group(
    patch_pipeline_globals, async_session_maker,
):
    """Sibling whose review state was caused by something else (e.g. low
    confidence) must NOT be silently re-approved by the wake-up step."""
    async with async_session_maker() as s:
        s.add(Job(id="disc-a", album_group="grp-3", status="review"))
        s.add(JobMetadata(
            job_id="disc-a",
            confidence=40,
            needs_review=True,
            issues=json.dumps(["other_issue"]),
        ))
        s.add(Job(id="disc-b", album_group="grp-3", status="encoding"))
        s.add(JobMetadata(job_id="disc-b", confidence=95))
        await s.commit()

    await pipeline._check_approval("disc-b")

    # disc-b proceeds, disc-a stays put untouched.
    assert "disc-b" in patch_pipeline_globals.finalize_calls
    assert "disc-a" not in patch_pipeline_globals.finalize_calls

    async with async_session_maker() as s:
        a = await s.get(Job, "disc-a")
        a_meta = await s.get(JobMetadata, "disc-a")
    assert a.status == "review"
    assert json.loads(a_meta.issues) == ["other_issue"]
