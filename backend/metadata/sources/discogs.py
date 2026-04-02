"""Discogs metadata source — search by catalog number and text.

Rate limit: 1 request per second (Discogs API requirement).
Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_discogs_search).
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from backend.config import get_config
from backend.metadata.normalize import similarity
from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

DISCOGS_BASE = "https://api.discogs.com"
HEADERS = {"User-Agent": "RipTower/0.1.0 (https://github.com/kouki/rip-tower)"}
RATE_LIMIT = 1.0  # 1 request per second


class DiscogsSource(MetadataSource):
    @property
    def name(self) -> str:
        return "discogs"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        token = get_config().integrations.discogs_token
        if not token:
            logger.debug("DISCOGS_TOKEN not configured, skipping Discogs search")
            return []

        params: dict[str, str] = {"type": "release", "format": "CD"}
        if hints:
            if hints.get("catalog"):
                params["catno"] = hints["catalog"]
            if hints.get("title"):
                params["title"] = hints["title"]
            if hints.get("artist"):
                params["artist"] = hints["artist"]

        if len(params) <= 2:
            # Only type and format — no actual search terms
            return []

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            try:
                resp = await client.get(
                    f"{DISCOGS_BASE}/database/search",
                    params=params,
                    headers={"Authorization": f"Discogs token={token}"},
                )
                if resp.status_code != 200:
                    logger.warning("Discogs search returned %d", resp.status_code)
                    return []

                data = resp.json()
            except Exception:
                logger.exception("Discogs search failed")
                return []

        hint_catalog = (hints or {}).get("catalog", "")
        hint_title = (hints or {}).get("title", "")
        hint_artist = (hints or {}).get("artist", "")

        candidates = []
        for r in data.get("results", [])[:5]:
            # Discogs titles are "Artist - Album" format
            parts = r.get("title", "").split(" - ", 1)
            r_artist = parts[0].strip() if len(parts) == 2 else ""
            r_album = parts[1].strip() if len(parts) == 2 else r.get("title", "")

            conf = 35
            # Boost confidence for catalog number match
            if hint_catalog:
                for label_info in r.get("label", []):
                    if hint_catalog.upper() in label_info.get("catno", "").upper():
                        conf += 25
                        break
            # Title similarity boost
            if hint_title and similarity(hint_title, r_album) >= 0.8:
                conf += 10
            # Artist similarity boost
            if hint_artist and similarity(hint_artist, r_artist) >= 0.6:
                conf += 5

            candidates.append({
                "artist": r_artist,
                "album": r_album,
                "year": (r.get("year") or ""),
                "confidence": min(conf, 75),
                "source_url": r.get("resource_url", ""),
                "evidence": json.dumps({
                    "discogs_id": r.get("id", ""),
                    "catno": r.get("catno", ""),
                    "match": "search",
                }, ensure_ascii=False),
            })

        return candidates
