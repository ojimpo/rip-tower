"""Tests for the metadata resolver's two-phase orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.metadata import resolver
from backend.models import Job, JobMetadata, MetadataCandidate


@pytest.mark.asyncio
async def test_enrich_hints_extracts_artist_and_album_from_phase1(
    monkeypatch, async_session_maker
):
    """After Phase 1, _enrich_hints should pull artist/album from the top
    disc-ID candidate so Phase 2 text-search sources have something to query."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-1", drive_id="d", disc_id="disc-1"))
        s.add(MetadataCandidate(
            job_id="job-1",
            source="cddb",
            artist="Mr.Children",
            album="Mr.Children 2011 – 2015",
            confidence=60,
        ))
        s.add(MetadataCandidate(
            job_id="job-1",
            source="kashidashi",
            artist=None,
            album="Mr.Children 2011 - 2015",
            confidence=80,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints("job-1", hints=None)
    assert enriched["artist"] == "Mr.Children"
    # Higher-confidence kashidashi album wins
    assert "Mr.Children 2011" in enriched["title"]


@pytest.mark.asyncio
async def test_enrich_hints_preserves_caller_hints(monkeypatch, async_session_maker):
    """Original hints (e.g. catalog from filename) take precedence."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-2", drive_id="d", disc_id="disc-2"))
        s.add(MetadataCandidate(
            job_id="job-2",
            source="cddb",
            artist="Cddb Artist",
            album="Cddb Album",
            confidence=60,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints(
        "job-2",
        hints={"artist": "User Provided", "catalog": "ABC-123"},
    )
    assert enriched["artist"] == "User Provided"  # caller hint wins
    assert enriched["catalog"] == "ABC-123"
    assert enriched["title"] == "Cddb Album"  # filled from candidate


@pytest.mark.asyncio
async def test_enrich_hints_strips_disc_suffix_and_extracts_disc_number(
    monkeypatch, async_session_maker,
):
    """Album names with disc suffixes like '[Disc 2]' should be cleaned for
    text search; disc_number gets exposed for downstream sources."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-3", drive_id="d", disc_id="disc-3"))
        s.add(MetadataCandidate(
            job_id="job-3",
            source="cddb",
            artist="Some Artist",
            album="Some Album [Disc 2]",
            confidence=60,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints("job-3", hints=None)
    assert enriched["title"] == "Some Album"
    assert enriched["disc_number"] == 2


@pytest.mark.asyncio
async def test_enrich_hints_no_candidates_returns_input(monkeypatch, async_session_maker):
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    enriched = await resolver._enrich_hints("nonexistent-job", hints={"foo": "bar"})
    assert enriched == {"foo": "bar"}


async def _noop_broadcast(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_auto_match_new_group_resolves_disc_number_collision(
    monkeypatch, async_session_maker,
):
    """Two siblings with the same album both tagged disc_number=1 (because MB
    can't tell which medium they actually are) should not both end up labeled
    disc 1 — the older job keeps the slot and the newer one is bumped to 2."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    older = datetime.now(timezone.utc) - timedelta(minutes=30)
    newer = older + timedelta(seconds=30)

    async with async_session_maker() as s:
        s.add(Job(id="disc-a", drive_id="d1", disc_id="aaa", created_at=older))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        s.add(Job(id="disc-b", drive_id="d2", disc_id="bbb", created_at=newer))
        s.add(JobMetadata(
            job_id="disc-b",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-b")

    async with async_session_maker() as s:
        a = await s.get(JobMetadata, "disc-a")
        b = await s.get(JobMetadata, "disc-b")
        ja = await s.get(Job, "disc-a")
        jb = await s.get(Job, "disc-b")
    assert ja.album_group is not None and ja.album_group == jb.album_group
    assert {a.disc_number, b.disc_number} == {1, 2}
    # Older job wins disc 1
    assert a.disc_number == 1
    assert b.disc_number == 2


@pytest.mark.asyncio
async def test_auto_match_join_existing_group_bumps_collision(
    monkeypatch, async_session_maker,
):
    """A disc joining an existing group while still tagged disc_number=1 must
    be reassigned if disc 1 is already taken in that group."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with async_session_maker() as s:
        # Existing group already has disc 1 claimed
        s.add(Job(
            id="disc-a", drive_id="d1", disc_id="aaa",
            created_at=t0, album_group="grp-1",
        ))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="嵐",
            album="ARASHI 5×10",
            album_base="ARASHI 5×10",
            disc_number=1,
            total_discs=3,
            confidence=70,
            source="musicbrainz",
        ))
        # Newcomer also tagged disc 1
        s.add(Job(
            id="disc-c", drive_id="d3", disc_id="ccc",
            created_at=t0 + timedelta(seconds=60),
        ))
        s.add(JobMetadata(
            job_id="disc-c",
            artist="嵐",
            album="ARASHI 5×10",
            album_base="ARASHI 5×10",
            disc_number=1,
            total_discs=3,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-c")

    async with async_session_maker() as s:
        jc = await s.get(Job, "disc-c")
        mc = await s.get(JobMetadata, "disc-c")
        ma = await s.get(JobMetadata, "disc-a")
    assert jc.album_group == "grp-1"
    assert ma.disc_number == 1  # incumbent unchanged
    assert mc.disc_number == 2  # newcomer bumped


@pytest.mark.asyncio
async def test_auto_match_preserves_distinct_disc_numbers(
    monkeypatch, async_session_maker,
):
    """When sources correctly identify distinct disc numbers, leave them alone."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with async_session_maker() as s:
        s.add(Job(id="disc-a", drive_id="d1", disc_id="aaa", created_at=t0))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="A",
            album="B",
            album_base="B",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        s.add(Job(id="disc-b", drive_id="d2", disc_id="bbb",
                  created_at=t0 + timedelta(seconds=30)))
        s.add(JobMetadata(
            job_id="disc-b",
            artist="A",
            album="B",
            album_base="B",
            disc_number=2,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-b")

    async with async_session_maker() as s:
        a = await s.get(JobMetadata, "disc-a")
        b = await s.get(JobMetadata, "disc-b")
    assert a.disc_number == 1
    assert b.disc_number == 2
