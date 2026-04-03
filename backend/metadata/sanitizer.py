"""Metadata sanitization — clean, rank, and select best candidate.

Reads all MetadataCandidate records for a job, applies deterministic fixes
(fullwidth->halfwidth, disc suffix removal, compilation detection, mojibake
detection), ranks by confidence, selects the best, and creates a JobMetadata record.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_sanitizer.py.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from sqlalchemy import select

from backend.database import async_session
from backend.metadata.normalize import (
    extract_disc_info,
    fullwidth_to_halfwidth,
    normalize_various_artists,
)
from backend.models import JobMetadata, MetadataCandidate, Track

logger = logging.getLogger(__name__)


async def sanitize_candidates(job_id: str) -> JobMetadata | None:
    """Read all candidates for a job, sanitize, rank, select best, and save JobMetadata.

    Returns the created JobMetadata record or None if no candidates exist.
    """
    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == job_id)
            .order_by(MetadataCandidate.confidence.desc())
        )
        candidates = list(result.scalars().all())

    if not candidates:
        logger.info("No metadata candidates for job %s", job_id)
        return None

    # Sanitize each candidate in place
    for c in candidates:
        c.artist = _sanitize_text(c.artist or "")
        c.album = _sanitize_text(c.album or "")
        if c.track_titles:
            try:
                titles = json.loads(c.track_titles)
                titles = [_sanitize_text(t) for t in titles]
                c.track_titles = json.dumps(titles, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass

    # Detect issues across all candidates
    issues: list[str] = []

    best = candidates[0]  # Already sorted by confidence desc

    artist = best.artist or ""
    album = best.album or ""

    # Various Artists normalization
    artist = normalize_various_artists(artist)

    # Disc info extraction
    album_base, disc_number = extract_disc_info(album)

    # Compilation detection from track titles
    is_compilation = False
    track_titles: list[str] = []
    track_artists: list[str] = []

    if best.track_titles:
        try:
            raw_titles = json.loads(best.track_titles)
        except (json.JSONDecodeError, TypeError):
            raw_titles = []

        compilation_count = 0
        for t in raw_titles:
            if " / " in t:
                parts = t.split(" / ", 1)
                track_artists.append(fullwidth_to_halfwidth(parts[0].strip()))
                track_titles.append(fullwidth_to_halfwidth(parts[1].strip()))
                compilation_count += 1
            else:
                track_artists.append(artist)
                track_titles.append(t)

        if raw_titles and compilation_count > len(raw_titles) / 2:
            is_compilation = True
            artist = normalize_various_artists(artist) if artist else "Various Artists"

    # Merge track titles from lower-ranked candidates if best has none
    if not track_titles:
        for c in candidates[1:]:
            if c.track_titles:
                try:
                    fallback = json.loads(c.track_titles)
                    if fallback:
                        track_titles = fallback
                        break
                except (json.JSONDecodeError, TypeError):
                    pass

    # Check for placeholder tracks
    if not track_titles:
        issues.append("no_track_titles")
    elif all(re.match(r"^Track\s*\d+$", t, re.IGNORECASE) for t in track_titles):
        issues.append("no_track_titles")

    # Mojibake detection
    for text in [artist, album] + track_titles:
        if _looks_like_mojibake(text):
            issues.append("mojibake")
            break

    # Katakana-only artist detection
    if artist and _is_katakana_only(artist):
        issues.append("artist_variant")

    # Contradiction detection — different candidates disagree on artist/album
    if len(candidates) >= 2:
        artists_seen = {c.artist for c in candidates[:3] if c.artist and c.confidence and c.confidence >= 50}
        albums_seen = {c.album for c in candidates[:3] if c.album and c.confidence and c.confidence >= 50}
        if len(artists_seen) > 1:
            issues.append("artist_contradiction")
        if len(albums_seen) > 1:
            issues.append("album_contradiction")

    # Determine review need
    confidence = best.confidence or 0
    needs_review = confidence < 50 or bool(issues)

    # Year: try to parse from best, fallback to others
    year = None
    if best.year:
        try:
            year = int(str(best.year)[:4])
        except (ValueError, TypeError):
            pass
    if not year:
        for c in candidates[1:]:
            if c.year:
                try:
                    year = int(str(c.year)[:4])
                    break
                except (ValueError, TypeError):
                    pass

    # Genre: from best or fallback
    genre = best.genre or ""
    if not genre:
        for c in candidates[1:]:
            if c.genre:
                genre = c.genre
                break

    # Mark best candidate as selected
    async with async_session() as session:
        # Reset all selected flags
        all_cands = await session.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == job_id)
        )
        for c in all_cands.scalars():
            c.selected = c.id == best.id
        await session.flush()

        # Create or update JobMetadata
        existing = await session.get(JobMetadata, job_id)
        if existing:
            existing.artist = artist
            existing.album = album
            existing.album_base = album_base
            existing.year = year
            existing.genre = genre
            existing.disc_number = disc_number or 1
            existing.is_compilation = is_compilation
            existing.confidence = confidence
            existing.source = best.source
            existing.source_url = best.source_url
            existing.needs_review = needs_review
            existing.issues = json.dumps(issues, ensure_ascii=False) if issues else None
            meta = existing
        else:
            meta = JobMetadata(
                job_id=job_id,
                artist=artist,
                album=album,
                album_base=album_base,
                year=year,
                genre=genre,
                disc_number=disc_number or 1,
                total_discs=1,
                is_compilation=is_compilation,
                confidence=confidence,
                source=best.source,
                source_url=best.source_url,
                needs_review=needs_review,
                issues=json.dumps(issues, ensure_ascii=False) if issues else None,
            )
            session.add(meta)

        # Update track titles and artists from the selected candidate
        if track_titles:
            tracks = await session.execute(
                select(Track)
                .where(Track.job_id == job_id)
                .order_by(Track.track_num)
            )
            for track in tracks.scalars():
                idx = track.track_num - 1
                if idx < len(track_titles):
                    track.title = track_titles[idx]
                if is_compilation and idx < len(track_artists):
                    track.artist = track_artists[idx]

        await session.commit()

    logger.info(
        "Sanitized metadata for job %s: artist=%r album=%r conf=%d issues=%s",
        job_id, artist, album, confidence, issues,
    )
    return meta


def _sanitize_text(text: str) -> str:
    """Apply deterministic text cleanups."""
    if not text:
        return text
    # Fullwidth -> halfwidth
    text = fullwidth_to_halfwidth(text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_like_mojibake(text: str) -> bool:
    """Detect common mojibake patterns.

    Shift_JIS -> UTF-8 misinterpretation produces sequences like:
    - U+FFFD (replacement character)
    - Control characters in text
    - High ratio of rare Latin Extended characters
    """
    if not text:
        return False
    # Replacement characters
    if "\ufffd" in text:
        return True
    # Control characters (except newline/tab)
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t\r" for c in text):
        return True
    # High ratio of rare Latin Extended characters (common in Shift_JIS misreads)
    rare = sum(
        1 for c in text
        if "\u00c0" <= c <= "\u00ff"
        and c not in "àáâãäåèéêëìíîïòóôõöùúûüñç"
    )
    if len(text) > 3 and rare / len(text) > 0.3:
        return True
    return False


def _is_katakana_only(text: str) -> bool:
    """Check if text is primarily katakana (potential English artist in kana)."""
    stripped = re.sub(r"[\s・\-=]+", "", text)
    if not stripped or len(stripped) < 2:
        return False
    katakana_count = sum(1 for c in stripped if "\u30a0" <= c <= "\u30ff")
    return katakana_count / len(stripped) > 0.8
