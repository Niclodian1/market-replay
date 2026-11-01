#!/usr/bin/env python3
"""回填指标事件与结果（统计页/练习页的数据源）。

    python scripts/backfill_indicator_events.py --symbol BTC-USD --timeframe 1day
    python scripts/backfill_indicator_events.py --symbol BTC-USD --timeframe 4h --indicators chanlun.buy3 ma.breakout
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.core import event_store  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        result = await event_store.backfill(
            db,
            args.symbol,
            args.timeframe,
            indicator_ids=args.indicators,
            horizon=args.horizon,
            target_r=args.target_r,
            stop_lookback=args.stop_lookback,
        )
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--timeframe", default="1day")
    parser.add_argument("--indicators", nargs="*", default=None)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--target-r", type=float, default=2.0)
    parser.add_argument("--stop-lookback", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
