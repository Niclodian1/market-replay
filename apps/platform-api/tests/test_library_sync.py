"""指标库双环境推送/拉取：走 HTTP 但可注入解析函数，离线可测。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.library import service
from app.models.models import PromotionAudit
from tests._db import make_engine, make_session, prepare

PARAMS = {"periods": [20]}


def test_push_and_pull(monkeypatch) -> None:
    pushed: list[dict] = []

    async def fake_post(url, payload, headers):
        pushed.append({"url": url, "payload": payload, "headers": headers})
        return {"created": [{"id": 99, "name": "ma20"}], "skipped": []}

    monkeypatch.setattr(service, "_post_json", fake_post)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            indicator = await service.create_indicator(
                db, name="ma20", primitive_id="ma.breakout", params=PARAMS, timeframes=["1day"]
            )
            result = await service.push_bundle(
                db, target_url="http://prod:8000/", token="secret", actor="tester"
            )
            assert result["pushed"] == 1
            assert result["target"] == "http://prod:8000"
            assert pushed[0]["url"] == "http://prod:8000/api/library/import"
            assert pushed[0]["headers"]["Authorization"] == "Bearer secret"
            assert pushed[0]["payload"]["schema"] == service.BUNDLE_SCHEMA

            audits = list((await db.execute(select(PromotionAudit))).scalars().all())
            assert any(row.action == "push" and row.actor == "tester" for row in audits)
            assert indicator.id

            bundle = await service.export_bundle(db)
            bundle["indicators"][0]["name"] = "ma20-from-prod"

            async def fake_get(url, headers):
                assert "/api/library/export" in url
                return bundle

            monkeypatch.setattr(service, "_get_json", fake_get)
            pulled = await service.pull_bundle(db, source_url="http://prod:8000", actor="tester")
            assert pulled["pulled"] == 1
            assert pulled["created"][0]["name"] == "ma20-from-prod"

            again = await service.pull_bundle(db, source_url="http://prod:8000")
            assert again["created"] == []
            assert again["skipped"][0]["reason"] == "同名指标已存在"
        await engine.dispose()

    asyncio.run(run())


def test_remote_url_must_be_http() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            with pytest.raises(HTTPException) as excinfo:
                await service.push_bundle(db, target_url="ftp://prod")
            assert excinfo.value.status_code == 400
            with pytest.raises(HTTPException):
                await service.pull_bundle(db, source_url="file:///tmp/bundle.json")
        await engine.dispose()

    asyncio.run(run())
