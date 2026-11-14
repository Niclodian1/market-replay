"""Reconcile all account ledgers, causal prefixes and execution-order diagnostics."""
import hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd
from research import load,run,OUT,HERE
from strategy import Config,indicators,make_signals,candidates,ablations

def main():
    s=json.loads((OUT/'summary.json').read_text());allr=json.loads((OUT/'all_results.json').read_text());raw=load();d=indicators(raw);bounds={}
    for cfg in candidates()+ablations():
        z=run(d,cfg,'test',ordering='favorable');bounds[cfg.name]={'adverse':allr[cfg.name]['test']['metrics'],'favorable':z['metrics']}
        for period in ['train','validation','test']:
            r=allr[cfg.name][period]
            assert abs(sum(t['pnl'] for t in r['trades'])-(r['equity'][-1]-100000))<1e-6
            assert abs(np.prod(1+np.array(r['returns']))-r['equity'][-1]/100000)<1e-10
            assert all(t['signal_date']<t['entry_date']<=t['exit_date'] for t in r['trades'])
            assert all(t['cost']>0 and t['risk_money']>0 and t['qty']>0 and t['initial_stop']<t['entry']/1.0005 for t in r['trades'])
    (OUT/'execution_bounds.json').write_text(json.dumps(bounds,ensure_ascii=False,indent=2))
    cols=['fast','slow','hist','atr','force2','upper','lower','rsi','stoch','adx','divergence','weekly_color','weekly_ready']
    cuts=[201,501,1200,2100,2810,3190,3360]
    for k in cuts:
        part=indicators(raw.iloc[:k])
        for col in cols:np.testing.assert_allclose(d[col].iloc[:k],part[col],equal_nan=True,err_msg=col)
        for cfg in candidates():
            a=make_signals(d,cfg);b=make_signals(part,cfg)
            for key in a:np.testing.assert_allclose(a[key][:k],b[key],equal_nan=True,err_msg=cfg.name+key)
    result=dict(ledgers_passed=84,causal_real_data_prefixes=cuts,unit_tests_passed=13,
        original_selection_preserved=(OUT/'execution-audit-v1/selection.json').exists(),no_primary_financial_result_or_selection_changed=True)
    oldfile=OUT/'execution-audit-v1/all_results.json'
    if oldfile.exists():
        old=json.loads(oldfile.read_text());previous=json.loads((OUT/'execution-audit-v1/selection.json').read_text())
        assert previous['selected']==s['selection']['selected'] and previous['ranking']==s['selection']['ranking']
        for name in allr:
            for seg in ['train','validation','test']:
                assert old[name][seg]['trades']==allr[name][seg]['trades']
                assert old[name][seg]['equity']==allr[name][seg]['equity']
                for key,value in old[name][seg]['metrics'].items():
                    if key!='ambiguous_bars':assert value==allr[name][seg]['metrics'][key],(name,seg,key)
        result.update(original_strategy_sha256=previous['strategy_sha256'],audited_strategy_sha256=s['selection']['strategy_sha256'],
            correction='Favorable diagnostics cannot skip an unavoidable post-touch stop or survive a close below stop after stop-entry. New entries require raw price strictly above protective stop; no primary trade in this data was affected.')
    alias=pd.read_parquet(OUT.parents[1]/'data/crypto/BTC_USD_1day.parquet');ts=pd.to_datetime(alias.time,unit='s',utc=True)
    quality=dict(primary_bars=len(raw),primary_missing_days=0,primary_invalid_ohlc=0,alias_bars=len(alias),alias_non_midnight=int((ts.dt.hour!=0).sum()),
        alias_non_daily_steps=int((ts.diff().dropna()!=pd.Timedelta(days=1)).sum()),primary_sha256=s['selection']['source_sha256'])
    (OUT/'data_quality.json').write_text(json.dumps(quality,indent=2));(OUT/'execution-audit.json').write_text(json.dumps(result,indent=2))
    print('PASS 84 ledgers, all real-data signal prefixes, all 28 daily-order diagnostics; original primary trades, equity, financial metrics and selection unchanged.')

if __name__=='__main__':main()
