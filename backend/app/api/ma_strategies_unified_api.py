"""
Unified MA strategy result reader.

This endpoint keeps the legacy static JSON folders intact and exposes the
actually available A/B/C, timeframe, and leverage combinations to the frontend.
"""

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2] / "data"
REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))

# 第一阶段产品面只暴露四个市场、策略 A、日线、无杠杆。
# 旧策略数据仍保留在磁盘和旧 API 中，避免破坏用户已有实验结果。
PRODUCT_SYMBOLS = {
    "BTC-USD": "BTC",
    "AAPL": "Apple",
    "XAUUSD": "现货黄金",
    "600519.SS": "贵州茅台",
}
TIMEFRAMES = ("1day",)
STRATEGIES = ("A",)
LEVERAGES = ("none",)

AB_STRATEGY_KEY = {"A": "breakout", "B": "pullback"}
AB_OPTION_KEYS = ("30", "60", "99", "120")
C_OPTION_KEYS = ("x2", "x3", "x4")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"JSON 损坏：{path.name}（{e}）")


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "-")


def _ab_dir(timeframe: str, leverage: str) -> Path:
    if leverage == "none":
        return BASE_DIR / ("ma_strategies" if timeframe == "1day" else f"ma_strategies_{timeframe}")
    return BASE_DIR / (f"ma_strategies_{leverage}x" if timeframe == "1day" else f"ma_strategies_{leverage}x_{timeframe}")


def _c_dir(leverage: str) -> Path:
    return BASE_DIR / ("ma_strategies_c" if leverage == "none" else f"ma_strategies_c_{leverage}x")


def _data_path(symbol: str, strategy: str, timeframe: str, leverage: str) -> Path:
    safe = _safe_symbol(symbol)
    if strategy in AB_STRATEGY_KEY:
        return _ab_dir(timeframe, leverage) / f"{safe}.json"
    return _c_dir(leverage) / f"{safe}_{timeframe}.json"


def _load_manifest(path: Path) -> dict[str, str]:
    p = path / "_manifest.json"
    if not p.exists():
        return {}
    data = _read_json(p)
    return {item["code"]: item.get("name", item["code"]) for item in data.get("symbols", [])}


def _all_known_symbols() -> dict[str, str]:
    manifest = _load_manifest(_ab_dir("1day", "none"))
    return {code: manifest.get(code, name) for code, name in PRODUCT_SYMBOLS.items()}


def _combo_available(symbol: str, strategy: str, timeframe: str, leverage: str) -> bool:
    return _data_path(symbol, strategy, timeframe, leverage).exists()


def _available_combos(symbol: str) -> list[dict[str, str]]:
    out = []
    for strategy in STRATEGIES:
        for timeframe in TIMEFRAMES:
            for leverage in LEVERAGES:
                if _combo_available(symbol, strategy, timeframe, leverage):
                    out.append({"strategy": strategy, "timeframe": timeframe, "leverage": leverage})
    return out


def _basic_events(trade: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "time": trade["entry_time"],
            "type": "entry",
            "price": trade["entry_price"],
            "size": 1.0,
        },
        {
            "time": trade["exit_time"],
            "type": "exit",
            "price": trade["exit_price"],
            "size": None,
            "reason": trade.get("exit_reason", "均线"),
        },
    ]


def _normalize_trade(trade: dict[str, Any], leverage: str) -> dict[str, Any]:
    item = dict(trade)
    item.setdefault("leverage", None if leverage == "none" else int(leverage))
    item.setdefault("n_adds", 0)
    item.setdefault("events", _basic_events(item))
    return item


def _normalize_payload(raw: dict[str, Any], strategy: str, timeframe: str, leverage: str) -> dict[str, Any]:
    if strategy in AB_STRATEGY_KEY:
        source_key = AB_STRATEGY_KEY[strategy]
        source = raw.get("strategies", {}).get(source_key, {})
        results = {}
        for key in AB_OPTION_KEYS:
            block = source.get(key)
            if block:
                results[key] = {
                    "stats": block["stats"],
                    "trades": [_normalize_trade(t, leverage) for t in block.get("trades", [])],
                }
        return {
            "symbol": raw.get("symbol"),
            "name": raw.get("name"),
            "strategy": strategy,
            "timeframe": timeframe,
            "leverage": leverage,
            "generated_at": raw.get("generated_at"),
            "data_source": raw.get("data_source", "unknown"),
            "adjusted": bool(raw.get("adjusted", False)),
            "bar_count": raw.get("bar_count", len(raw.get("candles", []))),
            "data_range": raw.get("data_range", {}),
            "candles": raw.get("candles", []),
            "trend_ma": raw.get("trend_ma", []),
            "ma_lines": raw.get("ma_lines", {}),
            "parameter_kind": "ma",
            "options": list(results.keys()),
            "results": results,
        }

    source = raw.get("variants", {})
    results = {}
    for key in C_OPTION_KEYS:
        block = source.get(key)
        if block:
            results[key] = {
                "stats": block["stats"],
                "trades": [_normalize_trade(t, leverage) for t in block.get("trades", [])],
            }
    return {
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "strategy": strategy,
        "timeframe": timeframe,
        "leverage": leverage,
        "generated_at": raw.get("generated_at"),
        "candles": raw.get("candles", []),
        "trend_ma": [],
        "ma_lines": raw.get("ma_lines", {}),
        "parameter_kind": "variant",
        "options": list(results.keys()),
        "results": results,
    }


