"""Metadata resolution — query all sources in parallel and rank results.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py.
"""

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select

from backend.database import async_session
from backend.metadata.evidence import parse_evidence
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

    # Two-phase resolution:
    #   Phase 1 — disc-ID-based sources (work without text hints)
    #   Phase 2 — text-search-based sources (need artist/album hints to be useful)
    # Phase 1 results enrich the hints for Phase 2, so e.g. CDDB's artist+album
    # can drive iTunes/Discogs/HMV searches that would otherwise return nothing.
    phase1_sources = [
        MusicBrainzSource(mode="disc_id"),
        CddbSource(),
        KashidashiSource(),
    ]
    phase2_sources = [
        MusicBrainzSource(mode="text_search"),
        DiscogsSource(),
        HmvSource(),
        ItunesSource(),
    ]

    # If force metadata is provided, skip resolution
    if force:
        await _apply_forced(job_id, force)
        return

    # Clear any candidates from a prior resolve so re-resolves don't accumulate
    # stale rows (which would skew sanitizer ranking and contradiction checks).
    async with async_session() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
        )
        await session.commit()

    # ---- Phase 1: disc-ID-based sources ----
    # _query_source never raises (it logs failures per source), so a broken
    # source can't take down its siblings.
    await asyncio.gather(*(
        _query_source(source, job_id, identity, hints)
        for source in phase1_sources
    ))

    # Build enriched hints from Phase 1 candidates
    enriched_hints = await _enrich_hints(job_id, hints)

    # ---- Phase 2: text-search-based sources, using enriched hints ----
    await asyncio.gather(*(
        _query_source(source, job_id, identity, enriched_hints)
        for source in phase2_sources
    ))

    # Reject fuzzy TOC matches whose per-track durations disagree with the
    # physical disc. MB's /discid/-?toc= matches loosely and can return an
    # unrelated release that merely shares a track count at confidence 90,
    # overwriting the correct kashidashi/CDDB answer (Todoist 6grvQh7mwWmPgX9F).
    # Run before the kashidashi boost so the demoted collision can't anchor.
    await _penalize_toc_length_mismatch(job_id, identity)

    # Cross-reference candidates against currently-borrowed kashidashi items.
    # When MB's disc-ID lookup returns multiple releases sharing one TOC (or a
    # different user's mis-submission for an unrelated disc-ID), a candidate
    # that matches a CD the user is physically holding is far stronger evidence
    # than confidence alone. Run before sanitize so the boosted confidence
    # flows into ranking.
    await _boost_kashidashi_matches(job_id)

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


async def _enrich_hints(job_id: str, hints: dict | None) -> dict:
    """Build text-search hints from Phase 1 candidates.

    Picks the highest-confidence Phase 1 candidate's artist/album as the
    text-search hints for Phase 2 sources (iTunes/HMV/Discogs). Original
    hints (e.g. catalog number from filename) take precedence.

    Phase 1 sources (MB-discID, CDDB, kashidashi) get authoritative artist/album
    from the disc — this lets us feed Phase 2 sources useful search terms even
    when the user didn't provide any.
    """
    enriched: dict = dict(hints or {})

    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == job_id)
            .order_by(MetadataCandidate.confidence.desc())
        )
        candidates = list(result.scalars().all())

    if not candidates:
        return enriched

    # Take artist/album from highest-confidence candidate that has them
    for c in candidates:
        if not enriched.get("artist") and c.artist:
            enriched["artist"] = c.artist
        if not enriched.get("title") and c.album:
            # Strip common disc-suffix patterns so text search isn't poisoned
            # by "Album [Disc 2]" or "Album / 30th giving 1" garbage.
            from backend.metadata.normalize import extract_disc_info
            base, disc_num = extract_disc_info(c.album)
            enriched["title"] = base
            if disc_num and not enriched.get("disc_number"):
                enriched["disc_number"] = disc_num
        if enriched.get("artist") and enriched.get("title"):
            break

    return enriched


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


