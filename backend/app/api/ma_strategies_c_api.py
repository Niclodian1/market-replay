"""
策略 C（多均线同时上穿）回测结果（静态 JSON）读取端点。
数据由 scripts/backtests/backtest_ma_strategies_c.py 生成，落在 backend/data/ma_strategies_c/。
按 品种 × 周期 各一个文件：{SYMBOL}_{tf}.json。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api._backtest_artifacts import REPO_ROOT, python_cmd, read_json, run_backtest

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "ma_strategies_c"
SCRIPT_PATH = REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies_c.py"

VALID_TIMEFRAMES = {"1day", "4h", "1h"}


@router.get("/manifest")
def get_manifest():
    p = DATA_DIR / "_manifest.json"
    if not p.exists():
        raise HTTPException(
            404,
            "未生成策略C回测数据。请先运行 `python3 scripts/backtests/backtest_ma_strategies_c.py`。",
        )
    return read_json(p)


@router.get("/{symbol}/{timeframe}")
def get_symbol(symbol: str, timeframe: str):
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(400, f"非法周期：{timeframe}（合法：{', '.join(sorted(VALID_TIMEFRAMES))}）")
    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}_{timeframe}.json"
    if not p.exists():
        raise HTTPException(404, f"未找到 {symbol} {timeframe} 的策略C回测数据")
    return read_json(p)


@router.post("/{symbol}/{timeframe}/refresh")
def refresh_symbol(symbol: str, timeframe: str):
    """重新拉取该品种该周期最新数据 + 跑回测 + 覆写 JSON，返回最新 payload。"""
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(400, f"非法周期：{timeframe}（合法：{', '.join(sorted(VALID_TIMEFRAMES))}）")
    run_backtest(
        [*python_cmd(SCRIPT_PATH), "--symbol", symbol, "--timeframe", timeframe, "--no-excel"],
        timeout_seconds=300,
        timeout_message="回测超时（>5 分钟）",
    )
    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}_{timeframe}.json"
    if not p.exists():
        raise HTTPException(500, f"刷新后仍未找到 {symbol} {timeframe} 的 JSON 文件")
    return read_json(p)
