"""缠论图表分析 API — 供前端图表页实时绘制 笔/线段/中枢/买卖点/分型。

前端把当前可见 K 线直接 POST 上来，复用 chanlun_service（fenxing 管线）
在同一份数据上计算，保证图表 K 线与缠论结构严格对齐。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import chanlun_service

router = APIRouter()

# 与 chanlun_service 一致：后端周期名 -> fenxing 周期名（fenxing 仅定义 1D/4H/1H 联动）
_TF_MAP = {"1day": "1D", "4h": "4H", "1h": "1H"}


class CandleIn(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class ChanlunAnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    candles: list[CandleIn] = Field(..., min_length=20)


def _point(p: dict, mode: str) -> dict:
    """买卖点精简为前端所需字段（含 why/sub/upper 供悬浮提示）。"""
    return {
        "t": p.get("t"),
        "p": p.get("p"),
        "k": p.get("k"),
        "mode": mode,
        "state": p.get("state"),
        "tier": p.get("tier"),
        "confirmedT": p.get("confirmedT"),
        "why": p.get("why"),
        "sub": p.get("sub"),
        "upper": p.get("upper"),
    }


def _beichi_points(level: dict[str, Any]) -> list[dict]:
    """背驰买/卖（抢先层 beichiPointsRaw）：结构上与普通买卖点并列，
    单独返回避免与一/二/三类买卖点混淆；含 alertT/ratio/trigger 供提示。"""
    out: list[dict] = []
    for e in level.get("beichiPointsRaw", []):
        out.append({
            "t": e.get("t"),
            "p": e.get("p"),
            "k": e.get("k"),
            "mode": "fast",
            "state": e.get("state"),
            "tier": None,
            "confirmedT": e.get("confirmedT"),
            "alertT": e.get("alertT"),
            "ratio": e.get("ratio"),
            "trigger": e.get("trigger"),
            "why": e.get("why"),
            "sub": "",
            "upper": "",
        })
    out.sort(key=lambda x: x["t"] or 0)
    return out


def _merge_points(level: dict[str, Any], tf_seconds: int) -> list[dict]:
    """合并笔级(stroke)与线段级(seg)三类买卖点。

    同 k + 方向且位置相近(±3 bar) 时保留线段级（更稳），与
    chanlun_service.extract_signals 的去重口径一致。
    """
    seg = [_point(p, "seg") for p in
           level.get("buyPointsSegmentRaw", []) + level.get("sellPointsSegmentRaw", [])]
    stk = [_point(p, "stroke") for p in
           level.get("buyPointsStrokeRaw", []) + level.get("sellPointsStrokeRaw", [])]
    if seg and stk:
        win = 3 * tf_seconds
        seg_keys: dict[str, list[int]] = {}
        for p in seg:
            seg_keys.setdefault(p["k"], []).append(p["t"] or 0)
        stk = [
            p for p in stk
            if not any(abs((p["t"] or 0) - t) <= win for t in seg_keys.get(p["k"], []))
        ]
    merged = seg + stk
    merged.sort(key=lambda x: x["t"] or 0)
    return merged


@router.post("/analyze")
async def analyze(req: ChanlunAnalyzeRequest) -> dict[str, Any]:
    """对前端提供的 K 线跑完整缠论链路，返回 raw 视图结构。"""
    if req.timeframe not in _TF_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"缠论暂仅支持 1day/4h/1h，收到: {req.timeframe}",
        )
    if not chanlun_service.chanlun_available():
        raise HTTPException(status_code=503, detail="fenxing 缠论模块不可用")
    try:
        candles = [c.model_dump() for c in req.candles]
        levels = chanlun_service.analyze_symbol({req.timeframe: candles})
    except chanlun_service.ChanlunUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"缠论分析失败: {exc}")

    f_tf = _TF_MAP[req.timeframe]
    lvl = levels.get(f_tf)
    if not lvl:
        raise HTTPException(status_code=500, detail="缠论分析无输出")

    tf_seconds = chanlun_service._TIMEFRAME_SECONDS.get(req.timeframe, 86_400)
    return {
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe,
        "strokes": lvl.get("strokesRaw", []),
        "segments": lvl.get("segmentsRaw", []),
        "pivots_stroke": lvl.get("pivotsStrokeRaw", []),
        "pivots_segment": lvl.get("pivotsSegmentRaw", []),
        "buy_sell_points": _merge_points(lvl, tf_seconds),
        "beichi_points": _beichi_points(lvl),
        "top_fractals": lvl.get("topMarkers", []),
        "bot_fractals": lvl.get("bottomMarkers", []),
        "meta": lvl.get("meta", {}),
    }