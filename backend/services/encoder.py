"""Audio encoding — WAV to configured format.

Supports FLAC, ALAC, Opus, MP3, WAV (copy).
Ported from ~/dev/openclaw-cd-rip/scripts/encoder.py.
"""

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Job, JobMetadata, Track
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)

# Format extension mapping
FORMAT_EXT = {
    "flac": ".flac",
    "alac": ".m4a",
    "opus": ".opus",
    "mp3": ".mp3",
    "wav": ".wav",
}


def safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    # Remove audio extensions from title (e.g., "Song.mp3" -> "Song")
    name = re.sub(r"\.(m4a|mp3|wav|flac|aif|aiff)$", "", name, flags=re.IGNORECASE).strip()
    # Replace path-unsafe characters
    name = name.replace("/", "-").replace("\\", "-").replace(":", " -")
    name = name.replace("?", "").replace("*", "")
    name = re.sub(r'[<>"|]', "_", name)
    return name.strip(". ")


async def encode_all(job_id: str) -> None:
    """Encode all tracks for a job."""
    config = get_config()
    fmt = config.output.format
    quality = config.output.quality
    ext = FORMAT_EXT.get(fmt, ".flac")

    async with async_session() as session:
        tracks = await session.execute(
            select(Track)
            .where(Track.job_id == job_id, Track.rip_status.in_(["ok", "ok_degraded"]))
            .order_by(Track.track_num)
        )
        tracks = tracks.scalars().all()

        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    for track in tracks:
        if not track.wav_path:
            continue
        await _encode_track(job_id, track, meta, fmt, quality, ext)


async def _encode_track(
    job_id: str,
    track,
    meta,
    fmt: str,
    quality: int,
    ext: str,
) -> None:
    """Encode a single track."""
    wav_path = Path(track.wav_path)
    if not wav_path.exists():
        logger.error("WAV not found: %s", wav_path)
        return

    output_path = wav_path.with_suffix(ext)

    # Update status
    async with async_session() as session:
        t = await session.execute(
            select(Track).where(Track.job_id == job_id, Track.track_num == track.track_num)
        )
        t = t.scalar_one()
        t.encode_status = "encoding"
        await session.commit()

    # Build encode command
    cmd = _build_encode_cmd(fmt, quality, wav_path, output_path)

    if cmd:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("Encoding failed for track %d: %s", track.track_num, stderr.decode()[:200])
            async with async_session() as session:
                t = await session.execute(
                    select(Track).where(Track.job_id == job_id, Track.track_num == track.track_num)
                )
                t = t.scalar_one()
                t.encode_status = "failed"
                await session.commit()
            return

    # Tag the file
    if fmt == "flac" and meta:
        await _tag_flac(output_path, track, meta)

    # Update status
    async with async_session() as session:
        t = await session.execute(
            select(Track).where(Track.job_id == job_id, Track.track_num == track.track_num)
        )
        t = t.scalar_one()
        t.encode_status = "ok"
        t.encoded_path = str(output_path)
        await session.commit()

    logger.info("Encoded track %d → %s", track.track_num, output_path.name)


def _build_encode_cmd(
    fmt: str, quality: int, wav_path: Path, output_path: Path
) -> list[str] | None:
    """Build the encoding command for the given format."""
    if fmt == "flac":
        return ["flac", "-f", f"-{quality}", "-o", str(output_path), str(wav_path)]
    elif fmt == "alac":
        return [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-acodec", "alac", str(output_path),
        ]
    elif fmt == "opus":
        return [
            "opusenc", f"--bitrate={quality}",
            str(wav_path), str(output_path),
        ]
    elif fmt == "mp3":
        return [
            "lame", f"-V{quality}", str(wav_path), str(output_path),
        ]
    elif fmt == "wav":
        # Just copy
        import shutil
        shutil.copy2(wav_path, output_path)
        return None
    else:
        raise ValueError(f"Unknown format: {fmt}")


async def _tag_flac(path: Path, track, meta) -> None:
    """Apply metadata tags to a FLAC file using metaflac.

    Clears all existing tags first (matching original encoder.py behavior),
    then sets all tags in a single metaflac call.
    """
    if not meta:
        return

    # Determine per-track artist vs album artist
    track_artist = track.artist or meta.artist or ""
    album_artist = meta.artist or ""

    # Build tag arguments — clear first, then set all
    tags = ["--remove-all-tags"]

    if track_artist:
        tags.append(f"--set-tag=ARTIST={track_artist}")
    album_tag = (meta.album_base if hasattr(meta, "album_base") and meta.album_base else None) or meta.album
    if album_tag:
        tags.append(f"--set-tag=ALBUM={album_tag}")
    if track.title:
        tags.append(f"--set-tag=TITLE={track.title}")
    if track.track_num:
        tags.append(f"--set-tag=TRACKNUMBER={track.track_num}")

    # ALBUMARTIST: use "Various Artists" for compilations, else album artist
    if getattr(meta, "is_compilation", False):
        tags.append("--set-tag=ALBUMARTIST=Various Artists")
    elif album_artist:
        tags.append(f"--set-tag=ALBUMARTIST={album_artist}")

    if meta.year:
        tags.append(f"--set-tag=DATE={meta.year}")
    if meta.genre:
        tags.append(f"--set-tag=GENRE={meta.genre}")
    if meta.disc_number is not None:
        tags.append(f"--set-tag=DISCNUMBER={meta.disc_number}")
    if meta.total_discs is not None:
        tags.append(f"--set-tag=TOTALDISCS={meta.total_discs}")

    tags.append(str(path))

    proc = await asyncio.create_subprocess_exec(
        "metaflac", *tags,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "metaflac tagging failed for %s: %s",
            path.name, stderr.decode("utf-8", "replace")[:200],
        )
