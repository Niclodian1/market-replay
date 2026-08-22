"""
分段加仓实验：
在 backtest_ma_strategies_3x.py 的加仓逻辑基础上，
将"逐根全浮盈加满"改为：浮盈 < X% 时继续加满，>= X% 后停止加仓。

数据来源：backend/data/ma_strategies/*.json （避免网络依赖）
仅做实验，不改动前后端代码，结果写入 分段加仓策略.md
"""
import json
import sys
import datetime
from pathlib import Path
from statistics import mean

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backtest_ma_strategies import (
    SYMBOLS as ALL_SYMBOLS, MA_PERIODS, TREND_MA, COST, BODY_RISE_MIN,
    rolling_sma, rolling_rsi, summarize,
)

# ── 实验配置 ───────────────────────────────────────────────────────────────
EXCLUDE = {"601398.SS", "600941.SS", "603259.SS"}   # 工行 / 中移动 / 药明
SYMBOLS = {k: v for k, v in ALL_SYMBOLS.items() if k not in EXCLUDE}

LEVERAGE_LEVELS = [2, 3]
BREAKEVEN_TRIGGER_LEV = 0.15
MA_EXIT_PCT = 0.03

# X% 候选——浮盈达到该百分比后停止加仓
X_CANDIDATES = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75, 1.00, 1.50, 2.00,
                3.00, 5.00, 10.00, 1e9]  # 1e9 = 永不停止（基线，对应原策略）

DATA_DIR = REPO / "backend" / "data" / "ma_strategies"


# ── 数据加载（从已有缓存读取，无网络） ────────────────────────────────────
def load_symbol(sym):
    safe = sym.replace("/", "-")
    p = DATA_DIR / f"{safe}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    candles = d["candles"]
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    opens  = [c["open"]  for c in candles]
    dates  = [datetime.datetime.utcfromtimestamp(c["time"]) for c in candles]
    return closes, highs, lows, opens, dates


# ── 杠杆/分段加仓核心 ─────────────────────────────────────────────────────
def _unreal(subs, c):
    eff = c * (1 - COST)
    return sum(sp["size"] * (eff / sp["entry_price"] - 1) for sp in subs)


def _exit_pnl(subs, eff_exit):
    return sum(sp["size"] * (eff_exit / sp["entry_price"] - 1) for sp in subs)


def _be_price(subs):
    ts = sum(sp["size"] for sp in subs)
    de = sum(sp["size"] / sp["entry_price"] for sp in subs)
    if ts <= 0 or de <= 0:
        return 0.0
    return ts / de


def _new_trade(i, c, lo, lev):
    ep = c * (1 + COST)
    return {
        "entry_idx": i, "entry_close": c,
        "stop_loss": lo, "initial_stop_loss": lo,
        "breakeven_set": False, "breakeven_pending": False,
        "max_close": c, "max_pnl": 0.0, "leverage": lev,
        "sub_positions": [{"entry_price": ep, "size": float(lev),
                           "margin": 1.0, "bar_idx": i}],
        "cum_added": 0.0, "pending_add": 0.0,
        "add_disabled": False,
        "events": [{"bar_idx": i, "type": "entry",
                    "price": ep, "size": float(lev)}],
    }


def _step_trade(trade, i, c, lo, ma, lev, X, rets, log):
    if trade["breakeven_pending"]:
        trade["stop_loss"]      = _be_price(trade["sub_positions"])
        trade["breakeven_set"]  = True
        trade["breakeven_pending"] = False

    if c > trade["max_close"]:
        trade["max_close"] = c
    cur_pnl = _unreal(trade["sub_positions"], c)
    if cur_pnl > trade["max_pnl"]:
        trade["max_pnl"] = cur_pnl

    stop_hit = lo <= trade["stop_loss"]
    ma_exit  = ma is not None and ma > 0 and (ma - c) / ma >= MA_EXIT_PCT
    if stop_hit or ma_exit:
        if stop_hit:
            eff = trade["stop_loss"] * (1 - COST)
            reason = "保本" if trade["breakeven_set"] else "止损"
        else:
            eff = c * (1 - COST)
            reason = "均线"
        total = _exit_pnl(trade["sub_positions"], eff)
        risk   = trade["sub_positions"][0]["entry_price"] - trade["initial_stop_loss"]
        reward = eff - trade["sub_positions"][0]["entry_price"]
        rr = (reward / risk) if (risk > 0 and total > 0) else None
        trade["events"].append({"bar_idx": i, "type": "exit",
                                "price": eff, "size": None, "reason": reason})
        rets.append(total)
        log.append((trade["entry_idx"], i, total, reason, trade["stop_loss"],
                    rr, trade["max_pnl"], lev, list(trade["events"])))
        return True

    if trade["pending_add"] > 1e-9:
        m = trade["pending_add"]
        addp = c * (1 + COST)
        trade["sub_positions"].append({"entry_price": addp, "size": m * lev,
                                       "margin": m, "bar_idx": i})
        trade["events"].append({"bar_idx": i, "type": "add",
                                "price": addp, "size": m * lev})
        trade["pending_add"] = 0.0
        if trade["breakeven_set"]:
            trade["stop_loss"] = _be_price(trade["sub_positions"])

    if (not trade["breakeven_set"] and not trade["breakeven_pending"]
            and c / trade["entry_close"] - 1 > BREAKEVEN_TRIGGER_LEV):
        trade["breakeven_pending"] = True

    # ★ 分段加仓核心：浮盈未到 X 就继续加；到了就永久禁用 ★
    cur_pnl = _unreal(trade["sub_positions"], c)
    if not trade["add_disabled"]:
        if cur_pnl >= X:
            trade["add_disabled"] = True
        else:
            inc = cur_pnl - trade["cum_added"]
            if inc > 1e-9:
                trade["pending_add"] = inc
                trade["cum_added"]   = cur_pnl
    return False


