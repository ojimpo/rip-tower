"""Tests for backend.config module."""

import os
from pathlib import Path

import pytest
import yaml


class TestAppConfigDefaults:
    """Test AppConfig default values."""

    def test_default_general(self):
        from backend.config import AppConfig

        config = AppConfig()
        assert config.general.auto_approve_threshold == 85
        assert config.general.reminder_initial_hours == 6
        assert config.general.reminder_interval_hours == 24

    def test_default_output(self):
        from backend.config import AppConfig

        config = AppConfig()
        assert config.output.format == "flac"
        assert config.output.quality == 8
        assert config.output.music_dir == "/mnt/media/music"
        assert config.output.incoming_dir == "/mnt/media/audio/_incoming"
        assert config.output.folder_template == "{artist}/{album}"
        assert config.output.file_template == "{track_num} {artist} - {title}"

    def test_default_integrations(self):
        from backend.config import AppConfig

        config = AppConfig()
        assert config.integrations.discord_webhook == ""
        assert config.integrations.discogs_token == ""
        assert config.integrations.plex_section_id is None
        assert config.integrations.llm_model == "haiku"
        assert config.integrations.kashidashi_url == "http://kashidashi-app-web-1:18080"

    def test_custom_values(self):
        from backend.config import AppConfig, GeneralConfig, OutputConfig

        config = AppConfig(
            general=GeneralConfig(auto_approve_threshold=50),
            output=OutputConfig(format="mp3", quality=5),
        )
        assert config.general.auto_approve_threshold == 50
        assert config.output.format == "mp3"
        assert config.output.quality == 5


class TestLoadSaveConfig:
    """Test load_config and save_config with tmp_path."""

    def test_load_creates_default_file(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        # Clear singleton
        monkeypatch.setattr(config_mod, "_config", None)

        config = config_mod.load_config()
        assert config_path.exists()
        assert config.output.format == "flac"

    def test_save_and_reload(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        config = config_mod.AppConfig()
        config.output.format = "opus"
        config.general.auto_approve_threshold = 42
        config_mod.save_config(config)

        loaded = config_mod.load_config()
        assert loaded.output.format == "opus"
        assert loaded.general.auto_approve_threshold == 42

    def test_yaml_format(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        config_mod.save_config(config_mod.AppConfig())

        with open(config_path) as f:
            data = yaml.safe_load(f)

        assert "general" in data
        assert "output" in data
        assert "integrations" in data
        assert data["output"]["format"] == "flac"

    def test_get_config_singleton(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        c1 = config_mod.get_config()
        c2 = config_mod.get_config()
        assert c1 is c2

    def test_reload_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        import backend.config as config_mod

        monkeypatch.setattr(config_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(config_mod, "_config", None)

        c1 = config_mod.get_config()

        # Modify file directly
        c1_copy = config_mod.AppConfig()
        c1_copy.output.format = "mp3"
        config_mod.save_config(c1_copy)

        c2 = config_mod.reload_config()
        assert c2.output.format == "mp3"
        assert c2 is not c1
