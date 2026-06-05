"""
均线策略回测结果（静态 JSON）读取端点。
数据由 scripts/backtests/backtest_ma_strategies.py 脚本生成，落在 backend/data/ma_strategies/。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "ma_strategies"
# 优先使用环境变量 REPO_ROOT（Docker 中注入），回退到从文件路径推断
REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
SCRIPT_PATH = REPO_ROOT / "scripts" / "backtests" / "backtest_ma_strategies.py"


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"JSON 损坏：{path.name}（{e}）")


@router.get("/manifest")
def get_manifest():
    p = DATA_DIR / "_manifest.json"
    if not p.exists():
        raise HTTPException(
            404,
            "未生成均线策略回测数据。请先运行 `python3 scripts/backtests/backtest_ma_strategies.py`。",
        )
    return _read_json(p)


@router.get("/{symbol}")
def get_symbol(symbol: str):
    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}.json"
    if not p.exists():
        raise HTTPException(404, f"未找到 {symbol} 的回测数据")
    return _read_json(p)


@router.post("/{symbol}/refresh")
def refresh_symbol(symbol: str):
    """重新下载该品种最新数据 + 跑回测 + 覆写 JSON，返回最新 payload。"""
    if not SCRIPT_PATH.exists():
        raise HTTPException(500, f"找不到脚本：{SCRIPT_PATH}")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--symbol", symbol, "--no-excel", "--no-html"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "回测超时（>3 分钟）")

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        raise HTTPException(500, f"脚本执行失败：\n{chr(10).join(tail)}")

    safe = symbol.replace("/", "-")
    p = DATA_DIR / f"{safe}.json"
    if not p.exists():
        raise HTTPException(500, f"刷新后仍未找到 {symbol} 的 JSON 文件")
    return _read_json(p)
