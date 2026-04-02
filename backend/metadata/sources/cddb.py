"""CDDB metadata source — queries gnudb.org for disc ID lookup.

Ported from ~/dev/openclaw-cd-rip/scripts/metadata_resolver.py (_cddb_lookup).
"""

import json
import logging
import urllib.parse
from typing import Any

import httpx

from backend.metadata.sources.base import MetadataSource

logger = logging.getLogger(__name__)

CDDB_BASE = "https://gnudb.gnudb.org/~cddb/cddb.cgi"
CDDB_HELLO = "kouki arigato-nas rip-tower 0.1"


class CddbSource(MetadataSource):
    @property
    def name(self) -> str:
        return "cddb"

    async def search(self, identity: Any, hints: dict | None = None) -> list[dict]:
        """Query CDDB via gnudb.org using disc ID and TOC."""
        if not identity or not identity.disc_id:
            return []
        if not identity.toc or not identity.total_seconds:
            return []

        disc_id = identity.disc_id
        track_count = identity.track_count
        offsets = identity.toc[:track_count]  # Track offsets only
        duration_secs = identity.total_seconds

        # Step 1: CDDB query — find matching category/disc ID
        try:
            query_resp = await self._cddb_request(
                f"cddb query {disc_id} {track_count} "
                f"{' '.join(map(str, offsets))} {duration_secs}"
            )
        except Exception:
            logger.exception("CDDB query failed for disc_id=%s", disc_id)
            return []

        lines = [line.strip() for line in query_resp.splitlines() if line.strip()]
        if len(lines) < 2:
            logger.debug("CDDB: no matches for disc_id=%s", disc_id)
            return []

        query_parts = lines[1].split(" ", 2)
        if len(query_parts) < 2:
            logger.debug("CDDB: malformed response: %s", lines[1])
            return []

        cat, did = query_parts[:2]

        # Step 2: CDDB read — fetch full record
        try:
            read_resp = await self._cddb_request(f"cddb read {cat} {did}")
        except Exception:
            logger.exception("CDDB read failed for %s/%s", cat, did)
            return []

        # Parse the CDDB record
        artist = ""
        album = ""
        year = ""
        genre = ""
        titles: list[tuple[int, str]] = []

        for line in read_resp.splitlines():
            if line.startswith("DTITLE="):
                v = line.split("=", 1)[1]
                if " / " in v:
                    artist, album = v.split(" / ", 1)
                else:
                    album = v
            elif line.startswith("DYEAR="):
                year = line.split("=", 1)[1]
            elif line.startswith("DGENRE="):
                genre = line.split("=", 1)[1]
            elif line.startswith("TTITLE"):
                k, v = line.split("=", 1)
                try:
                    titles.append((int(k.replace("TTITLE", "")), v))
                except ValueError:
                    pass

        track_titles = [v for _, v in sorted(titles)][:track_count]

        conf = 60  # CDDB is decent but not as reliable as MB disc ID
        evidence = {
            "cddb_cat": cat,
            "cddb_discid": did,
        }
        if year:
            evidence["year"] = year
        if genre:
            evidence["genre"] = genre

        return [{
            "artist": artist.strip(),
            "album": album.strip(),
            "year": year or None,
            "genre": genre or None,
            "track_titles": json.dumps(track_titles, ensure_ascii=False),
            "confidence": conf,
            "source_url": "",
            "evidence": json.dumps(evidence, ensure_ascii=False),
        }]

    async def _cddb_request(self, cmd: str) -> str:
        """Send a CDDB protocol request to gnudb.org."""
        params = {
            "cmd": cmd,
            "hello": CDDB_HELLO,
            "proto": "6",
        }
        url = CDDB_BASE + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
            raw = resp.content

        # Try multiple encodings — CDDB records often use Shift_JIS or EUC-JP
        for enc in ("utf-8", "shift_jis", "euc_jp", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
        return raw.decode("latin-1", "replace")
