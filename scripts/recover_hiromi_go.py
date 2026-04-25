"""One-shot recovery for both discs of 郷ひろみ
『Hiromi Go 50th Anniversary Celebration Tour 2022 ～Keep Singing～』.

Disc 1 (8c0b710b) and Disc 2 (9d0bf70b): live tour album from 2023, neither
disc is in CDDB and both fail MusicBrainz disc-id lookup. All other sources
(Discogs / iTunes / HMV) returned nothing. With no resolved metadata, the
post-resolution kashidashi fuzzy match never ran (it gates on artist+album
being set), so kashidashi item 144 was never linked even though it was
borrowed and matches by title.

Ground truth tracklist confirmed via Sony Music Japan SRCL-12186/7 (and
Amazon/Tower listings). The album was released 2023-03-08 as a 2CD set.

Script actions:
  1. Create JobMetadata records for both jobs (none existed because no
     candidates ever materialized).
  2. Generate an album_group UUID and link both jobs to it.
  3. Set tracklist titles on both jobs.
  4. Clear stale candidate / kashidashi / artwork rows so the UI shows clean
     state when the user reviews the result.
  5. Re-fetch artwork (Cover Art Archive / iTunes will now find it because
     artist+album are set) and re-fetch lyrics from track titles.
  6. retag_all so the existing FLACs receive the corrected tags without
     re-encoding (commit acb3ac4).
"""
import asyncio
import logging
import uuid

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DISC1_JOB_ID = "2a29f9d0-d87f-44d3-9955-b84407cba47e"  # disc_id 8c0b710b
DISC2_JOB_ID = "abbe8f68-3b20-4a60-8ff8-18222dca2175"  # disc_id 9d0bf70b

ARTIST = "郷ひろみ"
ALBUM_BASE = "Hiromi Go 50th Anniversary Celebration Tour 2022 ～Keep Singing～"
YEAR = 2023
GENRE = "J-Pop"
SOURCE_URL = "https://www.sonymusic.co.jp/artist/HiromiGo/discography/SRCL-12186"

DISC1_TRACKS = [
    (1, "2億4千万の瞳 -エキゾチック・ジャパン-"),
    (2, "セクシー・ユー(モンロー・ウォーク)"),
    (3, "お嫁サンバ"),
    (4, "GOLDFINGER'99"),
    (5, "千年の孤独"),
    (6, "サファイア・ブルー"),
    (7, "CHARISMA"),
    (8, "愛より速く"),
    (9, "デンジャラー☆"),
    (10, "Good Times Bad Times"),
    (11, "男願 Groove!"),
]

DISC2_TRACKS = [
    (1, "ハリウッド・スキャンダル"),
    (2, "哀愁のカサブランカ"),
    (3, "五時までに"),
    (4, "よろしく哀愁"),
    (5, "愛してる"),
    (6, "ありのままでそばにいて"),
    (7, "あなたがいたから僕がいた"),
    (8, "見つめてほしい"),
    (9, "言えないよ"),
    (10, "男の子女の子"),
    (11, "おなじ道・おなじ場所"),
]


async def fix_job(
    job_id: str,
    disc_number: int,
    album_group: str,
    tracks_data: list[tuple[int, str]],
) -> None:
    from sqlalchemy import delete, select
    from backend.database import async_session
    from backend.models import (
        Artwork,
        Job,
        JobMetadata,
        KashidashiCandidate,
        MetadataCandidate,
        Track,
    )

    album_dir = f"{ALBUM_BASE} [DISC{disc_number}]"

    async with async_session() as s:
        await s.execute(delete(MetadataCandidate).where(MetadataCandidate.job_id == job_id))
        await s.execute(delete(KashidashiCandidate).where(KashidashiCandidate.job_id == job_id))
        await s.execute(delete(Artwork).where(Artwork.job_id == job_id))

        job = await s.get(Job, job_id)
        if not job:
            raise SystemExit(f"No Job for {job_id}")
        job.album_group = album_group
        job.source_type = "library"

        meta = await s.get(JobMetadata, job_id)
        if not meta:
            meta = JobMetadata(
                job_id=job_id,
                artist=ARTIST,
                album=album_dir,
                album_base=ALBUM_BASE,
                year=YEAR,
                genre=GENRE,
                disc_number=disc_number,
                total_discs=2,
                is_compilation=False,
                confidence=100,
                source="forced",
                source_url=SOURCE_URL,
                needs_review=True,
                issues=None,
                approved=False,
            )
            s.add(meta)
        else:
            meta.artist = ARTIST
            meta.album = album_dir
            meta.album_base = ALBUM_BASE
            meta.year = YEAR
            meta.genre = GENRE
            meta.disc_number = disc_number
            meta.total_discs = 2
            meta.is_compilation = False
            meta.confidence = 100
            meta.source = "forced"
            meta.source_url = SOURCE_URL
            meta.needs_review = True
            meta.issues = None
            meta.approved = False

        result = await s.execute(
            select(Track).where(Track.job_id == job_id).order_by(Track.track_num)
        )
        tracks = {t.track_num: t for t in result.scalars().all()}
        expected = {n for n, _ in tracks_data}
        if set(tracks.keys()) != expected:
            raise SystemExit(
                f"Track number mismatch for {job_id}: db={sorted(tracks)} expected={sorted(expected)}"
            )
        for num, title in tracks_data:
            tracks[num].title = title
            tracks[num].artist = ARTIST
            tracks[num].lyrics_plain = None
            tracks[num].lyrics_synced = None
            tracks[num].lyrics_source = None

        await s.commit()
    logging.info("Updated DB metadata and tracks for job %s (disc %d)", job_id, disc_number)


async def main() -> None:
    from backend.metadata.artwork import fetch_artwork
    from backend.metadata.lyrics import fetch_lyrics
    from backend.metadata.sources.kashidashi import match_kashidashi
    from backend.services.disc_identity import restore_identity
    from backend.services.encoder import encode_all, retag_all

    album_group = str(uuid.uuid4())
    logging.info("album_group=%s", album_group)

    await fix_job(DISC1_JOB_ID, 1, album_group, DISC1_TRACKS)
    await fix_job(DISC2_JOB_ID, 2, album_group, DISC2_TRACKS)

    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            await fetch_artwork(jid)
        except Exception:
            logging.exception("Artwork fetch failed for %s", jid)

    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            await fetch_lyrics(jid)
        except Exception:
            logging.exception("Lyrics fetch failed for %s", jid)

    # Link kashidashi item 144 by re-running fuzzy match now that artist/album
    # are set on the JobMetadata.
    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            identity = await restore_identity(jid)
            await match_kashidashi(jid, identity)
        except Exception:
            logging.exception("Kashidashi match failed for %s", jid)

    for jid in (DISC1_JOB_ID, DISC2_JOB_ID):
        try:
            if not await retag_all(jid):
                logging.warning("retag_all returned False for %s, falling back to encode_all", jid)
                await encode_all(jid)
        except Exception:
            logging.exception("Retag/encode failed for %s", jid)

    print("Recovery complete")


asyncio.run(main())
