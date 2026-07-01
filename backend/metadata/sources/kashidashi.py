"""Kashidashi (library loan tracking) metadata source.

Queries kashidashi API for matching items. Also provides a standalone
match_kashidashi() function for post-resolution fuzzy matching.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_kashidashi_priors)
and ~/dev/openclaw-cd-rip/scripts/kashidashi.py.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.config import get_config
from backend.database import async_session
from backend.metadata.evidence import parse_evidence
from backend.metadata.normalize import norm, similarity
from backend.metadata.sources.base import MetadataSource
from backend.models import KashidashiCandidate, JobMetadata

logger = logging.getLogger(__name__)


class KashidashiSource(MetadataSource):
    @property
    def name(self) -> str:
        return "kashidashi"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        """Search kashidashi for items matching the disc being ripped."""
        base_url = get_config().integrations.kashidashi_url
        if not base_url:
            return []

        items = await fetch_active_borrowed_items()

        disc_id = identity.disc_id if identity else None
        track_count = identity.track_count if identity else 0
        hint_artist = (hints or {}).get("artist", "")
        hint_album = (hints or {}).get("title", "")
        hint_catalog = (hints or {}).get("catalog", "")

        candidates = []
        for it in items:
            # Exact disc ID match — highest confidence
            if disc_id and it.get("rip_discid") and disc_id == it["rip_discid"]:
                candidates.append({
                    "artist": it.get("metadata_artist") or it.get("artist", ""),
                    "album": it.get("metadata_album") or it.get("title", ""),
                    "confidence": 95,
                    "source_url": f"{base_url}/api/items/{it.get('id')}",
                    "evidence": json.dumps({
                        "kashidashi_id": it.get("id"),
                        "match": "exact_discid",
                    }, ensure_ascii=False),
                })
                continue

            # Fuzzy matching
            score = 0
            evidence: dict[str, Any] = {"kashidashi_id": it.get("id")}

            # Album similarity
            for field in [it.get("title", ""), it.get("metadata_album", "")]:
                s = similarity(hint_album, field)
                if s >= 0.8:
                    score += 4
                    evidence["album_sim"] = round(s, 2)
                    break
                elif s >= 0.4:
                    score += 2
                    evidence["album_partial"] = round(s, 2)
                    break

            # Artist similarity
            for field in [it.get("artist", ""), it.get("metadata_artist", "")]:
                s = similarity(hint_artist, field)
                if s >= 0.6:
                    score += 2
                    evidence["artist_sim"] = round(s, 2)
                    break

            # Track count match
            if _track_count_matches(it, track_count):
                score += 3
                evidence["track_count_match"] = True

            # Catalog number match
            item_cat = it.get("catalog_number") or it.get("catalog") or ""
            if hint_catalog and item_cat and norm(hint_catalog) == norm(item_cat):
                score += 5
                evidence["catalog_match"] = True

            # Recency bonus
            days = _days_since_borrow(it)
            if days is not None and 0 <= days <= 14:
                score += 1
                evidence["recent_days"] = days

            # Require at least one real signal (artist/album/track_count/catalog).
            # Recency alone (score 1) is not evidence that this disc is that item —
            # it just means the user borrowed it recently, which would flood the
            # candidate pool with unrelated recent borrows when no real source hits.
            if score >= 2:
                candidates.append({
                    "artist": it.get("metadata_artist") or it.get("artist", ""),
                    "album": it.get("metadata_album") or it.get("title", ""),
                    "confidence": min(int(score * 5), 80),
                    "source_url": f"{base_url}/api/items/{it.get('id')}",
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                })

        # Phase 1 fallback: when no exact / fuzzy match and no external hints,
        # surface recently-borrowed unripped items as low-to-mid confidence
        # candidates. Even when kashidashi's metadata_* fields are empty, the
        # plain `artist`/`title` columns suffice to drive Phase 2 text-search
        # (MusicBrainz/iTunes/Discogs) which can then return proper tracklists.
        if not candidates and not hint_artist and not hint_album:
            candidates.extend(
                _recency_fallback_candidates(items, base_url, track_count)
            )

        return candidates


_FALLBACK_WINDOW_DAYS = 7


def _track_count_matches(item: dict, track_count: int) -> bool:
    """Whether the item's recorded track count equals the disc's.

    kashidashi's metadata_track_count is free-form user input — a non-numeric
    value must not blow up the whole source with a ValueError.
    """
    raw = item.get("metadata_track_count")
    if not raw or not track_count:
        return False
    try:
        return int(raw) == int(track_count)
    except (TypeError, ValueError):
        return False


def _days_since_borrow(item: dict) -> int | None:
    """Days since the item's borrowed_date (UTC), or None when unparseable."""
    bd = item.get("borrowed_date") or ""
    try:
        borrowed = datetime.strptime(bd, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - borrowed).days


