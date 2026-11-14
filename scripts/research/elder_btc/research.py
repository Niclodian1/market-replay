#!/usr/bin/env python3
"""Frozen book-derived study. Select using <=2024 only, then open the test set once."""
from __future__ import annotations
import argparse,hashlib,json,math
from dataclasses import asdict,replace
from pathlib import Path
import numpy as np
import pandas as pd
from strategy import Config,indicators,backtest,metrics,candidates,ablations

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'reports/elder-btc-1d-2026-11-12'
PROTOCOL=json.loads((HERE/'protocol.json').read_text())
LABELS={
 'value_raw':'价值区回调', 'value_force':'回调＋强力指数', 'value_impulse':'回调＋动力禁入', 'value_full':'回调＋强力＋动力',
 'false_break':'向下假突破', 'false_break_div':'假突破＋MACD背离','channel':'平缓通道反转','impulse_direct':'直接绿买非绿卖',
 'kangaroo':'袋鼠尾','rsi_pullback':'趋势＋RSI回升30','stoch_pullback':'趋势＋随机指标回升30','macd_cross':'MACD金叉死叉',
 'triple_weekly_reference':'原框架周线＋日线参照','value_no_trend':'去掉EMA趋势过滤','value_limit_proxy':'平均EMA下穿触价入场',
 'value_no_new_force_low':'强力不创新低过滤','value_volume':'增加放量过滤','value_adx':'增加ADX过滤','value_nick':'次低点止损',
 'value_safezone':'安全区域止损','value_atr_stop':'初始2ATR止损','value_no_trail':'去掉ATR移动止损','value_no_target':'去掉通道止盈',
 'value_target_ema':'改为EMA止盈','value_close_be':'增加收盘1.5R移保本','value_no_month_guard':'去掉6%月风险限制','value_risk_2pct':'单笔风险提高至2%',
 'false_break_strong_div':'背离限定20～40根且第二谷≤半深','buy_hold':'买入并持有','ema_trend_control':'仅EMA26顺势＋同风险管理'}

CONTRASTS=[('动力禁入','value_full','value_force'),('强力指数','value_full','value_impulse'),('强力与动力组合','value_full','value_raw'),
 ('EMA趋势过滤','value_full','value_no_trend'),('平均穿透入场','value_limit_proxy','value_full'),
 ('强力不创新低','value_no_new_force_low','value_full'),('放量过滤','value_volume','value_full'),('ADX过滤','value_adx','value_full'),
 ('次低点止损','value_nick','value_full'),('安全区域止损','value_safezone','value_full'),('初始2ATR止损','value_atr_stop','value_full'),
 ('ATR移动止损','value_full','value_no_trail'),('放开通道止盈','value_no_target','value_full'),('改为EMA止盈','value_target_ema','value_full'),
 ('收盘1.5R移保本','value_close_be','value_full'),('6%月风险限制','value_full','value_no_month_guard')]

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load():
    d=pd.read_parquet(ROOT/PROTOCOL['data']);d=d.rename(columns={'open_time':'date'});d=d[['date','open','high','low','close','volume']].copy()
    d['date']=pd.to_datetime(d.date,utc=True)
    assert len(d)==len(d.date.unique()) and d.date.is_monotonic_increasing
    assert (d.date.diff().dropna()==pd.Timedelta(days=1)).all()
    assert (d[['open','high','low','close']]>0).all().all() and (d.volume>=0).all()
    assert (d.high>=d[['open','low','close']].max(axis=1)).all() and (d.low<=d[['open','high','close']].min(axis=1)).all()
    assert np.isfinite(d[['open','high','low','close','volume']].to_numpy()).all()
    return d
def run(d,cfg,segment,ordering='adverse'):
    z=backtest(d,cfg,*PROTOCOL[segment],ordering=ordering);z['metrics']=metrics(z);return z
def complexity(c):return sum([c.trend,c.force,c.impulse,c.divergence,c.volume,c.adx,c.force_extreme,c.strong_div])
def eligible(m,v,positive):
    return m['trades']>=20 and v['trades']>=8 and (m['profit_factor'] or 0)>1 and (v['profit_factor'] or 0)>1 and m['sharpe']>0 and v['sharpe']>0 and m['max_drawdown']>=-35 and v['max_drawdown']>=-25 and positive>=3
def buy_hold(d,segment):
    ix=(d.date>=pd.Timestamp(PROTOCOL[segment][0],tz='UTC'))&(d.date<=pd.Timestamp(PROTOCOL[segment][1],tz='UTC'))
    a=d.loc[ix];cfg=Config();qty=100000/(float(a.open.iloc[0])*(1+cfg.slip)*(1+cfg.fee));e=(qty*a.close).to_numpy()
    e[-1]*=(1-cfg.slip)*(1-cfg.fee);rs=np.diff(np.r_[100000.,e])/np.r_[100000.,e[:-1]]
    initial=100000.;pnl=e[-1]-initial
    z=dict(config={'name':'buy_hold'},dates=[str(x.date()) for x in a.date],equity=e.tolist(),returns=rs.tolist(),exposure=[1.]*len(a),
        trades=[dict(pnl=float(pnl),R=float(pnl/initial),hold=len(a),reason='boundary_liquidation')],ambiguous_bars=0,blocked_orders=0)
    z['metrics']=metrics(z);return z

