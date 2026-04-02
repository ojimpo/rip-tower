"""Finalization — file placement, artwork embedding, Plex refresh.

Ported from ~/dev/openclaw-cd-rip/scripts/finalizer.py.
"""

import asyncio
import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Artwork, Job, JobMetadata, Track
from backend.services.encoder import safe_filename

logger = logging.getLogger(__name__)


async def finalize(job_id: str) -> None:
    """Move encoded files to the music library, embed artwork, refresh Plex."""
    config = get_config()

    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()
        tracks = await session.execute(
            select(Track)
            .where(Track.job_id == job_id, Track.encode_status == "ok")
            .order_by(Track.track_num)
        )
        tracks = tracks.scalars().all()

        artwork = await session.execute(
            select(Artwork).where(Artwork.job_id == job_id, Artwork.selected == True)
        )
        artwork = artwork.scalar_one_or_none()

    if not meta or not job:
        raise RuntimeError(f"No metadata for job {job_id}")

    # Build output directory from template
    artist = safe_filename(meta.artist or "Unknown Artist")
    album = safe_filename(meta.album or "Unknown Album")

    folder = config.output.folder_template.format(
        artist=artist,
        album=album,
        year=meta.year or "",
        genre=meta.genre or "",
        disc_num=meta.disc_number,
    )
    output_dir = Path(config.output.music_dir) / folder
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy artwork
    if artwork and artwork.local_path:
        cover_src = Path(artwork.local_path)
        if cover_src.exists():
            cover_dst = output_dir / "cover.jpg"
            shutil.copy2(cover_src, cover_dst)

    # Move encoded files
    ext = Path(tracks[0].encoded_path).suffix if tracks and tracks[0].encoded_path else ".flac"
    for track in tracks:
        if not track.encoded_path:
            continue

        src = Path(track.encoded_path)
        if not src.exists():
            continue

        filename = config.output.file_template.format(
            track_num=f"{track.track_num:02d}",
            artist=safe_filename(track.artist or meta.artist or "Unknown"),
            title=safe_filename(track.title or f"Track {track.track_num}"),
            album=album,
            year=meta.year or "",
            genre=meta.genre or "",
            disc_num=meta.disc_number,
            ext=ext.lstrip("."),
        )
        dst = output_dir / f"{filename}{ext}"

        shutil.move(str(src), str(dst))

        # Embed artwork in FLAC
        if artwork and artwork.local_path and ext == ".flac":
            await _embed_artwork(dst, Path(artwork.local_path))

        # Embed lyrics
        if track.lyrics_synced and ext == ".flac":
            await _embed_lyrics(dst, track.lyrics_synced, synced=True)
        elif track.lyrics_plain and ext == ".flac":
            await _embed_lyrics(dst, track.lyrics_plain, synced=False)

        # Update track path
        async with async_session() as session:
            t = await session.execute(
                select(Track).where(Track.job_id == job_id, Track.track_num == track.track_num)
            )
            t = t.scalar_one()
            t.encoded_path = str(dst)
            await session.commit()

    # Update job output_dir
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.output_dir = str(output_dir)
            await session.commit()

    # Clean up incoming directory
    incoming = Path(config.output.incoming_dir) / job_id
    if incoming.exists():
        shutil.rmtree(incoming)

    # Plex refresh
    await _plex_refresh()

    logger.info("Finalized job %s → %s", job_id, output_dir)


async def reapply_metadata(job_id: str) -> None:
    """Re-apply metadata tags to already-finalized files."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()
        tracks = await session.execute(
            select(Track).where(Track.job_id == job_id).order_by(Track.track_num)
        )
        tracks = tracks.scalars().all()

    if not meta or not job or not job.output_dir:
        logger.error("Cannot reapply: missing metadata or output_dir for job %s", job_id)
        return

    for track in tracks:
        if not track.encoded_path or not Path(track.encoded_path).exists():
            continue

        path = Path(track.encoded_path)
        if path.suffix == ".flac":
            # Clear existing tags and rewrite
            from backend.services.encoder import _tag_flac
            await _tag_flac(path, track, meta)

            # Re-embed artwork
            artwork_result = await session.execute(
                select(Artwork).where(Artwork.job_id == job_id, Artwork.selected == True)
            )
            artwork = artwork_result.scalar_one_or_none()
            if artwork and artwork.local_path:
                await _embed_artwork(path, Path(artwork.local_path))

    logger.info("Re-applied metadata for job %s", job_id)


async def _embed_artwork(flac_path: Path, artwork_path: Path) -> None:
    """Embed artwork into a FLAC file."""
    if not artwork_path.exists():
        return
    proc = await asyncio.create_subprocess_exec(
        "metaflac",
        "--import-picture-from",
        f"3||||{artwork_path}",
        str(flac_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _embed_lyrics(flac_path: Path, lyrics: str, synced: bool = False) -> None:
    """Embed lyrics into a FLAC file."""
    tag = "LYRICS" if synced else "UNSYNCEDLYRICS"
    proc = await asyncio.create_subprocess_exec(
        "metaflac",
        "--set-tag", f"{tag}={lyrics}",
        str(flac_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _plex_refresh() -> None:
    """Trigger Plex library refresh."""
    config = get_config()
    section_id = config.integrations.plex_section_id
    if not section_id:
        return

    proc = await asyncio.create_subprocess_exec(
        "curl", "-s", "-X", "GET",
        f"http://localhost:32400/library/sections/{section_id}/refresh",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    logger.info("Plex refresh triggered for section %s", section_id)
