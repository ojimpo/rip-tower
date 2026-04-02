"""iTunes Search API metadata source — good for artwork URLs.

Rate limit: 3 seconds between requests.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

ITUNES_BASE = "https://itunes.apple.com/search"
RATE_LIMIT = 3.0  # 3 seconds — iTunes API is strict


class ItunesSource(MetadataSource):
    @property
    def name(self) -> str:
        return "itunes"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        if not hints:
            return []

        # Build search term from available hints
        parts = []
        if hints.get("artist"):
            parts.append(hints["artist"])
        if hints.get("title"):
            parts.append(hints["title"])
        if not parts:
            return []

        term = " ".join(parts)

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(ITUNES_BASE, params={
                    "term": term,
                    "media": "music",
                    "entity": "album",
                    "limit": 5,
                    "country": "JP",  # Prefer Japanese store for J-music
                })
                if resp.status_code != 200:
                    logger.warning("iTunes search returned %d", resp.status_code)
                    return []

                data = resp.json()
            except Exception:
                logger.exception("iTunes search failed")
                return []

        track_count = identity.track_count if identity else 0
        candidates = []

        for r in data.get("results", []):
            if r.get("wrapperType") != "collection":
                continue

            r_artist = r.get("artistName", "")
            r_album = r.get("collectionName", "")
            r_year = str(r.get("releaseDate", ""))[:4] or None
            r_genre = r.get("primaryGenreName", "")

            # Artwork URL: replace 100x100 with 600x600 for higher quality
            artwork_url = (r.get("artworkUrl100") or "").replace("100x100", "600x600")

            conf = 30
            # Track count match boosts confidence
            if track_count and r.get("trackCount") == track_count:
                conf += 10

            evidence = {
                "itunes_id": r.get("collectionId", ""),
                "artwork_url": artwork_url,
                "match": "search",
            }

            candidates.append({
                "artist": r_artist,
                "album": r_album,
                "year": r_year,
                "genre": r_genre,
                "confidence": min(conf, 60),  # Cap — iTunes text search is imprecise
                "source_url": r.get("collectionViewUrl", ""),
                "evidence": json.dumps(evidence, ensure_ascii=False),
            })

        return candidates
