"""USB CD drive detection and monitoring.

Scans /dev/sr* on startup and USB hotplug events.
Identifies drives by USB serial number (via udevadm).
"""

import asyncio
import fcntl
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Track background tasks to prevent duplicates
_hotplug_task: asyncio.Task | None = None
_disc_poll_task: asyncio.Task | None = None

# ioctl constants for CDROM_DRIVE_STATUS
_CDROM_DRIVE_STATUS = 0x5326
CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4


def get_tray_status(dev_path: str) -> int:
    """Get CD drive tray status via ioctl.

    Returns one of CDS_NO_INFO, CDS_NO_DISC, CDS_TRAY_OPEN,
    CDS_DRIVE_NOT_READY, CDS_DISC_OK.
    Returns CDS_NO_INFO on error.
    """
    try:
        fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            status = fcntl.ioctl(fd, _CDROM_DRIVE_STATUS, 0)
            return status
        finally:
            os.close(fd)
    except OSError:
        return CDS_NO_INFO


def _get_drive_info(dev_path: str) -> dict | None:
    """Get drive serial and model via /sys tree, udevadm, or fallback."""
    dev_name = Path(dev_path).name  # e.g. "sr0"

    serial = None
    model = None

    # 1. Walk /sys device tree to find USB serial (works inside Docker)
    sys_device = Path(f"/sys/block/{dev_name}/device")
    try:
        real_path = sys_device.resolve()
        p = real_path
        while p != Path("/"):
            serial_file = p / "serial"
            if serial_file.exists():
                serial = serial_file.read_text().strip()
                break
            p = p.parent
    except Exception:
        pass

    # Read model from /sys/block
    try:
        vendor = (sys_device / "vendor").read_text().strip() if (sys_device / "vendor").exists() else ""
        model_sys = (sys_device / "model").read_text().strip() if (sys_device / "model").exists() else ""
        if vendor or model_sys:
            model = f"{vendor} {model_sys}".strip()
    except Exception:
        pass

    # 2. Try udevadm (may provide better info on host)
    if not serial:
        try:
            result = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={dev_path}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("ID_SERIAL_SHORT="):
                        serial = line.split("=", 1)[1].strip()
                    elif line.startswith("ID_SERIAL=") and not serial:
                        serial = line.split("=", 1)[1].strip()
                    elif line.startswith("ID_MODEL=") and not model:
                        model = line.split("=", 1)[1].strip().replace("_", " ")
        except Exception:
            pass

    # 3. Last resort: use device name (not stable across reconnects)
    if not serial:
        serial = dev_name
        model = model or dev_name

    return {"serial": serial, "model": model or serial[:20]}


def scan_drives() -> list[dict]:
    """Scan for connected CD/DVD drives.

    Returns list of {"path": "/dev/sr0", "serial": "...", "model": "...", "has_disc": bool, "tray_open": bool}.
    """
    drives = []
    for dev in sorted(Path("/dev").glob("sr*")):
        dev_path = str(dev)
        info = _get_drive_info(dev_path)
        if info:
            tray_status = get_tray_status(dev_path)
            drives.append({
                "path": dev_path,
                "serial": info["serial"],
                "model": info["model"],
                "has_disc": tray_status == CDS_DISC_OK,
                "tray_open": tray_status == CDS_TRAY_OPEN,
            })
    return drives


async def _migrate_legacy_drive(session, info: dict) -> "Drive | None":
    """Migrate a drive from legacy fallback serial (vendor_model_srN) to real USB serial.

    Returns the migrated Drive if found, None otherwise.
    """
    from backend.models import Drive, Job
    from sqlalchemy import select, update

    dev_name = Path(info["path"]).name  # e.g. "sr2"
    legacy_suffix = f"_{dev_name}"

    # Find legacy entry whose drive_id ends with _srN
    result = await session.execute(
        select(Drive).where(Drive.drive_id.endswith(legacy_suffix))
    )
    candidates = result.scalars().all()
    if len(candidates) != 1:
        # Ambiguous or none — skip migration
        return None
    legacy_drive = candidates[0]

    old_id = legacy_drive.drive_id
    new_id = info["serial"]

    # Update jobs referencing the old drive_id
    await session.execute(
        update(Job).where(Job.drive_id == old_id).values(drive_id=new_id)
    )

    # Delete old entry and create new one (drive_id is PK, can't update)
    name = legacy_drive.name
    auto_rip = legacy_drive.auto_rip
    auto_rip_source_type = legacy_drive.auto_rip_source_type
    await session.delete(legacy_drive)
    await session.flush()

    new_drive = Drive(
        drive_id=new_id,
        name=name,
        current_path=info["path"],
        auto_rip=auto_rip,
        auto_rip_source_type=auto_rip_source_type,
    )
    session.add(new_drive)
    logger.info("Drive migrated: %s → %s (%s)", old_id, new_id, name)
    return new_drive


