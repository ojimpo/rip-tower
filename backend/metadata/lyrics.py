"""Lyrics fetching — query LRCLIB (and optionally Musixmatch) for synced/plain lyrics.

For each track in a job, looks up lyrics from available sources and saves
them to Track records (lyrics_plain, lyrics_synced, lyrics_source).

LRCLIB (lrclib.net) is the primary source — free, no auth needed, supports
synced (LRC format) and plain lyrics.
"""

import asyncio
import logging

import httpx
from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import JobMetadata, Track

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
LRCLIB_RATE_LIMIT = 0.5  # be gentle with the free API

MUSIXMATCH_BASE = "https://api.musixmatch.com/ws/1.1"
MUSIXMATCH_RATE_LIMIT = 1.0


async def fetch_lyrics(job_id: str) -> None:
    """Fetch lyrics for all tracks in a job.

    Queries LRCLIB first (free, synced lyrics). Falls back to Musixmatch
    if configured and LRCLIB has no results.
    """
    # Load metadata and tracks
    async with async_session() as session:
        meta = await session.get(JobMetadata, job_id)
        if not meta or not meta.artist:
            logger.debug("No metadata for job %s, skipping lyrics fetch", job_id)
            return

        result = await session.execute(
            select(Track)
            .where(Track.job_id == job_id)
            .order_by(Track.track_num)
        )
        tracks = list(result.scalars().all())

    if not tracks:
        return

    artist = meta.artist or ""
    album = meta.album or ""

    musixmatch_token = get_config().integrations.musixmatch_token

    for track in tracks:
        title = track.title or ""
        if not title:
            continue

        # Try LRCLIB first
        synced, plain = await _fetch_lrclib(artist, title, album, track.duration_ms)
        source = "lrclib" if (synced or plain) else None

        # Fallback to Musixmatch
        if not synced and not plain and musixmatch_token:
            synced, plain = await _fetch_musixmatch(
                artist, title, album, musixmatch_token
            )
            source = "musixmatch" if (synced or plain) else None

        if synced or plain:
            async with async_session() as session:
                db_track = await session.get(Track, track.id)
                if db_track:
                    db_track.lyrics_synced = synced
                    db_track.lyrics_plain = plain
                    db_track.lyrics_source = source
                    await session.commit()

            logger.debug(
                "Lyrics found for track %d (%s): source=%s synced=%s",
                track.track_num, title, source, bool(synced),
            )


async def _fetch_lrclib(
    artist: str, title: str, album: str, duration_ms: int | None
) -> tuple[str | None, str | None]:
    """Query LRCLIB for synced and plain lyrics.

    Returns (synced_lyrics, plain_lyrics). Either can be None.
    """
    await asyncio.sleep(LRCLIB_RATE_LIMIT)

    params: dict[str, str | int] = {
        "artist_name": artist,
        "track_name": title,
    }
    if album:
        params["album_name"] = album
    if duration_ms:
        params["duration"] = duration_ms // 1000

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{LRCLIB_BASE}/get", params=params)
            if resp.status_code != 200:
                # Try search endpoint as fallback
                return await _lrclib_search(client, artist, title)

            data = resp.json()
            synced = data.get("syncedLyrics") or None
            plain = data.get("plainLyrics") or None
            return synced, plain

        except Exception:
            logger.debug("LRCLIB fetch failed for %s - %s", artist, title)
            return None, None


async def _lrclib_search(
    client: httpx.AsyncClient, artist: str, title: str
) -> tuple[str | None, str | None]:
    """Fallback: search LRCLIB when exact match fails."""
    try:
        resp = await client.get(f"{LRCLIB_BASE}/search", params={
            "q": f"{artist} {title}",
        })
        if resp.status_code != 200:
            return None, None

        results = resp.json()
        if not results:
            return None, None

        # Take first result
        best = results[0]
        synced = best.get("syncedLyrics") or None
        plain = best.get("plainLyrics") or None
        return synced, plain

    except Exception:
        return None, None


async def _fetch_musixmatch(
    artist: str, title: str, album: str, token: str
) -> tuple[str | None, str | None]:
    """Query Musixmatch API for lyrics.

    Returns (synced_lyrics, plain_lyrics). Musixmatch free tier only
    returns 30% of lyrics — synced lyrics require commercial license.
    """
    await asyncio.sleep(MUSIXMATCH_RATE_LIMIT)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # Step 1: Search for the track
            resp = await client.get(f"{MUSIXMATCH_BASE}/track.search", params={
                "q_artist": artist,
                "q_track": title,
                "f_has_lyrics": 1,
                "s_track_rating": "desc",
                "apikey": token,
            })
            if resp.status_code != 200:
                return None, None

            data = resp.json()
            track_list = (
                data.get("message", {})
                .get("body", {})
                .get("track_list", [])
            )
            if not track_list:
                return None, None

            track_id = track_list[0].get("track", {}).get("track_id")
            if not track_id:
                return None, None

            # Step 2: Get lyrics
            await asyncio.sleep(MUSIXMATCH_RATE_LIMIT)
            resp = await client.get(f"{MUSIXMATCH_BASE}/track.lyrics.get", params={
                "track_id": track_id,
                "apikey": token,
            })
            if resp.status_code != 200:
                return None, None

            data = resp.json()
            lyrics_body = (
                data.get("message", {})
                .get("body", {})
                .get("lyrics", {})
                .get("lyrics_body", "")
            )

            # Musixmatch free tier truncates lyrics, note this
            if lyrics_body:
                return None, lyrics_body  # No synced lyrics from free API
            return None, None

        except Exception:
            logger.debug("Musixmatch fetch failed for %s - %s", artist, title)
            return None, None
