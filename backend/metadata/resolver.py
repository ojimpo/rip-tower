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

    # Auto-match multi-disc albums that were ripped without album_group
    if best:
        await _auto_match_album_group(job_id)

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
        existing = await session.get(JobMetadata, job_id)
        if existing:
            existing.artist = force.get("artist") or existing.artist
            existing.album = force.get("album") or existing.album
            existing.confidence = 100
            existing.source = "forced"
            existing.approved = True
        else:
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


async def _auto_match_album_group(job_id: str) -> None:
    """Auto-detect and link multi-disc albums that were ripped without album_group.

    Triggered after metadata resolution. Two matching strategies:

    1. This job has total_discs > 1 (from source or album name like "[Disc 1]"):
       Search for other recent ungrouped jobs with the same artist + album_base.
    2. This job has total_discs == 1 but a sibling with total_discs > 1 already
       matched us by artist + album_base — we get pulled in by strategy 1 of
       the sibling's resolution.

    This means the disc whose album name contains "[Disc N]" acts as the anchor,
    and the other disc (even without a disc suffix) gets matched by artist + album_base.
    """
    from datetime import datetime, timedelta, timezone

    from backend.models import Job, JobMetadata
    from backend.metadata.normalize import norm

    async with async_session() as session:
        job = await session.get(Job, job_id)
        if not job or job.album_group:
            return  # Already grouped

        meta = await session.get(JobMetadata, job_id)
        if not meta:
            return

        our_album_base = meta.album_base or meta.album
        our_artist = meta.artist
        if not our_album_base or not our_artist:
            return

        our_artist_norm = norm(our_artist)
        our_album_norm = norm(our_album_base)

        # Find other recent jobs with matching artist + album_base.
        # Include both ungrouped AND already-grouped jobs so that the 3rd disc
        # of a 3-disc set can join an existing group created by the first two.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await session.execute(
            select(Job, JobMetadata)
            .join(JobMetadata, Job.id == JobMetadata.job_id)
            .where(
                Job.id != job_id,
                Job.created_at >= cutoff,
            )
        )
        candidates = result.all()

        if not candidates:
            return

        # Match by normalized artist + album_base
        matched_ungrouped: list[tuple[Job, JobMetadata]] = []
        existing_group_id: str | None = None
        for cand_job, cand_meta in candidates:
            cand_album_base = cand_meta.album_base or cand_meta.album
            cand_artist = cand_meta.artist
            if not cand_album_base or not cand_artist:
                continue
            if norm(cand_artist) == our_artist_norm and norm(cand_album_base) == our_album_norm:
                if cand_job.album_group:
                    existing_group_id = cand_job.album_group
                else:
                    matched_ungrouped.append((cand_job, cand_meta))

        # If an existing group was found, join it
        if existing_group_id:
            job.album_group = existing_group_id
            # Count total members to update total_discs
            group_result = await session.execute(
                select(Job).where(Job.album_group == existing_group_id)
            )
            group_size = len(group_result.scalars().all()) + 1  # +1 for this job
            # Update total_discs for all members
            group_metas = await session.execute(
                select(JobMetadata)
                .join(Job, Job.id == JobMetadata.job_id)
                .where(Job.album_group == existing_group_id)
            )
            used_numbers = set()
            for gm in group_metas.scalars():
                gm.total_discs = group_size
                if gm.disc_number and gm.disc_number > 0:
                    used_numbers.add(gm.disc_number)
            meta.total_discs = group_size
            # Assign disc_number if not set
            if not meta.disc_number or meta.disc_number < 1:
                next_num = 1
                while next_num in used_numbers:
                    next_num += 1
                meta.disc_number = next_num
            await session.commit()
            logger.info(
                "Joined existing album group %s as disc %d/%d for job %s",
                existing_group_id, meta.disc_number, group_size, job_id,
            )
            await broadcast("job:group", {
                "album_group": existing_group_id,
                "job_ids": [job_id],
            })
            return

        # No existing group — try to create a new one from ungrouped matches
        all_matched: list[tuple[Job, JobMetadata]] = [(job, meta)] + matched_ungrouped
        if len(all_matched) < 2:
            return

        # At least one job must indicate multi-disc
        has_multi_disc_signal = any(
            (m.total_discs and m.total_discs > 1) or (m.disc_number and m.disc_number > 1)
            for _, m in all_matched
        )
        if not has_multi_disc_signal:
            return  # Could be separate single-disc albums by same artist

        import uuid

        group_id = str(uuid.uuid4())

        # Sort by disc_number if available, else by creation time
        all_matched.sort(
            key=lambda jm: (jm[1].disc_number or 999, jm[0].created_at)
        )

        # Assign disc numbers sequentially for jobs that don't have one
        used_numbers = {m.disc_number for _, m in all_matched if m.disc_number and m.disc_number > 0}
        next_num = 1
        for matched_job, matched_meta in all_matched:
            matched_job.album_group = group_id
            matched_meta.total_discs = len(all_matched)
            if not matched_meta.disc_number or matched_meta.disc_number < 1:
                while next_num in used_numbers:
                    next_num += 1
                matched_meta.disc_number = next_num
                used_numbers.add(next_num)
                next_num += 1

        await session.commit()

        job_ids = [j.id for j, _ in all_matched]
        logger.info(
            "Auto-matched album group %s for %d discs: %s (artist=%s, album=%s)",
            group_id, len(all_matched), job_ids, our_artist, our_album_base,
        )

        await broadcast("job:group", {
            "album_group": group_id,
            "job_ids": job_ids,
        })
