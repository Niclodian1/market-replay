#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量更新 BTC/ETH 的 1D/4H/1H parquet(数据源: Binance 现货公开K线, 无需密钥)。

用法: python3 update_data.py
逻辑: 读现有 parquet 的最后 openTime → 只拉取其后的K线(分页×1000)
      → 合并去重(keep=last, 自动覆盖上一轮的未收盘临时bar) → 写回原文件。
列结构保持: time(epoch秒) / open / high / low / close / volume。
"""
import json
import subprocess
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "crypto"
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
INTERVALS = {"1day": "1d", "4h": "4h", "1h": "1h"}
PAGE = 1000


def fetch_klines(symbol: str, interval: str, start_ms: int):
    """curl 拉取 Binance 公开K线(环境 urllib 证书链不可用, curl 走系统信任库)。"""
    out = []
    url0 = start_ms
    while True:
        url = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=%s"
               "&startTime=%d&limit=%d" % (symbol, interval, url0, PAGE))
        r = subprocess.run(["curl", "-s", "--max-time", "30", url],
                           capture_output=True, text=True, check=True)
        batch = json.loads(r.stdout)
        if isinstance(batch, dict) or not batch:
            break                      # 错误对象或空数组
        out.extend(batch)
        if len(batch) < PAGE:
            break
        url0 = batch[-1][0] + 1        # 下一页从最后一根的下一毫秒开始
    return out


def update(sym_key: str, sym_api: str, tf_file: str, tf_api: str):
    path = DATA_DIR / f"{sym_key}_USD_{tf_file}.parquet"
    df = pd.read_parquet(path).sort_values("time").reset_index(drop=True)
    last_ms = int(df["time"].iloc[-1]) * 1000
    kl = fetch_klines(sym_api, tf_api, last_ms + 1)
    if not kl:
        print(f"  {path.name}: 已是最新 (最后 {df['time'].iloc[-1]})")
        return
    add = pd.DataFrame({
        "time":   [int(k[0] // 1000) for k in kl],   # openTime ms → s
        "open":   [float(k[1]) for k in kl],
        "high":   [float(k[2]) for k in kl],
        "low":    [float(k[3]) for k in kl],
        "close":  [float(k[4]) for k in kl],
        "volume": [float(k[5]) for k in kl],
    })
    merged = (pd.concat([df, add])
                .drop_duplicates(subset="time", keep="last")
                .sort_values("time")
                .reset_index(drop=True))
    merged.to_parquet(path, index=False)
    print(f"  {path.name}: {len(df)} → {len(merged)} 根 (+{len(merged) - len(df)}) · "
          f"最新开仓 {merged['time'].iloc[-1]}")


def main():
    for sym_key, sym_api in SYMBOLS.items():
        print(f"[{sym_key}]")
        for tf_file, tf_api in INTERVALS.items():
            try:
                update(sym_key, sym_api, tf_file, tf_api)
            except Exception as ex:                     # noqa: BLE001 — 单档失败不阻断其余
                print(f"  {sym_key}_{tf_file}: 失败 — {ex}")


if __name__ == "__main__":
    main()
