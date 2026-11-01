"""收盘对齐的扫描窗口：把「每分钟轮询」变成「K 线收盘后 5 分钟内扫一次」。

调度器每分钟 tick 一次，本模块只做纯计算：
- 对齐扫描：某周期最新收盘 bar 还没扫过，且距收盘 <= grace 秒 → 需要扫；
- 兜底扫描：距上次兜底扫描 >= fallback 秒 → 强制扫一遍（进程重启/错过窗口后的补扫）。
"""
from __future__ import annotations

from typing import Iterable

TIMEFRAME_SECONDS: dict[str, int] = {
    "15min": 900,
    "30min": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "12h": 43_200,
    "1day": 86_400,
    "1d": 86_400,
}

DEFAULT_GRACE_SECONDS = 300
DEFAULT_FALLBACK_SECONDS = 1_800


def timeframe_seconds(timeframe: str) -> int | None:
    return TIMEFRAME_SECONDS.get(timeframe.strip().lower())


def closed_bar_time(now_ts: int, timeframe: str) -> int | None:
    """返回该周期在 now_ts 之前最近一根**已收盘** K 线的收盘时间戳。"""
    seconds = timeframe_seconds(timeframe)
    if seconds is None:
        return None
    return now_ts - (now_ts % seconds)


def due_timeframes(
    now_ts: int,
    timeframes: Iterable[str],
    scanned_close: dict[str, int],
    last_fallback_ts: int,
    *,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    fallback_seconds: int = DEFAULT_FALLBACK_SECONDS,
) -> tuple[list[str], str]:
    """返回 (需要扫描的周期列表, 原因)。

    原因取值：``aligned``（刚收盘）、``fallback``（兜底补扫）、``idle``（无需扫描）。
    """
    aligned: list[str] = []
    for timeframe in timeframes:
        key = timeframe.strip().lower()
        close = closed_bar_time(now_ts, key)
        if close is None:
            continue
        if scanned_close.get(key) == close:
            continue
        if now_ts - close <= grace_seconds:
            aligned.append(key)
    if aligned:
        return aligned, "aligned"
    if now_ts - last_fallback_ts >= fallback_seconds:
        known = [tf.strip().lower() for tf in timeframes if timeframe_seconds(tf) is not None]
        return known, "fallback"
    return [], "idle"
