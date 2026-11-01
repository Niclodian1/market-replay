"""实验运行控制：超时、后台执行、取消。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from app.api import experiments_api
from app.experiment_core import service
from app.models.models import Experiment, ExperimentRun
from tests._db import make_engine, make_session, prepare

TABLES = ("experiments", "experiment_runs", "experiment_trades")

SPEC = {
    "name": "ma20 breakout",
    "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
    "entry": {"all": [{"indicator": "ma.breakout", "params": {"periods": [20]}}]},
    "exit": {"any": [{"type": "target", "r": 2.0}]},
}


async def _seed(db) -> Experiment:
    experiment = Experiment(name=SPEC["name"], spec_json=SPEC)
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)
    return experiment


def test_real_timeout_marks_run_failed(monkeypatch) -> None:
    """真实超时：引擎跑 2s，1s 超时兜住并落库失败。"""

    def slow_runner(*args, **kwargs):
        time.sleep(2)
        raise AssertionError("超时后不应拿到结果")

    monkeypatch.setattr(service, "run_experiment", slow_runner)
    monkeypatch.setattr(service, "load_data", lambda spec: {})

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            experiment = await _seed(db)
            result = await service.execute(db, experiment, timeout_seconds=1)
            assert result.status == "failed"
            assert "超时" in (result.error or "")
        await engine.dispose()

    asyncio.run(run())


def test_timeout_branch_persists_error(monkeypatch) -> None:
    async def fake_wait_for(awaitable, timeout):  # noqa: ANN001
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(service.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(service, "load_data", lambda spec: {})

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            experiment = await _seed(db)
            result = await service.execute(db, experiment, timeout_seconds=5)
            assert result.status == "failed"
            assert "超时" in (result.error or "")
            stored = await db.get(ExperimentRun, result.id)
            assert stored is not None and stored.status == "failed"
        await engine.dispose()

    asyncio.run(run())


def test_background_run_can_be_cancelled(monkeypatch) -> None:
    started = asyncio.Event()

    def slow_runner(*args, **kwargs):
        time.sleep(2)
        raise AssertionError("被取消后不应完成")

    monkeypatch.setattr(service, "run_experiment", slow_runner)
    monkeypatch.setattr(service, "load_data", lambda spec: {})

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        factory = __import__("sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]).async_sessionmaker(
            engine, expire_on_commit=False
        )
        import app.db.session as db_session

        monkeypatch.setattr(db_session, "AsyncSessionLocal", factory)

        async with make_session(engine) as db:
            experiment = await _seed(db)
            queued = await service.create_run(db, experiment)
            assert queued.status == "queued"
            service.spawn_background(experiment.id, queued.id)
            started.set()
            await asyncio.sleep(0.2)

            cancelled = await service.cancel(db, queued)
            assert cancelled.status == "cancelled"
            assert cancelled.finished_at is not None

            await asyncio.sleep(0.2)
            stored = await db.get(ExperimentRun, queued.id)
            await db.refresh(stored)
            assert stored.status == "cancelled"
            assert service._RUN_TASKS.get(queued.id) is None
        await engine.dispose()

    asyncio.run(run())


def test_cancel_finished_run_conflicts() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            experiment = await _seed(db)
            finished = ExperimentRun(experiment_id=experiment.id, status="succeeded")
            db.add(finished)
            await db.commit()
            with pytest.raises(HTTPException) as excinfo:
                await service.cancel(db, finished)
            assert excinfo.value.status_code == 409
        await engine.dispose()

    asyncio.run(run())


def test_cancel_endpoint_returns_404_for_missing_run() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            with pytest.raises(HTTPException) as excinfo:
                await experiments_api.cancel_run(999, db=db)
            assert excinfo.value.status_code == 404
        await engine.dispose()

    asyncio.run(run())
