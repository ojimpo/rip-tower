"""Metadata resolution — query all sources in parallel and rank results.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py.
"""

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select

from backend.database import async_session
from backend.models import JobMetadata, MetadataCandidate
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)


async def resolve(
    job_id: str,
    identity: Any,
    hints: dict | None = None,
    force: dict | None = None,
) -> None:
    """Run metadata resolution pipeline.

    1. Query all sources in parallel
    2. Sanitize results
    3. Rank and select best candidate
    4. Optionally call LLM for assistance
    5. Fetch artwork and lyrics
    6. Match kashidashi
    """
    from backend.metadata.sources.musicbrainz import MusicBrainzSource
    from backend.metadata.sources.discogs import DiscogsSource
    from backend.metadata.sources.kashidashi import KashidashiSource
    from backend.metadata.sources.hmv import HmvSource
    from backend.metadata.sources.cddb import CddbSource
    from backend.metadata.sources.itunes import ItunesSource

    sources = [
        MusicBrainzSource(),
        DiscogsSource(),
        KashidashiSource(),
        HmvSource(),
        CddbSource(),
        ItunesSource(),
    ]

    # If force metadata is provided, skip resolution
    if force:
        await _apply_forced(job_id, force)
        return

    # Query all sources in parallel
    tasks = [
        asyncio.create_task(
            _query_source(source, job_id, identity, hints)
        )
        for source in sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Log errors
    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            logger.warning("Source %s failed: %s", source.name, result)

    # Sanitize and rank
    from backend.metadata.sanitizer import sanitize_candidates

    best = await sanitize_candidates(job_id)

    if best:
        # Check if LLM assist is needed
        from backend.metadata.llm_assist import maybe_assist

        assisted = await maybe_assist(job_id)

        # Re-sanitize to pick up LLM candidate if one was added
        if assisted:
            best = await sanitize_candidates(job_id)

    # Sync shared metadata from album group siblings if available
    if best:
        await _sync_from_group(job_id, best)

    # Fetch artwork and lyrics in parallel
    artwork_task = asyncio.create_task(_fetch_artwork(job_id))
    lyrics_task = asyncio.create_task(_fetch_lyrics(job_id))
    kashidashi_task = asyncio.create_task(_match_kashidashi(job_id, identity))

    await asyncio.gather(artwork_task, lyrics_task, kashidashi_task, return_exceptions=True)

    logger.info("Metadata resolution complete for job %s", job_id)


async def _query_source(source, job_id: str, identity, hints: dict | None) -> None:
    """Query a single metadata source and save results."""
    try:
        candidates = await source.search(identity, hints)
        async with async_session() as session:
            for c in candidates:
                candidate = MetadataCandidate(
                    job_id=job_id,
                    source=source.name,
                    source_url=c.get("source_url"),
                    artist=c.get("artist"),
                    album=c.get("album"),
                    year=c.get("year"),
                    genre=c.get("genre"),
                    track_titles=c.get("track_titles"),
                    confidence=c.get("confidence", 0),
                    evidence=c.get("evidence"),
                )
                session.add(candidate)
            await session.commit()
    except Exception:
        logger.exception("Source %s query failed", source.name)
        raise


async def _apply_forced(job_id: str, force: dict) -> None:
    """Apply forced metadata directly."""
    async with async_session() as session:
        meta = JobMetadata(
            job_id=job_id,
            artist=force.get("artist"),
            album=force.get("album"),
            confidence=100,
            source="forced",
            approved=True,
        )
        session.add(meta)
        await session.commit()


async def _fetch_artwork(job_id: str) -> None:
    """Fetch artwork from all sources."""
    from backend.metadata.artwork import fetch_artwork

    await fetch_artwork(job_id)


async def _fetch_lyrics(job_id: str) -> None:
    """Fetch lyrics for all tracks."""
    from backend.metadata.lyrics import fetch_lyrics

    await fetch_lyrics(job_id)


async def _match_kashidashi(job_id: str, identity) -> None:
    """Match against kashidashi candidates."""
    from backend.metadata.sources.kashidashi import match_kashidashi

    await match_kashidashi(job_id, identity)


async def _sync_from_group(job_id: str, meta: Any) -> None:
    """If this job belongs to an album group, adopt shared metadata from siblings.

    When disc 2 is resolved after disc 1, this inherits artist/album_base/year/genre
    from the already-resolved disc 1 — provided the sibling has higher confidence.
    """
    from backend.models import Job, JobMetadata

    async with async_session() as session:
        job = await session.get(Job, job_id)
        if not job or not job.album_group:
            return

        # Find sibling with highest confidence
        result = await session.execute(
            select(JobMetadata)
            .join(Job, Job.id == JobMetadata.job_id)
            .where(
                Job.album_group == job.album_group,
                Job.id != job_id,
            )
            .order_by(JobMetadata.confidence.desc())
        )
        sibling = result.scalars().first()
        if not sibling:
            return

        # Only sync if sibling has higher confidence
        if (sibling.confidence or 0) <= (meta.confidence or 0):
            return

        # Reload our meta in this session for update
        our_meta = await session.get(JobMetadata, job_id)
        if not our_meta:
            return

        shared_fields = ["artist", "album_base", "year", "genre", "is_compilation"]
        synced = []
        for field in shared_fields:
            sibling_val = getattr(sibling, field, None)
            if sibling_val is not None:
                setattr(our_meta, field, sibling_val)
                synced.append(field)

        # Also set total_discs from sibling
        if sibling.total_discs and sibling.total_discs > 1:
            our_meta.total_discs = sibling.total_discs

        await session.commit()

        if synced:
            logger.info(
                "Synced group metadata for job %s from sibling %s: %s",
                job_id, sibling.job_id, synced,
            )
