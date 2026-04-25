"""Tests for backend.services.gnudb_submit."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.models import Job, JobMetadata, Track
from backend.services import gnudb_submit
from backend.services.gnudb_submit import (
    FREEDB_CATEGORIES,
    _categorize,
    _emit_long,
    _parse_cddb_code,
    _sanitize,
    build_xmcd,
    submit,
    submit_with_test_first,
)


# ─────────────────────────── pure helpers ───────────────────────────


class TestCategorize:
    def test_empty_falls_to_misc(self):
        assert _categorize(None) == "misc"
        assert _categorize("") == "misc"
        assert _categorize("   ") == "misc"

    def test_jazz(self):
        assert _categorize("Jazz") == "jazz"
        assert _categorize("Jazz Fusion") == "jazz"
        assert _categorize("ジャズ") == "jazz"

    def test_classical_japanese(self):
        assert _categorize("交響曲") == "classical"
        assert _categorize("ピアノ協奏曲") == "classical"
        assert _categorize("クラシック") == "classical"

    def test_soundtrack(self):
        assert _categorize("Soundtrack") == "soundtrack"
        assert _categorize("OST") == "soundtrack"
        assert _categorize("劇伴") == "soundtrack"
        assert _categorize("アニメソング") == "soundtrack"

    def test_jpop_falls_to_rock(self):
        # everything we can't classify but is non-empty becomes rock
        assert _categorize("J-Pop") == "rock"
        assert _categorize("歌謡曲") == "rock"
        assert _categorize("Hip Hop") == "rock"
        assert _categorize("R&B") == "rock"
        assert _categorize("Pop") == "rock"

    def test_blues(self):
        assert _categorize("Blues") == "blues"
        assert _categorize("ブルース") == "blues"

    def test_reggae(self):
        assert _categorize("Reggae") == "reggae"
        assert _categorize("レゲエ") == "reggae"

    def test_country(self):
        assert _categorize("Country") == "country"
        assert _categorize("カントリー") == "country"

    def test_newage(self):
        assert _categorize("New Age") == "newage"
        assert _categorize("Ambient") == "newage"
        assert _categorize("ヒーリング") == "newage"

    def test_folk(self):
        assert _categorize("Folk") == "folk"
        assert _categorize("民謡") == "folk"

    def test_all_outputs_are_valid_freedb_categories(self):
        for cat in (
            None, "", "rock", "Jazz", "クラシック", "Country", "Folk",
            "Blues", "New Age", "Reggae", "OST", "歌謡曲", "Hip Hop",
        ):
            assert _categorize(cat) in FREEDB_CATEGORIES


class TestSanitize:
    def test_strips_newlines(self):
        assert _sanitize("foo\nbar") == "foo bar"
        assert _sanitize("foo\r\nbar") == "foo bar"

    def test_strips_tabs(self):
        assert _sanitize("a\tb") == "a b"

    def test_empty(self):
        assert _sanitize("") == ""
        assert _sanitize(None) == ""


class TestParseCddbCode:
    def test_parses_200(self):
        assert _parse_cddb_code("200 OK accepted\nfoo") == 200

    def test_parses_401(self):
        assert _parse_cddb_code("401 Permission denied") == 401

    def test_returns_none_for_garbage(self):
        assert _parse_cddb_code("hello world") is None
        assert _parse_cddb_code("") is None
        assert _parse_cddb_code(None) is None


class TestEmitLong:
    def test_short_value_one_line(self):
        out: list[str] = []
        _emit_long("TTITLE0", "Hello", out)
        assert out == ["TTITLE0=Hello"]

    def test_empty_value_one_line(self):
        out: list[str] = []
        _emit_long("TTITLE0", "", out)
        assert out == ["TTITLE0="]

    def test_long_ascii_splits(self):
        out: list[str] = []
        long = "x" * 1000
        _emit_long("TTITLE0", long, out)
        assert len(out) > 1
        assert all(line.startswith("TTITLE0=") for line in out)
        # Concatenating values reconstructs the original
        joined = "".join(line[len("TTITLE0="):] for line in out)
        assert joined == long

    def test_long_japanese_splits_at_char_boundary(self):
        out: list[str] = []
        # ~600 bytes of multi-byte chars
        long = "あ" * 200
        _emit_long("TTITLE0", long, out)
        assert len(out) > 1
        joined = "".join(line[len("TTITLE0="):] for line in out)
        # Each chunk must round-trip through utf-8 cleanly
        for line in out:
            value = line[len("TTITLE0="):]
            value.encode("utf-8")  # would raise on bad split
        assert joined == long


# ─────────────────────────── build_xmcd ───────────────────────────


@pytest.fixture
def patched_session(monkeypatch, async_session_maker):
    """Point gnudb_submit + disc_identity at the in-memory test DB."""
    from backend.services import disc_identity as identity_mod

    monkeypatch.setattr(gnudb_submit, "async_session", async_session_maker)
    monkeypatch.setattr(identity_mod, "async_session", async_session_maker)
    return async_session_maker


@pytest.fixture
def patched_config(monkeypatch):
    from backend import config as config_mod

    cfg = config_mod.AppConfig()
    cfg.integrations.gnudb_email = "test@example.com"
    cfg.integrations.gnudb_url = "https://gnudb.example"
    cfg.integrations.gnudb_client_name = "rip-tower-test"
    cfg.integrations.gnudb_client_version = "0.0.1"
    cfg.integrations.gnudb_enabled = True
    monkeypatch.setattr(gnudb_submit, "get_config", lambda: cfg)
    return cfg


async def _seed_hiromi_disc1(session_maker):
    """Seed the Hiromi Go anniversary tour disc 1 fixture (11 tracks)."""
    titles = [
        "2億4千万の瞳 -エキゾチック・ジャパン-",
        "セクシー・ユー(モンロー・ウォーク)",
        "How many いい顔",
        "哀愁のカサブランカ",
        "林檎殺人事件",
        "お嫁サンバ",
        "あなたがいたから僕がいた",
        "ジェルセミナの瞳",
        "Goldfinger '99",
        "言えないよ",
        "男願 Groove!",
    ]
    offsets = [
        150, 22207, 41560, 60913, 76266, 99619,
        118972, 138325, 157678, 184031, 199384,
    ]
    leadout = 219000

    async with session_maker() as session:
        job = Job(
            id="hiromi-disc1",
            disc_id="8c0b710b",
            disc_total_seconds=leadout,
            disc_offsets=json.dumps(offsets),
            disc_leadout=leadout,
            status="complete",
        )
        meta = JobMetadata(
            job_id="hiromi-disc1",
            artist="郷ひろみ",
            album="Hiromi Go 50th Anniversary Celebration Tour 2022 ～Keep Singing～ [DISC1]",
            album_base="Hiromi Go 50th Anniversary Celebration Tour 2022 ～Keep Singing～",
            year=2023,
            genre="J-Pop",
            disc_number=1,
            total_discs=2,
            is_compilation=False,
            confidence=100,
            source="manual",
        )
        session.add_all([job, meta])
        for i, title in enumerate(titles, 1):
            session.add(Track(
                job_id="hiromi-disc1", track_num=i, title=title,
                rip_status="ok", encode_status="ok",
            ))
        await session.commit()


@pytest.mark.asyncio
async def test_build_xmcd_hiromi(patched_session, patched_config):
    await _seed_hiromi_disc1(patched_session)
    body, category = await build_xmcd("hiromi-disc1")

    # Header
    assert body.startswith("# xmcd\n")
    assert "# Track frame offsets:" in body
    assert "#\t150" in body
    assert "#\t199384" in body
    assert "# Disc length: 219000 seconds" in body
    assert "# Submitted via: rip-tower-test 0.0.1" in body

    # Body
    assert "DISCID=8c0b710b" in body
    assert (
        "DTITLE=郷ひろみ / "
        "Hiromi Go 50th Anniversary Celebration Tour 2022 "
        "～Keep Singing～ [DISC1]"
    ) in body
    assert "DYEAR=2023" in body
    assert "DGENRE=J-Pop" in body
    # Track 1 zero-indexed
    assert "TTITLE0=2億4千万の瞳 -エキゾチック・ジャパン-" in body
    assert "TTITLE10=男願 Groove!" in body
    assert "EXTD=" in body
    assert "EXTT0=" in body
    assert "EXTT10=" in body
    assert body.rstrip().endswith("PLAYORDER=")

    # Category
    assert category == "rock"  # J-Pop -> rock per design


@pytest.mark.asyncio
async def test_build_xmcd_compilation(patched_session, patched_config):
    async with patched_session() as session:
        session.add(Job(
            id="comp-1",
            disc_id="0a0b0c0d",
            disc_offsets=json.dumps([150, 20000]),
            disc_leadout=400,
            disc_total_seconds=400,
            status="complete",
        ))
        session.add(JobMetadata(
            job_id="comp-1",
            artist="Various Artists",
            album="Compilation",
            album_base="Compilation",
            is_compilation=True,
            year=2020,
            genre="Rock",
            disc_number=1,
            total_discs=1,
        ))
        session.add(Track(
            job_id="comp-1", track_num=1,
            title="Song A", artist="Band X",
            rip_status="ok", encode_status="ok",
        ))
        session.add(Track(
            job_id="comp-1", track_num=2,
            title="Song B", artist="Band Y",
            rip_status="ok", encode_status="ok",
        ))
        await session.commit()

    body, category = await build_xmcd("comp-1")
    assert "DTITLE=Various / Compilation" in body
    assert "TTITLE0=Band X / Song A" in body
    assert "TTITLE1=Band Y / Song B" in body
    assert category == "rock"


@pytest.mark.asyncio
async def test_build_xmcd_rejects_missing_offsets(patched_session, patched_config):
    async with patched_session() as session:
        session.add(Job(
            id="no-offsets",
            disc_id="cafef00d",
            disc_total_seconds=300,
            # no offsets / leadout
            status="complete",
        ))
        session.add(JobMetadata(
            job_id="no-offsets", artist="A", album="B",
            disc_number=1, total_discs=1,
        ))
        session.add(Track(
            job_id="no-offsets", track_num=1,
            rip_status="ok", encode_status="ok",
        ))
        await session.commit()

    with pytest.raises(RuntimeError, match="track offsets"):
        await build_xmcd("no-offsets")


@pytest.mark.asyncio
async def test_build_xmcd_rejects_empty_artist(patched_session, patched_config):
    async with patched_session() as session:
        session.add(Job(
            id="no-artist",
            disc_id="deadbeef",
            disc_offsets=json.dumps([150, 200]),
            disc_leadout=300,
            status="complete",
        ))
        session.add(JobMetadata(
            job_id="no-artist", artist=None, album="Something",
            disc_number=1, total_discs=1,
        ))
        session.add(Track(
            job_id="no-artist", track_num=1,
            rip_status="ok", encode_status="ok",
        ))
        await session.commit()

    with pytest.raises(RuntimeError, match="artist/album"):
        await build_xmcd("no-artist")


# ─────────────────────────── submit() http ───────────────────────────


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_post(monkeypatch):
    """Patch httpx.AsyncClient inside the gnudb_submit module."""

    def install(handler):
        original = httpx.AsyncClient

        class _PatchedClient(original):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = _mock_transport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(gnudb_submit.httpx, "AsyncClient", _PatchedClient)

    return install


@pytest.mark.asyncio
async def test_submit_accepted(patched_session, patched_config, mock_post):
    await _seed_hiromi_disc1(patched_session)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="200 OK accepted\n")

    mock_post(handler)
    record = await submit("hiromi-disc1", mode="test")

    assert record.response_code == 200
    assert record.error is None
    assert record.submit_mode == "test"
    assert record.disc_id == "8c0b710b"

    assert captured["url"].endswith("/~cddb/submit.cgi")
    assert captured["headers"]["category"] == "rock"
    assert captured["headers"]["discid"] == "8c0b710b"
    assert captured["headers"]["submit-mode"] == "test"
    assert captured["headers"]["user-email"] == "test@example.com"
    assert captured["headers"]["charset"] == "UTF-8"
    assert "DISCID=8c0b710b" in captured["body"]


@pytest.mark.asyncio
async def test_submit_rejected_401(patched_session, patched_config, mock_post):
    await _seed_hiromi_disc1(patched_session)

    def handler(request):
        return httpx.Response(200, text="401 Permission denied\n")

    mock_post(handler)
    record = await submit("hiromi-disc1", mode="test")
    assert record.response_code == 401
    assert record.error is None


@pytest.mark.asyncio
async def test_submit_network_error(patched_session, patched_config, mock_post):
    await _seed_hiromi_disc1(patched_session)

    def handler(request):
        raise httpx.ConnectError("connection refused")

    mock_post(handler)
    record = await submit("hiromi-disc1", mode="test")
    assert record.response_code is None
    assert record.error is not None
    assert "ConnectError" in record.error


@pytest.mark.asyncio
async def test_submit_with_test_first_skips_real_on_test_failure(
    patched_session, patched_config, mock_post
):
    await _seed_hiromi_disc1(patched_session)
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(200, text="501 Invalid format\n")

    mock_post(handler)
    record = await submit_with_test_first("hiromi-disc1")
    assert record.submit_mode == "test"
    assert record.response_code == 501
    assert call_count["n"] == 1  # didn't proceed to submit


@pytest.mark.asyncio
async def test_submit_with_test_first_promotes_on_success(
    patched_session, patched_config, mock_post
):
    await _seed_hiromi_disc1(patched_session)
    modes_seen = []

    def handler(request):
        modes_seen.append(request.headers["submit-mode"])
        return httpx.Response(200, text="200 OK accepted\n")

    mock_post(handler)
    record = await submit_with_test_first("hiromi-disc1")
    assert modes_seen == ["test", "submit"]
    assert record.submit_mode == "submit"
    assert record.response_code == 200


@pytest.mark.asyncio
async def test_already_accepted_blocks_re_submit(
    patched_session, patched_config, mock_post
):
    await _seed_hiromi_disc1(patched_session)

    def handler(request):
        return httpx.Response(200, text="200 OK accepted\n")

    mock_post(handler)
    await submit("hiromi-disc1", mode="submit")

    with pytest.raises(RuntimeError, match="already has an accepted"):
        await submit("hiromi-disc1", mode="submit")
