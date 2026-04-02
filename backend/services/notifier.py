"""Discord webhook notifications and review reminders.

Ported from ~/dev/openclaw-cd-rip/scripts/notifier.py.
"""

import asyncio
import logging

import httpx
from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Job, JobMetadata

logger = logging.getLogger(__name__)


async def _send_discord(content: str) -> None:
    """Send a message to Discord via webhook."""
    config = get_config()
    url = config.integrations.discord_webhook
    if not url:
        return

    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={"content": content}, timeout=10)
        except Exception:
            logger.exception("Discord notification failed")


async def notify_start(job_id: str) -> None:
    """Notify that a ripping job has started."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    artist = meta.artist if meta else "Unknown"
    album = meta.album if meta else "Unknown"
    drive = job.drive_id or "import" if job else "unknown"
    disc_id = (job.disc_id or "")[:8] if job else ""
    track_count = job.track_count if job and hasattr(job, "track_count") else ""

    parts = [f"{artist} / {album}"]
    detail_parts = []
    if track_count:
        detail_parts.append(f"{track_count}tracks")
    if drive:
        detail_parts.append(drive)
    if disc_id:
        detail_parts.append(disc_id)
    if detail_parts:
        parts.append(f"（{' / '.join(detail_parts)}）")

    await _send_discord(f"▶ リッピング開始：{''.join(parts)}")


async def notify_complete(job_id: str) -> None:
    """Notify that a job completed successfully."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    artist = meta.artist if meta else "Unknown"
    album = meta.album if meta else "Unknown"

    await _send_discord(
        f"✅ 完了：{artist} / {album}\n🔗 /job/{job_id}"
    )


async def notify_review(job_id: str) -> None:
    """Notify that a job needs manual review."""
    async with async_session() as session:
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    confidence = meta.confidence if meta else 0
    album = meta.album if meta else "不明なアルバム"

    await _send_discord(
        f"⚠️ メタデータ要確認：{album}（confidence:{confidence}）\n🔗 /job/{job_id}"
    )


async def notify_error(job_id: str, message: str) -> None:
    """Notify that a job failed."""
    await _send_discord(f"❌ エラー：{message}\n🔗 /job/{job_id}")


async def schedule_reminder(job_id: str) -> None:
    """Schedule periodic reminders for unresolved review jobs."""
    config = get_config()
    initial_hours = config.general.reminder_initial_hours
    interval_hours = config.general.reminder_interval_hours

    if interval_hours <= 0:
        return

    # Wait for initial period
    await asyncio.sleep(initial_hours * 3600)

    while True:
        async with async_session() as session:
            job = await session.get(Job, job_id)
            if not job or job.status != "review":
                return  # Job was approved or deleted

        # Collect all pending review jobs for a combined notification
        async with async_session() as session:
            result = await session.execute(
                select(Job, JobMetadata)
                .outerjoin(JobMetadata, Job.id == JobMetadata.job_id)
                .where(Job.status == "review")
            )
            pending = result.all()

        if not pending:
            return

        if len(pending) == 1:
            job, meta = pending[0]
            album = meta.album if meta else "不明"
            await _send_discord(
                f"⏰ 未承認ジョブ（1件）：{album}\n🔗 /job/{job.id}"
            )
        else:
            lines = []
            for job, meta in pending:
                album = meta.album if meta else "不明"
                lines.append(f"  - {album}")
            await _send_discord(
                f"⏰ 未承認ジョブ（{len(pending)}件）\n"
                + "\n".join(lines)
                + "\n🔗 /"
            )

        await asyncio.sleep(interval_hours * 3600)
