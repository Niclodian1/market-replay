#!/usr/bin/env python3
"""按 spec 文件跑一次实验（不进 DB），用于验证 DSL 表达力与快速对比。

用法：
    python scripts/run_experiment_spec.py experiments/specs/ma_breakout_2r.spec.json
    python scripts/run_experiment_spec.py <spec> --json > metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.core.indicator_provider import get_registry  # noqa: E402
from app.experiment_core.runner import run_experiment  # noqa: E402
from app.experiment_core.service import load_data  # noqa: E402
from app.experiment_core.spec import parse_spec  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按 spec 运行实验")
    parser.add_argument("spec", type=Path, help="spec JSON 路径")
    parser.add_argument("--json", action="store_true", help="只输出 metrics JSON")
    args = parser.parse_args()

    raw = json.loads(args.spec.read_text(encoding="utf-8"))
    spec = parse_spec(raw)
    data = load_data(spec)
    bars_total = sum(len(rows) for rows in data.values())
    if bars_total == 0:
        print(f"没有取到行情数据：{sorted(data)}", file=sys.stderr)
        return 2

    result = run_experiment(spec, data, get_registry())
    if args.json:
        print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    else:
        print(f"{spec.name} · params_hash={spec.params_hash}")
        print(f"数据: {bars_total} 根 K 线 / {len(data)} 个 (symbol,timeframe) 组合")
        print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
