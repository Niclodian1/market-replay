#!/usr/bin/env python3
"""MA 同族回测的统一入口（薄封装，委托 scripts/backtests/*，保留旧脚本可回滚）。

    python3 labs/backtests/ma.py --variant base|3x|c [原脚本参数...]
    python3 labs/backtests/ma.py --list

对应关系（REPO_AUDIT P4 第 3 步；完整合并为单文件仍是后续工作）：
    base -> scripts/backtests/backtest_ma_strategies.py   （A/B 突破+回调，1day/4h）
    3x   -> scripts/backtests/backtest_ma_strategies_3x.py（杠杆 2/3 倍）
    c    -> scripts/backtests/backtest_ma_strategies_c.py （策略 C 均线池）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VARIANTS = {
    "base": "scripts/backtests/backtest_ma_strategies.py",
    "3x": "scripts/backtests/backtest_ma_strategies_3x.py",
    "c": "scripts/backtests/backtest_ma_strategies_c.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="MA 同族回测统一入口")
    parser.add_argument("--variant", choices=sorted(VARIANTS), help="base | 3x | c")
    parser.add_argument("--list", action="store_true", help="列出变体与对应脚本")
    args, rest = parser.parse_known_args()

    if args.list or not args.variant:
        for name, script in VARIANTS.items():
            print(f"{name:5s} -> {script}")
        return 0

    script = REPO_ROOT / VARIANTS[args.variant]
    if not script.exists():
        print(f"脚本不存在: {script}", file=sys.stderr)
        return 2
    print(f"[labs] variant={args.variant} -> {VARIANTS[args.variant]}", file=sys.stderr)
    return subprocess.call([sys.executable, str(script), *rest], cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
