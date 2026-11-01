"""
Twelve Data API 代理层。
所有对外部 API 的请求都在这里发出，不对前端暴露 API Key。
"""
import httpx
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings


# ─── 简单内存缓存（TTL 60s），避免同一请求重复打 API ────────────────────────
class _Cache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: int = 60) -> Optional[Any]:
        import time
        entry = self._store.get(key)
        if entry and time.time() - entry[1] < ttl:
            return entry[0]
        return None

    def set(self, key: str, value: Any) -> None:
        import time
        self._store[key] = (value, time.time())


_cache = _Cache()


def _parse_dt(dt_str: str) -> int:
    """Twelve Data datetime 字符串 → Unix 时间戳（UTC 秒）"""
    fmt = "%Y-%m-%d %H:%M:%S" if " " in dt_str else "%Y-%m-%d"
    dt = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class TwelveDataProvider:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)

    def _require_key(self) -> str:
        if not self._api_key:
            raise ValueError(
                "TWELVE_DATA_API_KEY 未配置，请在 .env 中填入 API Key。"
                " 申请地址：https://twelvedata.com/"
            )
        return self._api_key

    async def search(self, query: str) -> list[dict]:
        """搜索标的（不需要 API Key）"""
        cache_key = f"search:{query}"
        cached = _cache.get(cache_key, ttl=300)
        if cached is not None:
            return cached

        resp = await self._client.get(
            "/symbol_search",
            params={"symbol": query, "outputsize": 15},
        )
        resp.raise_for_status()
        data = resp.json()

        results = [
            {
                "symbol": item.get("symbol", ""),
                "name": item.get("instrument_name", ""),
                "type": item.get("instrument_type", "").lower(),
                "exchange": item.get("exchange", ""),
                "currency": item.get("currency", ""),
            }
            for item in data.get("data", [])
        ]
        _cache.set(cache_key, results)
        return results

    async def get_quote(self, symbol: str) -> dict:
        cache_key = f"quote:{symbol}"
        cached = _cache.get(cache_key, ttl=30)
        if cached is not None:
            return cached

        resp = await self._client.get(
            "/quote",
            params={"symbol": symbol, "apikey": self._require_key()},
        )
        resp.raise_for_status()
        data = resp.json()
        self._check_error(data)

        result = {
            "symbol": data.get("symbol", symbol),
            "name": data.get("name", ""),
            "price": float(data.get("close", 0) or 0),
            "change": float(data.get("change", 0) or 0),
            "change_percent": float(data.get("percent_change", 0) or 0),
            "volume": int(float(data.get("volume", 0) or 0)),
            "timestamp": data.get("datetime", ""),
        }
        _cache.set(cache_key, result)
        return result

    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 300,
    ) -> list[dict]:
        cache_key = f"ts:{symbol}:{interval}:{outputsize}"
        cached = _cache.get(cache_key, ttl=60)
        if cached is not None:
            return cached

        resp = await self._client.get(
            "/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "apikey": self._require_key(),
                "timezone": "UTC",
                "order": "ASC",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._check_error(data)

        candles = [
            {
                "time": _parse_dt(v["datetime"]),
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
                "volume": float(v.get("volume", 0) or 0),
            }
            for v in data.get("values", [])
        ]
        _cache.set(cache_key, candles)
        return candles

    @staticmethod
    def _check_error(data: dict) -> None:
        if isinstance(data, dict) and data.get("status") == "error":
            raise ValueError(data.get("message", "Twelve Data API 返回错误"))
        if isinstance(data, dict) and "code" in data and data["code"] != 200:
            raise ValueError(data.get("message", f"API Error {data['code']}"))


# ─── 单例 ──────────────────────────────────────────────────────────────────
_provider: Optional[TwelveDataProvider] = None


def get_provider() -> TwelveDataProvider:
    global _provider
    if _provider is None:
        _provider = TwelveDataProvider(settings.TWELVE_DATA_API_KEY)
    return _provider
