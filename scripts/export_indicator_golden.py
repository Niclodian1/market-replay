#!/usr/bin/env python3
"""导出统一指标层 golden 快照：12 个指标在 BTC 1D 尾段的事件全量。

    python3 scripts/export_indicator_golden.py --bars 600

产物：apps/platform-api/tests/fixtures/indicator_events_golden.json（入库）
守护：uid / confirmed_at / direction / params_hash 一旦变化，测试与 CI 立刻发现。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "chanlun-core"))

import pandas as pd  # noqa: E402

from app.core.indicator_provider import get_registry  # noqa: E402

PARQUET_1D = REPO_ROOT / "data" / "crypto" / "BTC_USD_1day.parquet"
OUT_PATH = REPO_ROOT / "apps" / "platform-api" / "tests" / "fixtures" / "indicator_events_golden.json"


def load_candles(bars: int) -> list[dict]:
    frame = pd.read_parquet(PARQUET_1D).sort_values("time").tail(bars)
    return [
        {
            "time": int(row.time),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in frame.itertuples()
    ]


def build_payload(bars: int) -> dict:
    candles = load_candles(bars)
    registry = get_registry()
    events = registry.run_many(registry.names, candles, "BTC-USD", "1day", {})
    return {
        "symbol": "BTC-USD",
        "timeframe": "1day",
        "bars": len(candles),
        "first_time": candles[0]["time"],
        "last_time": candles[-1]["time"],
        "indicator_ids": sorted(registry.names),
        "events": [event.to_dict() for event in events],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=600)
    args = parser.parse_args()
    if not PARQUET_1D.exists():
        raise SystemExit(f"缺少行情数据：{PARQUET_1D}")
    payload = build_payload(args.bars)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(
        f"已写出 {OUT_PATH.relative_to(REPO_ROOT)}：{payload['bars']} 根K线 · "
        f"{len(payload['indicator_ids'])} 个指标 · {len(payload['events'])} 个事件"
    )


if __name__ == "__main__":
    main()
