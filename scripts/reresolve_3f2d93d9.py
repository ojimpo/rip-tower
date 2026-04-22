"""One-shot re-resolve for Job 3f2d93d9 (Disc 2).

Disc 2 (disc_id 870a5e0b) is not registered in MusicBrainz, so automatic
resolution returned no candidates. Disc 1 (job 556561f1) resolved as
『THE WAVES / THE WORLD SOCCER SONG SERIES Vol.5"OLE OLE!NIPPON"』.

This script:
  1. Links Disc 2's album_group to Disc 1's so _sync_from_group can
     inherit shared metadata.
  2. Clears old candidates/artwork.
  3. Re-runs resolve() with artist/title hints so hint-using sources
     (Discogs / iTunes / HMV / Kashidashi) can fetch Disc 2 track titles.
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DISC2_JOB_ID = "3f2d93d9-9334-49c4-ae61-ef015c08aa2a"
DISC1_JOB_ID = "556561f1-69a8-4088-a13d-32d834434993"

HINTS = {
    "artist": "THE WAVES",
    "title": "THE WORLD SOCCER SONG SERIES Vol.5",
}


async def main() -> None:
    from sqlalchemy import delete
    from backend.database import async_session
    from backend.models import Artwork, Job, MetadataCandidate, KashidashiCandidate
    from backend.metadata.resolver import resolve
    from backend.services.disc_identity import restore_identity

    async with async_session() as s:
        disc1 = await s.get(Job, DISC1_JOB_ID)
        disc2 = await s.get(Job, DISC2_JOB_ID)
        if not disc1 or not disc2:
            raise SystemExit("One of the jobs was not found")
        print(f"Disc 1 album_group: {disc1.album_group}")
        print(f"Disc 2 album_group (before): {disc2.album_group}")
        disc2.album_group = disc1.album_group
        await s.commit()
        print(f"Disc 2 album_group (after):  {disc2.album_group}")

    async with async_session() as s:
        await s.execute(delete(MetadataCandidate).where(MetadataCandidate.job_id == DISC2_JOB_ID))
        await s.execute(delete(KashidashiCandidate).where(KashidashiCandidate.job_id == DISC2_JOB_ID))
        await s.execute(delete(Artwork).where(Artwork.job_id == DISC2_JOB_ID))
        await s.commit()
    print(f"Cleared old candidates/artworks for {DISC2_JOB_ID}")

    identity = await restore_identity(DISC2_JOB_ID)
    print(f"Restored identity: disc_id={getattr(identity, 'disc_id', None)} track_count={getattr(identity, 'track_count', None)}")

    await resolve(DISC2_JOB_ID, identity, HINTS, None)
    print("resolve() done")


asyncio.run(main())
