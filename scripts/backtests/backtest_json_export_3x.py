"""
将杠杆浮盈加仓策略回测结果导出为 JSON。
trade_log 格式（9 元素）：(ei, xi, ret, reason, sl, rr, max_ret, leverage, events)
其中 events 是 list[dict(bar_idx, type, price, size?, reason?)]
"""

import datetime
import json
import math
import os

COST = 0.0015


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
    out = []
    for i in range(len(ohlc["dates"])):
        out.append({
            "time":   _to_unix(ohlc["dates"][i]),
            "open":   _safe(ohlc["opens"][i]),
            "high":   _safe(ohlc["highs"][i]),
            "low":    _safe(ohlc["lows"][i]),
            "close":  _safe(ohlc["closes"][i]),
            "volume": 0,
        })
    return out


def _build_line(values, dates):
    out = []
    for i, v in enumerate(values):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        out.append({"time": _to_unix(dates[i]), "value": v})
    return out


def _build_strategy_block_3x(results, ohlc, best_n, leverage):
    dates  = ohlc["dates"]
    closes = ohlc["closes"]
    out    = {}
    for r in results or []:
        n          = r["n"]
        trade_log  = r.get("trade_log") or []
        trades_out = []

        for tlog in trade_log:
            ei, xi, ret, reason, sl, rr, max_ret, lev, events = tlog
            entry_px = closes[ei] * (1 + COST)
            exit_px  = sl * (1 - COST) if reason in ("止损", "保本") else closes[xi] * (1 - COST)

            # 将 events 的 bar_idx 转换为 unix 时间戳
            events_out = []
            for ev in events:
                ev_ts = _to_unix(dates[ev["bar_idx"]])
                item  = {
                    "time":  ev_ts,
                    "type":  ev["type"],
                    "price": round(float(ev["price"]), 6),
                    "size":  round(float(ev["size"]), 4) if ev.get("size") is not None else None,
                }
                if ev.get("reason"):
                    item["reason"] = ev["reason"]
                events_out.append(item)

            trades_out.append({
                "entry_time":      _to_unix(dates[ei]),
                "exit_time":       _to_unix(dates[xi]),
                "entry_price":     round(entry_px, 6),
                "exit_price":      round(exit_px, 6),
                "stop_loss":       round(sl, 6),
                "pnl_pct":         round(ret * 100, 4),
                "max_profit_pct":  round(max_ret * 100, 4),
                "rr":              round(rr, 4) if rr is not None else None,
                "exit_reason":     reason,
                "leverage":        lev,
                "n_adds":          sum(1 for e in events if e["type"] == "add"),
                "events":          events_out,
            })

        out[str(n)] = {
            "leverage": leverage,
            "stats": {
                "total_return": round(r["total_return"], 4),
                "n_trades":     r["n_trades"],
                "win_rate":     round(r["win_rate"], 4),
                "max_drawdown": round(r["max_drawdown"], 4),
                "max_profit":   round(r["max_profit"], 4),
                "avg_profit":   round(r["avg_profit"], 4),
                "avg_rr":       round(r["avg_rr"], 4) if r.get("avg_rr") is not None else None,
                "is_best":      (best_n is not None and n == best_n),
                "leverage":     leverage,
            },
            "trades": trades_out,
        }
    return out


def export_json_3x(sym, name, ohlc, breakout, pullback, out_path,
                   breakout_best=None, pullback_best=None, leverage=2):
    payload = {
        "symbol":       sym,
        "name":         name,
        "leverage":     leverage,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "candles":      _build_candles(ohlc),
        "trend_ma":     _build_line(ohlc["trend_sma"], ohlc["dates"]),
        "ma_lines":     {
            str(n): _build_line(line, ohlc["dates"])
            for n, line in ohlc["sma_by_n"].items()
        },
        "strategies": {
            "breakout": _build_strategy_block_3x(breakout, ohlc, breakout_best, leverage),
            "pullback": _build_strategy_block_3x(pullback, ohlc, pullback_best, leverage),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def write_manifest_3x(symbols_map, out_dir):
    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbols": [{"code": code, "name": name} for code, name in symbols_map.items()],
    }
    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
