"""ma_strategies 系列 API 的公共工具：JSON 工件读取与回测脚本子进程执行。"""
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException

# 优先使用环境变量 REPO_ROOT（Docker 中注入），回退到从文件路径推断
REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"JSON 损坏：{path.name}（{e}）")


def run_backtest(args: list[str], *, timeout_seconds: int, timeout_message: str) -> None:
    if not script_exists(args[1]):
        raise HTTPException(500, f"找不到脚本：{args[1]}")
    try:
        result = subprocess.run(
            args,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, timeout_message)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        raise HTTPException(500, f"脚本执行失败：\n{chr(10).join(tail)}")


def python_cmd(script: Path) -> list[str]:
    return [sys.executable, str(script)]


def script_exists(path: Path | str) -> bool:
    return Path(path).exists()
