#!/usr/bin/env python3
"""
乖离率止盈设计研究

对比几种基于 MA20/MA60 乖离率的止盈规则 (日线 BTC, 2017-11 起):
  1) 固定阈值全清 (b20 >= 15/20/25/30)
  2) 分批止盈 (1/3 @15 + 1/3 @25 + 尾仓破 MA20)
  3) 回落确认 (b20 上穿 20 后回落跌破 15 再卖)
  4) MA60 乖离止盈 (b60 >= 30/40)
  5) 自适应分位 (b20 处于滚动 1 年 P95)
  6) 始终满仓 + 乖离止盈 + 回均值再入

统一口径: 日线收盘出信号, 次日开盘成交, 单边手续费 0.1%
入场基准: 收盘上穿 MA60 (趋势入场); 或"始终满仓"模式
输出: 终值倍数 / CAGR / 最大回撤 / Calmar / 交易数 / 平均单笔 / 持仓占比
"""
import numpy as np
import pandas as pd
from analyze import load

FEE = 0.001
pd.set_option("display.width", 200)


def prep():
    df = load()
    df["ma20"] = df.close.rolling(20).mean()
    df["ma60"] = df.close.rolling(60).mean()
    df["b20"] = (df.close / df.ma20 - 1) * 100
    df["b60"] = (df.close / df.ma60 - 1) * 100
    df["cross60"] = (df.close > df.ma60) & (df.close.shift() <= df.ma60.shift())
    df["cross20"] = (df.close > df.ma20) & (df.close.shift() <= df.ma20.shift())
    df["pct20"] = df.b20.rolling(365, min_periods=120).rank(pct=True)
    df = df[df.ma60.notna()].reset_index(drop=True)
    return df


def simulate(df, entry_rule, exit_rule, tranches=(), start_invested=False, rearm_rule=None,
             redeploy_rule=None):
    cash, units, in_pos = 1.0, 0.0, False
    state, pending = {}, None
    eq, pos = [], []
    trades = []
    entry_cash = proceeds = 0.0
    entry_dt = None
    frac, tdone = 0.0, set()

    if start_invested:
        px = df.open.iloc[0]
        units = (1 - FEE) / px
        entry_cash = 1.0
        cash = 0.0
        in_pos, frac = True, 1.0
        entry_dt = df.dt.iloc[0]
        state = {"max_b20": df.b20.iloc[0], "max_b60": df.b60.iloc[0]}

    for i in range(len(df)):
        row = df.iloc[i]
        if pending is not None:
            px = row.open
            if pending == "enter" and not in_pos:
                units = cash * (1 - FEE) / px
                entry_cash, proceeds = cash, 0.0
                cash, in_pos, frac = 0.0, True, 1.0
                tdone = set()
                entry_dt = row["dt"]
                state = {"max_b20": row.b20, "max_b60": row.b60}
            elif pending == "exit" and in_pos:
                cash += units * px * (1 - FEE)
                proceeds += units * px * (1 - FEE)
                trades.append(dict(entry=entry_dt, exit=row["dt"],
                                   ret=proceeds / entry_cash - 1,
                                   days=(row["dt"] - entry_dt).days))
                units, in_pos = 0.0, False
            elif isinstance(pending, tuple) and in_pos:
                _, k, size = pending
                sell = units * (size / frac)
                cash += sell * px * (1 - FEE)
                proceeds += sell * px * (1 - FEE)
                units -= sell
                frac -= size
                tdone.add(k)
            elif pending == "redeploy" and in_pos:
                units += cash * (1 - FEE) / px
                cash = 0.0
            pending = None

        if in_pos:
            state["max_b20"] = max(state["max_b20"], row.b20)
            state["max_b60"] = max(state["max_b60"], row.b60)
            if rearm_rule is not None and rearm_rule(row, state):
                tdone.clear()
            if exit_rule is not None and exit_rule(row, state):
                pending = "exit"
            else:
                trimmed = False
                for k, (cond, size) in enumerate(tranches):
                    if k not in tdone and cond(row, state) and frac > size + 1e-9:
                        pending = ("trim", k, size)
                        trimmed = True
                        break
                if not trimmed and redeploy_rule is not None and cash > 1e-9 \
                        and redeploy_rule(row, state):
                    pending = "redeploy"
        else:
            if entry_rule is not None and entry_rule(row, state):
                pending = "enter"

        eq.append(cash + units * row.close)
        pos.append(1 if in_pos else 0)

    return np.array(eq), np.array(pos), trades


def report(name, eq, pos, trades, dts):
    total = eq[-1] / eq[0]
    years = (dts.iloc[-1] - dts.iloc[0]).days / 365.25
    cagr = total ** (1 / years) - 1
    dd = (pd.Series(eq) / pd.Series(eq).cummax() - 1).min()
    n = len(trades)
    rets = [t["ret"] for t in trades]
    avg_ret = np.mean(rets) * 100 if n else 0.0
    avg_days = np.mean([t["days"] for t in trades]) if n else 0.0
    print(f"{name:<38} {total:>7.1f}x {cagr*100:>7.1f}% {dd*100:>8.1f}% {cagr/abs(dd):>6.2f} "
          f"{n:>4} {avg_ret:>7.1f}% {avg_days:>6.0f} {pos.mean()*100:>6.0f}%")


