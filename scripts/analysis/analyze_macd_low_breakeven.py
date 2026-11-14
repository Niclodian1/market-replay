#!/usr/bin/env python3
"""Frozen current-chart MACD two-price-low signals, initial stop, 1.5R -> entry stop.
Run export_macd_low_signals.mjs first. Does not modify production indicators.
"""
from __future__ import annotations
import argparse, collections, gzip, hashlib, json, math, sys
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from macd_low_paths import evaluate
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/macd-low-breakeven-2026-11-11'
TF_SECONDS={'1D':86400,'4H':14400,'1H':3600,'15F':900}

def iso(t):return datetime.fromtimestamp(t,timezone.utc).strftime('%Y-%m-%d %H:%M')
def compact(result):
    if result is None:return None
    for key,p in result['windows'].items():
        if key!='end':p.pop('cases',None)
    return result

class Refiner:
    def __init__(self,series,all_series):
        self.bars=series['bars'];self.seconds=TF_SECONDS[series['tf']]
        self.sources=[all_series[(series['sym'],tf)] for tf in ['15F','1H','4H']
            if (series['sym'],tf) in all_series and TF_SECONDS[tf]<self.seconds]
        self.cache={};self.by_source=collections.Counter();self.rejected=collections.Counter()
    def __call__(self,j):
        if j in self.cache:return self.cache[j]
        b=self.bars[j];t=b[0];end=t+self.seconds
        for source in self.sources:
            cs=source['bars'];sec=TF_SECONDS[source['tf']]
            if t<cs[0,0] or end>cs[-1,0]+sec:continue
            a=int(np.searchsorted(cs[:,0],t));z=int(np.searchsorted(cs[:,0],end))
            p=cs[a:z]
            if len(p)!=round(self.seconds/sec) or p[0,0]!=t or p[-1,0]+sec!=end or not np.all(np.diff(p[:,0])==sec):
                self.rejected['missing_subbars']+=1;continue
            aggregate=[p[0,1],p[:,2].max(),p[:,3].min(),p[-1,4]]
            if not np.allclose(aggregate,b[1:5],rtol=1e-6,atol=0):
                self.rejected['ohlc_mismatch']+=1;continue
            self.cache[j]=p;self.by_source[source['tf']]+=1;return p
        self.cache[j]=None
        return None

def pair_stat(paths,key):
    return [sum(p[key][k] for p in paths) for k in [0,1]]
