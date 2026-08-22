"""
杠杆浮盈加仓策略回测结果读取端点。
数据由 scripts/backtests/backtest_ma_strategies_3x.py 生成，落在 backend/data/ma_strategies_{leverage}x/。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.api._backtest_artifacts import REPO_ROOT, python_cmd, read_json, run_backtest

router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parents[2] / "data"
_SCRIPT = REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies_3x.py"


def _data_dir(leverage: int) -> Path:
    return _BASE_DIR / f"ma_strategies_{leverage}x"


@router.get("/manifest")
def get_manifest(leverage: int = Query(default=2, ge=2, le=3)):
    p = _data_dir(leverage) / "_manifest.json"
    if not p.exists():
        raise HTTPException(
            404,
            f"未生成 {leverage}x 杠杆策略数据。"
            f"请先运行 `python3 scripts/backtests/backtest_ma_strategies_3x.py`。",
        )
    return read_json(p)


@router.get("/{symbol}")
def get_symbol(symbol: str, leverage: int = Query(default=2, ge=2, le=3)):
    safe = symbol.replace("/", "-")
    p = _data_dir(leverage) / f"{safe}.json"
    if not p.exists():
        raise HTTPException(404, f"未找到 {symbol} 的 {leverage}x 回测数据")
    return read_json(p)


@router.post("/{symbol}/refresh")
def refresh_symbol(symbol: str, leverage: int = Query(default=2, ge=2, le=3)):
    """重新跑指定品种 + 杠杆的回测，覆写 JSON。"""
    run_backtest(
        [*python_cmd(_SCRIPT), "--symbol", symbol, "--leverage", str(leverage), "--no-excel"],
        timeout_seconds=300,
        timeout_message="回测超时（>5 分钟）",
    )
    safe = symbol.replace("/", "-")
    p = _data_dir(leverage) / f"{safe}.json"
    if not p.exists():
        raise HTTPException(500, f"刷新后仍未找到 {symbol} 的 {leverage}x JSON 文件")
    return read_json(p)
