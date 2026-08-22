"""
均线策略回测结果（静态 JSON）读取端点。
数据由 scripts/backtests/backtest_ma_strategies.py 脚本生成，落在 backend/data/ma_strategies/。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api._backtest_artifacts import REPO_ROOT, python_cmd, read_json, run_backtest

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "ma_strategies"
SCRIPT_PATH = REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies.py"


@router.get("/manifest")
def get_manifest():
    p = DATA_DIR / "_manifest.json"
    if not p.exists():
        raise HTTPException(
            404,
            "未生成均线策略回测数据。请先运行 `python3 scripts/backtests/backtest_ma_strategies.py`。",
        )
    return read_json(p)


@router.get("/{symbol}")
def get_symbol(symbol: str):
    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}.json"
    if not p.exists():
        raise HTTPException(404, f"未找到 {symbol} 的回测数据")
    return read_json(p)


@router.post("/{symbol}/refresh")
def refresh_symbol(symbol: str):
    """重新下载该品种最新数据 + 跑回测 + 覆写 JSON，返回最新 payload。"""
    run_backtest(
        [*python_cmd(SCRIPT_PATH), "--symbol", symbol, "--no-excel", "--no-html"],
        timeout_seconds=180,
        timeout_message="回测超时（>3 分钟）",
    )
    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}.json"
    if not p.exists():
        raise HTTPException(500, f"刷新后仍未找到 {symbol} 的 JSON 文件")
    return read_json(p)
