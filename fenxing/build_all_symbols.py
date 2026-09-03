#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量运行缠论引擎 → 为 data/ 下全部品种生成 index.html 可切换的 data_<sym>.js。

品种清单覆盖仓库数据仓库的全部面板:
  crypto : BTC/ETH   (1D+4H+1H 三级别联动)
  stocks : AAPL MSFT NVDA GOOGL META TSLA AMZN BRK_B JPM (1D)
  gold   : XAUUSD    (1D)
  hk     : 0700_HK 9988_HK (1D)
  cn     : 600519_SS (1D)

用法: python3 build_all_symbols.py [SYM ...]   # 不带参数则跑全部
输出: fenxing/data-view/data_<sym小写>.js  (window.MARKET_DATA = {...})
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from chanlun import (  # noqa: E402
    SUB_RELATION, _strip_internal, _sync_merged, analyze, link_levels,
)

REPO = _HERE.parent
DATA = REPO / "data"

# key, 显示名, {级别: parquet 相对 data/ 的路径}
PANELS: list[tuple[str, str, dict[str, str]]] = [
    ("BTC", "BTC/USD", {
        "1D": "crypto/BTC_USD_1day.parquet",
        "4H": "crypto/BTC_USD_4h.parquet",
        "1H": "crypto/BTC_USD_1h.parquet",
    }),
    ("ETH", "ETH/USD", {
        "1D": "crypto/ETH_USD_1day.parquet",
        "4H": "crypto/ETH_USD_4h.parquet",
        "1H": "crypto/ETH_USD_1h.parquet",
    }),
    ("XAUUSD", "XAUUSD 现货黄金", {"1D": "gold/XAUUSD_1day.parquet"}),
    ("XAGUSD", "XAGUSD 现货白银", {"1D": "gold/XAGUSD_1day.parquet"}),
    ("AAPL", "AAPL 苹果", {"1D": "stocks/AAPL_1day.parquet"}),
    ("MSFT", "MSFT 微软", {"1D": "stocks/MSFT_1day.parquet"}),
    ("NVDA", "NVDA 英伟达", {"1D": "stocks/NVDA_1day.parquet"}),
    ("GOOGL", "GOOGL 谷歌", {"1D": "stocks/GOOGL_1day.parquet"}),
    ("META", "META Meta", {"1D": "stocks/META_1day.parquet"}),
    ("TSLA", "TSLA 特斯拉", {"1D": "stocks/TSLA_1day.parquet"}),
    ("AMZN", "AMZN 亚马逊", {"1D": "stocks/AMZN_1day.parquet"}),
    ("BRK_B", "BRK.B 伯克希尔", {"1D": "stocks/BRK_B_1day.parquet"}),
    ("JPM", "JPM 摩根大通", {"1D": "stocks/JPM_1day.parquet"}),
    ("0700_HK", "0700.HK 腾讯", {"1D": "hk_equity/0700_HK_1day.parquet"}),
    ("9988_HK", "9988.HK 阿里巴巴", {"1D": "hk_equity/9988_HK_1day.parquet"}),
    ("600519_SS", "600519.SS 贵州茅台", {"1D": "cn_equity/600519_SS_1day.parquet"}),
    # ---- 2026-08 新增 ----
    ("PLTR", "PLTR Palantir", {"1D": "stocks/PLTR_1day.parquet"}),
    ("COIN", "COIN Coinbase", {"1D": "stocks/COIN_1day.parquet"}),
    ("AVGO", "AVGO 博通", {"1D": "stocks/AVGO_1day.parquet"}),
    ("MU", "MU 美光", {"1D": "stocks/MU_1day.parquet"}),
    ("WMT", "WMT 沃尔玛", {"1D": "stocks/WMT_1day.parquet"}),
    ("AMD", "AMD 超威半导体", {"1D": "stocks/AMD_1day.parquet"}),
    ("NFLX", "NFLX 奈飞", {"1D": "stocks/NFLX_1day.parquet"}),
    ("SOL", "SOL Solana", {
        "1D": "crypto/SOL_USD_1day.parquet",
        "4H": "crypto/SOL_USD_4h.parquet",
        "1H": "crypto/SOL_USD_1h.parquet",
    }),
    ("PEPE", "PEPE Pepe", {
        "1D": "crypto/PEPE_USD_1day.parquet",
        "4H": "crypto/PEPE_USD_4h.parquet",
        "1H": "crypto/PEPE_USD_1h.parquet",
    }),
    ("HYPE", "HYPE Hyperliquid", {   # 注: 1H 受源保留限制, 仅覆盖 2026-01 之后
        "1D": "crypto/HYPE_USD_1day.parquet",
        "4H": "crypto/HYPE_USD_4h.parquet",
        "1H": "crypto/HYPE_USD_1h.parquet",
    }),
    ("002460_SZ", "002460.SZ 赣锋锂业", {"1D": "cn_equity/002460_SZ_1day.parquet"}),
    ("300750_SZ", "300750.SZ 宁德时代", {"1D": "cn_equity/300750_SZ_1day.parquet"}),
    ("600900_SS", "600900.SS 长江电力", {"1D": "cn_equity/600900_SS_1day.parquet"}),
    ("000333_SZ", "000333.SZ 美的集团", {"1D": "cn_equity/000333_SZ_1day.parquet"}),
    ("603259_SS", "603259.SS 药明康德", {"1D": "cn_equity/603259_SS_1day.parquet"}),
    ("300033_SZ", "300033.SZ 同花顺", {"1D": "cn_equity/300033_SZ_1day.parquet"}),
]


