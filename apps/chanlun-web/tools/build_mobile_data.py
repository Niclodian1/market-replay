#!/usr/bin/env python3
"""Build phone-only practice payloads: 1D / 4H / 1H OHLCV, no chanlun, no minutes."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

KEEP_TFS = ("1D", "4H", "1H")

# id -> (display name, market). Must match desktop index.html / MARKET_GROUPS.
SYMBOLS = [
    ("BTC", "BTC · 比特币", "加密"),
    ("ETH", "ETH · 以太坊", "加密"),
    ("SOL", "SOL · Solana", "加密"),
    ("PEPE", "PEPE · Pepe", "加密"),
    ("HYPE", "HYPE · Hyperliquid", "加密"),
    ("XAUUSD", "XAUUSD · 现货黄金", "贵金属"),
    ("XAGUSD", "XAGUSD · 现货白银", "贵金属"),
    ("N225", "N225 · 日经225", "指数"),
    ("SPX", "SPX · 标普500", "指数"),
    ("NDX", "NDX · 纳斯达克100", "指数"),
    ("399006_SZ", "399006.SZ · 创业板指数", "指数"),
    ("000688_SS", "000688.SS · 科创50", "指数"),
    ("AAPL", "AAPL · 苹果", "美股"),
    ("MSFT", "MSFT · 微软", "美股"),
    ("NVDA", "NVDA · 英伟达", "美股"),
    ("GOOGL", "GOOGL · 谷歌", "美股"),
    ("META", "META · Meta", "美股"),
    ("TSLA", "TSLA · 特斯拉", "美股"),
    ("AMZN", "AMZN · 亚马逊", "美股"),
    ("BRK_B", "BRK.B · 伯克希尔", "美股"),
    ("JPM", "JPM · 摩根大通", "美股"),
    ("PLTR", "PLTR · Palantir", "美股"),
    ("COIN", "COIN · Coinbase", "美股"),
    ("AVGO", "AVGO · 博通", "美股"),
    ("MU", "MU · 美光", "美股"),
    ("WMT", "WMT · 沃尔玛", "美股"),
    ("AMD", "AMD · 超威半导体", "美股"),
    ("NFLX", "NFLX · 奈飞", "美股"),
    ("INTC", "INTC · 英特尔", "美股"),
    ("SNDK", "SNDK · 闪迪", "美股"),
    ("0700_HK", "0700.HK · 腾讯", "港股"),
    ("9988_HK", "9988.HK · 阿里巴巴", "港股"),
    ("600519_SS", "600519.SS · 贵州茅台", "A股"),
    ("002460_SZ", "002460.SZ · 赣锋锂业", "A股"),
    ("300750_SZ", "300750.SZ · 宁德时代", "A股"),
    ("600900_SS", "600900.SS · 长江电力", "A股"),
    ("000333_SZ", "000333.SZ · 美的集团", "A股"),
    ("603259_SS", "603259.SS · 药明康德", "A股"),
    ("300033_SZ", "300033.SZ · 同花顺", "A股"),
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def src_path(root: Path, sym: str) -> Path:
    dv = root / "data-view"
    if sym == "BTC":
        p = dv / "data.js"
        if p.exists():
            return p
    return dv / f"data_{sym.lower()}.js"


def load_market(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    i = text.find("{")
    if i < 0:
        raise ValueError(f"no JSON object in {path}")
    obj, _end = json.JSONDecoder().raw_decode(text, i)
    return obj


def _drop_close_only_prefix(rows: list) -> list:
    for i, row in enumerate(rows):
        if row[2] > row[3]:
            return rows[i:]
    return rows


def slim_symbol(obj: dict) -> dict:
    levels_in = obj.get("levels") or {}
    levels = {}
    tfs = []
    for tf in KEEP_TFS:
        lv = levels_in.get(tf)
        if not lv:
            continue
        candles = lv.get("candles") or []
        volume = lv.get("volume") or []
        if not candles:
            continue
        rows = []
        for i, c in enumerate(candles):
            vol = volume[i] if i < len(volume) else 0
            rows.append([c[0], c[1], c[2], c[3], c[4], vol])
        # Yahoo 指数早期常把开高低填成收盘价, 实体和影线都是 0, 看起来像没画出 K 线
        rows = _drop_close_only_prefix(rows)
        if not rows:
            continue
        levels[tf] = {"candles": rows}
        tfs.append(tf)
    meta_in = obj.get("meta") or {}
    return {
        "meta": {
            "symbol": meta_in.get("symbol") or "",
            "timeframes": tfs,
        },
        "levels": levels,
    }


def make_icons(icon_dir: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("PIL missing, skip icons", file=sys.stderr)
        return
    icon_dir.mkdir(parents=True, exist_ok=True)

    def paint(size: int) -> Image.Image:
        img = Image.new("RGBA", (size, size), (13, 13, 13, 255))
        d = ImageDraw.Draw(img)
        # rounded-ish frame
        pad = size * 0.08
        d.rounded_rectangle(
            [pad, pad, size - pad, size - pad],
            radius=size * 0.18,
            fill=(26, 26, 25, 255),
        )
        # two candles
        def candle(x, y0, y1, wick0, wick1, up: bool):
            col = (12, 163, 12, 255) if up else (230, 103, 103, 255)
            w = size * 0.035
            d.line([(x, wick0), (x, wick1)], fill=col, width=max(2, int(size * 0.018)))
            d.rectangle([x - w, y0, x + w, y1], fill=col)

        s = size
        candle(s * 0.34, s * 0.42, s * 0.68, s * 0.30, s * 0.74, False)
        candle(s * 0.50, s * 0.36, s * 0.58, s * 0.26, s * 0.66, True)
        candle(s * 0.66, s * 0.32, s * 0.52, s * 0.24, s * 0.62, True)
        return img

    for sz in (180, 192, 512):
        paint(sz).save(icon_dir / f"icon-{sz}.png", optimize=True)


def main() -> int:
    root = repo_root()
    out_dir = root / "mobile" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    vendor_src = root / "lightweight-charts.standalone.production.js"
    vendor_dst = root / "mobile" / "vendor" / "lightweight-charts.standalone.production.js"
    vendor_dst.parent.mkdir(parents=True, exist_ok=True)
    if vendor_src.exists():
        shutil.copy2(vendor_src, vendor_dst)

    make_icons(root / "mobile" / "icons")

    catalog = {"timeframes": list(KEEP_TFS), "updatedAt": int(time.time()), "symbols": []}
    total_bytes = 0
    missing = []
    for sid, name, market in SYMBOLS:
        src = src_path(root, sid)
        if not src.exists():
            missing.append(str(src))
            continue
        obj = load_market(src)
        slim = slim_symbol(obj)
        if not slim["levels"]:
            missing.append(f"{sid} (no 1D/4H/1H candles)")
            continue
        n_by_tf = {tf: len(slim["levels"][tf]["candles"]) for tf in slim["meta"]["timeframes"]}
        payload = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
        dest = out_dir / f"{sid.lower()}.json"
        dest.write_text(payload, encoding="utf-8")
        total_bytes += dest.stat().st_size
        catalog["symbols"].append({
            "id": sid,
            "name": name,
            "market": market,
            "symbol": slim["meta"]["symbol"],
            "timeframes": slim["meta"]["timeframes"],
            "bars": n_by_tf,
            "bytes": dest.stat().st_size,
        })
        print(f"{sid:12} {dest.stat().st_size/1e6:6.2f} MB  {n_by_tf}")

    cat_path = out_dir / "catalog.json"
    cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(catalog['symbols'])} symbols  {total_bytes/1e6:.1f} MB  -> {out_dir}")
    if missing:
        print("missing:", *missing, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
