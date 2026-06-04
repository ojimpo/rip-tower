"""Tests for the metadata resolver's two-phase orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.metadata import resolver
from backend.models import Job, JobMetadata, MetadataCandidate


@pytest.mark.asyncio
async def test_enrich_hints_extracts_artist_and_album_from_phase1(
    monkeypatch, async_session_maker
):
    """After Phase 1, _enrich_hints should pull artist/album from the top
    disc-ID candidate so Phase 2 text-search sources have something to query."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-1", drive_id="d", disc_id="disc-1"))
        s.add(MetadataCandidate(
            job_id="job-1",
            source="cddb",
            artist="Mr.Children",
            album="Mr.Children 2011 – 2015",
            confidence=60,
        ))
        s.add(MetadataCandidate(
            job_id="job-1",
            source="kashidashi",
            artist=None,
            album="Mr.Children 2011 - 2015",
            confidence=80,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints("job-1", hints=None)
    assert enriched["artist"] == "Mr.Children"
    # Higher-confidence kashidashi album wins
    assert "Mr.Children 2011" in enriched["title"]


@pytest.mark.asyncio
async def test_enrich_hints_preserves_caller_hints(monkeypatch, async_session_maker):
    """Original hints (e.g. catalog from filename) take precedence."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-2", drive_id="d", disc_id="disc-2"))
        s.add(MetadataCandidate(
            job_id="job-2",
            source="cddb",
            artist="Cddb Artist",
            album="Cddb Album",
            confidence=60,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints(
        "job-2",
        hints={"artist": "User Provided", "catalog": "ABC-123"},
    )
    assert enriched["artist"] == "User Provided"  # caller hint wins
    assert enriched["catalog"] == "ABC-123"
    assert enriched["title"] == "Cddb Album"  # filled from candidate


@pytest.mark.asyncio
async def test_enrich_hints_strips_disc_suffix_and_extracts_disc_number(
    monkeypatch, async_session_maker,
):
    """Album names with disc suffixes like '[Disc 2]' should be cleaned for
    text search; disc_number gets exposed for downstream sources."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async with async_session_maker() as s:
        s.add(Job(id="job-3", drive_id="d", disc_id="disc-3"))
        s.add(MetadataCandidate(
            job_id="job-3",
            source="cddb",
            artist="Some Artist",
            album="Some Album [Disc 2]",
            confidence=60,
        ))
        await s.commit()

    enriched = await resolver._enrich_hints("job-3", hints=None)
    assert enriched["title"] == "Some Album"
    assert enriched["disc_number"] == 2


@pytest.mark.asyncio
async def test_enrich_hints_no_candidates_returns_input(monkeypatch, async_session_maker):
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    enriched = await resolver._enrich_hints("nonexistent-job", hints={"foo": "bar"})
    assert enriched == {"foo": "bar"}


async def _noop_broadcast(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_auto_match_new_group_resolves_disc_number_collision(
    monkeypatch, async_session_maker,
):
    """Two siblings with the same album both tagged disc_number=1 (because MB
    can't tell which medium they actually are) should not both end up labeled
    disc 1 — the older job keeps the slot and the newer one is bumped to 2."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    older = datetime.now(timezone.utc) - timedelta(minutes=30)
    newer = older + timedelta(seconds=30)

    async with async_session_maker() as s:
        s.add(Job(id="disc-a", drive_id="d1", disc_id="aaa", created_at=older))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        s.add(Job(id="disc-b", drive_id="d2", disc_id="bbb", created_at=newer))
        s.add(JobMetadata(
            job_id="disc-b",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-b")

    async with async_session_maker() as s:
        a = await s.get(JobMetadata, "disc-a")
        b = await s.get(JobMetadata, "disc-b")
        ja = await s.get(Job, "disc-a")
        jb = await s.get(Job, "disc-b")
    assert ja.album_group is not None and ja.album_group == jb.album_group
    assert {a.disc_number, b.disc_number} == {1, 2}
    # Older job wins disc 1
    assert a.disc_number == 1
    assert b.disc_number == 2


@pytest.mark.asyncio
async def test_auto_match_join_existing_group_bumps_collision(
    monkeypatch, async_session_maker,
):
    """A disc joining an existing group while still tagged disc_number=1 must
    be reassigned if disc 1 is already taken in that group."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with async_session_maker() as s:
        # Existing group already has disc 1 claimed
        s.add(Job(
            id="disc-a", drive_id="d1", disc_id="aaa",
            created_at=t0, album_group="grp-1",
        ))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="嵐",
            album="ARASHI 5×10",
            album_base="ARASHI 5×10",
            disc_number=1,
            total_discs=3,
            confidence=70,
            source="musicbrainz",
        ))
        # Newcomer also tagged disc 1
        s.add(Job(
            id="disc-c", drive_id="d3", disc_id="ccc",
            created_at=t0 + timedelta(seconds=60),
        ))
        s.add(JobMetadata(
            job_id="disc-c",
            artist="嵐",
            album="ARASHI 5×10",
            album_base="ARASHI 5×10",
            disc_number=1,
            total_discs=3,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-c")

    async with async_session_maker() as s:
        jc = await s.get(Job, "disc-c")
        mc = await s.get(JobMetadata, "disc-c")
        ma = await s.get(JobMetadata, "disc-a")
    assert jc.album_group == "grp-1"
    assert ma.disc_number == 1  # incumbent unchanged
    assert mc.disc_number == 2  # newcomer bumped


@pytest.mark.asyncio
async def test_auto_match_preserves_distinct_disc_numbers(
    monkeypatch, async_session_maker,
):
    """When sources correctly identify distinct disc numbers, leave them alone."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with async_session_maker() as s:
        s.add(Job(id="disc-a", drive_id="d1", disc_id="aaa", created_at=t0))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="A",
            album="B",
            album_base="B",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        s.add(Job(id="disc-b", drive_id="d2", disc_id="bbb",
                  created_at=t0 + timedelta(seconds=30)))
        s.add(JobMetadata(
            job_id="disc-b",
            artist="A",
            album="B",
            album_base="B",
            disc_number=2,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await resolver._auto_match_album_group("disc-b")

    async with async_session_maker() as s:
        a = await s.get(JobMetadata, "disc-a")
        b = await s.get(JobMetadata, "disc-b")
    assert a.disc_number == 1
    assert b.disc_number == 2


@pytest.mark.asyncio
async def test_auto_match_concurrent_resolution_keeps_group_unified(
    monkeypatch, async_session_maker,
):
    """Regression for the Singles II two-disc split observed on 2026-05-07.

    Two sibling discs from the same album finish metadata resolution almost
    simultaneously (jobs 8bovmxq7 and ofgncm2c were 14 seconds apart). If
    _auto_match_album_group runs concurrently for both, each invocation can
    read the other as ungrouped, mint its own UUID, and overwrite the
    sibling's album_group — leaving the two discs in *different* groups.

    This test exercises the concurrent path with asyncio.gather and asserts
    that both discs end up in the same group regardless of scheduling.
    """
    monkeypatch.setattr(resolver, "async_session", async_session_maker)
    monkeypatch.setattr(resolver, "broadcast", _noop_broadcast)

    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with async_session_maker() as s:
        s.add(Job(id="disc-a", drive_id="d1", disc_id="aaa", created_at=t0))
        s.add(JobMetadata(
            job_id="disc-a",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=1,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        s.add(Job(
            id="disc-b", drive_id="d2", disc_id="bbb",
            created_at=t0 + timedelta(seconds=14),
        ))
        s.add(JobMetadata(
            job_id="disc-b",
            artist="中島みゆき",
            album="Singles II",
            album_base="Singles II",
            disc_number=2,
            total_discs=2,
            confidence=70,
            source="musicbrainz",
        ))
        await s.commit()

    await asyncio.gather(
        resolver._auto_match_album_group("disc-a"),
        resolver._auto_match_album_group("disc-b"),
    )

    async with async_session_maker() as s:
        ja = await s.get(Job, "disc-a")
        jb = await s.get(Job, "disc-b")

    assert ja.album_group is not None
    assert jb.album_group is not None
    assert ja.album_group == jb.album_group, (
        f"Concurrent _auto_match_album_group split the album into different "
        f"groups: disc-a={ja.album_group} disc-b={jb.album_group}"
    )


# ───────── _boost_kashidashi_matches ─────────


def _kashidashi_item(**overrides):
    base = {
        "id": 1,
        "artist": "宮本浩次",
        "title": "ROMANCE",
        "metadata_artist": None,
        "metadata_album": None,
        "metadata_track_count": None,
        "borrowed_date": datetime.now(timezone.utc).date().isoformat(),
        "returned_at": None,
        "ripped_at": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_boost_flips_tied_disc_id_candidates(
    monkeypatch, async_session_maker,
):
    """Three MB candidates tied at conf=90 from a disc-ID collision: the one
    matching a borrowed CD should win after the boost."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return [_kashidashi_item(id=42, artist="宮本浩次", title="ROMANCE")]

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-tie", drive_id="d", disc_id="dx"))
        for i, (artist, album) in enumerate([
            ("Crystal Lewis", "Simply the Best"),
            ("The Jamgrass Slammers", "JamGrass: A Phish Tribute"),
            ("宮本浩次", "ROMANCE"),
        ]):
            s.add(MetadataCandidate(
                job_id="job-tie", source="musicbrainz",
                artist=artist, album=album, confidence=90,
            ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-tie")

    async with async_session_maker() as s:
        from sqlalchemy import select
        result = await s.execute(
            select(MetadataCandidate)
            .where(MetadataCandidate.job_id == "job-tie")
            .order_by(MetadataCandidate.confidence.desc())
        )
        cands = list(result.scalars())

    assert cands[0].artist == "宮本浩次"
    assert cands[0].confidence > 90  # boosted past the tie
    assert "kashidashi_confirmed" in (cands[0].evidence or "")
    # Non-matching candidates stay at 90
    others = [c for c in cands if c.artist != "宮本浩次"]
    assert all(c.confidence == 90 for c in others)


@pytest.mark.asyncio
async def test_boost_tags_each_match_with_its_borrowed_item(
    monkeypatch, async_session_maker,
):
    """For the xtc096hn case (MB false-positive ROMANCE at 65 vs iTunes correct
    THANKS at 30) the +25 boost alone can't close the gap. What matters is that
    each candidate's evidence records which borrowed item it maps to — sanitize
    uses those item ids to raise kashidashi_ambiguous when best and a lower
    candidate point at *different* CDs in the user's hands."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return [
            _kashidashi_item(id=799, artist="宮本浩次", title="ROMANCE"),
            _kashidashi_item(id=800, artist="ポケットビスケッツ", title="THANKS"),
        ]

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-xtc", drive_id="d", disc_id="dx2"))
        s.add(MetadataCandidate(
            job_id="job-xtc", source="musicbrainz",
            artist="宮本浩次", album="ROMANCE", confidence=65,
        ))
        s.add(MetadataCandidate(
            job_id="job-xtc", source="itunes",
            artist="ポケットビスケッツ", album="Thanks", confidence=30,
        ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-xtc")

    async with async_session_maker() as s:
        from sqlalchemy import select
        result = await s.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-xtc")
        )
        by_source = {c.source: c for c in result.scalars()}

    import json as _json
    mb_ev = _json.loads(by_source["musicbrainz"].evidence)
    it_ev = _json.loads(by_source["itunes"].evidence)
    assert mb_ev["kashidashi_confirmed"]["item_id"] == 799
    assert it_ev["kashidashi_confirmed"]["item_id"] == 800
    assert by_source["musicbrainz"].confidence == 90  # 65 + 25
    assert by_source["itunes"].confidence == 55       # 30 + 25


@pytest.mark.asyncio
async def test_boost_skips_when_no_items(monkeypatch, async_session_maker):
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return []

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-none", drive_id="d", disc_id="dx"))
        s.add(MetadataCandidate(
            job_id="job-none", source="musicbrainz",
            artist="A", album="B", confidence=80,
        ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-none")

    async with async_session_maker() as s:
        from sqlalchemy import select
        result = await s.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-none")
        )
        cands = list(result.scalars())

    assert cands[0].confidence == 80
    assert cands[0].evidence is None


@pytest.mark.asyncio
async def test_boost_excludes_kashidashi_source_candidates(
    monkeypatch, async_session_maker,
):
    """kashidashi-source candidates already encode the library evidence in their
    base confidence — boosting them again would compound the same signal."""
    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return [_kashidashi_item(id=1, artist="宮本浩次", title="ROMANCE")]

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-k", drive_id="d", disc_id="dx"))
        s.add(MetadataCandidate(
            job_id="job-k", source="kashidashi",
            artist="宮本浩次", album="ROMANCE", confidence=70,
        ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-k")

    async with async_session_maker() as s:
        from sqlalchemy import select
        result = await s.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-k")
        )
        c = list(result.scalars())[0]

    assert c.confidence == 70  # untouched


@pytest.mark.asyncio
async def test_boost_penalizes_conflicting_disc_anchored_candidate(
    monkeypatch, async_session_maker,
):
    """TOC collision: a disc-anchored MB candidate (Various Artists comp) matches
    no borrowed CD, while the user is holding JUJU. The colliding candidate must
    be knocked below auto-approve so review leads with the borrowed CD, not the
    wrong release confirmed at high confidence (Todoist 6gp5wg5vFMRmvVmF)."""
    import json as _json

    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return [_kashidashi_item(id=55, artist="JUJU", title="スナックJUJU")]

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-coll", drive_id="d", disc_id="e60dda0f"))
        # Disc-anchored MB candidate from a TOC collision — wrong album.
        s.add(MetadataCandidate(
            job_id="job-coll", source="musicbrainz",
            artist="Various Artists", album="Hit Summer Now", confidence=90,
            evidence=_json.dumps({"match": "toc_submission"}, ensure_ascii=False),
        ))
        # The borrowed CD the user actually holds, surfaced by kashidashi.
        s.add(MetadataCandidate(
            job_id="job-coll", source="kashidashi",
            artist="JUJU", album="スナックJUJU", confidence=70,
            evidence=_json.dumps({
                "kashidashi_id": 55, "match": "recency_fallback",
            }, ensure_ascii=False),
        ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-coll")

    async with async_session_maker() as s:
        from sqlalchemy import select
        result = await s.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-coll")
        )
        by_source = {c.source: c for c in result.scalars()}

    assert by_source["musicbrainz"].confidence == resolver._KASHIDASHI_CONFLICT_FLOOR
    assert "kashidashi_conflict" in (by_source["musicbrainz"].evidence or "")
    assert by_source["kashidashi"].confidence == 70  # borrowed CD untouched
    # Borrowed CD now outranks the colliding disc-anchored candidate.
    assert by_source["kashidashi"].confidence > by_source["musicbrainz"].confidence


@pytest.mark.asyncio
async def test_boost_does_not_penalize_without_borrowed_candidate(
    monkeypatch, async_session_maker,
):
    """A disc-anchored candidate that matches no borrowed pool is left alone when
    no kashidashi candidate was surfaced for this disc (the disc isn't a borrow)."""
    import json as _json

    monkeypatch.setattr(resolver, "async_session", async_session_maker)

    async def _items():
        return [_kashidashi_item(id=99, artist="Someone Else", title="Other")]

    monkeypatch.setattr(
        "backend.metadata.sources.kashidashi.fetch_active_borrowed_items",
        _items,
    )

    async with async_session_maker() as s:
        s.add(Job(id="job-own", drive_id="d", disc_id="dx"))
        s.add(MetadataCandidate(
            job_id="job-own", source="musicbrainz",
            artist="My Own Band", album="My Own Album", confidence=90,
            evidence=_json.dumps({"match": "toc_submission"}, ensure_ascii=False),
        ))
        await s.commit()

    await resolver._boost_kashidashi_matches("job-own")

    async with async_session_maker() as s:
        from sqlalchemy import select
        c = (await s.execute(
            select(MetadataCandidate).where(MetadataCandidate.job_id == "job-own")
        )).scalars().first()

    assert c.confidence == 90  # no borrowed candidate surfaced → untouched
