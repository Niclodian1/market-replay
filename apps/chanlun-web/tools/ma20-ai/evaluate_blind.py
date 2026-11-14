"""Evaluate first blind decisions against frozen V2 predictions. Never retrains."""
from pathlib import Path
import json,argparse,hashlib
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'reports/ma20-ai-v2-2026-11-04'
def evaluate(model,predictions,doc):
 if doc.get('symbol')!='BTC' or doc.get('tf')!='1D':raise ValueError('Only BTC 1D feedback is supported')
 target=set(model['blindReviewTimes']);rows={};problems=[];last={it.get('features',{}).get('time'):it for it in doc.get('items',[])if it.get('source')=='ma20_ai_v2'}
 for t,it in last.items():
  if t not in target:continue
  a=it.get('algorithm',{});b=a.get('blindReview')or{}
  if b.get('firstModelVersion')!=model['version'] or a.get('version')!=model['version']:
   problems.append({'time':t,'reason':'model version mismatch'});continue
  score=predictions[t]['scores'][model['selected']]
  if abs(float(b.get('firstScore',-1))-score)>1e-8 or abs(float(b.get('firstThreshold',-1))-model['threshold'])>1e-10:
   problems.append({'time':t,'reason':'score / feature drift or incomplete first-decision evidence'});continue
  d=b.get('firstDecision');rows[t]={'time':t,'firstDecision':d,'currentDecision':it.get('decision'),'revisedAfterExposure':d!=it.get('decision'),'score':score,'retained':score>=model['threshold']}
 valid=[r for r in rows.values()if r['firstDecision']in ['yes','no']];pos=[r for r in valid if r['firstDecision']=='yes'];neg=[r for r in valid if r['firstDecision']=='no'];tp=sum(r['retained']for r in pos);tn=sum(not r['retained']for r in neg)
 return {'version':model['version'],'expected':len(target),'received':len(rows),'missingTimes':sorted(target-set(rows)),'firstUncertain':sum(r['firstDecision']not in ['yes','no']for r in rows.values()),'positive':len(pos),'negative':len(neg),'positiveRetained':tp,'negativeRejected':tn,'recall':tp/len(pos)if pos else None,'negativeRejection':tn/len(neg)if neg else None,'revisedAfterExposure':sum(r['revisedAfterExposure']for r in rows.values()),'problems':problems,'rows':list(rows.values()),'status':'complete_new_label_check'if len(rows)==len(target)and not problems else 'incomplete_or_mismatched','notProspective':True,'automaticPromotion':False}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--feedback',required=True);ap.add_argument('--snapshot',default=str(OUT));args=ap.parse_args();folder=Path(args.snapshot);model=json.loads((folder/'model.json').read_text());pred={r['time']:r for r in json.loads((folder/'parity.json').read_text())['rows']};p=Path(args.feedback);result=evaluate(model,pred,json.loads(p.read_text()));result['feedbackSha256']=hashlib.sha256(p.read_bytes()).hexdigest();target=folder/('blind-evaluation-'+result['feedbackSha256'][:10]+'.json');target.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items()if k!='rows'},ensure_ascii=False,indent=2));print(target)
if __name__=='__main__':main()
