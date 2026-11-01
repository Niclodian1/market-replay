"""指标库影响面：删除/下线前返回事件、订阅计划、实验的引用统计。"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import func, select

from app.api import library_api
from app.library import service
from app.models.models import (
    Experiment,
    Indicator,
    IndicatorEvent,
    IndicatorVersion,
    MonitorPlan,
    User,
)
from tests._db import make_engine, make_session, prepare

TABLES = (
    "users",
    "monitor_plans",
    "experiments",
    "experiment_runs",
    "experiment_trades",
    "indicators",
    "indicator_versions",
    "promotion_audit",
    "indicator_events",
    "indicator_outcomes",
)


def _event(uid: str, version_id: int | None) -> IndicatorEvent:
    return IndicatorEvent(
        uid=uid,
        indicator_id="ma.breakout",
        indicator_version_id=version_id,
        symbol="BTC-USD",
        timeframe="1day",
        direction="long",
        bar_time=1_700_000_000,
        confirmed_at=1_700_000_000,
        price=100.0,
        params_hash="abcd1234",
    )


def test_impact_counts_events_plans_experiments() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            indicator = await service.create_indicator(
                db, name="ma20", primitive_id="ma.breakout", params={"periods": [20]}, timeframes=["1day"]
            )
            version = (
                await db.execute(
                    select(IndicatorVersion).where(
                        IndicatorVersion.indicator_id == indicator.id
                    )
                )
            ).scalars().one()

            db.add(User(id=1, username="default", password_hash="x"))
            await db.flush()
            db.add(
                MonitorPlan(
                    user_id=1,
                    name="订阅 ma20",
                    indicator_version_ids=json.dumps([version.id]),
                )
            )
            db.add(
                Experiment(
                    name="ma20 breakout",
                    spec_json={
                        "name": "ma20 breakout",
                        "entry": {"all": [{"indicator": "ma.breakout", "params": {"periods": [20]}}]},
                        "exit": {"any": [{"type": "target", "r": 2.0}]},
                    },
                )
            )
            db.add(_event("v1", version.id))
            db.add(_event("p1", None))
            await db.commit()

            impact = await service.indicator_impact(db, indicator)
            assert impact["primitive_id"] == "ma.breakout"
            assert impact["events"] == {"by_version": 1, "by_primitive": 2, "total": 3}
            assert impact["plans"]["count"] == 1
            assert impact["experiments"]["count"] == 1
            assert impact["has_references"] is True
            assert [item["id"] for item in impact["versions"]] == [version.id]

            detail = await library_api.indicator_impact(indicator.id, db=db)
            assert detail["plans"]["ids"] == impact["plans"]["ids"]

            # purge 前返回影响面，purge 后事件（事实）仍在
            purged = await library_api.delete_indicator(indicator.id, purge=True, reason="", db=db)
            assert purged["purged"] is True
            assert purged["impact"]["events"]["total"] == 3
            indicator_rows = await db.scalar(
                select(func.count()).select_from(Indicator).where(Indicator.id == indicator.id)
            )
            assert indicator_rows == 0
            remaining = (
                await db.execute(
                    select(IndicatorEvent).where(
                        IndicatorEvent.indicator_id == "ma.breakout"
                    )
                )
            ).scalars().all()
            assert len(remaining) == 2

    asyncio.run(run())


def test_impact_without_references() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            indicator = await service.create_indicator(
                db, name="ma60", primitive_id="ma.breakout", params={"periods": [60]}, timeframes=["1day"]
            )
            impact = await service.indicator_impact(db, indicator)
            assert impact["events"]["total"] == 0
            assert impact["plans"]["count"] == 0
            assert impact["experiments"]["count"] == 0
            assert impact["has_references"] is False

    asyncio.run(run())
