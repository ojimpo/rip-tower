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


def _get_usb_serial(dev_path: str) -> str | None:
    """Get USB serial number for a device via udevadm."""
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", f"--name={dev_path}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ID_SERIAL_SHORT="):
                return line.split("=", 1)[1].strip()
            if line.startswith("ID_SERIAL="):
                return line.split("=", 1)[1].strip()
    except Exception:
        logger.warning("Failed to get serial for %s", dev_path)
    return None


def scan_drives() -> list[dict]:
    """Scan for connected CD/DVD drives.

    Returns list of {"path": "/dev/sr0", "serial": "...", "model": "..."}.
    """
    drives = []
    for dev in sorted(Path("/dev").glob("sr*")):
        dev_path = str(dev)
        serial = _get_usb_serial(dev_path)
        if serial:
            # Try to get model name
            model = None
            try:
                result = subprocess.run(
                    ["udevadm", "info", "--query=property", f"--name={dev_path}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in result.stdout.splitlines():
                    if line.startswith("ID_MODEL="):
                        model = line.split("=", 1)[1].strip().replace("_", " ")
                        break
            except Exception:
                pass

            drives.append({
                "path": dev_path,
                "serial": serial,
                "model": model or serial[:16],
            })
    return drives


async def start_monitoring() -> None:
    """Register discovered drives in the database and start hotplug monitoring."""
    from backend.database import async_session
    from backend.models import Drive
    from backend.services.websocket import broadcast

    from sqlalchemy import select

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

        await session.commit()

    # Start background hotplug watcher
    asyncio.create_task(_watch_hotplug())


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
