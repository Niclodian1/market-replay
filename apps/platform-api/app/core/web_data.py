"""数据出库：把「看盘/练习」需要的数据预生成成静态 JSON，前端可直接吃 CDN/静态目录。

产物结构（默认 ``frontend/public/data``）::

    manifest.json
    BTC-USD/4h.json     # candles + series + events（含结果）

这样看盘页不再需要每次请求时现算缠论；静态文件缺失时前端自动回退 API。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.indicator_core.series import compute_series, sanitize_values

DEFAULT_SERIES = [
    {"id": "ma", "params": {"periods": [20, 60]}},
    {"id": "macd", "params": {}},
]
DEFAULT_EVENTS = ["chanlun.buy3", "chanlun.sell3", "ma.breakout", "macd.divergence"]


def build_bundle(
    symbol: str,
    timeframe: str,
    *,
    limit: int = 1200,
    series: Sequence[dict[str, Any]] | None = None,
    indicators: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    bars = list(read_local_bars(symbol, timeframe))
    if not bars:
        return None
    bars = bars[-limit:]
    computed = compute_series(bars, list(series or DEFAULT_SERIES))
    registry = get_registry()
    events = registry.run_many(
        list(indicators or DEFAULT_EVENTS), bars, symbol, timeframe, {}
    )
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candles": bars,
        "series": [{**item.to_dict(), "values": sanitize_values(item.values)} for item in computed],
        "events": [event.to_dict() for event in events],
    }


def build_manifest(
    bundles: Iterable[tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    for symbol, timeframe, payload in bundles:
        entry = items.setdefault(
            symbol.upper(),
            {"symbol": symbol.upper(), "timeframes": {}},
        )
        entry["timeframes"][timeframe] = {
            "path": f"{symbol.upper()}/{timeframe}.json",
            "bars": len(payload["candles"]),
            "events": len(payload["events"]),
            "generated_at": payload["generated_at"],
        }
    return {
        "schema": "web-data/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": [items[key] for key in sorted(items)],
    }


def write_bundles(
    out_dir: Path,
    pairs: Sequence[tuple[str, str]],
    *,
    limit: int = 1200,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    built: list[tuple[str, str, dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    for symbol, timeframe in pairs:
        payload = build_bundle(symbol, timeframe, limit=limit)
        if payload is None:
            skipped.append({"symbol": symbol, "timeframe": timeframe, "reason": "no local bars"})
            continue
        target = out_dir / symbol.upper() / f"{timeframe}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        built.append((symbol, timeframe, payload))
    manifest = build_manifest(built)
    manifest["skipped"] = skipped
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
