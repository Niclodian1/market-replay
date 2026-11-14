#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 DeepSeek 分享对话里给出的「T1-T5 触发器 + 打分」规则量化到真实行情上。

来源：https://chat.deepseek.com/share/vit8ujtxbjbtimbk3h
原对话只有标注 JSON、没有 K 线，所以它只给了规则和风险分桶，无法回测。本脚本补上：
  1) 复核它报的每一条样本统计（riskPct 分桶、closePos/body/volRatio/d20/d60 占比）；
  2) 把 T1-T5 逐条落成可执行条件，统计每个触发器在 BTC 日线上命中多少根；
  3) 按它给的权重打分，统计 A/B 分级各自出多少信号、覆盖多少个手工买点；
  4) 用 entry=收盘 / stop=最低 跑前向统计（多少根止损、MFE、R）。

用法：
    python3 scripts/analysis/ds_rules.py
    python3 scripts/analysis/ds_rules.py --md   # 输出 markdown 表
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/analysis"))
import mybuy_rules as mb  # noqa: E402

ANN = ROOT / "annotations-BTC-1D-manual.json"
HOLD_MAX = 120


# ------------------------------------------------------------ 触发器
def t1_ma20_break(M, i) -> bool:
    """T1 MA20 突破/收回：收盘站上，且前一根在下方 / 当根下探后收回 / 近 3 根内跌破过。"""
    c, l, o, m20 = M["c"], M["l"], M["o"], M["m20"]
    if not (c[i] > m20[i]):
        return False
    rng = max(M["h"][i] - l[i], 1e-12)
    close_pos = (c[i] - l[i]) / rng
    body = (c[i] - o[i]) / o[i]
    if close_pos < 0.65 or body < 0.005:
        return False
    crossed = c[i - 1] <= m20[i - 1] or (l[i] < m20[i] < c[i])
    if not crossed:
        crossed = any(c[k] < m20[k] for k in range(max(0, i - 3), i))
    return bool(crossed)


def t2_range_break(M, i, spans=range(10, 31), atr_mult=3.0) -> bool:
    """T2 盘整收敛后突破：10-30 根里存在一段振幅 <= atr_mult*ATR20，当根站上 MA20 且收盘位置 >=0.7。"""
    h, l, c, o, m20, atr = M["h"], M["l"], M["c"], M["o"], M["m20"], M["atr"]
    if i < 30 or np.isnan(atr[i]):
        return False
    rng = max(h[i] - l[i], 1e-12)
    if not (c[i] > m20[i] and (c[i] - l[i]) / rng >= 0.70):
        return False
    cap = atr_mult * atr[i]
    return any((h[i - sp:i].max() - l[i - sp:i].min()) <= cap for sp in spans)


def t3_dual_ma(M, i, look=3) -> bool:
    """T3 双均线：当根同时站上 MA20/MA60，且近 3 根里还有一根收在 MA20 或 MA60 下方。"""
    c, m20, m60 = M["c"], M["m20"], M["m60"]
    if not (c[i] > m20[i] and c[i] > m60[i]):
        return False
    return any(c[k] < m20[k] or c[k] < m60[k] for k in range(max(1, i - look), i))


def t4_trendline(M, i, lb=3, gap_min=5, gap_max=30) -> bool:
    """T4 下降趋势线突破：最近两个下降摆动高点连线，当根收盘站上线且站上 MA20。"""
    h, c, m20 = M["h"], M["c"], M["m20"]
    if c[i] <= m20[i]:
        return False
    sh = mb.swing_highs(h, i, lb=lb, win=90)
    if len(sh) < 2:
        return False
    h2 = sh[-1]
    for h1 in reversed(sh[:-1]):
        if h[h1] <= h[h2]:
            continue
        gap = h2 - h1
        if gap < gap_min or gap > gap_max:
            continue
        slope = (h[h2] - h[h1]) / gap
        line = h[h2] + slope * (i - h2)
        line_prev = h[h2] + slope * (i - 1 - h2)
        # 必须是「刚穿过去」，不是一直在线上面
        return bool(c[i] > line and c[i - 1] <= line_prev)
    return False


