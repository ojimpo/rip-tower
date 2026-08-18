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
    if await copy_from_group_sibling(job_id):
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
    itunes_artwork_url = await _find_itunes_artwork_url(job_id, artist, album)

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


async def invalidate_auto_artwork(job_id: str) -> int:
    """Delete a job's auto-fetched (non-manual) artwork rows and files.

    Manual uploads are preserved. Returns the number of rows removed. Used
    when artist/album is edited so artwork fetched for a mis-identified album
    (the Hard-Disk → 大発見 / 教育 mixups) doesn't linger after the fix.
    """
    from sqlalchemy import select

    removed = 0
    async with async_session() as session:
        result = await session.execute(
            select(Artwork).where(
                Artwork.job_id == job_id,
                Artwork.source != "manual",
            )
        )
        for art in result.scalars():
            if art.local_path:
                try:
                    Path(art.local_path).unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Failed to remove stale artwork file %s", art.local_path
                    )
            await session.delete(art)
            removed += 1
        await session.commit()
    if removed:
        logger.info("Invalidated %d auto-fetched artwork(s) for job %s", removed, job_id)
    return removed


async def refresh_artwork_for_edit(job_id: str) -> None:
    """Re-fetch artwork after artist/album was manually corrected.

    Drops the job's stale auto-fetched artwork — and, for a multi-disc album,
    the rest of the group's, so `copy_from_group_sibling` can't re-seed the old
    image — fetches fresh artwork for the edited disc, then propagates the new
    selection to the siblings. Manual uploads are never touched.
    """
    from sqlalchemy import select

    async with async_session() as session:
        job = await session.get(Job, job_id)
        group = job.album_group if job else None
        sibling_ids: list[str] = []
        if group:
            result = await session.execute(
                select(Job.id).where(Job.album_group == group, Job.id != job_id)
            )
            sibling_ids = [r[0] for r in result.all()]

    await invalidate_auto_artwork(job_id)
    for sid in sibling_ids:
        await invalidate_auto_artwork(sid)

    # Fetch fresh artwork for the edited disc. With every group member's auto
    # artwork cleared, copy_from_group_sibling won't short-circuit on a stale
    # sibling, so this performs a real external lookup for the corrected album.
    await fetch_artwork(job_id)

    # Push the freshly-selected image out to siblings that now have none.
    for sid in sibling_ids:
        await copy_from_group_sibling(sid)


async def _find_itunes_artwork_url(
    job_id: str, artist: str, album: str
) -> str | None:
    """Check iTunes candidates for pre-found artwork URLs.

    Only a candidate that actually matches the resolved artist/album may
    donate its artwork URL — the first iTunes hit is often a different album
    (the resolver keeps low-confidence candidates around for review), and
    blindly reusing its URL attached the wrong cover to the release.
    """
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == job_id)
            .where(MetadataCandidate.source == "itunes")
        )
        for candidate in result.scalars():
            if not candidate.evidence:
                continue
            if (similarity(artist, candidate.artist or "") < 0.85
                    or similarity(album, candidate.album or "") < 0.85):
                continue
            try:
                ev = json.loads(candidate.evidence)
            except (json.JSONDecodeError, TypeError):
                continue
            url = ev.get("artwork_url")
            if url:
                return url
    return None


async def copy_from_group_sibling(job_id: str) -> bool:
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

        # Skip if this job already has a selected artwork
        existing = await session.execute(
            select(Artwork).where(
                Artwork.job_id == job_id, Artwork.selected.is_(True)
            ).limit(1)
        )
        if existing.scalars().first():
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
                        if (similarity(artist, r_artist) >= 0.85
                                and similarity(album, r_album) >= 0.85):
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
                if (similarity(artist, r_artist) >= 0.85
                        and similarity(album, r_album) >= 0.85):
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
    """Save artwork image to disk and upsert the Artwork record.

    Auto-fetched sources keep one row per (job_id, source): re-resolves and
    post-edit refreshes update it in place instead of piling up duplicate
    rows for the same image. Manual uploads don't go through here.
    """
    from sqlalchemy import select

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
        result = await session.execute(
            select(Artwork).where(
                Artwork.job_id == job_id, Artwork.source == source
            )
        )
        rows = list(result.scalars().all())
        if rows:
            artwork, *extra = rows
            # Same source refetched with a different format leaves the old
            # file behind — remove it before repointing the row.
            if artwork.local_path and artwork.local_path != str(filepath):
                Path(artwork.local_path).unlink(missing_ok=True)
            artwork.url = url
            artwork.local_path = str(filepath)
            artwork.width = width
            artwork.height = height
            artwork.file_size = file_size
            # Clean up duplicates accumulated before upserting existed.
            for dup in extra:
                if dup.local_path and dup.local_path != str(filepath):
                    Path(dup.local_path).unlink(missing_ok=True)
                await session.delete(dup)
        else:
            session.add(Artwork(
                job_id=job_id,
                source=source,
                url=url,
                local_path=str(filepath),
                width=width,
                height=height,
                file_size=file_size,
            ))
        await session.commit()

    logger.info(
        "Saved artwork for job %s from %s: %dx%d (%d bytes)",
        job_id, source, width, height, file_size,
    )


def _is_squarish(art: Artwork) -> bool:
    """Whether the image has the roughly 1:1 shape of a CD jacket.

    Discogs search results sometimes return a banner or spine scan (a 599×362
    image got auto-selected over a square 1200×1200 cover — Todoist
    6hHpxR4qgw53mMJF). Unknown dimensions count as non-square so an
    unverifiable image can't outrank a verified square one.
    """
    if not art.width or not art.height:
        return False
    return 0.8 <= art.width / art.height <= 1.25


async def _auto_select_best(job_id: str) -> None:
    """Auto-select the most trustworthy artwork.

    Order: manual upload > square shape > Cover Art Archive > Discogs >
    iTunes, with image resolution as a tiebreak. CAA only fires when
    MusicBrainz matched the disc by ID, so it is the most reliable
    text-search-free source. A manual upload always wins regardless of shape —
    the user chose it deliberately.
    """
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Artwork).where(Artwork.job_id == job_id)
        )
        artworks = list(result.scalars().all())

        if not artworks:
            return

        source_priority = {
            "manual": 4,
            "cover_art_archive": 3,
            "discogs": 2,
            "itunes": 1,
        }
        artworks.sort(
            key=lambda a: (
                a.source == "manual",
                _is_squarish(a),
                source_priority.get(a.source, 0),
                (a.width or 0) * (a.height or 0),
            ),
            reverse=True,
        )

        for a in artworks:
            a.selected = a.id == artworks[0].id

        await session.commit()

    logger.info("Auto-selected artwork for job %s: source=%s", job_id, artworks[0].source)
