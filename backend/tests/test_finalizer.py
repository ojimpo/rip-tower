"""Tests for finalizer helpers — conflict detection."""

from __future__ import annotations

import pytest

from backend.services.finalizer import _find_existing_audio


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
