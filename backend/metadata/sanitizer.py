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

    # Disc info extraction — prefer source data (e.g. MusicBrainz medium position)
    album_base, disc_number = extract_disc_info(album)
    source_total_discs = None
    if best.evidence:
        try:
            ev = json.loads(best.evidence)
            if ev.get("disc_number"):
                disc_number = disc_number or ev["disc_number"]
            if ev.get("total_discs"):
                source_total_discs = ev["total_discs"]
        except (json.JSONDecodeError, TypeError):
            pass

    # If disc_number was extracted from album name but no total_discs from source,
    # infer total_discs >= disc_number (at least 2) so auto-grouping can kick in.
    # This is a guess — don't override user-set or existing total_discs with it.
    inferred_total_discs = None
    if disc_number and disc_number >= 1 and not source_total_discs:
        inferred_total_discs = max(disc_number, 2)

    # ---- Pick best track_titles independently of best album metadata ----
    # Album/artist may come from the highest-confidence candidate, but track titles
    # are selected separately by quality (track count match, no mojibake, no
    # placeholders, low annotation density, source preference). This lets us
    # combine e.g. CDDB album info with iTunes/MB clean track titles.
    expected_track_count = await _get_track_count(job_id)
    track_source = _pick_best_track_titles(candidates, expected_track_count)
    raw_titles: list[str] = track_source["titles"] if track_source else []

    # Compilation detection from track titles
    is_compilation = False
    track_titles: list[str] = []
    track_artists: list[str] = []

    if raw_titles:
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

    # Flag annotation-heavy track titles (CDDB tie-up notes etc.) so the user
    # is prompted to review even when confidence is otherwise high.
    if track_source and track_source.get("annotation_ratio", 0) >= 0.3:
        issues.append("annotated_track_titles")

    # Check for placeholder tracks
    clear_placeholder_titles = False
    if not track_titles:
        issues.append("no_track_titles")
    elif all(re.match(r"^Track\s*\d+$", t, re.IGNORECASE) for t in track_titles):
        issues.append("no_track_titles")
    elif all(t and re.match(r"^\d{1,4}$", t.strip()) for t in track_titles):
        # GnuDB unsubmitted-disc placeholder pattern: pure digits like "101","102",...
        # ("Track 1" handled above). Common multi-disc form is {disc}{track:02d}, so try
        # to recover disc_number from a consistent leading digit when source didn't supply one.
        issues.append("no_track_titles")
        if not disc_number:
            stripped = [t.strip() for t in track_titles]
            leads = {t[0] for t in stripped if len(t) >= 3}
            if len(leads) == 1 and stripped[0].startswith(next(iter(leads))):
                try:
                    inferred = int(next(iter(leads)))
                    if 1 <= inferred <= 9:
                        disc_number = inferred
                        if not source_total_discs and not inferred_total_discs:
                            inferred_total_discs = max(inferred, 2)
                except ValueError:
                    pass
        # Drop the placeholder titles so we don't surface them in the review UI;
        # LLM (if available) will repopulate via the second sanitize pass, otherwise
        # tracks stay blank. Explicitly mark for DB clear so existing rows get reset
        # too (re-resolve case where placeholders were previously written).
        track_titles = []
        track_artists = []
        clear_placeholder_titles = True

    # Mojibake detection
    for text in [artist, album] + track_titles:
        if _looks_like_mojibake(text):
            issues.append("mojibake")
            break

    # Katakana-only artist detection
    if artist and _is_katakana_only(artist):
        issues.append("artist_variant")

    # Parenthesized romanization/translation detection (e.g. "葉加瀬太郎 (Taro Hakase)")
    if _has_parenthesized_variant(artist) or _has_parenthesized_variant(album):
        issues.append("parenthesized_variant")

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
            existing.disc_number = disc_number or existing.disc_number or 1
            # Priority: source > existing (user-set or prior) > inferred > 1
            existing.total_discs = (
                source_total_discs
                or (existing.total_discs if existing.total_discs and existing.total_discs > 1 else None)
                or inferred_total_discs
                or 1
            )
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
                total_discs=source_total_discs or inferred_total_discs or 1,
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
        elif clear_placeholder_titles:
            # Placeholder titles like "101","102" were previously written into
            # tracks.title — clear them so the review UI shows blanks instead
            # of misleading numbers when no real titles are available.
            tracks = await session.execute(
                select(Track)
                .where(Track.job_id == job_id)
                .order_by(Track.track_num)
            )
            for track in tracks.scalars():
                if track.title and re.match(r"^\d{1,4}$", track.title.strip()):
                    track.title = None

        await session.commit()

    logger.info(
        "Sanitized metadata for job %s: artist=%r album=%r conf=%d issues=%s",
        job_id, artist, album, confidence, issues,
    )
    return meta


async def _get_track_count(job_id: str) -> int:
    """Number of tracks actually ripped — used as a hard signal for picking track_titles."""
    async with async_session() as session:
        from sqlalchemy import func
        result = await session.execute(
            select(func.count(Track.id)).where(Track.job_id == job_id)
        )
        return result.scalar() or 0


