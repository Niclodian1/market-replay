"""三段背离引擎验证: BTC 1D 基准集核对 + 4H 统计 + 前端数据导出。

用法:
    python3 scripts/strategies/run_macd_3seg_validation.py            # 核对 + 报告 + 导出
    python3 scripts/strategies/run_macd_3seg_validation.py --debug    # 未命中时打印三段明细
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from macd_3seg_divergence import ENGINE_VERSION, MACD3SegDivergence, Params  # noqa: E402

# 原文基准集: (日期, 类型, 原文入场价, 原文说明)
GROUND_TRUTH = [
    ("2018-02-03", "buy", 9240, "首次入场，次日(02-04)动能止损"),
    ("2018-02-06", "buy", 7688, "二次入场"),
    ("2018-06-25", "buy", 6246, "入场，次日止损"),
    ("2019-12-18", "buy", 7285, "红柱缩短并翻绿"),
    ("2023-06-06", "buy", 27243, "首次入场（翻绿），次日止损"),
    ("2023-06-15", "buy", 25569, "二次入场"),
    ("2024-05-03", "buy", 62913, "确认买点"),
    ("2025-04-09", "buy", 82594, "确认买点"),
    ("2020-02-10", "sell", 9851, "首次入场，次日硬止损 10199"),
    ("2020-02-13", "sell", 10236, "二次入场（52.8 vs 51.5 微小缩短）"),
    ("2020-06-02", "sell", 9522, "确认卖点"),
    ("2021-04-16", "sell", 61427, "确认卖点"),
    ("2024-01-11", "sell", 46342, "确认卖点"),
    ("2024-10-31", "sell", 70292, "确认卖点，后续止损"),
    ("2025-05-23", "sell", 107329, "确认卖点"),
]
# 原文给出的止损事件（用于核对次日止损逻辑）
GT_STOPS = {"2018-02-03": "2018-02-04", "2018-06-25": "2018-06-26", "2023-06-06": "2023-06-07", "2020-02-10": "2020-02-11"}

WEB_OUT = ROOT / "apps/chanlun-web/data-view/macd3seg_data.js"
REPORT_JSON = ROOT / "data/experiments/macd_3seg/macd_3seg_report.json"

# 与 chanlun-web index.html / build_all_symbols.py 保持完全一致的全品种清单
WEB_PANELS: list[tuple[str, str, str, dict[str, str]]] = [
    # (sym, name, market_group, {tf: parquet_rel_to_data})
    ("BTC", "BTC · 比特币", "加密", {"1D": "crypto/BTC_USD_1day.parquet", "4H": "crypto/BTC_USD_4h.parquet"}),
    ("ETH", "ETH · 以太坊", "加密", {"1D": "crypto/ETH_USD_1day.parquet", "4H": "crypto/ETH_USD_4h.parquet"}),
    ("SOL", "SOL · Solana", "加密", {"1D": "crypto/SOL_USD_1day.parquet", "4H": "crypto/SOL_USD_4h.parquet"}),
    ("PEPE", "PEPE · Pepe", "加密", {"1D": "crypto/PEPE_USD_1day.parquet", "4H": "crypto/PEPE_USD_4h.parquet"}),
    ("HYPE", "HYPE · Hyperliquid", "加密", {"1D": "crypto/HYPE_USD_1day.parquet", "4H": "crypto/HYPE_USD_4h.parquet"}),
    ("XAUUSD", "XAUUSD · 现货黄金", "黄金/贵金属", {"1D": "gold/XAUUSD_1day.parquet"}),
    ("XAGUSD", "XAGUSD · 现货白银", "黄金/贵金属", {"1D": "gold/XAGUSD_1day.parquet"}),
    ("SPX", "SPX · 标普500", "美股", {"1D": "indices/SPX_1day.parquet"}),
    ("NDX", "NDX · 纳斯达克100", "美股", {"1D": "indices/NDX_1day.parquet"}),
    ("AAPL", "AAPL · 苹果", "美股", {"1D": "stocks/AAPL_1day.parquet"}),
    ("MSFT", "MSFT · 微软", "美股", {"1D": "stocks/MSFT_1day.parquet"}),
    ("NVDA", "NVDA · 英伟达", "美股", {"1D": "stocks/NVDA_1day.parquet"}),
    ("GOOGL", "GOOGL · 谷歌", "美股", {"1D": "stocks/GOOGL_1day.parquet"}),
    ("META", "META · Meta", "美股", {"1D": "stocks/META_1day.parquet"}),
    ("TSLA", "TSLA · 特斯拉", "美股", {"1D": "stocks/TSLA_1day.parquet"}),
    ("AMZN", "AMZN · 亚马逊", "美股", {"1D": "stocks/AMZN_1day.parquet"}),
    ("BRK_B", "BRK.B · 伯克希尔", "美股", {"1D": "stocks/BRK_B_1day.parquet"}),
    ("JPM", "JPM · 摩根大通", "美股", {"1D": "stocks/JPM_1day.parquet"}),
    ("PLTR", "PLTR · Palantir", "美股", {"1D": "stocks/PLTR_1day.parquet"}),
    ("COIN", "COIN · Coinbase", "美股", {"1D": "stocks/COIN_1day.parquet"}),
    ("AVGO", "AVGO · 博通", "美股", {"1D": "stocks/AVGO_1day.parquet"}),
    ("MU", "MU · 美光", "美股", {"1D": "stocks/MU_1day.parquet"}),
    ("WMT", "WMT · 沃尔玛", "美股", {"1D": "stocks/WMT_1day.parquet"}),
    ("AMD", "AMD · 超威半导体", "美股", {"1D": "stocks/AMD_1day.parquet"}),
    ("NFLX", "NFLX · 奈飞", "美股", {"1D": "stocks/NFLX_1day.parquet"}),
    ("INTC", "INTC · 英特尔", "美股", {"1D": "stocks/INTC_1day.parquet"}),
    ("SNDK", "SNDK · 闪迪", "美股", {"1D": "stocks/SNDK_1day.parquet"}),
    ("399006_SZ", "399006.SZ · 创业板指", "A股", {"1D": "indices/399006_SZ_1day.parquet"}),
    ("000688_SS", "000688.SS · 科创50", "A股", {"1D": "indices/000688_SS_1day.parquet"}),
    ("600519_SS", "600519.SS · 贵州茅台", "A股", {"1D": "cn_equity/600519_SS_1day.parquet"}),
    ("002460_SZ", "002460.SZ · 赣锋锂业", "A股", {"1D": "cn_equity/002460_SZ_1day.parquet"}),
    ("300750_SZ", "300750.SZ · 宁德时代", "A股", {"1D": "cn_equity/300750_SZ_1day.parquet"}),
    ("600900_SS", "600900.SS · 长江电力", "A股", {"1D": "cn_equity/600900_SS_1day.parquet"}),
    ("000333_SZ", "000333.SZ · 美的集团", "A股", {"1D": "cn_equity/000333_SZ_1day.parquet"}),
    ("603259_SS", "603259.SS · 药明康德", "A股", {"1D": "cn_equity/603259_SS_1day.parquet"}),
    ("300033_SZ", "300033.SZ · 同花顺", "A股", {"1D": "cn_equity/300033_SZ_1day.parquet"}),
    ("0700_HK", "0700.HK · 腾讯", "港股/其他", {"1D": "hk_equity/0700_HK_1day.parquet"}),
    ("9988_HK", "9988.HK · 阿里巴巴", "港股/其他", {"1D": "hk_equity/9988_HK_1day.parquet"}),
    ("N225", "N225 · 日经225", "港股/其他", {"1D": "indices/N225_1day.parquet"}),
]


def load(path: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / path)
    if "open_time" in df.columns:
        df = df.sort_values("open_time").reset_index(drop=True)
    else:
        df = df.sort_values("time").reset_index(drop=True)
    return df


def run(df: pd.DataFrame, params: Params | None = None, meta: dict | None = None) -> tuple[MACD3SegDivergence, dict]:
    eng = MACD3SegDivergence(df, params).run()
    res = eng.result()
    _enrich_metrics(eng, res, meta or {})
    return eng, res


def _enrich_metrics(eng: MACD3SegDivergence, res: dict, meta: dict) -> None:
    import numpy as np
    t_map = {}
    for tr in res["trades"]:
        i = tr["entry_bar"]
        long = tr["side"] == "long"
        entry = tr["entry"]
        stop = tr["stop"]
        init_risk = (entry - stop) / entry if long else (stop - entry) / entry
        next_stopped = tr.get("exit_kind") in ("stop_next", "stop_hard") and tr.get("bars") == 1
        hard_bar = None
        for j in range(i + 1, min(eng.n, i + 51)):
            if (long and eng.l_[j] <= stop) or (not long and eng.h_[j] >= stop):
                hard_bar = j - i
                break
        mfe_pre = {}
        for w in (15, 30, 50):
            if next_stopped:
                mfe_pre[w] = 0.0
                continue
            end_k = min(eng.n - 1, i + w)
            if hard_bar is not None and hard_bar <= w:
                end_k = i + hard_bar - 1
            if end_k <= i:
                mfe_pre[w] = 0.0
            else:
                if long:
                    mfe_pre[w] = max(0.0, float(np.max(eng.h_[i + 1:end_k + 1]) / entry - 1))
                else:
                    mfe_pre[w] = max(0.0, float(1 - np.min(eng.l_[i + 1:end_k + 1]) / entry))
        r_mult50 = mfe_pre[50] / init_risk if init_risk > 1e-6 else 0.0
        true_stopped = next_stopped or (hard_bar is not None and hard_bar <= 50 and mfe_pre[50] < init_risk)
        tr["init_risk"] = round(float(init_risk), 5)
        tr["next_stopped"] = bool(next_stopped)
        tr["true_stopped"] = bool(true_stopped)
        tr["hard_stop_50"] = bool(next_stopped or (hard_bar is not None and hard_bar <= 50))
        tr["mfe15"] = round(float(mfe_pre[15]), 5)
        tr["mfe30"] = round(float(mfe_pre[30]), 5)
        tr["mfe50"] = round(float(mfe_pre[50]), 5)
        tr["r_mult50"] = round(float(r_mult50), 3)
        t_map[tr["id"]] = tr

    for ev in res["events"]:
        if ev["kind"] in ("buy", "sell") and ev["trade_id"] in t_map:
            tr = t_map[ev["trade_id"]]
            ev["init_risk"] = tr["init_risk"]
            ev["mfe15"] = tr["mfe15"]
            ev["mfe30"] = tr["mfe30"]
            ev["mfe50"] = tr["mfe50"]
            ev["r_mult50"] = tr["r_mult50"]
            ev["next_stopped"] = tr["next_stopped"]
            ev["true_stopped"] = tr["true_stopped"]

    def summarize(sub: list[dict]) -> dict:
        n = len(sub)
        if n == 0:
            return {
                "n": 0, "valid_n": 0, "init_risk_mean": 0.0,
                "next_stop_n": 0, "next_stop_rate": 0.0, "next_stop_ret": 0.0,
                "true_stop_n": 0, "true_stop_rate": 0.0, "hard_stop_50_rate": 0.0,
                "mfe15_mean": 0.0, "mfe30_mean": 0.0, "mfe50_mean": 0.0,
                "valid_mfe15_mean": 0.0, "valid_mfe30_mean": 0.0, "valid_mfe50_mean": 0.0, "valid_mfe50_med": 0.0,
                "valid_r_mean": 0.0, "valid_r_med": 0.0,
                "ge1r_rate": 0.0, "ge2r_rate": 0.0, "ge3r_rate": 0.0,
            }
        ns = [x for x in sub if x["next_stopped"]]
        valid = [x for x in sub if not x["next_stopped"]]
        ts_n = sum(1 for x in sub if x["true_stopped"])
        hs50_n = sum(1 for x in sub if x["hard_stop_50"])
        return {
            "n": n,
            "valid_n": len(valid),
            "init_risk_mean": round(float(np.mean([x["init_risk"] for x in sub])), 5),
            "next_stop_n": len(ns),
            "next_stop_rate": round(len(ns) / n, 4),
            "next_stop_ret": round(float(np.mean([x.get("ret", 0.0) for x in ns])) if ns else 0.0, 5),
            "true_stop_n": ts_n,
            "true_stop_rate": round(ts_n / n, 4),
            "hard_stop_50_rate": round(hs50_n / n, 4),
            "mfe15_mean": round(float(np.mean([x["mfe15"] for x in sub])), 5),
            "mfe30_mean": round(float(np.mean([x["mfe30"] for x in sub])), 5),
            "mfe50_mean": round(float(np.mean([x["mfe50"] for x in sub])), 5),
            "valid_mfe15_mean": round(float(np.mean([x["mfe15"] for x in valid])) if valid else 0.0, 5),
            "valid_mfe30_mean": round(float(np.mean([x["mfe30"] for x in valid])) if valid else 0.0, 5),
            "valid_mfe50_mean": round(float(np.mean([x["mfe50"] for x in valid])) if valid else 0.0, 5),
            "valid_mfe50_med": round(float(np.median([x["mfe50"] for x in valid])) if valid else 0.0, 5),
            "valid_r_mean": round(float(np.mean([x["r_mult50"] for x in valid])) if valid else 0.0, 3),
            "valid_r_med": round(float(np.median([x["r_mult50"] for x in valid])) if valid else 0.0, 3),
            "ge1r_rate": round(sum(1 for x in sub if x["r_mult50"] >= 1.0) / n, 4),
            "ge2r_rate": round(sum(1 for x in sub if x["r_mult50"] >= 2.0) / n, 4),
            "ge3r_rate": round(sum(1 for x in sub if x["r_mult50"] >= 3.0) / n, 4),
        }

    trs = res["trades"]
    res["meta"] = meta
    res["summary"] = {
        "all": summarize(trs),
        "long": summarize([x for x in trs if x["side"] == "long"]),
        "short": summarize([x for x in trs if x["side"] == "short"]),
    }


def hit_table(res: dict) -> tuple[list[dict], list[dict]]:
    entries = {(e["date"], e["kind"]): e for e in res["events"] if e["kind"] in ("buy", "sell")}
    stops = {}
    for e in res["events"]:
        if e["kind"] in ("stop_next", "stop_hard"):
            stops.setdefault(e["trade_id"], e)
    rows = []
    for date, kind, gt_px, note in GROUND_TRUTH:
        e = entries.get((date, kind))
        st = stops.get(e["trade_id"]) if e else None
        stop_ok = None
        if date in GT_STOPS:
            stop_ok = bool(st and st["date"] == GT_STOPS[date])
        rows.append({
            "date": date, "kind": kind, "hit": e is not None, "gt_price": gt_px,
            "entry": e["price"] if e else None, "stop": e["stop_price"] if e else None,
            "attempt": e["attempt"] if e else None,
            "next_stop": (f"{st['date']} {st['label']} @{st['price']:.0f}" if st and st["bar"] == e["bar"] + 1 else ""),
            "stop_check": stop_ok, "note": note,
        })
    gt_keys = {(d, k) for d, k, *_ in GROUND_TRUTH}
    extras = [e for (d, k), e in entries.items() if (d, k) not in gt_keys]
    return rows, extras


def debug_miss(eng: MACD3SegDivergence, date: str) -> None:
    t = pd.Timestamp(date, tz="UTC").value // 10**9
    i = int((eng.t == t).nonzero()[0][0])
    print(f"\n--- 未命中诊断 {date} (bar {i}) hist[i-1]={eng.hist[i-1]:.1f} hist[i]={eng.hist[i]:.1f}")
    segs = [s for s in eng.segments if s.end >= i - 150 and s.start <= i]
    for s in segs:
        st = eng.seg_stats(s)
        print(f"  {'G' if s.sign > 0 else 'R'} {_d(eng, s.start)}..{_d(eng, s.end)} noise={s.noise} state={s.state} "
              f"peak={st['peak']:.1f}@{_d(eng, st['peak_bar'])} px={st['px']:.1f}@{_d(eng, st['px_bar'])}")


def _d(eng, i):
    return pd.Timestamp(int(eng.t[i]), unit="s", tz="UTC").strftime("%Y-%m-%d")


def stats(res: dict, start: str | None = None, end: str | None = None) -> dict:
    tr = [t for t in res["trades"] if (start is None or t["entry_date"] >= start) and (end is None or t["entry_date"] <= end)]
    def agg(xs):
        done = [x for x in xs if "ret" in x]
        n = len(xs)
        nstop1 = sum(1 for x in xs if x.get("exit_kind") in ("stop_next", "stop_hard") and x.get("bars") == 1)
        r15 = [x["forward"].get("ret15") for x in xs if x["forward"].get("ret15") is not None]
        mfe = [x["forward"].get("mfe50") for x in xs if x["forward"].get("mfe50") is not None]
        return {
            "n": n, "next_day_stop": nstop1, "next_day_stop_rate": (nstop1 / n) if n else None,
            "win_rate_exit": (sum(1 for x in done if x["ret"] > 0) / len(done)) if done else None,
            "mean_ret_exit": (sum(x["ret"] for x in done) / len(done)) if done else None,
            "mean_ret15": (sum(r15) / len(r15)) if r15 else None,
            "mean_mfe50": (sum(mfe) / len(mfe)) if mfe else None,
        }
    return {"all": agg(tr), "long": agg([t for t in tr if t["side"] == "long"]), "short": agg([t for t in tr if t["side"] == "short"])}


def export_web(results: dict[str, dict]) -> None:
    payload = {"engine": ENGINE_VERSION, "generated": pd.Timestamp.now(tz="UTC").isoformat()[:19], "series": {}}
    for key, res in results.items():
        payload["series"][key] = {
            "meta": res.get("meta", {}),
            "summary": res.get("summary", {}),
            "params": res["params"],
            "events": res["events"],
            "trades": [{k: v for k, v in t.items() if k != "segments"} for t in res["trades"]],
        }
    WEB_OUT.write_text("window.MACD3SEG_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"写出 {WEB_OUT.relative_to(ROOT)} ({WEB_OUT.stat().st_size / 1024:.1f} KB)")


def fmt_pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    p = Params()
    eng1d, res1d = run(load("data/crypto/BTCUSDT_1d.parquet"), p)
    rows, extras = hit_table(res1d)
    print(f"\n== BTCUSDT 1D 基准核对 ({ENGINE_VERSION}) ==")
    print(f"{'日期':<11}{'类型':<6}{'状态':<6}{'入场价':>10}{'原文价':>9}{'硬止损':>10}{'次':>3}  次日处理")
    for r in rows:
        print(f"{r['date']:<11}{'买' if r['kind'] == 'buy' else '卖':<6}{'HIT' if r['hit'] else 'MISS':<6}"
              f"{(r['entry'] or 0):>10.0f}{r['gt_price']:>9}{(r['stop'] or 0):>10.0f}{(r['attempt'] or 0):>3}  "
              f"{r['next_stop']}{'' if r['stop_check'] in (None, True) else '  ✗止损日期不符'}")
    hits = sum(r["hit"] for r in rows)
    print(f"召回: {hits}/{len(rows)}")
    in_range = [e for e in extras if "2018-01-01" <= e["date"] <= "2025-11-09"]
    print(f"原文区间(2018-01 ~ 2025-11-09)额外信号: {len(in_range)} 个；全样本额外: {len(extras)} 个")
    for e in sorted(extras, key=lambda x: x["bar"]):
        print(f"   + {e['date']} {e['label']} @{e['price']:.0f} 止损 {e['stop_price']:.0f} ({e['note']})")
    if args.debug:
        for r in rows:
            if not r["hit"]:
                debug_miss(eng1d, r["date"])

    # 前端数据：覆盖 chanlun-web 全部品种（加密、黄金/白银、美股、A股、港股、指数），保证时间戳逐根对齐
    results = {}
    for sym, name, market, tf_map in WEB_PANELS:
        for tf, rel_path in tf_map.items():
            path = ROOT / "data" / rel_path
            if path.exists():
                df_sym = load(str(path.relative_to(ROOT)))
                _, results[f"{sym}|{tf}"] = run(
                    df_sym, p,
                    meta={"sym": sym, "name": name, "market": market, "tf": tf, "bars": len(df_sym)},
                )
    _, res4h = run(load("data/crypto/BTCUSDT_4h.parquet"), p)
    results_report = {"BTC|1D": res1d, "BTC|4H": res4h}

    print("\n== 统计报告 (BTCUSDT) ==")
    report = {"engine": ENGINE_VERSION, "params": res1d["params"], "ground_truth": rows,
              "extras_1d": [{k: e[k] for k in ("date", "label", "price", "stop_price", "note")} for e in extras], "stats": {}}
    for key in ("BTC|1D", "BTC|4H"):
        st = stats(results_report[key])
        report["stats"][key] = st
        a, l, s = st["all"], st["long"], st["short"]
        print(f"{key}: 信号 {a['n']} (买 {l['n']} / 卖 {s['n']}) | 次日即止损 {fmt_pct(a['next_day_stop_rate'])} | "
              f"离场胜率 {fmt_pct(a['win_rate_exit'])} | 均收益 {fmt_pct(a['mean_ret_exit'])} | "
              f"15根均收益 {fmt_pct(a['mean_ret15'])} | 50根均MFE {fmt_pct(a['mean_mfe50'])}")
    yrs = {}
    for t in res4h["trades"]:
        y = t["entry_date"][:4]
        yrs.setdefault(y, [0, 0])
        yrs[y][0 if t["side"] == "long" else 1] += 1
    print("BTC 4H 年度分布(买/卖): " + "  ".join(f"{y}:{a}/{b}" for y, (a, b) in sorted(yrs.items())))
    report["stats"]["BTC|4H"]["by_year"] = yrs
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not args.no_export:
        export_web(results)
    return 0 if hits == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
