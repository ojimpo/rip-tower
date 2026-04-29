"""Finalization — file placement, artwork embedding, artifact archiving, Plex refresh.

Ported from ~/dev/openclaw-cd-rip/scripts/finalizer.py.
"""

import asyncio
import logging
import shutil
import zipfile
from pathlib import Path

import httpx
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

    # Check for existing audio files that would conflict
    existing = _find_existing_audio(output_dir)
    if existing:
        logger.info(
            "Existing audio files found in %s: %d files, sending to review",
            output_dir, len(existing),
        )
        async with async_session() as session:
            j = await session.get(Job, job_id)
            m = await session.execute(
                select(JobMetadata).where(JobMetadata.job_id == job_id)
            )
            m = m.scalar_one_or_none()
            if j and m:
                # Add existing_files issue and revert to review
                import json as _json
                issues = _json.loads(m.issues) if m.issues else []
                if "existing_files" not in issues:
                    issues.append("existing_files")
                    m.issues = _json.dumps(issues, ensure_ascii=False)
                m.needs_review = True
                j.status = "review"
                await session.commit()

            from backend.services.websocket import broadcast as ws_broadcast
            await ws_broadcast("job:review", {
                "job_id": job_id,
                "reason": f"existing audio files in {output_dir}",
                "existing_files": [str(f) for f in existing],
            })
        return

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

        # Re-tag FLAC at finalize time. encode-time tagging is skipped when
        # JobMetadata is empty (early in the pipeline), so this is the last
        # chance to ensure tags reflect the resolved metadata before the file
        # leaves incoming.
        if ext == ".flac":
            from backend.services.encoder import _tag_flac
            await _tag_flac(dst, track, meta)

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

    # Determine new output directory based on current metadata
    config = get_config()
    artist_dir = safe_dirname(meta.artist or "Unknown Artist")
    album_base = safe_dirname(
        getattr(meta, "album_base", None) or meta.album or "Unknown Album"
    )
    if meta.disc_number is not None and meta.total_discs and meta.total_discs > 1:
        album_dir = f"{album_base} [DISC{meta.disc_number}]"
    else:
        album_dir = album_base

    new_output_dir = Path(config.output.music_dir) / artist_dir / album_dir
    old_output_dir = Path(job.output_dir)

    # Rename folder if metadata changed the path
    if new_output_dir != old_output_dir and old_output_dir.exists():
        new_output_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_output_dir), str(new_output_dir))
        logger.info("Renamed folder: %s → %s", old_output_dir, new_output_dir)

        # Clean up empty parent directories
        try:
            old_output_dir.parent.rmdir()
        except OSError:
            pass  # Not empty, that's fine

    target_dir = new_output_dir if new_output_dir.exists() else old_output_dir

    # Re-tag files and rename them based on new metadata
    ext = Path(tracks[0].encoded_path).suffix if tracks and tracks[0].encoded_path else ".flac"

    for track in tracks:
        if not track.encoded_path or not Path(track.encoded_path).exists():
            # File might have moved with the folder
            old_path = Path(track.encoded_path)
            new_possible = target_dir / old_path.name
            if new_possible.exists():
                path = new_possible
            else:
                continue
        else:
            path = Path(track.encoded_path)

        # Re-tag
        if path.suffix == ".flac":
            from backend.services.encoder import _tag_flac
            await _tag_flac(path, track, meta)

            if artwork and artwork.local_path:
                await _embed_artwork(path, Path(artwork.local_path))

        # Rename file if needed
        file_artist = safe_filename(track.artist or meta.artist or "Unknown")
        file_title = safe_filename(track.title or f"Track {track.track_num}")
        new_filename = f"{track.track_num:02d} {file_artist} - {file_title}{ext}"
        new_path = target_dir / new_filename

        if path != new_path and path.exists():
            shutil.move(str(path), str(new_path))
            path = new_path

        # Update track encoded_path in DB
        async with async_session() as session:
            t = await session.execute(
                select(Track).where(Track.job_id == job_id, Track.track_num == track.track_num)
            )
            t = t.scalar_one()
            t.encoded_path = str(new_path)
            await session.commit()

    # Update job output_dir
    async with async_session() as session:
        j = await session.get(Job, job_id)
        if j:
            j.output_dir = str(target_dir)
            await session.commit()

    # Refresh Plex so post-complete edits propagate to the library view.
    await _plex_refresh()

    logger.info("Re-applied metadata for job %s → %s", job_id, target_dir)


AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".ogg", ".opus", ".wma", ".alac", ".wav"}


def _find_existing_audio(output_dir: Path) -> list[Path]:
    """Find non-FLAC audio files already in the target directory.

    Returns empty list if dir doesn't exist or has no audio files.
    FLAC files are excluded since they could be from a previous rip of the same job.
    """
    if not output_dir.exists():
        return []
    return [
        f for f in output_dir.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]


def move_to_trash(files: list[Path], trash_dir: Path, label: str = "") -> int:
    """Move files to trash directory, preserving context via subdirectory.

    Returns number of files moved.
    """
    if not files:
        return 0

    # Create a subdirectory in trash named after the source folder
    sub = trash_dir / label if label else trash_dir
    sub.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f in files:
        if f.exists():
            dst = sub / f.name
            # Avoid overwrite — append suffix if needed
            if dst.exists():
                dst = sub / f"{f.stem}_{moved}{f.suffix}"
            shutil.move(str(f), str(dst))
            moved += 1

    # Also move macOS ._ resource fork junk
    for f in files:
        junk = f.parent / f"._{f.name}"
        if junk.exists():
            dst = sub / junk.name
            shutil.move(str(junk), str(dst))

    return moved


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


async def update_kashidashi(job_id: str) -> None:
    """Notify kashidashi that a matched item has been ripped (set ripped_at)."""
    from datetime import datetime, timezone

    import httpx

    from backend.models import KashidashiCandidate, Job, JobMetadata

    config = get_config()
    base_url = config.integrations.kashidashi_url
    if not base_url:
        return

    async with async_session() as session:
        # Find matched kashidashi candidate for this job. Re-resolves can leave
        # stale matched rows behind, so pick the most recent and tolerate
        # duplicates rather than crashing the finalize tail.
        result = await session.execute(
            select(KashidashiCandidate)
            .where(
                KashidashiCandidate.job_id == job_id,
                KashidashiCandidate.matched == True,
            )
            .order_by(KashidashiCandidate.id.desc())
        )
        candidate = result.scalars().first()
        if not candidate:
            return

        job = await session.get(Job, job_id)
        meta_result = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta_result.scalar_one_or_none()

    ripped_at = datetime.now(timezone.utc)
    if job and job.completed_at:
        ripped_at = job.completed_at
    ripped_iso = ripped_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    patch_data = {
        "ripped_at": ripped_iso,
        "metadata_artist": meta.artist if meta else None,
        "metadata_album": meta.album if meta else None,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{base_url}/api/items/{candidate.item_id}",
                json=patch_data,
            )
            resp.raise_for_status()
        logger.info(
            "Kashidashi ripped_at updated: item %d for job %s",
            candidate.item_id, job_id,
        )
    except Exception:
        logger.warning(
            "Kashidashi update failed for item %d (non-critical)",
            candidate.item_id, exc_info=True,
        )


async def _plex_refresh() -> None:
    """Trigger Plex library refresh via HTTP API."""
    config = get_config()
    plex_url = config.integrations.plex_url
    token = config.integrations.plex_token
    section_id = config.integrations.plex_section_id

    if not (plex_url and token and section_id):
        logger.debug("Plex not configured (url/token/section_id), skipping refresh")
        return

    url = f"{plex_url.rstrip('/')}/library/sections/{section_id}/refresh"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"X-Plex-Token": token})
        if resp.status_code == 200:
            logger.info("Plex refresh triggered for section %s", section_id)
        else:
            logger.warning("Plex refresh returned HTTP %s", resp.status_code)
    except Exception:
        logger.warning("Plex refresh failed (non-critical)", exc_info=True)
