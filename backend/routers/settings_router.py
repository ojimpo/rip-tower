"""Settings read/write endpoints."""

from fastapi import APIRouter, HTTPException

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


@router.post("/plex/scan")
async def trigger_plex_scan():
    """Manually trigger a Plex library refresh."""
    config = get_config()
    if not (
        config.integrations.plex_url
        and config.integrations.plex_token
        and config.integrations.plex_section_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Plex is not configured (url/token/section_id missing).",
        )

    from backend.services.finalizer import _plex_refresh

    await _plex_refresh()
    return {"status": "triggered"}
