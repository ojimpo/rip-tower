"""Tests for the metadata sanitizer's track-title selection and annotation detection."""

from __future__ import annotations

import json

import pytest

from backend.metadata import sanitizer


# ─────────────────────────── _has_annotation ───────────────────────────


@pytest.mark.parametrize("title", [
    "innocent world 『【es】 Mr.Children in FILM / 1995 Tour Atomic Heart』",
    "終わりなき旅 『Mr.Children STADIUM TOUR 2011 SENSE -in the field-』",
    "hypnosis (日本テレビ系ドラマ「トッカン 特別国税徴収官」主題歌)",
    "Marshmallow day (資生堂「マキアージュ」CMソング)",
    "進化論 (日本テレビ「NEWS ZERO」テーマ曲)",
    "口笛 (LIVE FILM 『Mr.Children REFLECTION』)",
    "hypnosis (Remastering 2022)",
])
def test_has_annotation_positive(title):
    assert sanitizer._has_annotation(title)


@pytest.mark.parametrize("title", [
    "hypnosis",
    "祈り 〜涙の軌道",
    "REM",
    "End of the day",
    "未完",
    "葉加瀬太郎 (Taro Hakase)",  # romanization variant — different concern
    "I (Single Edit)",  # ambiguous; "Edit" present but parens too short
])
def test_has_annotation_negative(title):
    assert not sanitizer._has_annotation(title)


# ───────────────────────── _pick_best_track_titles ─────────────────────────


def _make_candidate(source: str, confidence: int, titles: list[str] | None):
    """Build a minimal MetadataCandidate-like object for scoring."""
    class _C:
        pass

    c = _C()
    c.source = source
    c.confidence = confidence
    c.track_titles = json.dumps(titles, ensure_ascii=False) if titles else None
    return c


def test_pick_best_prefers_clean_over_annotated_at_equal_count():
    """Even at lower confidence, a clean candidate beats an annotated one."""
    cddb_titles = [
        "hypnosis (日本テレビ系ドラマ「トッカン 特別国税徴収官」主題歌)",
        "REM (映画「リアル~完全なる首長竜の日~」)",
        "Marshmallow day (資生堂「マキアージュ」CMソング)",
    ]
    itunes_titles = ["hypnosis", "REM", "Marshmallow day"]

    candidates = [
        _make_candidate("cddb", 60, cddb_titles),
        _make_candidate("itunes", 50, itunes_titles),
    ]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=3)
    assert result is not None
    assert result["source"] == "itunes"
    assert result["titles"] == itunes_titles


def test_pick_best_track_count_match_dominates_confidence():
    """Higher-conf candidate with wrong track count loses to matching count."""
    high_conf_wrong_count = ["a", "b"]
    low_conf_right_count = ["a", "b", "c"]
    candidates = [
        _make_candidate("musicbrainz", 90, high_conf_wrong_count),
        _make_candidate("cddb", 60, low_conf_right_count),
    ]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=3)
    assert result["titles"] == low_conf_right_count


def test_pick_best_placeholder_disqualified():
    """Placeholder titles (Track NN) score way below real titles."""
    placeholder = ["Track 1", "Track 2", "Track 3"]
    real = ["foo", "bar", "baz"]
    candidates = [
        _make_candidate("cddb", 60, placeholder),
        _make_candidate("hmv", 40, real),
    ]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=3)
    assert result["titles"] == real


def test_pick_best_returns_none_when_no_titles():
    candidates = [
        _make_candidate("cddb", 60, None),
        _make_candidate("itunes", 50, None),
    ]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=3)
    assert result is None


def test_pick_best_annotation_ratio_reported():
    """Annotation ratio surfaces so caller can flag the issue."""
    annotated = [
        "innocent world 『tour A』",
        "Dance Dance Dance 『tour A』",
        "抱きしめたい 『tour B』",
        "CROSS ROAD",  # one clean
    ]
    candidates = [_make_candidate("cddb", 60, annotated)]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=4)
    assert 0.7 <= result["annotation_ratio"] <= 0.8


def test_pick_best_source_preference_breaks_ties():
    """When everything else is equal, source preference picks the winner."""
    titles = ["a", "b", "c"]
    candidates = [
        _make_candidate("cddb", 60, titles),
        _make_candidate("itunes", 60, titles),
        _make_candidate("musicbrainz", 60, titles),
    ]
    result = sanitizer._pick_best_track_titles(candidates, expected_count=3)
    assert result["source"] == "musicbrainz"


# ───────────────── sanitize_candidates: no-candidate placeholder ─────────────────


@pytest.mark.asyncio
async def test_sanitize_candidates_creates_placeholder_when_no_candidates(
    monkeypatch, async_session_maker,
):
    """When resolve produces zero candidates, a placeholder JobMetadata row
    must still exist so manual edits via PUT /metadata work and the row can
    be carried through review/approve."""
    from backend.models import Job, JobMetadata

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-empty", drive_id="d", disc_id="disc-empty"))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-empty")
    assert result is None  # contract: caller's `if best:` branches stay skipped

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "job-empty")

    assert meta is not None
    assert meta.artist is None
    assert meta.album is None
    assert meta.confidence == 0
    assert meta.source == "none"
    assert meta.needs_review is True
    assert json.loads(meta.issues) == ["no_metadata"]


@pytest.mark.asyncio
async def test_sanitize_candidates_no_candidates_preserves_existing_disc_info(
    monkeypatch, async_session_maker,
):
    """A pre-existing JobMetadata row from job creation (disc_number/total_discs)
    must be kept; we only stamp the no-metadata flags on top."""
    from backend.models import Job, JobMetadata

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-disc2", drive_id="d", disc_id="disc-disc2"))
        s.add(JobMetadata(
            job_id="job-disc2",
            disc_number=2,
            total_discs=3,
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-disc2")
    assert result is None

    async with async_session_maker() as s:
        meta = await s.get(JobMetadata, "job-disc2")

    assert meta.disc_number == 2
    assert meta.total_discs == 3
    assert meta.needs_review is True
    assert json.loads(meta.issues) == ["no_metadata"]
