"""Finalization — file placement, artwork embedding, artifact archiving, Plex refresh.

Ported from ~/dev/openclaw-cd-rip/scripts/finalizer.py.
"""

import asyncio
import logging
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Artwork, Job, JobMetadata, Track
from backend.services.encoder import safe_filename

logger = logging.getLogger(__name__)


def safe_dirname(s: str) -> str:
    """Sanitize string for use as directory name (matching original finalizer)."""
    return s.replace("/", "-").replace("\\", "-").replace(":", " -").replace("\0", "")


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

    # Build output directory — use safe_dirname for directory components
    artist_dir = safe_dirname(meta.artist or "Unknown Artist")
    album_base = safe_dirname(
        getattr(meta, "album_base", None) or meta.album or "Unknown Album"
    )

    # Add disc number suffix for multi-disc releases (matching original)
    if meta.disc_number is not None and meta.total_discs and meta.total_discs > 1:
        album_dir = f"{album_base} [DISC{meta.disc_number}]"
    else:
        album_dir = album_base

    output_dir = Path(config.output.music_dir) / artist_dir / album_dir
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

        # Build filename matching original encoder.py pattern:
        # "NN artist - title.ext"
        file_artist = safe_filename(track.artist or meta.artist or "Unknown")
        file_title = safe_filename(track.title or f"Track {track.track_num}")
        filename = f"{track.track_num:02d} {file_artist} - {file_title}{ext}"
        dst = output_dir / filename

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

    # Archive artifacts (.toc, .cue, .m3u, .log)
    _archive_artifacts(output_dir)

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


def _archive_artifacts(album_dir: Path) -> bool:
    """Archive .toc/.cue/.m3u/.log files into _ripmeta/ zip."""
    extensions = {".toc", ".cue", ".m3u", ".log"}
    artifacts = [f for f in album_dir.rglob("*") if f.suffix.lower() in extensions and f.is_file()]

    if not artifacts:
        return False

    ripmeta = album_dir / "_ripmeta"
    ripmeta.mkdir(exist_ok=True)

    # Group by parent directory
    by_parent: dict[Path, list[Path]] = {}
    for f in artifacts:
        by_parent.setdefault(f.parent, []).append(f)

    for parent, files in by_parent.items():
        label = "album_root" if parent == album_dir else parent.name

        zip_path = ripmeta / f"{label}_artifacts.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, f.name)
            # Delete originals only after successful zip
            for f in files:
                f.unlink()
            logger.info("Archived %d artifacts → %s", len(files), zip_path)
        except Exception as e:
            logger.warning("Archive error: %s", e)

    return True


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

    # Load artwork once
    async with async_session() as session:
        artwork_result = await session.execute(
            select(Artwork).where(Artwork.job_id == job_id, Artwork.selected == True)
        )
        artwork = artwork_result.scalar_one_or_none()

    for track in tracks:
        if not track.encoded_path or not Path(track.encoded_path).exists():
            continue

        path = Path(track.encoded_path)
        if path.suffix == ".flac":
            # Clear existing tags and rewrite
            from backend.services.encoder import _tag_flac
            await _tag_flac(path, track, meta)

            # Re-embed artwork
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
    """Trigger Plex library refresh (best-effort, with token auth).

    Extracts Plex token from the Plex container, then calls the refresh API.
    Matches original finalizer.py behavior.
    """
    config = get_config()
    section_id = config.integrations.plex_section_id
    if not section_id:
        return

    try:
        # Extract Plex token from container
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "plex", "sh", "-lc",
            'grep -o \'PlexOnlineToken="[^"]*"\' '
            '"/config/Library/Application Support/Plex Media Server/Preferences.xml" '
            "| head -n1 | cut -d'\"' -f2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        token = stdout.decode().strip()

        if token:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                f"http://127.0.0.1:32400/library/sections/{section_id}/refresh"
                f"?X-Plex-Token={token}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            logger.info("Plex refresh triggered for section %s", section_id)
        else:
            logger.debug("Could not extract Plex token, skipping refresh")
    except Exception:
        logger.debug("Plex refresh failed (non-critical)", exc_info=True)
