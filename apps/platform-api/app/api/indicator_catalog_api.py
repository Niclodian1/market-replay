"""指标目录与事件 API（P0/P1 契约出口）。

- ``GET /api/indicator-catalog``         指标目录（前端下拉框 / 参数表单的数据源）
- ``GET /api/indicator-catalog/{id}``    单个指标定义
- ``POST /api/indicator-events``         给定 K 线，返回统一事件（看盘/练习/实验共用）

事件接口刻意「无状态」：K 线由调用方传入（与 ``/api/chanlun/analyze`` 一致），
因此不依赖数据库，可离线测试，也方便前端在练习模式里按前缀请求。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import chanlun_service
from app.core.indicator_provider import get_registry
from app.indicator_core import UnknownIndicator
from app.indicator_core.series import SERIES_CATALOG, compute_series, sanitize_values

catalog_router = APIRouter()
events_router = APIRouter()
series_router = APIRouter()


class CandleIn(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class SeriesRequest(BaseModel):
    id: str = Field(description="序列 id：ma / macd / rsi / atr")
    params: dict[str, Any] = Field(default_factory=dict)


class SeriesComputeRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    candles: list[CandleIn] = Field(min_length=1)
    series: list[SeriesRequest] = Field(
        default_factory=list,
        description="要计算的序列；留空则返回空 series（只取事件时用）",
    )
    as_of: int | None = Field(default=None, description="严格前缀：只用 <= as_of 的 K 线计算")
    events: bool = Field(default=False, description="是否同时返回事件")
    indicators: list[str] | None = None
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    confirmed_before: int | None = None
    structure: bool = Field(
        default=False,
        description="是否附带缠论结构（笔/线段/中枢/买卖点），仅 1day/4h/1h 支持",
    )


class EventsRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    candles: list[CandleIn] = Field(min_length=1)
    indicators: list[str] | None = None
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    as_of: int | None = Field(
        default=None,
        description="严格前缀模式：只把 <= as_of 的 K 线交给引擎（等价于当时重算）",
    )
    confirmed_before: int | None = Field(
        default=None,
        description="预计算过滤模式：全量计算后只保留 confirmed_at <= 该时刻的事件（练习用）",
    )


@catalog_router.get("")
def list_indicators() -> dict[str, Any]:
    registry = get_registry()
    return {
        "schema_version": "ev1",
        "indicators": registry.catalog(),
        "count": len(registry.names),
    }


@catalog_router.get("/{indicator_id}")
def get_indicator(indicator_id: str) -> dict[str, Any]:
    try:
        detector = get_registry().get(indicator_id)
    except UnknownIndicator as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detector.spec.to_dict()


@events_router.post("")
def compute_events(req: EventsRequest) -> dict[str, Any]:
    registry = get_registry()
    wanted = req.indicators or registry.names
    unknown = [item for item in wanted if item not in registry.names]
    if unknown:
        raise HTTPException(status_code=404, detail=f"未注册的指标: {unknown}")

    bars = [candle.model_dump() for candle in req.candles]
    if req.as_of is not None:
        bars = [bar for bar in bars if bar["time"] <= req.as_of]

    started = time.perf_counter()
    try:
        events = registry.run_many(wanted, bars, req.symbol, req.timeframe, req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000

    if req.confirmed_before is not None:
        events = [event for event in events if event.confirmed_at <= req.confirmed_before]

    return {
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe,
        "as_of": req.as_of,
        "confirmed_before": req.confirmed_before,
        "count": len(events),
        "elapsed_ms": round(elapsed_ms, 1),
        "events": [event.to_dict() for event in events],
    }


@series_router.get("/catalog")
def list_series_catalog() -> dict[str, Any]:
    return {
        "series": list(SERIES_CATALOG.values()),
        "count": len(SERIES_CATALOG),
    }


@series_router.post("")
def compute(req: SeriesComputeRequest) -> dict[str, Any]:
    """一个请求喂饱看盘/练习：K 线 + 数值序列 +（可选）事件。"""
    bars = [candle.model_dump() for candle in req.candles]
    if req.as_of is not None:
        bars = [bar for bar in bars if bar["time"] <= req.as_of]

    try:
        series = compute_series(bars, [item.model_dump() for item in req.series])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    events: list[dict[str, Any]] = []
    if req.events:
        registry = get_registry()
        wanted = req.indicators or registry.names
        unknown = [item for item in wanted if item not in registry.names]
        if unknown:
            raise HTTPException(status_code=404, detail=f"未注册的指标: {unknown}")
        try:
            computed = registry.run_many(wanted, bars, req.symbol, req.timeframe, req.params)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.confirmed_before is not None:
            computed = [e for e in computed if e.confirmed_at <= req.confirmed_before]
        events = [event.to_dict() for event in computed]

    structure: dict[str, Any] | None = None
    if req.structure:
        if req.timeframe not in chanlun_service.CHANLUN_TIMEFRAMES:
            raise HTTPException(
                status_code=400,
                detail=f"缠论结构暂仅支持 1day/4h/1h，收到: {req.timeframe}",
            )
        if not chanlun_service.chanlun_available():
            raise HTTPException(status_code=503, detail="缠论引擎不可用")
        try:
            structure = chanlun_service.analyze_chart(req.symbol, req.timeframe, bars)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe,
        "as_of": req.as_of,
        "count": len(bars),
        "candles": bars,
        "structure": structure,
        "series": [
            {**item.to_dict(), "values": sanitize_values(item.values)} for item in series
        ],
        "events": events,
    }
