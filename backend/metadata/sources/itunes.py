"""iTunes Search API metadata source.

Phase 1: search for album by artist/title text → get collection candidates.
Phase 2: lookup each top collection for its track listing → fill track_titles.

Rate limit: 3 seconds between requests.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

ITUNES_BASE = "https://itunes.apple.com"
RATE_LIMIT = 3.0
LOOKUP_LIMIT = 200


class ItunesSource(MetadataSource):
    @property
    def name(self) -> str:
        return "itunes"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        if not hints:
            return []

        parts = []
        if hints.get("artist"):
            parts.append(hints["artist"])
        if hints.get("title"):
            parts.append(hints["title"])
        if not parts:
            return []

        term = " ".join(parts)
        track_count = identity.track_count if identity else 0
        target_disc = (hints.get("disc_number") if hints else None) or 1

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(f"{ITUNES_BASE}/search", params={
                    "term": term,
                    "media": "music",
                    "entity": "album",
                    "limit": 5,
                    "country": "JP",
                })
                if resp.status_code != 200:
                    logger.warning("iTunes search returned %d", resp.status_code)
                    return []
                data = resp.json()
            except Exception:
                logger.exception("iTunes search failed")
                return []

            collections = [
                r for r in data.get("results", [])
                if r.get("wrapperType") == "collection"
            ]
            if not collections:
                return []

            candidates: list[dict] = []
            # Lookup track listings for top 3 collections
            for r in collections[:3]:
                collection_id = r.get("collectionId")
                if not collection_id:
                    continue

                tracks = await self._lookup_tracks(
                    client, collection_id, target_disc, track_count
                )
                candidates.append(
                    self._build_candidate(r, tracks, track_count, target_disc)
                )

            return candidates

    async def _lookup_tracks(
        self,
        client: httpx.AsyncClient,
        collection_id: int,
        target_disc: int,
        track_count: int,
    ) -> list[str]:
        """Fetch track listing for an iTunes collection, filtered to target disc."""
        await asyncio.sleep(RATE_LIMIT)
        try:
            resp = await client.get(f"{ITUNES_BASE}/lookup", params={
                "id": collection_id,
                "entity": "song",
                "country": "JP",
                "limit": LOOKUP_LIMIT,
            })
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            logger.exception("iTunes lookup failed for id=%s", collection_id)
            return []

        # Group tracks by disc number, sort by track number
        by_disc: dict[int, list[tuple[int, str]]] = {}
        for r in data.get("results", []):
            if r.get("wrapperType") != "track":
                continue
            disc = int(r.get("discNumber") or 1)
            tnum = int(r.get("trackNumber") or 0)
            title = r.get("trackName", "")
            if title:
                by_disc.setdefault(disc, []).append((tnum, title))

        # Prefer requested disc; fall back to disc 1; fall back to single-disc collection
        chosen_disc = None
        if target_disc in by_disc:
            chosen_disc = target_disc
        elif track_count and len(by_disc) > 1:
            # Pick disc whose track count matches the actual disc being ripped
            for d, items in by_disc.items():
                if len(items) == track_count:
                    chosen_disc = d
                    break
        if chosen_disc is None and by_disc:
            chosen_disc = sorted(by_disc.keys())[0]

        if chosen_disc is None:
            return []

        tracks = sorted(by_disc[chosen_disc], key=lambda x: x[0])
        return [title for _, title in tracks]

    def _build_candidate(
        self,
        collection: dict,
        tracks: list[str],
        track_count: int,
        target_disc: int,
    ) -> dict:
        artist = collection.get("artistName", "")
        album = collection.get("collectionName", "")
        year = str(collection.get("releaseDate", ""))[:4] or None
        genre = collection.get("primaryGenreName", "")
        artwork_url = (collection.get("artworkUrl100") or "").replace(
            "100x100", "600x600"
        )

        conf = 30
        if track_count and collection.get("trackCount") == track_count:
            conf += 10
        if tracks and track_count and len(tracks) == track_count:
            conf += 15  # exact track-listing match — strong signal

        track_titles_json = json.dumps(tracks, ensure_ascii=False) if tracks else None

        evidence = {
            "itunes_id": collection.get("collectionId", ""),
            "artwork_url": artwork_url,
            "match": "search",
            "disc_number": target_disc,
        }
        if collection.get("collectionExplicitness"):
            evidence["explicitness"] = collection["collectionExplicitness"]

        return {
            "artist": artist,
            "album": album,
            "year": year,
            "genre": genre,
            "track_titles": track_titles_json,
            # Cap at 75 — text search is still less reliable than disc-ID lookups
            "confidence": min(conf, 75),
            "source_url": collection.get("collectionViewUrl", ""),
            "evidence": json.dumps(evidence, ensure_ascii=False),
        }
