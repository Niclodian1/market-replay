#!/usr/bin/env python3
"""Build a self-contained offline report; full precision audit JSON is kept separately."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/macd-low-breakeven-2026-11-11'
HORIZONS=['20','60','120','end']
BE_KEYS=['complete','loss_stop','breakeven_stop','negative_exit','open','armed','mfe_R','mfe_pct','pnl_R','first_sl','first_be','targets','statuses']
BASE_KEYS=['complete','loss_stop','mfe_R','targets']

def pack_windows(windows,keys):
    unique=[];lookup={};indices=[]
    for horizon in HORIZONS:
        w={key:windows[horizon][key] for key in keys}
        encoded=json.dumps(w,separators=(',',':'))
        if encoded not in lookup:
            lookup[encoded]=len(unique);unique.append(w)
        indices.append(lookup[encoded])
    return unique,indices

def main():
    data=json.loads((OUT/'results-low.json').read_text())
    compact={key:data[key] for key in ['engine_sha256','exclusions','series']}
    for s in compact['series']:
        for p in s['periods'].values():p.pop('baseline',None)
    compact['trades']=[]
    for r in data['trades']:
        item={key:r[key] for key in ['sym','tf','market','time','date','low_date','lag_bars','strength','episode']}
        item['be']={key:r['be'][key] for key in ['entry','stop','risk_pct']}
        item['_be'],item['_bi']=pack_windows(r['be']['windows'],BE_KEYS)
        item['_base'],item['_bai']=pack_windows(r['base']['windows'],BASE_KEYS)
        compact['trades'].append(item)
    payload=json.dumps(compact,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('</','<\\/')
    template=(ROOT/'scripts/analysis/macd_low_report.template.html').read_text()
    target=ROOT/'apps/chanlun-web/analysis/macd-low-breakeven-2026-11-11.html'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(template.replace('/*__DATA__*/',payload))
    print(f'{target} ({target.stat().st_size/1e6:.1f} MB)')

if __name__=='__main__':main()
