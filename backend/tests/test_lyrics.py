"""Tests for the lyrics module's LRCLIB search-fallback validation."""

from __future__ import annotations

import pytest

from backend.metadata import lyrics


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url, params=None):
        return _Resp(200, self._payload)


@pytest.mark.asyncio
async def test_lrclib_search_skips_unrelated_first_hit():
    """The fuzzy search's first hit is a different song — it must be skipped
    in favor of the result that actually matches artist+title."""
    client = _Client([
        {
            "artistName": "Wrong Artist",
            "trackName": "Different Song",
            "plainLyrics": "wrong lyrics",
        },
        {
            "artistName": "米津玄師",
            "trackName": "Lemon",
            "plainLyrics": "correct lyrics",
            "syncedLyrics": None,
        },
    ])
    synced, plain = await lyrics._lrclib_search(client, "米津玄師", "Lemon")
    assert plain == "correct lyrics"
    assert synced is None


@pytest.mark.asyncio
async def test_lrclib_search_returns_none_when_nothing_matches():
    client = _Client([
        {"artistName": "Wrong", "trackName": "Nope", "plainLyrics": "x"},
    ])
    synced, plain = await lyrics._lrclib_search(client, "米津玄師", "Lemon")
    assert synced is None and plain is None


@pytest.mark.asyncio
async def test_lrclib_search_accepts_title_with_tieup_suffix():
    """Containment (canonical title inside an annotated one) still matches."""
    client = _Client([
        {
            "artistName": "Mr.Children",
            "trackName": "himawari (映画「君の膵臓をたべたい」主題歌)",
            "plainLyrics": "lyrics",
        },
    ])
    synced, plain = await lyrics._lrclib_search(client, "Mr.Children", "himawari")
    assert plain == "lyrics"


# ───────── per-track artist preference (compilations) ─────────


@pytest.mark.asyncio
async def test_fetch_lyrics_prefers_per_track_artist(
    monkeypatch, async_session_maker,
):
    """On a compilation the album artist is "Various Artists" — the LRCLIB
    query must use the track's own performer when one is recorded, falling
    back to the album artist otherwise."""
    from types import SimpleNamespace

    from backend.models import Job, JobMetadata, Track

    monkeypatch.setattr(lyrics, "async_session", async_session_maker)
    monkeypatch.setattr(
        lyrics, "get_config",
        lambda: SimpleNamespace(
            integrations=SimpleNamespace(musixmatch_token=None)
        ),
    )

    queried: list[str] = []

    async def fake_lrclib(artist, title, album, duration_ms):
        queried.append(artist)
        return None, f"lyrics for {title}"

    monkeypatch.setattr(lyrics, "_fetch_lrclib", fake_lrclib)

    async with async_session_maker() as s:
        s.add(Job(id="job-ly", drive_id="d", disc_id="l"))
        s.add(JobMetadata(
            job_id="job-ly", artist="Various Artists", album="Christmas Songs",
        ))
        s.add(Track(job_id="job-ly", track_num=1,
                    title="All I Want for Christmas Is You",
                    artist="Mariah Carey"))
        s.add(Track(job_id="job-ly", track_num=2, title="Untitled", artist=None))
        await s.commit()

    await lyrics.fetch_lyrics("job-ly")

    assert queried == ["Mariah Carey", "Various Artists"]


@pytest.mark.asyncio
async def test_fetch_lyrics_for_track_prefers_per_track_artist(
    monkeypatch, async_session_maker,
):
    from types import SimpleNamespace

    from backend.models import Job, JobMetadata, Track

    monkeypatch.setattr(lyrics, "async_session", async_session_maker)
    monkeypatch.setattr(
        lyrics, "get_config",
        lambda: SimpleNamespace(
            integrations=SimpleNamespace(musixmatch_token=None)
        ),
    )

    queried: list[str] = []

    async def fake_lrclib(artist, title, album, duration_ms):
        queried.append(artist)
        return "[00:01.00] line", None

    monkeypatch.setattr(lyrics, "_fetch_lrclib", fake_lrclib)

    async with async_session_maker() as s:
        s.add(Job(id="job-l1", drive_id="d", disc_id="l"))
        s.add(JobMetadata(job_id="job-l1", artist="Various Artists", album="Comp"))
        s.add(Track(job_id="job-l1", track_num=1, title="Song", artist="宇多田ヒカル"))
        await s.commit()

    await lyrics.fetch_lyrics_for_track("job-l1", 1)

    assert queried == ["宇多田ヒカル"]
