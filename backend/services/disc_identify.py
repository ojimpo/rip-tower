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
    """Run cd-discid and do a quick MusicBrainz TOC lookup.

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

    # Quick MusicBrainz lookup via TOC
    artist = None
    album = None
    if offsets and leadout_seconds:
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
        except Exception:
            logger.debug("MusicBrainz lookup failed for %s", dev_path)

    return DiscInfo(disc_id=disc_id, track_count=track_count, artist=artist, album=album)
