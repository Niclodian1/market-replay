#!/usr/bin/env python3
"""Compare stratified long-text samples with the independent single-post API."""
import concurrent.futures
import difflib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'v2'


def normalized(text):
    text=re.sub(r'https?://\S+','',text)
    text=re.sub(r'@\w+','',text)
    return re.sub(r'\s+','',text)


def main():
    rows=[json.loads(l) for l in (OUT/'corpus.jsonl').read_text().splitlines()]
    selected={}
    for year in sorted({r['created_at_from_id'][:4] for r in rows}):
        group=[r for r in rows if r['created_at_from_id'].startswith(year)]
        for r in sorted(group,key=lambda x:len(x['text']),reverse=True)[:4]+group[:1]+group[-1:]:selected[r['id']]=r
    for r in rows:
        if r['text'].endswith(('…','...')) or not r['text']:
            selected[r['id']]=r
    folder=OUT/'fulltext_verification';folder.mkdir(exist_ok=True)
    def verify(r):
        file=folder/(r['id']+'.json')
        previous=ROOT/'raw'/(r['id']+'.json')
        url=f'https://api.fxtwitter.com/baoshen23761792/status/{r["id"]}'
        try:
            if previous.exists():
                data=json.loads(previous.read_text())['response']
            elif file.exists():data=json.loads(file.read_text())
            else:
                request=subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','35',url],capture_output=True,text=True,check=True)
                data=json.loads(request.stdout)
                file.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
            post=data['tweet']
            assert post['id']==r['id'] and post['author']['screen_name']=='baoshen23761792'
            a,b=normalized(r['text']),normalized(post['text'])
            return {'id':r['id'],'url':r['url'],'nitter_chars':len(r['text']),'api_chars':len(post['text']),
                    'normalized_match':a==b,'similarity':round(difflib.SequenceMatcher(None,a,b,autojunk=False).ratio(),4),
                    'api_text':post['text'],'status':'checked','is_note_tweet':post.get('is_note_tweet')}
        except Exception as e:return {'id':r['id'],'status':'failed','error':str(e)}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for i,r in enumerate(pool.map(verify,selected.values()),1):
            results.append(r)
            if i%10==0:print(f'checked {i}/{len(selected)}',flush=True)
    (OUT/'fulltext_verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'checked':len(results),'failed':sum(r['status']=='failed' for r in results),
                      'mismatches':[{k:r[k] for k in ['id','nitter_chars','api_chars','similarity']} for r in results if r.get('normalized_match') is False]},ensure_ascii=False))


if __name__=='__main__':main()
