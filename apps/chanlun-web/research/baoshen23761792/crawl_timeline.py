#!/usr/bin/env python3
"""Walk public Nitter cursors, retaining HTML, provenance and context separately."""
import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
USER = 'baoshen23761792'


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def parse(html, url, file):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for item in soup.select('.timeline-item'):
        link = item.select_one('a.tweet-link')
        if not link:
            continue
        match = re.search(r'/([^/]+)/status/(\d+)', link.get('href', ''))
        if not match:
            continue
        author, pid = match.groups()
        content = item.select_one('.tweet-content')
        date = item.select_one('.tweet-date a')
        timestamp = ((int(pid) >> 22) + 1288834974657) / 1000
        images = []
        for a in item.select('.attachments a.still-image'):
            if a.find_parent(class_='quote'):
                continue
            href = a.get('href', '')
            if href.startswith('/pic/orig/'):
                images.append('https://pbs.twimg.com/' + unquote(href[len('/pic/orig/'):]))
        quote = item.select_one('.quote')
        quote_link = quote.select_one('a.quote-link') if quote else None
        records.append({
            'id': pid, 'author': author, 'url': f'https://x.com/{author}/status/{pid}',
            'text': content.get_text('', strip=False).strip() if content else '',
            'display_date': date.get('title') if date else None,
            'created_at_from_id': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            'date_basis': 'Twitter snowflake milliseconds; also retain displayed UTC date',
            'replying_to': [a.get_text(strip=True) for a in item.select('.replying-to a') if not a.find_parent(class_='quote')],
            'timeline_classes': item.get('class', []),
            'retweet_label': item.select_one('.retweet-header').get_text(' ', strip=True) if item.select_one('.retweet-header') else None,
            'pinned': bool(item.select_one('.pinned')),
            'quote_url': urljoin(url, quote_link.get('href')) if quote_link else None,
            'quote_photo_urls': ['https://pbs.twimg.com/'+unquote(a['href'][len('/pic/orig/'):]) for a in quote.select('.still-image') if a.get('href','').startswith('/pic/orig/')] if quote else [],
            'quoted_text': quote.get_text(' ',strip=True) if quote else None,
            'photo_urls': list(dict.fromkeys(images)),
            'links': [{'label': a.get_text(), 'href': urljoin(url,a.get('href',''))} for a in content.select('a')] if content else [],
            'has_video': bool(item.select('video, .video-container')),
            'source_page': url, 'source_file': str(file.relative_to(ROOT)),
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        })
    next_urls = [urljoin(url, a['href']) for a in soup.select('.show-more a')
                 if 'cursor=' in a.get('href','') and 'newest' not in a.get_text().lower()]
    errors = [x.get_text(' ',strip=True) for x in soup.select('.error-panel')]
    return records, (next_urls[-1] if next_urls else None), soup, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', default='nitter.meowing.monster')
    ap.add_argument('--mode', choices=['with_replies','posts','media','articles','search','reply_search'], default='with_replies')
    ap.add_argument('--start-url')
    ap.add_argument('--max-pages', type=int, default=200)
    ap.add_argument('--delay', type=float, default=2)
    args = ap.parse_args()
    out = ROOT / 'v2' / args.mode
    out.mkdir(parents=True,exist_ok=True)
    pages, records, visited = [], {}, set()
    suffix = '' if args.mode == 'posts' else '/' + args.mode
    url = args.start_url or f'https://{args.host}/{USER}{suffix}'
    stop = 'page_limit'
    for page_no in range(1,args.max_pages+1):
        if url in visited:
            stop = 'repeated_cursor'; break
        visited.add(url)
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        file = out / f'{page_no:04d}_{digest}.html'
        cached = file.exists()
        status = 'cached'
        if not cached:
            result = subprocess.run(['curl','--silent','--show-error','--location','--max-time','40',
                                     '--write-out','%{http_code}',url,'--output',str(file)],
                                    capture_output=True,text=True)
            status = result.stdout
            if result.returncode or status != '200':
                pages.append({'url':url,'file':str(file.relative_to(ROOT)), 'status':status,
                              'error':result.stderr,'page':page_no})
                stop='http_or_network_error'; break
        html = file.read_text(errors='replace')
        items, nxt, soup, errors = parse(html,url,file)
        if errors or not soup.select_one('.timeline'):
            pages.append({'url':url,'file':str(file.relative_to(ROOT)),'page':page_no,'errors':errors,
                          'title':soup.title.get_text() if soup.title else ''})
            stop='invalid_timeline'; break
        before = len(records)
        for item in items:
            key = item['id']
            if key not in records or len(item['text']) > len(records[key]['text']):
                records[key] = item
        own = [r for r in records.values() if r['author'].lower()==USER]
        page = {'page':page_no,'url':url,'file':str(file.relative_to(ROOT)), 'http':status,
                'items':len(items),'new_unique_items':len(records)-before,
                'own_total':len(own),'next_url':nxt,
                'terminal_text':soup.select_one('.timeline').get_text(' ',strip=True)[-350:] if not nxt else None}
        pages.append(page)
        write_json(out/'pages.json',pages)
        with (out/'records.jsonl').open('w') as f:
            for r in sorted(records.values(),key=lambda x:int(x['id'])):
                f.write(json.dumps(r,ensure_ascii=False)+'\n')
        print(json.dumps({'mode':args.mode,'page':page_no,'own':len(own),'new':len(records)-before,
                          'oldest':min((r['created_at_from_id'] for r in own),default=None),
                          'has_next':bool(nxt)},ensure_ascii=False),flush=True)
        if not nxt:
            stop='no_next_cursor'; break
        if len(records)==before and page_no>1:
            stop='no_new_records'; break
        url=nxt
        if not cached:
            time.sleep(args.delay)
    write_json(out/'pages.json',pages)
    own=[r for r in records.values() if r['author'].lower()==USER]
    report={'mode':args.mode,'host':args.host,'finished_at':datetime.now(timezone.utc).isoformat(),
            'page_count':len(pages),'stop_reason':stop,'unique_own_posts':len(own),
            'context_or_reposts':len(records)-len(own),'account_history_complete':False,
            'oldest':min((r['created_at_from_id'] for r in own),default=None),
            'newest':max((r['created_at_from_id'] for r in own),default=None)}
    write_json(out/'crawl_manifest.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
