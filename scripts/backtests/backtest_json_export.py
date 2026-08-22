"""
将均线策略回测结果导出为后端可读的 JSON。
- 每品种一个 <SYMBOL>.json：candles + ma_lines + trend_ma + (策略 × MA) 统计 + 每笔交易
- _manifest.json：品种列表 + 生成时间
"""

import datetime
import json
import os
import math

COST = 0.0015  # FEE_RATE + SLIPPAGE


def _to_unix(d):
    if hasattr(d, "timestamp"):
        return int(d.timestamp())
    if isinstance(d, datetime.datetime):
        return int(d.timestamp())
    if isinstance(d, datetime.date):
        return int(datetime.datetime(d.year, d.month, d.day).timestamp())
    return int(d)


def _safe(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _build_candles(ohlc):
    dates  = ohlc["dates"]
    opens  = ohlc["opens"]
    highs  = ohlc["highs"]
    lows   = ohlc["lows"]
    closes = ohlc["closes"]
    volumes = ohlc.get("volumes") or [0] * len(dates)
    out = []
    for i in range(len(dates)):
        out.append({
            "time":  _to_unix(dates[i]),
            "open":  _safe(opens[i]),
            "high":  _safe(highs[i]),
            "low":   _safe(lows[i]),
            "close": _safe(closes[i]),
            "volume": _safe(volumes[i]),
        })
    return out


def _build_line(values, dates):
    out = []
    for i, v in enumerate(values):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        out.append({"time": _to_unix(dates[i]), "value": v})
    return out


def _build_strategy_block(results, ohlc, best_n):
    dates  = ohlc["dates"]
    closes = ohlc["closes"]
    out = {}
    for r in results or []:
        n = r["n"]
        trades_out = []
        for ei, xi, ret, reason, sl, rr, max_ret in r["trade_log"]:
            entry_px = closes[ei] * (1 + COST)
            if reason in ("止损", "保本"):
                exit_px = sl * (1 - COST)
            else:
                exit_px = closes[xi] * (1 - COST)
            trades_out.append({
                "entry_time":     _to_unix(dates[ei]),
                "exit_time":      _to_unix(dates[xi]),
                "entry_price":    round(entry_px, 6),
                "exit_price":     round(exit_px, 6),
                "stop_loss":      round(sl, 6),
                "pnl_pct":        round(ret * 100, 4),
                "max_profit_pct": round(max_ret * 100, 4),
                "rr":             round(rr, 4) if rr is not None else None,
                "exit_reason":    reason,
            })
        out[str(n)] = {
            "stats": {
                "total_return": round(r["total_return"], 4),
                "n_trades":     r["n_trades"],
                "win_rate":     round(r["win_rate"], 4),
                "max_drawdown": round(r["max_drawdown"], 4),
                "max_profit":   round(r["max_profit"], 4),
                "avg_profit":   round(r["avg_profit"], 4),
                "avg_rr":       round(r["avg_rr"], 4) if r["avg_rr"] is not None else None,
                "avg_loss":     round(r.get("avg_loss", 0), 4),
                "expectancy":   round(r.get("expectancy", 0), 4),
                "profit_factor": round(r["profit_factor"], 4) if r.get("profit_factor") is not None else None,
                "aggregation_method": r.get("aggregation_method", "sum_of_independent_trade_returns"),
                "is_best":      (best_n is not None and n == best_n),
            },
            "trades": trades_out,
        }
    return out


def export_json(sym, name, ohlc, breakout, pullback, out_path,
                breakout_best=None, pullback_best=None):
    payload = {
        "symbol": sym,
        "name": name,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data_source": ohlc.get("data_source", "unknown"),
        "adjusted": bool(ohlc.get("adjusted", False)),
        "bar_count": len(ohlc["dates"]),
        "data_range": {
            "start": _to_unix(ohlc["dates"][0]) if ohlc["dates"] else None,
            "end": _to_unix(ohlc["dates"][-1]) if ohlc["dates"] else None,
        },
        "candles": _build_candles(ohlc),
        "trend_ma": _build_line(ohlc["trend_sma"], ohlc["dates"]),
        "ma_lines": {
            str(n): _build_line(line, ohlc["dates"])
            for n, line in ohlc["sma_by_n"].items()
        },
        "strategies": {
            "breakout": _build_strategy_block(breakout, ohlc, breakout_best),
            "pullback": _build_strategy_block(pullback, ohlc, pullback_best),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def write_manifest(symbols_map, out_dir):
    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbols": [{"code": code, "name": name} for code, name in symbols_map.items()],
    }
    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
