"""MusicBrainz metadata source — disc ID lookup and text search."""

import asyncio
import json
import logging
from typing import Any

import httpx

from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "RipTower/0.1.0 (https://github.com/kouki/rip-tower)"}
RATE_LIMIT = 1.0  # 1 request per second


class MusicBrainzSource(MetadataSource):
    @property
    def name(self) -> str:
        return "musicbrainz"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        candidates = []

        # 1. Disc ID lookup (highest confidence)
        if identity and identity.disc_id:
            results = await self._lookup_discid(identity.disc_id)
            candidates.extend(results)

        # 2. Text search fallback
        if not candidates and hints:
            results = await self._text_search(hints)
            candidates.extend(results)

        return candidates

    async def _lookup_discid(self, disc_id: str) -> list[dict]:
        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/discid/{disc_id}",
                    params={"fmt": "json", "inc": "recordings+artist-credits"},
                )
                if resp.status_code != 200:
                    return []

                data = resp.json()
                releases = data.get("releases", [])
                candidates = []

                for release in releases:
                    artist = ""
                    credits = release.get("artist-credit", [])
                    if credits:
                        artist = "".join(
                            c.get("name", "") + c.get("joinphrase", "")
                            for c in credits
                        )

                    tracks = []
                    for medium in release.get("media", []):
                        for track in medium.get("tracks", []):
                            tracks.append(track.get("title", ""))

                    candidates.append({
                        "artist": artist,
                        "album": release.get("title", ""),
                        "year": (release.get("date") or "")[:4] or None,
                        "track_titles": json.dumps(tracks, ensure_ascii=False),
                        "confidence": 95,
                        "source_url": f"https://musicbrainz.org/release/{release.get('id', '')}",
                        "evidence": json.dumps({"match": "disc_id_exact"}, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz disc ID lookup failed")
                return []

    async def _text_search(self, hints: dict) -> list[dict]:
        query_parts = []
        if hints.get("artist"):
            query_parts.append(f'artist:"{hints["artist"]}"')
        if hints.get("title"):
            query_parts.append(f'release:"{hints["title"]}"')
        if hints.get("catalog"):
            query_parts.append(f'catno:"{hints["catalog"]}"')

        if not query_parts:
            return []

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/release",
                    params={"query": " AND ".join(query_parts), "fmt": "json", "limit": 5},
                )
                if resp.status_code != 200:
                    return []

                data = resp.json()
                candidates = []

                for release in data.get("releases", []):
                    artist = ""
                    credits = release.get("artist-credit", [])
                    if credits:
                        artist = "".join(
                            c.get("name", "") + c.get("joinphrase", "")
                            for c in credits
                        )

                    score = release.get("score", 0)
                    candidates.append({
                        "artist": artist,
                        "album": release.get("title", ""),
                        "year": (release.get("date") or "")[:4] or None,
                        "confidence": min(score, 80),  # Cap text search confidence
                        "source_url": f"https://musicbrainz.org/release/{release.get('id', '')}",
                        "evidence": json.dumps({"match": "text_search", "score": score}, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz text search failed")
                return []
