"""MusicBrainz metadata source — disc ID lookup and text search."""

import asyncio
import json
import logging
from typing import Any

import httpx

from backend.metadata.normalize import similarity
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
            track_count = identity.track_count if identity else 0
            results = await self._lookup_discid(identity.disc_id, track_count)
            candidates.extend(results)

        # 2. Text search fallback
        if not candidates and hints:
            track_count = identity.track_count if identity else 0
            results = await self._text_search(hints, track_count)
            candidates.extend(results)

        return candidates

    async def _lookup_discid(self, disc_id: str, track_count: int) -> list[dict]:
        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/discid/{disc_id}",
                    params={"fmt": "json", "inc": "recordings+artist-credits"},
                )
                if resp.status_code == 404:
                    logger.debug("MB discid %s: not found", disc_id)
                    return []
                if resp.status_code != 200:
                    logger.warning("MB discid error: HTTP %d", resp.status_code)
                    return []

                data = resp.json()
                releases = data.get("releases", [])
                candidates = []

                for release in releases:
                    artist = ""
                    ac = release.get("artist-credit", [])
                    if ac:
                        artist = ac[0].get("name", "") if isinstance(ac[0], dict) else str(ac[0])

                    # Find which medium (disc) this disc ID matched
                    media = release.get("media", [])
                    total_discs = len(media)
                    disc_number = 1
                    tracks = []

                    for medium in media:
                        # Check if this medium contains our disc ID
                        medium_discids = [
                            d.get("id", "") for d in medium.get("discs", [])
                        ]
                        if disc_id in medium_discids or len(media) == 1:
                            disc_number = medium.get("position", 1)
                            for track in medium.get("tracks", []):
                                rec = track.get("recording", {})
                                tracks.append(rec.get("title", track.get("title", "")))
                            break
                    else:
                        # Fallback: use first medium
                        for track in media[0].get("tracks", []) if media else []:
                            rec = track.get("recording", {})
                            tracks.append(rec.get("title", track.get("title", "")))

                    candidates.append({
                        "artist": artist,
                        "album": release.get("title", ""),
                        "year": (release.get("date") or "")[:4] or None,
                        "genre": None,
                        "track_titles": json.dumps(
                            tracks[:track_count] if track_count else tracks,
                            ensure_ascii=False,
                        ),
                        "confidence": 90,
                        "disc_number": disc_number,
                        "total_discs": total_discs,
                        "source_url": f"https://musicbrainz.org/release/{release.get('id', '')}",
                        "evidence": json.dumps({
                            "match": "disc_id_exact",
                            "discid": disc_id,
                            "mb_release": release.get("id", ""),
                            "disc_number": disc_number,
                            "total_discs": total_discs,
                        }, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz disc ID lookup failed")
                return []

    async def _text_search(self, hints: dict, track_count: int = 0) -> list[dict]:
        query_parts = []
        catalog = hints.get("catalog", "")
        title = hints.get("title", "")
        artist = hints.get("artist", "")

        if catalog:
            query_parts.append(f'catno:"{catalog}"')
        if title:
            query_parts.append(f'release:"{title}"')
        if artist:
            query_parts.append(f'artist:"{artist}"')

        if not query_parts:
            return []

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
            try:
                resp = await client.get(
                    f"{MB_BASE}/release/",
                    params={
                        "query": " AND ".join(query_parts),
                        "fmt": "json",
                        "limit": 10,
                    },
                )
                if resp.status_code != 200:
                    return []

                data = resp.json()
                candidates = []

                for r in data.get("releases", []):
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")

                    # Confidence scoring matching original logic
                    conf = 40
                    evidence: dict[str, Any] = {
                        "match": "text_search",
                        "mb_release": r.get("id", ""),
                    }

                    if catalog and any(
                        li.get("catalog-number", "").upper() == catalog.upper()
                        for li in r.get("label-info", [])
                    ):
                        conf += 30
                        evidence["catno_match"] = catalog

                    if title and similarity(title, r.get("title", "")) >= 0.8:
                        conf += 15
                        evidence["title_match"] = True

                    if artist and similarity(artist, r_artist) >= 0.6:
                        conf += 10
                        evidence["artist_match"] = True

                    tc = sum(m.get("track-count", 0) for m in r.get("media", []))
                    if track_count and tc == track_count:
                        conf += 5
                        evidence["track_count_match"] = True

                    candidates.append({
                        "artist": r_artist,
                        "album": r.get("title", ""),
                        "year": (r.get("date") or "")[:4] or None,
                        "confidence": min(conf, 85),
                        "source_url": f"https://musicbrainz.org/release/{r.get('id', '')}",
                        "evidence": json.dumps(evidence, ensure_ascii=False),
                    })

                return candidates

            except Exception:
                logger.exception("MusicBrainz text search failed")
                return []
