#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trades_detail.json → trades_data.js (供 index.html 回测交易图层加载)

每笔交易预解析出场时间为 epoch 秒(exit_ts), 前端可直接定位K线;
入场时间 entry_t 本身即 epoch 秒(与 data.js candles 的 time 同口径)。

用法: python3 build_trades_data.py  (backtest.py 重跑后需再次执行)
"""

import json
from pathlib import Path
from datetime import datetime, timezone

SRC = str(Path(__file__).resolve().parent / "trades_detail.json")
DST = str(Path(__file__).resolve().parents[1] / "data-view" / "trades_data.js")


def main():
    with open(SRC, encoding="utf-8") as f:
        trades = json.load(f)
    for tr in trades:
        dt = datetime.strptime(tr["exit_iso"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        tr["exit_ts"] = int(dt.timestamp())
    with open(DST, "w", encoding="utf-8") as f:
        f.write("// 自动生成: python3 build_trades_data.py — 请勿手改\n")
        f.write(f"// 源: {SRC} · {len(trades)} 笔回测交易\n")
        f.write("window.TRADES_DATA=")
        json.dump(trades, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"[ok] {DST} · {len(trades)} trades")


if __name__ == "__main__":
    main()