def select():
    OUT.mkdir(exist_ok=True,parents=True);raw=load();d=indicators(raw)
    configs=candidates()+ablations();study={};ranking=[]
    for cfg in configs:
        tr=run(d,cfg,'train');va=run(d,cfg,'validation')
        folds=[]
        for year in range(2019,2023):
            z=backtest(d,cfg,f'{year}-01-01',f'{year}-12-31');folds.append({'year':year,**metrics(z)})
        m=tr['metrics'];v=va['metrics'];positive=sum(f['total_return']>0 for f in folds)
        score=min(m['sharpe'],v['sharpe'])-.05*complexity(cfg)
        ok=eligible(m,v,positive) and cfg.name!='triple_weekly_reference' and cfg in candidates()
        study[cfg.name]=dict(label=LABELS[cfg.name],config=asdict(cfg),train=tr,validation=va,folds=folds,eligible=ok,score=score)
        if cfg in candidates() and cfg.name!='triple_weekly_reference':ranking.append(dict(name=cfg.name,eligible=ok,score=score,complexity=complexity(cfg),enough=m['trades']>=20 and v['trades']>=8))
        print(cfg.name,'train',m['trades'],round(m['sharpe'],2),'val',v['trades'],round(v['sharpe'],2),'positiveYears',positive,'eligible',ok,flush=True)
    ranking.sort(key=lambda r:(-r['score'],r['complexity'],r['name']))
    choices=[r for r in ranking if r['eligible']];enough=[r for r in ranking if r['enough']]
    research=(choices or enough or ranking)[0]['name'];selected=choices[0]['name'] if choices else 'CASH'
    selection=dict(analysis_date='2026-11-12',selected=selected,research_candidate=research,ranking=ranking,
        config=study[research]['config'],protocol=PROTOCOL,protocol_sha256=sha(HERE/'protocol.json'),strategy_sha256=sha(HERE/'strategy.py'),
        source_sha256=sha(ROOT/PROTOCOL['data']),source_first=str(raw.date.iloc[0]),source_last=str(raw.date.iloc[-1]),bars=len(raw),
        book=json.loads((OUT/'book-manifest.json').read_text())['sha256'],
        state='FROZEN_BEFORE_TEST_EVALUATION',contrasts=CONTRASTS)
    save('training.json',study);save('selection.json',selection)
    (OUT/'frozen_strategy.py').write_text((HERE/'strategy.py').read_text())
    save('final_config.json',selection['config'])
    print('FROZEN SELECTED',selected,'RESEARCH',research,flush=True)

def holm(p):
    p=np.asarray(p);out=np.empty(len(p));order=np.argsort(p);last=0
    for j,i in enumerate(order):last=max(last,min(1.,(len(p)-j)*p[i]));out[i]=last
    return out
def bootstrap(matrix,benchmark,B=4000,block=20):
    """Common circular blocks preserve paired daily differences and serial dependence.

    Exploratory finite-history intervals, not guarantees under regime nonstationarity.
    Alpha bootstrap re-estimates the BTC regression beta in each paired resample.
    """
    rng=np.random.default_rng(20261112);n,k=matrix.shape;means=[];alphas=[]
    mu=matrix.mean(axis=0);bm=benchmark.mean();bv=np.var(benchmark)
    beta=((matrix*benchmark[:,None]).mean(axis=0)-mu*bm)/bv
    alpha=mu-beta*bm
    for offset in range(0,B,100):
        b=min(100,B-offset);starts=rng.integers(0,n,size=(b,math.ceil(n/block)))
        ix=((starts[:,:,None]+np.arange(block))%n).reshape(b,-1)[:,:n]
        xx=matrix[ix];bb=benchmark[ix];mm=xx.mean(axis=1);mb=bb.mean(axis=1);vb=(bb*bb).mean(axis=1)-mb*mb
        bt=((xx*bb[:,:,None]).mean(axis=1)-mm*mb[:,None])/vb[:,None]
        means.append(mm);alphas.append(mm-bt*mb[:,None])
    means=np.concatenate(means);alphas=np.concatenate(alphas)
    p=((means-mu[None,:]>=mu[None,:]).sum(axis=0)+1)/(B+1)
    pa=((alphas-alpha[None,:]>=alpha[None,:]).sum(axis=0)+1)/(B+1)
    return dict(mean=mu,beta=beta,alpha=alpha,means=means,alphas=alphas,p=p,p_alpha=pa)

