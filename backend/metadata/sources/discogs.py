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
        target_disc = (hints or {}).get("disc_number") or 1
        track_count = identity.track_count if identity else 0

        # Score releases first; only fetch tracklists for top hits
        scored: list[tuple[int, dict, str, str]] = []
        for r in data.get("results", [])[:5]:
            parts = r.get("title", "").split(" - ", 1)
            r_artist = parts[0].strip() if len(parts) == 2 else ""
            r_album = parts[1].strip() if len(parts) == 2 else r.get("title", "")

            conf = 35
            if hint_catalog:
                for label_info in r.get("label", []):
                    if hint_catalog.upper() in label_info.get("catno", "").upper():
                        conf += 25
                        break
            if hint_title and similarity(hint_title, r_album) >= 0.8:
                conf += 10
            if hint_artist and similarity(hint_artist, r_artist) >= 0.6:
                conf += 5

            scored.append((conf, r, r_artist, r_album))

        scored.sort(key=lambda x: x[0], reverse=True)

        candidates = []
        # Fetch tracklist for top 3 hits (Discogs allows ~60 req/min for authed users)
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            for idx, (conf, r, r_artist, r_album) in enumerate(scored):
                tracks: list[str] = []
                disc_number = 1
                total_discs = 1
                if idx < 3:
                    tracks, disc_number, total_discs = await self._fetch_tracklist(
                        client, token, r.get("id"), target_disc, track_count
                    )
                    if tracks and track_count and len(tracks) == track_count:
                        conf += 5

                candidates.append({
                    "artist": r_artist,
                    "album": r_album,
                    "year": (r.get("year") or ""),
                    "confidence": min(conf, 75),
                    "track_titles": json.dumps(tracks, ensure_ascii=False) if tracks else None,
                    "disc_number": disc_number,
                    "total_discs": total_discs,
                    "source_url": r.get("resource_url", ""),
                    "evidence": json.dumps({
                        "discogs_id": r.get("id", ""),
                        "catno": r.get("catno", ""),
                        "match": "search",
                        "disc_number": disc_number,
                        "total_discs": total_discs,
                    }, ensure_ascii=False),
                })

        return candidates

    async def _fetch_tracklist(
        self,
        client: httpx.AsyncClient,
        token: str,
        release_id: Any,
        target_disc: int,
        track_count: int,
    ) -> tuple[list[str], int, int]:
        """Fetch full tracklist for a Discogs release.

        Discogs tracklists include heading/index entries; we filter to actual
        track positions. For multi-disc releases positions look like "1-1", "2-3"
        — pick tracks for target_disc.
        """
        if not release_id:
            return [], 1, 1

        await asyncio.sleep(RATE_LIMIT)
        try:
            resp = await client.get(
                f"{DISCOGS_BASE}/releases/{release_id}",
                headers={"Authorization": f"Discogs token={token}"},
            )
            if resp.status_code != 200:
                return [], 1, 1
            data = resp.json()
        except Exception:
            logger.exception("Discogs release fetch failed for %s", release_id)
            return [], 1, 1

        raw = data.get("tracklist", [])
        # Group by disc; only keep "track" type entries
        by_disc: dict[int, list[tuple[str, str]]] = {}
        for t in raw:
            if t.get("type_") != "track":
                continue
            pos = (t.get("position") or "").strip()
            if not pos:
                continue
            if "-" in pos or "." in pos:
                # Multi-disc format: "1-1", "2.3"
                sep = "-" if "-" in pos else "."
                disc_part, track_part = pos.split(sep, 1)
                try:
                    disc = int(disc_part)
                except ValueError:
                    disc = 1
                track_pos = track_part
            else:
                disc = 1
                track_pos = pos
            by_disc.setdefault(disc, []).append((track_pos, t.get("title", "")))

        if not by_disc:
            return [], 1, 1

        total_discs = len(by_disc)
        # Choose disc: track_count match → target_disc → first
        chosen_disc = None
        if track_count:
            for d, items in by_disc.items():
                if len(items) == track_count:
                    chosen_disc = d
                    break
        if chosen_disc is None and target_disc in by_disc:
            chosen_disc = target_disc
        if chosen_disc is None:
            chosen_disc = sorted(by_disc.keys())[0]

        tracks = [title for _, title in by_disc[chosen_disc]]
        return tracks, chosen_disc, total_discs
