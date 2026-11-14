"""Causal, single-timeframe translations of Elder's toolbox, not a verbatim book robot.

Run research.py for the frozen chronological study. All orders on bar t use only
information available at close t-1. This module never submits live orders.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Config:
    name: str = 'value_full'
    family: str = 'value'
    trend: bool = True
    force: bool = True
    impulse: bool = True
    divergence: bool = False
    entry: str = 'stop'   # stop above prior high; market next open; touch below EMA
    stop: str = 'structure'  # 5-bar low minus .1 ATR
    trail: bool = True
    target: str = 'upper'    # previous completed bar envelope, EMA, or none
    be: bool = False
    month_guard: bool = True
    volume: bool = False
    adx: bool = False
    force_extreme: bool = False
    strong_div: bool = False
    chase_guard: bool = True
    risk: float = .01
    fee: float = .0005
    slip: float = .0005
    fast: int = 13
    slow: int = 26
    atr: int = 14
    envelope: int = 100
    support: int = 20
    buffer: float = .1
    trail_atr: float = 2.0
    max_hold: int = 0


def ema(x, n):
    """SMA-seeded EMA, ignoring only leading NaNs; no future backfill."""
    a=np.asarray(x,dtype=float);out=np.full(len(a),np.nan)
    finite=np.flatnonzero(np.isfinite(a))
    if not len(finite):return out
    start=int(finite[0])+n-1
    if start>=len(a):return out
    out[start]=np.mean(a[start-n+1:start+1]);alpha=2/(n+1)
    for i in range(start+1,len(a)):out[i]=alpha*a[i]+(1-alpha)*out[i-1]
    return out


def wilder(x,n):
    a=np.asarray(x,dtype=float);out=np.full(len(a),np.nan);valid=np.flatnonzero(np.isfinite(a))
    if not len(valid):return out
    k=int(valid[0])+n-1
    if k>=len(a):return out
    out[k]=np.mean(a[k-n+1:k+1])
    for i in range(k+1,len(a)):out[i]=(out[i-1]*(n-1)+a[i])/n
    return out


def indicators(data,cfg=Config()):
    d=data.copy();o,h,l,c,v=[d[k].to_numpy(float) for k in ['open','high','low','close','volume']]
    n=len(d); e13=ema(c,cfg.fast);e26=ema(c,cfg.slow)
    dif=ema(c,12)-ema(c,26);hist=dif-ema(dif,9)
    d['fast']=e13;d['slow']=e26;d['hist']=hist;d['dif']=dif
    delta=np.r_[np.nan,np.diff(c)];tr=np.maximum(h-l,np.maximum(np.abs(h-np.r_[c[0],c[:-1]]),np.abs(l-np.r_[c[0],c[:-1]])))
    atr=wilder(tr,cfg.atr);d['atr']=atr
    d['force2']=ema(delta*v,2);d['force13']=ema(delta*v,13)
    up=np.r_[False,np.diff(e13)>0];hu=np.r_[False,np.diff(hist)>0]
    down=np.r_[False,np.diff(e13)<0];hd=np.r_[False,np.diff(hist)<0]
    d['color']=np.where(up&hu,1,np.where(down&hd,-1,0))
    d['trend']=np.r_[False,np.diff(e26)>0]
    dev=np.maximum(np.abs(h/e26-1),np.abs(l/e26-1))
    q=pd.Series(dev).rolling(cfg.envelope,min_periods=cfg.envelope).quantile(.95).to_numpy()
    d['upper']=e26*(1+q);d['lower']=e26*(1-q)
    d['support']=pd.Series(l).shift(1).rolling(cfg.support).min().to_numpy()
    d['resistance']=pd.Series(h).shift(1).rolling(cfg.support).max().to_numpy()
    d['low5']=pd.Series(l).rolling(5).min().to_numpy()
    d['nick']=pd.Series(l).rolling(5).apply(lambda a:np.partition(a,1)[1],raw=True).to_numpy()
    penetration=np.maximum(e13-l,0);pen=pd.Series(np.where(penetration>0,penetration,np.nan))
    d['penetration']=pen.rolling(20,min_periods=1).mean().to_numpy()
    noise=np.maximum(np.r_[l[0],l[:-1]]-l,0);ns=pd.Series(np.where(noise>0,noise,np.nan))
    d['safezone']=np.r_[np.nan,l[:-1]]-2*ns.rolling(20,min_periods=1).mean().to_numpy()
    d['volume_ratio']=v/pd.Series(v).shift(1).rolling(14).mean().to_numpy()
    gains=wilder(np.maximum(delta,0),14);losses=wilder(np.maximum(-delta,0),14)
    d['rsi']=np.divide(100*gains,gains+losses,out=np.full(n,50.),where=(gains+losses)>0)
    low14=pd.Series(l).rolling(14).min().to_numpy();high14=pd.Series(h).rolling(14).max().to_numpy()
    k=np.divide(100*(c-low14),high14-low14,out=np.full(n,np.nan),where=(high14-low14)>0)
    d['stoch']=pd.Series(k).rolling(3).mean().to_numpy()
    pdm=np.maximum(np.r_[0,np.diff(h)],0);mdm=np.maximum(np.r_[0,-np.diff(l)],0)
    plus=wilder(np.where(pdm>mdm,pdm,0),14)/atr;minus=wilder(np.where(mdm>pdm,mdm,0),14)/atr
    dx=np.divide(100*np.abs(plus-minus),plus+minus,out=np.full(n,np.nan),where=plus+minus>0)
    d['adx']=wilder(dx,14)
    # Completed opposite-signed MACD episodes only. Current episode uses its prefix.
    div=np.zeros(n,bool);gap=np.full(n,np.nan);depth=np.full(n,np.nan)
    prev=None;current=None
    for i in range(n):
        if not np.isfinite(hist[i]):continue
        if hist[i]>=0:
            if current is not None:prev=current;current=None
            continue
        if current is None:current={'price':l[i],'hist':hist[i],'bottom':i,'start':i}
        if l[i]<current['price']:current['price']=l[i]
        if hist[i]<current['hist']:current['hist']=hist[i];current['bottom']=i
        if prev is not None:
            gap[i]=current['bottom']-prev['bottom'];depth[i]=current['hist']/prev['hist']
            div[i]=current['price']<prev['price'] and current['hist']>prev['hist'] and i>0 and hist[i]>hist[i-1]
    d['divergence']=div;d['div_gap']=gap;d['div_depth']=depth
    # Completed W-SUN bar becomes available only at Sunday's daily close.
    weekly=d.set_index('date')[['open','high','low','close','volume']].resample('W-SUN',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    wc=weekly.close.to_numpy();we=ema(wc,13);wh=ema(wc,12)-ema(wc,26);wh=wh-ema(wh,9)
    color=np.where((np.r_[False,np.diff(we)>0])&(np.r_[False,np.diff(wh)>0]),1,np.where((np.r_[False,np.diff(we)<0])&(np.r_[False,np.diff(wh)<0]),-1,0))
    weekly['color']=color;weekly['ready']=np.isfinite(we)&np.isfinite(wh)
    # last incomplete week's label is later than the final daily timestamp; never visible.
    aligned=weekly[['color','ready']].reindex(pd.DatetimeIndex(d.date),method='ffill')
    d['weekly_color']=aligned.color.to_numpy();d['weekly_ready']=aligned.ready.fillna(False).to_numpy()
    return d


def make_signals(d,cfg):
    """Signal at close t -> an order valid for t+1 only. Return order plan arrays."""
    n=len(d);a={k:d[k].to_numpy() for k in d.columns if k!='date'}
    c,h,l,atr=a['close'],a['high'],a['low'],a['atr'];fast,slow=a['fast'],a['slow']
    trend=a['trend'].astype(bool)
    base=np.isfinite(a['upper'])&np.isfinite(atr)&(np.arange(n)>=200)
    family=cfg.family
    if family in ['value','weekly']:
        setup=(l<=np.maximum(fast,slow))&(h>=np.minimum(fast,slow))&(c>=np.minimum(fast,slow))
        if cfg.force:setup &= a['force2']<0
        if cfg.trend:setup &= trend
    elif family=='falsebreak':
        setup=(l<a['support']-cfg.buffer*atr)&(c>a['support'])
        if cfg.divergence:setup &= a['divergence'].astype(bool)
        if cfg.strong_div:setup &= (a['div_gap']>=20)&(a['div_gap']<=40)&(a['div_depth']<=.5)
    elif family=='channel':
        previous_lower=np.r_[np.nan,a['lower'][:-1]]
        setup=(l<previous_lower)&(c>previous_lower)&(np.abs(slow-np.r_[np.nan,slow[:-1]])<=.1*atr)
    elif family=='impulse':
        setup=(a['color']==1)&(np.r_[0,a['color'][:-1]]!=1)
    elif family=='rsi':
        setup=(a['rsi']>30)&(np.r_[np.nan,a['rsi'][:-1]]<=30)&trend
    elif family=='stoch':
        setup=(a['stoch']>30)&(np.r_[np.nan,a['stoch'][:-1]]<=30)&trend
    elif family=='macd':
        setup=(a['hist']>0)&(np.r_[np.nan,a['hist'][:-1]]<=0)
    elif family=='trend':
        setup=trend.copy()
    elif family=='kangaroo':
        ranges=h-l;old=np.r_[np.nan,ranges[:-1]];oldatr=np.r_[np.nan,atr[:-1]]
        setup=(old>=2*oldatr)&(ranges<=old/2)&(np.r_[np.nan,np.nan,ranges[:-2]]<=old/2)&(l>np.r_[np.nan,l[:-1]]+old/2)
    else:raise ValueError(family)
    if cfg.impulse:setup &= a['color']!=-1
    if family=='weekly':setup &= a['weekly_ready'].astype(bool)&(a['weekly_color']!=-1)
    if cfg.volume:setup &= a['volume_ratio']>=1.25
    if cfg.adx:setup &= (a['adx']>20)&(a['adx']>np.r_[np.nan,a['adx'][:-1]])
    if cfg.force_extreme:setup &= a['force2']>pd.Series(a['force2']).shift(1).rolling(20).min().to_numpy()
    setup &= base
    price=h+.01
    if cfg.entry=='market':price=c.copy() # actual entry is next open, not this close
    if cfg.entry=='touch':price=2*fast-np.r_[np.nan,fast[:-1]]-a['penetration']
    stop=a['low5']-cfg.buffer*atr
    if cfg.stop=='nick':stop=a['nick']-.01
    if cfg.stop=='atr':stop=l-2*atr
    if cfg.stop=='safezone':stop=a['safezone']
    if family=='kangaroo':stop=np.r_[np.nan,l[:-1]]+np.r_[np.nan,(h-l)[:-1]]/2
    target=a['upper'] if cfg.target=='upper' else slow if cfg.target=='ema' else np.full(n,np.inf)
    if family=='impulse':exit_signal=a['color']!=1
    elif family=='macd':exit_signal=a['hist']<0
    elif family=='trend':exit_signal=~trend
    elif family=='weekly':exit_signal=(a['color']==-1)|(a['weekly_color']==-1)
    else:exit_signal=(a['color']==-1)|~trend
    return dict(setup=setup,price=price,stop=stop,target=target,exit=exit_signal)


def backtest(d,cfg,start,end,ordering='adverse'):
    """Unlevered cash+BTC, one position, costs on both entry/exit, daily close NAV.

    Bars are complete UTC days. Pending orders and ratchets use yesterday's data.
    When daily OHLC cannot order stop/target or stop-entry/stop, adverse is primary.
    Boundary liquidation at the specified end close is explicit and costs money.
    """
    signals=make_signals(d,cfg);dates=d.date;idx=np.flatnonzero((dates>=pd.Timestamp(start,tz='UTC'))&(dates<=pd.Timestamp(end,tz='UTC')))
    initial=100000.;cash=initial;pos=None;trades=[];curve=[];exposures=[];ambiguities=[]
    month=None;month_equity=initial;month_losses=0.;blocked=0
    arr={k:d[k].to_numpy() for k in ['open','high','low','close','atr','safezone']}
    def exit_position(raw,i,reason):
        nonlocal pos,cash,month_losses
        fill=raw*(1-cfg.slip);proceeds=pos['qty']*fill*(1-cfg.fee);cash+=proceeds
        pnl=proceeds-pos['cost'];month_losses+=max(-pnl,0)
        trades.append({**pos,'exit_date':str(dates.iloc[i].date()),'exit_i':int(i),'exit':fill,'reason':reason,'pnl':pnl,
            'return_pct':pnl/pos['cost']*100,'R':pnl/pos['risk_money'],'hold':i-pos['entry_i']+1,
            'mfe_R':(pos['max_price']-pos['entry'])/(pos['entry']-pos['initial_stop'])})
        pos=None
    for i in idx:
        o,h,l,c=[float(arr[k][i]) for k in ['open','high','low','close']]
        key=(dates.iloc[i].year,dates.iloc[i].month)
        if key!=month:
            month=key;month_equity=cash+(pos['qty']*float(arr['close'][i-1]) if pos else 0);month_losses=0
        had_position=pos is not None
        if pos is not None:
            # Existing protective order takes priority over a discretionary next-open exit on a gap.
            if o<=pos['stop']:exit_position(o,i,'gap_stop')
            elif pos.get('exit_next') or signals['exit'][i-1]:exit_position(o,i,'signal_exit')
        entered=False
        if pos is None and not had_position and i>0 and signals['setup'][i-1]:
            j=i-1;level=float(signals['price'][j]);st=float(signals['stop'][j]);raw=None
            if cfg.entry=='market':raw=o
            elif cfg.entry=='stop' and h>=level:raw=max(o,level)
            elif cfg.entry=='touch' and l<=level:raw=min(o,level)
            if raw is not None and math.isfinite(st) and st>0:
                fill=raw*(1+cfg.slip)
                unit_risk=fill*(1+cfg.fee)-st*(1-cfg.slip)*(1-cfg.fee)
                target_level=float(signals['target'][j])
                chase_ok=not cfg.chase_guard or fill<float(d['upper'].iloc[j])
                if st<raw and unit_risk>0 and chase_ok and (not math.isfinite(target_level) or target_level>fill):
                    budget=min(cfg.risk*month_equity,.02*month_equity)
                    qty=min(budget/unit_risk,cash/(fill*(1+cfg.fee)))
                    actual_risk=qty*unit_risk
                    if cfg.month_guard and month_losses+actual_risk>.06*month_equity+1e-8:blocked+=1
                    else:
                        cost=qty*fill*(1+cfg.fee);cash-=cost
                        pos=dict(entry_date=str(dates.iloc[i].date()),signal_date=str(dates.iloc[j].date()),entry_i=int(i),entry=fill,
                            initial_stop=st,stop=st,qty=qty,cost=cost,risk_money=actual_risk,max_price=fill)
                        entered=True
        if pos is not None:
            target=float(signals['target'][i-1]);stop=pos['stop']
            # A target already below today's open is an immediate open exit for an existing position.
            if not entered and o>=target:
                pos['max_price']=max(pos['max_price'],o);exit_position(o,i,'gap_target')
            else:
                stop_hit=l<=stop;target_hit=h>=target and target>pos['entry']
                # A buy-touch descending through entry and then stop cannot avoid the
                # stop without first taking a target. A buy-stop can survive if the
                # day's low preceded entry AND the close remains above the stop.
                ambiguous=stop_hit and (target_hit or (entered and cfg.entry=='stop' and o<signals['price'][i-1] and c>stop))
                if ambiguous:ambiguities.append(str(dates.iloc[i].date()))
                if stop_hit and not (ambiguous and ordering=='favorable'):
                    # Do not credit an exit bar's high after a prior stop.
                    exit_position(min(o,stop) if not entered else stop,i,'stop')
                elif target_hit:
                    pos['max_price']=max(pos['max_price'],target);exit_position(target,i,'target')
                elif stop_hit and not ambiguous:exit_position(stop,i,'stop')
                else:pos['max_price']=max(pos['max_price'],h)
        if pos is not None:
            if cfg.max_hold and i-pos['entry_i']+1>=cfg.max_hold:exit_position(c,i,'time_exit')
            else:
                candidate=pos['stop']
                if cfg.trail:
                    candidate=max(candidate,float(arr['safezone'][i]) if cfg.stop=='safezone' and np.isfinite(arr['safezone'][i]) else l-cfg.trail_atr*float(arr['atr'][i]))
                # BE activation uses today's close, applies from tomorrow: no hidden intrabar ordering.
                if cfg.be and c>=pos['entry']+1.5*(pos['entry']-pos['initial_stop']):candidate=max(candidate,pos['entry'])
                # Close-based tightening is only submitted after this bar. If above close, exit next open.
                pos['stop']=candidate
                pos['exit_next']=candidate>=c
        if i==idx[-1] and pos is not None:exit_position(c,i,'boundary_liquidation')
        equity=cash+(pos['qty']*c if pos else 0)
        assert cash>=-1e-6 and equity>0,('invalid account',i,cash,equity)
        curve.append(equity);exposures.append(pos['qty']*c/equity if pos else 0)
    eq=np.array(curve);returns=np.diff(np.r_[initial,eq])/np.r_[initial,eq[:-1]]
    return dict(config=asdict(cfg),dates=[str(x.date()) for x in dates.iloc[idx]],equity=eq.tolist(),returns=returns.tolist(),
        exposure=exposures,trades=trades,ambiguous_bars=len(ambiguities),ambiguous_dates=ambiguities,blocked_orders=blocked)


def metrics(result):
    e=np.array(result['equity']);r=np.array(result['returns']);ts=result['trades'];p=np.array([t['pnl'] for t in ts]);rr=np.array([t['R'] for t in ts])
    wins=p[p>1e-9];losses=p[p<-1e-9];years=len(e)/365.25
    f=lambda a:float(a) if np.isfinite(a) else None
    return dict(trades=len(ts),win_rate=float(np.mean(p>0))*100 if len(p) else None,
        payoff=f(wins.mean()/-losses.mean()) if len(wins) and len(losses) else None,
        profit_factor=f(wins.sum()/-losses.sum()) if len(losses) else None,
        total_return=(e[-1]/100000-1)*100,cagr=((e[-1]/100000)**(1/years)-1)*100,
        max_drawdown=float(np.min(e/np.maximum.accumulate(np.r_[100000,e])[1:]-1))*100,
        sharpe=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(365.25)) if len(r)>1 and np.std(r)>0 else 0,
        mean_R=float(np.mean(rr)) if len(rr) else None,median_R=float(np.median(rr)) if len(rr) else None,
        exposure=float(np.mean(result['exposure']))*100,mean_hold=float(np.mean([t['hold'] for t in ts])) if ts else None,
        initial_stop_rate=float(np.mean([t['reason'] in ['stop','gap_stop'] and t['pnl']<0 for t in ts]))*100 if ts else None,
        ambiguous_bars=result['ambiguous_bars'],blocked_orders=result['blocked_orders'])


def candidates():
    base=Config()
    return [replace(base,name='value_raw',force=False,impulse=False),
        replace(base,name='value_force',impulse=False),replace(base,name='value_impulse',force=False),base,
        replace(base,name='false_break',family='falsebreak',trend=False,force=False,entry='market'),
        replace(base,name='false_break_div',family='falsebreak',trend=False,force=False,divergence=True,entry='market'),
        replace(base,name='channel',family='channel',trend=False,force=False,entry='market',target='ema'),
        replace(base,name='impulse_direct',family='impulse',trend=False,force=False,impulse=False,entry='market',target='none',chase_guard=False),
        replace(base,name='kangaroo',family='kangaroo',trend=False,force=False,entry='market',target='ema'),
        replace(base,name='rsi_pullback',family='rsi',force=False,entry='market'),
        replace(base,name='stoch_pullback',family='stoch',force=False,entry='market'),
        replace(base,name='macd_cross',family='macd',force=False,impulse=False,entry='market',target='none',chase_guard=False),
        replace(base,name='triple_weekly_reference',family='weekly',trend=False)]


def ablations():
    b=Config()
    return [replace(b,name='value_no_trend',trend=False),replace(b,name='value_limit_proxy',entry='touch'),
        replace(b,name='value_no_new_force_low',force_extreme=True),replace(b,name='value_volume',volume=True),
        replace(b,name='value_adx',adx=True),replace(b,name='value_nick',stop='nick'),
        replace(b,name='value_safezone',stop='safezone'),replace(b,name='value_atr_stop',stop='atr'),
        replace(b,name='value_no_trail',trail=False),replace(b,name='value_no_target',target='none'),
        replace(b,name='value_target_ema',target='ema'),replace(b,name='value_close_be',be=True),
        replace(b,name='value_no_month_guard',month_guard=False),replace(b,name='value_risk_2pct',risk=.02),
        replace(b,name='false_break_strong_div',family='falsebreak',trend=False,force=False,divergence=True,strong_div=True,entry='market')]
