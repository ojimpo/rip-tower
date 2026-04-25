"""GnuDB submit service.

Posts human-resolved metadata back to GnuDB so the next person ripping the
same disc gets an automatic match. See docs/gnudb-submit.md for protocol
details and the design rationale.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

import httpx
from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import GnudbSubmission, Job, JobMetadata, Track
from backend.services.disc_identity import restore_identity
from backend.services.websocket import broadcast

logger = logging.getLogger(__name__)


# freedb 11-category whitelist used in the HTTP `Category` header.
FREEDB_CATEGORIES = (
    "rock",
    "jazz",
    "classical",
    "folk",
    "country",
    "blues",
    "newage",
    "reggae",
    "soundtrack",
    "misc",
    "data",
)

# Per-line byte cap on xmcd body. The freedb spec allows 256 bytes; we leave
# a small margin for the `KEY=` prefix to be safe.
MAX_LINE_BYTES = 240

# Throttle concurrent submits — GnuDB silently throttles aggressive clients.
_submit_lock = asyncio.Lock()


# ─────────────────────────── public API ───────────────────────────


async def submit_with_test_first(
    job_id: str, *, category: str | None = None
) -> GnudbSubmission:
    """Submit in test mode first; on 200, follow up with the real submit.

    Two-stage so a malformed body is caught by the test endpoint instead of
    polluting the real DB. Returns the final submission record (test result
    if test failed, otherwise the real submit result).
    """
    test_record = await submit(job_id, mode="test", category=category)
    if test_record.response_code != 200:
        return test_record
    return await submit(job_id, mode="submit", category=category)


async def submit(
    job_id: str,
    *,
    mode: Literal["test", "submit"],
    category: str | None = None,
) -> GnudbSubmission:
    """Build the xmcd body, POST to GnuDB, persist + broadcast the result."""
    config = get_config()
    integ = config.integrations
    if not integ.gnudb_email:
        raise RuntimeError("integrations.gnudb_email is not configured")

    xmcd, resolved_category = await build_xmcd(job_id, category_override=category)

    if mode == "submit":
        await _ensure_not_already_accepted(job_id)

    headers = {
        "Category": resolved_category,
        "Discid": _extract_discid_from_xmcd(xmcd),
        "User-Email": integ.gnudb_email,
        "Submit-Mode": mode,
        "Charset": "UTF-8",
        "Content-Type": "text/plain; charset=UTF-8",
        "X-Cddbd-Note": (
            f"Submitted via {integ.gnudb_client_name} {integ.gnudb_client_version}"
        )[:70],
    }

    url = integ.gnudb_url.rstrip("/") + "/~cddb/submit.cgi"

    response_code: int | None = None
    response_body: str | None = None
    error: str | None = None

    async with _submit_lock:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url, content=xmcd.encode("utf-8"), headers=headers
                )
            response_body = resp.text
            response_code = _parse_cddb_code(resp.text)
            if response_code is None:
                # No protocol code in the body — fall back to HTTP status
                response_code = resp.status_code
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"
            logger.exception("GnuDB submit network error for job %s", job_id)

    record = GnudbSubmission(
        job_id=job_id,
        disc_id=headers["Discid"],
        category=resolved_category,
        submit_mode=mode,
        response_code=response_code,
        response_body=response_body,
        xmcd_body=xmcd,
        error=error,
        submitted_at=datetime.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)

    accepted = response_code == 200 and not error
    await broadcast(
        "job:gnudb_submitted",
        {
            "job_id": job_id,
            "submission_id": record.id,
            "mode": mode,
            "status": "accepted" if accepted else "rejected",
            "response_code": response_code,
            "reason": _summarize_response(response_body, error),
        },
    )

    logger.info(
        "GnuDB %s for job %s (disc=%s cat=%s): code=%s",
        mode, job_id, headers["Discid"], resolved_category, response_code,
    )
    return record


async def build_xmcd(
    job_id: str, *, category_override: str | None = None
) -> tuple[str, str]:
    """Build the xmcd body for a job. Returns (body, freedb_category)."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if not job:
            raise RuntimeError(f"Job {job_id} not found")
        if not job.disc_id:
            raise RuntimeError(f"Job {job_id} has no disc_id")

        meta = (
            await session.execute(
                select(JobMetadata).where(JobMetadata.job_id == job_id)
            )
        ).scalar_one_or_none()
        if not meta or not meta.artist or not meta.album:
            raise RuntimeError(
                "Job is missing artist/album — refusing to submit empty entry"
            )

        tracks = (
            await session.execute(
                select(Track)
                .where(Track.job_id == job_id)
                .order_by(Track.track_num)
            )
        ).scalars().all()
        if not tracks:
            raise RuntimeError(f"Job {job_id} has no tracks")

    identity = await restore_identity(job_id)
    if identity is None or not identity.offsets or not identity.leadout:
        raise RuntimeError(
            "Job is missing track offsets/leadout — re-rip or re-identify "
            "the disc to populate them before submitting"
        )

    config = get_config()
    integ = config.integrations
    submitted_via = f"{integ.gnudb_client_name} {integ.gnudb_client_version}"

    # Album title: append [DISCn] for multi-disc
    album_title = meta.album_base or meta.album or ""
    if meta.total_discs and meta.total_discs > 1 and meta.disc_number:
        album_title = f"{album_title} [DISC{meta.disc_number}]"

    artist = "Various" if meta.is_compilation else (meta.artist or "")
    dtitle = f"{_sanitize(artist)} / {_sanitize(album_title)}"

    # Track titles. Compilation: each track is `track_artist / title`.
    track_lines: list[str] = []
    for i, t in enumerate(tracks):
        title = _sanitize(t.title or "")
        if meta.is_compilation and t.artist:
            title = f"{_sanitize(t.artist)} / {title}"
        track_lines.append(title)

    category = category_override or _categorize(
        meta.genre, meta.album, meta.artist
    )
    if category not in FREEDB_CATEGORIES:
        raise RuntimeError(f"Invalid freedb category: {category}")

    # ── Header comments ──
    lines: list[str] = ["# xmcd", "#"]
    lines.append("# Track frame offsets:")
    for off in identity.offsets:
        lines.append(f"#\t{off}")
    lines.append("#")
    lines.append(f"# Disc length: {identity.leadout} seconds")
    lines.append("#")
    lines.append("# Revision: 0")
    lines.append(f"# Submitted via: {submitted_via}")
    lines.append("#")

    # ── Body ──
    lines.append(f"DISCID={job.disc_id}")
    _emit_long("DTITLE", dtitle, lines)
    lines.append(f"DYEAR={meta.year if meta.year else ''}")
    lines.append(f"DGENRE={_sanitize(meta.genre or '')}")
    for i, title in enumerate(track_lines):
        _emit_long(f"TTITLE{i}", title, lines)
    lines.append("EXTD=")
    for i in range(len(track_lines)):
        lines.append(f"EXTT{i}=")
    lines.append("PLAYORDER=")

    return "\n".join(lines) + "\n", category


