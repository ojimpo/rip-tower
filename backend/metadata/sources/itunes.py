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
        # None when the caller gave no disc hint — then track-count matching
        # decides the disc instead of silently defaulting to disc 1.
        hinted_disc = hints.get("disc_number")

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

                tracks, disc_number, total_discs = await self._lookup_tracks(
                    client, collection_id, hinted_disc, track_count
                )
                candidates.append(
                    self._build_candidate(
                        r, tracks, track_count, disc_number, total_discs
                    )
                )

            return candidates

    async def _lookup_tracks(
        self,
        client: httpx.AsyncClient,
        collection_id: int,
        hinted_disc: int | None,
        track_count: int,
    ) -> tuple[list[str], int, int]:
        """Fetch track listing for an iTunes collection.

        Returns (titles, chosen_disc, total_discs). An explicit disc hint wins;
        otherwise the disc whose track count matches the physical disc is
        chosen, so ripping disc 2 of a set without a hint doesn't silently get
        disc 1's titles.
        """
        await asyncio.sleep(RATE_LIMIT)
        try:
            resp = await client.get(f"{ITUNES_BASE}/lookup", params={
                "id": collection_id,
                "entity": "song",
                "country": "JP",
                "limit": LOOKUP_LIMIT,
            })
            if resp.status_code != 200:
                return [], 1, 1
            data = resp.json()
        except Exception:
            logger.exception("iTunes lookup failed for id=%s", collection_id)
            return [], 1, 1

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

        if not by_disc:
            return [], 1, 1
        total_discs = len(by_disc)

        # Explicit hint → track-count match → first disc
        chosen_disc = None
        if hinted_disc is not None and hinted_disc in by_disc:
            chosen_disc = hinted_disc
        if chosen_disc is None and track_count:
            for d in sorted(by_disc):
                if len(by_disc[d]) == track_count:
                    chosen_disc = d
                    break
        if chosen_disc is None:
            chosen_disc = sorted(by_disc)[0]

        tracks = sorted(by_disc[chosen_disc], key=lambda x: x[0])
        return [title for _, title in tracks], chosen_disc, total_discs

    def _build_candidate(
        self,
        collection: dict,
        tracks: list[str],
        track_count: int,
        disc_number: int,
        total_discs: int,
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
            "disc_number": disc_number,
            "total_discs": total_discs,
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