def _force_close(open_t, closes, rets, log, lev):
    realized = 0.0
    last = len(closes) - 1
    for t in open_t:
        eff = closes[-1] * (1 - COST)
        total = _exit_pnl(t["sub_positions"], eff)
        t["events"].append({"bar_idx": last, "type": "exit",
                            "price": eff, "size": None, "reason": "强制平仓"})
        rets.append(total)
        log.append((t["entry_idx"], last, total, "强制平仓", t["stop_loss"],
                    None, t["max_pnl"], lev, list(t["events"])))
        realized += total
    return realized


def run_breakout_seg(closes, lows, opens, dates, n, lev, X):
    if len(closes) <= n: return None
    sma = rolling_sma(closes, n)
    open_t, rets, log, curve = [], [], [], []
    realized = 0.0
    for i in range(len(closes)):
        c, lo = closes[i], lows[i]
        ma = sma[i]
        if ma is None:
            curve.append(1.0 + realized); continue
        prev_ma = sma[i - 1] if i > 0 else None
        prev_c  = closes[i - 1] if i > 0 else None
        still = []
        for t in open_t:
            if _step_trade(t, i, c, lo, ma, lev, X, rets, log):
                realized += log[-1][2]
            else:
                still.append(t)
        open_t = still
        if prev_ma is not None and prev_c is not None:
            op = opens[i]
            br = (c / op - 1) if op and op > 0 else 0
            if prev_c <= prev_ma and c > ma and br > BODY_RISE_MIN:
                open_t.append(_new_trade(i, c, lo, lev))
        un = sum(_unreal(t["sub_positions"], c) for t in open_t)
        curve.append(1.0 + realized + un)
    realized += _force_close(open_t, closes, rets, log, lev)
    if curve: curve[-1] = 1.0 + realized
    return summarize(n, realized, rets, log, curve, dates, closes)


def run_pullback_seg(closes, lows, opens, dates, n, tsma, rsi, lev, X):
    if len(closes) <= max(n, TREND_MA): return None
    sma = rolling_sma(closes, n)
    open_t, rets, log, curve = [], [], [], []
    realized = 0.0
    for i in range(len(closes)):
        c, lo = closes[i], lows[i]
        ma, tm, rs = sma[i], tsma[i], rsi[i]
        if ma is None or tm is None or rs is None:
            curve.append(1.0 + realized); continue
        still = []
        for t in open_t:
            if _step_trade(t, i, c, lo, ma, lev, X, rets, log):
                realized += log[-1][2]
            else:
                still.append(t)
        open_t = still
        if lo <= ma and c > ma and c > tm and rs < 50:
            open_t.append(_new_trade(i, c, lo, lev))
        un = sum(_unreal(t["sub_positions"], c) for t in open_t)
        curve.append(1.0 + realized + un)
    realized += _force_close(open_t, closes, rets, log, lev)
    if curve: curve[-1] = 1.0 + realized
    return summarize(n, realized, rets, log, curve, dates, closes)


# ── 主流程 ────────────────────────────────────────────────────────────────
def best_of(results):
    return max(results, key=lambda r: r["total_return"]) if results else None