def _latest_setup(raw: dict[str, Any], parameter: str) -> dict[str, Any] | None:
    candles = raw.get("candles") or []
    line = raw.get("ma_lines", {}).get(parameter) or []
    if len(candles) < 2 or len(line) < 2:
        return None
    ma_by_time = {point["time"]: point["value"] for point in line}
    previous, latest = candles[-2], candles[-1]
    previous_ma = ma_by_time.get(previous["time"])
    current_ma = ma_by_time.get(latest["time"])
    if previous_ma is None or current_ma is None:
        return None
    body_rise = (latest["close"] / latest["open"] - 1) * 100 if latest["open"] else 0
    crossed = previous["close"] <= previous_ma and latest["close"] > current_ma
    return {
        "time": latest["time"],
        "close": latest["close"],
        "ma": current_ma,
        "body_rise": round(body_rise, 4),
        "crossed": crossed,
        "triggered": crossed and body_rise > 3,
    }


def _variant_summary(raw: dict[str, Any], parameter: str) -> dict[str, Any] | None:
    block = raw.get("strategies", {}).get("breakout", {}).get(parameter)
    if not block:
        return None
    stats = block.get("stats", {})
    trades = block.get("trades") or []
    return {
        "parameter": parameter,
        "stats": stats,
        "last_signal_time": max((trade.get("entry_time", 0) for trade in trades), default=None),
        "latest_setup": _latest_setup(raw, parameter),
    }


def _weighted(variants: list[dict[str, Any]], key: str) -> float:
    total = sum(int(item["stats"].get("n_trades", 0)) for item in variants)
    if not total:
        return 0.0
    return round(sum(float(item["stats"].get(key, 0)) * int(item["stats"].get("n_trades", 0)) for item in variants) / total, 4)


def _symbol_overview(symbol: str, name: str, raw: dict[str, Any]) -> dict[str, Any]:
    variants = [item for parameter in AB_OPTION_KEYS if (item := _variant_summary(raw, parameter))]
    positive = [item for item in variants if float(item["stats"].get("expectancy", 0)) > 0]
    candidates = positive or variants
    preferred = max(
        candidates,
        key=lambda item: (int(item["stats"].get("n_trades", 0)), float(item["stats"].get("expectancy", 0))),
        default=None,
    )
    sample_count = sum(int(item["stats"].get("n_trades", 0)) for item in variants)
    positive_count = len(positive)
    evidence = (
        "稳定" if positive_count >= 3 and sample_count >= 40
        else "候选" if positive_count >= 2
        else "观察"
    )
    return {
        "symbol": symbol,
        "name": name,
        "data_source": raw.get("data_source", "unknown"),
        "bar_count": raw.get("bar_count", len(raw.get("candles", []))),
        "data_range": raw.get("data_range", {}),
        "strategy": "A",
        "strategy_name": "日线均线突破",
        "sample_count": sample_count,
        "sample_count_note": "四组MA参数样本合计，未去重",
        "positive_variants": positive_count,
        "variant_count": len(variants),
        "weighted_expectancy": _weighted(variants, "expectancy"),
        "weighted_win_rate": _weighted(variants, "win_rate"),
        "evidence": evidence,
        "preferred_parameter": preferred["parameter"] if preferred else None,
        "current_signals": [item["parameter"] for item in variants if (item.get("latest_setup") or {}).get("triggered")],
        "variants": variants,
    }


@router.get("/manifest")
def get_manifest():
    symbols = _all_known_symbols()
    payload_symbols = []
    for code, name in symbols.items():
        combos = _available_combos(code)
        if combos:
            payload_symbols.append({"code": code, "name": name, "available": combos})
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbols": payload_symbols,
        "timeframes": list(TIMEFRAMES),
        "strategies": list(STRATEGIES),
        "leverages": list(LEVERAGES),
    }


