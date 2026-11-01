"""数值序列（Series）：前端只画不算。

与 ``Event`` 的分工：
- ``Event`` 是「发生了什么」，带因果锚点；
- ``Series`` 是「这条线长什么样」，与 K 线按索引对齐，warmup 期为 ``None``。

这里只实现纯函数（SMA/EMA/MACD/RSI/ATR），任何消费方都能复用；
新增指标 = 在这里加一个纯函数 + 在 ``SERIES_CATALOG`` 里加一条元数据。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

Panel = str  # "price" | "sub"（副图）


@dataclass(frozen=True)
class Series:
    key: str
    label: str
    values: list[float | None]
    panel: Panel = "price"
    kind: str = "line"  # line | histogram
    color: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "panel": self.panel,
            "kind": self.kind,
            "color": self.color,
            "values": self.values,
            "meta": self.meta,
        }


# ── 基础平滑 ────────────────────────────────────────────────────────────────

def sma(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period 必须为正整数")
    out: list[float | None] = [None] * len(values)
    window = 0.0
    for i, value in enumerate(values):
        window += value
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def ema(values: Sequence[float | None], period: int) -> list[float | None]:
    """指数移动平均；跳过前导 ``None``，至少积累 period 个有效值后才输出。"""
    if period <= 0:
        raise ValueError("period 必须为正整数")
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (period + 1)
    prev: float | None = None
    seen = 0
    for i, value in enumerate(values):
        if value is None:
            continue
        seen += 1
        prev = value if prev is None else value * alpha + prev * (1 - alpha)
        if seen >= period:
            out[i] = prev
    return out


# ── 指标函数 ────────────────────────────────────────────────────────────────

def ma_series(
    bars: Sequence[dict[str, Any]],
    periods: Sequence[int] = (20, 60, 120),
    kind: str = "sma",
) -> list[Series]:
    closes = [float(bar["close"]) for bar in bars]
    if not periods:
        raise ValueError("至少需要一个均线周期")
    if any(period <= 0 for period in periods):
        raise ValueError("均线周期必须为正整数")
    func = sma if kind == "sma" else ema if kind == "ema" else None
    if func is None:
        raise ValueError(f"不支持的均线类型: {kind}")
    return [
        Series(
            key=f"ma_{kind}_{period}",
            label=f"MA{period}" if kind == "sma" else f"EMA{period}",
            values=func(closes, period),  # type: ignore[arg-type]
            panel="price",
            color=None,
            meta={"kind": kind, "period": period},
        )
        for period in periods
    ]


def macd_series(
    bars: Sequence[dict[str, Any]],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[Series]:
    if not (0 < fast < slow) or signal <= 0:
        raise ValueError("MACD 参数需满足 0 < fast < slow 且 signal > 0")
    closes = [float(bar["close"]) for bar in bars]
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    dif: list[float | None] = [
        None if f is None or s is None else f - s for f, s in zip(fast_ema, slow_ema)
    ]
    dea = ema(dif, signal)
    hist: list[float | None] = [
        None if d is None or e is None else (d - e) * 2 for d, e in zip(dif, dea)
    ]
    meta = {"fast": fast, "slow": slow, "signal": signal}
    return [
        Series("macd_dif", f"DIF({fast},{slow})", dif, "sub", meta=meta),
        Series("macd_dea", f"DEA({signal})", dea, "sub", meta=meta),
        Series("macd_hist", "MACD", hist, "sub", kind="histogram", meta=meta),
    ]


def rsi_series(bars: Sequence[dict[str, Any]], period: int = 14) -> list[Series]:
    if period <= 0:
        raise ValueError("RSI 周期必须为正整数")
    closes = [float(bar["close"]) for bar in bars]
    out: list[float | None] = [None] * len(closes)
    avg_gain: float | None = None
    avg_loss: float | None = None
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if i <= period:
            if i == period:
                avg_gain = sum(
                    max(closes[j] - closes[j - 1], 0.0) for j in range(1, period + 1)
                ) / period
                avg_loss = sum(
                    max(closes[j - 1] - closes[j], 0.0) for j in range(1, period + 1)
                ) / period
                out[i] = _rsi_value(avg_gain, avg_loss)
            continue
        assert avg_gain is not None and avg_loss is not None
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return [Series("rsi", f"RSI({period})", out, "sub", meta={"period": period})]


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_series(bars: Sequence[dict[str, Any]], period: int = 14) -> list[Series]:
    """Wilder ATR；强度/止损口径与 ``outcome_labeling`` 保持一致。"""
    if period <= 0:
        raise ValueError("ATR 周期必须为正整数")
    if not bars:
        return [Series("atr", f"ATR({period})", [], "sub", meta={"period": period})]
    true_ranges: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        if prev_close is None:
            true_ranges.append(high - low)
        else:
            true_ranges.append(
                max(high - low, abs(high - prev_close), abs(low - prev_close))
            )
        prev_close = close

    out: list[float | None] = [None] * len(bars)
    avg: float | None = None
    for i, tr in enumerate(true_ranges):
        if i < period - 1:
            continue
        if i == period - 1:
            avg = sum(true_ranges[:period]) / period
        else:
            assert avg is not None
            avg = (avg * (period - 1) + tr) / period
        out[i] = avg
    return [Series("atr", f"ATR({period})", out, "sub", meta={"period": period})]


# ── 目录与调度 ──────────────────────────────────────────────────────────────

_MA_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "periods": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "default": [20, 60, 120],
        },
        "kind": {"type": "string", "enum": ["sma", "ema"], "default": "sma"},
    },
    "additionalProperties": False,
}

SERIES_CATALOG: dict[str, dict[str, Any]] = {
    "ma": {
        "id": "ma",
        "label": "均线",
        "panel": "price",
        "description": "SMA/EMA，同一条线在看盘、练习、实验里共用",
        "params_schema": _MA_PARAMS,
    },
    "macd": {
        "id": "macd",
        "label": "MACD",
        "panel": "sub",
        "description": "DIF/DEA/柱，参数默认 12/26/9",
        "params_schema": {
            "type": "object",
            "properties": {
                "fast": {"type": "integer", "default": 12, "minimum": 1},
                "slow": {"type": "integer", "default": 26, "minimum": 2},
                "signal": {"type": "integer", "default": 9, "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    "rsi": {
        "id": "rsi",
        "label": "RSI",
        "panel": "sub",
        "description": "Wilder RSI，默认 14",
        "params_schema": {
            "type": "object",
            "properties": {"period": {"type": "integer", "default": 14, "minimum": 1}},
            "additionalProperties": False,
        },
    },
    "atr": {
        "id": "atr",
        "label": "ATR",
        "panel": "sub",
        "description": "Wilder ATR，与止损/结果标注口径一致",
        "params_schema": {
            "type": "object",
            "properties": {"period": {"type": "integer", "default": 14, "minimum": 1}},
            "additionalProperties": False,
        },
    },
}

_SERIES_FUNCS: dict[str, Callable[..., list[Series]]] = {
    "ma": ma_series,
    "macd": macd_series,
    "rsi": rsi_series,
    "atr": atr_series,
}


def compute_series(
    bars: Sequence[dict[str, Any]],
    requests: Sequence[dict[str, Any]],
) -> list[Series]:
    """按请求顺序产出序列；未知 id / 非法参数直接报错，不做静默降级。"""
    out: list[Series] = []
    for request in requests:
        series_id = str(request.get("id") or "")
        params = dict(request.get("params") or {})
        func = _SERIES_FUNCS.get(series_id)
        if func is None:
            raise ValueError(f"未知序列: {series_id!r}（可选: {sorted(_SERIES_FUNCS)}）")
        try:
            out.extend(func(bars, **params))
        except TypeError as exc:
            raise ValueError(f"序列 {series_id} 参数非法: {exc}") from exc
        except ValueError:
            raise
    return out


def sanitize_values(values: Sequence[float | None]) -> list[float | None]:
    """把 NaN/Inf 统一成 None，避免 JSON 序列化出 ``NaN``。"""
    return [
        None if value is None or not math.isfinite(value) else round(float(value), 6)
        for value in values
    ]
