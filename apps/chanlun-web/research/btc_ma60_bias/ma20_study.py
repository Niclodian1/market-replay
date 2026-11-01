#!/usr/bin/env python3
"""
MA20 vs MA60 乖离率对比研究

问题: 针对 MA20 的乖离率, 在历史顶/底上表现如何? 能否提供有价值的信息?

内容:
  A. 同一批 ZigZag 拐点上的 MA20 / MA60 乖离率对比
  B. 按级别/类型的统计对比 (中位/区间/离散度)
  C. 乖离率分桶 -> 未来 30/90/180 天收益 (检验预测价值)
  D. 乖离率极值点与价格顶底点的领先/滞后
  E. 图: 价格+双均线+拐点 / 双乖离率曲线 / 乖离-未来收益散点
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

from analyze import load, zigzag

HERE = Path(__file__).resolve().parent

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


def build_pivots(df):
    piv30 = zigzag(df, 0.30)
    set50 = set(zigzag(df, 0.50))
    rows = []
    for idx, typ in piv30:
        r = df.iloc[idx]
        if pd.isna(r.ma60) or pd.isna(r.ma20):
            continue
        px = r.high if typ == "top" else r.low
        rows.append({
            "日期": r["dt"].date().isoformat(),
            "类型": "顶" if typ == "top" else "底",
            "级别": "大" if (idx, typ) in set50 else "中",
            "价格": px,
            "b20收盘": (r.close / r.ma20 - 1) * 100,
            "b20极值": (px / r.ma20 - 1) * 100,
            "b60收盘": (r.close / r.ma60 - 1) * 100,
            "b60极值": (px / r.ma60 - 1) * 100,
            "_idx": idx,
        })
    return pd.DataFrame(rows)


def group_stats(t, col):
    out = []
    for level in ["大", "中"]:
        for typ in ["顶", "底"]:
            v = t[(t["级别"] == level) & (t["类型"] == typ)][col]
            out.append(dict(级别=level, 类型=typ, n=len(v),
                            中位=v.median(), 均值=v.mean(),
                            区间=f"[{v.min():+.1f}, {v.max():+.1f}]",
                            IQR=f"{v.quantile(.75)-v.quantile(.25):.1f}",
                            std=v.std()))
    return pd.DataFrame(out)


def main():
    df = load()
    df["ma20"] = df.close.rolling(20).mean()
    t = build_pivots(df)

    print("=" * 100)
    print("A. 拐点上的 MA20 / MA60 乖离率 (极值口径, %)")
    print("=" * 100)
    show = t[["日期", "类型", "级别", "价格", "b20极值", "b60极值", "b20收盘", "b60收盘"]].copy()
    show["价格"] = show["价格"].map(lambda x: f"{x:,.0f}")
    for c in ["b20极值", "b60极值", "b20收盘", "b60收盘"]:
        show[c] = show[c].map(lambda x: f"{x:+.1f}")
    print(show.to_string(index=False))
    print()

    print("=" * 100)
    print("B. 统计对比 (极值口径)")
    print("=" * 100)
    for col, name in [("b20极值", "MA20"), ("b60极值", "MA60")]:
        st = group_stats(t, col)
        st["中位"] = st["中位"].map(lambda x: f"{x:+.1f}")
        st["均值"] = st["均值"].map(lambda x: f"{x:+.1f}")
        st["std"] = st["std"].map(lambda x: f"{x:.1f}")
        print(f"--- {name} ---")
        print(st.to_string(index=False))
        print()

    # C. 未来收益
    for h in (30, 90, 180):
        df[f"fwd{h}"] = df.close.shift(-h) / df.close - 1
    df["b20"] = (df.close / df.ma20 - 1) * 100
    df["b60"] = (df.close / df.ma60 - 1) * 100

    edges = [-np.inf, -30, -20, -10, 0, 10, 20, 30, np.inf]
    labels = ["<-30", "-30~-20", "-20~-10", "-10~0", "0~10", "10~20", "20~30", ">30"]

    print("=" * 100)
    print("C. 乖离率分桶 -> 未来收益 (全样本, 重叠窗口, 仅作相对比较)")
    print("=" * 100)
    for col, name in [("b20", "MA20"), ("b60", "MA60")]:
        df["bucket"] = pd.cut(df[col], edges, labels=labels)
        g = df.groupby("bucket", observed=True)
        out = pd.DataFrame({
            "n": g.size(),
            "未来30d中位": g["fwd30"].median() * 100,
            "未来90d中位": g["fwd90"].median() * 100,
            "未来180d中位": g["fwd180"].median() * 100,
            "90d胜率": g["fwd90"].apply(lambda s: (s > 0).mean()) * 100,
        })
        out["未来30d中位"] = out["未来30d中位"].map(lambda x: f"{x:+.1f}%")
        out["未来90d中位"] = out["未来90d中位"].map(lambda x: f"{x:+.1f}%")
        out["未来180d中位"] = out["未来180d中位"].map(lambda x: f"{x:+.1f}%")
        out["90d胜率"] = out["90d胜率"].map(lambda x: f"{x:.0f}%")
        print(f"--- {name} 乖离率桶 ---")
        print(out.to_string())
        # Spearman
        sub = df.dropna(subset=["fwd90"])
        rho = sub[[col, "fwd90"]].corr(method="spearman").iloc[0, 1]
        print(f"Spearman(乖离, 未来90d收益) = {rho:+.3f}")
        print()

    # D. 极值领先/滞后
    print("=" * 100)
    print("D. 大级别拐点: 乖离率极值日相对价格拐点日的偏移(天)  [负=领先, 正=滞后]")
    print("=" * 100)
    big = t[t["级别"] == "大"]
    rows = []
    for _, p in big.iterrows():
        idx = int(p["_idx"])
        typ = p["类型"]
        lo, hi = max(0, idx - 25), min(len(df), idx + 26)
        win = df.iloc[lo:hi]
        off20 = (win["b20"].idxmin() if typ == "底" else win["b20"].idxmax()) - idx
        off60 = (win["b60"].idxmin() if typ == "底" else win["b60"].idxmax()) - idx
        rows.append(dict(日期=p["日期"], 类型=typ, MA20偏移=off20, MA60偏移=off60))
    print(pd.DataFrame(rows).to_string(index=False))
    print()

    # 历史极值
    print("历史 b20 极值: min", f"{df.b20.min():+.1f}%", df.loc[df.b20.idxmin(), "dt"].date(),
          " / max", f"{df.b20.max():+.1f}%", df.loc[df.b20.idxmax(), "dt"].date())
    print("历史 b60 极值: min", f"{df.b60.min():+.1f}%", df.loc[df.b60.idxmin(), "dt"].date(),
          " / max", f"{df.b60.max():+.1f}%", df.loc[df.b60.idxmax(), "dt"].date())

    # ---------------- 图 ----------------
    fig = plt.figure(figsize=(15, 13))
    gs = fig.add_gridspec(3, 1, height_ratios=[1.6, 1.0, 1.0], hspace=0.28)

    ax1 = fig.add_subplot(gs[0])
    ax1.plot(df.dt, df.close, color="#888", lw=0.9, label="BTC 收盘")
    ax1.plot(df.dt, df.ma20, color="#2962ff", lw=1.1, label="MA20")
    ax1.plot(df.dt, df.ma60, color="#ff9800", lw=1.3, label="MA60")
    ax1.set_yscale("log")
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper left")
    ax1.set_ylabel("价格 (对数轴)")
    for _, r in t.iterrows():
        d = pd.Timestamp(r["日期"])
        color = "#e53935" if r["类型"] == "顶" else "#2e7d32"
        marker = "v" if r["类型"] == "顶" else "^"
        ax1.scatter(d, r["价格"], marker=marker, color=color,
                    s=60 if r["级别"] == "大" else 22, zorder=5,
                    edgecolors="white", linewidths=0.5)

    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.plot(df.dt, df.b20, color="#2962ff", lw=0.9, label="MA20 乖离")
    ax2.plot(df.dt, df.b60, color="#ff9800", lw=1.0, label="MA60 乖离")
    ax2.axhline(0, color="#999", lw=0.8)
    for y in (30, 50, -30, -50):
        ax2.axhline(y, color="#ddd", lw=0.6, ls="--")
    ax2.grid(alpha=0.25)
    ax2.legend(loc="upper left")
    ax2.set_ylabel("乖离率 %")

    ax3 = fig.add_subplot(gs[2])
    sub = df.dropna(subset=["fwd90", "b20"])
    ax3.scatter(sub.b20, sub.fwd90 * 100, s=5, alpha=0.12, color="#2962ff")
    # 分桶中位线
    q = pd.cut(sub.b20, np.arange(-80, 81, 10))
    med = sub.groupby(q, observed=True)["fwd90"].median() * 100
    centers = [iv.mid for iv in med.index]
    ax3.plot(centers, med.values, color="#e53935", lw=2, marker="o", ms=3, label="各桶中位(未来90d)")
    ax3.axhline(0, color="#999", lw=0.8)
    ax3.set_xlabel("MA20 乖离率 %")
    ax3.set_ylabel("未来90天收益 %")
    ax3.set_ylim(-70, 150)
    ax3.grid(alpha=0.25)
    ax3.legend(loc="upper left")

    fig.suptitle("BTC 日线: MA20 乖离率研究 (上: 价格/双均线/拐点, 中: 双乖离率, 下: MA20乖离 vs 未来90天收益)", fontsize=11)
    png = HERE / "ma20_study.png"
    fig.savefig(png, dpi=130, bbox_inches="tight")
    print(f"\n图已保存: {png}")

    t.drop(columns=["_idx"]).to_csv(HERE / "pivots_ma20_vs_ma60.csv", index=False, encoding="utf-8-sig")
    print(f"CSV 已保存: {HERE / 'pivots_ma20_vs_ma60.csv'}")


if __name__ == "__main__":
    main()
