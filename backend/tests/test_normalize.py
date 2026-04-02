"""Tests for backend.metadata.normalize module."""

import pytest

from backend.metadata.normalize import (
    detect_disc_hint,
    extract_disc_info,
    fullwidth_to_halfwidth,
    norm,
    normalize_album_base,
    normalize_various_artists,
    similarity,
)


class TestNorm:
    """Tests for norm() — normalize text for fuzzy matching."""

    def test_empty_string(self):
        assert norm("") == ""

    def test_none_input(self):
        assert norm(None) == ""

    def test_lowercase(self):
        assert norm("ABC") == "abc"

    def test_fullwidth_to_halfwidth(self):
        # Fullwidth ABC -> halfwidth abc
        assert norm("ABC") == "abc"

    def test_strips_disc_suffix(self):
        result = norm("Album DISC 1")
        assert "disc" not in result
        assert "1" not in result or result == norm("Album")

    def test_strips_cd_suffix(self):
        result = norm("Album CD2")
        assert "cd" not in result

    def test_strips_bracketed_disc(self):
        result = norm("Album [Disc 3]")
        assert "disc" not in result

    def test_collapses_punctuation(self):
        assert norm("hello, world!") == "helloworld"

    def test_katakana_to_hiragana(self):
        # カタカナ -> ひらがな
        assert norm("カタカナ") == norm("かたかな")

    def test_wave_dash_normalization(self):
        # Both wave dash variants should normalize the same
        assert norm("A\u301cB") == norm("A\uff5eB")

    def test_preserves_kanji(self):
        result = norm("東京タワー")
        assert "東京" in result

    def test_preserves_numbers(self):
        result = norm("track 01")
        assert "01" in result

    def test_fullwidth_numbers(self):
        # Fullwidth 1 (U+FF11) -> halfwidth 1
        assert norm("１２３") == "123"


class TestSimilarity:
    """Tests for similarity() — substring-based similarity."""

    def test_identical_strings(self):
        assert similarity("hello", "hello") == 1.0

    def test_empty_string(self):
        assert similarity("", "hello") == 0.0
        assert similarity("hello", "") == 0.0

    def test_both_empty(self):
        assert similarity("", "") == 0.0

    def test_substring_match(self):
        assert similarity("abc", "abcdef") == 0.8

    def test_reverse_substring(self):
        assert similarity("abcdef", "abc") == 0.8

    def test_partial_overlap(self):
        score = similarity("abc", "bcd")
        assert 0 < score < 1.0

    def test_no_overlap(self):
        score = similarity("xyz", "abc")
        assert score == 0.0

    def test_case_insensitive(self):
        assert similarity("Hello", "hello") == 1.0

    def test_japanese_matching(self):
        assert similarity("東京タワー", "東京タワー") == 1.0


class TestFullwidthToHalfwidth:
    """Tests for fullwidth_to_halfwidth()."""

    def test_fullwidth_ascii(self):
        assert fullwidth_to_halfwidth("ＡＢＣ") == "ABC"

    def test_fullwidth_numbers(self):
        assert fullwidth_to_halfwidth("１２３") == "123"

    def test_already_halfwidth(self):
        assert fullwidth_to_halfwidth("ABC123") == "ABC123"

    def test_mixed(self):
        assert fullwidth_to_halfwidth("ＡBC１23") == "ABC123"

    def test_empty(self):
        assert fullwidth_to_halfwidth("") == ""

    def test_japanese_unaffected(self):
        # Hiragana/katakana should pass through
        result = fullwidth_to_halfwidth("あいうえお")
        assert result == "あいうえお"


class TestExtractDiscInfo:
    """Tests for extract_disc_info()."""

    def test_disc_suffix(self):
        base, num = extract_disc_info("Album DISC 1")
        assert base == "Album"
        assert num == 1

    def test_cd_suffix(self):
        base, num = extract_disc_info("Album CD2")
        assert base == "Album"
        assert num == 2

    def test_bracketed_disc(self):
        base, num = extract_disc_info("Album [Disc 3]")
        assert base == "Album"
        assert num == 3

    def test_no_disc_info(self):
        base, num = extract_disc_info("Regular Album")
        assert base == "Regular Album"
        assert num is None

    def test_disc_with_dash(self):
        base, num = extract_disc_info("Album - Disc 1")
        assert base == "Album"
        assert num == 1

    def test_empty_string(self):
        base, num = extract_disc_info("")
        assert base == ""
        assert num is None

    def test_case_insensitive(self):
        base, num = extract_disc_info("Album disc 4")
        assert num == 4

    def test_cd_uppercase(self):
        base, num = extract_disc_info("Album CD 5")
        assert num == 5


class TestDetectDiscHint:
    """Tests for detect_disc_hint()."""

    def test_has_disc(self):
        assert detect_disc_hint("Album DISC 1") is True

    def test_has_cd(self):
        assert detect_disc_hint("Album CD2") is True

    def test_no_disc(self):
        assert detect_disc_hint("Regular Album") is False


class TestNormalizeAlbumBase:
    """Tests for normalize_album_base()."""

    def test_strips_disc(self):
        assert normalize_album_base("Album DISC 1") == "Album"

    def test_no_disc(self):
        assert normalize_album_base("Album") == "Album"


class TestNormalizeVariousArtists:
    """Tests for normalize_various_artists()."""

    def test_various_artists(self):
        assert normalize_various_artists("Various Artists") == "Various Artists"

    def test_various_artist_singular(self):
        assert normalize_various_artists("Various Artist") == "Various Artists"

    def test_va(self):
        assert normalize_various_artists("V.A.") == "Various Artists"

    def test_va_no_dots(self):
        assert normalize_various_artists("VA") == "Various Artists"

    def test_various(self):
        assert normalize_various_artists("various") == "Various Artists"

    def test_normal_artist(self):
        assert normalize_various_artists("Radiohead") == "Radiohead"

    def test_artist_with_various_in_name(self):
        # Should NOT normalize if it's not exactly "Various Artists"
        assert normalize_various_artists("The Various") == "The Various"

    def test_whitespace(self):
        assert normalize_various_artists("  Various Artists  ") == "Various Artists"
