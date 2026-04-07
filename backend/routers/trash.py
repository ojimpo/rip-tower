"""Trash management — list, empty, and move conflicting files."""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_config
from backend.database import get_session
from backend.models import Job, JobMetadata

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trash"])


@router.get("/trash")
async def list_trash():
    """List all items in the trash directory."""
    config = get_config()
    trash_dir = Path(config.output.trash_dir)

    if not trash_dir.exists():
        return {"items": [], "total_size": 0}

    items = []
    total_size = 0
    for sub in sorted(trash_dir.iterdir()):
        if sub.is_dir():
            files = []
            dir_size = 0
            for f in sorted(sub.iterdir()):
                if f.is_file() and not f.name.startswith("._"):
                    size = f.stat().st_size
                    files.append({"name": f.name, "size": size})
                    dir_size += size
            if files:
                items.append({
                    "label": sub.name,
                    "files": files,
                    "total_size": dir_size,
                })
                total_size += dir_size

    return {"items": items, "total_size": total_size}


@router.delete("/trash")
async def empty_trash():
    """Permanently delete all items in the trash."""
    config = get_config()
    trash_dir = Path(config.output.trash_dir)

    if not trash_dir.exists():
        return {"status": "empty", "deleted": 0}

    deleted = 0
    for sub in list(trash_dir.iterdir()):
        if sub.is_dir():
            count = sum(1 for f in sub.rglob("*") if f.is_file())
            shutil.rmtree(sub)
            deleted += count
        elif sub.is_file():
            sub.unlink()
            deleted += 1

    return {"status": "emptied", "deleted": deleted}


@router.delete("/trash/{label}")
async def delete_trash_item(label: str):
    """Delete a specific item from the trash."""
    config = get_config()
    trash_path = Path(config.output.trash_dir) / label

    if not trash_path.exists():
        raise HTTPException(status_code=404, detail="Trash item not found")

    if trash_path.is_dir():
        count = sum(1 for f in trash_path.rglob("*") if f.is_file())
        shutil.rmtree(trash_path)
    else:
        count = 1
        trash_path.unlink()

    return {"status": "deleted", "deleted": count}
