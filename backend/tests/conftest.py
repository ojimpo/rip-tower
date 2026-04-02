"""Shared fixtures for Rip Tower backend tests."""

from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Point config to a temp path so it doesn't touch real config
os.environ.setdefault("CONFIG_PATH", "/tmp/rip-tower-test-config.yaml")

from backend.database import Base, get_session
from backend.models import Drive, Job, JobMetadata, Track, MetadataCandidate, Artwork, KashidashiCandidate


@pytest_asyncio.fixture
async def async_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_maker(async_engine):
    """Session factory bound to the test engine."""
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(async_session_maker) -> AsyncGenerator[AsyncSession, None]:
    """Yield a test database session."""
    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def test_app(async_session_maker):
    """Create a FastAPI test app with overridden DB dependency."""
    from fastapi import FastAPI
    from backend.routers import drives, history, jobs, settings_router

    app = FastAPI()
    app.include_router(jobs.router, prefix="/api")
    app.include_router(drives.router, prefix="/api")
    app.include_router(history.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    return app


@pytest_asyncio.fixture
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
