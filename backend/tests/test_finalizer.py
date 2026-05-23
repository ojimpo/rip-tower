"""Tests for finalizer helpers — conflict detection and Plex path mapping."""

from __future__ import annotations

import pytest

from backend.services.finalizer import _find_existing_audio, _translate_to_plex_path


def test_find_existing_audio_detects_flac(tmp_path):
    """FLAC files at the target path must surface as conflicts.

    The old behavior skipped FLAC — letting a re-rip of the same disc
    silently overwrite a previously finalized album. Confirm FLAC is now
    included alongside lossy formats.
    """
    (tmp_path / "01 Artist - Track.flac").write_bytes(b"x")
    (tmp_path / "02 Artist - Track.flac").write_bytes(b"x")

    found = _find_existing_audio(tmp_path)
    assert len(found) == 2
    assert all(f.suffix == ".flac" for f in found)


def test_find_existing_audio_detects_mixed_formats(tmp_path):
    (tmp_path / "old.mp3").write_bytes(b"x")
    (tmp_path / "old.m4a").write_bytes(b"x")
    (tmp_path / "old.flac").write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"x")  # non-audio, should be ignored

    found = sorted(f.name for f in _find_existing_audio(tmp_path))
    assert found == ["old.flac", "old.m4a", "old.mp3"]


def test_find_existing_audio_missing_dir_returns_empty(tmp_path):
    assert _find_existing_audio(tmp_path / "does-not-exist") == []


def _plex_paths(monkeypatch, host_root: str, plex_root: str) -> None:
    """Patch get_config to return the given host→Plex mapping."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        integrations=SimpleNamespace(
            plex_music_host_root=host_root,
            plex_music_plex_root=plex_root,
        )
    )
    monkeypatch.setattr("backend.services.finalizer.get_config", lambda: cfg)


def test_translate_returns_none_when_mapping_unset(monkeypatch):
    _plex_paths(monkeypatch, "", "")
    assert _translate_to_plex_path("/mnt/media/music/Cocco/best") is None


def test_translate_returns_none_when_only_one_side_set(monkeypatch):
    _plex_paths(monkeypatch, "/mnt/media/music", "")
    assert _translate_to_plex_path("/mnt/media/music/Cocco/best") is None
    _plex_paths(monkeypatch, "", "/media/music")
    assert _translate_to_plex_path("/mnt/media/music/Cocco/best") is None


def test_translate_replaces_host_prefix_with_plex_prefix(monkeypatch):
    _plex_paths(monkeypatch, "/mnt/media/music", "/media/music")
    assert (
        _translate_to_plex_path("/mnt/media/music/Cocco/ザ・ベスト盤 [DISC2]")
        == "/media/music/Cocco/ザ・ベスト盤 [DISC2]"
    )


def test_translate_handles_trailing_slashes_in_config(monkeypatch):
    _plex_paths(monkeypatch, "/mnt/media/music/", "/media/music/")
    assert (
        _translate_to_plex_path("/mnt/media/music/Cocco/best")
        == "/media/music/Cocco/best"
    )


def test_translate_returns_none_when_path_outside_host_root(monkeypatch):
    # /mnt/media/audio is the incoming dir, not the library. Refresh
    # would be a no-op there, so refuse to map.
    _plex_paths(monkeypatch, "/mnt/media/music", "/media/music")
    assert _translate_to_plex_path("/mnt/media/audio/_incoming/xyz") is None


def test_translate_does_not_partial_match_prefix(monkeypatch):
    # /mnt/media/music-old must not match /mnt/media/music.
    _plex_paths(monkeypatch, "/mnt/media/music", "/media/music")
    assert _translate_to_plex_path("/mnt/media/music-old/foo") is None


def test_translate_accepts_path_object(monkeypatch):
    from pathlib import Path
    _plex_paths(monkeypatch, "/mnt/media/music", "/media/music")
    assert (
        _translate_to_plex_path(Path("/mnt/media/music/Cocco/best"))
        == "/media/music/Cocco/best"
    )
