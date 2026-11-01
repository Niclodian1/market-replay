#!/usr/bin/env python3
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'v2'


def main():
    folder=OUT/'media';folder.mkdir(exist_ok=True)
    photos={}
    for line in (OUT/'corpus.jsonl').read_text().splitlines():
        post=json.loads(line)
        for url in post['photo_urls']:
            key=url.split('?')[0]
            photos.setdefault(key,[]).append(post['id'])
    previous={}
    for item in json.loads((ROOT/'media_manifest.json').read_text()):
        if item['status']=='downloaded':previous[item['url'].split('?')[0]]=ROOT/item['path']
    def get(pair):
        url,ids=pair
        name=Path(urlparse(url).path).name
        file=folder/name
        row={'url':url,'post_ids':ids,'file':str(file.relative_to(OUT))}
        if not file.exists() and url in previous:
            shutil.copy2(previous[url],file)
        if not file.exists():
            temp=file.with_suffix(file.suffix+'.part')
            r=subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','35',
                              '--retry','1','--retry-delay','2',url+'?name=orig','--output',str(temp)],
                             capture_output=True,text=True)
            if r.returncode:return row|{'status':'failed','error':r.stderr}
            temp.replace(file)
        try:
            with Image.open(file) as im:
                width,height=im.size;im.verify()
            return row|{'status':'downloaded','bytes':file.stat().st_size,'width':width,'height':height,
                        'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
        except Exception as e:return row|{'status':'invalid_image','error':str(e)}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i,row in enumerate(pool.map(get,photos.items()),1):
            results.append(row)
            if i%25==0:print(f'{i}/{len(photos)} photos processed',flush=True)
    (OUT/'media_manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'total':len(results),'success':sum(r['status']=='downloaded' for r in results),
                      'failed':[r for r in results if r['status']!='downloaded']},ensure_ascii=False))


if __name__=='__main__':main()
