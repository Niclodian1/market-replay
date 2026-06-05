"""
杠杆浮盈加仓策略回测结果读取端点。
数据由 scripts/backtests/backtest_ma_strategies_3x.py 生成，落在 backend/data/ma_strategies_{leverage}x/。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_BASE_DIR  = Path(__file__).resolve().parents[2] / "data"
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
_SCRIPT    = _REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies_3x.py"


def _data_dir(leverage: int) -> Path:
    return _BASE_DIR / f"ma_strategies_{leverage}x"


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"JSON 损坏：{path.name}（{e}）")


@router.get("/manifest")
def get_manifest(leverage: int = Query(default=2, ge=2, le=3)):
    p = _data_dir(leverage) / "_manifest.json"
    if not p.exists():
        raise HTTPException(
            404,
            f"未生成 {leverage}x 杠杆策略数据。"
            f"请先运行 `python3 scripts/backtests/backtest_ma_strategies_3x.py`。",
        )
    return _read_json(p)


@router.get("/{symbol}")
def get_symbol(symbol: str, leverage: int = Query(default=2, ge=2, le=3)):
    safe = symbol.replace("/", "-")
    p    = _data_dir(leverage) / f"{safe}.json"
    if not p.exists():
        raise HTTPException(404, f"未找到 {symbol} 的 {leverage}x 回测数据")
    return _read_json(p)


@router.post("/{symbol}/refresh")
def refresh_symbol(symbol: str, leverage: int = Query(default=2, ge=2, le=3)):
    """重新跑指定品种 + 杠杆的回测，覆写 JSON。"""
    if not _SCRIPT.exists():
        raise HTTPException(500, f"找不到脚本：{_SCRIPT}")
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--symbol", symbol,
             "--leverage", str(leverage),
             "--no-excel"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "回测超时（>5 分钟）")

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        raise HTTPException(500, f"脚本执行失败：\n{chr(10).join(tail)}")

    safe = symbol.replace("/", "-")
    p    = _data_dir(leverage) / f"{safe}.json"
    if not p.exists():
        raise HTTPException(500, f"刷新后仍未找到 {symbol} 的 {leverage}x JSON 文件")
    return _read_json(p)
