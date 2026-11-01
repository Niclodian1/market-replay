"""缠论图表分析 API — 供前端图表页实时绘制 笔/线段/中枢/买卖点/分型。

前端把当前可见 K 线直接 POST 上来，复用 chanlun_service 在同一份数据上计算，
保证图表 K 线与缠论结构严格对齐。

重构后该端点只做「兼容转发」：真正的 payload 构造在
``chanlun_service.analyze_chart``，新前端统一走 ``POST /api/series``
（``structure=true``）。保留本路由并标记 ``deprecated``，随时可回滚。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import chanlun_service

router = APIRouter()

_TF_MAP = chanlun_service._CHART_TF_MAP  # noqa: SLF001

# 兼容旧引用（scripts/export_chanlun_golden.py 等）：实现已迁到 chanlun_service
_beichi_points = chanlun_service._chart_beichi_points  # noqa: SLF001
_merge_points = chanlun_service._chart_merge_points  # noqa: SLF001


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


@router.post("/analyze", deprecated=True)
async def analyze(req: ChanlunAnalyzeRequest) -> dict[str, Any]:
    """对前端提供的 K 线跑完整缠论链路，返回 overlay payload（deprecated）。

    新代码请用 ``POST /api/series``（``structure=true``），响应结构与本端点一致。
    """
    if req.timeframe not in _TF_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"缠论暂仅支持 1day/4h/1h，收到: {req.timeframe}",
        )
    if not chanlun_service.chanlun_available():
        raise HTTPException(status_code=503, detail="缠论引擎不可用")
    candles = [c.model_dump() for c in req.candles]
    try:
        return chanlun_service.analyze_chart(req.symbol, req.timeframe, candles)
    except chanlun_service.ChanlunUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - 引擎内部异常
        raise HTTPException(status_code=500, detail=f"缠论分析失败: {exc}") from exc