async def fetch_active_borrowed_items() -> list[dict]:
    """Return kashidashi CD items currently checked out (not returned, not ripped).

    Shared by KashidashiSource.search() and the resolver's cross-reference
    boost — both need the same "what's currently in the user's hands" list,
    so fetching it once here avoids divergence. Returns an empty list if
    kashidashi isn't configured or the API call fails.
    """
    base_url = get_config().integrations.kashidashi_url
    if not base_url:
        return []
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.get(f"{base_url}/api/items", params={"type": "cd"})
            if resp.status_code != 200:
                return []
            items = resp.json()
        except Exception:
            logger.exception("Kashidashi API error")
            return []
    return [
        it for it in items
        if not it.get("returned_at") and not it.get("ripped_at")
    ]


def kashidashi_match_score(
    candidate_artist: str,
    candidate_album: str,
    items: list[dict],
) -> tuple[dict | None, float, float]:
    """Find the best fuzzy match between a candidate and the borrowed pool.

    Returns (item, artist_sim, album_sim) for the highest combined similarity,
    or (None, 0.0, 0.0) if neither side has any text to compare. Both `artist`
    and `metadata_artist` (same for album) on the item are tried so that
    library staff's free-text spelling and resolver-corrected metadata both
    count as evidence.
    """
    if not candidate_artist and not candidate_album:
        return None, 0.0, 0.0
    best: tuple[dict | None, float, float] = (None, 0.0, 0.0)
    for it in items:
        item_artists = [it.get("artist") or "", it.get("metadata_artist") or ""]
        item_albums = [it.get("title") or "", it.get("metadata_album") or ""]
        art_sim = max(
            (similarity(candidate_artist, a) for a in item_artists if a),
            default=0.0,
        )
        alb_sim = max(
            (similarity(candidate_album, a) for a in item_albums if a),
            default=0.0,
        )
        if art_sim + alb_sim > best[1] + best[2]:
            best = (it, art_sim, alb_sim)
    return best


_ALIAS_SIM_THRESHOLD = 0.6


def _mb_release_id(candidate: Any) -> str | None:
    """The MusicBrainz release id a candidate points at, if any."""
    ev = parse_evidence(candidate)
    if ev.get("mb_release"):
        return ev["mb_release"]
    url = candidate.source_url or ""
    if "musicbrainz.org/release/" in url:
        return url.rstrip("/").split("/")[-1]
    return None


def _is_disc_anchored(candidate: Any) -> bool:
    """True when the candidate matched the disc by its physical TOC / disc ID.

    Then the album identity is proven by the disc itself, so the kashidashi
    cross-check only needs to confirm the *artist* to decide a TOC collision.
    """
    return parse_evidence(candidate).get("match") in ("toc_submission", "exact_discid")