# ─────────────────────────── helpers ───────────────────────────


def _categorize(
    genre: str | None, album: str | None = None, artist: str | None = None
) -> str:
    """Map a free-form genre into one of the 11 freedb categories."""
    g = (genre or "").lower()

    rules: list[tuple[tuple[str, ...], str]] = [
        (("jazz", "fusion", "ジャズ"), "jazz"),
        (("classical", "クラシック", "交響", "協奏"), "classical"),
        (("country", "カントリー"), "country"),
        (("blues", "ブルース"), "blues"),
        (("reggae", "レゲエ"), "reggae"),
        (("newage", "new age", "アンビエント", "ヒーリング", "ambient"), "newage"),
        (
            ("soundtrack", "ost", "サウンドトラック", "劇伴", "アニメ", "ゲーム"),
            "soundtrack",
        ),
        # folk last among substring rules so 'folklore' inside other genres
        # doesn't override more specific matches above
        (("folk", "民謡"), "folk"),
    ]

    for needles, cat in rules:
        if any(n in g for n in needles):
            return cat

    if g.strip():
        # Anything we recognize as a genre but didn't match above is rock by
        # convention (J-Pop, pop, hip hop, R&B, …).
        return "rock"
    return "misc"


def _sanitize(value: str) -> str:
    """Strip control chars + collapse newlines that would break the xmcd format."""
    if not value:
        return ""
    return (
        value.replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )


def _emit_long(key: str, value: str, out: list[str]) -> None:
    """Emit `KEY=value`, splitting into multiple `KEY=` lines if needed.

    freedb caps each line at 256 bytes (UTF-8). Concatenated by readers.
    """
    if not value:
        out.append(f"{key}=")
        return

    # Reserve bytes for `KEY=`
    prefix = f"{key}="
    budget = MAX_LINE_BYTES - len(prefix.encode("utf-8"))
    encoded = value.encode("utf-8")
    if len(encoded) <= budget:
        out.append(prefix + value)
        return

    # Walk char-by-char so we don't split a multi-byte char.
    chunk = bytearray()
    chunk_text = []
    for ch in value:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > budget:
            out.append(prefix + "".join(chunk_text))
            chunk = bytearray()
            chunk_text = []
        chunk.extend(b)
        chunk_text.append(ch)
    if chunk_text:
        out.append(prefix + "".join(chunk_text))


def _parse_cddb_code(body: str | None) -> int | None:
    """Pull the leading 3-digit CDDB response code from a freedb response."""
    if not body:
        return None
    first = body.lstrip().splitlines()[0] if body.strip() else ""
    head = first.split(" ", 1)[0]
    if head.isdigit() and len(head) == 3:
        return int(head)
    return None


def _summarize_response(body: str | None, error: str | None) -> str:
    if error:
        return error
    if not body:
        return ""
    first = body.strip().splitlines()[0] if body.strip() else ""
    return first[:200]


def _extract_discid_from_xmcd(xmcd: str) -> str:
    for line in xmcd.splitlines():
        if line.startswith("DISCID="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("xmcd has no DISCID line")


async def _ensure_not_already_accepted(job_id: str) -> None:
    """Prevent re-submitting a job whose entry was already accepted.

    GnuDB doesn't support edits — duplicate submits create dupes.
    """
    async with async_session() as session:
        existing = (
            await session.execute(
                select(GnudbSubmission)
                .where(GnudbSubmission.job_id == job_id)
                .where(GnudbSubmission.submit_mode == "submit")
                .where(GnudbSubmission.response_code == 200)
            )
        ).scalars().first()
        if existing:
            raise RuntimeError(
                f"Job {job_id} already has an accepted GnuDB submission "
                f"(submission_id={existing.id})"
            )


async def list_submissions(job_id: str) -> list[GnudbSubmission]:
    async with async_session() as session:
        result = await session.execute(
            select(GnudbSubmission)
            .where(GnudbSubmission.job_id == job_id)
            .order_by(GnudbSubmission.submitted_at.desc())
        )
        return list(result.scalars().all())


async def already_accepted(job_id: str) -> bool:
    async with async_session() as session:
        existing = (
            await session.execute(
                select(GnudbSubmission)
                .where(GnudbSubmission.job_id == job_id)
                .where(GnudbSubmission.submit_mode == "submit")
                .where(GnudbSubmission.response_code == 200)
            )
        ).scalars().first()
        return existing is not None
