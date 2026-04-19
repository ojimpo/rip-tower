"""Apply Discogs release 14126757 (Sylvie Vartan - Irrésistiblement ~ 60s Best)
metadata to Job b107a077: year, track titles, artwork."""
import asyncio
import json
import logging
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

JOB_ID = "b107a077-7c12-4def-9be7-4ac7751c3de1"
RELEASE_ID = 14126757


async def main() -> None:
    from sqlalchemy import select
    from PIL import Image
    from io import BytesIO

    from backend.config import DATA_DIR, get_config
    from backend.database import async_session
    from backend.models import Artwork, JobMetadata, Track

    token = get_config().integrations.discogs_token

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.discogs.com/releases/{RELEASE_ID}",
            headers={"User-Agent": "RipTower/0.1.0", "Authorization": f"Discogs token={token}"},
        )
        resp.raise_for_status()
        rel = resp.json()

    tracklist = rel.get("tracklist", [])
    titles = [t.get("title", "").strip() for t in tracklist]
    print(f"Fetched {len(titles)} track titles")

    primary_img = None
    for img in rel.get("images") or []:
        if img.get("type") == "primary":
            primary_img = img
            break
    if not primary_img and rel.get("images"):
        primary_img = rel["images"][0]

    async with async_session() as s:
        meta = await s.get(JobMetadata, JOB_ID)
        meta.year = rel.get("year") or meta.year
        meta.source = "discogs"
        meta.source_url = f"https://www.discogs.com/release/{RELEASE_ID}"
        meta.confidence = 80
        issues = json.loads(meta.issues) if meta.issues else []
        issues = [i for i in issues if i != "no_track_titles"]
        meta.issues = json.dumps(issues, ensure_ascii=False) if issues else None
        await s.commit()

    async with async_session() as s:
        result = await s.execute(
            select(Track).where(Track.job_id == JOB_ID).order_by(Track.track_num)
        )
        tracks = list(result.scalars().all())
        for idx, tr in enumerate(tracks):
            if idx < len(titles):
                tr.title = titles[idx]
        await s.commit()
    print(f"Updated {len(tracks)} track titles in DB")

    if primary_img:
        url = primary_img.get("uri") or primary_img.get("resource_url")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "RipTower/0.1.0", "Authorization": f"Discogs token={token}"},
            )
            resp.raise_for_status()
            image_data = resp.content

        img_obj = Image.open(BytesIO(image_data))
        w, h = img_obj.size
        ext = "jpg" if img_obj.format in ("JPEG", None) else img_obj.format.lower()
        artwork_dir = DATA_DIR / "artworks"
        artwork_dir.mkdir(parents=True, exist_ok=True)
        filepath = artwork_dir / f"{JOB_ID}_discogs.{ext}"
        filepath.write_bytes(image_data)

        async with async_session() as s:
            art = Artwork(
                job_id=JOB_ID,
                source="discogs",
                url=url,
                local_path=str(filepath),
                width=w,
                height=h,
                file_size=len(image_data),
                selected=True,
            )
            s.add(art)
            await s.commit()
        print(f"Saved artwork: {filepath} ({w}x{h}, {len(image_data)} bytes)")


asyncio.run(main())
