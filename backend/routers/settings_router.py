"""Settings read/write endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import AppConfig, get_config, reload_config, save_config

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppConfig)
async def get_settings():
    """Get current settings."""
    return get_config()


@router.put("/settings", response_model=AppConfig)
async def update_settings(config: AppConfig):
    """Update settings (writes to config.yaml)."""
    save_config(config)
    return reload_config()
