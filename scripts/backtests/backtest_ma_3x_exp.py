"""
3x 杠杆策略实验脚本（不改动原有仓库文件）

实验目标：
  1. 分段止盈：单根 K 线实体涨幅 >= X 时按比例 p 减仓，最多 cap 次
  2. 两阶段加仓：
       阶段一（蓄势期）：close < entry*(1+T_confirm) 时小步加仓
         add_mode=pullback_ma  → 收盘上穿 MA 且阳线时加
         add_mode=pnl_step     → 浮盈每 +step% 加
         每次加固定保证金 m_step，最多 N1 次
       阶段二（趋势期）：close >= entry*(1+T_confirm) 后停止加仓，
         启用分段止盈 + 跟踪止损（峰值回撤 r_trail 触发）

数据来源：backend/data/ma_strategies_3x/<SYMBOL>.json（已缓存，避免重新下载）
基线参数与 SYMBOLS / MA_PERIODS / 信号逻辑均直接复用 backtest_ma_strategies.py

输出：data/experiments/<ts>_<stage>.csv
"""

from __future__ import annotations

import csv
import datetime as _dt
import itertools
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))

from backtest_ma_strategies import (  # noqa: E402
    SYMBOLS, MA_PERIODS, TREND_MA, COST, BODY_RISE_MIN,
    rolling_sma, rolling_rsi,
    run_breakout, run_pullback,
)
from backtest_ma_strategies_3x import (  # noqa: E402
    run_leveraged_breakout, run_leveraged_pullback,
)


CACHE_DIR = _REPO / "backend" / "data" / "ma_strategies_3x"
OUT_DIR   = _REPO / "data" / "experiments"
MA_EXIT_PCT = 0.03


# ──────────────────────────────────────────────────────────────────────────────
# 数据加载（从缓存 JSON 读 OHLC）
# ──────────────────────────────────────────────────────────────────────────────

def load_ohlc(symbol: str):
    fp = CACHE_DIR / f"{symbol.replace('/', '-')}.json"
    if not fp.exists():
        return None
    d = json.load(open(fp))
    candles = d.get("candles") or []
    if len(candles) < max(MA_PERIODS) + 10:
        return None
    opens  = [c["open"] for c in candles]
    highs  = [c["high"] for c in candles]
    lows   = [c["low"]  for c in candles]
    closes = [c["close"] for c in candles]
    dates  = [_dt.datetime.utcfromtimestamp(c["time"]).date() for c in candles]
    return dict(opens=opens, highs=highs, lows=lows, closes=closes, dates=dates)


# ──────────────────────────────────────────────────────────────────────────────
# 实验引擎：参数化的单笔处理
# ──────────────────────────────────────────────────────────────────────────────

def _unreal(subs, c):
    eff = c * (1 - COST)
    return sum(sp["size"] * (eff / sp["entry_price"] - 1) for sp in subs)


def _exit_pnl(subs, eff_exit):
    return sum(sp["size"] * (eff_exit / sp["entry_price"] - 1) for sp in subs)


def _be_price(subs):
    tot = sum(sp["size"] for sp in subs)
    den = sum(sp["size"] / sp["entry_price"] for sp in subs)
    if tot <= 0 or den <= 0:
        return 0.0
    return tot / den


def _new_trade(i, c, lo, leverage, m_initial=1.0):
    return {
        "entry_idx":     i,
        "entry_close":   c,
        "stop_loss":     lo,
        "initial_stop":  lo,
        "be_set":        False,
        "be_pending":    False,
        "max_close":     c,
        "max_pnl":       0.0,
        "leverage":      leverage,
        "subs": [{
            "entry_price": c * (1 + COST),
            "size":        m_initial * leverage,
            "margin":      m_initial,
            "bar_idx":     i,
        }],
        "pending_add":   0.0,
        "n_adds_phase1": 0,
        "phase2":        False,
        "last_pnl_add":  0.0,   # for pnl_step add_mode
        "tp_done":       0,     # partial-TP count
        "trail_peak":    c,
        "cum_pnl_added": 0.0,   # legacy add 模式的浮盈高水位
    }


