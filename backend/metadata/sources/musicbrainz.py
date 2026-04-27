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
    """MusicBrainz source.

    `mode` controls which lookup strategies run:
      - "both" (default): disc ID lookup, fall back to text search if no hits
      - "disc_id": only disc ID lookup (Phase 1 of two-phase resolution)
      - "text_search": only text search (Phase 2 — uses enriched hints)
    """

    def __init__(self, mode: str = "both") -> None:
        self.mode = mode

    @property
    def name(self) -> str:
        return "musicbrainz"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        candidates = []
        track_count = identity.track_count if identity else 0

        if self.mode in ("both", "disc_id") and identity and identity.disc_id:
            results = await self._lookup_discid(identity.disc_id, track_count)
            candidates.extend(results)

        if self.mode == "text_search" or (
            self.mode == "both" and not candidates and hints
        ):
            if hints:
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
        target_disc = hints.get("disc_number") or 1

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
                releases = data.get("releases", [])

                # Score releases first to pick top candidates worth fetching tracks for
                scored: list[tuple[int, dict, dict]] = []
                for r in releases:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")

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

                    scored.append((conf, r, evidence))

                scored.sort(key=lambda x: x[0], reverse=True)

                candidates = []
                # Fetch full track listings for top 3 — cheap MB lookups, big quality gain
                for conf, r, evidence in scored[:3]:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")

                    tracks, disc_number, total_discs = await self._fetch_tracks(
                        client, r.get("id", ""), target_disc, track_count
                    )
                    if tracks and track_count and len(tracks) == track_count:
                        conf += 5
                        evidence["disc_track_match"] = True

                    candidates.append({
                        "artist": r_artist,
                        "album": r.get("title", ""),
                        "year": (r.get("date") or "")[:4] or None,
                        "confidence": min(conf, 85),
                        "track_titles": json.dumps(tracks, ensure_ascii=False) if tracks else None,
                        "disc_number": disc_number,
                        "total_discs": total_discs,
                        "source_url": f"https://musicbrainz.org/release/{r.get('id', '')}",
                        "evidence": json.dumps(
                            {**evidence, "disc_number": disc_number, "total_discs": total_discs},
                            ensure_ascii=False,
                        ),
                    })

                # Append remaining lower-ranked releases without track fetches
                for conf, r, evidence in scored[3:]:
                    r_artist = ""
                    ac = r.get("artist-credit", [])
                    if ac and isinstance(ac[0], dict):
                        r_artist = ac[0].get("name", "")
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

    async def _fetch_tracks(
        self,
        client: httpx.AsyncClient,
        release_id: str,
        target_disc: int,
        track_count: int,
    ) -> tuple[list[str], int, int]:
        """Fetch track listing for a release; pick the medium that matches track_count."""
        if not release_id:
            return [], 1, 1
        await asyncio.sleep(RATE_LIMIT)
        try:
            resp = await client.get(
                f"{MB_BASE}/release/{release_id}",
                params={"fmt": "json", "inc": "recordings+media"},
            )
            if resp.status_code != 200:
                return [], 1, 1
            data = resp.json()
        except Exception:
            logger.exception("MB release detail fetch failed for %s", release_id)
            return [], 1, 1

        media = [m for m in data.get("media", []) if m.get("format") == "CD"]
        if not media:
            media = data.get("media", [])
        total_discs = len(media)
        if not media:
            return [], 1, 1

        # Pick medium: prefer track_count match, then target_disc, else first
        chosen = None
        if track_count:
            for m in media:
                if m.get("track-count") == track_count:
                    chosen = m
                    break
        if chosen is None:
            for m in media:
                if m.get("position") == target_disc:
                    chosen = m
                    break
        if chosen is None:
            chosen = media[0]

        tracks = []
        for t in chosen.get("tracks", []):
            rec = t.get("recording", {})
            tracks.append(rec.get("title", t.get("title", "")))

        return tracks, chosen.get("position", 1), total_discs
