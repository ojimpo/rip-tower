"""FastAPI application entry point."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Configure root logger so all backend logs are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import get_config
from backend.routers import drives, history, jobs, settings_router, trash
from backend.services.websocket import router as ws_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    config = get_config()
    logger.info("Rip Tower starting — config loaded")

    # Ensure incoming dir exists
    Path(config.output.incoming_dir).mkdir(parents=True, exist_ok=True)

    # Recover orphaned jobs: anything in an in-progress state at startup
    # has no live task driving it (the previous process is gone).
    await _recover_orphan_jobs()

    # Start drive monitor
    from backend.services.drive_monitor import start_monitoring

    await start_monitoring()
    logger.info("Drive monitor started")

    yield

    logger.info("Rip Tower shutting down")


async def _recover_orphan_jobs() -> None:
    from sqlalchemy import select

    from backend.database import async_session
    from backend.models import Job

    active_states = ("pending", "identifying", "ripping", "encoding", "finalizing")
    async with async_session() as session:
        result = await session.execute(
            select(Job).where(Job.status.in_(active_states))
        )
        orphans = list(result.scalars())
        for job in orphans:
            previous = job.status
            logger.warning(
                "Recovering orphaned job %s (was %s) → error", job.id, previous
            )
            job.status = "error"
            if not job.error_message:
                job.error_message = (
                    f"Process interrupted (was '{previous}' at startup); please re-rip."
                )
        if orphans:
            await session.commit()


app = FastAPI(
    title="Rip Tower",
    description="CD Ripping & Metadata Management",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(jobs.router, prefix="/api")
app.include_router(drives.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(trash.router, prefix="/api")
app.include_router(ws_router)

# Serve frontend static files (production)
if STATIC_DIR.exists():
    from fastapi.responses import FileResponse

    # Serve static assets (JS, CSS, icons, etc.)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    app.mount("/icons", StaticFiles(directory=STATIC_DIR / "icons"), name="icons")

    # SPA fallback — serve index.html for all non-API, non-asset routes
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Don't intercept API or WS routes
        if full_path.startswith("api/") or full_path.startswith("ws"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        file_path = STATIC_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html")
