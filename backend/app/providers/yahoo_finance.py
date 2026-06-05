"""
Yahoo Finance 数据层 —— 基于 yfinance 库。
无需 API Key，免费，支持股票 / ETF / 指数 / 加密货币。

symbol 格式说明：
  股票/ETF：AAPL、SPY、QQQ
  加密货币：BTC-USD、ETH-USD（也接受 BTC/USD，自动转换）
  指数：    ^GSPC（S&P500）、^IXIC（Nasdaq）、^DJI
"""
import asyncio
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import yfinance as yf

# ─── 内存缓存 ────────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self, maxsize: int = 256) -> None:
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str, ttl: int = 60) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() - entry[1] >= ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return entry[0]

    def set(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        elif len(self._store) >= self._maxsize:
            self._store.popitem(last=False)
        self._store[key] = (value, time.time())


_cache = _Cache()

# ─── 周期映射 ────────────────────────────────────────────────────────────────
_INTERVAL_MAP: dict[str, str] = {
    "15min": "15m",
    "1h":    "1h",
    "4h":    "1h",   # 拉 1h 后聚合成 4h
    "1day":  "1d",
    "1week": "1wk",
}

_PERIOD_MAP: dict[str, str] = {
    "15min": "60d",
    "1h":    "2y",
    "4h":    "2y",
    "1day":  "5y",
    "1week": "max",
}

_TYPE_MAP: dict[str, str] = {
    "equity":         "stock",
    "etf":            "etf",
    "mutualfund":     "fund",
    "index":          "index",
    "cryptocurrency": "crypto",
    "currency":       "currency",
    "futures":        "futures",
}


def _normalize(symbol: str) -> str:
    """BTC/USD → BTC-USD；A 股 6 位数字自动补交易所后缀"""
    sym = symbol.replace("/", "-")
    # A 股：6 位纯数字，按首位判断交易所
    if sym.isdigit() and len(sym) == 6:
        if sym.startswith(("6",)):
            return sym + ".SS"   # 上交所
        elif sym.startswith(("0", "3")):
            return sym + ".SZ"   # 深交所
        elif sym.startswith(("4", "8")):
            return sym + ".BJ"   # 北交所
    return sym


def _df_to_candles(df: pd.DataFrame) -> list[dict]:
    """pandas DataFrame → 标准 candle dict 列表"""
    candles = []
    for ts_idx, row in df.iterrows():
        # 转 UTC Unix 时间戳
        try:
            unix_ts = int(ts_idx.timestamp())
        except Exception:
            unix_ts = int(pd.Timestamp(ts_idx).timestamp())

        o, h, l, c = row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")
        v = row.get("Volume", 0)

        # 过滤 None / NaN
        if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in [o, h, l, c]):
            continue

        candles.append({
            "time":   unix_ts,
            "open":   float(o),   # type: ignore[arg-type]
            "high":   float(h),   # type: ignore[arg-type]
            "low":    float(l),   # type: ignore[arg-type]
            "close":  float(c),   # type: ignore[arg-type]
            "volume": float(v or 0),
        })
    return candles


