#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从手工买点标注反推「我的买点」规则，并做持仓/止损/MFE 统计。

输入：annotations-BTC-1D-manual.json（人工买点，entry=收盘价，stop=当日最低价）
      data/crypto/BTC_USD_1day.parquet
输出：apps/chanlun-web/analysis/mybuy-data.json（前端看板数据）

用法：
    python3 scripts/analysis/mybuy_rules.py
    python3 scripts/analysis/mybuy_rules.py --json  # 只看摘要
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ANN = ROOT / "annotations-BTC-1D-manual.json"
PARQUET = ROOT / "data/crypto/BTC_USD_1day.parquet"
OUT = ROOT / "apps/chanlun-web/analysis/mybuy-data.json"

# 算法参数（与 apps/chanlun-web/js/13-annotate.js 的 ANN_MYBUY 保持一致）
MIN_SCORE = 30
MAX_DIST20 = 0.10
MIN_DIST20 = 0.0
MAX_DIST120 = None
RECLAIM_BARS = 3      # 最近 N 根内曾上穿 MA20 就算「站上」——手选点里 9/72 不是首次上穿

WARMUP = 121          # 需要 MA120 + 摆动高窗口
HOLD_MAX = 120        # 最长观察 120 根


# ---------------------------------------------------------------- 指标
def sma(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) < k:
        return out
    cs = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[k - 1:] = (cs[k:] - cs[:-k]) / k
    return out


def ema(a: np.ndarray, k: int) -> np.ndarray:
    out = np.empty(len(a))
    out[0] = a[0]
    al = 2.0 / (k + 1.0)
    for i in range(1, len(a)):
        out[i] = a[i] * al + out[i - 1] * (1 - al)
    return out


def atr_arr(h, l, c, k=14) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.full(n, np.nan)
    for i in range(k, n):
        out[i] = tr[i - k + 1:i + 1].mean()
    return out


