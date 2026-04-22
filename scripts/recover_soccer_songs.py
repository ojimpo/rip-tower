"""One-shot recovery for both discs of 『決定盤!!「サッカーソングス」ベスト』.

Disc 1 (556561f1): CDDB misidentified as 「THE WORLD SOCCER SONG SERIES Vol.5
"OLE OLE!NIPPON"」 (a different, single-disc 2004/2014 release).
Disc 2 (3f2d93d9): disc_id 870a5e0b not in MusicBrainz. Hint-driven re-resolve
picked up random Discogs/iTunes hits and LLM assist locked in Imagine Dragons
/ Evolve (also 11 tracks — coincidental collision).

Ground truth: both discs belong to the Pony Canyon 2-disc compilation
『決定盤!!「サッカーソングス」ベスト』(PCCK-20016, 2008-10-08), which kashidashi
item 138 corresponds to. Tracklist from Tower Records.

Script actions:
  1. Force canonical metadata onto both jobs (artist, album, year, disc nums,
     compilation flag, confidence=100, source=forced).
  2. Overwrite track titles and per-track artists from the Tower tracklist.
  3. Clear old metadata candidates / kashidashi candidates / artworks so the
     UI review screen shows a clean state.
  4. Re-fetch artwork under the correct album name.
  5. Re-encode (which re-runs metaflac tagging) so the encoded FLACs carry
     the new tags before finalize.
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DISC1_JOB_ID = "556561f1-69a8-4088-a13d-32d834434993"
DISC2_JOB_ID = "3f2d93d9-9334-49c4-ae61-ef015c08aa2a"

ALBUM = "決定盤!!「サッカーソングス」ベスト"
YEAR = 2008
ALBUM_ARTIST = "Various Artists"

# (track_num, title, artist) — artist=None means unknown/leave as Various Artists
DISC1_TRACKS = [
    (1, "WE ARE THE CHAMP (ANDERLECHT CHAMPION)", "THE WAVES"),
    (2, "CORAGGIO!NIPPON", "THE WAVES"),
    (3, "GO!J~POWERFUL NIPPON~", "J-Freaks"),
    (4, "POWER OF BLUE~青の勇気~", "TWO-WAVE with BLUE POWERS"),
    (5, "KICK IT (21st.Century Version)", "Kalapana"),
    (6, "GET GOAL!", "TWO-WAVE"),
    (7, "HERO", "THE WAVES"),
    (8, "GO!J~POWERFUL NIPPON~ (REMIX)", "J-Freaks"),
    (9, "OLE OLE (ANDERLECHT CHAMPION) STADIUM VERSION", "ザ・ハット・トリックス"),
    (10, "OLE OLE (ANDERLECHT CHAMPION) S/T KLUB VERSION", "ザ・ハット・トリックス"),
]

DISC2_TRACKS = [
    (1, "プラ・フレンテ・ブラジル", None),
    (2, "パイス・トロピカル~フィオ・マラヴィルハ~タジマハール", None),
    (3, "カンペオン~ミュー・タイム", None),
    (4, "ゴ~~~ル!ブラジル", None),
    (5, "ディス・タイム~ウィール・ゲット・イット・ライト", None),
    (6, "ユール・ネヴァー・ウォーク・アローン", None),
    (7, "イングランド、ウィール・フライ・ザ・フラッグ", None),
    (8, "ウィー・アー・ザ・チャンピオンズ", None),
    (9, "カルメン~アイーダ・メドレー", None),
    (10, "ウン・エスターテ・イタリアーナ", None),
    (11, "ヴォラーレ~ネル・ブル・ディピント・ディ・ブル", None),
]


async def fix_job(job_id: str, disc_number: int, tracks_data: list[tuple[int, str, str | None]]) -> None:
    from sqlalchemy import delete, select
    from backend.database import async_session
    from backend.models import Artwork, Job, JobMetadata, KashidashiCandidate, MetadataCandidate, Track

    async with async_session() as s:
        # Clear old candidates/artworks
        await s.execute(delete(MetadataCandidate).where(MetadataCandidate.job_id == job_id))
        await s.execute(delete(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id))
        await s.execute(delete(Artwork).where(Artwork.job_id == job_id))

        meta = await s.get(JobMetadata, job_id)
        if not meta:
            raise SystemExit(f"No JobMetadata for {job_id}")

        meta.artist = ALBUM_ARTIST
        meta.album = ALBUM
        meta.album_base = ALBUM
        meta.year = YEAR
        meta.genre = None
        meta.disc_number = disc_number
        meta.total_discs = 2
        meta.is_compilation = True
        meta.confidence = 100
        meta.source = "forced"
        meta.source_url = "https://tower.jp/item/2452004"
        meta.needs_review = True
        meta.issues = None
        meta.approved = False

        # Update tracks
        result = await s.execute(select(Track).where(Track.job_id == job_id).order_by(Track.track_num))
        tracks = {t.track_num: t for t in result.scalars().all()}
        expected = {n for n, _, _ in tracks_data}
        if set(tracks.keys()) != expected:
            raise SystemExit(
                f"Track number mismatch for {job_id}: db={sorted(tracks)} expected={sorted(expected)}"
            )
        for num, title, artist in tracks_data:
            tracks[num].title = title
            tracks[num].artist = artist
            tracks[num].lyrics_plain = None
            tracks[num].lyrics_synced = None
            tracks[num].lyrics_source = None

        await s.commit()
    logging.info("Updated DB metadata and tracks for job %s (disc %d)", job_id, disc_number)


async def main() -> None:
    from backend.metadata.artwork import fetch_artwork
    from backend.metadata.lyrics import fetch_lyrics
    from backend.services.encoder import encode_all, retag_all

    await fix_job(DISC1_JOB_ID, 1, DISC1_TRACKS)
    await fix_job(DISC2_JOB_ID, 2, DISC2_TRACKS)

    # Re-fetch artwork for the compilation
    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            await fetch_artwork(jid)
        except Exception:
            logging.exception("Artwork fetch failed for %s", jid)

    # Re-fetch lyrics using the corrected track titles
    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            await fetch_lyrics(jid)
        except Exception:
            logging.exception("Lyrics fetch failed for %s", jid)

    # Re-apply tags on the already-encoded FLACs (fall back to encode if any
    # encoded file is missing, e.g. for WAV imports or mid-pipeline recovery).
    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            if not await retag_all(jid):
                await encode_all(jid)
        except Exception:
            logging.exception("Retag/encode failed for %s", jid)

    print("Recovery complete")


asyncio.run(main())
