#!/usr/bin/env python3
"""Archive known public post IDs; this does NOT enumerate a full X timeline."""
import argparse
import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USER = 'baoshen23761792'


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def fetch(post_id):
    path = ROOT / 'raw' / (post_id + '.json')
    if path.exists():
        return json.loads(path.read_text())
    url = f'https://api.fxtwitter.com/{USER}/status/{post_id}'
    response = subprocess.run(
        ['curl', '--fail', '--silent', '--show-error', '--location',
         '--max-time', '35', url], capture_output=True, text=True, check=True)
    data = json.loads(response.stdout)
    if data.get('code') != 200 or not data.get('tweet'):
        raise ValueError(f'API returned {data.get("code")}: {data.get("message")}')
    save_json(path, {'fetched_at': datetime.now(timezone.utc).isoformat(),
                     'source_url': url, 'response': data})
    return json.loads(path.read_text())


def own(post):
    return post.get('author', {}).get('screen_name', '').lower() == USER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-posts', type=int, default=250)
    parser.add_argument('--delay', type=float, default=0.5)
    args = parser.parse_args()
    (ROOT / 'raw').mkdir(exist_ok=True)
    queue = [s.strip() for s in (ROOT / 'tweet_ids.txt').read_text().splitlines()
             if s.strip().isdigit()]
    seeds = len(set(queue))
    seen, records, failures = set(), {}, []

    def accept(post, fetched_at, source_url, provenance):
        if not own(post):
            return
        pid = str(post['id'])
        records[pid] = {
            'id': pid, 'author': USER, 'url': post['url'],
            'created_at': post.get('created_at'),
            'created_timestamp': post.get('created_timestamp'),
            'text': post.get('text', ''),
            'replying_to': post.get('replying_to'),
            'replying_to_status': post.get('replying_to_status'),
            'quote_id': (post.get('quote') or {}).get('id'),
            'media': post.get('media'),
            'is_note_tweet': post.get('is_note_tweet'),
            'source_url': source_url, 'fetched_at': fetched_at,
            'provenance': provenance,
        }
        quote = post.get('quote')
        if isinstance(quote, dict) and own(quote):
            accept(quote, fetched_at, source_url, 'embedded_self_quote')
            if str(quote['id']) not in seen:
                queue.append(str(quote['id']))
        if str(post.get('replying_to', '')).lstrip('@').lower() == USER:
            parent = str(post.get('replying_to_status') or '')
            if parent.isdigit() and parent not in seen:
                queue.append(parent)

    while queue and len(seen) < args.max_posts:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        try:
            entry = fetch(pid)
            post = entry['response']['tweet']
            if str(post['id']) != pid or not own(post):
                raise ValueError('ID or author mismatch')
            accept(post, entry['fetched_at'], entry['source_url'], 'single_post_api')
            print(f'{len(seen)}: {pid} OK', flush=True)
        except (subprocess.CalledProcessError, ValueError, KeyError) as exc:
            failures.append({'id': pid, 'error': str(exc)})
            print(f'{len(seen)}: {pid} FAILED', flush=True)
        time.sleep(max(0, args.delay))

    ordered = sorted(records.values(), key=lambda r: (r['created_timestamp'] or 0, r['id']))
    with (ROOT / 'tweets.jsonl').open('w') as handle:
        for record in ordered:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    fields = ['id', 'author', 'created_at', 'url', 'text', 'replying_to',
              'replying_to_status', 'quote_id', 'source_url', 'fetched_at', 'provenance']
    with (ROOT / 'tweets.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(ordered)
    manifest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'username': USER, 'complete': False,
        'collection_method': 'Public search-discovered IDs + individual FxTwitter post responses + self-references',
        'seed_count': seeds, 'attempted_count': len(seen),
        'unique_posts': len(ordered), 'failed': failures,
        'pending_ids': sorted(set(queue) - seen),
        'oldest_post': ordered[0]['created_at'] if ordered else None,
        'newest_post': ordered[-1]['created_at'] if ordered else None,
        'full_timeline_pagination_available': False,
        'media_archive_status_file': 'media_manifest.json',
        'all_media_analyzed': False,
        'limitations': [
            'Third-party cached source, not an authenticated X export.',
            'Known-ID sampling is incomplete and not representative.',
            'No verified inventory of deleted, private, unindexed posts or edit history.',
            'Media URLs are retained; photo download status is in media_manifest.json. Spaces not transcribed.',
            'Date span does not imply continuous coverage.',
        ],
    }
    save_json(ROOT / 'manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