# Confidence boost applied when a candidate matches a borrowed kashidashi item
# on both artist and album. +25 is large enough to flip same-confidence ties
# (e.g. three MB releases at 90 sharing one disc-ID) toward the borrowed disc,
# but small enough that a genuinely high-confidence non-library candidate can
# still win against a low-conf library guess that happens to match.
_KASHIDASHI_BOOST = 25
_KASHIDASHI_SIM_THRESHOLD = 0.6

# Confidence a disc-anchored candidate is knocked down to when it conflicts with
# a borrowed CD. A CDDB/TOC disc-ID collision lets MusicBrainz's disc lookup
# "confirm" a release the user isn't holding (JUJU's disc mis-confirmed as a
# Various-Artists comp at confidence ~90 — Todoist 6gp5wg5vFMRmvVmF). When a
# borrowed CD is surfaced for this disc but a disc-anchored candidate matches
# *none* of the borrowed pool, drop it below the auto-approve line (50) and
# below a typical recency-fallback borrowed candidate (50–80) so review leads
# with the CD the user physically holds rather than the colliding release.
_KASHIDASHI_CONFLICT_FLOOR = 40


# Confidence a TOC-matched candidate is knocked down to when its per-track
# durations disagree with the physical disc. Below auto-approve (50), the
# kashidashi-conflict floor (40), and a typical recency-fallback borrowed
# candidate so the correct disc leads review instead of the collision.
_TOC_LENGTH_FLOOR = 30
# A genuine same-pressing TOC match has near-zero per-track drift; MB recording
# lengths vs physical track lengths differ by at most a second or two. Tolerate
# small drift but reject the gross divergence a different release produces.
_TOC_LENGTH_TOTAL_TOLERANCE = 60   # summed abs per-track deviation (seconds)
_TOC_LENGTH_TRACK_TOLERANCE = 30   # worst single-track deviation (seconds)


def _disc_track_seconds(identity: Any) -> list[int]:
    """Per-track durations (whole seconds) derived from the physical disc TOC.

    offsets are LBA sectors (75/sec); leadout is whole seconds. Track i spans
    offsets[i]..offsets[i+1], the last track runs to the leadout.
    """
    offsets = list(getattr(identity, "offsets", None) or [])
    leadout_seconds = getattr(identity, "leadout", 0) or 0
    if not offsets or not leadout_seconds:
        return []
    bounds = offsets + [leadout_seconds * 75]
    return [
        max(0, (bounds[i + 1] - bounds[i]) // 75) for i in range(len(offsets))
    ]


async def _penalize_toc_length_mismatch(job_id: str, identity: Any) -> None:
    """Demote disc-anchored candidates whose track durations don't fit the disc.

    Only TOC-submission candidates carry `track_lengths` evidence (MusicBrainz
    disc-ID lookup). When MB returns a release that shares the disc's track
    count but is a different recording, the per-track durations diverge sharply;
    comparing them against the physical disc rejects the collision before it can
    win on confidence alone.
    """
    disc_secs = _disc_track_seconds(identity)
    if not disc_secs:
        return

    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
        )
        candidates = list(result.scalars().all())

        changed = False
        for c in candidates:
            evidence = parse_evidence(c)
            cand_secs = evidence.get("track_lengths")
            # Need a full, non-placeholder length array of the same shape as the
            # disc. MB occasionally omits lengths (all zeros) — can't judge those.
            if (
                not isinstance(cand_secs, list)
                or len(cand_secs) != len(disc_secs)
                or not any(cand_secs)
            ):
                continue

            diffs = [abs(int(a) - b) for a, b in zip(cand_secs, disc_secs)]
            total_dev = sum(diffs)
            max_dev = max(diffs)
            if (
                total_dev <= _TOC_LENGTH_TOTAL_TOLERANCE
                and max_dev <= _TOC_LENGTH_TRACK_TOLERANCE
            ):
                continue

            old_conf = c.confidence or 0
            if old_conf <= _TOC_LENGTH_FLOOR:
                continue
            c.confidence = _TOC_LENGTH_FLOOR
            evidence["toc_length_mismatch"] = {
                "previous_confidence": old_conf,
                "total_deviation": total_dev,
                "max_deviation": max_dev,
                "floor": _TOC_LENGTH_FLOOR,
            }
            c.evidence = json.dumps(evidence, ensure_ascii=False)
            changed = True
            logger.info(
                "Penalized TOC candidate id=%s (%s/%s) %d→%d: per-track "
                "durations off by %ds total / %ds worst from physical disc",
                c.id, c.source, c.album, old_conf, _TOC_LENGTH_FLOOR,
                total_dev, max_dev,
            )

        if changed:
            await session.commit()


