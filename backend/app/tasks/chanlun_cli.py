"""Manual 缠论 signal scan/preview command.

Examples:
  python -m app.tasks.chanlun_cli --dry-run BTC-USD
  python -m app.tasks.chanlun_cli --dry-run --all
  python -m app.tasks.chanlun_cli --scan BTC-USD
  python -m app.tasks.chanlun_cli --scan --all
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import settings
from app.core import chanlun_service
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
import app.models  # noqa: F401


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="缠论买卖点/背驰/抢先告警扫描")
    parser.add_argument("symbols", nargs="*", help="标的列表；留空用 CHANLUN_SYMBOLS")
    parser.add_argument("--all", action="store_true", help="扫描 CHANLUN_SYMBOLS 全部标的")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅打印将投递的 Discord 消息，不写库、不投递",
    )
    parser.add_argument("--scan", action="store_true", help="真实扫描：写库并投递")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出归一化信号")
    return parser.parse_args()


def _print_signal_lines(sig: dict) -> None:
    print(f"  [{sig['timeframe']}] {sig['k']} {sig['direction']} "
          f"state={sig.get('state')} t={sig.get('t')} "
          f"price={sig.get('price')} tier={sig.get('tier')}")
    if sig.get("why"):
        print(f"    why: {sig['why']}")
    if sig.get("sub") or sig.get("upper"):
        print(f"    sub: {sig.get('sub')} | upper: {sig.get('upper')}")


async def _preview(symbols: list[str]) -> None:
    if not chanlun_service.chanlun_available():
        raise SystemExit("fenxing 缠论模块不可用，无法预览")
    for symbol in symbols:
        symbol = symbol.upper()
        try:
            candles_by_tf: dict[str, list[dict]] = {}
            for tf in chanlun_service._tfs_for(symbol):
                candles_by_tf[tf] = await chanlun_service.fetch_closed(symbol, tf)
            if any(len(c) < settings.CHANLUN_MIN_BARS for c in candles_by_tf.values()):
                print(f"{symbol}: 有效K线不足（<{settings.CHANLUN_MIN_BARS}）")
                continue
            levels = chanlun_service.analyze_symbol(candles_by_tf)
            signals = chanlun_service.extract_signals(symbol, levels)
            print(f"\n===== {symbol} ===== {len(signals)} 条信号")
            for sig in signals:
                _print_signal_lines(sig)
            for tf, candles in candles_by_tf.items():
                title, description, color = chanlun_service._build_message(
                    symbol, tf, [s for s in signals if s["timeframe"] == tf]
                )
                print(f"--- Discord / {symbol} / {tf} / #{color:06x} ---")
                print(title)
                print(description)
        except Exception as exc:
            print(f"{symbol}: 失败 {exc}")


async def _scan(symbols: list[str]) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        results = await chanlun_service.scan_symbols(session, symbols)
        for result in results:
            print(json.dumps(result, ensure_ascii=False))
    await engine.dispose()


async def _run(args: argparse.Namespace) -> None:
    if args.scan and args.dry_run:
        raise SystemExit("--scan 与 --dry-run 不能同时使用")
    if args.scan and args.json:
        raise SystemExit("--scan 与 --json 不能同时使用")
    if args.all and args.symbols:
        raise SystemExit("--all 与显式标的不能同时使用")
    symbols = (
        [s.strip() for s in (settings.CHANLUN_SYMBOLS or "").split(",") if s.strip()]
        if args.all or not args.symbols
        else args.symbols
    )
    if not symbols:
        raise SystemExit("未指定标的（CHANLUN_SYMBOLS 为空）")
    if args.scan:
        await _scan(symbols)
    else:
        await _preview(symbols)


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
