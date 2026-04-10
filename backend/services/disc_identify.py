"""Disc identification: read disc ID and quick MusicBrainz lookup.

Extracted from routers/drives.py so it can be called from both the
API endpoint and the background disc poll.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DiscInfo:
    disc_id: str
    track_count: int
    artist: str | None
    album: str | None


async def identify(dev_path: str) -> DiscInfo:
    """Run cd-discid and query MusicBrainz + CDDB in parallel.

    Raises RuntimeError if cd-discid fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "cd-discid", dev_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"cd-discid failed: {stderr.decode().strip()}")

    raw = stdout.decode().strip()
    parts = raw.split()
    disc_id = parts[0].lower() if parts else "unknown"
    track_count = int(parts[1]) if len(parts) > 1 else 0
    offsets = [int(x) for x in parts[2:2 + track_count]] if len(parts) > 2 else []
    leadout_seconds = int(parts[2 + track_count]) if len(parts) > 2 + track_count else 0

    artist = None
    album = None

    if offsets and leadout_seconds:
        # Query MusicBrainz and CDDB in parallel
        mb_task = asyncio.create_task(_mb_toc_lookup(disc_id, track_count, offsets, leadout_seconds))
        cddb_task = asyncio.create_task(_cddb_lookup(disc_id, track_count, offsets, leadout_seconds))
        results = await asyncio.gather(mb_task, cddb_task, return_exceptions=True)

        # Pick first successful result (MusicBrainz preferred)
        for result in results:
            if isinstance(result, Exception):
                continue
            if result and result[0]:
                artist, album = result
                break

    return DiscInfo(disc_id=disc_id, track_count=track_count, artist=artist, album=album)


async def _mb_toc_lookup(
    disc_id: str, track_count: int, offsets: list[int], leadout_seconds: int
) -> tuple[str | None, str | None]:
    """Quick MusicBrainz TOC lookup. Returns (artist, album)."""
    leadout_sectors = leadout_seconds * 75
    toc = f"1 {track_count} {leadout_sectors} {' '.join(str(o) for o in offsets)}"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "RipTower/0.1.0"},
            timeout=10,
        ) as client:
            resp = await client.get(
                "https://musicbrainz.org/ws/2/discid/-",
                params={"toc": toc, "fmt": "json", "inc": "artist-credits"},
            )
            if resp.status_code == 200:
                data = resp.json()
                releases = data.get("releases", [])
                if releases:
                    rel = releases[0]
                    ac = rel.get("artist-credit", [])
                    artist = ac[0].get("name", "") if ac and isinstance(ac[0], dict) else None
                    album = rel.get("title")
                    return artist, album
    except Exception:
        logger.debug("MusicBrainz TOC lookup failed for disc %s", disc_id)
    return None, None


async def _cddb_lookup(
    disc_id: str, track_count: int, offsets: list[int], leadout_seconds: int
) -> tuple[str | None, str | None]:
    """Quick CDDB lookup via gnudb.org. Returns (artist, album)."""
    import urllib.parse

    base = "https://gnudb.gnudb.org/~cddb/cddb.cgi"
    hello = "kouki arigato-nas rip-tower 0.1"

    async def _req(cmd: str) -> str:
        params = {"cmd": cmd, "hello": hello, "proto": "6"}
        url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            raw_bytes = resp.content
        for enc in ("utf-8", "shift_jis", "euc_jp", "latin-1"):
            try:
                return raw_bytes.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
        return raw_bytes.decode("latin-1", "replace")

    try:
        query_resp = await _req(
            f"cddb query {disc_id} {track_count} "
            f"{' '.join(map(str, offsets))} {leadout_seconds}"
        )
        lines = [line.strip() for line in query_resp.splitlines() if line.strip()]
        if len(lines) < 2:
            return None, None

        query_parts = lines[1].split(" ", 2)
        if len(query_parts) < 2:
            return None, None

        cat, did = query_parts[:2]
        read_resp = await _req(f"cddb read {cat} {did}")

        for line in read_resp.splitlines():
            if line.startswith("DTITLE="):
                v = line.split("=", 1)[1]
                if " / " in v:
                    artist, album = v.split(" / ", 1)
                    return artist.strip(), album.strip()
                return None, v.strip()
    except Exception:
        logger.debug("CDDB lookup failed for disc %s", disc_id)
    return None, None