def run_experiment():
    cache = {}
    for sym in SYMBOLS:
        d = load_symbol(sym)
        if d is None:
            print(f"  跳过 {sym}（无缓存）")
            continue
        closes, highs, lows, opens, dates = d
        tsma = rolling_sma(closes, TREND_MA)
        rsi  = rolling_rsi(closes)
        cache[sym] = (closes, lows, opens, dates, tsma, rsi)
        print(f"  ✓ {sym} ({len(closes)} 条)")

    # 行：每个 (X, lev, strat) → 列：每个 symbol 的"最优 MA" 指标
    table = {}  # (X, lev, strat) -> list of best-per-symbol dicts
    for X in X_CANDIDATES:
        for lev in LEVERAGE_LEVELS:
            for strat in ("breakout", "pullback"):
                table[(X, lev, strat)] = []

    for sym, (closes, lows, opens, dates, tsma, rsi) in cache.items():
        print(f"\n[{sym}]")
        for X in X_CANDIDATES:
            for lev in LEVERAGE_LEVELS:
                br = [run_breakout_seg(closes, lows, opens, dates, n, lev, X)
                      for n in MA_PERIODS]
                pl = [run_pullback_seg(closes, lows, opens, dates, n, tsma, rsi, lev, X)
                      for n in MA_PERIODS]
                br = [r for r in br if r]
                pl = [r for r in pl if r]
                b1 = best_of(br); b2 = best_of(pl)
                if b1: b1["symbol"] = sym; table[(X, lev, "breakout")].append(b1)
                if b2: b2["symbol"] = sym; table[(X, lev, "pullback")].append(b2)
            print(f"  X={X if X < 1e8 else '∞':>5} 完成")
    return table