async def _boost_kashidashi_matches(job_id: str) -> None:
    """Boost candidates whose artist+album matches a currently-borrowed CD.

    Records `kashidashi_confirmed` evidence on every matched candidate so
    sanitizer can flag a `kashidashi_mismatch` issue when the ranked-best
    candidate disagrees with the user's physical disc.

    Excludes kashidashi-sourced candidates from the boost — they are already
    library-evidenced by construction, double-counting would let an empty-text
    recency-fallback row overtake a clearly-named MB candidate that also
    matches.
    """
    from sqlalchemy import func

    from backend.metadata.sanitizer import candidate_expected_track_count
    from backend.metadata.sources.kashidashi import (
        _is_disc_anchored,
        best_kashidashi_match,
        fetch_active_borrowed_items,
    )
    from backend.models import Track

    items = await fetch_active_borrowed_items()
    if not items:
        return

    async with async_session() as session:
        # The physical disc's actual track count is a hard cross-check: a
        # candidate whose release has a different track count cannot be this
        # disc, so it must not receive a borrowed-CD confidence boost (which
        # would otherwise push a recency-fallback text match over the
        # auto-approve line — the o8maxpvg "18-track disc → 11-track LOVE" bug).
        disc_track_count = (
            await session.execute(
                select(func.count(Track.id)).where(Track.job_id == job_id)
            )
        ).scalar() or 0

        result = await session.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
        )
        candidates = list(result.scalars().all())

        boosted_ids: set[int] = set()
        for c in candidates:
            if c.source == "kashidashi":
                continue
            artist = c.artist or ""
            album = c.album or ""
            if not artist or not album:
                continue
            expected = candidate_expected_track_count(c)
            if disc_track_count and expected and expected != disc_track_count:
                continue
            # Script-insensitive match: falls back to MusicBrainz artist/release
            # aliases so a Japanese borrowed record ("エイミー・ワインハウス")
            # still matches an English MB candidate ("Amy Winehouse").
            item, art_sim, alb_sim = await best_kashidashi_match(c, items)
            if item is None:
                continue
            if art_sim < _KASHIDASHI_SIM_THRESHOLD or alb_sim < _KASHIDASHI_SIM_THRESHOLD:
                continue
            boosted_ids.add(c.id)
            old_conf = c.confidence or 0
            c.confidence = min(old_conf + _KASHIDASHI_BOOST, 100)
            evidence = parse_evidence(c)
            evidence["kashidashi_confirmed"] = {
                "item_id": item.get("id"),
                "artist_sim": round(art_sim, 2),
                "album_sim": round(alb_sim, 2),
                "boost": _KASHIDASHI_BOOST,
            }
            c.evidence = json.dumps(evidence, ensure_ascii=False)
            logger.info(
                "Boosted candidate id=%s (%s/%s) by +%d for kashidashi item %s",
                c.id, c.source, c.artist, _KASHIDASHI_BOOST, item.get("id"),
            )

        # Penalize disc-anchored candidates that conflict with a borrowed CD.
        # Only engages when a borrowed CD was actually surfaced for this disc
        # (a kashidashi-source candidate exists): a disc-anchored candidate that
        # matched *no* borrowed item is then likely a TOC collision, so it must
        # not present itself as the high-confidence confirmed answer over the CD
        # the user is holding.
        borrowed_surfaced = any(c.source == "kashidashi" for c in candidates)
        if borrowed_surfaced:
            for c in candidates:
                if c.source == "kashidashi" or c.id in boosted_ids:
                    continue
                if not _is_disc_anchored(c):
                    continue
                old_conf = c.confidence or 0
                if old_conf <= _KASHIDASHI_CONFLICT_FLOOR:
                    continue
                c.confidence = _KASHIDASHI_CONFLICT_FLOOR
                evidence = parse_evidence(c)
                evidence["kashidashi_conflict"] = {
                    "previous_confidence": old_conf,
                    "floor": _KASHIDASHI_CONFLICT_FLOOR,
                }
                c.evidence = json.dumps(evidence, ensure_ascii=False)
                logger.info(
                    "Penalized disc-anchored candidate id=%s (%s/%s) %d→%d: "
                    "conflicts with borrowed CD",
                    c.id, c.source, c.artist, old_conf, _KASHIDASHI_CONFLICT_FLOOR,
                )

        await session.commit()


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


