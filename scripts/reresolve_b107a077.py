"""One-shot re-resolve for Job b107a077 with corrected hints.

The initial resolve misidentified this disc as ラーゲリ OST (kashidashi top hit).
It's actually シルヴィ・バルタン『あなたのとりこ ～ 60s ベスト』(kashidashi item 125).
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

JOB_ID = "b107a077-7c12-4def-9be7-4ac7751c3de1"
HINTS = {
    "artist": "Sylvie Vartan",
    "title": "あなたのとりこ ～ 60s ベスト",
}


async def main() -> None:
    from sqlalchemy import delete
    from backend.database import async_session
    from backend.models import Artwork, MetadataCandidate, KashidashiCandidate
    from backend.metadata.resolver import resolve
    from backend.services.disc_identity import restore_identity

    async with async_session() as s:
        await s.execute(delete(MetadataCandidate).where(MetadataCandidate.job_id == JOB_ID))
        await s.execute(delete(KashidashiCandidate).where(KashidashiCandidate.job_id == JOB_ID))
        await s.execute(delete(Artwork).where(Artwork.job_id == JOB_ID))
        await s.commit()
    print(f"Cleared old candidates/artworks for {JOB_ID}")

    identity = await restore_identity(JOB_ID)
    print(f"Restored identity: disc_id={getattr(identity, 'disc_id', None)} track_count={getattr(identity, 'track_count', None)}")

    await resolve(JOB_ID, identity, HINTS, None)
    print("resolve() done")


asyncio.run(main())
