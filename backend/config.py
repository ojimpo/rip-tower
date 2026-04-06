"""Application configuration — loads from YAML config file."""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/data/config.yaml"))
DATA_DIR = CONFIG_PATH.parent

DEFAULT_CONFIG = {
    "general": {
        "auto_approve_threshold": 85,
        "reminder_initial_hours": 6,
        "reminder_interval_hours": 24,
        "base_url": "",
    },
    "output": {
        "format": "flac",
        "quality": 8,
        "music_dir": "/mnt/media/music",
        "incoming_dir": "/mnt/media/audio/_incoming",
        "folder_template": "{artist}/{album}",
        "file_template": "{track_num} {artist} - {title}",
    },
    "integrations": {
        "discord_webhook": "",
        "discogs_token": "",
        "musixmatch_token": "",
        "plex_url": "",
        "plex_section_id": None,
        "llm_api_key": "",
        "llm_model": "haiku",
        "kashidashi_url": "http://kashidashi-app-web-1:18080",
    },
}


class GeneralConfig(BaseModel):
    auto_approve_threshold: int = 85
    reminder_initial_hours: int = 6
    reminder_interval_hours: int = 24
    eject_reminder_minutes: int = 10
    base_url: str = ""


class OutputConfig(BaseModel):
    format: str = "flac"
    quality: int = 8
    music_dir: str = "/mnt/media/music"
    incoming_dir: str = "/mnt/media/audio/_incoming"
    folder_template: str = "{artist}/{album}"
    file_template: str = "{track_num} {artist} - {title}"


class IntegrationsConfig(BaseModel):
    discord_webhook: str = ""
    discogs_token: str = ""
    musixmatch_token: str = ""
    plex_url: str = ""
    plex_section_id: Optional[int] = None
    llm_api_key: str = ""
    llm_model: str = "haiku"
    kashidashi_url: str = "http://kashidashi-app-web-1:18080"


class AppConfig(BaseModel):
    general: GeneralConfig = GeneralConfig()
    output: OutputConfig = OutputConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()


def load_config() -> AppConfig:
    """Load config from YAML file, creating with defaults if missing."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}
        return AppConfig(**data)

    # First run — create default config
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_config(AppConfig())
    return AppConfig()


def save_config(config: AppConfig) -> None:
    """Write config back to YAML file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(
            config.model_dump(mode="json"),
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


# Singleton — reloaded on settings save
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    global _config
    _config = load_config()
    return _config