@router.get("/overview")
def get_overview():
    symbols = []
    generated = []
    for code, name in PRODUCT_SYMBOLS.items():
        path = _data_path(code, "A", "1day", "none")
        if not path.exists():
            continue
        raw = _read_json(path)
        symbols.append(_symbol_overview(code, name, raw))
        if raw.get("generated_at"):
            generated.append(raw["generated_at"])
    ranked = sorted(
        symbols,
        key=lambda item: (item["sample_count"], item["positive_variants"], item["weighted_expectancy"]),
        reverse=True,
    )
    breadth = {
        parameter: sum(
            1 for item in symbols
            for variant in item["variants"]
            if variant["parameter"] == parameter and float(variant["stats"].get("expectancy", 0)) > 0
        )
        for parameter in AB_OPTION_KEYS
    }
    return {
        "generated_at": max(generated) if generated else datetime.datetime.now().isoformat(timespec="seconds"),
        "methodology": {
            "ranking": "样本数降序 → 正期望参数数 → 加权单笔期望",
            "sample_note": "综合样本数为四组MA参数样本合计，参数间信号可能重叠",
            "evidence_note": "证据等级用于比较历史适配性，不代表未来胜率或交易建议",
        },
        "strategies": [{
            "id": "A",
            "name": "日线均线突破",
            "description": "前收盘不高于均线，本日收盘突破均线，且实体涨幅大于3%",
            "parameters": list(AB_OPTION_KEYS),
            "positive_symbol_breadth": breadth,
        }],
        "symbols": symbols,
        "ranked_symbols": ranked,
    }


@router.get("/{symbol}")
def get_symbol(
    symbol: str,
    strategy: str = Query(..., pattern="^[ABC]$"),
    timeframe: str = Query(...),
    leverage: str = Query(default="none"),
):
    if symbol not in PRODUCT_SYMBOLS:
        raise HTTPException(404, f"第一阶段暂不支持 {symbol}")
    if strategy not in STRATEGIES:
        raise HTTPException(400, "第一阶段仅支持策略 A")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, f"非法周期：{timeframe}（合法：4h, 1day）")
    if leverage not in LEVERAGES:
        raise HTTPException(400, f"非法杠杆：{leverage}（合法：none, 2, 3）")
    path = _data_path(symbol, strategy, timeframe, leverage)
    if not path.exists():
        raise HTTPException(404, f"暂无 {symbol} 策略{strategy} {timeframe} {leverage} 杠杆回测数据")
    return _normalize_payload(_read_json(path), strategy, timeframe, leverage)


@router.post("/{symbol}/refresh")
def refresh_symbol(
    symbol: str,
    strategy: str = Query(..., pattern="^[ABC]$"),
    timeframe: str = Query(...),
    leverage: str = Query(default="none"),
):
    if symbol not in PRODUCT_SYMBOLS:
        raise HTTPException(404, f"第一阶段暂不支持 {symbol}")
    if strategy not in STRATEGIES:
        raise HTTPException(400, "第一阶段仅支持策略 A")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, f"非法周期：{timeframe}（合法：4h, 1day）")
    if leverage not in LEVERAGES:
        raise HTTPException(400, f"非法杠杆：{leverage}（合法：none, 2, 3）")

    if strategy in AB_STRATEGY_KEY:
        script = REPO_ROOT / "scripts" / "backtests" / (
            "backtest_ma_strategies.py" if leverage == "none" else "backtest_ma_strategies_3x.py"
        )
        args = [sys.executable, str(script), "--symbol", symbol, "--timeframe", timeframe, "--no-excel"]
        if leverage == "none":
            args.append("--no-html")
        else:
            args.extend(["--leverage", leverage])
    else:
        script = REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies_c.py"
        args = [sys.executable, str(script), "--symbol", symbol, "--timeframe", timeframe, "--no-excel"]
        if leverage != "none":
            args.extend(["--leverage", leverage])

    if not script.exists():
        raise HTTPException(500, f"找不到脚本：{script}")
    try:
        result = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "回测超时（>5 分钟）")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        raise HTTPException(500, f"脚本执行失败：\n{chr(10).join(tail)}")

    path = _data_path(symbol, strategy, timeframe, leverage)
    if not path.exists():
        raise HTTPException(500, f"刷新后仍未找到 JSON 文件：{path.name}")
    return _normalize_payload(_read_json(path), strategy, timeframe, leverage)
