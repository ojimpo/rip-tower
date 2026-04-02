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
    from fastapi.responses import FileResponse

    # Serve static assets (JS, CSS, icons, etc.)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    app.mount("/icons", StaticFiles(directory=STATIC_DIR / "icons"), name="icons")

    # Serve SW and manifest at root level
    @app.get("/sw.js", include_in_schema=False)
    @app.get("/workbox-{rest:path}", include_in_schema=False)
    @app.get("/registerSW.js", include_in_schema=False)
    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def static_file(rest: str = ""):
        import os
        filename = "workbox-" + rest if rest else ""
        # Determine which file was requested from the path
        from starlette.requests import Request
        return FileResponse(STATIC_DIR / filename)

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
