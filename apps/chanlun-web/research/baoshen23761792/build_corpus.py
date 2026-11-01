#!/usr/bin/env python3
"""Reparse saved pages without network; merge author posts, provenance and coverage."""
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from crawl_timeline import ROOT, USER, parse, write_json


def main():
    out=ROOT/'v2'
    merged={}
    mode_ids={}
    page_reports=[]
    for mode in ['posts','with_replies','articles','media','search','reply_search']:
        p=out/mode/'pages.json'
        if not p.exists():continue
        pages=json.loads(p.read_text())
        mode_ids[mode]=set()
        for page in pages:
            file=ROOT/page['file']
            if not file.exists():continue
            items,_,_,errors=parse(file.read_text(errors='replace'),page['url'],file)
            if errors:continue
            for r in items:
                mode_ids[mode].add(r['id'])
                source={'mode':mode,'url':r['source_page'],'file':r['source_file']}
                if r['id'] not in merged:
                    r['sources']=[source]; merged[r['id']]=r
                else:
                    old=merged[r['id']]
                    sources=old['sources']+[source]
                    if len(r['text'])>len(old['text']):merged[r['id']]=r
                    merged[r['id']]['sources']=sources
        f=out/mode/'crawl_manifest.json'
        if f.exists():page_reports.append(json.loads(f.read_text()))
    own=[];context=[]
    for r in sorted(merged.values(),key=lambda r:int(r['id'])):
        if r['author'].lower()!=USER:
            r['role']='reposted_author' if r['retweet_label'] else 'conversation_context'
            context.append(r);continue
        in_posts=r['id'] in mode_ids.get('posts',set())
        r['post_type']='quote' if in_posts and r['quote_url'] else ('post' if in_posts else 'reply_or_thread_context')
        if r['replying_to']:r['post_type']='reply'
        r['text_chars']=len(r['text'])
        r['possible_truncation']=r['text'].endswith(('…','...'))
        own.append(r)
    for name,rows in [('corpus.jsonl',own),('context.jsonl',context)]:
        with (out/name).open('w') as f:
            for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    fields=['id','created_at_from_id','display_date','post_type','url','text','text_chars','quote_url','source_file']
    with (out/'corpus.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(own)
    by_month=Counter(r['created_at_from_id'][:7] for r in own)
    counts_by_type=Counter(r['post_type'] for r in own)
    own_ids={r['id'] for r in own}
    old_ids=set((ROOT/'tweet_ids.txt').read_text().splitlines())
    inventory={
      'built_at':datetime.now(timezone.utc).isoformat(),'unique_author_posts':len(own),
      'other_author_context':len(context),'by_month':dict(sorted(by_month.items())),
      'by_type':dict(counts_by_type),'old_sample_missing':sorted(old_ids-own_ids),
      'originals_missing_from_replies_timeline':sorted((mode_ids.get('posts',set())&own_ids)-mode_ids.get('with_replies',set())),
      'date_min':own[0]['created_at_from_id'] if own else None,'date_max':own[-1]['created_at_from_id'] if own else None,
      'total_text_chars':sum(r['text_chars'] for r in own),
      'posts_with_photos':sum(bool(r['photo_urls']) for r in own),
      'unique_photo_urls':len({url for r in own for url in r['photo_urls']}),
      'has_video_posts':[r['id'] for r in own if r['has_video']],
      'possible_truncation_ids':[r['id'] for r in own if r['possible_truncation']],
      'article_links':[{'id':r['id'],'links':[a for a in r['links'] if '/article/' in a['href']]} for r in own if any('/article/' in a['href'] for a in r['links'])],
      'crawl_reports':page_reports,'complete':False,
      'completeness_note':'Cursor exhaustion is recorded per mode; account-wide completeness requires coverage review.'}
    write_json(out/'inventory.json',inventory)
    start=datetime(2020,11,1);end=datetime.now()
    months=[]
    while start<=end:
        month=start.strftime('%Y-%m')
        months.append({'month':month,'collected_posts':by_month.get(month,0),'meaning_of_zero':'not_observed_not_proof_of_no_posts' if not by_month.get(month) else ''})
        start=datetime(start.year+(start.month==12),start.month%12+1,1)
    with (out/'monthly_coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=months[0].keys());w.writeheader();w.writerows(months)
    print(json.dumps({k:v for k,v in inventory.items() if k not in ['article_links','possible_truncation_ids','has_video_posts','originals_missing_from_replies_timeline']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
