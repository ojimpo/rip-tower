"""USB CD drive detection and monitoring.

Scans /dev/sr* on startup and USB hotplug events.
Identifies drives by USB serial number (via udevadm).
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_drive_info(dev_path: str) -> dict | None:
    """Get drive serial and model via udevadm or /sys fallback."""
    dev_name = Path(dev_path).name  # e.g. "sr0"

    # Try udevadm first
    serial = None
    model = None
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
                elif line.startswith("ID_MODEL="):
                    model = line.split("=", 1)[1].strip().replace("_", " ")
    except Exception:
        pass

    # Fallback: read from /sys/block/
    if not serial:
        sys_path = Path(f"/sys/block/{dev_name}/device")
        try:
            vendor = (sys_path / "vendor").read_text().strip() if (sys_path / "vendor").exists() else ""
            model_sys = (sys_path / "model").read_text().strip() if (sys_path / "model").exists() else ""
            # Use vendor+model as a stable identifier
            if vendor or model_sys:
                serial = f"{vendor}_{model_sys}_{dev_name}".replace(" ", "_")
                model = model or f"{vendor} {model_sys}".strip()
        except Exception:
            pass

    if not serial:
        # Last resort: use device name
        serial = dev_name
        model = dev_name

    return {"serial": serial, "model": model or serial[:20]}


def scan_drives() -> list[dict]:
    """Scan for connected CD/DVD drives.

    Returns list of {"path": "/dev/sr0", "serial": "...", "model": "..."}.
    """
    drives = []
    for dev in sorted(Path("/dev").glob("sr*")):
        dev_path = str(dev)
        info = _get_drive_info(dev_path)
        if info:
            drives.append({
                "path": dev_path,
                "serial": info["serial"],
                "model": info["model"],
            })
    return drives


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

    # Start background hotplug watcher
    asyncio.create_task(_watch_hotplug())


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
                await start_monitoring()
    except Exception:
        logger.exception("Hotplug watcher failed")
