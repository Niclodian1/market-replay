#!/usr/bin/env python3
"""导出缠论 golden JSON：Python 引擎跑一遍，TS 侧测试比对同一口径。

    python3 scripts/export_chanlun_golden.py --bars 400

产物：apps/platform-web/tests/fixtures/chanlun_golden.json（入库，防止口径漂移）
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

from app.core import chanlun_service  # noqa: E402

DEFAULT_PARQUET = REPO_ROOT / "data" / "crypto" / "BTC_USD_1day.parquet"
OUT_PATH = REPO_ROOT / "apps" / "platform-web" / "tests" / "fixtures" / "chanlun_golden.json"


def build_payload(bars: int) -> dict:
    frame = pd.read_parquet(DEFAULT_PARQUET).tail(bars)
    candles = [
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
    levels = chanlun_service.analyze_symbol({"1day": candles})
    level = levels["1D"]
    return {
        "symbol": "BTC-USD",
        "timeframe": "1day",
        "source": str(DEFAULT_PARQUET.relative_to(REPO_ROOT)),
        "bars": bars,
        "candles": candles,
        "closes": [item["close"] for item in candles],
        "macd_hist": level.get("macdHist", []),
        "strokes": level.get("strokesRaw", []),
        "segments": level.get("segmentsRaw", []),
        "pivots_stroke": level.get("pivotsStrokeRaw", []),
        "pivots_segment": level.get("pivotsSegmentRaw", []),
        "buy_sell_points": chanlun_service._chart_merge_points(  # noqa: SLF001
            level, chanlun_service._TIMEFRAME_SECONDS["1day"]
        ),
        "beichi_points": chanlun_service._chart_beichi_points(level),  # noqa: SLF001
        "top_fractals": level.get("topMarkers", []),
        "bot_fractals": level.get("bottomMarkers", []),
        "meta": level.get("meta", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=400)
    args = parser.parse_args()
    if not DEFAULT_PARQUET.exists():
        raise SystemExit(f"缺少行情数据：{DEFAULT_PARQUET}")
    payload = build_payload(args.bars)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"已写出 {OUT_PATH.relative_to(REPO_ROOT)}：{payload['bars']} 根K线 · "
        f"笔 {len(payload['strokes'])} · 线段 {len(payload['segments'])} · "
        f"中枢 {len(payload['pivots_stroke'])} · 买卖点 {len(payload['buy_sell_points'])} · "
        f"背驰 {len(payload['beichi_points'])}"
    )


if __name__ == "__main__":
    main()
