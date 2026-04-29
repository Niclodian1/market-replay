"""
OKX 公开 K 线 API（无需 API Key）。
国内可直接访问，仅支持加密货币交易对。
1h/4h 历史可回溯至 2018 年（各币种上线时间不同）。

OKX 分页规则（newest-first 排序）：
  after=ts  → 返回比 ts 更旧的数据（往历史翻页）
  before=ts → 返回比 ts 更新的数据（往现在翻页）
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import httpx

_BASE_URL = "https://www.okx.com"

_INTERVAL_MAP: dict[str, str] = {
    "1h":    "1H",
    "4h":    "4H",
    "1day":  "1D",
    "1week": "1W",
}

# OKX 各周期理论最早可拉取的历史（天数）
_FULL_HISTORY_DAYS: dict[str, int] = {
    "1h":    365 * 8,
    "4h":    365 * 8,
    "1day":  365 * 10,
    "1week": 365 * 10,
}

# 历史起点（2018-01-01），大多数主流币上线后即有数据
_HISTORY_START_MS = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def is_crypto_symbol(symbol: str) -> bool:
    """判断 symbol 是否为加密货币（适合走 OKX）。"""
    s = symbol.upper()
    return any(s.endswith(sfx) for sfx in ("-USD", "-USDT", "-BTC", "-ETH", "-BNB"))


def to_okx_inst_id(symbol: str) -> str:
    """将通用 symbol 转为 OKX instId 格式。BTC-USD → BTC-USDT，ETH-USDT → ETH-USDT。"""
    s = symbol.upper()
    # BTC-USD → BTC-USDT
    if s.endswith("-USD") and not s.endswith("-USDT"):
        s = s[:-4] + "-USDT"
    return s


def _parse_candles(data: list[list]) -> list[dict]:
    """OKX candle 响应解析。格式: [ts_ms, o, h, l, c, vol, ...]，newest first。"""
    candles: list[dict] = []
    for row in data:
        try:
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = float(row[5])
            if any(math.isnan(x) for x in [o, h, l, c]):
                continue
            candles.append({
                "time":   int(row[0]) // 1000,  # ms → 秒
                "open":   o,
                "high":   h,
                "low":    l,
                "close":  c,
                "volume": v,
            })
        except (ValueError, IndexError, TypeError):
            continue
    return candles


class BinanceProvider:
    """对外仍用 BinanceProvider 名称，实际调用 OKX API。"""

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        inst_id: str,
        bar: str,
        after_ms: int | None = None,
        before_ms: int | None = None,
    ) -> list[list]:
        """拉取单页 K 线（最多 100 根）。"""
        params: dict = {"instId": inst_id, "bar": bar, "limit": "100"}
        if after_ms is not None:
            params["after"] = str(after_ms)
        if before_ms is not None:
            params["before"] = str(before_ms)

        resp = await client.get(f"{_BASE_URL}/api/v5/market/history-candles", params=params)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != "0":
            raise ValueError(f"OKX API error: {result.get('msg')}")
        return result.get("data", [])

    async def get_full_history(self, symbol: str, interval: str) -> list[dict]:
        """
        从当前时间向历史分页拉取全量 K 线，直到 _HISTORY_START_MS。
        OKX 每次最多 100 根，用 after=oldest_ts 往历史翻页。
        """
        inst_id = to_okx_inst_id(symbol)
        bar     = _INTERVAL_MAP.get(interval, "1H")

        all_candles: list[dict] = []
        after_ms: int | None = None  # 首页不传 after，拿最新 100 根

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                data = await self._fetch_page(client, inst_id, bar, after_ms=after_ms)
                if not data:
                    break

                candles = _parse_candles(data)
                all_candles.extend(candles)

                # data 按 newest-first 排列，最后一个是最旧的
                oldest_ts_ms = int(data[-1][0])
                if oldest_ts_ms <= _HISTORY_START_MS:
                    break  # 已拉到起点

                after_ms = oldest_ts_ms  # 下一页：拉比当前最旧更旧的数据

                if len(data) < 100:
                    break

                await asyncio.sleep(0.05)  # 礼貌性限速

        # 转为时间升序
        all_candles.sort(key=lambda c: c["time"])
        return all_candles

    async def get_time_series_since(
        self,
        symbol: str,
        interval: str,
        since_ts: int,
    ) -> list[dict]:
        """
        拉取 since_ts（秒）之后的增量 K 线。
        OKX before=ts 返回比 ts 更新的数据，newest-first。
        """
        inst_id  = to_okx_inst_id(symbol)
        bar      = _INTERVAL_MAP.get(interval, "1H")
        before_ms = since_ts * 1000  # 取比 since_ts 更新的数据

        all_candles: list[dict] = []

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                data = await self._fetch_page(client, inst_id, bar, before_ms=before_ms)
                if not data:
                    break

                candles = _parse_candles(data)
                all_candles.extend(candles)

                if len(data) < 100:
                    break

                # 继续往更新方向翻页（before 参数取最新那根的 ts）
                before_ms = int(data[0][0])
                await asyncio.sleep(0.05)

        all_candles.sort(key=lambda c: c["time"])
        return [c for c in all_candles if c["time"] > since_ts]


def get_full_history_days(interval: str) -> int:
    """返回 OKX 对该周期可提供的最大历史天数。"""
    return _FULL_HISTORY_DAYS.get(interval, 365 * 8)