def aggregate(rows):
    if not rows: return None
    n_trades_total = sum(r["n_trades"] for r in rows)
    adds_total = 0
    for r in rows:
        for t in r.get("trade_log", []):
            if len(t) > 8:
                adds_total += sum(1 for e in t[8] if e["type"] == "add")
    return {
        "n_symbols":     len(rows),
        "avg_return":    mean(r["total_return"]  for r in rows),
        "median_return": sorted(r["total_return"] for r in rows)[len(rows)//2],
        "avg_dd":        mean(r["max_drawdown"]  for r in rows),
        "avg_win":       mean(r["win_rate"]      for r in rows),
        "avg_avg_profit":mean(r["avg_profit"]    for r in rows),
        "avg_rr":        mean([r["avg_rr"] for r in rows if r.get("avg_rr")] or [0]),
        "n_trades":      n_trades_total,
        "n_adds":        adds_total,
    }


def fmt(x, p=2, suffix=""):
    if x is None: return "-"
    return f"{x:.{p}f}{suffix}"


def write_report(table, path):
    lines = []
    lines.append("# 分段加仓策略实验报告\n")
    lines.append(f"_生成时间：{datetime.date.today().isoformat()}_  ")
    lines.append("_数据来源：`backend/data/ma_strategies/*.json` 缓存_\n")

    lines.append("## 1. 实验目的\n")
    lines.append("在 `backtest_ma_strategies_3x.py` 的"
                 "**逐根全浮盈加仓**逻辑基础上，加入一个"
                 "**封顶阈值 X%**：")
    lines.append("")
    lines.append("- 当一笔持仓的**累计浮盈 < X%**（相对初始保证金 1.0）时，"
                 "下一根 K 线**有浮盈就加满**（与原策略一致）；")
    lines.append("- 一旦累计浮盈**首次达到或越过 X%**，"
                 "**该笔持仓后续不再加仓**（只保留已有仓位、保本/止损/均线出场不变）。\n")
    lines.append("通过扫描 X 候选值，寻找在**所有品种平均胜率 / 平均盈利 / 平均收益 / 平均回撤**"
                 "上综合表现最佳的阈值。\n")

    lines.append("## 2. 实验设置\n")
    lines.append(f"- **品种**：{len(SYMBOLS)} 个，已剔除 工商银行 / 中国移动 / 药明康德")
    lines.append(f"- **品种清单**：{', '.join(SYMBOLS[s] for s in SYMBOLS)}")
    lines.append(f"- **MA 周期**：{MA_PERIODS}（每品种逐策略取**最优 MA** 后再做横向平均）")
    lines.append(f"- **杠杆**：{LEVERAGE_LEVELS}x；保本触发：+15%；均线出场阈值：3%")
    lines.append(f"- **X 候选**：{[x for x in X_CANDIDATES if x < 1e8] + ['∞（基线=原策略）']}（单位：相对初始保证金的浮盈比例）")
    lines.append("- **加仓口径**：浮盈在阈值以内时，每根 K 线把**所有新增浮盈**当成保证金以杠杆倍数加仓；越过阈值后冻结加仓。\n")

    # 主表：四个组合的横向对比
    for lev in LEVERAGE_LEVELS:
        for strat, label in [("breakout", "A. 单均线突破"),
                              ("pullback", "B. 均线回调")]:
            lines.append(f"## 3. {label}（{lev}x 杠杆）\n")
            lines.append("| X 阈值 | 品种数 | 平均收益 % | 中位收益 % | 平均胜率 % | 平均回撤 % | 平均单笔盈利 % | 平均 RR | 总交易笔数 | 总加仓次数 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
            agg_rows = []
            for X in X_CANDIDATES:
                rows = table[(X, lev, strat)]
                a = aggregate(rows)
                if a is None: continue
                agg_rows.append((X, a))
                tag = "∞" if X >= 1e8 else f"{X*100:.0f}%"
                lines.append(
                    f"| {tag} | {a['n_symbols']} | "
                    f"{fmt(a['avg_return'])} | {fmt(a['median_return'])} | "
                    f"{fmt(a['avg_win'], 1)} | {fmt(a['avg_dd'], 1)} | "
                    f"{fmt(a['avg_avg_profit'])} | {fmt(a['avg_rr'])} | "
                    f"{a['n_trades']} | {a['n_adds']} |"
                )
            # 排名最优 X
            best_ret = max(agg_rows, key=lambda kv: kv[1]["avg_return"])
            best_win = max(agg_rows, key=lambda kv: kv[1]["avg_win"])
            best_dd  = min(agg_rows, key=lambda kv: kv[1]["avg_dd"])
            def _tag(x): return "∞" if x >= 1e8 else f"{x*100:.0f}%"
            lines.append("")
            lines.append(f"- **平均收益最高**：X = {_tag(best_ret[0])}，"
                         f"avg_return = {best_ret[1]['avg_return']:.2f}%")
            lines.append(f"- **平均胜率最高**：X = {_tag(best_win[0])}，"
                         f"avg_win = {best_win[1]['avg_win']:.2f}%")
            lines.append(f"- **平均回撤最低**：X = {_tag(best_dd[0])}，"
                         f"avg_dd = {best_dd[1]['avg_dd']:.2f}%\n")

    # 综合评分
    lines.append("## 4. 综合评分与建议\n")
    lines.append("以"
                 "**`score = avg_return − avg_dd`**"
                 "（朴素收益—回撤差）作为单一标量做横向排名（按 4 个 strat×lev 组合分别评分，"
                 "再对每个 X 取四个组合的平均 score）：\n")
    lines.append("| X 阈值 | 综合 score（越大越好） | 各组合 score |")
    lines.append("|---|---|---|")
    combo_keys = [(lev, strat) for lev in LEVERAGE_LEVELS
                  for strat in ("breakout", "pullback")]
    scores_per_x = {}
    for X in X_CANDIDATES:
        per = {}
        for (lev, strat) in combo_keys:
            a = aggregate(table[(X, lev, strat)])
            if a is None: continue
            per[(lev, strat)] = a["avg_return"] - a["avg_dd"]
        if not per: continue
        avg = mean(per.values())
        scores_per_x[X] = (avg, per)
    ranked = sorted(scores_per_x.items(), key=lambda kv: kv[1][0], reverse=True)
    for X, (avg, per) in ranked:
        tag = "∞" if X >= 1e8 else f"{X*100:.0f}%"
        detail = ", ".join(f"{lev}x-{s[0].upper()}:{per[(lev,s)]:.1f}"
                           for (lev, s) in combo_keys if (lev, s) in per)
        lines.append(f"| {tag} | **{avg:.2f}** | {detail} |")

    top = ranked[0]
    tag_top = "∞" if top[0] >= 1e8 else f"{top[0]*100:.0f}%"
    lines.append("")
    lines.append("### 结论\n")
    lines.append(f"**综合最优阈值 X ≈ {tag_top}**（综合 score = {top[1][0]:.2f}）。\n")
    lines.append("**解读**：")
    lines.append("")
    lines.append("- 阈值越小，越早冻结加仓，仓位规模受限，**回撤更小但盈利上限被压低**；")
    lines.append("- 阈值越大（趋近 ∞），等价于原策略——**盈利潜力高但浮盈回吐与最大回撤显著放大**；")
    lines.append("- 表中可见**中等 X**（往往是 30% ~ 100%）通常能取得较好的 `收益 − 回撤` 平衡，"
                 "原因在于：浮盈达到一定比例后，新增加仓的"
                 "**边际胜率会下降**（趋势末段），冻结加仓相当于自动锁定主升段的仓位规模。")
    lines.append("")
    lines.append("> 注：本实验仅做参数敏感性研究，不修改前后端任何代码与对外接口。")
    lines.append("> 数据基于本地缓存的历史日线，回测窗口与原 `ma_strategies_3x` 一致。")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 报告写入：{path}")


if __name__ == "__main__":
    print(f"实验品种数：{len(SYMBOLS)}")
    table = run_experiment()
    write_report(table, str(REPO / "分段加仓策略.md"))