async def best_kashidashi_match(
    candidate: Any, items: list[dict]
) -> tuple[dict | None, float, float]:
    """Like `kashidashi_match_score`, but script-insensitive.

    Tries a direct text match first. If that doesn't clear the bar and the
    candidate is a MusicBrainz release, it augments the comparison with the
    release's MB artist/release aliases — so a Japanese borrowed record
    ("エイミー・ワインハウス") matches an English candidate ("Amy Winehouse").
    For disc-anchored candidates the album is already proven by the TOC, so a
    confirmed artist alone suffices; this is what tips a TOC collision toward
    the CD the user actually borrowed.
    """
    artist = candidate.artist or ""
    album = candidate.album or ""

    item, art_sim, alb_sim = kashidashi_match_score(artist, album, items)
    if (
        item is not None
        and art_sim >= _ALIAS_SIM_THRESHOLD
        and alb_sim >= _ALIAS_SIM_THRESHOLD
    ):
        return item, art_sim, alb_sim

    release_id = _mb_release_id(candidate)
    if not release_id:
        return item, art_sim, alb_sim

    from backend.metadata.sources.musicbrainz import fetch_release_artist_aliases

    artist_aliases, release_aliases = await fetch_release_artist_aliases(release_id)
    if not artist_aliases and not release_aliases:
        return item, art_sim, alb_sim

    artist_names = [artist, *artist_aliases]
    album_names = [album, *release_aliases]
    disc_anchored = _is_disc_anchored(candidate)

    best = (item, art_sim, alb_sim)
    for it in items:
        item_artists = [it.get("artist") or "", it.get("metadata_artist") or ""]
        item_albums = [it.get("title") or "", it.get("metadata_album") or ""]
        a_sim = max(
            (similarity(n, a) for n in artist_names if n for a in item_artists if a),
            default=0.0,
        )
        l_sim = max(
            (similarity(n, a) for n in album_names if n for a in item_albums if a),
            default=0.0,
        )
        # Disc-anchored: the TOC already proves which album this is, so a
        # cross-script album title must not veto a confirmed-artist match.
        if disc_anchored and a_sim >= _ALIAS_SIM_THRESHOLD:
            l_sim = max(l_sim, _ALIAS_SIM_THRESHOLD)
        if a_sim + l_sim > best[1] + best[2]:
            best = (it, a_sim, l_sim)
    return best


def _recency_fallback_candidates(
    items: list[dict], base_url: str, track_count: int
) -> list[dict]:
    """Build recency-fallback candidates from kashidashi items.

    Only items borrowed within _FALLBACK_WINDOW_DAYS and not yet returned/ripped,
    that have at least one of `artist`/`title` filled in, are considered.

    Confidence scales inversely with eligible-pool size: when the user borrowed
    one disc today, that disc is almost certainly the one being ripped now;
    when they borrowed five at once, we can't tell which is which, so each
    candidate is low-confidence and review will surface them all.
    """
    eligible: list[tuple[int, dict]] = []
    for it in items:
        if it.get("returned_at") or it.get("ripped_at"):
            continue
        if not (it.get("artist") or it.get("title")
                or it.get("metadata_artist") or it.get("metadata_album")):
            continue
        days = _days_since_borrow(it)
        if days is None or days < 0 or days > _FALLBACK_WINDOW_DAYS:
            continue
        eligible.append((days, it))

    pool_size = len(eligible)
    if pool_size == 0:
        return []
    if pool_size == 1:
        base = 70
    elif pool_size <= 3:
        base = 60
    else:
        base = 50

    out: list[dict] = []
    for days, it in eligible:
        c = base
        if _track_count_matches(it, track_count):
            c += 5
        if days == 0:
            c += 5
        out.append({
            "artist": it.get("metadata_artist") or it.get("artist", ""),
            "album": it.get("metadata_album") or it.get("title", ""),
            "confidence": min(c, 80),
            "source_url": f"{base_url}/api/items/{it.get('id')}",
            "evidence": json.dumps({
                "kashidashi_id": it.get("id"),
                "match": "recency_fallback",
                "days_since_borrow": days,
                "pool_size": pool_size,
            }, ensure_ascii=False),
        })
    return out


