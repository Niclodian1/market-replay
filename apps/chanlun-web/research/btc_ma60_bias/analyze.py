#!/usr/bin/env python3
"""
BTC 历史顶/底 对 MA60(日线, 收盘) 的乖离率统计

口径:
  - 数据: data/crypto/BTC_USD_1day.parquet (2017-08-17 起)
  - MA60: 收盘价 60 日简单均线
  - 顶/底: ZigZag 算法, 以 high/low 极值定义拐点; 主口径反转阈值 30%, 大级别 50%
  - 乖离率: (价格 - MA60) / MA60, 顶部为正, 底部为负
      收盘乖离 = (当日收盘 - MA60) / MA60
      极值乖离 = (当日 high/low - MA60) / MA60

输出:
  - pivots.csv: 全部 >=30% 拐点明细
  - chart.png:  价格 + MA60 + 拐点标记 + 乖离率曲线
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]             # market-replay/
SRC = ROOT / "data/crypto/BTC_USD_1day.parquet"


def load() -> pd.DataFrame:
    df = pd.read_parquet(SRC)
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    df = df.sort_values("dt").reset_index(drop=True)
    df["ma60"] = df["close"].rolling(60).mean()
    return df


def zigzag(df: pd.DataFrame, theta: float):
    """基于 high/low 的 ZigZag 拐点检测; theta 为反转幅度阈值(比例)"""
    pivots = []
    trend = None          # 1 上升段, -1 下降段
    ext_i = 0             # 当前段的极值位置
    for i in range(1, len(df)):
        hi, lo = df.high[i], df.low[i]
        if trend is None:
            if hi >= df.high[ext_i] * (1 + theta):
                trend = 1
            elif lo <= df.low[ext_i] * (1 - theta):
                trend = -1
        if trend == 1:
            if hi >= df.high[ext_i]:
                ext_i = i
            elif lo <= df.high[ext_i] * (1 - theta):
                pivots.append((ext_i, "top"))
                trend, ext_i = -1, i
        elif trend == -1:
            if lo <= df.low[ext_i]:
                ext_i = i
            elif hi >= df.low[ext_i] * (1 + theta):
                pivots.append((ext_i, "bottom"))
                trend, ext_i = 1, i
    return pivots


def build_table(df: pd.DataFrame):
    piv30 = zigzag(df, 0.30)
    set50 = set(zigzag(df, 0.50))
    rows = []
    for idx, typ in piv30:
        r = df.iloc[idx]
        if pd.isna(r.ma60):
            continue
        px = r.high if typ == "top" else r.low
        rows.append({
            "日期": r["dt"].date().isoformat(),
            "类型": "顶" if typ == "top" else "底",
            "级别": "大" if (idx, typ) in set50 else "中",
            "极值价": px,
            "收盘": r.close,
            "MA60": r.ma60,
            "收盘乖离%": (r.close / r.ma60 - 1) * 100,
            "极值乖离%": (px / r.ma60 - 1) * 100,
            "_idx": idx,
            "_typ": typ,
        })
    return pd.DataFrame(rows), piv30


def stats(t: pd.DataFrame, level: str, typ: str):
    s = t[(t["级别"] == level) & (t["类型"] == typ)]
    out = {}
    for col in ["收盘乖离%", "极值乖离%"]:
        v = s[col]
        out[col] = dict(n=len(v), med=v.median(), mean=v.mean(),
                        lo=v.min(), hi=v.max())
    return out


def main():
    df = load()
    t, _ = build_table(df)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)

    print(f"数据范围: {df.dt.iloc[0].date()} ~ {df.dt.iloc[-1].date()}  ({len(df)} 根日线)")
    last = df.iloc[-1]
    print(f"最新: {last['dt'].date()} 收盘 {last.close:,.0f}  MA60 {last.ma60:,.0f}  "
          f"收盘乖离 {(last.close/last.ma60-1)*100:+.1f}%")
    print()

    for level, name in [("大", "大级别 (反转 >=50%)"), ("中", "中级别 (30%~50%)")]:
        for typ in ["顶", "底"]:
            st = stats(t, level, typ)
            for col in ["收盘乖离%", "极值乖离%"]:
                d = st[col]
                print(f"{name} {typ}: n={d['n']:<2} 中位 {d['med']:+7.1f}%  均值 {d['mean']:+7.1f}%  "
                      f"区间 [{d['lo']:+7.1f}%, {d['hi']:+7.1f}%]   ({col})")
        print()

    print("===== 全部拐点明细 (>=30%) =====")
    show = t[["日期", "类型", "级别", "极值价", "收盘", "MA60", "收盘乖离%", "极值乖离%"]].copy()
    show["极值价"] = show["极值价"].map(lambda x: f"{x:,.0f}")
    show["收盘"] = show["收盘"].map(lambda x: f"{x:,.0f}")
    show["MA60"] = show["MA60"].map(lambda x: f"{x:,.0f}")
    show["收盘乖离%"] = show["收盘乖离%"].map(lambda x: f"{x:+.1f}")
    show["极值乖离%"] = show["极值乖离%"].map(lambda x: f"{x:+.1f}")
    print(show.to_string(index=False))
    print()

    csv_path = HERE / "pivots.csv"
    t.drop(columns=["_idx", "_typ"]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"CSV 已保存: {csv_path}")

    # ---------------- 图 ----------------
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 9), sharex=True,
                                   gridspec_kw=dict(height_ratios=[2.2, 1]))
    ax1.plot(df.dt, df.close, color="#888", lw=0.9, label="BTC 收盘")
    ax1.plot(df.dt, df.ma60, color="#ff9800", lw=1.4, label="MA60")
    ax1.set_yscale("log")
    ax1.set_ylabel("价格 (对数轴)")
    ax1.grid(alpha=0.25)

    for _, r in t.iterrows():
        d = pd.Timestamp(r["日期"])
        color = "#e53935" if r["类型"] == "顶" else "#2e7d32"
        size = 70 if r["级别"] == "大" else 28
        marker = "v" if r["类型"] == "顶" else "^"
        ax1.scatter(d, r["极值价"], marker=marker, color=color, s=size, zorder=5,
                    edgecolors="white", linewidths=0.5)
        if r["级别"] == "大":
            ax1.annotate(f"{r['极值乖离%']:+.0f}%", (d, r["极值价"]),
                         textcoords="offset points", xytext=(0, 10 if r["类型"] == "顶" else -14),
                         ha="center", fontsize=8, color=color)
    ax1.legend(loc="upper left")

    bias = (df.close / df.ma60 - 1) * 100
    ax2.plot(df.dt, bias, color="#2962ff", lw=1.0)
    ax2.axhline(0, color="#999", lw=0.8)
    for y in (30, 50, -30, -50):
        ax2.axhline(y, color="#ccc", lw=0.6, ls="--")
    ax2.scatter(pd.to_datetime(t["日期"]), t["收盘乖离%"],
                c=["#e53935" if x == "顶" else "#2e7d32" for x in t["类型"]],
                s=[45 if x == "大" else 15 for x in t["级别"]], zorder=5)
    ax2.set_ylabel("收盘乖离率 %")
    ax2.grid(alpha=0.25)
    ax2.set_title("BTC 日线: 价格/MA60 与拐点乖离率 (红=顶 绿=底, 大点=大级别)", fontsize=10)

    fig.tight_layout()
    png_path = HERE / "chart.png"
    fig.savefig(png_path, dpi=130)
    print(f"图已保存: {png_path}")


if __name__ == "__main__":
    main()
