#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论三类买卖点多周期回测 — 只做多

规则(用户指定):
  入场: 买点(一/二/三买)在 confirmedT 当根收盘价买入(因果点, 无未来函数);
        同一根K线多个信号只取第一个(按 一→二→三 排序); 已持仓则忽略新买点。
  止损: 买点的最低价(pt.p)。盘中触发即出: 开盘已破则以开盘价成交, 否则以止损价成交;
        开仓当根不检查(入场发生在收盘)。
  出场: 卖点(一/二/三卖)在 confirmedT 当根收盘价卖出; 或止损; 或数据末端强制平仓。
        盘中止损优先于收盘卖点。

A/B 对照(理论修正开关, 通过 chanlun._collect_causal_points 透传):
  baseline      : 现行系统口径
  fixA-enter    : 一买/一卖的 A 段改为「进入最后中枢的同向段」(原文口径)
  fixB-pullback : 中枢延伸改为「离开[ZD,ZG]后回拉才延伸」(原文口径)
  fixAB         : 两者同时启用

输出: 控制台对比表 + trades_detail.json(供深入分析)
"""

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

import chanlun

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "crypto"
DATA_FILES = {
    "1D": _DATA_DIR / "BTC_USD_1day.parquet",
    "4H": _DATA_DIR / "BTC_USD_4h.parquet",
    "1H": _DATA_DIR / "BTC_USD_1h.parquet",
}
# 口径已定稿(2026-08): A段=进入段, 中枢延伸=α。原 baseline/fixB/fixAB
# 实验组已随 chanlun 侧参数移除, 只保留现行口径。
CONFIGS = [("enter", dict())]


# --------------------------------------------------------------------------- #
# 回测引擎                                                                      #
# --------------------------------------------------------------------------- #
def run_backtest(bars, epochs, buys, sells, tf, mode):
    buy_ev, sell_ev = {}, {}
    for p in buys:
        buy_ev.setdefault(p["confirmedT"], []).append(p)
    for p in sells:
        sell_ev.setdefault(p["confirmedT"], []).append(p)

    pos, trades = None, []
    for t, bar in enumerate(bars):
        te = epochs[t]
        # 1) 盘中止损(先于收盘卖点)
        if pos is not None and bar["low"] <= pos["stop"]:
            exit_px = bar["open"] if bar["open"] <= pos["stop"] else pos["stop"]
            trades.append(_close(pos, t, exit_px, "止损", tf, mode))
            pos = None
        # 2) 收盘卖点平仓
        if pos is not None and te in sell_ev:
            trades.append(_close(pos, t, bar["close"], sell_ev[te][0]["k"], tf, mode))
            pos = None
        # 3) 收盘买点开仓
        if pos is None and te in buy_ev:
            b = buy_ev[te][0]
            stop, entry = float(b["p"]), bar["close"]
            if stop < entry:  # 防御: 止损必须低于入场价
                pos = {
                    "type": b["k"], "entry_t": te, "entry_i": t, "entry": entry,
                    "stop": stop, "stop_dist": (entry - stop) / entry * 100,
                }
    if pos is not None:
        trades.append(_close(pos, len(bars) - 1, bars[-1]["close"], "期末", tf, mode))
    return trades


def _close(pos, t, px, reason, tf, mode):
    return {
        "tf": tf, "mode": mode, "type": pos["type"],
        "entry_t": pos["entry_t"], "exit_t": t,
        "entry": round(pos["entry"], 2), "exit": round(px, 2),
        "ret": round((px / pos["entry"] - 1) * 100, 3),
        "hold_bars": t - pos["entry_i"],
        "reason": reason, "stop_dist": round(pos["stop_dist"], 2),
    }


# --------------------------------------------------------------------------- #
# 统计                                                                          #
# --------------------------------------------------------------------------- #
def stats(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r / 100
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    by_type = {}
    for ty in ("一买", "二买", "三买"):
        sub = [t for t in trades if t["type"] == ty]
        if sub:
            sr = [t["ret"] for t in sub]
            by_type[ty] = {
                "n": len(sub),
                "win%": round(100 * sum(1 for r in sr if r > 0) / len(sr), 1),
                "avg": round(sum(sr) / len(sr), 2),
                "sum": round(sum(sr), 1),
            }
    sl = [t for t in trades if t["reason"] == "止损"]
    return {
        "n": n,
        "win%": round(100 * len(wins) / n, 1),
        "avg": round(sum(rets) / n, 2),
        "med": round(sorted(rets)[n // 2], 2),
        "total%": round((eq - 1) * 100, 1),
        "pf": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
        "mdd%": round(mdd * 100, 1),
        "hold": round(sum(t["hold_bars"] for t in trades) / n, 1),
        "stop_n": len(sl),
        "by_type": by_type,
    }


# --------------------------------------------------------------------------- #
# 主流程                                                                        #
# --------------------------------------------------------------------------- #
def load_bars(tf):
    df = pd.read_parquet(DATA_FILES[tf]).sort_values("time").reset_index(drop=True)
    epochs = [int(x) for x in df["time"].tolist()]
    bars = [
        {"idx": i, "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
        for i, r in df.iterrows()
    ]
    return bars, epochs


def run_one(args):
    """子进程任务: 加载单个周期数据, 跑单配置的因果信号生成 + 回测。"""
    tf, cfg_name, kw = args
    bars, epochs = load_bars(tf)
    buy_s, sell_s, buy_g, sell_g, *_ = chanlun._collect_causal_points(bars, epochs, tf, **kw)
    out = []
    for mode, buys, sells in (("stroke", buy_s, sell_s), ("segment", buy_g, sell_g)):
        trades = run_backtest(bars, epochs, buys, sells, tf, mode)
        for tr in trades:
            tr["cfg"] = cfg_name
            tr["exit_iso"] = _iso(epochs, tr.pop("exit_t"))
        out.append((f"{tf}|{mode}|{cfg_name}", trades))
    return out


def main():
    jobs = [(tf, name, kw) for tf in ("1D", "4H", "1H") for name, kw in CONFIGS]
    all_trades, report = [], {}
    with ProcessPoolExecutor(max_workers=8) as ex:
        for out in ex.map(run_one, jobs):
            for key, trades in out:
                tf, mode, cfg = key.split("|")
                all_trades.extend(trades)
                st = stats(trades)
                st["bh%"] = round(
                    (load_bars(tf)[0][-1]["close"] / load_bars(tf)[0][0]["close"] - 1) * 100, 1)
                report[key] = st
                print(f"[done] {key}  n={st.get('n',0)} win={st.get('win%','-')}% "
                      f"total={st.get('total%','-')}%", flush=True)

    with open("trades_detail.json", "w", encoding="utf-8") as f:
        json.dump(all_trades, f, ensure_ascii=False)
    with open("backtest_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # ---- 汇总表 ----
    print("\n" + "=" * 108)
    print(f"{'级别':5s} {'口径':8s} {'配置':14s} {'笔数':>4s} {'胜率%':>6s} {'均笔%':>7s} "
          f"{'复利%':>9s} {'PF':>5s} {'回撤%':>6s} {'止损':>4s} {'持有时长':>8s}  分类型(n/胜%/均%)")
    print("-" * 108)
    for key, st in report.items():
        tf, mode, cfg = key.split("|")
        bt = "  ".join(
            f"{ty}:{d['n']}/{d['win%']}/{d['avg']:+}" for ty, d in st.get("by_type", {}).items())
        print(f"{tf:5s} {mode:8s} {cfg:14s} {st.get('n',0):4d} {st.get('win%','-'):>6} "
              f"{st.get('avg','-'):>7} {st.get('total%','-'):>9} {st.get('pf','-'):>5} "
              f"{st.get('mdd%','-'):>6} {st.get('stop_n','-'):>4} {st.get('hold','-'):>8}  {bt}")
    print("=" * 108)
    print("Buy&Hold: " + "  ".join(
        "%s %.0f%%" % (tf, (load_bars(tf)[0][-1]["close"] / load_bars(tf)[0][0]["close"] - 1) * 100)
        for tf in ("1D", "4H", "1H")))


def _iso(epochs, t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epochs[t], timezone.utc).strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