def _aggregate_to_4h(candles: list[dict]) -> list[dict]:
    """将 1h K线按 UTC 4 小时窗口聚合为 4h"""
    if not candles:
        return []
    window = 4 * 3600
    groups: dict[int, list[dict]] = {}
    for c in candles:
        key = (c["time"] // window) * window
        groups.setdefault(key, []).append(c)

    result = []
    for key in sorted(groups):
        g = groups[key]
        result.append({
            "time":   g[0]["time"],
            "open":   g[0]["open"],
            "high":   max(x["high"] for x in g),
            "low":    min(x["low"]  for x in g),
            "close":  g[-1]["close"],
            "volume": sum(x["volume"] for x in g),
        })
    return result


# ─── Provider ────────────────────────────────────────────────────────────────
class YahooFinanceProvider:

    # ── 搜索 ──────────────────────────────────────────────────────────────
    async def search(self, query: str) -> list[dict]:
        cache_key = f"yf:search:{query}"
        if (cached := _cache.get(cache_key, ttl=300)) is not None:
            return cached

        def _do() -> list[dict]:
            try:
                res = yf.Search(query, max_results=15, news_count=0)
                quotes = res.quotes or []
            except Exception:
                quotes = []

            results = []
            for item in quotes:
                qt = str(item.get("quoteType", "")).lower()
                results.append({
                    "symbol":   item.get("symbol", ""),
                    "name":     item.get("longname") or item.get("shortname") or "",
                    "type":     _TYPE_MAP.get(qt, qt),
                    "exchange": item.get("exchange", ""),
                    "currency": item.get("currency", ""),
                })
            return results

        result = await asyncio.to_thread(_do)
        _cache.set(cache_key, result)
        return result

    # ── 报价 ──────────────────────────────────────────────────────────────
    async def get_quote(self, symbol: str) -> dict:
        yf_sym = _normalize(symbol)
        cache_key = f"yf:quote:{yf_sym}"
        if (cached := _cache.get(cache_key, ttl=30)) is not None:
            return cached

        def _do() -> dict:
            ticker = yf.Ticker(yf_sym)

            # ── 尝试 fast_info ────────────────────────────────────────────
            price, prev, volume = 0.0, 0.0, 0
            try:
                fi    = ticker.fast_info
                price = float(getattr(fi, "last_price", None) or 0)
                prev  = float(getattr(fi, "previous_close", None) or 0)
                volume = int(getattr(fi, "regular_market_volume", 0) or 0)
            except Exception:
                pass

            # ── fallback: 用最近两根日线 K 线推算 ──────────────────────────
            if price == 0:
                try:
                    hist = ticker.history(period="5d", interval="1d", auto_adjust=True)
                    if not hist.empty:
                        closes = hist["Close"].dropna().tolist()
                        if closes:
                            price = float(closes[-1])
                            prev  = float(closes[-2]) if len(closes) >= 2 else price
                            volume = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
                except Exception:
                    pass

            if price == 0:
                raise ValueError(f"无法获取 {symbol} 的报价数据，请确认 symbol 格式正确")

            change  = price - prev
            chg_pct = (change / prev * 100) if prev else 0.0

            # ── 尝试获取名称 ──────────────────────────────────────────────
            name = yf_sym
            try:
                info = ticker.info
                name = info.get("longName") or info.get("shortName") or yf_sym
            except Exception:
                pass

            return {
                "symbol":         symbol,
                "name":           name,
                "price":          round(price, 6),
                "change":         round(change, 6),
                "change_percent": round(chg_pct, 4),
                "volume":         volume,
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            }

        result = await asyncio.to_thread(_do)
        _cache.set(cache_key, result)
        return result

    # ── K线 ───────────────────────────────────────────────────────────────
    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        before: Optional[int] = None,  # Unix 时间戳，仅返回此时间之前的数据
    ) -> list[dict]:
        from datetime import timedelta

        yf_sym = _normalize(symbol)
        cache_key = f"yf:ts:{yf_sym}:{interval}:{before or 'latest'}"
        if (cached := _cache.get(cache_key, ttl=60)) is not None:
            return cached[-outputsize:]

        yf_interval = _INTERVAL_MAP.get(interval, "1d")
        yf_period   = _PERIOD_MAP.get(interval, "5y")

        # before 参数：拉取指定时间点之前的历史数据
        _LOOKBACK_DAYS = {
            "15min": 60, "1h": 730, "4h": 730,
            "1day": 365 * 10, "1week": 365 * 20,
        }

        def _do() -> pd.DataFrame:
            ticker = yf.Ticker(yf_sym)
            if before:
                end_dt   = datetime.fromtimestamp(before, tz=timezone.utc)
                days_back = _LOOKBACK_DAYS.get(interval, 365 * 5)
                start_dt = end_dt - timedelta(days=days_back)
                df = ticker.history(
                    start=start_dt, end=end_dt,
                    interval=yf_interval,
                    auto_adjust=True,
                    prepost=False,
                )
            else:
                df = ticker.history(
                    period=yf_period,
                    interval=yf_interval,
                    auto_adjust=True,
                    prepost=False,
                )
            return df

        df = await asyncio.to_thread(_do)

        if df is None or df.empty:
            raise ValueError(f"未找到 {symbol} 的数据，请确认 symbol 格式正确（加密货币用 BTC-USD）")

        candles = _df_to_candles(df)

        if interval == "4h":
            candles = _aggregate_to_4h(candles)

        _cache.set(cache_key, candles)
        return candles[-outputsize:]


    # ── 增量拉取（从指定时间戳之后） ─────────────────────────────────────────
    async def get_time_series_since(
        self,
        symbol: str,
        interval: str,
        since_ts: int,
    ) -> list[dict]:
        """拉取 since_ts 之后的 K 线（用于增量同步）。"""
        from datetime import timedelta

        yf_sym = _normalize(symbol)
        yf_interval = _INTERVAL_MAP.get(interval, "1d")

        def _do() -> pd.DataFrame:
            ticker = yf.Ticker(yf_sym)
            start_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
            # 多拉一天避免边界遗漏
            end_dt = datetime.now(timezone.utc) + timedelta(days=1)
            return ticker.history(
                start=start_dt,
                end=end_dt,
                interval=yf_interval,
                auto_adjust=True,
                prepost=False,
            )

        df = await asyncio.to_thread(_do)
        if df is None or df.empty:
            return []

        candles = _df_to_candles(df)
        if interval == "4h":
            candles = _aggregate_to_4h(candles)
        # 只返回严格晚于 since_ts 的数据，避免重复最后一根
        return [c for c in candles if c["time"] > since_ts]

    # ── 全量拉取（供首次同步使用） ────────────────────────────────────────────
    # 全量拉取使用尽可能长的历史，受 yfinance 各周期最大限制约束
    # 1h/4h 数据 yfinance 只提供最近 2 年，日线/周线可拉 max
    _FULL_PERIOD_MAP: dict[str, str] = {
        "1h":    "2y",
        "4h":    "2y",
        "1day":  "max",
        "1week": "max",
    }

    async def get_full_history(self, symbol: str, interval: str) -> list[dict]:
        """拉取尽可能长的历史数据（不限 outputsize）。"""
        yf_sym = _normalize(symbol)
        yf_interval = _INTERVAL_MAP.get(interval, "1d")
        yf_period   = self._FULL_PERIOD_MAP.get(interval, "max")

        def _do() -> pd.DataFrame:
            ticker = yf.Ticker(yf_sym)
            return ticker.history(
                period=yf_period,
                interval=yf_interval,
                auto_adjust=True,
                prepost=False,
            )

        df = await asyncio.to_thread(_do)
        if df is None or df.empty:
            raise ValueError(f"未找到 {symbol} 的数据，请确认 symbol 格式正确")

        candles = _df_to_candles(df)
        if interval == "4h":
            candles = _aggregate_to_4h(candles)
        return candles

    # ── 批量报价（一次 HTTP 请求获取多个标的，大幅优于逐个 get_quote） ──────────────
    async def get_quotes_batch(self, symbols: list[str]) -> list[Optional[dict]]:
        """用 yf.download 一次性拉取多个标的的最近 5 天日线，速度远快于逐个 get_quote。"""
        if not symbols:
            return []

        results: dict[str, Optional[dict]] = {}
        need_fetch: list[str] = []
        for sym in symbols:
            yf_sym = _normalize(sym)
            cache_key = f"yf:quote:{yf_sym}"
            cached = _cache.get(cache_key, ttl=30)
            if cached is not None:
                results[sym] = cached
            else:
                need_fetch.append(sym)

        if need_fetch:
            yf_syms = [_normalize(s) for s in need_fetch]

            def _do() -> pd.DataFrame:
                return yf.download(
                    tickers=yf_syms,
                    period="5d",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                    group_by="ticker",
                )

            df = await asyncio.to_thread(_do)

            for orig_sym, yf_sym in zip(need_fetch, yf_syms):
                try:
                    if df.columns.nlevels >= 2 and yf_sym in df.columns.get_level_values(0):
                        sub = df[yf_sym]
                    elif df.columns.nlevels < 2:
                        sub = df
                    else:
                        sub = None

                    if sub is None or sub.empty:
                        results[orig_sym] = None
                        continue

                    closes = sub["Close"].dropna()
                    if closes.empty:
                        results[orig_sym] = None
                        continue

                    price = float(closes.iloc[-1])
                    prev = float(closes.iloc[-2]) if len(closes) >= 2 else price
                    vol_series = sub.get("Volume")
                    try:
                        raw_vol = vol_series.iloc[-1] if vol_series is not None and not vol_series.empty else 0
                        volume = int(raw_vol) if raw_vol == raw_vol else 0  # NaN check
                    except (ValueError, TypeError):
                        volume = 0

                    change = price - prev
                    chg_pct = (change / prev * 100) if prev else 0.0

                    name_key = f"yf:name:{yf_sym}"
                    name = _cache.get(name_key, ttl=86400) or orig_sym

                    quote = {
                        "symbol": orig_sym,
                        "name": name,
                        "price": round(price, 6),
                        "change": round(change, 6),
                        "change_percent": round(chg_pct, 4),
                        "volume": volume,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    _cache.set(f"yf:quote:{yf_sym}", quote)
                    results[orig_sym] = quote
                except Exception:
                    results[orig_sym] = None

            missing_names = [
                (orig, _normalize(orig))
                for orig in need_fetch
                if results.get(orig) and _cache.get(f"yf:name:{_normalize(orig)}", ttl=86400) is None
            ]
            if missing_names:
                asyncio.create_task(self._resolve_names(missing_names))

        return [results.get(s) for s in symbols]

    async def _resolve_names(self, pairs: list[tuple[str, str]]) -> None:
        def _do():
            for orig_sym, yf_sym in pairs:
                try:
                    ticker = yf.Ticker(yf_sym)
                    info = ticker.info
                    name = info.get("longName") or info.get("shortName") or orig_sym
                    _cache.set(f"yf:name:{yf_sym}", name)
                    cached_quote = _cache.get(f"yf:quote:{yf_sym}", ttl=30)
                    if cached_quote and cached_quote.get("name") == orig_sym:
                        cached_quote["name"] = name
                        _cache.set(f"yf:quote:{yf_sym}", cached_quote)
                except Exception:
                    pass
        await asyncio.to_thread(_do)

    # ── 财报日期查询 ──────────────────────────────────────────────────────
    _EARNINGS_NO_DATA = "__no_data__"

    async def get_earnings_dates(self, symbols: list[str]) -> dict[str, Optional[str]]:
        def _do() -> dict[str, Optional[str]]:
            result = {}
            for sym in symbols:
                yf_sym = _normalize(sym)
                cache_key = f"yf:earnings:{yf_sym}"
                cached = _cache.get(cache_key, ttl=3600)
                if cached is not None:
                    result[sym] = None if cached == YahooFinanceProvider._EARNINGS_NO_DATA else cached
                    continue
                try:
                    ticker = yf.Ticker(yf_sym)
                    cal = ticker.calendar
                    if cal is not None and isinstance(cal, dict):
                        ed = cal.get("Earnings Date")
                        if ed:
                            date_val = ed[0] if isinstance(ed, list) else ed
                            date_str = str(date_val)[:10]
                            _cache.set(cache_key, date_str)
                            result[sym] = date_str
                            continue
                    _cache.set(cache_key, YahooFinanceProvider._EARNINGS_NO_DATA)
                    result[sym] = None
                except Exception:
                    result[sym] = None
            return result
        return await asyncio.to_thread(_do)

    # ── 基本面快照 ────────────────────────────────────────────────────────
    async def get_financial_snapshot(self, symbol: str) -> dict:
        yf_sym = _normalize(symbol)
        cache_key = f"yf:fundamentals:snapshot:{yf_sym}"
        if (cached := _cache.get(cache_key, ttl=3600)) is not None:
            return cached

        def _do() -> dict:
            ticker = yf.Ticker(yf_sym)
            info: dict[str, Any] = {}
            try:
                info = ticker.info or {}
            except Exception:
                info = {}

            qf = pd.DataFrame()
            try:
                qf = ticker.quarterly_financials
            except Exception:
                qf = pd.DataFrame()

            revenue_values = _financial_row_values(qf, ["Total Revenue", "Operating Revenue"])
            net_income_values = _financial_row_values(qf, ["Net Income", "Net Income Common Stockholders"])
            gross_profit_values = _financial_row_values(qf, ["Gross Profit"])

            revenue = revenue_values[0][1] if revenue_values else None
            net_income = net_income_values[0][1] if net_income_values else None
            quarter = revenue_values[0][0] if revenue_values else None

            snapshot = {
                "symbol": symbol,
                "revenue": revenue,
                "net_income": net_income,
                "revenue_yoy": _growth(revenue_values, 4),
                "revenue_qoq": _growth(revenue_values, 1),
                "net_income_yoy": _growth(net_income_values, 4),
                "net_income_qoq": _growth(net_income_values, 1),
                "gross_margin": _ratio(gross_profit_values[0][1] if gross_profit_values else None, revenue),
                "market_cap": _safe_float(info.get("marketCap")),
                "trailing_pe": _safe_float(info.get("trailingPE")),
                "forward_pe": _safe_float(info.get("forwardPE")),
                "roe": _safe_float(info.get("returnOnEquity")),
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "currency": info.get("financialCurrency") or info.get("currency"),
                "quarter": quarter,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _cache.set(cache_key, snapshot)
            return snapshot

        return await asyncio.to_thread(_do)

    async def get_earnings_history(self, symbol: str, n: int = 4) -> dict:
        yf_sym = _normalize(symbol)
        cache_key = f"yf:fundamentals:earnings:{yf_sym}:{n}"
        if (cached := _cache.get(cache_key, ttl=3600)) is not None:
            return cached

        def _do() -> dict:
            ticker = yf.Ticker(yf_sym)
            next_date = None
            try:
                cal = ticker.calendar
                if cal is not None and isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed:
                        date_val = ed[0] if isinstance(ed, list) else ed
                        next_date = str(date_val)[:10]
            except Exception:
                next_date = None

            history = []
            try:
                earnings = ticker.get_earnings_dates(limit=max(n, 4))
            except Exception:
                earnings = pd.DataFrame()

            if earnings is not None and not earnings.empty:
                for idx, row in earnings.head(n).iterrows():
                    actual = _row_value(row, ["Reported EPS", "EPS Actual"])
                    estimate = _row_value(row, ["EPS Estimate", "Estimated EPS"])
                    history.append({
                        "date": str(idx)[:10],
                        "eps_actual": actual,
                        "eps_estimate": estimate,
                        "surprise_pct": _row_value(row, ["Surprise(%)", "Surprise %"]),
                    })

            result = {"symbol": symbol, "next_earnings_date": next_date, "history": history}
            _cache.set(cache_key, result)
            return result

        return await asyncio.to_thread(_do)


# ─── 单例 ────────────────────────────────────────────────────────────────────
_provider: Optional[YahooFinanceProvider] = None


def get_provider() -> YahooFinanceProvider:
    global _provider
    if _provider is None:
        _provider = YahooFinanceProvider()
    return _provider


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _row_value(row: pd.Series, names: list[str]) -> Optional[float]:
    for name in names:
        if name in row.index:
            return _safe_float(row.get(name))
    return None


def _financial_row_values(df: pd.DataFrame, row_names: list[str]) -> list[tuple[str, float]]:
    if df is None or df.empty:
        return []
    for row_name in row_names:
        if row_name not in df.index:
            continue
        values = []
        series = df.loc[row_name].dropna()
        for col, value in series.items():
            safe = _safe_float(value)
            if safe is None:
                continue
            values.append((str(col)[:10], safe))
        return values
    return []


def _growth(values: list[tuple[str, float]], lag: int) -> Optional[float]:
    if len(values) <= lag:
        return None
    current = values[0][1]
    previous = values[lag][1]
    if previous == 0:
        return None
    return (current - previous) / abs(previous)


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator
