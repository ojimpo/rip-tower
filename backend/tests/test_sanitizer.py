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


# ───────────────── sanitize_candidates: kashidashi cross-check ─────────────────


def _confirmed_evidence(item_id: int) -> str:
    return json.dumps({
        "kashidashi_confirmed": {
            "item_id": item_id, "artist_sim": 1.0, "album_sim": 1.0, "boost": 25,
        }
    }, ensure_ascii=False)


def _recency_evidence(item_id: int) -> str:
    return json.dumps({
        "kashidashi_id": item_id,
        "match": "recency_fallback",
        "days_since_borrow": 0,
        "pool_size": 2,
    }, ensure_ascii=False)


@pytest.mark.asyncio
async def test_kashidashi_mismatch_when_best_lacks_match_but_others_have(
    monkeypatch, async_session_maker,
):
    """Best has no kashidashi tag, but a lower candidate maps to a borrowed
    CD — user is probably holding that other CD, force review."""
    from backend.models import Job, MetadataCandidate, Track

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-mm", drive_id="d", disc_id="dx"))
        for n in range(1, 4):
            s.add(Track(job_id="job-mm", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-mm", source="musicbrainz",
            artist="Wrong", album="Wrong Album", confidence=90,
        ))
        s.add(MetadataCandidate(
            job_id="job-mm", source="kashidashi",
            artist="Right", album="Right Album", confidence=70,
            evidence=_recency_evidence(123),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-mm")
    assert result is not None
    issues = json.loads(result.issues)
    assert "kashidashi_mismatch" in issues
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_kashidashi_ambiguous_when_best_and_other_point_at_different_items(
    monkeypatch, async_session_maker,
):
    """Best matched borrowed CD A, but another candidate matched borrowed CD B —
    the system can't tell which of the user's CDs is on the spindle, force review."""
    from backend.models import Job, MetadataCandidate, Track

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-amb", drive_id="d", disc_id="dx"))
        for n in range(1, 4):
            s.add(Track(job_id="job-amb", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-amb", source="musicbrainz",
            artist="宮本浩次", album="ROMANCE", confidence=90,
            evidence=_confirmed_evidence(799),
        ))
        s.add(MetadataCandidate(
            job_id="job-amb", source="itunes",
            artist="ポケットビスケッツ", album="Thanks", confidence=55,
            evidence=_confirmed_evidence(800),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-amb")
    assert result is not None
    issues = json.loads(result.issues)
    assert "kashidashi_ambiguous" in issues
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_kashidashi_no_flag_when_best_and_others_share_item(
    monkeypatch, async_session_maker,
):
    """Single-CD borrow happy path: best matches the only borrowed CD; a
    kashidashi recency candidate for that same CD is also present. They share
    the item id → no ambiguity, no mismatch."""
    from backend.models import Job, MetadataCandidate, Track

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-ok", drive_id="d", disc_id="dx"))
        for n in range(1, 4):
            s.add(Track(job_id="job-ok", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-ok", source="musicbrainz",
            artist="宮本浩次", album="ROMANCE", confidence=95,
            evidence=_confirmed_evidence(799),
        ))
        s.add(MetadataCandidate(
            job_id="job-ok", source="kashidashi",
            artist="宮本浩次", album="ROMANCE", confidence=70,
            evidence=_recency_evidence(799),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-ok")
    assert result is not None
    issues = json.loads(result.issues or "[]")
    assert "kashidashi_mismatch" not in issues
    assert "kashidashi_ambiguous" not in issues


# ───────────────── Fix A: track-count hard gate ─────────────────

from types import SimpleNamespace  # noqa: E402


def _cand(**kw):
    base = {"track_titles": None, "evidence": None, "source_url": None,
            "artist": None, "album": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_candidate_expected_track_count_from_titles():
    c = _cand(track_titles=json.dumps(["a", "b", "c"], ensure_ascii=False))
    assert sanitizer.candidate_expected_track_count(c) == 3


def test_candidate_expected_track_count_from_evidence():
    c = _cand(evidence=json.dumps({"track_count": 11}))
    assert sanitizer.candidate_expected_track_count(c) == 11


def test_candidate_expected_track_count_unknown():
    assert sanitizer.candidate_expected_track_count(_cand()) is None


@pytest.mark.asyncio
async def test_track_count_mismatch_flagged(monkeypatch, async_session_maker):
    """18-track disc but the chosen release lists 11 tracks → mismatch + review.

    This is the o8maxpvg failure: a recency-fallback 'LOVE' (11tr) text-matched
    onto an 18-track disc must not slip through as confirmed metadata.
    """
    from backend.models import Job, MetadataCandidate, Track
    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-tc", drive_id="d", disc_id="dx"))
        for n in range(1, 19):  # 18 ripped tracks
            s.add(Track(job_id="job-tc", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-tc", source="musicbrainz", artist="菅田将暉",
            album="LOVE", confidence=90,
            track_titles=json.dumps([f"t{i}" for i in range(11)], ensure_ascii=False),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-tc")
    issues = json.loads(result.issues or "[]")
    assert "track_count_mismatch" in issues
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_track_count_match_no_mismatch_flag(monkeypatch, async_session_maker):
    """When the release track count equals the disc's, no mismatch is flagged."""
    from backend.models import Job, MetadataCandidate, Track
    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-tcok", drive_id="d", disc_id="dx"))
        for n in range(1, 12):  # 11 ripped tracks
            s.add(Track(job_id="job-tcok", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-tcok", source="musicbrainz", artist="菅田将暉",
            album="LOVE", confidence=90,
            track_titles=json.dumps([f"t{i}" for i in range(11)], ensure_ascii=False),
            evidence=json.dumps({"match": "toc_submission"}, ensure_ascii=False),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-tcok")
    issues = json.loads(result.issues or "[]")
    assert "track_count_mismatch" not in issues


# ───────────────── Fix C: unanchored-identification guard ─────────────────


def test_candidate_match_kind():
    assert sanitizer._candidate_match_kind(
        _cand(evidence=json.dumps({"match": "toc_submission"}))) == "toc_submission"
    assert sanitizer._candidate_match_kind(_cand()) is None


@pytest.mark.asyncio
async def test_unanchored_identification_flagged(monkeypatch, async_session_maker):
    """Best is a text_search echo of a recency-fallback seed, nothing disc-anchored."""
    from backend.models import Job, MetadataCandidate, Track
    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-un", drive_id="d", disc_id="dx"))
        for n in range(1, 12):  # 11 tracks — matches title count so only the anchor check fires
            s.add(Track(job_id="job-un", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-un", source="musicbrainz", artist="菅田将暉",
            album="LOVE", confidence=90,
            track_titles=json.dumps([f"t{i}" for i in range(11)], ensure_ascii=False),
            evidence=json.dumps({"match": "text_search"}, ensure_ascii=False),
        ))
        s.add(MetadataCandidate(
            job_id="job-un", source="kashidashi", artist="菅田将暉",
            album="LOVE", confidence=55, evidence=_recency_evidence(190),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-un")
    issues = json.loads(result.issues or "[]")
    assert "unanchored_identification" in issues
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_disc_anchored_match_not_flagged_unanchored(monkeypatch, async_session_maker):
    """A disc-anchored (toc_submission) best that matches a borrowed CD is the GOOD
    case — the borrowed disc correctly identified — and must NOT be flagged."""
    from backend.models import Job, MetadataCandidate, Track
    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-anch", drive_id="d", disc_id="dx"))
        for n in range(1, 12):
            s.add(Track(job_id="job-anch", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-anch", source="musicbrainz", artist="菅田将暉",
            album="LOVE", confidence=90,
            track_titles=json.dumps([f"t{i}" for i in range(11)], ensure_ascii=False),
            evidence=json.dumps({"match": "toc_submission"}, ensure_ascii=False),
        ))
        s.add(MetadataCandidate(
            job_id="job-anch", source="kashidashi", artist="菅田将暉",
            album="LOVE", confidence=55, evidence=_recency_evidence(190),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-anch")
    issues = json.loads(result.issues or "[]")
    assert "unanchored_identification" not in issues


# ───────────────── compilation detection (Various Artists album artist) ─────────────────


@pytest.mark.asyncio
async def test_va_track_artists_yield_compilation(monkeypatch, async_session_maker):
    """MusicBrainz now emits "artist / title" per track for Various-Artists discs;
    the sanitizer must flag is_compilation and normalize the album artist to
    "Various Artists" so it isn't tagged to a single performer (Todoist
    6gp628r73WQ8576F)."""
    from backend.models import Job, JobMetadata, MetadataCandidate, Track

    monkeypatch.setattr(sanitizer, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-va", drive_id="d", disc_id="dx"))
        for n in range(1, 4):
            s.add(Track(job_id="job-va", track_num=n))
        s.add(MetadataCandidate(
            job_id="job-va", source="musicbrainz",
            artist="平沢進", album="スナックJUJU", confidence=90,
            track_titles=json.dumps(
                ["平沢進 / T1", "JUJU / T2", "椎名林檎 / T3"], ensure_ascii=False),
            evidence=json.dumps({"match": "toc_submission"}, ensure_ascii=False),
        ))
        await s.commit()

    result = await sanitizer.sanitize_candidates("job-va")
    assert result.is_compilation is True
    assert result.artist == "Various Artists"

    async with async_session_maker() as s:
        from sqlalchemy import select
        tracks = (await s.execute(
            select(Track).where(Track.job_id == "job-va").order_by(Track.track_num)
        )).scalars().all()
    assert [t.artist for t in tracks] == ["平沢進", "JUJU", "椎名林檎"]
    assert [t.title for t in tracks] == ["T1", "T2", "T3"]


# ───────────────── genre selection (no scavenging from mis-matches) ─────────────────


def test_select_genre_prefers_best():
    best = _cand(artist="A", album="B", genre="Rock")
    others = [_cand(artist="A", album="B", genre="Pop")]
    assert sanitizer._select_genre(best, others) == "Rock"


def test_select_genre_falls_back_to_agreeing_candidate():
    """Best (MB) has no genre — adopt it from another source for the SAME release."""
    best = _cand(artist="YUMING", album="Shout at YUMING ROCKS", genre=None)
    others = [
        _cand(artist="YUMING", album="Shout at YUMING ROCKS", genre="ロック"),
    ]
    assert sanitizer._select_genre(best, others) == "ロック"


def test_select_genre_ignores_mismatched_candidate():
    """A genre from an unrelated mis-matched candidate must NOT be scavenged.

    The "Shout at YUMING ROCKS" (rock) disc had a mis-matched iTunes hit for
    "Melky Sedeck / Sister & Brother" (R&B/ソウル); its genre must not leak in
    (Todoist 6gp5wgCCP78G7FCm)."""
    best = _cand(artist="YUMING", album="Shout at YUMING ROCKS", genre=None)
    others = [
        _cand(artist="Melky Sedeck", album="Sister & Brother", genre="R&B／ソウル"),
    ]
    assert sanitizer._select_genre(best, others) == ""