def _next_free_slot(used: set[int]) -> int:
    """Lowest disc number not already claimed."""
    n = 1
    while n in used:
        n += 1
    return n


def _add_issue(meta: Any, issue: str) -> None:
    """Append an issue tag to a JobMetadata row, keeping the list unique."""
    try:
        issues = json.loads(meta.issues) if meta.issues else []
    except (ValueError, TypeError):
        issues = []
    if not isinstance(issues, list):
        issues = []
    if issue not in issues:
        issues.append(issue)
    meta.issues = json.dumps(issues, ensure_ascii=False)
    meta.needs_review = True


async def _disc_evidence(session: Any, job_ids: list[str]) -> dict[str, dict]:
    """Per-job disc position/count as claimed by each job's selected candidate.

    Grouping must never invent a disc number for a disc whose TOC match already
    said which medium it is. A candidate's `evidence` is written once at
    resolution time and is never touched by grouping, so it stays trustworthy
    even after JobMetadata has been rewritten.

    Returns {job_id: {"disc_number": int | None, "total_discs": int | None}}.
    """
    from backend.metadata.normalize import extract_disc_info

    if not job_ids:
        return {}

    result = await session.execute(
        select(MetadataCandidate)
        .where(
            MetadataCandidate.job_id.in_(job_ids),
            MetadataCandidate.selected.is_(True),
        )
        .order_by(MetadataCandidate.confidence.desc())
    )

    out: dict[str, dict] = {}
    for cand in result.scalars():
        if cand.job_id in out:
            continue  # keep the highest-confidence selected candidate only
        ev = parse_evidence(cand)
        disc = ev.get("disc_number")
        total = ev.get("total_discs")
        # A source that doesn't expose a medium position may still spell the
        # disc out in the release title ("... (Disc 2：路上から ...)").
        _, title_disc = extract_disc_info(cand.album or "")
        out[cand.job_id] = {
            "disc_number": disc if isinstance(disc, int) and disc > 0 else title_disc,
            "total_discs": total if isinstance(total, int) and total > 1 else None,
        }
    return out


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
            # Query the siblings BEFORE assigning job.album_group, and exclude
            # this job explicitly. The session autoflushes pending changes
            # before a SELECT, so assigning the group first made this job show
            # up in its own sibling query: the count was one too high and the
            # job collided with its own disc_number and got bumped to a free
            # slot. That is what turned a correct "disc 2 of 3" into "disc 4
            # of 4" for a whole 3-disc set.
            sibling_jobs = (await session.execute(
                select(Job).where(
                    Job.album_group == existing_group_id,
                    Job.id != job_id,
                )
            )).scalars().all()
            sibling_metas = (await session.execute(
                select(JobMetadata)
                .join(Job, Job.id == JobMetadata.job_id)
                .where(
                    Job.album_group == existing_group_id,
                    Job.id != job_id,
                )
            )).scalars().all()
            group_size = len(sibling_jobs) + 1

            evidence = await _disc_evidence(
                session, [j.id for j in sibling_jobs] + [job_id]
            )

            # total_discs is a property of the album, not a tally of what we
            # happened to rip in the last two hours. Prefer the strongest
            # source-derived count (e.g. MusicBrainz's CD medium count) and
            # fall back to the group size only when no source supplied one.
            source_total = max(
                (e["total_discs"] for e in evidence.values() if e["total_discs"]),
                default=None,
            )
            # Without candidate evidence, fall back to the largest count anyone
            # already carries (sanitizer may have written a source value there)
            # before resorting to the group size.
            declared_total = max(
                [sm.total_discs or 0 for sm in sibling_metas]
                + [meta.total_discs or 0, group_size]
            )
            total_discs = source_total or declared_total
            if source_total and group_size > source_total:
                logger.warning(
                    "Album group %s has %d members but sources say %d discs — "
                    "a mis-identified disc may have joined the group",
                    existing_group_id, group_size, source_total,
                )

            # Slots the siblings hold, and which of those claims a source
            # actually backs. An unbacked claim is only a guess and may be
            # moved; a backed one may not.
            used_numbers: set[int] = set()
            backed_numbers: set[int] = set()
            for sm in sibling_metas:
                sm.total_discs = total_discs
                if sm.disc_number and sm.disc_number > 0:
                    used_numbers.add(sm.disc_number)
                    if evidence.get(sm.job_id, {}).get("disc_number") == sm.disc_number:
                        backed_numbers.add(sm.disc_number)

            meta.total_discs = total_discs

            our_disc = evidence.get(job_id, {}).get("disc_number")
            if our_disc:
                meta.disc_number = our_disc

            if not meta.disc_number or meta.disc_number < 1:
                meta.disc_number = _next_free_slot(used_numbers)
            elif meta.disc_number in used_numbers:
                if our_disc and meta.disc_number not in backed_numbers:
                    # We know which disc we are; the sibling sitting on this
                    # slot only guessed. Move the guess, not the evidence.
                    for sm in sibling_metas:
                        if sm.disc_number == meta.disc_number:
                            used_numbers.discard(sm.disc_number)
                            sm.disc_number = _next_free_slot(
                                used_numbers | {meta.disc_number}
                            )
                            used_numbers.add(sm.disc_number)
                            break
                elif our_disc:
                    # Both sides are source-backed. Renumbering either one
                    # would be a guess, so keep both and let review decide.
                    _add_issue(meta, "disc_number_conflict")
                    logger.warning(
                        "Job %s and a sibling both claim disc %d of group %s "
                        "with source backing",
                        job_id, meta.disc_number, existing_group_id,
                    )
                else:
                    meta.disc_number = _next_free_slot(used_numbers)

            job.album_group = existing_group_id
            await session.commit()
            logger.info(
                "Joined existing album group %s as disc %d/%d for job %s",
                existing_group_id, meta.disc_number, total_discs, job_id,
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

        evidence = await _disc_evidence(session, [j.id for j, _ in all_matched])

        # As in the join path: the album's disc count comes from the sources,
        # not from how many discs happen to be sitting in the drives.
        source_total = max(
            (e["total_discs"] for e in evidence.values() if e["total_discs"]),
            default=None,
        )
        total_discs = source_total or max(
            [m.total_discs or 0 for _, m in all_matched] + [len(all_matched)]
        )

        # Source-backed discs claim their slot first; guesses fill in around
        # them. Ordering purely by arrival let a guess squat on the slot a
        # TOC-confirmed disc turned out to need.
        all_matched.sort(
            key=lambda jm: (
                0 if evidence.get(jm[0].id, {}).get("disc_number") else 1,
                jm[1].disc_number or 999,
                jm[0].created_at,
            )
        )

        claimed: set[int] = set()
        for matched_job, matched_meta in all_matched:
            matched_job.album_group = group_id
            matched_meta.total_discs = total_discs
            cur = evidence.get(matched_job.id, {}).get("disc_number") or matched_meta.disc_number
            if not cur or cur < 1 or cur in claimed:
                cur = _next_free_slot(claimed)
            matched_meta.disc_number = cur
            claimed.add(cur)

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
