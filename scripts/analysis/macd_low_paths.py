"""Price-path envelopes for a long signal: fixed initial stop, +1.5R activates entry stop.

The envelopes allow either intrabar order and revisits. They never credit the full high
of an exit bar as a guaranteed reachable high. No data after an exit is used.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import math

@dataclass(frozen=True)
class State:
    status: str = 'live'
    protected: bool = False
    low: float = 0.0
    high: float = 0.0
    exit_price: float | None = None
    exit_bar: int | None = None
    exit_time: int | None = None
    armed_bar: int | None = None


def step(states, bar, entry, stop, trigger, index):
    """Advance all feasible states over one OHLC bar (or one finer-timeframe OHLC)."""
    t, o, h, l, c = bar
    out = []
    for s in states:
        if s.status != 'live':
            out.append(s)
            continue
        active = entry if s.protected else stop
        if o <= active:
            out.append(replace(s, status='be' if s.protected else 'sl', exit_price=float(o), exit_bar=index, exit_time=int(t)))
            continue
        opened_protected = s.protected or o >= trigger
        armed = s.armed_bar if s.protected else index if o >= trigger else None
        if opened_protected:
            if l <= entry:
                out.append(replace(s, status='be', protected=True, armed_bar=armed,
                    low=max(s.low, o-entry), high=max(s.high,h-entry), exit_price=entry, exit_bar=index, exit_time=int(t)))
            else:
                out.append(replace(s, protected=True, armed_bar=armed, low=max(s.low,h-entry), high=max(s.high,h-entry)))
        elif h < trigger:
            if l <= stop:
                out.append(replace(s, status='sl', low=max(s.low,o-entry), high=max(s.high,h-entry),
                    exit_price=stop, exit_bar=index, exit_time=int(t)))
            else:
                out.append(replace(s,low=max(s.low,h-entry),high=max(s.high,h-entry)))
        else:
            # H >= trigger: the order of hitting stop, trigger and revisiting entry is unknown.
            if l <= stop:
                out.append(replace(s,status='sl', low=max(s.low,o-entry), high=max(s.high,trigger-entry),
                    exit_price=stop, exit_bar=index,exit_time=int(t)))
            if l <= entry:
                out.append(replace(s,status='be',protected=True,armed_bar=index,
                    low=max(s.low,trigger-entry),high=max(s.high,h-entry),exit_price=entry,exit_bar=index,exit_time=int(t)))
            if l > stop and c > entry:
                out.append(replace(s,protected=True,armed_bar=index,low=max(s.low,h-entry),high=max(s.high,h-entry)))
    return out


def summarize(states, entry, stop, close):
    risk = entry-stop
    pnl = [((s.exit_price if s.status != 'live' else close)-entry)/risk for s in states]
    def bounds(pred):
        vals=[bool(pred(s)) for s in states]
        return [int(all(vals)),int(any(vals))]
    mfe=[min(s.low for s in states)/risk,max(s.high for s in states)/risk]
    return dict(statuses=sorted({s.status for s in states}),
        loss_stop=bounds(lambda s:s.status=='sl'),
        breakeven_stop=bounds(lambda s:s.status=='be'),
        open=bounds(lambda s:s.status=='live'),
        negative_exit=bounds(lambda s:s.status!='live' and s.exit_price < entry),
        armed=bounds(lambda s:s.protected),
        first_sl={str(k):bounds(lambda s:s.status=='sl' and s.exit_bar<=k) for k in [1,2,3,5,10,20]},
        first_be={str(k):bounds(lambda s:s.status=='be' and s.exit_bar<=k) for k in [1,3,5,10,20]},
        mfe_R=mfe,mfe_pct=[v*risk/entry*100 for v in mfe], pnl_R=[min(pnl),max(pnl)],
        targets={str(k):[int(mfe[0]>=k),int(mfe[1]>=k)] for k in [2,3,5,10]},
        cases=[dict(status=s.status,armed=s.protected,armed_bar=s.armed_bar,exit_bar=s.exit_bar,
            exit_time=s.exit_time,exit_price=s.exit_price,mfe_R=[s.low/risk,s.high/risk]) for s in states])


def evaluate(bars, index, stop, trigger_r=1.5, finer=None):
    entry=float(bars[index][4])
    if not (entry > stop > 0):
        return None
    trigger=entry+trigger_r*(entry-stop)
    states=[State()]
    snapshots={}
    refined=0
    end=len(bars)-1
    for j in range(index+1,len(bars)):
        bar=bars[j]
        detail=None
        if finer and any(s.status=='live' for s in states):
            # Any exit can leave the high/low order unresolved. Refine those bars only.
            needs=any(s.status=='live' and (bar[3] <= (entry if s.protected else stop)
                      or (not s.protected and bar[2]>=trigger and bar[3]<=entry)) for s in states)
            if needs:
                detail=finer(j)
        if detail is not None:
            refined+=1
            for part in detail:
                states=step(states,part,entry,stop,trigger,j-index)
        else:
            states=step(states,bar,entry,stop,trigger,j-index)
        elapsed=j-index
        if elapsed in [20,60,120]:
            snapshots[str(elapsed)]=dict(complete=True,observed=elapsed,**summarize(states,entry,stop,float(bar[4])))
        if all(s.status!='live' for s in states):
            end=j
            break
    last=summarize(states,entry,stop,float(bars[end][4]))
    available=len(bars)-1-index
    for h in [20,60,120]:
        if str(h) not in snapshots:
            snapshots[str(h)]=dict(complete=available>=h,observed=min(available,h),**last)
    snapshots['end']=dict(complete=True,observed=available,**last)
    return dict(entry=entry,stop=stop,risk_pct=(entry-stop)/entry*100,refined_bars=refined,windows=snapshots)