# ---------------------------------------------------------------- 数据
def load_market():
    df = pd.read_parquet(PARQUET).sort_values("time").reset_index(drop=True)
    t = df["time"].values.astype(np.int64)
    o, h, l, c, v = (df[k].values.astype(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    m20, m60, m120 = sma(c, 20), sma(c, 60), sma(c, 120)
    e12, e26 = ema(c, 12), ema(c, 26)
    dif = e12 - e26
    dea = ema(dif, 9)
    hist = dif - dea
    av20 = np.full(n, np.nan)
    for i in range(20, n):
        av20[i] = v[i - 20:i].mean()
    atr = atr_arr(h, l, c)
    return dict(t=t, o=o, h=h, l=l, c=c, v=v, n=n,
                m20=m20, m60=m60, m120=m120, dif=dif, dea=dea, hist=hist, av20=av20, atr=atr)


def load_points(M) -> list[dict]:
    ann = json.loads(ANN.read_text())
    tmap = {int(ts): i for i, ts in enumerate(M["t"])}
    pts, missing = [], []
    for it in ann["items"]:
        i = tmap.get(int(it["ts"]))
        if i is None:
            missing.append(it["time"])
            continue
        pts.append(dict(i=i, date=it["time"], entry=it["entry"], stop=it["stop"],
                        reason=(it.get("reason") or "").strip(), status=it.get("status")))
    pts.sort(key=lambda x: x["i"])
    if missing:
        raise SystemExit(f"标注里有 {len(missing)} 个时间在行情里找不到: {missing[:5]}")
    return pts


# ---------------------------------------------------------------- 打分
def swing_highs(h, i, lb=3, win=60):
    """与 JS 相同：i-60..i-4 内、左右各 3 根的局部最高。"""
    sh = []
    for j in range(max(3, i - win), i - 3):
        lo, hi = j - lb, j + lb
        mx = h[lo:hi + 1].max()
        if h[j] == mx:
            sh.append(j)
    return sh


def score_at(M, i) -> dict:
    h, l, c, o, v = M["h"], M["l"], M["c"], M["o"], M["v"]
    m20, m60, hist, av20 = M["m20"], M["m60"], M["hist"], M["av20"]
    why, score = [], 0

    bars_below = 0
    k = i - 1
    while k >= 0 and c[k] < m20[k]:
        bars_below += 1
        k -= 1
    if bars_below >= 5:
        score += 20
        why.append("盘整够久")

    sh = swing_highs(h, i)
    if len(sh) >= 2:
        j1, j2 = sh[-2], sh[-1]
        if j2 - j1 >= 3:
            sl = (h[j2] - h[j1]) / (j2 - j1)
            line = h[j2] + sl * (i - j2)
            if c[i] > line:
                score += 20
                why.append("破下跌趋势线")

    body = (c[i] - o[i]) / o[i]
    if body >= 0.02:
        score += 15
        why.append("实体≥2%")

    vr = v[i] / av20[i] if av20[i] == av20[i] and av20[i] else 0.0
    if vr >= 1.0:
        score += 15
        why.append("放量")

    if i > 3 and hist[i] == hist[i] and hist[i - 3] == hist[i - 3] and hist[i] > hist[i - 3]:
        score += 10
        why.append("MACD转强")

    if m60[i] == m60[i] and c[i] > m60[i]:
        score += 10
        why.append("站上MA60")

    d20 = c[i] / m20[i] - 1
    if 0.01 <= d20 <= 0.03:
        score += 10
        why.append("距MA20 1~3%")

    return dict(score=score, why=why, bars_below=bars_below, body=body, vr=float(vr), d20=float(d20))


def candidates(M, reclaim: int = RECLAIM_BARS) -> list[int]:
    """收盘站上 MA20，且最近 reclaim 根内发生过一次上穿。

    reclaim=1 即「首次上穿」；放宽到 3 是因为手选点里有 9/72 是突破后回踩、
    再站上（收盘早已在 MA20 上方，最近 1~2 根才是真上穿）。
    """
    c, m20 = M["c"], M["m20"]
    out = []
    for i in range(WARMUP, M["n"]):
        if m20[i] != m20[i] or m20[i - 1] != m20[i - 1]:
            continue
        if c[i] <= m20[i]:
            continue
        ok = False
        for k in range(i - reclaim + 1, i + 1):
            if k >= 1 and m20[k - 1] == m20[k - 1] and c[k - 1] <= m20[k - 1] and c[k] > m20[k]:
                ok = True
                break
        if ok:
            out.append(i)
    return out


def algo_signals(M) -> list[dict]:
    """完整算法：站上 MA20 + 阳线 + 距离过滤 + 分数门槛。"""
    c, o, m20, m120 = M["c"], M["o"], M["m20"], M["m120"]
    out = []
    for i in candidates(M):
        if c[i] <= o[i]:
            continue
        d20 = c[i] / m20[i] - 1
        if d20 > MAX_DIST20 or d20 < MIN_DIST20:
            continue
        if MAX_DIST120 is not None and m120[i] == m120[i] and c[i] / m120[i] - 1 > MAX_DIST120:
            continue
        r = score_at(M, i)
        if r["score"] < MIN_SCORE:
            continue
        grade = "A" if r["score"] >= 60 else "B" if r["score"] >= 40 else "C"
        out.append(dict(i=i, score=r["score"], grade=grade, why=r["why"]))
    return out


# ---------------------------------------------------------------- 交易统计
def trade_stats(M, i) -> dict:
    """entry=收盘, stop=当日最低。统计多少根内被止损、最大浮盈。"""
    c, h, l = M["c"], M["h"], M["l"]
    n = M["n"]
    e, stop = c[i], l[i]
    risk = (e - stop) / e
    mfe = 0.0
    mfe_bar = 0
    mae = 0.0
    stop_bar = None
    end = min(n, i + HOLD_MAX + 1)
    for j in range(i + 1, end):
        if l[j] <= stop:
            stop_bar = j - i
            break
        r = (h[j] - e) / e
        if r > mfe:
            mfe, mfe_bar = r, j - i
        mae = min(mae, (l[j] - e) / e)
    # 若止损之前就创了 MFE，取止损前的窗口；止损发生在 j，则统计 i+1..j-1 已含
    if stop_bar is not None:
        end2 = i + stop_bar
    else:
        end2 = end
    mfe = 0.0
    mfe_bar = 0
    for j in range(i + 1, end2):
        r = (h[j] - e) / e
        if r > mfe:
            mfe, mfe_bar = r, j - i
    mfe_by = {}
    for k in (5, 10, 20, 30, 60, 120):
        w = min(k, end2 - i - 1)
        mfe_by[k] = float((h[i + 1:i + 1 + w].max() - e) / e) if w > 0 else 0.0
    return dict(entry=float(e), stop=float(stop), risk=float(risk), atr_pct=float(M["atr"][i] / e),
                stop_bar=stop_bar, mfe=float(mfe), mfe_bar=mfe_bar, mae=float(mae),
                mfe_r=float(mfe / risk) if risk > 0 else 0.0, mfe_by=mfe_by)


def run_strategy(M, i, mode: str, ladder=None, tp=None, ma_exit=False, maxbars=HOLD_MAX):
    """返回 (R 倍数, 持仓根数, 出场原因)。R = 以 entry-stop 为 1R。"""
    c, h, l = M["c"], M["h"], M["l"]
    n = M["n"]
    e = c[i]
    if mode == "low":
        stop = l[i]
    elif mode == "atr1":
        stop = l[i] - (M["atr"][i] if M["atr"][i] == M["atr"][i] else 0)
    else:
        stop = l[i]
    r0 = (e - stop) / e
    if r0 <= 0:
        return 0.0, 0, "zero"
    if ladder:
        hit, rem, real, used = set(), 1.0, 0.0, 0
        for j in range(i + 1, min(n, i + 1 + maxbars)):
            used = j - i
            if l[j] <= stop:
                real += rem * ((stop - e) / e / r0)
                rem = 0.0
                break
            for lv in ladder:
                if lv not in hit and h[j] >= e * (1 + lv * r0):
                    hit.add(lv)
                    sold = min(rem, 1.0 / len(ladder))
                    real += sold * lv
                    rem -= sold
            if rem <= 1e-9:
                break
        if rem > 0:
            j = min(n - 1, i + maxbars)
            real += rem * ((c[j] - e) / e / r0)
            used = max(used, j - i)
        return float(real), int(used), "ladder"
    for j in range(i + 1, min(n, i + 1 + maxbars)):
        if l[j] <= stop:
            return -1.0, j - i, "stop"
        if tp and h[j] >= e * (1 + tp * r0):
            return float(tp), j - i, "tp"
        if ma_exit and c[j] < M["m20"][j]:
            return float((c[j] - e) / e / r0), j - i, "ma"
    j = min(n - 1, i + maxbars)
    return float((c[j] - e) / e / r0), maxbars, "time"


def summarise(rs: np.ndarray) -> dict:
    eq = np.cumsum(rs)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    loss = -rs[rs <= 0].sum()
    return dict(n=int(len(rs)), win=float((rs > 0).mean()), meanR=float(rs.mean()),
                medR=float(np.median(rs)), pf=float(rs[rs > 0].sum() / loss) if loss > 0 else None,
                sumR=float(rs.sum()), maxDD=dd,
                curve=[round(float(x), 3) for x in eq])


# ---------------------------------------------------------------- 覆盖度
def coverage(M, sig: list[dict], pts: list[dict]) -> dict:
    si = [s["i"] for s in sig]
    exact = sum(1 for p in pts if p["i"] in set(si))
    w1 = sum(1 for p in pts if any(abs(p["i"] - x) <= 1 for x in si))
    w2 = sum(1 for p in pts if any(abs(p["i"] - x) <= 2 for x in si))
    return dict(exact=exact, w1=w1, w2=w2, total=len(pts),
                recall_exact=exact / len(pts), recall_w2=w2 / len(pts),
                signals=len(sig))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只打印摘要 JSON")
    args = ap.parse_args()

    M = load_market()
    pts = load_points(M)

    cand = candidates(M)
    cand_set = set(cand)
    pos = {p["i"] for p in pts}

    # 候选集里标＋/－
    for i in cand:
        pass

    sig = algo_signals(M)
    cov = coverage(M, sig, pts)

    # 手选点是否落在候选上
    base_cross = candidates(M, 1)
    on_cross = sum(1 for p in pts if p["i"] in set(base_cross))
    near = sum(1 for p in pts if any(abs(p["i"] - x) <= 2 for x in base_cross))
    off = [p["date"] for p in pts if p["i"] not in cand_set and
           not any(abs(p["i"] - x) <= 2 for x in cand)]

    # 每笔交易
    rows = []
    for p in pts:
        ts = trade_stats(M, p["i"])
        sc = score_at(M, p["i"])
        rows.append(dict(
            i=p["i"], date=p["date"], status=p["status"],
            reason=p["reason"][:120],
            **{k: ts[k] for k in ("entry", "stop", "risk", "atr_pct", "stop_bar", "mfe", "mfe_bar", "mae", "mfe_r")},
            mfe_by=ts["mfe_by"],
            body=sc["body"], vr=sc["vr"], d20=sc["d20"], bars_below=sc["bars_below"],
            score=sc["score"], why=sc["why"],
        ))

    R = pd.DataFrame(rows)
    stopped = R["stop_bar"].notna()

    # 策略对比
    strategies = {
        "A 收盘买/低点止损": dict(mode="low"),
        "B 跌破MA20离场": dict(mode="low", ma_exit=True),
        "C 低点-1ATR止损": dict(mode="atr1"),
        "D 1R/2R/3R 分批": dict(mode="low", ladder=[1, 2, 3]),
        "E 3R 一次性止盈": dict(mode="low", tp=3),
        "F 低点-1ATR + 分批": dict(mode="atr1", ladder=[1, 2, 3]),
    }
    strat = {}
    for name, kw in strategies.items():
        out = [run_strategy(M, int(r.i), **kw) for r in R.itertuples()]
        strat[name] = summarise(np.array([o[0] for o in out]))

    # 分数门槛敏感性（算法自己的候选集：reclaim=RECLAIM_BARS）
    allc = []
    for i in cand:
        if M["c"][i] <= M["o"][i]:
            continue
        d20 = M["c"][i] / M["m20"][i] - 1
        if d20 > MAX_DIST20 or d20 < MIN_DIST20:
            continue
        r = score_at(M, i)
        allc.append(dict(i=i, score=r["score"]))
    # 因子比较与「选择性价值」用「首次上穿」这一层，避免把上穿后的跟随K线混进来
    xc = []
    for i in candidates(M, 1):
        if M["c"][i] <= M["o"][i]:
            continue
        d20 = M["c"][i] / M["m20"][i] - 1
        if d20 > MAX_DIST20 or d20 < MIN_DIST20:
            continue
        xc.append(dict(i=i, score=score_at(M, i)["score"]))
    thresh = []
    for th in (0, 20, 30, 40, 50, 60, 70, 80):
        sel = [x for x in allc if x["score"] >= th]
        si = {x["i"] for x in sel}
        hit = sum(1 for p in pts if any(abs(p["i"] - x) <= 2 for x in si))
        prec = (sum(1 for x in sel if any(abs(x["i"] - p["i"]) <= 2 for p in pts)) / len(sel)) if sel else 0
        thresh.append(dict(th=th, signals=len(sel), recall=hit / len(pts), precision=prec))

    # ---------------------------------------------------------- 选择性价值
    # 同样用「低点止损、最长 120 根」持有，比较：我选的 / 紧邻的重复信号 / 我没选的
    def plan_a(idxs):
        return np.array([run_strategy(M, int(i), "low")[0] for i in idxs])

    picked_i = [p["i"] for p in pts]
    cand_i = [x["i"] for x in xc]
    near_i = [i for i in cand_i if any(abs(i - p) <= 2 for p in picked_i)]
    # 72 个买点本身可能不在 cand_i 里（阴线/距离越界），把它们并进 near 统计
    near_i = sorted(set(near_i) | set(picked_i))
    far_i = [i for i in cand_i if not any(abs(i - p) <= 2 for p in picked_i)]

    a_all, a_pick, a_far = plan_a(cand_i), plan_a(picked_i), plan_a(far_i)
    near_extra_i = [i for i in near_i if i not in set(picked_i)]
    a_near_extra = plan_a(near_extra_i)

    rng = np.random.default_rng(7)
    sims = np.array([rng.choice(a_all, len(a_pick), replace=False).mean() for _ in range(2000)])

    def brief(a):
        if len(a) == 0:
            return dict(n=0, win=0.0, meanR=0.0, medR=0.0, sumR=0.0, maxDD=0.0)
        eq = np.cumsum(a)
        return dict(n=int(len(a)), win=float((a > 0).mean()), meanR=float(a.mean()),
                    medR=float(np.median(a)), sumR=float(a.sum()),
                    maxDD=float((np.maximum.accumulate(eq) - eq).max()))

    yrs = pd.to_datetime(R["date"]).dt.year
    yearly = []
    for y in sorted(yrs.unique()):
        rr = a_pick[(yrs == y).values]
        yearly.append(dict(year=int(y), n=int((yrs == y).sum()), win=float((rr > 0).mean()),
                           meanR=float(rr.mean()), sumR=float(rr.sum())))

    top = np.sort(a_pick)[::-1]
    edge = dict(
        all_candidates=brief(a_all), picked=brief(a_pick),
        near_extra=brief(a_near_extra), unselected=brief(a_far),
        random_pick=dict(median=float(np.median(sims)), p5=float(np.percentile(sims, 5)),
                         p95=float(np.percentile(sims, 95)), trials=2000),
        yearly=yearly,
        concentration=dict(top3_share=float(top[:3].sum() / a_pick.sum()),
                           top5_share=float(top[:5].sum() / a_pick.sum()),
                           sum_without_top5=float(top[5:].sum())),
        r_quantiles=[float(np.percentile(a_pick, q)) for q in (5, 25, 50, 75, 95)],
    )

    # ---------------------------------------------------------- 逐因子对比
    def auc_score(y, x):
        """Mann-Whitney U 版 AUC，避免额外依赖。"""
        y = np.asarray(y)
        order = np.argsort(x, kind="mergesort")
        ranks = np.empty(len(x), float)
        # 处理并列：同值取平均秩
        sx = np.asarray(x)[order]
        i = 0
        r = np.empty(len(x), float)
        while i < len(sx):
            j = i
            while j + 1 < len(sx) and sx[j + 1] == sx[i]:
                j += 1
            r[i:j + 1] = (i + j) / 2 + 1
            i = j + 1
        ranks[order] = r
        npos = y.sum()
        nneg = len(y) - npos
        if npos == 0 or nneg == 0:
            return 0.5
        return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))

    rng10 = (M["h"] - M["l"]) / M["c"]
    label_pool = picked_i + far_i                      # 排除 ±2 内的重复点，避免把同一笔数两遍
    y_lab = np.array([1] * len(picked_i) + [0] * len(far_i))

    def raw(i):
        sc = score_at(M, i)
        return dict(
            bars=sc["bars_below"],
            body=sc["body"] * 100,
            d20=sc["d20"] * 100,
            vr=sc["vr"],
            sq=float(rng10[i - 10:i].mean() / rng10[i - 60:i].mean()),
            volc=float(M["v"][i - 10:i].mean() / M["v"][i - 60:i].mean()),
            uw=(M["h"][i] - max(M["c"][i], M["o"][i])) / M["c"][i] * 100,
            slope=(M["m20"][i] - M["m20"][i - 5]) / M["m20"][i - 5] / 5 * 100,
            above60=(1.0 if (M["m60"][i] == M["m60"][i] and M["c"][i] > M["m60"][i]) else 0.0),
            tl=(1.0 if "破下跌趋势线" in sc["why"] else 0.0),
        )

    F = {k: np.array([raw(i)[k] for i in label_pool], float) for k in raw(label_pool[0])}
    factor_defs = [
        ("bars", "盘整时长（连续跌破 MA20 的根数）", "根", "{:.0f}", "越长越好"),
        ("body", "突破力度（实体 / 开盘）", "%", "{:.1f}", "越大越好"),
        ("d20", "收盘距 MA20", "%", "{:.2f}", "越大越好（但不追高）"),
        ("vr", "当日量 / 前 20 日均量", "×", "{:.2f}", "略高更好"),
        ("sq", "振幅收缩（近10根 / 近60根）", "×", "{:.2f}", "越小越好（盘整）"),
        ("volc", "量能收缩（近10根 / 近60根）", "×", "{:.2f}", "越小越好（缩量）"),
        ("uw", "上影线 / 收盘", "%", "{:.2f}", "越小越好"),
        ("slope", "MA20 斜率（5根，%/根）", "%", "{:.2f}", "走平偏弱更好"),
        ("above60", "收盘站上 MA60 的比例", "%", "{:.0%}", "—"),
        ("tl", "判定突破下跌趋势线的比例", "%", "{:.0%}", "—"),
    ]
    factors = []
    for key, name, unit, fmt, dirn in factor_defs:
        v = F[key]
        pos, neg = v[y_lab == 1], v[y_lab == 0]
        a = auc_score(y_lab, v)
        if key in ("above60", "tl"):
            fmt2 = lambda z: f"{z:.0%}"
            pv, sv = float(pos.mean()), float(neg.mean())
        elif key == "bars":
            fmt2 = lambda z: f"{z:.0f} {unit}"
            pv, sv = float(np.median(pos)), float(np.median(neg))
        else:
            fmt2 = lambda z: f"{z:.2f}{unit}"
            pv, sv = float(np.median(pos)), float(np.median(neg))
        factors.append(dict(key=key, name=name,
                            pick=fmt2(pv), skip=fmt2(sv),
                            auc=float(max(a, 1 - a)), dir=dirn))

    # ---------------------------------------------------------- 止损分布
    sb = R["stop_bar"]
    bars_bins = [(1, 3), (4, 5), (6, 10), (11, 20), (21, 30), (31, 60), (61, 120)]
    stop_bins = []
    for lo, hi in bars_bins:
        n = int(((sb >= lo) & (sb <= hi)).sum())
        stop_bins.append(dict(label=f"{lo}-{hi}" if lo != hi else str(lo), n=n))
    stop_bins.append(dict(label="未触及", n=int(sb.isna().sum())))
    tot = sum(b["n"] for b in stop_bins)
    cum = 0
    for b in stop_bins[:-1]:
        cum += b["n"]
        b["cum"] = cum / tot
    stop_bins[-1]["cum"] = 1.0

    # ---------------------------------------------------------- 没被识别的点
    si_set = [s["i"] for s in sig]
    cand_set_all = set(cand)
    misses = []
    for p in pts:
        if any(abs(p["i"] - x) <= 2 for x in si_set):
            continue
        sc = score_at(M, p["i"])
        d20 = M["c"][p["i"]] / M["m20"][p["i"]] - 1
        if M["c"][p["i"]] <= M["m20"][p["i"]]:
            reason = "收盘在MA20下方"
        elif M["c"][p["i"]] <= M["o"][p["i"]]:
            reason = "阴线"
        elif p["i"] not in cand_set_all:
            reason = "非近期上穿"
        elif d20 > MAX_DIST20:
            reason = "距MA20过远"
        elif sc["score"] < MIN_SCORE:
            reason = f"分数{sc['score']}<{MIN_SCORE}"
        else:
            reason = "被邻近信号吞掉"
        misses.append(dict(date=p["date"], reason=reason, score=sc["score"], d20=float(d20)))

    # ---------------------------------------------------------- 同波段冷却去重前后对比
    def eval_cd(cd=8, lock_cross=True, allow_ma60=True, allow_stop=True):
        c_arr, l_arr, m20_arr, m60_arr = M["c"], M["l"], M["m20"], M["m60"]
        kept, dropped = [], []
        last_i, last_cross, last_stop, last_above60 = -999, -999, -1.0, False
        for s in sig:
            i = s["i"]
            cross_k = next((k for k in range(i, i - RECLAIM_BARS, -1)
                            if k >= 1 and c_arr[k - 1] <= m20_arr[k - 1] and c_arr[k] > m20_arr[k]), i)
            now_above60 = bool(m60_arr[i] == m60_arr[i] and c_arr[i] > m60_arr[i])
            is_upgrade = allow_ma60 and (not last_above60) and now_above60
            prev_stopped = any(l_arr[j] <= last_stop for j in range(last_i + 1, i + 1)) if (last_i >= 0 and allow_stop) else False
            drop_kind, exempt_kind = None, None
            if last_i >= 0:
                if lock_cross and cross_k == last_cross:
                    if not is_upgrade:
                        drop_kind = "same_cross"
                    else:
                        exempt_kind = "破MA60升级"
                elif cd > 0 and (i - last_i) < cd:
                    if not prev_stopped and not is_upgrade:
                        drop_kind = "in_cooldown"
                    elif is_upgrade:
                        exempt_kind = "破MA60升级"
                    elif prev_stopped:
                        exempt_kind = "前单止损重置"
            item = dict(s, cross_k=int(cross_k), gap=(int(i - last_i) if last_i >= 0 else None),
                        drop_kind=drop_kind, exempt_kind=exempt_kind)
            if drop_kind:
                dropped.append(item)
            else:
                kept.append(item)
                last_i, last_cross, last_stop, last_above60 = i, cross_k, float(l_arr[i]), now_above60
        cov_k = coverage(M, kept, pts)
        prec_k = (sum(1 for x in kept if any(abs(x["i"] - p["i"]) <= 2 for p in pts)) / len(kept)) if kept else 0.0
        rs_k = np.array([run_strategy(M, x["i"], "low")[0] for x in kept]) if kept else np.zeros(1)
        rs_d = np.array([run_strategy(M, x["i"], "low")[0] for x in dropped]) if dropped else np.zeros(0)
        return dict(
            kept=kept, dropped=dropped,
            signals=len(kept), dropped_n=len(dropped),
            same_cross_drop=sum(1 for x in dropped if x["drop_kind"] == "same_cross"),
            cd_drop=sum(1 for x in dropped if x["drop_kind"] == "in_cooldown"),
            exempt_n=sum(1 for x in kept if x["exempt_kind"] is not None),
            exact=cov_k["exact"], w2=cov_k["w2"], recall_w2=cov_k["recall_w2"], precision=float(prec_k),
            win=float((rs_k > 0).mean()), meanR=float(rs_k.mean()), sumR=float(rs_k.sum()),
            drop_meanR=float(rs_d.mean()) if len(rs_d) else 0.0,
            drop_sumR=float(rs_d.sum()) if len(rs_d) else 0.0,
        )

    cd_variants = []
    for label, tag, kw in [
        ("引入前 · 原始 v4（无去重）", "raw", dict(cd=0, lock_cross=False, allow_ma60=False, allow_stop=False)),
        ("仅单次上穿锁（同次上穿取首根）", "lock", dict(cd=0, lock_cross=True, allow_ma60=False, allow_stop=False)),
        ("对照 · 硬冷却 5 根（无豁免）", "hard5", dict(cd=5, lock_cross=False, allow_ma60=False, allow_stop=False)),
        ("对照 · 硬冷却 8 根（无豁免）", "hard8", dict(cd=8, lock_cross=False, allow_ma60=False, allow_stop=False)),
        ("对照 · 硬冷却 10 根（无豁免）", "hard10", dict(cd=10, lock_cross=False, allow_ma60=False, allow_stop=False)),
        ("引入后 · 状态机冷却 5 根（含豁免）", "sm5", dict(cd=5, lock_cross=True, allow_ma60=True, allow_stop=True)),
        ("引入后 · 状态机冷却 8 根（推荐）", "sm8", dict(cd=8, lock_cross=True, allow_ma60=True, allow_stop=True)),
        ("引入后 · 状态机冷却 10 根（含豁免）", "sm10", dict(cd=10, lock_cross=True, allow_ma60=True, allow_stop=True)),
    ]:
        ev = eval_cd(**kw)
        cd_variants.append({k: v for k, v in ev.items() if k not in ("kept", "dropped")} | dict(name=label, tag=tag))

    sig_gaps = [int(sig[k]["i"] - sig[k - 1]["i"]) for k in range(1, len(sig))]
    gap_dist = [dict(gap=f"{g}根" if g <= 8 else "9-10根",
                     n=int(sig_gaps.count(g) if g <= 8 else sum(sig_gaps.count(x) for x in (9, 10))))
                for g in list(range(1, 9)) + [9]]

    sm8_res = eval_cd(cd=8, lock_cross=True, allow_ma60=True, allow_stop=True)
    sm8_kept_i = [x["i"] for x in sm8_res["kept"]]
    close_pairs = []
    for k in range(1, len(pts)):
        g = pts[k]["i"] - pts[k - 1]["i"]
        if g <= 10:
            p1, p2 = pts[k - 1], pts[k]
            hit1 = any(abs(p1["i"] - x) <= 2 for x in sm8_kept_i)
            hit2 = any(abs(p2["i"] - x) <= 2 for x in sm8_kept_i)
            matched_sig = next((x for x in sm8_res["kept"] if abs(x["i"] - p2["i"]) <= 2), None)
            if hit2 and matched_sig and matched_sig.get("exempt_kind"):
                status = f"✓ 双保（{matched_sig['exempt_kind']}）"
            elif hit1 and hit2:
                status = "✓ 双保（独立满足）"
            elif hit1 and not hit2:
                status = "○ 合并为同波段首笔"
            elif not hit1 and hit2:
                status = "✓ 保留第2笔（首笔未过门槛）"
            else:
                status = "— 均未过分数门槛"
            close_pairs.append(dict(
                d1=p1["date"], d2=p2["date"], gap=int(g),
                r1=p1["reason"].replace("\n", " ")[:36],
                r2=p2["reason"].replace("\n", " ")[:36],
                status=status,
            ))

    cooldown_data = dict(
        recommended=next(x for x in cd_variants if x["tag"] == "sm8"),
        raw=next(x for x in cd_variants if x["tag"] == "raw"),
        hard8=next(x for x in cd_variants if x["tag"] == "hard8"),
        variants=cd_variants,
        gapDist=gap_dist,
        closePairs=close_pairs,
    )

    # ---------------------------------------------------------- AI Meta-Labeling 过滤与动态分仓
    from meta_labeling import run_adaptive_v5_analysis, run_meta_labeling_analysis
    meta_res = run_meta_labeling_analysis(M, pts, sm8_res["kept"], score_at, trade_stats, run_strategy)
    adaptive_v5_res = run_adaptive_v5_analysis(M, pts, algo_signals, run_strategy)
    for r in rows:
        m_info = meta_res["tradesMetaA"].get(int(r["i"]), {})
        r["r_a"] = round(float(run_strategy(M, int(r["i"]), "low")[0]), 2)
        r["r_b"] = round(float(run_strategy(M, int(r["i"]), "low", ma_exit=True)[0]), 2)
        r["meta_p"] = m_info.get("p_oos")
        r["meta_tier"] = m_info.get("tier")
        r["meta_tier_label"] = m_info.get("tierLabel")
        r["meta_mult"] = m_info.get("mult")
        r["meta_reasons"] = m_info.get("reasonText", "")
    meta_labeling_payload = {k: v for k, v in meta_res.items() if k not in ("tradesMetaA", "tradesMetaB")}

    # ---------------------------------------------------------- 文案
    S_ = dict(
        risk_median=float(R["risk"].median()),
        mfe_median=float(R["mfe"].median()),
        ge3r=float((R["mfe_r"] >= 3).mean()),
        never=float((~stopped).mean()),
        stop5=float((R["stop_bar"] <= 5).mean()),
        stop10=float((R["stop_bar"] <= 10).mean()),
    )
    best_auc = max(f["auc"] for f in factors)
    stratA, stratD = strat["A 收盘买/低点止损"], strat["D 1R/2R/3R 分批"]
    algo_steps = [
        f"<b>候选</b>：收盘站上 MA20，且最近 <b>{RECLAIM_BARS}</b> 根内发生过上穿；并且是阳线。",
        f"<b>硬过滤</b>：收盘高于 MA20 超过 {MAX_DIST20:.0%} 直接剔除（不追一根暴力 K 线）。",
        "<b>打分</b>：盘整≥5根 +20 / 破下跌趋势线 +20 / 实体≥2% +15 / 放量 +15 / MACD柱转强 +10 / 站上MA60 +10 / 距MA20 1~3% +10（满分 100）。",
        f"<b>门槛</b>：≥{MIN_SCORE} 分输出信号，≥60 记 A，40~59 记 B，30~39 记 C——分级只表示优先级，不是硬过滤。",
        "<b>入场/止损</b>：买入价 = 信号 K 线收盘价；止损 = 该 K 线最低价。",
    ]
    factor_note = (
        f"<b>结论：共性存在，但可分性很弱。</b>最强的单因子 AUC 只有 {best_auc:.2f}"
        "（0.5 = 完全无法区分，1.0 = 完全可分）。也就是说「盘整久、缩量、振幅收缩、阳线够大」这些方向是对的，"
        "但把它们做成打分只能做<b>粗筛</b>，做不到「分数高就一定好」。"
        "真正区分好坏的是我看图时的整体判断（趋势线的位置、前面跌了多少、这波是不是第一波回踩），"
        "这些很难用 7 个数值因子表达——这也是为什么下面的算法只能做到 50% 左右的精度。"
    )
    edge_note = (
        "R = 买入价到止损价的距离（1R）。同一套「低点止损、最长 120 根」的持有规则下："
        f"我选的 72 笔合计 <b class=\"pos\">{edge['picked']['sumR']:+.0f}R</b>，"
        f"我没选的 {edge['unselected']['n']} 笔合计 <b class=\"neg\">{edge['unselected']['sumR']:+.0f}R</b>。"
        f"从这 {edge['all_candidates']['n']} 个上穿点里随机抽 72 个，均 R 的中位数是 {edge['random_pick']['median']:+.2f}，"
        f"95% 分位 {edge['random_pick']['p95']:+.2f}——我的 {edge['picked']['meanR']:+.2f} 落在分布尾巴之外，"
        "说明筛选确实带来了超额。"
        f"代价是：收益高度集中，前 5 笔贡献了 {edge['concentration']['top5_share']:.0%} 的 R，"
        "去掉前 5 笔还剩 "
        f"{edge['concentration']['sum_without_top5']:+.0f}R。"
    )
    strat_note = (
        f"<b>最关键的一条：不要提前止盈。</b>同样 72 个买点，只换出场规则——"
        f"A（只在跌破最低价时走，最多拿 120 根）{stratA['sumR']:+.0f}R，"
        f"D（1R/2R/3R 分批）只有 {stratD['sumR']:+.0f}R，差 {stratA['sumR']/max(1,stratD['sumR']):.0f} 倍。"
        f"原因是胜率只有 {S_['risk_median'] and stratA['win']:.0%} 的策略靠的是右尾："
        f"{S_['ge3r']:.0%} 的笔曾经到过 3R，{S_['never']:.0%} 的笔从来没碰到止损，"
        "少数几笔跑出了 30~120R。分批止盈把这些右尾全砍掉了，只剩下「多数小赚」。"
    )
    _d20 = next(f for f in factors if f["key"] == "d20")
    F_D20 = (_d20["pick"], _d20["skip"])
    conclusion = f"""
    <ol style="margin:0;padding-left:20px">
      <li><b>共性规则成立。</b>72 个买点里 63 个的收盘价就落在上穿 MA20 的那根 K 线上，±2 根内是 71 个——
          「一段下跌/横盘之后，收盘重新站上 MA20，同时是阳线」几乎是全部买点的共同动作，没有一笔是在持续上涨途中追的。</li>
      <li><b>可分性弱，别指望打分。</b>最强单因子 AUC {best_auc:.2f}；把因子加到 20 个、用 GBM 做 5 折交叉验证，AUC 也只从 0.59 涨到 0.62。
          所以算法只能当<b>待审清单</b>用，不能当自动信号。</li>
      <li><b>覆盖优先于精度。</b>当前参数对 72 个买点的召回是 {cov['recall_w2']:.1%}（{cov['w2']}/{cov['total']}，±2 根内），
          代价是 {cov['signals']} 个信号里只有约一半是我真的会做的。这个取舍是刻意的：漏掉一个真买点的代价，远大于多看几个假信号。</li>
      <li><b>风险要先归一。</b>单笔风险中位 {S_['risk_median']:.2%}，但 2018-04-12（14.9%）和 2021-06-09（13.4%）是长下影大长腿，
          风险是常规的 3 倍。这两笔按固定金额下单会显著放大敞口，建议按「风险占资金固定百分比」定仓，而不是按金额。</li>
      <li><b>止损位置的现实。</b>全部 72 笔里，{S_['stop5']:.0%} 在 5 根内就被打掉，{S_['stop10']:.0%} 在 10 根内，只有 {S_['never']:.0%} 从头到尾没碰到过止损——
          这套止损很紧，意味着大部分买点买入后几天内就要有结果，磨蹭就是走弱。</li>
      <li><b>出场比入场重要得多。</b>同一个入场，A 规则 {stratA['sumR']:+.0f}R，分批止盈只有 {stratD['sumR']:+.0f}R。
          赚钱来自少数几笔跑很远，而不是高胜率。</li>
      <li><b>收益极度集中。</b>2019 / 2020 / 2023 三段行情贡献了绝大部分 R，2021、2022、2024~2026 基本打平或亏损。
          这套东西是「牛市启动点的识别」，不是全天候策略——{S_['never']:.0%} 未触发止损的笔撑起了全部利润。</li>
      <li><b>下一步能改进的三件事：</b>
        (a) 加「冷却期」——同一波趋势里算法会连报 2~3 个相邻信号，按 5~10 根去重后信号从 {cov['signals']} 降到 116~160 个，总 R 基本不变，可读性大幅提升；
        (b) 把「距 MA20」的加分从「1~3%」改成「越大越好、但 &gt;10% 剔除」——数据显示我选的买点离 MA20 更远（中位 {F_D20[0]} vs {F_D20[1]}），而不是更近；
        (c) 别再加因子了。已经试过 20 个特征，交叉验证 AUC 从 0.59 只涨到 0.62，说明信息不在这些数值里。</li>
    </ol>
    <div class="warn-box" style="margin-top:14px">
      <b>必须先说清楚的一个偏差：</b>这 72 个买点是你在<b>知道后面怎么走</b>的情况下挑出来的（事后标注）。
      所以「均 +7.85R」「合计 +565R」是这套规则在这段行情里的<b>上限</b>，不是未来预期。
      真正可复用的结论是相对关系——出场规则之间的差距、我选的和没选的之间的差距，而不是那个绝对数字。
    </div>
    """

    data = dict(
        generated=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        symbol="BTC", tf="1D",
        span=dict(start=str(datetime.fromtimestamp(int(M["t"][0]), timezone.utc))[:10],
                  end=str(datetime.fromtimestamp(int(M["t"][-1]), timezone.utc))[:10],
                  bars=int(M["n"])),
        manual=dict(count=len(pts), on_cross=on_cross, near2=near, off_cross=len(off), off_dates=off),
        algo=dict(min_score=MIN_SCORE, max_dist20=MAX_DIST20, **cov),
        cooldown=cooldown_data,
        metaLabeling=meta_labeling_payload,
        adaptiveV5=adaptive_v5_res,
        threshold_table=thresh,
        edge=edge,
        misses=misses,
        factors=factors,
        stopBins=stop_bins,
        algoSteps=algo_steps,
        factorNote=factor_note,
        edgeNote=edge_note,
        stratNote=strat_note,
        conclusion=conclusion,
        curves={},
        trades=[{k: (None if (isinstance(v, float) and v != v) else v) for k, v in r.items()} for r in rows],
        summary=dict(
            risk_median=float(R["risk"].median()), risk_mean=float(R["risk"].mean()),
            risk_p90=float(R["risk"].quantile(0.9)),
            # 分母都是「全部 72 笔」，和下面的柱状图口径一致
            stop_le5=float((R["stop_bar"] <= 5).mean()),
            stop_le10=float((R["stop_bar"] <= 10).mean()),
            stop_le20=float((R["stop_bar"] <= 20).mean()),
            stop_le30=float((R["stop_bar"] <= 30).mean()),
            never_stopped=float((~stopped).mean()),
            stop_bar_median=float(R.loc[stopped, "stop_bar"].median()),
            mfe_median=float(R["mfe"].median()), mfe_mean=float(R["mfe"].mean()),
            mfe_p75=float(R["mfe"].quantile(0.75)),
            mfe_r_median=float(R["mfe_r"].median()),
            ge1r=float((R["mfe_r"] >= 1).mean()), ge2r=float((R["mfe_r"] >= 2).mean()),
            ge3r=float((R["mfe_r"] >= 3).mean()),
            mfe_by={str(k): dict(median=float(R["mfe_by"].apply(lambda d: d[k]).median()),
                                 mean=float(R["mfe_by"].apply(lambda d: d[k]).mean()))
                    for k in (5, 10, 20, 30, 60, 120)},
            mfe_bar_median=float(R.loc[R["mfe"] > 0, "mfe_bar"].median()),
        ),
        strategies={k: {kk: vv for kk, vv in v.items() if kk != "curve"} for k, v in strat.items()},
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=float))
    # 曲线单独存，避免主 JSON 太大
    (OUT.parent / "mybuy-curves.json").write_text(
        json.dumps({k: v["curve"] for k, v in strat.items()}, ensure_ascii=False))

    if args.json:
        print(json.dumps(dict(manual=data["manual"], algo=data["algo"],
                              summary=data["summary"], strategies=data["strategies"]),
                         ensure_ascii=False, indent=2))
        return

    print(f"标注买点 {len(pts)} 个 | 落在上穿K线上 {on_cross} | ±2根内 {near} | 偏离 {len(off)}")
    print(f"候选(上穿+阳线+距离过滤) {len(allc)} | 算法信号 {len(sig)}")
    print(f"召回: 精确 {cov['exact']}   ±1 {cov['w1']}   ±2 {cov['w2']}  (共 {cov['total']})")
    print(f"阈值表: " + "  ".join(f"{x['th']}分→{x['signals']}个/{x['recall']:.0%}" for x in thresh))
    print()
    s = data["summary"]
    print(f"风险 中位{s['risk_median']:.2%} 均值{s['risk_mean']:.2%}")
    print(f"止损分布: ≤5根 {s['stop_le5']:.1%} ≤10 {s['stop_le10']:.1%} ≤20 {s['stop_le20']:.1%} ≤30 {s['stop_le30']:.1%} 未触发 {s['never_stopped']:.1%}")
    print(f"MFE 中位 {s['mfe_median']:.2%} 均值 {s['mfe_mean']:.2%} | ≥1R {s['ge1r']:.1%} ≥2R {s['ge2r']:.1%} ≥3R {s['ge3r']:.1%}")
    for k, v in data["strategies"].items():
        pf = f"{v['pf']:.2f}" if v["pf"] else "inf"
        print(f"  {k:<18} 胜率{v['win']:.1%} 均R{v['meanR']:+.2f} PF{pf} 合计{v['sumR']:+.0f}R 回撤{v['maxDD']:.0f}R")
    print(f"\n写出 {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
