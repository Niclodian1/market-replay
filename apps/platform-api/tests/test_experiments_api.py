"""实验台 API 集成测试（SQLite + 临时产物目录）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.experiments_api import (
    ExperimentCreate,
    cleanup,
    create_experiment,
    delete_experiment,
    delete_run,
    fork_experiment,
    get_experiment,
    get_run_chart,
    get_run_trades,
    list_experiments,
    restore_experiment,
    run_experiment,
)
from app.experiment_core import service
from tests._db import make_engine, make_session, prepare

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
pytestmark = pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")


def _spec() -> dict:
    return {
        "name": "ma20 breakout",
        "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
        "entry": {"all": [{"indicator": "ma.breakout", "params": {"periods": [20]}}]},
        "exit": {
            "any": [
                {"type": "stop", "atr": 1.5},
                {"type": "target", "r": 2.0},
                {"type": "time", "bars": 20},
            ]
        },
        "costs": {"fee_bps": 5, "slippage_bps": 2},
    }


def test_experiment_lifecycle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "experiments"))

    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            created = await create_experiment(
                ExperimentCreate(name="ma20", spec=_spec()), db=db
            )
            exp_id = created["id"]
            assert created["status"] == "draft"

            executed = await run_experiment(exp_id, db=db)
            assert executed["status"] == "succeeded", executed["error"]
            assert executed["metrics"]["n"] > 0
            run_id = executed["id"]

            root = service.artifact_root()
            directory = root / str(exp_id) / str(run_id)
            assert (directory / "spec.json").exists()
            assert (directory / "trades.json").exists()
            assert (directory / "metrics.json").exists()

            detail = await get_experiment(exp_id, db=db)
            assert len(detail["runs"]) == 1
            assert detail["status"] == "tested"

            trades = await get_run_trades(exp_id, run_id, limit=1000, db=db)
            assert trades["count"] == executed["metrics"]["n"]

            chart = await get_run_chart(exp_id, run_id, db=db)
            assert len(chart["candles"]) > 1000
            assert len(chart["markers"]) == 2 * trades["count"]

            forked = await fork_experiment(exp_id, db=db)
            assert forked["id"] != exp_id

            archived = await delete_experiment(exp_id, purge=False, db=db)
            assert archived["archived"] is True
            listed = await list_experiments(include_archived=False, db=db)
            assert all(item["id"] != exp_id for item in listed["experiments"])
            listed_all = await list_experiments(include_archived=True, db=db)
            assert any(item["id"] == exp_id for item in listed_all["experiments"])

            await restore_experiment(exp_id, db=db)
            restored = await get_experiment(exp_id, db=db)
            assert restored["archived_at"] is None

            await delete_run(run_id, db=db)
            assert not directory.exists()
            assert (await get_experiment(exp_id, db=db))["runs"] == []

            purged = await delete_experiment(exp_id, purge=True, db=db)
            assert purged["purged"] is True
            assert not (root / str(exp_id)).exists()
            with pytest.raises(Exception):
                await get_experiment(exp_id, db=db)
        await engine.dispose()

    asyncio.run(run())


def test_rejects_invalid_and_unknown_specs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "experiments"))

    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            with pytest.raises(Exception):
                await create_experiment(ExperimentCreate(name="broken", spec={"name": "x"}), db=db)
            bad = _spec()
            bad["entry"] = {"all": [{"indicator": "not.registered"}]}
            with pytest.raises(Exception):
                await create_experiment(ExperimentCreate(name="unknown", spec=bad), db=db)
        await engine.dispose()

    asyncio.run(run())