# Source preference order for track titles when scores tie. iTunes/MB/Discogs
# tend to have curated track listings; CDDB is community-submitted and often
# carries tie-up annotations or placeholder text; HMV is scraped and noisy.
_TRACK_SOURCE_BONUS = {
    "musicbrainz": 15,
    "itunes": 10,
    "discogs": 8,
    "llm": 5,
    "hmv": 3,
    "cddb": -10,
}

# Strong tie-up/show/remaster keywords. Generic variant markers (Live, Edit,
# Version, Mix) are deliberately excluded — they're often part of canonical
# track titles ("Track Name (Single Edit)") rather than added annotations.
_ANNOTATION_KEYWORDS = (
    "主題歌", "テーマ曲", "CMソング", "オープニング", "エンディング",
    "挿入歌", "ドラマ", "映画", "Remastering", "Remaster", "LIVE FILM",
)


def _has_annotation(title: str) -> bool:
    """Detect tie-up/tour/remaster annotations appended to a track title.

    Triggers when:
    - Title contains 『...』 (Japanese quotes — typically wrap tour/movie/show titles)
    - Title ends with a parenthesized phrase ≥ 8 chars containing a strong
      annotation keyword (主題歌, ドラマ, Remastering, etc.)
    """
    if not title:
        return False
    if "『" in title or "』" in title:
        return True
    m = re.search(r"[\(（]([^)）]{8,})[\)）]\s*$", title)
    if m:
        inner = m.group(1)
        if any(kw in inner for kw in _ANNOTATION_KEYWORDS):
            return True
    return False


def _pick_best_track_titles(
    candidates: list[MetadataCandidate],
    expected_count: int,
) -> dict | None:
    """Score each candidate's track_titles and return the best one.

    Scoring (per candidate that has track_titles):
      base                                     = candidate.confidence (0-100)
      track count match    +25  / mismatch     -50 (hard penalty)
      no mojibake          +10  / has mojibake -40
      no placeholder       +10  / placeholder  -50
      annotation ratio                          -30 * ratio (0..1)
      source preference                         see _TRACK_SOURCE_BONUS

    Returns: {"titles": [...], "source": "...", "score": int, "annotation_ratio": float}
    or None if no candidate has any titles.
    """
    best: dict | None = None
    best_score = -10**6

    for c in candidates:
        if not c.track_titles:
            continue
        try:
            titles = json.loads(c.track_titles)
        except (json.JSONDecodeError, TypeError):
            continue
        if not titles or not isinstance(titles, list):
            continue

        score = c.confidence or 0

        # Track count match — strongest signal
        if expected_count:
            if len(titles) == expected_count:
                score += 25
            else:
                score -= 50

        # Placeholder pattern: "Track NN" or pure digits
        is_placeholder = (
            all(re.match(r"^Track\s*\d+$", t, re.IGNORECASE) for t in titles)
            or all(t and re.match(r"^\d{1,4}$", t.strip()) for t in titles)
        )
        if is_placeholder:
            score -= 50

        # Mojibake on any title disqualifies heavily
        has_mojibake = any(_looks_like_mojibake(t) for t in titles)
        score += -40 if has_mojibake else 10

        # Annotation density — many sources tack tie-up names onto tracks;
        # canonical sources don't.
        annotated = sum(1 for t in titles if _has_annotation(t))
        annotation_ratio = annotated / len(titles) if titles else 0.0
        score -= int(30 * annotation_ratio)

        # Source preference
        score += _TRACK_SOURCE_BONUS.get(c.source, 0)

        if score > best_score:
            best_score = score
            best = {
                "titles": titles,
                "source": c.source,
                "score": score,
                "annotation_ratio": annotation_ratio,
            }

    return best


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


def _has_parenthesized_variant(text: str) -> bool:
    """Detect parenthesized romanization/translation appended to a name.

    Catches patterns like:
    - "葉加瀬太郎 (Taro Hakase)" — Japanese name + romanized
    - "Ryuichi Sakamoto (坂本龍一)" — Romanized + Japanese
    - "交響曲第9番 (Symphony No. 9)" — Japanese title + English translation

    Heuristic: text has a parenthesized suffix, and either the base or the
    parenthesized part uses a different primary script (CJK vs Latin).
    """
    if not text:
        return False
    m = re.match(r"^(.+?)\s*[(\uff08](.+?)[)\uff09]\s*$", text)
    if not m:
        return False
    base = m.group(1).strip()
    paren = m.group(2).strip()
    if not base or not paren:
        return False
    base_cjk = _cjk_ratio(base)
    paren_cjk = _cjk_ratio(paren)
    # One part is primarily CJK, the other primarily Latin → redundant variant
    return (base_cjk > 0.5) != (paren_cjk > 0.5)


def _cjk_ratio(text: str) -> float:
    """Return the ratio of CJK + kana characters in text."""
    stripped = re.sub(r"\s+", "", text)
    if not stripped:
        return 0.0
    cjk = sum(
        1 for c in stripped
        if "\u3000" <= c <= "\u9fff"  # CJK, hiragana, katakana
        or "\uf900" <= c <= "\ufaff"  # CJK compat
        or "\U00020000" <= c <= "\U0002fa1f"  # CJK ext
    )
    return cjk / len(stripped)