async def start_monitoring() -> None:
    """Register discovered drives in the database and start hotplug monitoring."""
    from backend.database import async_session
    from backend.models import Drive, Job
    from backend.services.websocket import broadcast

    from sqlalchemy import select

    auto_rip_candidates: list[tuple[str, str]] = []  # (drive_id, source_type)

    async with async_session() as session:
        # Mark all drives as disconnected first
        result = await session.execute(select(Drive))
        for drive in result.scalars():
            drive.current_path = None
            drive.cached_disc_id = None
            drive.cached_artist = None
            drive.cached_album = None
            drive.cached_track_count = None
        await session.commit()

        # Scan and register/update
        for info in scan_drives():
            result = await session.execute(
                select(Drive).where(Drive.drive_id == info["serial"])
            )
            drive = result.scalar_one_or_none()
            if drive:
                drive.current_path = info["path"]
                logger.info("Drive reconnected: %s (%s) at %s", drive.name, drive.drive_id, info["path"])
            else:
                # Check for legacy fallback serial to migrate
                drive = await _migrate_legacy_drive(session, info)
                if not drive:
                    drive = Drive(
                        drive_id=info["serial"],
                        name=info["model"] or info["serial"][:16],
                        current_path=info["path"],
                    )
                    session.add(drive)
                    logger.info("New drive registered: %s at %s", info["serial"], info["path"])

            await broadcast("drive:connected", {
                "drive_id": drive.drive_id,
                "name": drive.name,
                "path": info["path"],
            })

            # Check if auto_rip is enabled and no active job on this drive
            if drive.auto_rip:
                active_job = await session.execute(
                    select(Job)
                    .where(Job.drive_id == drive.drive_id)
                    .where(Job.status.notin_(["complete", "error"]))
                    .limit(1)
                )
                if not active_job.scalar_one_or_none():
                    auto_rip_candidates.append((drive.drive_id, drive.auto_rip_source_type))

        await session.commit()

    # Trigger auto-rip jobs outside the session
    for drive_id, source_type in auto_rip_candidates:
        logger.info("Auto-rip triggered for drive %s (source_type=%s)", drive_id, source_type)
        await _trigger_auto_rip(drive_id, source_type)

    # Start background tasks (only if not already running)
    global _hotplug_task, _disc_poll_task
    if _hotplug_task is None or _hotplug_task.done():
        _hotplug_task = asyncio.create_task(_watch_hotplug())
    if _disc_poll_task is None or _disc_poll_task.done():
        _disc_poll_task = asyncio.create_task(_poll_disc_status())


async def _trigger_auto_rip(drive_id: str, source_type: str) -> None:
    """Create and start a rip job for auto-rip."""
    import uuid
    from backend.database import async_session
    from backend.models import Job
    from backend.schemas import RipRequest
    from backend.services.pipeline import run_pipeline

    job_id = str(uuid.uuid4())
    async with async_session() as session:
        job = Job(
            id=job_id,
            drive_id=drive_id,
            status="pending",
            source_type=source_type,
        )
        session.add(job)
        await session.commit()

    request = RipRequest(drive_id=drive_id, source_type=source_type)
    asyncio.create_task(run_pipeline(job_id, request))
    logger.info("Auto-rip job %s created for drive %s", job_id, drive_id)


async def _rescan_drives() -> None:
    """Lightweight rescan: update drive paths and trigger auto-rip without spawning a new watcher."""
    from backend.database import async_session
    from backend.models import Drive, Job
    from backend.services.websocket import broadcast
    from sqlalchemy import select

    auto_rip_candidates: list[tuple[str, str]] = []

    async with async_session() as session:
        # Mark all drives as disconnected
        result = await session.execute(select(Drive))
        for drive in result.scalars():
            drive.current_path = None
            drive.cached_disc_id = None
            drive.cached_artist = None
            drive.cached_album = None
            drive.cached_track_count = None
        await session.commit()

        # Scan and update
        for info in scan_drives():
            result = await session.execute(
                select(Drive).where(Drive.drive_id == info["serial"])
            )
            drive = result.scalar_one_or_none()
            if drive:
                drive.current_path = info["path"]
                logger.info("Drive reconnected: %s (%s) at %s", drive.name, drive.drive_id, info["path"])
            else:
                # Check for legacy fallback serial to migrate
                drive = await _migrate_legacy_drive(session, info)
                if not drive:
                    drive = Drive(
                        drive_id=info["serial"],
                        name=info["model"] or info["serial"][:16],
                        current_path=info["path"],
                    )
                    session.add(drive)
                    logger.info("New drive registered: %s at %s", info["serial"], info["path"])

            await broadcast("drive:connected", {
                "drive_id": drive.drive_id,
                "name": drive.name,
                "path": info["path"],
            })

            if drive.auto_rip:
                active_job = await session.execute(
                    select(Job)
                    .where(Job.drive_id == drive.drive_id)
                    .where(Job.status.notin_(["complete", "error"]))
                    .limit(1)
                )
                if not active_job.scalar_one_or_none():
                    auto_rip_candidates.append((drive.drive_id, drive.auto_rip_source_type))

        await session.commit()

    for drive_id, source_type in auto_rip_candidates:
        logger.info("Auto-rip triggered for drive %s (source_type=%s)", drive_id, source_type)
        await _trigger_auto_rip(drive_id, source_type)


