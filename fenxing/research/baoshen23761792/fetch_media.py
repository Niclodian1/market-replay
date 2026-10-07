#!/usr/bin/env python3
"""Download photo attachments for the already archived posts."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent


def main():
    folder = ROOT / 'media'
    folder.mkdir(exist_ok=True)
    results = []
    for line in (ROOT / 'tweets.jsonl').read_text().splitlines():
        post = json.loads(line)
        for index, photo in enumerate((post.get('media') or {}).get('photos', []), 1):
            url = photo['url']
            if urlparse(url).hostname != 'pbs.twimg.com':
                results.append({'post_id': post['id'], 'url': url, 'status': 'skipped_host'})
                continue
            name = f"{post['id']}_{index}{Path(urlparse(url).path).suffix or '.jpg'}"
            path = folder / name
            item = {'post_id': post['id'], 'url': url, 'path': f'media/{name}'}
            try:
                if not path.exists():
                    temp = path.with_suffix(path.suffix + '.part')
                    subprocess.run(['curl', '--fail', '--silent', '--show-error',
                                    '--location', '--max-time', '30', url,
                                    '--output', str(temp)], check=True)
                    temp.replace(path)
                item.update(status='downloaded', bytes=path.stat().st_size)
                print(name, flush=True)
            except subprocess.CalledProcessError:
                item['status'] = 'failed'
            results.append(item)
    (ROOT / 'media_manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(f'{sum(r["status"] == "downloaded" for r in results)}/{len(results)} photos downloaded')


if __name__ == '__main__':
    main()
