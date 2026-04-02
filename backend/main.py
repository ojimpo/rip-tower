"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import get_config
from backend.routers import drives, history, jobs, settings_router
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

    # Start drive monitor
    from backend.services.drive_monitor import start_monitoring

    await start_monitoring()
    logger.info("Drive monitor started")

    yield

    logger.info("Rip Tower shutting down")


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
app.include_router(ws_router)

# Serve frontend static files (production)
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
