"""Tests for the metadata resolver's two-phase orchestration."""

from __future__ import annotations

import pytest

from backend.metadata import resolver
from backend.models import Job, MetadataCandidate


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
