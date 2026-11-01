"""测试用异步 SQLite 引擎（不依赖 Postgres）。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

ALL_NEW_TABLES = (
    "indicator_events",
    "indicator_outcomes",
    "experiments",
    "experiment_runs",
    "experiment_trades",
    "indicators",
    "indicator_versions",
    "promotion_audit",
)


def make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def prepare(engine, tables: tuple[str, ...] = ALL_NEW_TABLES) -> None:
    selected = [Base.metadata.tables[name] for name in tables]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=selected)
        )


def make_session(engine):
    """与生产一致：commit 后不过期，避免异步懒加载触发 MissingGreenlet。"""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory()
