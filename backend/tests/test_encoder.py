"""Tests for backend.services.encoder — safe_filename and _build_encode_cmd."""

from pathlib import Path

import pytest

from backend.services.encoder import FORMAT_EXT, _build_encode_cmd, safe_filename


class TestSafeFilename:
    """Tests for safe_filename()."""

    def test_normal_filename(self):
        assert safe_filename("hello world") == "hello world"

    def test_removes_illegal_chars(self):
        result = safe_filename('file<>:"/\\|?*name')
        # Should not contain any of: < > " | ? *
        for c in '<>"|?*':
            assert c not in result

    def test_strips_trailing_dots_and_spaces(self):
        assert safe_filename("file. ") == "file"

    def test_strips_leading_dots_and_spaces(self):
        assert safe_filename(". file") == "file"

    def test_empty_after_strip(self):
        assert safe_filename("...") == ""

    def test_japanese_characters(self):
        assert safe_filename("東京タワー") == "東京タワー"

    def test_mixed_content(self):
        result = safe_filename("01 Artist - Title (feat. Guest)")
        assert result == "01 Artist - Title (feat. Guest)"

    def test_colon_replacement(self):
        result = safe_filename("Track: The Beginning")
        assert ":" not in result
        assert "Track" in result
        assert "The Beginning" in result


class TestFormatExt:
    """Tests for FORMAT_EXT mapping."""

    def test_flac_extension(self):
        assert FORMAT_EXT["flac"] == ".flac"

    def test_alac_extension(self):
        assert FORMAT_EXT["alac"] == ".m4a"

    def test_opus_extension(self):
        assert FORMAT_EXT["opus"] == ".opus"

    def test_mp3_extension(self):
        assert FORMAT_EXT["mp3"] == ".mp3"

    def test_wav_extension(self):
        assert FORMAT_EXT["wav"] == ".wav"


class TestBuildEncodeCmd:
    """Tests for _build_encode_cmd()."""

    def test_flac_command(self):
        cmd = _build_encode_cmd("flac", 8, Path("/tmp/in.wav"), Path("/tmp/out.flac"))
        assert cmd == ["flac", "-8", "-o", "/tmp/out.flac", "/tmp/in.wav"]

    def test_flac_quality_5(self):
        cmd = _build_encode_cmd("flac", 5, Path("/tmp/in.wav"), Path("/tmp/out.flac"))
        assert cmd[1] == "-5"

    def test_alac_command(self):
        cmd = _build_encode_cmd("alac", 0, Path("/tmp/in.wav"), Path("/tmp/out.m4a"))
        assert cmd[0] == "ffmpeg"
        assert "-acodec" in cmd
        assert "alac" in cmd
        assert "/tmp/in.wav" in cmd
        assert "/tmp/out.m4a" in cmd

    def test_opus_command(self):
        cmd = _build_encode_cmd("opus", 128, Path("/tmp/in.wav"), Path("/tmp/out.opus"))
        assert cmd[0] == "opusenc"
        assert "--bitrate=128" in cmd

    def test_mp3_command(self):
        cmd = _build_encode_cmd("mp3", 2, Path("/tmp/in.wav"), Path("/tmp/out.mp3"))
        assert cmd[0] == "lame"
        assert "-V2" in cmd

    def test_wav_returns_none(self, tmp_path):
        """WAV format copies the file and returns None (no encode command)."""
        src = tmp_path / "in.wav"
        dst = tmp_path / "out.wav"
        src.write_bytes(b"RIFF" + b"\x00" * 100)

        result = _build_encode_cmd("wav", 0, src, dst)
        assert result is None
        assert dst.exists()

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="Unknown format"):
            _build_encode_cmd("aac", 0, Path("/tmp/in.wav"), Path("/tmp/out.aac"))