def t5_macd_div(M, i, win=60) -> bool:
    """T5 MACD 背离：近 60 根内的低点价格更高? 否——价格新低、柱体低点抬高，且有确认。"""
    l, c, o, hist = M["l"], M["c"], M["o"], M["hist"]
    lo = max(1, i - win)
    ref = lo + int(np.argmin(l[lo:i - 3])) if i - 3 > lo else lo
    if not (l[i] < l[ref]):
        return False
    if not (hist[i] > hist[ref] or M["dif"][i] > M["dif"][ref]):   # 价格新低而柱体/DIF 抬高 = 背离
        return False
    rng = max(M["h"][i] - l[i], 1e-12)
    close_pos = (c[i] - l[i]) / rng
    if c[i] > M["m20"][i]:
        return True                     # 背离 + 站上 MA20 即为确认
    return bool(close_pos >= 0.60 and c[i] > o[i])


# ------------------------------------------------------------ 打分
def score_at(M, i) -> dict:
    c, o, v, d = M["c"], M["o"], M["v"], M["dif"]
    m20, m60, m120, av20 = M["m20"], M["m60"], M["m120"], M["av20"]
    rng = max(M["h"][i] - M["l"][i], 1e-12)
    close_pos = (c[i] - M["l"][i]) / rng
    body = (c[i] - o[i]) / o[i]
    vr = v[i] / av20[i] if av20[i] else 0.0
    d20 = c[i] / m20[i] - 1
    d60 = c[i] / m60[i] - 1 if m60[i] else 0.0
    d120 = c[i] / m120[i] - 1 if m120[i] else 0.0
    s120 = (m120[i] - m120[i - 20]) / 20 / m120[i - 20] * 100 if i >= 20 and m120[i - 20] else 0.0
    risk = (c[i] - M["l"][i]) / c[i]

    T1, T2, T3 = t1_ma20_break(M, i), t2_range_break(M, i), t3_dual_ma(M, i)
    T4, T5 = t4_trendline(M, i), t5_macd_div(M, i)

    s, why = 0, []
    if T1: s += 2; why.append("T1+2")
    if T2: s += 1; why.append("T2+1")
    if T3: s += 1; why.append("T3+1")
    if T4: s += 1; why.append("T4+1")
    if T5: s += 1; why.append("T5+1")
    if close_pos >= 0.70: s += 1; why.append("收盘位置+1")
    if body >= 0.01: s += 1; why.append("实体+1")
    if vr >= 1.0: s += 1; why.append("放量+1")
    if risk <= 0.06: s += 1; why.append("低风险+1")
    if d60 < 0 or d120 < 0: s += 1; why.append("下跌修复+1")
    high_pos = d120 > 0.30 and s120 > 0.80
    if high_pos: s -= 2; why.append("高位-2")
    if risk > 0.08: s -= 2; why.append("高风险-2")

    base = T1 or T3 or T4 or (T5 and c[i] > o[i])
    return dict(i=i, score=s, base=base, T1=T1, T2=T2, T3=T3, T4=T4, T5=T5,
                close_pos=close_pos, body=body, vr=vr, d20=d20, d60=d60, d120=d120,
                s120=s120, risk=risk, high_pos=high_pos, why="·".join(why))


# ------------------------------------------------------------ 前向
def forward(M, i, entry, stop):
    c, l, h = M["c"], M["l"], M["h"]
    r = entry - stop
    stop_bar = None
    mfe, mae = 0.0, 0.0
    end = min(len(c) - 1, i + HOLD_MAX)
    for k in range(i + 1, end + 1):
        mfe = max(mfe, h[k] - entry)
        mae = min(mae, l[k] - entry)
        if l[k] <= stop:
            stop_bar = k - i
            break
    # 实际 R：被止损就是 -1，否则按 120 根收盘算
    real_r = -1.0 if stop_bar is not None else ((c[end] - entry) / r if r else 0.0)
    return dict(stop_bar=stop_bar, mfe=mfe / r if r else 0.0, mae=mae / r if r else 0.0,
                r=real_r, bars=end - i)


