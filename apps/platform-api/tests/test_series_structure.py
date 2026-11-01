"""/api/series 统一出口：structure=true 时返回缠论结构，旧 /api/chanlun/analyze 只做转发。"""
from __future__ import annotations

import math

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chanlun_api import router as chanlun_router
from app.api.indicator_catalog_api import series_router

STRUCTURE_KEYS = {
    "strokes",
    "segments",
    "pivots_stroke",
    "pivots_segment",
    "buy_sell_points",
    "beichi_points",
    "top_fractals",
    "bot_fractals",
    "meta",
}


def _candles(n: int = 160) -> list[dict]:
    out = []
    for i in range(n):
        base = 100 + i * 0.2 + 8 * math.sin(i / 6.0)
        out.append({
            "time": 1_700_000_000 + i * 86_400,
            "open": round(base, 2),
            "high": round(base + 2, 2),
            "low": round(base - 2, 2),
            "close": round(base + 0.5, 2),
            "volume": 10.0,
        })
    return out


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(series_router, prefix="/api/series")
    app.include_router(chanlun_router, prefix="/api/chanlun")
    return TestClient(app)


def test_series_structure_returns_chanlun_overlay() -> None:
    response = _client().post(
        "/api/series",
        json={
            "symbol": "TEST-USD",
            "timeframe": "1day",
            "candles": _candles(),
            "series": [],
            "structure": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    structure = body["structure"]
    assert structure is not None
    assert STRUCTURE_KEYS <= set(structure)
    assert structure["meta"]["nBars"] == len(body["candles"])
    assert isinstance(structure["strokes"], list)
    assert isinstance(structure["top_fractals"], list)
    for point in structure["buy_sell_points"] + structure["beichi_points"]:
        if point.get("confirmedT") is not None:
            assert point["confirmedT"] >= point["t"]


def test_series_structure_off_by_default() -> None:
    response = _client().post(
        "/api/series",
        json={"symbol": "TEST-USD", "timeframe": "1day", "candles": _candles(), "series": []},
    )
    assert response.status_code == 200
    assert response.json()["structure"] is None


def test_series_structure_rejects_unsupported_timeframe() -> None:
    response = _client().post(
        "/api/series",
        json={
            "symbol": "TEST-USD",
            "timeframe": "15min",
            "candles": _candles(),
            "series": [],
            "structure": True,
        },
    )
    assert response.status_code == 400
    assert "1day/4h/1h" in response.json()["detail"]


def test_legacy_chanlun_endpoint_matches_series_structure() -> None:
    candles = _candles()
    client = _client()
    legacy = client.post(
        "/api/chanlun/analyze",
        json={"symbol": "TEST-USD", "timeframe": "1day", "candles": candles},
    )
    assert legacy.status_code == 200
    unified = client.post(
        "/api/series",
        json={
            "symbol": "TEST-USD",
            "timeframe": "1day",
            "candles": candles,
            "series": [],
            "structure": True,
        },
    ).json()["structure"]
    assert legacy.json() == unified
