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


# Borrowed-CD cross-check threshold. Same value as resolver's
# `_KASHIDASHI_SIM_THRESHOLD` — using a different threshold here would let
# disc_identify trust a name the resolver would reject (or vice versa) for
# the same TOC. Keep them aligned.
_BORROWED_MATCH_THRESHOLD = 0.6


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
    release_id = None

    if offsets and leadout_seconds:
        # Query MusicBrainz and CDDB in parallel
        mb_task = asyncio.create_task(_mb_toc_lookup(disc_id, track_count, offsets, leadout_seconds))
        cddb_task = asyncio.create_task(_cddb_lookup(disc_id, track_count, offsets, leadout_seconds))
        mb_result, cddb_result = await asyncio.gather(
            mb_task, cddb_task, return_exceptions=True
        )

        # MusicBrainz preferred; only it yields a release id (for alias matching).
        if not isinstance(mb_result, Exception) and mb_result and mb_result[0]:
            artist, album, release_id = mb_result
        elif not isinstance(cddb_result, Exception) and cddb_result and cddb_result[0]:
            artist, album = cddb_result

    artist, album = await _reconcile_with_borrowed(artist, album, release_id)

    return DiscInfo(disc_id=disc_id, track_count=track_count, artist=artist, album=album)


async def _reconcile_with_borrowed(
    artist: str | None, album: str | None, release_id: str | None = None
) -> tuple[str | None, str | None]:
    """Override misleading MB/CDDB output when the user is borrowing CDs.

    MB's TOC lookup occasionally returns a release that shares the disc's
    TOC but is a completely different album (e.g. a Cocco best-of disc
    matches a Harry Potter audiobook TOC). When that happens, the cached
    artist/album shown on the drives dashboard actively misleads the user.

    Policy, mirroring resolver._boost_kashidashi_matches:
      - MB/CDDB hit AND it fuzzy-matches a borrowed CD → trust MB (no-op).
      - MB/CDDB hit but no borrowed CD matches AND exactly one CD is
        borrowed-not-yet-ripped → adopt that CD's name (the borrowed list
        is stronger evidence than a TOC-collided MB release).
      - MB/CDDB hit but no borrowed CD matches AND multiple CDs are
        borrowed → can't disambiguate from disc-ID alone; suppress the
        misleading name (review will surface candidates anyway).
      - No MB/CDDB hit and exactly one CD is borrowed → adopt that one.
      - No MB/CDDB hit and multiple CDs are borrowed → leave empty.

    Returns (artist, album), either unchanged or replaced.
    """
    from types import SimpleNamespace

    from backend.metadata.sources.kashidashi import (
        best_kashidashi_match,
        fetch_active_borrowed_items,
    )

    try:
        borrowed = await fetch_active_borrowed_items()
    except Exception:
        logger.debug("Borrowed-CD fetch failed; keeping raw lookup result")
        return artist, album
    if not borrowed:
        return artist, album

    if artist or album:
        # Reuse the resolver's script-insensitive matcher. With a release id it
        # bridges scripts via MusicBrainz aliases (so an English MB result still
        # matches a katakana borrowed record); without one it degrades to a
        # plain text match. A `toc_submission` shim marks the album as
        # disc-proven, so a confirmed artist alias alone is enough.
        shim = SimpleNamespace(
            artist=artist or "",
            album=album or "",
            source_url=(
                f"https://musicbrainz.org/release/{release_id}" if release_id else None
            ),
            evidence='{"match": "toc_submission"}' if release_id else None,
        )
        item, art_sim, alb_sim = await best_kashidashi_match(shim, borrowed)
        if item is not None and (
            art_sim >= _BORROWED_MATCH_THRESHOLD
            or alb_sim >= _BORROWED_MATCH_THRESHOLD
        ):
            return artist, album

    if len(borrowed) == 1:
        only = borrowed[0]
        new_artist = only.get("metadata_artist") or only.get("artist") or None
        new_album = only.get("metadata_album") or only.get("title") or None
        if new_artist or new_album:
            logger.info(
                "Drive identify: replacing %r/%r with sole borrowed CD %r/%r",
                artist, album, new_artist, new_album,
            )
            return new_artist, new_album

    if artist or album:
        logger.info(
            "Drive identify: suppressing %r/%r — does not match any of %d borrowed CDs",
            artist, album, len(borrowed),
        )
    return None, None


async def _mb_toc_lookup(
    disc_id: str, track_count: int, offsets: list[int], leadout_seconds: int
) -> tuple[str | None, str | None, str | None]:
    """Quick MusicBrainz TOC lookup. Returns (artist, album, release_id).

    The release id lets the borrowed-CD reconciliation match across scripts via
    MusicBrainz aliases (a Japanese borrowed record vs an English MB result).
    """
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
                    return artist, album, rel.get("id")
    except Exception:
        logger.debug("MusicBrainz TOC lookup failed for disc %s", disc_id)
    return None, None, None


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
