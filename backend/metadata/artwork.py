"""Artwork fetching — query Cover Art Archive, iTunes, Discogs in parallel.

After metadata resolution (artist + album known), fetch artwork from
multiple sources and save as Artwork records. The best image is auto-selected
based on resolution and source priority.
"""

import asyncio
import json
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from backend.config import DATA_DIR, get_config
from backend.database import async_session
from backend.metadata.normalize import similarity
from backend.models import Artwork, Job, JobMetadata, MetadataCandidate

logger = logging.getLogger(__name__)

ARTWORK_DIR = DATA_DIR / "artworks"


async def fetch_artwork(job_id: str) -> None:
    """Fetch artwork from all available sources for a job.

    Runs Cover Art Archive, iTunes, and Discogs lookups in parallel.
    Saves images locally and creates Artwork records.
    """
    ARTWORK_DIR.mkdir(parents=True, exist_ok=True)

    # If this job belongs to an album_group, copy artwork from a sibling
    # that already has one (e.g. disc 1 resolved before disc 2).
    if await _copy_from_group_sibling(job_id):
        return

    # Load job metadata to know what we're looking for
    async with async_session() as session:
        meta = await session.get(JobMetadata, job_id)
        if not meta or not meta.artist:
            logger.debug("No metadata for job %s, skipping artwork fetch", job_id)
            return

    artist = meta.artist or ""
    album = meta.album or ""

    # Also check candidates for iTunes artwork URLs in evidence
    itunes_artwork_url = await _find_itunes_artwork_url(job_id)

    tasks = [
        _fetch_cover_art_archive(job_id, meta.source_url),
        _fetch_itunes_artwork(job_id, artist, album, itunes_artwork_url),
        _fetch_discogs_artwork(job_id, artist, album),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Artwork source %d failed: %s", i, result)

    # Auto-select the best artwork (highest resolution)
    await _auto_select_best(job_id)


async def _find_itunes_artwork_url(job_id: str) -> str | None:
    """Check iTunes candidates for pre-found artwork URLs."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == job_id)
            .where(MetadataCandidate.source == "itunes")
        )
        for candidate in result.scalars():
            if candidate.evidence:
                try:
                    ev = json.loads(candidate.evidence)
                    url = ev.get("artwork_url")
                    if url:
                        return url
                except (json.JSONDecodeError, TypeError):
                    pass
    return None


async def _copy_from_group_sibling(job_id: str) -> bool:
    """Copy artwork from an album_group sibling that already has one.

    For multi-disc albums, the first disc to resolve fetches artwork normally.
    Subsequent discs reuse the same artwork file instead of searching again.

    Returns True if artwork was copied (caller can skip external fetches).
    """
    from sqlalchemy import select

    async with async_session() as session:
        job = await session.get(Job, job_id)
        if not job or not job.album_group:
            return False

        # Find selected artwork from any sibling in the same group
        result = await session.execute(
            select(Artwork)
            .join(Job, Job.id == Artwork.job_id)
            .where(
                Job.album_group == job.album_group,
                Job.id != job_id,
                Artwork.selected.is_(True),
            )
            .limit(1)
        )
        sibling_art = result.scalars().first()
        if not sibling_art or not sibling_art.local_path:
            return False

        # Copy the image file
        src = Path(sibling_art.local_path)
        if not src.exists():
            return False

        ext = src.suffix
        filename = f"{job_id}_group{ext}"
        dest = ARTWORK_DIR / filename
        dest.write_bytes(src.read_bytes())

        artwork = Artwork(
            job_id=job_id,
            source=sibling_art.source,
            url=sibling_art.url,
            local_path=str(dest),
            width=sibling_art.width,
            height=sibling_art.height,
            file_size=sibling_art.file_size,
            selected=True,
        )
        session.add(artwork)
        await session.commit()

    logger.info(
        "Copied artwork for job %s from group sibling %s",
        job_id, sibling_art.job_id,
    )
    return True


async def _fetch_cover_art_archive(job_id: str, source_url: str | None) -> None:
    """Fetch from Cover Art Archive (MusicBrainz).

    source_url like: https://musicbrainz.org/release/UUID
    """
    if not source_url or "musicbrainz.org/release/" not in source_url:
        return

    release_id = source_url.rstrip("/").split("/")[-1]
    caa_url = f"https://coverartarchive.org/release/{release_id}/front"

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(caa_url)
            if resp.status_code != 200:
                logger.debug("Cover Art Archive: no artwork for release %s", release_id)
                return

            image_data = resp.content
        except Exception:
            logger.exception("Cover Art Archive fetch failed")
            return

    await _save_artwork(job_id, "cover_art_archive", caa_url, image_data)


async def _fetch_itunes_artwork(
    job_id: str, artist: str, album: str, known_url: str | None
) -> None:
    """Fetch artwork from iTunes Search API or a known URL."""
    url = known_url
    if not url:
        # Search iTunes for the artwork — fetch multiple results and pick the
        # one that actually matches the artist/album to avoid irrelevant covers.
        term = f"{artist} {album}"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get("https://itunes.apple.com/search", params={
                    "term": term,
                    "media": "music",
                    "entity": "album",
                    "limit": 10,
                    "country": "JP",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    for r in data.get("results", []):
                        r_artist = r.get("artistName", "")
                        r_album = r.get("collectionName", "")
                        if (similarity(artist, r_artist) >= 0.6
                                and similarity(album, r_album) >= 0.6):
                            url = (r.get("artworkUrl100") or "").replace(
                                "100x100", "600x600"
                            )
                            break
                    if not url:
                        logger.debug(
                            "iTunes artwork: no matching result for '%s' / '%s'",
                            artist, album,
                        )
            except Exception:
                logger.exception("iTunes artwork search failed")
                return

    if not url:
        return

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return
            image_data = resp.content
        except Exception:
            logger.exception("iTunes artwork download failed")
            return

    await _save_artwork(job_id, "itunes", url, image_data)


async def _fetch_discogs_artwork(job_id: str, artist: str, album: str) -> None:
    """Fetch artwork from Discogs API."""
    token = get_config().integrations.discogs_token
    if not token:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                "https://api.discogs.com/database/search",
                params={"type": "release", "artist": artist, "title": album, "format": "CD"},
                headers={
                    "User-Agent": "RipTower/0.1.0",
                    "Authorization": f"Discogs token={token}",
                },
            )
            if resp.status_code != 200:
                return

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return

            # Find the first result that actually matches artist/album
            cover_url = None
            for r in results:
                r_title = r.get("title", "")  # "Artist - Album" format
                parts = r_title.split(" - ", 1)
                r_artist = parts[0] if parts else ""
                r_album = parts[1] if len(parts) > 1 else ""
                if (similarity(artist, r_artist) >= 0.6
                        and similarity(album, r_album) >= 0.6):
                    cover_url = r.get("cover_image") or r.get("thumb")
                    break

            if not cover_url:
                logger.debug(
                    "Discogs artwork: no matching result for '%s' / '%s'",
                    artist, album,
                )
                return

        except Exception:
            logger.exception("Discogs artwork search failed")
            return

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(cover_url, headers={
                "User-Agent": "RipTower/0.1.0",
                "Authorization": f"Discogs token={token}",
            })
            if resp.status_code != 200:
                return
            image_data = resp.content
        except Exception:
            logger.exception("Discogs artwork download failed")
            return

    await _save_artwork(job_id, "discogs", cover_url, image_data)


async def _save_artwork(
    job_id: str, source: str, url: str, image_data: bytes
) -> None:
    """Save artwork image to disk and create an Artwork record."""
    try:
        img = Image.open(BytesIO(image_data))
        width, height = img.size
    except Exception:
        logger.warning("Could not parse artwork image from %s", source)
        return

    # Save to disk
    ext = "jpg" if img.format in ("JPEG", None) else img.format.lower()
    filename = f"{job_id}_{source}.{ext}"
    filepath = ARTWORK_DIR / filename
    filepath.write_bytes(image_data)

    file_size = len(image_data)

    async with async_session() as session:
        artwork = Artwork(
            job_id=job_id,
            source=source,
            url=url,
            local_path=str(filepath),
            width=width,
            height=height,
            file_size=file_size,
        )
        session.add(artwork)
        await session.commit()

    logger.info(
        "Saved artwork for job %s from %s: %dx%d (%d bytes)",
        job_id, source, width, height, file_size,
    )


async def _auto_select_best(job_id: str) -> None:
    """Auto-select the highest resolution artwork."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Artwork).where(Artwork.job_id == job_id)
        )
        artworks = list(result.scalars().all())

        if not artworks:
            return

        # Prefer highest resolution (width * height), with source priority as tiebreak
        source_priority = {"cover_art_archive": 3, "discogs": 2, "itunes": 1}
        artworks.sort(
            key=lambda a: (
                (a.width or 0) * (a.height or 0),
                source_priority.get(a.source, 0),
            ),
            reverse=True,
        )

        for a in artworks:
            a.selected = a.id == artworks[0].id

        await session.commit()

    logger.info("Auto-selected artwork for job %s: source=%s", job_id, artworks[0].source)