# ------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    M = mb.load_market()
    pts = mb.load_points(M)
    mine = [p["i"] for p in pts]
    n = M["n"]
    print(f"行情 {n} 根  {mb.datetime.utcfromtimestamp(int(M['t'][0])).date()} ~ "
          f"{mb.datetime.utcfromtimestamp(int(M['t'][-1])).date()}  |  手工买点 {len(mine)}")

    # ---- 1) 复核对话里报的样本统计 ----
    ann = json.loads(ANN.read_text())["items"]
    F = [x["feature"] for x in ann]
    risk = np.array([x["riskPct"] for x in ann])
    def pct(mask): return f"{mask.sum()}/{len(risk)} ({mask.mean()*100:.1f}%)"
    print("\n【1】对话里的样本统计复核")
    print("  risk<=6%      ", pct(risk <= 0.06), "| 对话说 58/72 80.6%")
    print("  risk<=8%      ", pct(risk <= 0.08), "| 对话说 62/72 86.1%")
    print("  closePos>=0.7 ", pct(np.array([f["closePos"] for f in F]) >= 0.70), "| 对话说 65/72 90.3%")
    print("  body>=0.01    ", pct(np.array([f["body"] for f in F]) >= 0.010), "| 对话说 69/72 95.8%")
    print("  volRatio>=1   ", pct(np.array([f["volRatio20"] for f in F]) >= 1.0), "| 对话说 43/72 59.7%")
    print("  volRatio>=0.7 ", pct(np.array([f["volRatio20"] for f in F]) >= 0.7), "| 对话说 64/72 88.9%")
    print("  d20>0         ", pct(np.array([f["d20"] for f in F]) > 0), "| 对话说 70/72 97.2%")
    print("  d60<0         ", pct(np.array([f["d60"] for f in F]) < 0), "| 对话说 40/72 55.6%")

    # ---- 2) 触发器在全历史 / 在手工买点上的命中 ----
    rows = []
    for i in range(121, n):
        if np.isnan(M["m20"][i]) or np.isnan(M["m120"][i]) or np.isnan(M["atr"][i]):
            continue
        rows.append(score_at(M, i))
    print(f"\n【2】T1-T5 命中（全历史 {len(rows)} 根可评估）")
    for k in ("T1", "T2", "T3", "T4", "T5"):
        cnt = sum(1 for r in rows if r[k])
        on_pts = sum(1 for p in pts if rows[p["i"] - 121][k] if 0 <= p["i"] - 121 < len(rows))
        print(f"  {k}: 全历史 {cnt:>4} 根（{cnt/len(rows)*100:>4.1f}%）| 你的 72 个买点里满足 {on_pts}")

    # ---- 3) 打分 + 入场 ----
    sig = [r for r in rows if r["base"] and r["score"] >= 4 and r["risk"] <= 0.08 and not r["high_pos"]]
    A = [r for r in sig if r["score"] >= 6]
    B = [r for r in sig if r["score"] < 6]
    def cov(ss):
        idx = [r["i"] for r in ss]
        exact = sum(1 for p in mine if p in idx)
        within1 = sum(1 for p in mine if any(abs(p - j) <= 1 for j in idx))
        within2 = sum(1 for p in mine if any(abs(p - j) <= 2 for j in idx))
        return exact, within1, within2
    print(f"\n【3】最终信号 {len(sig)} 个（A级 {len(A)} / B级 {len(B)}）")
    for name, ss in (("全部", sig), ("A级", A), ("B级", B)):
        e, w1, w2 = cov(ss)
        print(f"  {name:<4} 信号 {len(ss):>4} | 覆盖手工买点 精确 {e:>2}/72  ±1 {w1:>2}  ±2 {w2:>2}"
              f" ({w2/72*100:.1f}%) | 命中率 {w2/len(ss)*100 if ss else 0:.1f}%")

    # ---- 4) 前向统计 ----
    print("\n【4】前向统计（entry=收盘, stop=最低, 最长 120 根）")
    for name, ss in (("全部", sig), ("A级", A), ("B级", B)):
        if not ss:
            continue
        fw = [forward(M, r["i"], M["c"][r["i"]], M["l"][r["i"]]) for r in ss]
        sb = [f["stop_bar"] for f in fw if f["stop_bar"] is not None]
        mfe = np.array([f["mfe"] for f in fw])
        r120 = np.array([f["r"] for f in fw])
        print(f"  {name:<4} n={len(ss):>3} | 止损中位 {np.median(sb) if sb else 0:>5.0f} 根"
              f" | 止损率 {len(sb)/len(ss)*100:>5.1f}%"
              f" | MFE中位 {np.median(mfe):>5.2f}R"
              f" | 120根R中位 {np.median(r120):>6.2f}R 均值 {r120.mean():>6.2f}R"
              f" | ≥1R {(r120>=1).mean()*100:>4.1f}% ≥2R {(r120>=2).mean()*100:>4.1f}%")

    # ---- 4b) 止损口径对照 ----
    print("\n【4b】把止损从「当根最低」换成 ATR 缓冲后")
    for label, fn_stop in (
        ("最低价（原规则）", lambda r: M["l"][r["i"]]),
        ("最低 − 0.2×ATR", lambda r: M["l"][r["i"]] - 0.2 * M["atr"][r["i"]]),
        ("最低 − 0.5×ATR", lambda r: M["l"][r["i"]] - 0.5 * M["atr"][r["i"]]),
        ("最低 − 1.0×ATR", lambda r: M["l"][r["i"]] - 1.0 * M["atr"][r["i"]]),
    ):
        fw = [forward(M, r["i"], M["c"][r["i"]], fn_stop(r)) for r in sig]
        sb = [f["stop_bar"] for f in fw if f["stop_bar"] is not None]
        rr = np.array([f["r"] for f in fw])
        print(f"  {label:<16} 止损率 {len(sb)/len(sig)*100:>5.1f}% | 止损中位 {(np.median(sb) if sb else 0):>4.0f} 根"
              f" | 合计 {rr.sum():>7.0f}R | 中位 {np.median(rr):>5.2f}R | 均值 {rr.mean():>5.2f}R")

    # ---- 5) R 集中度 ----
    fw = [forward(M, r["i"], M["c"][r["i"]], M["l"][r["i"]]) for r in sig]
    rr = np.sort(np.array([f["r"] for f in fw]))[::-1]
    tot = rr.sum()
    print(f"\n【4c】R 集中度：合计 {tot:.0f}R | 前5笔占 {rr[:5].sum()/tot*100:.0f}%"
          f" | 去掉前5笔 {rr[5:].sum():.0f}R | 亏损笔数 {(rr<0).sum()}")

    # ---- 4d) 分数是否有区分度 ----
    print("\n【4d】按分数看命中率（分数越高是否越准）")
    print("  分数  信号  命中  命中率   止损率  均值R")
    for sc in sorted({r["score"] for r in sig}):
        grp = [r for r in sig if r["score"] == sc]
        hit = sum(1 for r in grp if any(abs(r["i"] - p) <= 2 for p in mine))
        fw = [forward(M, r["i"], M["c"][r["i"]], M["l"][r["i"]]) for r in grp]
        st = sum(1 for f in fw if f["stop_bar"] is not None) / len(grp)
        print(f"  {sc:>4} {len(grp):>5} {hit:>5} {hit/len(grp)*100:>6.1f}% {st*100:>7.1f}%"
              f" {np.mean([f['r'] for f in fw]):>7.2f}")

    # ---- 4e) 门槛扫描 ----
    print("\n【4e】把入场门槛从 score>=4 往上调")
    print("  门槛   信号  覆盖买点  精度   止损率  均值R  合计R")
    for th in (4, 5, 6, 7, 8, 9):
        grp = [r for r in sig if r["score"] >= th]
        if not grp:
            continue
        idx = [r["i"] for r in grp]
        cv = sum(1 for p_ in mine if any(abs(p_ - j) <= 2 for j in idx))
        fw = [forward(M, r["i"], M["c"][r["i"]], M["l"][r["i"]]) for r in grp]
        st = sum(1 for f in fw if f["stop_bar"] is not None) / len(grp)
        rr = np.array([f["r"] for f in fw])
        print(f"  >={th}  {len(grp):>4}  {cv:>3}/72 ({cv/72*100:>4.1f}%)  {cv/len(grp)*100:>5.1f}%"
              f"  {st*100:>6.1f}%  {rr.mean():>6.2f}  {rr.sum():>6.0f}R")

    # 分档
    print("\n【5】分数分布（信号内）")
    from collections import Counter
    cc = Counter(r["score"] for r in sig)
    print("  ", dict(sorted(cc.items())))

    if args.md:
        print("\n| 触发器 | 全历史命中 | 你的买点满足 |")
        print("|---|---|---|")
        for k in ("T1", "T2", "T3", "T4", "T5"):
            cnt = sum(1 for r in rows if r[k])
            on_pts = sum(1 for p in pts if rows[p["i"] - 121][k] if 0 <= p["i"] - 121 < len(rows))
            print(f"| {k} | {cnt} | {on_pts}/72 |")


if __name__ == "__main__":
    main()