def summarize(rows,horizon='60',compare=False):
    eligible=[r for r in rows if r['be']['windows'][horizon]['complete']]
    ps=[r['base' if compare else 'be']['windows'][horizon] for r in eligible]
    n=len(ps)
    if not n:return dict(n=0,total=len(rows))
    def med(key,q):return [float(np.quantile([p[key][k] for p in ps],q)) for k in [0,1]]
    mean=lambda key:[float(np.mean([p[key][k] for p in ps])) for k in [0,1]]
    return dict(n=n,total=len(rows),loss_stop=pair_stat(ps,'loss_stop'),breakeven_stop=pair_stat(ps,'breakeven_stop'),
        open=pair_stat(ps,'open'),negative_exit=pair_stat(ps,'negative_exit'),armed=pair_stat(ps,'armed'),
        short_sl={str(k):[sum(p['first_sl'][str(k)][i] for p in ps) for i in [0,1]] for k in [1,2,3,5,10,20]},
        short_be={str(k):[sum(p['first_be'][str(k)][i] for p in ps) for i in [0,1]] for k in [1,3,5,10,20]},
        targets={str(k):[sum(p['targets'][str(k)][i] for p in ps) for i in [0,1]] for k in [2,3,5,10]},
        mfe_R_median=med('mfe_R',.5),mfe_R_p75=med('mfe_R',.75),mfe_R_p90=med('mfe_R',.90),mfe_R_mean=mean('mfe_R'),
        mfe_pct_median=med('mfe_pct',.5),mfe_pct_mean=mean('mfe_pct'),pnl_R_marked_mean=mean('pnl_R'),
        uncertain_status=sum(len(p['statuses'])>1 for p in ps),uncertain_mfe=sum(p['mfe_R'][1]-p['mfe_R'][0]>1e-9 for p in ps))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stop',choices=['confirm','low'],default='low');args=parser.parse_args()
    all_series={}
    source=OUT/'series.jsonl'
    stream=source.open() if source.exists() else gzip.open(str(source)+'.gz','rt')
    for line in stream:
        r=json.loads(line);r['bars']=np.asarray(r['bars'],dtype=float);all_series[(r['sym'],r['tf'])]=r
    rows=[];series_summary=[]; exclusions=collections.Counter()
    for key,s in all_series.items():
        bars=s['bars'];ref=Refiner(s,all_series);own=[]
        for signal in s['signals']:
            i=signal['confirm'];low=signal['low']
            if i<59:exclusions['warmup_before_60']+=1;continue
            stop=float(bars[i if args.stop=='confirm' else low,3]);entry=float(bars[i,4])
            if not (entry>stop>0):exclusions['zero_or_invalid_risk']+=1;continue
            be=compact(evaluate(bars,i,stop,finer=ref))
            base=compact(evaluate(bars,i,stop,trigger_r=math.inf,finer=ref))
            row=dict(id=s['sym']+'|'+s['tf']+'|'+str(int(bars[i,0])),sym=s['sym'],name=s['name'],market=s['market'],tf=s['tf'],
                time=int(bars[i,0]),date=iso(bars[i,0]),low_time=int(bars[low,0]),low_date=iso(bars[low,0]),
                lag_bars=i-low,strength=signal['strength'],tier=signal['tier'],
                exact_equal_low=signal['lowPrice']==signal['referenceLow'],
                episode=s['sym']+'|'+s['tf']+'|'+str(int(bars[signal['segmentStart'],0])),
                be=be,base=base)
            own.append(row);rows.append(row)
        periods={}
        for label,since in [('all',-math.inf),('last5y',datetime(2021,11,11,tzinfo=timezone.utc).timestamp())]:
            rr=[r for r in own if r['time']>=since]
            observation_start=max(float(bars[59,0]),since) if len(bars)>=60 else float(bars[-1,0])
            days=max(0,(float(bars[-1,0])-observation_start)/86400)
            periods[label]=dict(signals=len(rr),episodes=len({r['episode'] for r in rr}),observed_years=days/365.2425,
                annual=len(rr)/(days/365.2425) if days else None,
                summary={h:summarize(rr,h) for h in ['20','60','120','end']},
                baseline={h:summarize(rr,h,True) for h in ['20','60','120','end']})
        series_summary.append(dict(sym=s['sym'],name=s['name'],market=s['market'],tf=s['tf'],raw_signals=len(s['signals']),bars=len(bars),
            first=iso(bars[0,0]),last=iso(bars[-1,0]),first_time=int(bars[0,0]),last_time=int(bars[-1,0]),periods=periods,
            refined_unique_bars=dict(ref.by_source),refinement_rejected=dict(ref.rejected)))
        print(s['sym'],s['tf'],'valid',len(own),'refined',dict(ref.by_source),flush=True)
    periods={}
    for label,since in [('all',-math.inf),('last5y',datetime(2021,11,11,tzinfo=timezone.utc).timestamp())]:
        periods[label]={}
        for tf in TF_SECONDS:
            rr=[r for r in rows if r['tf']==tf and r['time']>=since]
            periods[label][tf]={h:summarize(rr,h) for h in ['20','60','120','end']}
            periods[label][tf]['baseline']={h:summarize(rr,h,True) for h in ['20','60','120','end']}
    catalog=json.loads((OUT/'catalog-result.json').read_text())
    result=dict(analysis_date='2026-11-11',stop_basis=args.stop,entry_basis='confirmation_bar_close',breakeven_trigger_R=1.5,
        fee_pct=0,slippage_pct=0,engine_sha256=catalog['engineSha256'],source_hashes=catalog['series'],
        exclusions=dict(exclusions),summary=periods,series=series_summary,trades=rows,
        notes=['Signals use the current front-end bottomDivergence implementation with default minGap=3, lowTolerance=0, minStrength=0.',
          'The low marker is not the executable entry. Entry uses evidence.confirmIndex close; all checks start next bar.',
          'OHLC ambiguity is an envelope over feasible orderings and revisits. Subbars are used only if complete and matching parent OHLC.',
          'Fixed-window summaries require the entire window to be present, even if a recent trade already stopped.',
          'Independent opportunity study; overlapping trades are not a capital-weighted portfolio. MFE is not realized profit.',
          'Chart lowTolerance=0 still accepts exactly equal lows; those are retained and flagged.',
          'All history includes current listed symbols and different histories. No survivorship correction or out-of-sample validation is claimed.'])
    target=OUT/('results-'+args.stop+'.json');target.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)))
    print('SAVED',target,'trades',len(rows),'exclusions',dict(exclusions),flush=True)

if __name__=='__main__':main()
