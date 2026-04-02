"""Kashidashi (library loan tracking) metadata source.

Queries kashidashi API for matching items. Also provides a standalone
match_kashidashi() function for post-resolution fuzzy matching.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_kashidashi_priors)
and ~/dev/openclaw-cd-rip/scripts/kashidashi.py.
"""

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from backend.config import get_config
from backend.database import async_session
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

        async with httpx.AsyncClient(timeout=8) as client:
            try:
                resp = await client.get(f"{base_url}/api/items", params={
                    "type": "cd", "status": "not_ripped",
                })
                if resp.status_code != 200:
                    return []
                items = resp.json()
            except Exception:
                logger.exception("Kashidashi API error")
                return []

        disc_id = identity.disc_id if identity else None
        track_count = identity.track_count if identity else 0
        hint_artist = (hints or {}).get("artist", "")
        hint_album = (hints or {}).get("title", "")
        hint_catalog = (hints or {}).get("catalog", "")

        candidates = []
        for it in items:
            if it.get("returned_at") or it.get("ripped_at"):
                continue

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
            item_tc = it.get("metadata_track_count")
            if item_tc and track_count and int(item_tc) == int(track_count):
                score += 3
                evidence["track_count_match"] = True

            # Catalog number match
            item_cat = it.get("catalog_number") or it.get("catalog") or ""
            if hint_catalog and item_cat and norm(hint_catalog) == norm(item_cat):
                score += 5
                evidence["catalog_match"] = True

            # Recency bonus
            bd = it.get("borrowed_date", "")
            if bd:
                try:
                    days = (datetime.now() - datetime.strptime(bd, "%Y-%m-%d")).days
                    if days <= 14:
                        score += 1
                        evidence["recent_days"] = days
                except ValueError:
                    pass

            if score > 0:
                candidates.append({
                    "artist": it.get("metadata_artist") or it.get("artist", ""),
                    "album": it.get("metadata_album") or it.get("title", ""),
                    "confidence": min(int(score * 5), 80),
                    "source_url": f"{base_url}/api/items/{it.get('id')}",
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                })

        return candidates


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

        # Album matching
        for field_val in [it.get("title", ""), it.get("metadata_album", "")]:
            fn = norm(field_val)
            if fn and album_n and (album_n == fn or album_n in fn or fn in album_n):
                score += 3
                break

        # Artist matching
        for field_val in [it.get("artist", ""), it.get("metadata_artist", "")]:
            fn = norm(field_val)
            if fn and artist_n and (artist_n in fn or fn in artist_n):
                score += 1
                break

        # Track count bonus
        item_tc = it.get("metadata_track_count")
        if item_tc and track_count and int(item_tc) == int(track_count):
            score += 3

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
        for score, it in scored[:5]:  # Save top 5 candidates
            candidate = KashidashiCandidate(
                job_id=job_id,
                item_id=it.get("id", 0),
                title=it.get("metadata_album") or it.get("title", ""),
                artist=it.get("metadata_artist") or it.get("artist", ""),
                score=float(score),
                match_type="exact_discid" if score >= 7 else "fuzzy",
                matched=is_unique_match and score == top_score,
            )
            session.add(candidate)
        await session.commit()

    if is_unique_match:
        target = scored[0][1]
        logger.info(
            "Kashidashi matched: job=%s -> item_id=%s (%s)",
            job_id, target.get("id"), target.get("title"),
        )
    else:
        logger.info(
            "Kashidashi ambiguous: job=%s, %d tied at score=%d",
            job_id, len(tied), top_score,
        )