def _process(trade, i, c, op, lo, hi, ma, prev_c, prev_ma, leverage,
             trade_rets, trade_log, params):
    """返回 True 表示已平仓。"""

    # 1) 激活上一棒的保本
    if trade["be_pending"]:
        trade["stop_loss"] = _be_price(trade["subs"])
        trade["be_set"]    = True
        trade["be_pending"] = False

    # 2) 更新极值
    if c > trade["max_close"]:
        trade["max_close"] = c
    if c > trade["trail_peak"]:
        trade["trail_peak"] = c
    cur_pnl = _unreal(trade["subs"], c)
    if cur_pnl > trade["max_pnl"]:
        trade["max_pnl"] = cur_pnl

    # 阶段判定
    if not trade["phase2"] and c >= trade["entry_close"] * (1 + params["T_confirm"]):
        trade["phase2"] = True

    # 3) 出场（止损/MA/跟踪止损）
    stop_hit  = lo <= trade["stop_loss"]
    ma_exit   = (ma is not None and ma > 0 and (ma - c) / ma >= MA_EXIT_PCT)
    trail_hit = False
    if params.get("r_trail") and trade["phase2"]:
        trail_px = trade["trail_peak"] * (1 - params["r_trail"])
        if lo <= trail_px and trail_px > trade["stop_loss"]:
            stop_hit = True
            trade["stop_loss"] = trail_px
            trail_hit = True

    if stop_hit or ma_exit:
        if stop_hit:
            eff_exit = trade["stop_loss"] * (1 - COST)
            reason   = ("跟踪" if trail_hit else ("保本" if trade["be_set"] else "止损"))
        else:
            eff_exit = c * (1 - COST)
            reason   = "均线"
        total = _exit_pnl(trade["subs"], eff_exit)
        risk   = trade["subs"][0]["entry_price"] - trade["initial_stop"]
        reward = eff_exit - trade["subs"][0]["entry_price"]
        rr     = (reward / risk) if (risk > 0 and total > 0) else None
        trade_rets.append(total)
        trade_log.append((trade["entry_idx"], i, total, reason,
                          trade["stop_loss"], rr, trade["max_pnl"], leverage))
        return True

    # 4) 执行上一棒安排的加仓
    if trade["pending_add"] > 1e-9:
        m = trade["pending_add"]
        trade["subs"].append({
            "entry_price": c * (1 + COST),
            "size":        m * leverage,
            "margin":      m,
            "bar_idx":     i,
        })
        trade["pending_add"] = 0.0
        if trade["be_set"]:
            trade["stop_loss"] = _be_price(trade["subs"])

    # 5) 分段止盈
    # tp_target: "all" (默认，按比例缩所有子仓) | "adds_only"（只平加仓，底仓保留）
    # adds_only 模式下不需要 phase2 限制——只要存在加仓就允许触发
    tp_target = params.get("tp_target", "all")
    tp_gate = trade["phase2"] if tp_target == "all" else (len(trade["subs"]) > 1)
    if params.get("tp_X") and tp_gate and trade["tp_done"] < params.get("tp_cap", 99):
        body = (c / op - 1) if op and op > 0 else 0.0
        if body >= params["tp_X"]:
            p = params["tp_p"]
            eff_exit = c * (1 - COST)
            realized_part = 0.0
            if tp_target == "adds_only":
                # 仅缩减 sub_positions[1:]，底仓保留
                base = trade["subs"][0]
                new_subs = [base]
                for sp in trade["subs"][1:]:
                    cut = sp["size"] * p
                    realized_part += cut * (eff_exit / sp["entry_price"] - 1)
                    rem = sp["size"] - cut
                    if rem > 1e-9:
                        new_subs.append({**sp, "size": rem})
                trade["subs"] = new_subs
                if realized_part != 0.0:
                    trade_rets.append(realized_part)
                    trade["tp_done"] += 1
                    if trade["be_set"]:
                        trade["stop_loss"] = _be_price(trade["subs"])
            else:
                new_subs = []
                for sp in trade["subs"]:
                    cut = sp["size"] * p
                    realized_part += cut * (eff_exit / sp["entry_price"] - 1)
                    rem = sp["size"] - cut
                    if rem > 1e-9:
                        new_subs.append({**sp, "size": rem})
                if new_subs:
                    trade["subs"] = new_subs
                    trade_rets.append(realized_part)
                    trade["tp_done"] += 1
                    if trade["be_set"]:
                        trade["stop_loss"] = _be_price(trade["subs"])
                else:
                    total = realized_part
                    trade_rets.append(total)
                    trade_log.append((trade["entry_idx"], i, total, "分段全平",
                                      trade["stop_loss"], None, trade["max_pnl"], leverage))
                    return True

    # 6) 保本触发（用 +15% 阈值）
    if (not trade["be_set"] and not trade["be_pending"]
            and c / trade["entry_close"] - 1 > 0.15):
        trade["be_pending"] = True

    # 7a) Legacy 加仓（复刻原 3x 脚本的"浮盈增量→下一棒全投"）
    if params.get("legacy_add"):
        cur = _unreal(trade["subs"], c)
        inc = cur - trade["cum_pnl_added"]
        if inc > 1e-9:
            trade["pending_add"]   = inc
            trade["cum_pnl_added"] = cur
        return False

    # 7b) 两阶段加仓（阶段一）
    if not trade["phase2"] and trade["n_adds_phase1"] < params["N1"]:
        do_add = False
        if params["add_mode"] == "pullback_ma":
            if ma is not None and prev_ma is not None and prev_c is not None:
                if lo <= ma and c > ma and c > op:
                    do_add = True
        elif params["add_mode"] == "pnl_step":
            step = params.get("pnl_step", 0.03)
            cur = _unreal(trade["subs"], c)
            if cur - trade["last_pnl_add"] >= step * sum(sp["margin"] for sp in trade["subs"]):
                do_add = True
                trade["last_pnl_add"] = cur
        if do_add:
            trade["pending_add"] = params["m_step"]
            trade["n_adds_phase1"] += 1

    return False


