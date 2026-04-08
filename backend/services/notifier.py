"""Discord webhook notifications and review reminders.

Ported from ~/dev/openclaw-cd-rip/scripts/notifier.py.

Threading: The first notification for a job (start or review) saves its
Discord message ID to jobs.discord_message_id.  Follow-up notifications
(complete, eject reminder, review reminder) reply to that message so the
conversation stays grouped.
"""

import asyncio
import logging

import httpx
from sqlalchemy import select

from backend.config import get_config
from backend.database import async_session
from backend.models import Drive, Job, JobMetadata

logger = logging.getLogger(__name__)


def _job_url(job_id: str) -> str:
    """Build a full or relative URL for a job."""
    config = get_config()
    base = config.general.base_url.rstrip("/")
    if base:
        return f"{base}/job/{job_id}"
    return f"/job/{job_id}"


async def _send_discord(
    content: str,
    reply_to: str | None = None,
) -> str | None:
    """Send a message to Discord.

    If reply_to is set AND bot token is configured, uses the Bot API to
    create a true reply.  Otherwise falls back to webhook.
    Returns the message ID on success, None otherwise.
    """
    config = get_config()
    bot_token = config.integrations.discord_bot_token
    channel_id = config.integrations.discord_channel_id

    # Use Bot API if configured (required for replies)
    if bot_token and channel_id:
        return await _send_via_bot(content, bot_token, channel_id, reply_to)

    # Fallback: webhook (no reply support)
    url = config.integrations.discord_webhook
    if not url:
        return None

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{url}?wait=true",
                json={"content": content},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            logger.warning("Discord webhook returned %d", resp.status_code)
        except Exception:
            logger.exception("Discord notification failed")

    return None


async def _send_via_bot(
    content: str,
    bot_token: str,
    channel_id: str,
    reply_to: str | None = None,
) -> str | None:
    """Send a message via Discord Bot API, optionally as a reply."""
    payload: dict = {"content": content}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to}
        payload["allowed_mentions"] = {"parse": []}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"Bot {bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            logger.warning("Discord Bot API returned %d: %s", resp.status_code, resp.text[:200])
        except Exception:
            logger.exception("Discord Bot API failed")

    return None


async def _get_discord_msg_id(job_id: str) -> str | None:
    """Get stored Discord message ID for a job."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        return job.discord_message_id if job else None


async def _save_discord_msg_id(job_id: str, message_id: str) -> None:
    """Save Discord message ID to the job record."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.discord_message_id = message_id
            await session.commit()


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

    msg_id = await _send_discord(f"▶ リッピング開始：{''.join(parts)}")
    if msg_id:
        await _save_discord_msg_id(job_id, msg_id)


async def notify_complete(job_id: str) -> None:
    """Notify that a job completed successfully (reply to original)."""
    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    artist = meta.artist if meta else "Unknown"
    album = meta.album if meta else "Unknown"
    reply_to = job.discord_message_id if job else None

    await _send_discord(
        f"✅ 完了：{artist} / {album}\n{_job_url(job_id)}",
        reply_to=reply_to,
    )


async def notify_review(job_id: str) -> None:
    """Notify that a job needs manual review.

    If there's no existing Discord message (e.g. auto-rip), this becomes
    the anchor message for future replies.
    """
    async with async_session() as session:
        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    confidence = meta.confidence if meta else 0
    album = meta.album if meta else "不明なアルバム"
    reply_to = job.discord_message_id if job else None

    msg_id = await _send_discord(
        f"⚠️ メタデータ要確認：{album}（confidence:{confidence}）\n{_job_url(job_id)}",
        reply_to=reply_to,
    )

    # If this is the first message for this job, save it as anchor
    if msg_id and not reply_to:
        await _save_discord_msg_id(job_id, msg_id)


async def notify_error(job_id: str, message: str) -> None:
    """Notify that a job failed (reply to original)."""
    reply_to = await _get_discord_msg_id(job_id)
    await _send_discord(
        f"❌ エラー：{message}\n{_job_url(job_id)}",
        reply_to=reply_to,
    )


async def schedule_eject_reminder(job_id: str, drive_id: str) -> None:
    """Wait N minutes after job completion, then check if disc is still inserted."""
    config = get_config()
    minutes = config.general.eject_reminder_minutes
    if minutes <= 0:
        return

    await asyncio.sleep(minutes * 60)

    async with async_session() as session:
        drive = await session.get(Drive, drive_id)
        if not drive or not drive.current_path:
            return  # Drive disconnected

        # Check actual disc status via ioctl, not just DB state
        from backend.services.drive_monitor import get_tray_status, CDS_DISC_OK

        tray_status = await asyncio.to_thread(get_tray_status, drive.current_path)
        if tray_status != CDS_DISC_OK:
            logger.debug("Disc already ejected from %s, skipping reminder", drive_id)
            return

        job = await session.get(Job, job_id)
        meta = await session.execute(
            select(JobMetadata).where(JobMetadata.job_id == job_id)
        )
        meta = meta.scalar_one_or_none()

    artist = meta.artist if meta else "Unknown"
    album = meta.album if meta else "Unknown"
    drive_name = drive.name if drive else drive_id
    reply_to = job.discord_message_id if job else None

    await _send_discord(
        f"💿 CD取り出し忘れ：{artist} / {album}\n"
        f"ドライブ「{drive_name}」にCDが残っています",
        reply_to=reply_to,
    )


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

            meta = await session.execute(
                select(JobMetadata).where(JobMetadata.job_id == job_id)
            )
            meta = meta.scalar_one_or_none()

        album = meta.album if meta else "不明"
        reply_to = job.discord_message_id if job else None

        await _send_discord(
            f"⏳ 未承認：{album}\n{_job_url(job_id)}",
            reply_to=reply_to,
        )

        await asyncio.sleep(interval_hours * 3600)
