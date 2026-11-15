"""Async SQLite engine + session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


def _ensure_sqlite_dir(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 15} if settings.database_url.startswith("sqlite") else {},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from . import models  # noqa: F401  (register tables on Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