def _force_close(open_trades, closes, trade_rets, trade_log, leverage):
    realized = 0.0
    last_i = len(closes) - 1
    for tr in open_trades:
        eff = closes[-1] * (1 - COST)
        tot = _exit_pnl(tr["subs"], eff)
        trade_rets.append(tot)
        trade_log.append((tr["entry_idx"], last_i, tot, "强制平仓",
                          tr["stop_loss"], None, tr["max_pnl"], leverage))
        realized += tot
    return realized


def _summarize(realized, trade_rets, trade_log, curve):
    if not trade_rets:
        return None
    n = len(trade_rets)
    wins = [r for r in trade_rets if r >= -0.003]
    peak, dd = curve[0], 0.0
    for v in curve:
        if v > peak:
            peak = v
        d = (peak - v) / peak * 100
        if d > dd:
            dd = d
    return {
        "total_return": realized * 100,
        "n_trades":     n,
        "win_rate":     len(wins) / n * 100,
        "max_drawdown": dd,
        "max_profit":   max(trade_rets) * 100,
        "avg_profit":   (sum(wins) / len(wins) * 100) if wins else 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 实验版策略 runner（突破A / 回调B）
# ──────────────────────────────────────────────────────────────────────────────

def run_exp_breakout(ohlc, n, leverage, params):
    closes, lows, opens = ohlc["closes"], ohlc["lows"], ohlc["opens"]
    if len(closes) <= n:
        return None
    sma = rolling_sma(closes, n)
    open_trades, trets, tlog, curve = [], [], [], []
    realized = 0.0
    for i in range(len(closes)):
        c, lo, op = closes[i], lows[i], opens[i]
        ma = sma[i]
        if ma is None:
            curve.append(1.0 + realized); continue
        prev_ma = sma[i-1] if i > 0 else None
        prev_c  = closes[i-1] if i > 0 else None
        still = []
        for tr in open_trades:
            closed = _process(tr, i, c, op, lo, ohlc["highs"][i],
                              ma, prev_c, prev_ma, leverage,
                              trets, tlog, params)
            if closed:
                realized = sum(trets)
            else:
                still.append(tr)
        open_trades = still
        if prev_ma is not None and prev_c is not None:
            body = (c / op - 1) if op and op > 0 else 0
            if prev_c <= prev_ma and c > ma and body > BODY_RISE_MIN:
                open_trades.append(_new_trade(i, c, lo, leverage))
        unreal = sum(_unreal(t["subs"], c) for t in open_trades)
        curve.append(1.0 + realized + unreal)
    realized += _force_close(open_trades, closes, trets, tlog, leverage)
    if curve:
        curve[-1] = 1.0 + realized
    return _summarize(realized, trets, tlog, curve)


def run_exp_pullback(ohlc, n, leverage, params, trend_sma, rsi_vals):
    closes, lows, opens = ohlc["closes"], ohlc["lows"], ohlc["opens"]
    if len(closes) <= max(n, TREND_MA):
        return None
    sma = rolling_sma(closes, n)
    open_trades, trets, tlog, curve = [], [], [], []
    realized = 0.0
    for i in range(len(closes)):
        c, lo, op = closes[i], lows[i], opens[i]
        ma, tma, rsi = sma[i], trend_sma[i], rsi_vals[i]
        if ma is None or tma is None or rsi is None:
            curve.append(1.0 + realized); continue
        prev_ma = sma[i-1] if i > 0 else None
        prev_c  = closes[i-1] if i > 0 else None
        still = []
        for tr in open_trades:
            closed = _process(tr, i, c, op, lo, ohlc["highs"][i],
                              ma, prev_c, prev_ma, leverage,
                              trets, tlog, params)
            if closed:
                realized = sum(trets)
            else:
                still.append(tr)
        open_trades = still
        if lo <= ma and c > ma and c > tma and rsi < 50:
            open_trades.append(_new_trade(i, c, lo, leverage))
        unreal = sum(_unreal(t["subs"], c) for t in open_trades)
        curve.append(1.0 + realized + unreal)
    realized += _force_close(open_trades, closes, trets, tlog, leverage)
    if curve:
        curve[-1] = 1.0 + realized
    return _summarize(realized, trets, tlog, curve)


# ──────────────────────────────────────────────────────────────────────────────
# 基线（沿用原 3x 脚本中的 leveraged 引擎）
# ──────────────────────────────────────────────────────────────────────────────

def run_baseline(ohlc, n, leverage, trend_sma, rsi_vals):
    closes, lows, opens, dates = ohlc["closes"], ohlc["lows"], ohlc["opens"], ohlc["dates"]
    b = run_leveraged_breakout(closes, lows, opens, dates, n, leverage)
    p = run_leveraged_pullback(closes, lows, opens, dates, n, trend_sma, rsi_vals, leverage)
    def pack(r):
        if not r: return None
        return {k: r[k] for k in ("total_return","n_trades","win_rate","max_drawdown","max_profit","avg_profit")}
    return pack(b), pack(p)


# ──────────────────────────────────────────────────────────────────────────────
# 网格定义
# ──────────────────────────────────────────────────────────────────────────────

# 默认参数：关闭实验功能（用于 baseline 复算 sanity check）
NOOP_PARAMS = dict(
    T_confirm=999.0,   # 永不进入阶段二
    m_step=0.0,
    N1=0,
    add_mode="pullback_ma",
    pnl_step=0.03,
    tp_X=None, tp_p=0.0, tp_cap=0, tp_target="all",
    r_trail=None,
    legacy_add=False,
)


def tp_grid():
    grid = []
    for X, p, cap in itertools.product(
        [0.05, 0.07, 0.10, 0.13, 0.15, 0.20],
        [0.25, 0.33, 0.50],
        [1, 2, 99],
    ):
        grid.append(dict(NOOP_PARAMS, T_confirm=0.0,  # 立即阶段二，只测分段止盈
                         tp_X=X, tp_p=p, tp_cap=cap, legacy_add=True))
    return grid


def addcut_grid():
    """Stage 4：只在加速上涨时平掉【加仓】仓位，底仓保留。
    两组 base：
      - legacy: 原 3x 引擎的浮盈增量加仓
      - phased: Stage 2 最优——pnl_step 两阶段加仓
    """
    bases = [
        ("legacy", dict(legacy_add=True, T_confirm=0.0, N1=0, m_step=0.0)),
        ("phased", dict(legacy_add=False, T_confirm=0.15, N1=3, m_step=1.0,
                        add_mode="pnl_step", pnl_step=0.03)),
    ]
    grid = []
    # 对照：每个 base 跑一行无 TP 的（base-only）
    for base_name, base in bases:
        grid.append(dict(NOOP_PARAMS, **base,
                         _base=base_name, tp_X=None))
    # 主网格
    for base_name, base in bases:
        for X, p, cap in itertools.product(
            [0.05, 0.07, 0.10, 0.13, 0.15, 0.20],
            [0.5, 1.0],
            [1, 2, 99],
        ):
            grid.append(dict(NOOP_PARAMS, **base,
                             _base=base_name,
                             tp_X=X, tp_p=p, tp_cap=cap,
                             tp_target="adds_only"))
    return grid


def combo_grid():
    """Stage 3: top 加仓 × top TP 组合（突破/回调通用）。"""
    top_adds = [
        dict(T_confirm=0.15, m_step=1.0, N1=3, add_mode="pnl_step", r_trail=None),
        dict(T_confirm=0.12, m_step=1.0, N1=3, add_mode="pnl_step", r_trail=None),
        dict(T_confirm=0.15, m_step=1.0, N1=2, add_mode="pnl_step", r_trail=None),
    ]
    top_tps = [
        dict(tp_X=0.05, tp_p=0.33, tp_cap=99),
        dict(tp_X=0.07, tp_p=0.25, tp_cap=99),
        dict(tp_X=0.10, tp_p=0.50, tp_cap=99),
    ]
    grid = []
    # 纯 add 基准（无 TP）作为对照
    for a in top_adds:
        grid.append(dict(NOOP_PARAMS, **a, tp_X=None))
    # combos
    for a in top_adds:
        for t in top_tps:
            grid.append(dict(NOOP_PARAMS, **a, **t))
    return grid


def add_grid():
    grid = []
    for T_confirm, m_step, N1, add_mode, r_trail in itertools.product(
        [0.08, 0.12, 0.15],
        [0.25, 0.5, 1.0],
        [2, 3],
        ["pullback_ma", "pnl_step"],
        [None, 0.08, 0.12],
    ):
        grid.append(dict(NOOP_PARAMS, T_confirm=T_confirm, m_step=m_step,
                         N1=N1, add_mode=add_mode, r_trail=r_trail))
    return grid


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────

def aggregate(rows_per_symbol):
    """rows_per_symbol: list of dict with metrics. 返回 cross-symbol 中位/均值。"""
    if not rows_per_symbol:
        return None
    keys = ("total_return","win_rate","max_drawdown","n_trades")
    out = {}
    for k in keys:
        vals = [r[k] for r in rows_per_symbol if r is not None]
        if not vals:
            out[k+"_mean"] = None; out[k+"_median"] = None; continue
        vals.sort()
        out[k+"_mean"]   = sum(vals)/len(vals)
        out[k+"_median"] = vals[len(vals)//2]
    out["n_symbols"] = len([r for r in rows_per_symbol if r is not None])
    return out


def run_grid(stage_name, grid, symbols, mas, ohlc_cache, trend_cache, rsi_cache,
             baseline_cache, leverage=3, strategies=("breakout","pullback"),
             write_pos_rate=True):
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_fp = OUT_DIR / f"{ts}_{stage_name}.csv"
    fields = ["stage","strategy","params_id","params",
              "n_symbols","total_return_mean","total_return_median",
              "win_rate_mean","win_rate_median",
              "max_drawdown_mean","max_drawdown_median",
              "n_trades_mean","n_trades_median",
              "delta_ret_mean","delta_ret_median",
              "delta_winrate_mean","delta_dd_mean",
              "pos_ret_share"]
    rows_out = []
    print(f"\n[{stage_name}] {len(grid)} params × {len(symbols)} symbols × {len(mas)} MAs × {len(strategies)} strats")
    for pid, params in enumerate(grid):
        for strat in strategies:
            # 对每个 symbol 取该 strat 在最佳 MA 上的回报，做 cross-symbol 汇总
            sym_rows = []
            sym_baseline = []
            for sym in symbols:
                ohlc = ohlc_cache[sym]
                tsma = trend_cache[sym]
                rsi  = rsi_cache[sym]
                # 取所有 MA 中最好的（按 total_return）
                best_exp = None
                best_base = None
                for n in mas:
                    if strat == "breakout":
                        r = run_exp_breakout(ohlc, n, leverage, params)
                        b = baseline_cache[sym]["breakout"].get(n)
                    else:
                        r = run_exp_pullback(ohlc, n, leverage, params, tsma, rsi)
                        b = baseline_cache[sym]["pullback"].get(n)
                    if r and (best_exp is None or r["total_return"] > best_exp["total_return"]):
                        best_exp = r
                    if b and (best_base is None or b["total_return"] > best_base["total_return"]):
                        best_base = b
                if best_exp:
                    sym_rows.append(best_exp)
                    sym_baseline.append(best_base)
            agg = aggregate(sym_rows)
            if not agg:
                continue
            deltas = []
            d_wr   = []
            d_dd   = []
            pos = 0
            for r, b in zip(sym_rows, sym_baseline):
                if b is None: continue
                d = r["total_return"] - b["total_return"]
                deltas.append(d)
                d_wr.append(r["win_rate"] - b["win_rate"])
                d_dd.append(r["max_drawdown"] - b["max_drawdown"])
                if d > 0: pos += 1
            dr_mean = sum(deltas)/len(deltas) if deltas else None
            deltas_sorted = sorted(deltas)
            dr_med  = deltas_sorted[len(deltas)//2] if deltas else None
            wr_mean = sum(d_wr)/len(d_wr) if d_wr else None
            dd_mean = sum(d_dd)/len(d_dd) if d_dd else None
            pos_share = pos / len(deltas) if deltas else None
            rows_out.append({
                "stage": stage_name, "strategy": strat,
                "params_id": pid, "params": json.dumps(params, ensure_ascii=False),
                **{f"{k}":agg.get(k) for k in (
                    "n_symbols","total_return_mean","total_return_median",
                    "win_rate_mean","win_rate_median",
                    "max_drawdown_mean","max_drawdown_median",
                    "n_trades_mean","n_trades_median")},
                "delta_ret_mean": dr_mean, "delta_ret_median": dr_med,
                "delta_winrate_mean": wr_mean, "delta_dd_mean": dd_mean,
                "pos_ret_share": pos_share,
            })
        # 进度
        if (pid+1) % 5 == 0 or pid == len(grid)-1:
            print(f"  [{stage_name}] {pid+1}/{len(grid)} done")
    with open(out_fp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print(f"✅ wrote {out_fp}  ({len(rows_out)} rows)")
    return out_fp, rows_out


def precompute_baseline(ohlc_cache, mas, leverage):
    """对每个 symbol × MA 跑一次 baseline，缓存结果。"""
    base = {}
    for sym, ohlc in ohlc_cache.items():
        tsma = rolling_sma(ohlc["closes"], TREND_MA)
        rsi  = rolling_rsi(ohlc["closes"])
        base[sym] = {"breakout": {}, "pullback": {}, "trend_sma": tsma, "rsi": rsi}
        closes, lows, opens, dates = ohlc["closes"], ohlc["lows"], ohlc["opens"], ohlc["dates"]
        for n in mas:
            b = run_leveraged_breakout(closes, lows, opens, dates, n, leverage)
            p = run_leveraged_pullback(closes, lows, opens, dates, n, tsma, rsi, leverage)
            if b: base[sym]["breakout"][n] = {k:b[k] for k in ("total_return","n_trades","win_rate","max_drawdown")}
            if p: base[sym]["pullback"][n] = {k:p[k] for k in ("total_return","n_trades","win_rate","max_drawdown")}
        print(f"  baseline cached: {sym}")
    return base


def main(stage="tp", symbols=None, mas=None, quick=False):
    syms = symbols or list(SYMBOLS.keys())
    mas  = mas or MA_PERIODS
    if quick:
        syms = ["BTC-USD","ETH-USD","NVDA","AAPL","TSLA","0700.HK","600519.SS"]
        mas  = [20, 30, 60, 120]

    ohlc_cache = {}
    trend_cache = {}
    rsi_cache = {}
    for s in syms:
        d = load_ohlc(s)
        if d is None:
            print(f"  skip {s}: no cache")
            continue
        ohlc_cache[s] = d
        trend_cache[s] = rolling_sma(d["closes"], TREND_MA)
        rsi_cache[s]   = rolling_rsi(d["closes"])
    syms = list(ohlc_cache.keys())
    print(f"loaded {len(syms)} symbols, mas={mas}")

    print("\n== precompute baseline ==")
    baseline_cache = {}
    for sym in syms:
        ohlc = ohlc_cache[sym]
        tsma, rsi = trend_cache[sym], rsi_cache[sym]
        baseline_cache[sym] = {"breakout": {}, "pullback": {}}
        for n in mas:
            b = run_leveraged_breakout(ohlc["closes"], ohlc["lows"], ohlc["opens"], ohlc["dates"], n, 3)
            p = run_leveraged_pullback(ohlc["closes"], ohlc["lows"], ohlc["opens"], ohlc["dates"], n, tsma, rsi, 3)
            if b: baseline_cache[sym]["breakout"][n] = {k:b[k] for k in ("total_return","n_trades","win_rate","max_drawdown")}
            if p: baseline_cache[sym]["pullback"][n] = {k:p[k] for k in ("total_return","n_trades","win_rate","max_drawdown")}
        print(f"  baseline: {sym}")

    if stage in ("tp", "all"):
        run_grid("TP", tp_grid(), syms, mas, ohlc_cache, trend_cache, rsi_cache,
                 baseline_cache)
    if stage in ("add", "all"):
        run_grid("ADD", add_grid(), syms, mas, ohlc_cache, trend_cache, rsi_cache,
                 baseline_cache)
    if stage in ("combo", "all"):
        run_grid("COMBO", combo_grid(), syms, mas, ohlc_cache, trend_cache, rsi_cache,
                 baseline_cache)
    if stage in ("addcut", "all"):
        run_grid("ADDCUT", addcut_grid(), syms, mas, ohlc_cache, trend_cache, rsi_cache,
                 baseline_cache)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["tp","add","combo","addcut","all"], default="tp")
    ap.add_argument("--quick", action="store_true",
                    help="只用 7 个代表性品种和 4 个 MA，粗筛")
    args = ap.parse_args()
    main(stage=args.stage, quick=args.quick)
