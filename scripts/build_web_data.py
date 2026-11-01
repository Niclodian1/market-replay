#!/usr/bin/env python3
"""把看盘/练习数据出库为静态 JSON（默认 apps/platform-web/public/data）。

    python scripts/build_web_data.py --pairs BTC-USD:4h BTC-USD:1day --limit 1200
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.core.web_data import write_bundles  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pairs",
        nargs="*",
        default=["BTC-USD:4h", "BTC-USD:1day", "ETH-USD:4h", "ETH-USD:1day"],
        help="SYMBOL:TIMEFRAME 列表",
    )
    parser.add_argument("--out", default=str(REPO_ROOT / "apps" / "platform-web" / "public" / "data"))
    parser.add_argument("--limit", type=int, default=1200)
    args = parser.parse_args()

    pairs = []
    for item in args.pairs:
        if ":" not in item:
            parser.error(f"格式应为 SYMBOL:TIMEFRAME，收到 {item}")
        symbol, timeframe = item.split(":", 1)
        pairs.append((symbol, timeframe))

    manifest = write_bundles(Path(args.out), pairs, limit=args.limit)
    print(
        f"已生成 {len(manifest['symbols'])} 个品种，"
        f"{sum(len(item['timeframes']) for item in manifest['symbols'])} 份数据；"
        f"跳过 {len(manifest['skipped'])}"
    )


if __name__ == "__main__":
    main()
