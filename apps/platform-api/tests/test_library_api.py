"""指标库：三层概念、晋升门禁、归档/恢复/物理删除、bundle 导入导出。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import experiments_api, library_api
from app.experiment_core import service as experiment_service
from app.library import service
from app.models.models import Indicator, PromotionAudit
from tests._db import make_engine, make_session, prepare

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
pytestmark = pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")

PARAMS = {"periods": [20]}


def _spec() -> dict:
    return {
        "name": "ma20 breakout",
        "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
        "entry": {"all": [{"indicator": "ma.breakout", "params": PARAMS}]},
        "exit": {
            "any": [
                {"type": "stop", "atr": 1.5},
                {"type": "target", "r": 2.0},
                {"type": "time", "bars": 20},
            ]
        },
    }


def test_library_full_lifecycle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "experiments"))

    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            primitives = await library_api.list_primitives(db=db)
            assert primitives["count"] == 12
            assert all(item["deletable"] is False for item in primitives["primitives"])

            indicator = await service.create_indicator(
                db, name="ma20", primitive_id="ma.breakout", params=PARAMS, timeframes=["1day"]
            )
            await service.create_indicator(
                db, name="ma60", primitive_id="ma.breakout", params={"periods": [60]}, timeframes=["1day"]
            )
            detail = await library_api.get_indicator(indicator.id, db=db)
            assert detail["status"] == "draft"
            assert len(detail["versions"]) == 1
            version_id = detail["versions"][0]["id"]

            with pytest.raises(HTTPException):
                await service.create_indicator(
                    db, name="ma20", primitive_id="ma.breakout", params=PARAMS, timeframes=["1day"]
                )
            with pytest.raises(HTTPException):
                await service.create_indicator(
                    db, name="bad", primitive_id="nope", params={}, timeframes=["1day"]
                )

            # 无证据不能晋升
            with pytest.raises(HTTPException):
                await service.promote(db, indicator, await db.get(type(indicator), version_id) if False else await _version(db, version_id), experiment_run_ids=[])

            created = await experiments_api.create_experiment(
                experiments_api.ExperimentCreate(name="evidence", spec=_spec()), db=db
            )
            executed = await experiments_api.run_experiment(created["id"], db=db)
            version = await _version(db, version_id)
            promoted = await service.promote(
                db, indicator, version, experiment_run_ids=[executed["id"]], actor="tester"
            )
            assert promoted["indicator"]["status"] == "promoted"
            assert version.evidence_run_ids == [executed["id"]]
            assert version.metrics_snapshot_json["n"] > 30

            # 新版本 + 归档/恢复
            v2 = await service.add_version(db, indicator, params={"periods": [30]}, note="faster")
            assert v2.version_no == 2
            await service.demote(db, indicator, version, actor="tester", note="underperform")
            refreshed = await service.get_indicator(db, indicator.id)
            assert refreshed.current_version_id is None

            await service.archive(db, indicator, reason="效果不好")
            listed = await library_api.list_indicators(include_archived=False, db=db)
            assert all(item["id"] != indicator.id for item in listed["indicators"])
            archived = await library_api.list_indicators(include_archived=True, db=db)
            assert any(item["id"] == indicator.id for item in archived["indicators"])

            await service.restore(db, indicator)
            assert (await service.get_indicator(db, indicator.id)).archived_at is None

            audit = list(
                (
                    await db.execute(
                        select(PromotionAudit).where(PromotionAudit.indicator_id == indicator.id)
                    )
                ).scalars().all()
            )
            actions = [row.action for row in audit]
            assert {"create", "version", "promote", "demote", "archive", "restore"} <= set(actions)

            bundle = await service.export_bundle(db)
            assert bundle["schema"] == "indicator-bundle/1"
            assert len(bundle["indicators"]) == 2

            await service.purge(db, indicator)
            assert (await db.execute(select(Indicator).where(Indicator.id == indicator.id))).scalar_one_or_none() is None

        # 导入到一个全新的库
        engine2 = make_engine()
        await prepare(engine2)
        async with make_session(engine2) as db2:
            imported = await service.import_bundle(db2, bundle)
            assert len(imported["created"]) == 2  # bundle 是导出时的快照
            assert {item["name"] for item in imported["created"]} == {"ma20", "ma60"}
            again = await service.import_bundle(db2, bundle)
            assert again["created"] == [] and again["skipped"]
        await engine.dispose()
        await engine2.dispose()

    asyncio.run(run())


async def _version(db, version_id: int):
    from app.models.models import IndicatorVersion

    return await db.get(IndicatorVersion, version_id)