def main():
    df = prep()
    dts = df.dt

    # ---------- 触发质量: 首次上穿 ----------
    print("=" * 110)
    print("触发质量: b20 首次上穿阈值 (事件间隔>20天), 从当日收盘起 60 天窗口")
    print("=" * 110)
    print(f"{'阈值':<6}{'事件数':>6}{'60d中位收益':>12}{'中位最大浮盈':>12}{'中位最大回撤':>12}"
          f"{'浮盈>20%概率':>12}{'回撤<-15%概率':>12}")
    for T in (15, 20, 25, 30, 40):
        idxs, last = [], -999
        for i in df.index[df.b20 >= T]:
            if i - last > 20:
                idxs.append(i)
                last = i
        mfe_l, mae_l, f60_l = [], [], []
        for i in idxs:
            c = df.close.iloc[i]
            fw = df.iloc[i + 1: min(i + 61, len(df))]
            if len(fw) == 0:
                continue
            mfe_l.append((fw.high.max() / c - 1) * 100)
            mae_l.append((fw.low.min() / c - 1) * 100)
            j = min(i + 60, len(df) - 1)
            f60_l.append((df.close.iloc[j] / c - 1) * 100)
        print(f"{T:<6}{len(mfe_l):>6}{np.median(f60_l):>11.1f}%{np.median(mfe_l):>11.1f}%"
              f"{np.median(mae_l):>11.1f}%{np.mean(np.array(mfe_l)>20)*100:>11.0f}%"
              f"{np.mean(np.array(mae_l)<-15)*100:>11.0f}%")
    print()

    # ---------- 规则对比 ----------
    cross60 = lambda r, s: r.cross60
    cross20 = lambda r, s: r.cross20
    ma20_break = lambda r, s: r.close < r.ma20
    ma60_break = lambda r, s: r.close < r.ma60

    variants = [
        ("基准: 买入持有", None, None, (), True),
        ("趋势: 上穿MA60入/破MA20出", cross60, ma20_break, (), False),
    ]
    for T in (15, 20, 25, 30):
        variants.append((f"全清: b20>={T}", cross60, lambda r, s, T=T: r.b20 >= T, (), False))
    variants += [
        ("分批: 1/3@15+1/3@25+尾仓破MA20", cross60, ma20_break,
         ((lambda r, s: r.b20 >= 15, 1 / 3), (lambda r, s: r.b20 >= 25, 1 / 3)), False),
        ("分批: 1/3@20+1/3@30+尾仓破MA20", cross60, ma20_break,
         ((lambda r, s: r.b20 >= 20, 1 / 3), (lambda r, s: r.b20 >= 30, 1 / 3)), False),
        ("回落确认: 触20/破15再卖", cross60,
         lambda r, s: s["max_b20"] >= 20 and r.b20 < 15, (), False),
        ("全清: b60>=30", cross60, lambda r, s: r.b60 >= 30, (), False),
        ("全清: b60>=40", cross60, lambda r, s: r.b60 >= 40, (), False),
        ("分批: 1/2@b60>=30+尾仓破MA20", cross60, ma20_break,
         ((lambda r, s: r.b60 >= 30, 0.5),), False),
        ("自适应: 1/2@P95(1y)+尾仓破MA20", cross60, ma20_break,
         ((lambda r, s: r.pct20 >= 0.95, 0.5),), False),
    ]
    for T in (20, 25, 30):
        variants.append((f"满仓: b20>={T}清/回0再入", lambda r, s: r.b20 <= 0,
                         lambda r, s, T=T: r.b20 >= T, (), True))
    variants.append(("满仓: b20>=25清/破MA60止损/回20再入", cross20,
                     lambda r, s: r.b20 >= 25 or r.close < r.ma60, (), True))
    variants += [
        ("长持: 每轮b20>=20/30各减1/4(破MA60重计)", None, None,
         ((lambda r, s: r.b20 >= 20, 0.25), (lambda r, s: r.b20 >= 30, 0.25)), True,
         lambda r, s: r.close < r.ma60),
        ("长持: 每轮b20>=25减1/3(破MA60重计)", None, None,
         ((lambda r, s: r.b20 >= 25, 1 / 3),), True,
         lambda r, s: r.close < r.ma60),
        ("长持: 每轮b60>=30减1/3(破MA60重计)", None, None,
         ((lambda r, s: r.b60 >= 30, 1 / 3),), True,
         lambda r, s: r.close < r.ma60),
        ("长持: 每轮20/30减1/4 + 恐慌b20<=-20买回", None, None,
         ((lambda r, s: r.b20 >= 20, 0.25), (lambda r, s: r.b20 >= 30, 0.25)), True,
         lambda r, s: r.close < r.ma60, lambda r, s: r.b20 <= -20),
        ("长持: 每轮b20>=25减1/3 + 恐慌b20<=-15买回", None, None,
         ((lambda r, s: r.b20 >= 25, 1 / 3),), True,
         lambda r, s: r.close < r.ma60, lambda r, s: r.b20 <= -15),
    ]

    print("=" * 110)
    print("规则对比 (信号次日开盘成交, 单边手续费 0.1%)")
    print("=" * 110)
    print(f"{'规则':<38}{'终值':>8}{'CAGR':>8}{'最大回撤':>9}{'Calmar':>7}{'交易':>5}{'单笔均':>8}{'天数':>7}{'持仓%':>7}")
    for v in variants:
        name, er, xr, tr, si = v[:5]
        ra = v[5] if len(v) > 5 else None
        rd = v[6] if len(v) > 6 else None
        eq, pos, trades = simulate(df, er, xr, tr, si, ra, rd)
        report(name, eq, pos, trades, dts)

    print()
    print(f"数据范围: {dts.iloc[0].date()} ~ {dts.iloc[-1].date()}, {len(df)} 根日线")


if __name__ == "__main__":
    main()
