#!/usr/bin/env python3
"""Binance 1m/15m 全史下载 → 月度二进制分片(前端按需加载)。

设计背景: BTC/ETH/SOL/PEPE 的分钟级全史达百万根量级, 无法整体塞进前端。
本脚本不做任何缠论计算, 只做 数据搬运+切片:
  - 月度归档: https://data.binance.vision/data/spot/monthly/klines/...
  - 当月/归档滞后期: /api/v3/klines 分页补齐, 过滤未收盘K线
  - 输出: fenxing/intraday/<SYM>/<interval>/<YYYY-MM>.bin
          (6×Float64 小端: time,open,high,low,close,volume; n=字节数/48)
          fenxing/intraday/<SYM>/<interval>/manifest.json
          {"symbol","interval","sec","total","chunks":[{file,t0,t1,n}]}
  - 可选主档(--parquet): data/crypto/<SYM>USDT_<interval>.parquet 标准六列

用法:
  python3 scripts/ops/build_intraday_chunks.py --symbols BTC --intervals 1m,15m
  python3 scripts/ops/build_intraday_chunks.py --symbols BTC,ETH,SOL,PEPE \
      --intervals 1m,15m --parquet --force-recent
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import struct
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "fenxing" / "intraday"
PARQUET_DIR = REPO / "data" / "crypto"

ARCHIVE_TPL = ("https://data.binance.vision/data/spot/monthly/klines/"
               "{sym}USDT/{iv}/{sym}USDT-{iv}-{ym}.zip")
API_KLINES = "https://api.binance.com/api/v3/klines"

IV_SECONDS = {"15m": 900}   # 1m 已于 2026-09 停用(练习噪声大、维护缺口多); 如需恢复加 "1m": 60
# 各币 Binance 现货上线月(仅用于缩小遍历范围; 缺月404会被自然跳过)
LISTING_MONTH = {"BTC": "2017-08", "ETH": "2017-08", "SOL": "2020-08", "PEPE": "2023-05"}
COLUMNS_6 = ["time", "open", "high", "low", "close", "volume"]


def month_iter(start_ym: str, end_ym: str):
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    ey, em = int(end_ym[:4]), int(end_ym[5:7])
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def norm_ts(value: str | int) -> int:
    """Binance 时间戳兼容: 秒/毫秒/微秒 → 秒。
    归档 CSV 实测为毫秒; API 为毫秒; 预留微秒兼容。"""
    v = int(value)
    if v > 10**15:      # 微秒
        return v // 10**6
    if v > 10**11:      # 毫秒
        return v // 1000
    return v            # 秒


def parse_archive_csv(text: str) -> list[tuple]:
    """月度 zip 内 CSV → [(time,o,h,l,c,v)]; 兼容带表头与µs时间戳的新版格式。

    练习层质量过滤: 剔除零成交K线(volume==0)——无价格发现意义, 纯噪声;
    主档 parquet 同口径(如需原始全量可从 Binance 归档重下)。
    """
    rows = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 6:
            continue
        try:
            t = norm_ts(row[0])
            o, h, l, c, v = (float(row[i]) for i in range(1, 6))
        except ValueError:
            continue   # 表头行或脏行
        if v <= 0:
            continue   # 零成交K线: 剔除
        rows.append((t, o, h, l, c, v))
    return rows


async def fetch_archive(client: httpx.AsyncClient, sym: str, iv: str, ym: str) -> list[tuple] | None:
    url = ARCHIVE_TPL.format(sym=sym, iv=iv, ym=ym)
    for attempt in (1, 2):
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = zf.namelist()[0]
                return parse_archive_csv(zf.read(name).decode("utf-8"))
        except (httpx.HTTPError, zipfile.BadZipFile) as exc:
            if attempt == 2:
                print(f"  [warn] {ym} 归档失败: {exc}", flush=True)
                return None
            await asyncio.sleep(2)


async def fetch_api_range(client: httpx.AsyncClient, sym: str, iv: str,
                          start_s: int, end_s: int) -> list[tuple]:
    """API 分页正向拉取 [start_s, end_s], 过滤未收盘K线。"""
    out: dict[int, tuple] = {}
    cursor = start_s * 1000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cursor < end_s * 1000:
        resp = await client.get(API_KLINES, params={
            "symbol": f"{sym}USDT", "interval": iv,
            "startTime": cursor, "endTime": end_s * 1000 - 1, "limit": 1000,
        })
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for row in batch:
            open_ms = int(row[0])
            close_ms = int(row[6])
            if close_ms >= now_ms:      # 未收盘
                continue
            o, h, l, c, v = (float(row[i]) for i in range(1, 6))
            if v <= 0:                  # 零成交K线: 剔除(与归档口径一致)
                continue
            out[open_ms // 1000] = (open_ms // 1000, o, h, l, c, v)
        if len(batch) < 1000:
            break
        cursor = int(batch[-1][0]) + IV_SECONDS[iv] * 1000
        await asyncio.sleep(0.15)
    return [out[k] for k in sorted(out)]


def write_bin(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat: list[float] = []
    for r in rows:
        flat.extend(float(x) for x in r)
    packed = struct.pack(f"<{len(flat)}d", *flat)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(packed)
    tmp.replace(path)


async def build_one(client: httpx.AsyncClient, sym: str, iv: str,
                    force_recent: bool, want_parquet: bool) -> None:
    sec = IV_SECONDS[iv]
    out_dir = OUT_ROOT / sym / iv
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    cur_ym = now.strftime("%Y-%m")
    months = list(month_iter(LISTING_MONTH[sym], cur_ym))

    buckets: dict[str, list[tuple]] = {}
    n_archives = 0
    for i, ym in enumerate(months):
        bin_path = out_dir / f"{ym}.bin"
        is_current = ym == cur_ym
        is_lag = i >= len(months) - 3          # 最近两个月归档可能未发布, 强制走一次归档探测
        if bin_path.exists() and not is_current and not (force_recent and is_lag):
            continue
        rows = await fetch_archive(client, sym, iv, ym) if not is_current else []
        if rows:
            n_archives += 1
            buckets[ym] = rows
            write_bin(bin_path, rows)
        elif is_current:
            buckets[ym] = []                   # 当月由 API 补齐
        elif not bin_path.exists():
            print(f"  [{sym} {iv}] {ym} 无归档(未上线?), 跳过", flush=True)

    # ---- 尾部补齐: 从已落库最后一根到当前 ----
    manifests_tmp = sorted(out_dir.glob("*.bin"))
    last_t = 0
    if manifests_tmp:
        raw = manifests_tmp[-1].read_bytes()
        n_last = len(raw) // 48
        if n_last:
            buf = struct.unpack(f"<{n_last*6}d", raw[:n_last*48])
            last_t = int(buf[(n_last - 1) * 6])
    if last_t:
        tail = await fetch_api_range(client, sym, iv, last_t + sec, int(now.timestamp()))
        if tail:
            ym_tail = datetime.fromtimestamp(tail[-1][0], tz=timezone.utc).strftime("%Y-%m")
            merged = {r[0]: r for r in buckets.get(ym_tail, [])}
            merged.update({r[0]: r for r in tail})
            buckets[ym_tail] = [merged[k] for k in sorted(merged)]
            print(f"  [{sym} {iv}] API 补齐尾部 +{len(tail)} 根", flush=True)

    # ---- manifest: 扫描目录全部分片(含本次未动的历史片), 保证增量运行不丢片 ----
    # 同时产出每个分片的 .js 变体: file:// 协议下浏览器禁用 fetch,
    # 前端回退为 <script src=...js> 注入(与 data_*.js 同机制), 因此 .js 是 file:// 场景必需品。
    chunks = []
    total = 0
    for bin_path in sorted(out_dir.glob("*.bin")):
        n = bin_path.stat().st_size // 48
        if n == 0:
            bin_path.unlink()
            continue
        with open(bin_path, "rb") as fh:
            t0 = int(struct.unpack("<d", fh.read(8))[0])
            fh.seek(-48, 2)   # 最后一条记录的起始处(time 为记录首字段)
            t1 = int(struct.unpack("<d", fh.read(8))[0])
        js_path = bin_path.with_suffix(".js")
        if not js_path.exists() or force_recent:
            buf = struct.unpack(f"<{n*6}d", bin_path.read_bytes())
            rows = []
            for i in range(n):
                rows.append([int(buf[i*6])] + [float(x) for x in buf[i*6+1:(i+1)*6]])
            key = f"{sym}/{iv}/{bin_path.stem}"
            js_path.write_text(
                'window.__CHUNKS=window.__CHUNKS||{};window.__CHUNKS["' + key + '"]='
                + json.dumps(rows, separators=(",", ":")) + ';'
            )
        chunks.append({"file": bin_path.name, "t0": t0, "t1": t1, "n": n})
        total += n
    manifest = {"symbol": f"{sym}USDT", "interval": iv, "sec": sec, "total": total, "chunks": chunks}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (out_dir / "manifest.js").write_text(
        'window.__CHUNK_MANIFESTS=window.__CHUNK_MANIFESTS||{};window.__CHUNK_MANIFESTS["' + sym + '/' + iv + '"]='
        + json.dumps(manifest, separators=(",", ":")) + ';'
    )
    print(f"[{sym} {iv}] 分片 {len(chunks)} 个 · 共 {total:,} 根 · 归档更新 {n_archives} 个 → {out_dir.relative_to(REPO)}", flush=True)

    # ---- 可选主档 parquet(供未来笔计算用) ----
    if want_parquet:
        import pandas as pd
        frames = []
        for ch in chunks:
            raw = (out_dir / ch["file"]).read_bytes()
            n = len(raw) // 48
            buf = struct.unpack(f"<{n*6}d", raw)
            cols = [buf[c*6:(c+1)*6*n] for c in range(6)]
            frames.append(pd.DataFrame({
                "time": [int(x) for x in cols[0]],
                "open": cols[1], "high": cols[2], "low": cols[3], "close": cols[4], "volume": cols[5],
            }))
        df = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
        pq = PARQUET_DIR / f"{sym}USDT_{iv}.parquet"
        df.to_parquet(pq, index=False)
        print(f"[{sym} {iv}] 主档 {len(df):,} 根 → {pq.relative_to(REPO)}", flush=True)


def refilter_bins(sym: str, iv: str) -> None:
    """对已生成的分片做零成交K线剔除(本地重写 bin/js/manifest, 不重新下载)。"""
    out_dir = OUT_ROOT / sym / iv
    sec = IV_SECONDS[iv]
    total, chunks, dropped = 0, [], 0
    for bin_path in sorted(out_dir.glob("*.bin")):
        raw = bin_path.read_bytes()
        n = len(raw) // 48
        buf = struct.unpack(f"<{n*6}d", raw)
        kept = []
        for i in range(n):
            t = int(buf[i*6])
            v = buf[i*6+5]
            if v <= 0:
                dropped += 1
                continue
            kept.append((t, buf[i*6+1], buf[i*6+2], buf[i*6+3], buf[i*6+4], v))
        if not kept:
            bin_path.unlink()
            if bin_path.with_suffix(".js").exists():
                bin_path.with_suffix(".js").unlink()
            continue
        write_bin(bin_path, kept)
        # 同步重写 .js 变体
        rows_js = [[r[0]] + [float(x) for x in r[1:]] for r in kept]
        key = f"{sym}/{iv}/{bin_path.stem}"
        bin_path.with_suffix(".js").write_text(
            'window.__CHUNKS=window.__CHUNKS||{};window.__CHUNKS["' + key + '"]='
            + json.dumps(rows_js, separators=(",", ":")) + ';'
        )
        with open(bin_path, "rb") as fh:
            t0 = int(struct.unpack("<d", fh.read(8))[0])
            fh.seek(-48, 2)
            t1 = int(struct.unpack("<d", fh.read(8))[0])
        chunks.append({"file": bin_path.name, "t0": t0, "t1": t1, "n": len(kept)})
        total += len(kept)
    manifest = {"symbol": f"{sym}USDT", "interval": iv, "sec": sec, "total": total, "chunks": chunks}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (out_dir / "manifest.js").write_text(
        'window.__CHUNK_MANIFESTS=window.__CHUNK_MANIFESTS||{};window.__CHUNK_MANIFESTS["' + sym + '/' + iv + '"]='
        + json.dumps(manifest, separators=(",", ":")) + ';'
    )
    print(f"[refilter] {sym} {iv}: 剔除零量 {dropped:,} 根 → {total:,} 根 / {len(chunks)} 片", flush=True)


async def main_async(args) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for sym in args.symbols.split(","):
            sym = sym.strip().upper()
            if sym not in LISTING_MONTH:
                print(f"[skip] 未知符号 {sym}")
                continue
            for iv in args.intervals.split(","):
                iv = iv.strip()
                if iv not in IV_SECONDS:
                    print(f"[skip] 未知周期 {iv}")
                    continue
                await build_one(client, sym, iv, args.force_recent, args.parquet)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTC", help="逗号分隔: BTC,ETH,SOL,PEPE")
    parser.add_argument("--intervals", default="1m,15m", help="逗号分隔: 1m,15m")
    parser.add_argument("--parquet", action="store_true", help="同时生成 data/crypto 主档 parquet")
    parser.add_argument("--force-recent", action="store_true",
                        help="重下最近三个月的月度归档(默认只补当月)")
    parser.add_argument("--refilter", action="store_true",
                        help="对已生成分片做零成交K线剔除(本地重写, 不下载)")
    args = parser.parse_args()
    if args.refilter:
        for sym in args.symbols.split(","):
            sym = sym.strip().upper()
            if sym not in LISTING_MONTH:
                print(f"[skip] 未知符号 {sym}")
                continue
            for iv in args.intervals.split(","):
                iv = iv.strip()
                if iv in IV_SECONDS:
                    refilter_bins(sym, iv)
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
