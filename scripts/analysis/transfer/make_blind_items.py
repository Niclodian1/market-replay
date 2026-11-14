#!/usr/bin/env python3
"""盲测出题（严格口径）：100% 题目满足「当根是阳线，且当根收盘上穿 MA20（前一根收盘 ≤ MA20，当根收盘 > MA20）」。

输出：
  apps/chanlun-web/blind/items.json & items.js
  reports/blind/key.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
from run_e4 import find_file  # noqa: E402

ROOT = HERE.parents[2]
SINCE = pd.Timestamp("2000-01-01").timestamp()
SHOW = 250
FUTURE = 130
GAP = 20
QUOTA = {
    "加密": (["crypto/BTC_USD", "crypto/ETH_USD", "crypto/SOL_USD"], 90),
    "美股个股": ([f"stocks/{s}" for s in ("AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "INTC", "JPM", "META", "MSFT",
                                       "MU", "NFLX", "NVDA", "TSLA", "WMT", "BRK_B")], 75),
    "贵金属": (["gold/XAUUSD", "gold/XAGUSD"], 45),
    "指数": (["indices/SPX", "indices/NDX", "indices/N225", "indices/399006_SZ", "indices/000688_SS"], 45),
    "A股港股": ([f"cn_equity/{s}" for s in ("000333_SZ", "002460_SZ", "300033_SZ", "600519_SS", "600900_SS")]
              + ["hk_equity/0700_HK", "hk_equity/9988_HK"], 45),
}
NO_VOL = {"SPX", "N225", "XAUUSD", "XAGUSD"}


def btc_seen_days() -> set[int]:
    days = set()
    for f in ("annotations-BTC-1D-manual.json", "annotations-BTC-1D.json"):
        for it in json.loads((ROOT / f).read_text())["items"]:
            ts = it.get("ts") or it.get("features", {}).get("time")
            if ts:
                d = int(ts) // 86400
                days.update(range(d - 15, d + 16))
    return days


def prep(df: pd.DataFrame) -> dict:
    c, o, h, l = (df[x].to_numpy(float) for x in ("close", "open", "high", "low"))
    v = df["volume"].to_numpy(float)
    M = dict(t=df.time.to_numpy(np.int64), o=o, h=h, l=l, c=c, v=v, n=len(c),
             ma20=engine.sma(c, 20), ma60=engine.sma(c, 60), ma120=engine.sma(c, 120), atr=engine.atr14(h, l, c))
    dif = engine.ema(c, 12) - engine.ema(c, 26)
    dea = engine.ema(dif, 9)
    M.update(dif=dif, dea=dea, hist=dif - dea)
    return M


def pivots(x: np.ndarray, i: int, lb: int, win: int, high: bool) -> list[int]:
    out = []
    for j in range(max(lb, i - win), i - lb):
        seg = x[j - lb:j + lb + 1]
        if (x[j] == seg.max()) if high else (x[j] == seg.min()):
            out.append(j)
    return out


def is_exact_bull_cross(M: dict, i: int) -> bool:
    """硬约束：当根必须是阳线，且当根收盘上穿 MA20（前一根收盘 <= MA20[i-1]，当根收盘 > MA20[i]）。"""
    c, o, ma = M["c"], M["o"], M["ma20"]
    if ma[i] != ma[i] or ma[i - 1] != ma[i - 1]:
        return False
    return bool(c[i] > o[i] and c[i - 1] <= ma[i - 1] and c[i] > ma[i])


def candidates(M: dict) -> dict[str, list[int]]:
    """所有候选点都先满足 is_exact_bull_cross，再按背景形态打标。"""
    c, h, l, dif = M["c"], M["h"], M["l"], M["dif"]
    lo_i, hi_i = SHOW + 1, M["n"] - FUTURE
    out = {"ma20_reclaim": [], "macd_div": [], "trendline": []}
    for i in range(lo_i, hi_i):
        if not is_exact_bull_cross(M, i):
            continue
        # 检查是否同时伴随 MACD 底背离后上穿 MA20
        lows = pivots(l, i + 1, 3, 60, high=False)
        is_div = len(lows) >= 2 and lows[-1] - lows[-2] >= 8 and l[lows[-1]] < l[lows[-2]] and dif[lows[-1]] > dif[lows[-2]]
        # 检查是否同时伴随下降趋势线突破
        ph = pivots(h, i, 3, 90, high=True)
        is_tl = False
        if len(ph) >= 2 and ph[-1] - ph[-2] >= 12 and h[ph[-1]] < h[ph[-2]]:
            a, b = ph[-2], ph[-1]
            sl = (h[b] - h[a]) / (b - a)
            line = h[b] + sl * (i - b)
            is_tl = bool(c[i] > line)
        if is_div:
            out["macd_div"].append(i)
        elif is_tl:
            out["trendline"].append(i)
        else:
            out["ma20_reclaim"].append(i)
    return out


def feats(M: dict, i: int) -> dict:
    c, o, h, l, atr = M["c"], M["o"], M["h"], M["l"], M["atr"]
    rng = h[i] - l[i]
    hi250, lo250 = h[i - 250:i + 1].max(), l[i - 250:i + 1].min()
    return dict(body_pct=round((c[i] - o[i]) / o[i] * 100, 3),
                body_ratio=round((c[i] - o[i]) / rng, 3) if rng > 0 else 0.0,
                range_ratio=round(rng / max(1e-12, np.mean(h[i - 10:i] - l[i - 10:i])), 3),
                pos250=round((c[i] - lo250) / max(1e-12, hi250 - lo250), 3),
                d_atr=round((c[i] - M["ma20"][i]) / atr[i], 3) if atr[i] > 0 else None,
                atr_pct=round(atr[i] / c[i] * 100, 3))


def stratified(pool: list[tuple], k: int, rng: np.random.Generator) -> list[tuple]:
    if len(pool) <= k:
        return list(pool)
    br = np.array([p[3]["body_ratio"] for p in pool])
    ps = np.array([p[3]["pos250"] for p in pool])
    qb, qp = np.quantile(br, [1 / 3, 2 / 3]), np.quantile(ps, [1 / 3, 2 / 3])
    cell = np.digitize(br, qb) * 3 + np.digitize(ps, qp)
    cells = {c: list(rng.permutation(np.flatnonzero(cell == c))) for c in range(9)}
    picked = []
    while len(picked) < k and any(cells.values()):
        for c in range(9):
            if cells[c] and len(picked) < k:
                picked.append(pool[cells[c].pop()])
    return picked


def payload(M: dict, i: int, show: int, has_vol: bool) -> dict:
    s = slice(i - show + 1, i + 1)
    base = M["c"][i]
    r = lambda a, d=5: [None if x != x else round(float(x), d) for x in a]  # noqa: E731
    p = {k: r(M[k][s] / base) for k in ("o", "h", "l", "c", "ma20", "ma60", "ma120")}
    sc = M["atr"][i] if M["atr"][i] > 0 else base * 0.02
    p.update({k: r(M[k][s] / sc, 4) for k in ("dif", "dea", "hist")})
    if has_vol:
        vv = M["v"][s]
        p["v"] = r(vv / max(1e-12, np.nanmean(vv)), 3)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261105)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    seen = btc_seen_days()
    data, pools = {}, {}
    for grp, (rels, _) in QUOTA.items():
        pools[grp] = []
        for rel in rels:
            f = find_file(rel)
            if f is None:
                continue
            df = pd.read_parquet(f).sort_values("time")
            df = df[df.time >= SINCE].reset_index(drop=True)
            sym = Path(rel).name
            M = prep(df)
            data[sym] = M
            cands = candidates(M)
            for typ, idx in cands.items():
                for i in idx:
                    if sym == "BTC_USD" and int(M["t"][i]) // 86400 in seen:
                        continue
                    pools[grp].append((sym, i, typ, feats(M, i)))
        print(grp, "候选总数(100%当根阳线上穿MA20):", len(pools[grp]))

    used: dict[str, list[int]] = {}

    def free(sym, i):
        return all(abs(i - j) >= GAP for j in used.get(sym, []))

    formal = []
    for grp, (_, q) in QUOTA.items():
        pool = pools[grp]
        # 优先拿一部分带背离/趋势线的上穿点，其余由普通上穿补足到配额 q
        div_tl = [p for p in pool if p[2] in ("macd_div", "trendline")]
        reclaim = [p for p in pool if p[2] == "ma20_reclaim"]
        ordered = stratified(div_tl, int(q * 0.35), rng) + stratified(reclaim, q * 2, rng)
        got = []
        for p in ordered:
            if len(got) >= q:
                break
            if free(p[0], p[1]):
                got.append(p); used.setdefault(p[0], []).append(p[1])
        formal += [(grp,) + p for p in got]

    # 检查题（Trap）：也必须是当根阳线上穿 MA20，但属于极端明显的差形态（长上影插针 / 止损极宽 >3ATR）
    traps, warm = [], []
    rest = [(g,) + p for g in pools for p in pools[g]]
    rng.shuffle(rest)
    for g, s, i, typ, f in rest:
        if not free(s, i):
            continue
        M = data[s]
        bad = f["body_ratio"] < 0.15 or (M["c"][i] - M["l"][i]) / M["atr"][i] > 3.0
        if bad and len(traps) < 20:
            traps.append((g, s, i, "trap", f)); used.setdefault(s, []).append(i)
        if len(traps) >= 20:
            break

    for g, s, i, typ, f in rest:
        if free(s, i):
            warm.append((g, s, i, typ, f)); used.setdefault(s, []).append(i)
        if len(warm) >= 20:
            break

    items, key = [], {}
    pool_all = formal + traps
    order = list(rng.permutation(len(pool_all)))
    n_sess = 4
    per = int(np.ceil(len(pool_all) / n_sess))
    sess_of = {j: pos // per + 1 for pos, j in enumerate(order)}
    holdout = set(rng.choice(len(formal), size=int(round(len(formal) * 0.3)), replace=False).tolist())

    def add(item_id, sess, src, show, repeat_of=None, trap=False, warmup=False, hold=False):
        g, s, i, typ, f = src
        M = data[s]
        assert is_exact_bull_cross(M, i), f"{item_id} violated exact bull cross!"
        has_vol = s not in NO_VOL and np.nanmean(M["v"][i - show + 1:i + 1] > 0) > 0.95
        items.append(dict(id=item_id, session=sess, warmup=warmup, n=show, bars=payload(M, i, show, has_vol)))
        key[item_id] = dict(group=g, symbol=s, date=str(pd.to_datetime(int(M["t"][i]), unit="s").date()),
                            barIndex=int(i), candidateType=typ, isRepeatOf=repeat_of, isTrap=trap,
                            isHoldout=hold, isWarmup=warmup, session=sess, features=f)

    for k, src in enumerate(warm):
        add(f"W-{k + 1:02d}", 0, src, SHOW, warmup=True)
    ids = {}
    for j in range(len(pool_all)):
        iid = f"B-{j + 1:03d}"
        ids[j] = iid
        add(iid, sess_of[j], pool_all[j], SHOW, trap=j >= len(formal), hold=j in holdout)
    rep = rng.choice([j for j in range(len(formal)) if sess_of[j] < n_sess], size=30, replace=False)
    for k, j in enumerate(rep):
        sess = int(rng.integers(sess_of[j] + 1, n_sess + 1))
        add(f"R-{k + 1:02d}", sess, pool_all[j], SHOW - 10, repeat_of=ids[j])

    by_s = {}
    for it in items:
        by_s.setdefault(it["session"], []).append(it)
    final = []
    for s in sorted(by_s):
        lst = by_s[s]
        rng.shuffle(lst)
        for o, it in enumerate(lst):
            it["order"] = o + 1
            key[it["id"]]["order"] = o + 1
        final += lst

    # 最终全量断言：100% 题目最后一根 K 线必须 c > o 且 c[-2] <= ma20[-2] 且 c[-1] > ma20[-1]
    for it in final:
        b = it["bars"]
        assert b["c"][-1] > b["o"][-1], f"{it['id']} not bullish!"
        assert b["c"][-2] <= b["ma20"][-2] and b["c"][-1] > b["ma20"][-1], f"{it['id']} not crossing MA20!"

    out = ROOT / "apps/chanlun-web/blind"
    out.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(dict(version=2, seed=args.seed, items=final), separators=(",", ":"))
    (out / "items.json").write_text(blob)
    (out / "items.js").write_text("window.BLIND_DATA = " + blob + ";")
    kd = ROOT / "reports/blind"
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))
    cnt = pd.DataFrame(key.values())
    print("题目总数:", len(final), "| 100% 通过当根阳线上穿 MA20 校验 | 各场:", cnt.groupby("session").size().to_dict())


if __name__ == "__main__":
    main()