def test():
    frozen=json.loads((OUT/'selection.json').read_text())
    assert frozen['strategy_sha256']==sha(HERE/'strategy.py') and frozen['protocol_sha256']==sha(HERE/'protocol.json')
    assert frozen['source_sha256']==sha(ROOT/PROTOCOL['data'])
    raw=load();d=indicators(raw);configs=candidates()+ablations();study=json.loads((OUT/'training.json').read_text())
    names=[c.name for c in configs];test_results={}
    for cfg in configs:
        z=run(d,cfg,'test');study[cfg.name]['test']=z;test_results[cfg.name]=z
    bh={seg:buy_hold(d,seg) for seg in ['train','validation','test']}
    control=replace(Config(),name='ema_trend_control',family='trend',trend=True,force=False,impulse=False,chase_guard=False,entry='market',target='none')
    controls={seg:run(d,control,seg) for seg in ['train','validation','test']}
    matrix=np.array([test_results[n]['returns'] for n in names]).T;br=np.array(bh['test']['returns'])
    boot=bootstrap(matrix,br);p_adj=holm(boot['p']);pa_adj=holm(boot['p_alpha'])
    inference={}
    for j,name in enumerate(names):
        inference[name]=dict(mean_daily=float(boot['mean'][j]),mean_daily_CI95=np.quantile(boot['means'][:,j],[.025,.975]).tolist(),
            mean_p=float(boot['p'][j]),mean_p_holm=float(p_adj[j]),beta=float(boot['beta'][j]),alpha_daily=float(boot['alpha'][j]),
            alpha_daily_CI95=np.quantile(boot['alphas'][:,j],[.025,.975]).tolist(),alpha_p_holm=float(pa_adj[j]),
            positive_return_evidence=bool(p_adj[j]<.05 and test_results[name]['metrics']['trades']>=20),
            timing_alpha_evidence=bool(pa_adj[j]<.05 and test_results[name]['metrics']['trades']>=20))
    contrasts=[];p=[]
    for label,lhs,rhs in CONTRASTS:
        a=names.index(lhs);b=names.index(rhs);obs=boot['mean'][a]-boot['mean'][b]
        samples=boot['means'][:,a]-boot['means'][:,b]
        pv=(1+np.sum(np.abs(samples-obs)>=abs(obs)))/(len(samples)+1)
        p.append(float(pv));contrasts.append(dict(rule=label,lhs=lhs,rhs=rhs,daily_delta=float(obs),CI95=np.quantile(samples,[.025,.975]).tolist(),p=float(pv)))
    for r,pv in zip(contrasts,holm(p)):r['p_holm']=float(pv)
    research=Config(**frozen['config']);sensitivity=[]
    for slip in [0,.00025,.0005,.001,.002]:
        cfg=replace(research,slip=slip);z=run(d,cfg,'test');sensitivity.append(dict(kind='slippage',value=slip,metrics=z['metrics']))
    for scale in [.8,1.2]:
        cfg=replace(research,fast=round(13*scale),slow=round(26*scale));dd=indicators(raw,cfg);z=run(dd,cfg,'test');sensitivity.append(dict(kind='ema_scale',value=scale,metrics=z['metrics']))
    for factor in [1.5,2.5]:
        z=run(d,replace(research,trail_atr=factor),'test');sensitivity.append(dict(kind='trail_atr',value=factor,metrics=z['metrics']))
    best=run(d,research,'test',ordering='favorable');sensitivity.append(dict(kind='daily_order',value='favorable',metrics=best['metrics']))
    # Yearly held-out display; diagnostic only, never a new selector.
    yearly=[]
    for year,end in [(2025,'2025-12-31'),(2026,PROTOCOL['test'][1])]:
        z=backtest(d,research,f'{year}-01-01',end);yearly.append(dict(year=year,**metrics(z)))
    summary=dict(analysis_date='2026-11-12',selection=frozen,labels=LABELS,protocol=PROTOCOL,inference=inference,contrasts=contrasts,
        sensitivity=sensitivity,yearly=yearly,models={name:{'label':r['label'],'config':r['config'],'eligible':r['eligible'],'score':r['score'],
            **{seg:r[seg]['metrics'] for seg in ['train','validation','test']},'folds':r['folds']} for name,r in study.items()},
        buy_hold={seg:r['metrics'] for seg,r in bh.items()},ema_control={seg:r['metrics'] for seg,r in controls.items()})
    save('summary.json',summary);save('all_results.json',study);save('benchmarks.json',{'buy_hold':bh,'ema_control':controls})
    pd.DataFrame([dict(name=n,segment=seg,**study[n][seg]['metrics']) for n in names for seg in ['train','validation','test']]).to_json(OUT/'metrics.json',orient='records',force_ascii=False)
    for name in [research.name,'value_full','false_break_div']:
        save(name+'-test-trades.json',test_results[name]['trades'])
    print('TEST COMPLETE',research.name,json.dumps(test_results[research.name]['metrics'],ensure_ascii=False),flush=True)
    print('QUALIFIED SIGNIFICANT MEAN',[n for n,r in inference.items() if r['positive_return_evidence']],flush=True)
    print('QUALIFIED SIGNIFICANT ALPHA',[n for n,r in inference.items() if r['timing_alpha_evidence']],flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['select','test'],required=True);a=parser.parse_args()
    select() if a.stage=='select' else test()