async def _watch_hotplug() -> None:
    """Monitor udev events for CD drive hotplug (add/remove)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "udevadm", "monitor", "--udev", "--subsystem-match=block",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async for line in proc.stdout:
            text = line.decode().strip()
            if re.search(r"(add|remove).*sr\d+", text):
                logger.info("Drive hotplug event: %s", text)
                await asyncio.sleep(1)  # Wait for device to settle
                await _rescan_drives()
    except Exception:
        logger.exception("Hotplug watcher failed")


# Previous disc state per drive_id: True = disc present, False = no disc
_prev_disc_state: dict[str, bool] = {}
# Counter for periodic full rescan (every N poll cycles)
_poll_cycle: int = 0
_RESCAN_INTERVAL: int = 10  # Full rescan every 10 cycles (≈30 seconds)


async def _auto_identify(drive, session) -> "DiscInfo | None":
    """Run disc identification and cache results. Returns DiscInfo or None on failure."""
    from backend.services.disc_identify import DiscInfo, identify

    try:
        info = await identify(drive.current_path)
        drive.cached_disc_id = info.disc_id
        drive.cached_artist = info.artist
        drive.cached_album = info.album
        drive.cached_track_count = info.track_count
        await session.commit()
        logger.info(
            "Auto-identified disc in %s: %s / %s (%d tracks)",
            drive.name, info.artist or "?", info.album or "?", info.track_count,
        )
        return info
    except Exception:
        logger.warning("Auto-identify failed for %s", drive.name)
        return None


async def _notify_disc_inserted(drive, disc_info: "DiscInfo | None") -> None:
    """Send a Discord notification when a disc is inserted."""
    from backend.services.notifier import _send_discord
    from backend.config import get_config

    config = get_config()
    base = config.general.base_url.rstrip("/")

    if disc_info and (disc_info.artist or disc_info.album):
        artist = disc_info.artist or "不明"
        album = disc_info.album or "不明"
        msg = f"💿 CD検出：{artist} / {album}（{disc_info.track_count}曲）\nドライブ：{drive.name}"
    else:
        msg = f"💿 CD検出（情報取得不可）\nドライブ：{drive.name}"

    if base:
        msg += f"\n{base}/"

    await _send_discord(msg)


async def _poll_disc_status() -> None:
    """Periodically check tray/disc status via ioctl and broadcast changes."""
    from backend.database import async_session
    from backend.models import Drive, Job
    from backend.services.websocket import broadcast
    from sqlalchemy import select

    global _prev_disc_state, _poll_cycle

    while True:
        await asyncio.sleep(3)
        try:
            # Periodic full rescan to detect new/removed drives
            # (udevadm monitor doesn't work reliably inside Docker)
            _poll_cycle += 1
            if _poll_cycle >= _RESCAN_INTERVAL:
                _poll_cycle = 0
                await _rescan_drives()

            async with async_session() as session:
                result = await session.execute(
                    select(Drive).where(Drive.current_path.isnot(None))
                )
                drives = result.scalars().all()

                for drive in drives:
                    tray_status = await asyncio.to_thread(
                        get_tray_status, drive.current_path
                    )
                    has_disc = tray_status == CDS_DISC_OK
                    prev = _prev_disc_state.get(drive.drive_id)

                    if prev is not None and prev != has_disc:
                        if has_disc:
                            logger.info("Disc inserted in %s (%s)", drive.name, drive.drive_id)

                            # Auto-identify the disc
                            disc_info = await _auto_identify(drive, session)

                            await broadcast("drive:disc_inserted", {
                                "drive_id": drive.drive_id,
                                "name": drive.name,
                            })

                            # Discord notification
                            await _notify_disc_inserted(drive, disc_info)

                            # Trigger auto-rip if enabled
                            if drive.auto_rip:
                                active_job = await session.execute(
                                    select(Job)
                                    .where(Job.drive_id == drive.drive_id)
                                    .where(Job.status.notin_(["complete", "error"]))
                                    .limit(1)
                                )
                                if not active_job.scalar_one_or_none():
                                    logger.info("Auto-rip triggered (disc poll) for drive %s", drive.drive_id)
                                    await _trigger_auto_rip(drive.drive_id, drive.auto_rip_source_type)
                        else:
                            logger.info("Disc ejected from %s (%s)", drive.name, drive.drive_id)
                            # Clear cached disc info
                            drive.cached_disc_id = None
                            drive.cached_artist = None
                            drive.cached_album = None
                            drive.cached_track_count = None
                            await session.commit()
                            await broadcast("drive:disc_ejected", {
                                "drive_id": drive.drive_id,
                                "name": drive.name,
                            })

                    _prev_disc_state[drive.drive_id] = has_disc

        except Exception:
            logger.exception("Disc poll error")