def _bars_from_parquet(path: Path):
    df = pd.read_parquet(path).sort_values("time").reset_index(drop=True)
    epochs = [int(x) for x in df["time"].tolist()]
    bars = [
        {
            "idx": idx,
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for idx, row in df.iterrows()
    ]
    return bars, epochs


def build(key: str, name: str, tf_paths: dict[str, str]) -> None:
    t0 = time.time()
    levels_out = {}
    for tf, rel in tf_paths.items():
        path = DATA / rel
        if not path.exists():
            raise FileNotFoundError(f"{key} {tf}: {path} 不存在")
        bars, epochs = _bars_from_parquet(path)
        levels_out[tf] = analyze(bars, epochs, tf)

    multi_tf = len(levels_out) > 1
    if multi_tf:
        # 按 1D→4H→1H 固定顺序输出, 与联动逻辑假设一致
        levels_out = {tf: levels_out[tf] for tf in ("1D", "4H", "1H") if tf in levels_out}
        link_levels(levels_out)
        _sync_merged(levels_out)
    _strip_internal(levels_out)

    payload = {
        "meta": {
            "symbol": name,
            "timeframes": list(levels_out.keys()),
            "subRelation": SUB_RELATION if multi_tf else {},
            "multiTF": multi_tf,
            "source": ", ".join(f"data/{tf_paths[tf]}" for tf in levels_out),
        },
        "levels": levels_out,
    }
    # BTC 直接写主数据文件 data.js(页面默认加载, 与 chanlun.py main 默认一致)
    out_path = _HERE / "data-view" / ("data.js" if key == "BTC" else f"data_{key.lower()}.js")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.MARKET_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
        # 非 BTC 品种没有回测交易层, 显式清空避免残留上一品种的数据
        if key != "BTC":
            f.write("window.TRADES_DATA = null;\n")

    m = levels_out["1D"]["meta"]
    print(f"[{key:<10}] {name:<22} 级别{list(levels_out)} · 1D {m['nBars']}根 "
          f"笔{m['nStrokes']} 段{m['nSegments']} 中枢{m['nPivotsStroke']}/{m['nPivotsSegment']} "
          f"买卖点 笔{m['nBuy1S']+m['nBuy2S']+m['nBuy3S']}+{m['nSell1S']+m['nSell2S']+m['nSell3S']}"
          f"/段{m['nBuy1G']+m['nBuy2G']+m['nBuy3G']}+{m['nSell1G']+m['nSell2G']+m['nSell3G']} "
          f"({time.time()-t0:.1f}s) → {out_path.name}")


def main(argv: list[str]) -> None:
    wanted = {a.upper() for a in argv} or None
    print(f"缠论引擎批量计算 · 品种数 {len(PANELS if not wanted else [p for p in PANELS if p[0] in wanted])}")
    for key, name, paths in PANELS:
        if wanted and key not in wanted:
            continue
        build(key, name, paths)


if __name__ == "__main__":
    main(sys.argv[1:])