async def match_kashidashi(job_id: str, identity: Any) -> None:
    """Post-resolution: fuzzy match the resolved metadata against kashidashi items.

    Saves KashidashiCandidate records for the job. If a clear match is found
    (no ambiguous ties), marks it as matched.
    """
    base_url = get_config().integrations.kashidashi_url
    if not base_url:
        return

    # Load resolved metadata for this job
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = result.scalar_one_or_none()
        if not meta or not meta.artist:
            logger.debug("No resolved metadata for job %s, skipping kashidashi match", job_id)
            return

    album = meta.album or ""
    artist = meta.artist or ""
    album_n = norm(album)
    artist_n = norm(artist)
    track_count = identity.track_count if identity else 0

    # Fetch kashidashi items
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.get(f"{base_url}/api/items")
            if resp.status_code != 200:
                return
            items = resp.json()
        except Exception:
            logger.exception("Kashidashi API error during match")
            return

    # Score each item
    scored: list[tuple[int, dict]] = []
    for it in items:
        if it.get("type") != "cd":
            continue
        if it.get("returned_at"):
            continue

        score = 0

        # Album matching — check both exact containment and fuzzy similarity
        best_album_sim = 0.0
        for field_val in [it.get("title", ""), it.get("metadata_album", "")]:
            fn = norm(field_val)
            if not fn or not album_n:
                continue
            if album_n == fn or album_n in fn or fn in album_n:
                score += 3
                best_album_sim = 1.0
                break
            sim = similarity(album_n, fn)
            best_album_sim = max(best_album_sim, sim)

        if best_album_sim >= 0.6 and score == 0:
            score += 2  # fuzzy album match

        # Artist matching — check containment, fuzzy similarity,
        # and handle romanization differences (e.g. "THE CHECKERS" vs "チェッカーズ")
        best_artist_sim = 0.0
        for field_val in [it.get("artist", ""), it.get("metadata_artist", "")]:
            fn = norm(field_val)
            if not fn or not artist_n:
                continue
            if artist_n == fn or artist_n in fn or fn in artist_n:
                score += 2
                best_artist_sim = 1.0
                break
            sim = similarity(artist_n, fn)
            best_artist_sim = max(best_artist_sim, sim)

        if best_artist_sim >= 0.5 and score > 0:
            score += 1  # partial artist match when album already matched

        # Track count bonus
        tc_match = _track_count_matches(it, track_count)
        if tc_match:
            score += 3

        # Strong album match + track count = likely correct even with different artist name format
        if best_album_sim >= 0.7 and tc_match:
            score += 2

        if score > 0:
            scored.append((score, it))

    if not scored:
        logger.debug("No kashidashi candidates matched for job %s", job_id)
        return

    scored.sort(key=lambda x: (x[0], x[1].get("borrowed_date", "")), reverse=True)

    # Save candidates and determine match
    top_score = scored[0][0]
    tied = [c for c in scored if c[0] == top_score]
    is_unique_match = len(tied) == 1

    async with async_session() as session:
        from sqlalchemy import delete
        # Clear prior candidates from earlier resolves so re-resolves don't
        # accumulate stale (and duplicate matched) rows.
        await session.execute(
            delete(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id)
        )
        for score, it in scored[:5]:  # Save top 5 candidates
            candidate = KashidashiCandidate(
                job_id=job_id,
                item_id=it.get("id", 0),
                title=it.get("metadata_album") or it.get("title", ""),
                artist=it.get("metadata_artist") or it.get("artist", ""),
                score=float(score),
                # Score-based labels: this matcher works on text similarity, so
                # even a high score is "strong", never a proven disc-ID match.
                match_type="strong" if score >= 7 else "fuzzy",
                matched=is_unique_match and score == top_score,
            )
            session.add(candidate)
        await session.commit()

    if is_unique_match:
        target = scored[0][1]
        # Auto-set source_type to library
        async with async_session() as session:
            from backend.models import Job
            job = await session.get(Job, job_id)
            if job and job.source_type == "unknown":
                job.source_type = "library"
                await session.commit()
        logger.info(
            "Kashidashi matched: job=%s -> item_id=%s (%s)",
            job_id, target.get("id"), target.get("title"),
        )
    else:
        logger.info(
            "Kashidashi ambiguous: job=%s, %d tied at score=%d",
            job_id, len(tied), top_score,
        )
