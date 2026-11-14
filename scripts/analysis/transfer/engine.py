"""参数化 AI-MA20 引擎：规则层（打分）→ 同波段冷却状态机 → 自动标签（MFE≥3R）。

k=1 + pct + low 口径与 scripts/analysis/mybuy_rules.py / freeze_ai_ma20.py 逐根一致。
所有计算纯因果（信号判定只用 <= i 的数据；标签/结果用 > i 的数据，仅用于评估）。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scale_spec import ScaleSpec


# ---------------------------------------------------------------- 指标（与 mybuy_rules 同口径）
def sma(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) < k:
        return out
    cs = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[k - 1:] = (cs[k:] - cs[:-k]) / k
    return out


def ema(a: np.ndarray, k: int) -> np.ndarray:
    al = 2.0 / (k + 1.0)
    return pd.Series(a).ewm(alpha=al, adjust=False).mean().to_numpy()


def atr14(h: np.ndarray, l: np.ndarray, c: np.ndarray, k: int = 14) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n)
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    out = np.full(n, np.nan)
    cs = np.cumsum(np.insert(tr, 0, 0.0))
    out[k:] = (cs[k + 1:] - cs[1:n - k + 1]) / k
    return out


def trailing_mean_excl(v: np.ndarray, w: int) -> np.ndarray:
    """av[i] = mean(v[i-w:i])（不含当根，与 mybuy_rules.av20 一致）。"""
    n = len(v)
    out = np.full(n, np.nan)
    cs = np.cumsum(np.insert(v.astype(float), 0, 0.0))
    out[w:] = (cs[w:n] - cs[0:n - w]) / w
    return out


def prepare(df: pd.DataFrame, spec: ScaleSpec) -> dict[str, Any]:
    df = df.sort_values("time").reset_index(drop=True)
    c, o, h, l = (df[x].to_numpy(float) for x in ("close", "open", "high", "low"))
    v = df["volume"].to_numpy(float) if "volume" in df.columns else np.ones_like(c)
    f, s, g = spec.macd
    dif = ema(c, f) - ema(c, s)
    hist = dif - ema(dif, g)
    return dict(
        t=df["time"].to_numpy(np.int64), o=o, h=h, l=l, c=c, v=v, n=len(c),
        ma=sma(c, spec.n), ma_hi=sma(c, spec.ma_hi), hist=hist,
        av=trailing_mean_excl(v, spec.av_win), atr=atr14(h, l, c),
    )


# ---------------------------------------------------------------- 规则层
def _cross_at(c, ma, k) -> bool:
    return k >= 1 and ma[k - 1] == ma[k - 1] and c[k - 1] <= ma[k - 1] and c[k] > ma[k]


def _swing_highs(h: np.ndarray, i: int, lb: int, win: int) -> list[int]:
    sh = []
    for j in range(max(lb, i - win), i - lb):
        if h[j] == h[j - lb:j + lb + 1].max():
            sh.append(j)
    return sh


def score_at(M: dict[str, Any], i: int, spec: ScaleSpec) -> dict[str, Any]:
    c, o, h, v = M["c"], M["o"], M["h"], M["v"]
    ma, ma_hi, hist, av, atr = M["ma"], M["ma_hi"], M["hist"], M["av"], M["atr"]
    score, why = 0, []

    below, k = 0, i - 1
    while k >= 0 and c[k] < ma[k]:
        below += 1
        k -= 1
    if below >= spec.bars_below:
        score += 20; why.append("盘整够久")

    sh = _swing_highs(h, i, spec.pivot_lb, spec.pivot_win)
    if len(sh) >= 2:
        j1, j2 = sh[-2], sh[-1]
        if j2 - j1 >= spec.pivot_gap:
            line = h[j2] + (h[j2] - h[j1]) / (j2 - j1) * (i - j2)
            if c[i] > line:
                score += 20; why.append("破下跌趋势线")

    m = spec.trig_bars
    o_trig = o[i - m + 1]
    if spec.amp_mode == "pct":
        body = (c[i] - o_trig) / o_trig
        body_ok = body >= 0.02
    else:
        body = (c[i] - o_trig) / (atr[i] * np.sqrt(m)) if atr[i] == atr[i] and atr[i] > 0 else 0.0
        body_ok = body >= 0.45
    if body_ok:
        score += 15; why.append("实体达标")

    vr = float(v[i - m + 1:i + 1].mean() / av[i]) if av[i] == av[i] and av[i] else 0.0
    if vr >= 1.0:
        score += 15; why.append("放量")

    lag = spec.hist_lag
    if i > lag and hist[i] == hist[i] and hist[i - lag] == hist[i - lag] and hist[i] > hist[i - lag]:
        score += 10; why.append("MACD转强")

    if ma_hi[i] == ma_hi[i] and c[i] > ma_hi[i]:
        score += 10; why.append(f"站上MA{spec.ma_hi}")

    d = c[i] / ma[i] - 1
    if spec.amp_mode == "pct":
        near = 0.01 <= d <= 0.03
    else:
        d_atr = (c[i] - ma[i]) / atr[i] if atr[i] == atr[i] and atr[i] > 0 else 0.0
        near = 0.20 <= d_atr <= 0.85
    if near:
        score += 10; why.append("贴近均线")
    return dict(score=score, why=why, below=below, body=float(body), vr=vr, d=float(d))


def raw_signals(M: dict[str, Any], spec: ScaleSpec, min_score: int = 30) -> list[dict[str, Any]]:
    c, o, ma, atr = M["c"], M["o"], M["ma"], M["atr"]
    rb, m = spec.reclaim, spec.trig_bars
    out = []
    for i in range(max(spec.warmup, m), M["n"]):
        if ma[i] != ma[i] or ma[i - 1] != ma[i - 1] or c[i] <= ma[i]:
            continue
        if not any(_cross_at(c, ma, k) for k in range(i - rb + 1, i + 1)):
            continue
        if c[i] <= o[i - m + 1]:
            continue
        if spec.amp_mode == "pct":
            d = c[i] / ma[i] - 1
            if d > 0.10 or d < 0.0:
                continue
        else:
            if not (atr[i] == atr[i] and atr[i] > 0) or (c[i] - ma[i]) / atr[i] > 2.4:
                continue
        r = score_at(M, i, spec)
        if r["score"] < min_score:
            continue
        r["i"] = i
        r["grade"] = "A" if r["score"] >= 60 else "B" if r["score"] >= 40 else "C"
        out.append(r)
    return out


def stop_price(M: dict[str, Any], i: int, spec: ScaleSpec) -> float:
    if spec.stop_mode == "low":
        return float(M["l"][i])
    atr = M["atr"][i]
    return float(min(M["l"][i], M["c"][i] - spec.s20 * atr * spec.amp_k))


def cooldown(M: dict[str, Any], sigs: list[dict[str, Any]], spec: ScaleSpec) -> list[dict[str, Any]]:
    """同 freeze_ai_ma20 的状态机：同次上穿锁定 + 冷却期 + 首次站上 ma_hi 豁免 + 前单止损重置豁免。"""
    c, l, ma, ma_hi = M["c"], M["l"], M["ma"], M["ma_hi"]
    rb, cd = spec.reclaim, spec.cooldown
    kept = []
    last_i, last_cross, last_stop, last_above = -999, -999, -1.0, False
    for s in sigs:
        i = s["i"]
        cross_k = next((k for k in range(i, i - rb, -1) if _cross_at(c, ma, k)), i)
        now_above = bool(ma_hi[i] == ma_hi[i] and c[i] > ma_hi[i])
        is_up = (not last_above) and now_above
        prev_stopped = bool(last_i >= 0 and (l[last_i + 1:i + 1] <= last_stop).any())
        drop = False
        if last_i >= 0:
            if cross_k == last_cross:
                drop = not is_up
            elif (i - last_i) < cd:
                drop = not prev_stopped and not is_up
        if not drop:
            kept.append(dict(s, cross_k=cross_k))
            last_i, last_cross, last_above = i, cross_k, now_above
            last_stop = stop_price(M, i, spec)
    return kept


# ---------------------------------------------------------------- 自动标签 / 结果
def outcome(M: dict[str, Any], i: int, spec: ScaleSpec, target_r: float = 3.0) -> dict[str, Any] | None:
    c, h, l, n = M["c"], M["h"], M["l"], M["n"]
    e, st = float(c[i]), stop_price(M, i, spec)
    r0 = e - st
    if r0 <= 0:
        return None
    hold, hz = spec.hold, spec.horizon
    end = min(n - 1, i + hold)
    if i + hz > n - 1:          # 标签视窗不完整：不参与评估（避免截尾样本被误记为负）
        return None
    seg_l = l[i + 1:end + 1]
    hit = np.flatnonzero(seg_l <= st)
    stop_bar = int(hit[0]) + 1 if len(hit) else None
    last_ok = (stop_bar - 1) if stop_bar else (end - i)
    w = min(hz, last_ok)
    mfe_h = float((h[i + 1:i + 1 + w].max() - e) / r0) if w > 0 else 0.0
    r_a = -1.0 if stop_bar else float((c[end] - e) / r0)
    return dict(
        risk_atr=r0 / M["atr"][i] if M["atr"][i] == M["atr"][i] else np.nan,
        risk_pct=r0 / e, stop_bar=stop_bar, mfe_h_r=mfe_h, y=int(mfe_h >= target_r), r_a=r_a,
        stop_le5=bool(stop_bar is not None and stop_bar <= spec.dur(5)),
        stop_le20=bool(stop_bar is not None and stop_bar <= spec.dur(20)),
        stop_hz=bool(stop_bar is not None and stop_bar <= hz),
    )


def run(df: pd.DataFrame, spec: ScaleSpec, with_outcome: bool = True) -> dict[str, Any]:
    M = prepare(df, spec)
    raw = raw_signals(M, spec)
    kept = cooldown(M, raw, spec)
    if with_outcome:
        for s in kept:
            s["out"] = outcome(M, s["i"], spec)
    return dict(M=M, raw=raw, kept=kept)


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(mid - half), float(mid + half)


def summarize(res: dict[str, Any], spec: ScaleSpec) -> dict[str, Any]:
    M, kept = res["M"], res["kept"]
    outs = [s["out"] for s in kept if s.get("out")]
    years = (M["t"][-1] - M["t"][spec.warmup]) / 86400 / 365.25 if M["n"] > spec.warmup else np.nan
    if not outs:
        return dict(raw=len(res["raw"]), kept=len(kept), labeled=0)
    arr = lambda key: np.array([o[key] for o in outs], dtype=float)  # noqa: E731
    lo, hi = wilson(arr("y").sum(), len(outs))
    return dict(
        raw=len(res["raw"]), kept=len(kept), labeled=len(outs),
        kept_per_year=round(len(kept) / years, 2),
        y_rate=round(arr("y").mean(), 3), y_ci95=(round(lo, 3), round(hi, 3)),
        stop_le5=round(arr("stop_le5").mean(), 3),
        stop_le20=round(arr("stop_le20").mean(), 3),
        stop_hz=round(arr("stop_hz").mean(), 3),
        med_risk_atr=round(float(np.nanmedian(arr("risk_atr"))), 3),
        med_risk_pct=round(float(np.median(arr("risk_pct")) * 100), 2),
        mean_r_a=round(arr("r_a").mean(), 2),
        win_a=round((arr("r_a") > 0).mean(), 3),
    )
