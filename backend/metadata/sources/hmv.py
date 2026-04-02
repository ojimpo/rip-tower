"""HMV.co.jp metadata source — scrapes catalog data for Japanese music.

Rate limit: 2 seconds between requests.
Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_hmv_search, _hmv_detail).
"""

import asyncio
import json
import logging
import re
import urllib.parse
from typing import Any

import httpx

from backend.metadata.normalize import norm, similarity
from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

RATE_LIMIT = 2.0  # 2 seconds between requests

_HMV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept-Language": "ja,en;q=0.5",
}


class HmvSource(MetadataSource):
    @property
    def name(self) -> str:
        return "hmv"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        if not hints:
            return []

        catalog = hints.get("catalog", "")
        jan = hints.get("jan", "")
        title = hints.get("title", "")
        artist = hints.get("artist", "")
        track_count = identity.track_count if identity else 0

        # Choose search keyword
        keyword = ""
        if catalog:
            keyword = re.sub(r"[-\s]", "", catalog)
        elif jan:
            keyword = jan
        elif title:
            keyword = title
        else:
            return []

        search_url = (
            f"https://www.hmv.co.jp/search/keyword_"
            f"{urllib.parse.quote(keyword)}/target_MUSIC/type_sr"
        )

        await asyncio.sleep(RATE_LIMIT)
        async with httpx.AsyncClient(headers=_HMV_HEADERS, timeout=15) as client:
            try:
                resp = await client.get(search_url)
                if resp.status_code != 200:
                    return []
                body = resp.text
            except Exception:
                logger.exception("HMV search failed for keyword=%s", keyword)
                return []

        # Extract product SKUs from search results
        sku_matches = re.findall(r"/item_[^\"]*?_(\d{5,8})", body)
        if not sku_matches:
            logger.debug("HMV: no results for %r", keyword)
            return []

        # Deduplicate
        seen: set[str] = set()
        skus: list[str] = []
        for s in sku_matches:
            if s not in seen:
                seen.add(s)
                skus.append(s)

        logger.info("HMV search: %d products found for %r", len(skus), keyword)

        # Fetch detail pages for top 2 results
        candidates = []
        for sku in skus[:2]:
            await asyncio.sleep(RATE_LIMIT)
            detail = await self._fetch_detail(
                client=None,  # create new client per request
                sku=sku,
                hint_catalog=catalog,
                hint_jan=jan,
                hint_title=title,
                hint_artist=artist,
                track_count=track_count,
            )
            if detail:
                candidates.append(detail)

        return candidates

    async def _fetch_detail(
        self,
        client: httpx.AsyncClient | None,
        sku: str,
        hint_catalog: str,
        hint_jan: str,
        hint_title: str,
        hint_artist: str,
        track_count: int,
    ) -> dict | None:
        """Scrape an HMV product detail page and extract metadata."""
        detail_url = f"https://www.hmv.co.jp/product/detail/{sku}"

        async with httpx.AsyncClient(headers=_HMV_HEADERS, timeout=15) as c:
            try:
                resp = await c.get(detail_url)
                if resp.status_code != 200:
                    return None
                body = resp.text
            except Exception:
                logger.exception("HMV detail error for SKU %s", sku)
                return None

        # Parse album title
        album = ""
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', body)
        if m:
            album = m.group(1).strip()
        if not album:
            m = re.search(r"<h1[^>]*>([^<]+)</h1>", body)
            if m:
                album = m.group(1).strip()

        # Parse catalog number
        found_catalog = ""
        m = re.search(r"カタログNo\s*[：:]\s*([A-Za-z0-9][-A-Za-z0-9]*)", body)
        if m:
            found_catalog = m.group(1).strip()

        # Parse artist
        found_artist = ""
        m = re.search(r"/artist_([^/]+?)_\d+", body)
        if m:
            found_artist = (
                urllib.parse.unquote(m.group(1)).replace("+", " ").replace("_", " ")
            )
        if not found_artist or found_artist.lower() in (
            "soundtrack", "various", "various artists",
        ):
            m = re.search(r"アーティスト\s*[：:]\s*([^\s<]+)", body)
            if m:
                found_artist = m.group(1).strip()

        # Parse track listing
        track_titles: list[str] = []
        for tm in re.finditer(r"(\d{1,2})\s*[·.．]\s*\[?([^\]\n<]{2,})\]?", body):
            track_titles.append(tm.group(2).strip())
        if not track_titles:
            for tm in re.finditer(r"<li[^>]*>\s*(\d{1,2})\s*[.．·]\s*([^<]+)", body):
                track_titles.append(tm.group(2).strip())

        if not album:
            logger.debug("HMV: could not parse album from SKU %s", sku)
            return None

        # Calculate confidence
        conf = 40
        evidence: dict[str, Any] = {"hmv_sku": sku}

        if hint_catalog and found_catalog:
            if norm(hint_catalog) == norm(found_catalog):
                conf += 30
                evidence["catalog_match"] = found_catalog
            else:
                evidence["catalog_found"] = found_catalog

        if hint_title and similarity(hint_title, album) >= 0.8:
            conf += 10
            evidence["title_match"] = True
        if hint_artist and found_artist and similarity(hint_artist, found_artist) >= 0.6:
            conf += 5
            evidence["artist_match"] = True

        if track_titles:
            conf += 5
            evidence["tracks_found"] = len(track_titles)
            if track_count and len(track_titles) == track_count:
                conf += 5
                evidence["track_count_match"] = True

        return {
            "artist": found_artist,
            "album": album,
            "track_titles": json.dumps(
                track_titles[:track_count] if track_count else track_titles,
                ensure_ascii=False,
            ),
            "confidence": min(conf, 85),
            "source_url": detail_url,
            "evidence": json.dumps(evidence, ensure_ascii=False),
        }
