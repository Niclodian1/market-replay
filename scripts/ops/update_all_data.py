#!/usr/bin/env python3
"""更新静态站全部39个品种、分时分片及图表产物；失败逐项报告，保留旧数据。

python scripts/ops/update_all_data.py [--fetch-only | --skip-fetch] [--skip-intraday] [--workers 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'apps' / 'chanlun-web'))
import build_all_symbols as builder
import update_daily_data as daily

BINANCE = 'https://data-api.binance.vision/api/v3/klines'
TF = {'1day': ('1d', 86400), '4h': ('4h', 14400), '1h': ('1h', 3600)}
COLUMNS = ['time', 'open', 'high', 'low', 'close', 'volume']


def atomic_frame(frame, path):
    if frame.empty or frame.time.duplicated().any() or not frame.time.is_monotonic_increasing:
        raise ValueError(f'Invalid or empty timeseries: {path}')
    numeric=frame[['open','high','low','close','volume']]
    if numeric.isna().any().any() or (numeric[['open','high','low','close']]<=0).any().any():
        raise ValueError(f'Invalid OHLCV: {path}')
    if ((frame.high+1e-7<frame[['open','close','low']].max(axis=1)) | (frame.low-1e-7>frame[['open','close','high']].min(axis=1)) | (frame.volume<0)).any():
        raise ValueError(f'Invalid candle bounds: {path}')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.parquet.tmp')
    frame.to_parquet(temporary,index=False)
    temporary.replace(path)


def merge_frame(old, fresh):
    return pd.concat([old,fresh],ignore_index=True).drop_duplicates('time',keep='last').sort_values('time').reset_index(drop=True)


def merge_chart_history(old, fresh, seconds):
    """补齐平台主档不能改写图表早期时间口径，否则旧标注会错位。"""
    fresh_chart=fresh[fresh.time>=int(old.time.max())-2*seconds]
    return merge_frame(old,fresh_chart)


def fetch_json(session, url, **kwargs):
    for attempt in range(3):
        try:
            response=session.request('POST' if 'json' in kwargs else 'GET',url,timeout=30,**kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException,ValueError):
            if attempt==2:raise
            time.sleep(1+attempt)


def update_crypto(symbol, cutoff):
    session=requests.Session()
    for tf,(interval,seconds) in TF.items():
        path=REPO/'data'/'crypto'/f'{symbol}_USD_{tf}.parquet'
        old=pd.read_parquet(path)
        start=int(old.time.max())-seconds
        alias=REPO/'data'/'crypto'/f'{symbol}USDT_{interval}.parquet'
        alias_old=None
        if alias.exists():
            alias_old=pd.read_parquet(alias)
            start=min(start,int(alias_old.open_time.max().timestamp())-seconds)
        cursor=start*1000
        rows=[]
        while cursor<cutoff*1000:
            if symbol=='HYPE':
                batch=fetch_json(session,'https://api.hyperliquid.xyz/info',json={
                    'type':'candleSnapshot','req':{'coin':'HYPE','interval':interval,'startTime':cursor,
                    'endTime':min(cutoff*1000,cursor+seconds*1000*4500)}})
                if not isinstance(batch,list):raise ValueError(str(batch)[:200])
                parsed=[[int(k['t']),k['o'],k['h'],k['l'],k['c'],k['v'],int(k['T'])] for k in batch]
            else:
                parsed=fetch_json(session,BINANCE,params={'symbol':symbol+'USDT','interval':interval,
                    'startTime':cursor,'endTime':cutoff*1000-1,'limit':1000})
                if not isinstance(parsed,list):raise ValueError(str(parsed)[:200])
            if not parsed:break
            rows.extend(k for k in parsed if int(k[0])//1000+seconds<=cutoff)
            next_cursor=int(parsed[-1][0])+seconds*1000
            if next_cursor<=cursor:raise ValueError('Kline pagination did not advance')
            cursor=next_cursor
            if symbol!='HYPE' and len(parsed)<1000:break
        if not rows:raise ValueError(f'{symbol} {tf}: no recent closed candles returned')
        fresh=pd.DataFrame([[int(k[0])//1000,*map(float,k[1:6])] for k in rows],columns=COLUMNS)
        # API主档可能落后数月，不能把它的补齐区间整段覆盖到另一份历史时间口径。
        # 图表历史时间戳保持不变，避免旧标注错位或同日出现两根日线。
        merged=merge_chart_history(old,fresh,seconds)
        merged=merged[merged.time+seconds<=cutoff].reset_index(drop=True)
        if int(merged.time.max())+seconds<cutoff-seconds:raise ValueError(f'{symbol} {tf}: source remains stale')
        atomic_frame(merged,path)
        if alias_old is not None:
            # 保留 API 主档的扩展字段与 UTC 时间列，避免平台仍停在4月。
            raw=pd.DataFrame({
                'open_time':pd.to_datetime([int(k[0]) for k in rows],unit='ms',utc=True),
                **{column:[float(k[i]) for k in rows] for i,column in enumerate(COLUMNS[1:],1)},
                'close_time':pd.to_datetime([int(k[6]) for k in rows],unit='ms',utc=True),
                'quote_volume':[float(k[7]) for k in rows], 'trades':[int(k[8]) for k in rows],
                'taker_buy_base':[float(k[9]) for k in rows], 'taker_buy_quote':[float(k[10]) for k in rows],
            })
            result=pd.concat([alias_old,raw],ignore_index=True).drop_duplicates('open_time',keep='last').sort_values('open_time')
            result=result[result.open_time.astype('int64')//10**9+seconds<=cutoff]
            temporary=alias.with_suffix('.parquet.tmp');result.to_parquet(temporary,index=False);temporary.replace(alias)
        print(f'[crypto] {symbol} {tf}: {len(old)} -> {len(merged)}, latest {pd.to_datetime(int(merged.time.max()),unit="s",utc=True)}',flush=True)
    session.close()


def backup(report_dir):
    with zipfile.ZipFile(report_dir/'backup.zip','w',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        paths=list((REPO/'data').rglob('*.parquet'))
        paths+=list((REPO/'apps'/'chanlun-web'/'data-view').glob('*.js'))
        paths+=list((REPO/'apps'/'chanlun-web'/'intraday').rglob('*'))
        paths+=[REPO/'experiments'/'ma_a_atr_entry'/'manifest.csv']
        for path in paths:
            if path.is_file():archive.write(path,path.relative_to(REPO))
    print(f'[backup] {report_dir / "backup.zip"}',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-only',action='store_true')
    parser.add_argument('--skip-fetch',action='store_true')
    parser.add_argument('--skip-intraday',action='store_true')
    parser.add_argument('--skip-metals',action='store_true',help='保留贵金属原数据，不请求其他来源')
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    now=datetime.now(timezone.utc);cutoff=int(now.timestamp())
    report_dir=REPO/'reports'/f'data-update-{now:%Y%m%d-%H%M%S}'
    report_dir.mkdir(parents=True,exist_ok=True)
    failures=[]
    if not args.skip_fetch:
        backup(report_dir)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(update_crypto,symbol,cutoff):symbol for symbol in ['BTC','ETH','SOL','PEPE','HYPE']}
            for job in as_completed(jobs):
                try:job.result()
                except Exception as exc:failures.append({'stage':'crypto','symbol':jobs[job],'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
        for panels,subdir in [(daily.STOCK_PANELS,'stocks'),(daily.CN_PANELS,'cn_equity'),(daily.HK_PANELS,'hk_equity'),(daily.INDEX_PANELS,'indices')]:
            for panel,(symbol,*_) in panels.items():
                try:daily.update_stock(panel,symbol,subdir=subdir)
                except Exception as exc:failures.append({'stage':'daily','symbol':symbol,'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
        for panel in daily.CN_INDEX_PANELS:
            try:daily.update_cn_index(panel)
            except Exception as exc:failures.append({'stage':'index','symbol':panel,'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
        for panel in ([] if args.skip_metals else daily.METAL_PANELS):
            try:daily.update_metal(panel)
            except Exception as exc:failures.append({'stage':'metal','symbol':panel,'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
        daily.rebuild_manifest()
    if not args.fetch_only:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            jobs={pool.submit(builder.build,*panel):panel[0] for panel in builder.PANELS}
            for job in as_completed(jobs):
                try:job.result()
                except Exception as exc:failures.append({'stage':'build','symbol':jobs[job],'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
        if not args.skip_intraday:
            import asyncio
            import build_intraday_chunks as intraday
            async def chunks():
                import httpx
                async with httpx.AsyncClient(timeout=30,follow_redirects=True) as client:
                    for symbol in ['BTC','ETH','SOL','PEPE']:
                        try:await intraday.build_one(client,symbol,'15m',False,False)
                        except Exception as exc:failures.append({'stage':'15m','symbol':symbol,'error':str(exc)});print('[FAILED]',failures[-1],flush=True)
            asyncio.run(chunks())
    (report_dir/'result.json').write_text(json.dumps({'startedAt':now.isoformat(),'cutoff':cutoff,'failures':failures},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'[report] {report_dir / "result.json"}',flush=True)
    if failures:raise SystemExit(1)


if __name__=='__main__':
    main()
