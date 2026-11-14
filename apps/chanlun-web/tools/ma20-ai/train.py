"""Train preference-screening baselines, never convert unknown bars to negatives.
python3 apps/chanlun-web/tools/ma20-ai/train.py --annotations /path/annotations.json
Optional --feedback adds a newer exported review file. No browser storage writes.
"""
from pathlib import Path
import argparse,json,hashlib,subprocess,sys,datetime
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score,roc_auc_score
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent
VERSION='MA20-AI-V1-2026-11-04';FEATURES='ma20-ai-features/1'

def labels(doc,feedback=None):
 grouped={}
 for item in doc['items']:grouped.setdefault(int(item.get('features',{}).get('time',0)),[]).append(item)
 if feedback:
  if feedback.get('symbol')!='BTC' or feedback.get('tf')!='1D':raise ValueError('Feedback must be BTC 1D')
  for item in feedback['items']:
   if item.get('source','').startswith(('ma20_ai_','ma20_structure_v3')):grouped.setdefault(int(item.get('features',{}).get('time',0)),[]).append(item)
 out={};audit={}
 for t,items in sorted(grouped.items()):
  manual=[x for x in items if x.get('source')=='manual_buy_v1']
  review=[x for x in items if x.get('source','').startswith(('ma20_ai_','ma20_structure_v3'))]
  m=manual[-1] if manual else None;r=max(enumerate(review),key=lambda pair:(pair[1].get('source','').startswith('ma20_ai_'),pair[0]))[1] if review else None
  old={x.get('decision') for x in items if x.get('source') in ('ma20','zzy') and x.get('decision') in ('yes','no')}
  if m and m.get('decision')=='yes' and not m.get('manual',{}).get('archived'):
   if r and r['decision']=='no':status='current_conflict_unknown'
   else:status='manual_confirmed';out[t]={'y':1,'weight':1.,'status':status}
  elif r and r.get('decision') in ('yes','no'):status='new_review_confirmed';out[t]={'y':int(r['decision']=='yes'),'weight':1.,'status':status}
  elif r:status='new_review_uncertain_unknown'
  elif m:status='manual_draft_or_withdrawn_unknown'
  elif old=={'no'}:status='legacy_negative_weak';out[t]={'y':0,'weight':.35,'status':status}
  elif len(old)>1:status='legacy_conflict_unknown'
  else:status='legacy_positive_unconfirmed_unknown'
  audit[str(t)]={'status':status,'sources':[{'source':x.get('source'),'decision':x.get('decision')} for x in items]}
 return out,audit

def prep_fit(x):
 med=np.array([np.nanmedian(x[:,j]) if np.isfinite(x[:,j]).any() else 0 for j in range(x.shape[1])]);z=np.where(np.isfinite(x),x,med);mean=z.mean(axis=0);std=z.std(axis=0);std=np.where(std>1e-9,std,1);return med,mean,std

def trans(x,prep):
 med,mean,std=prep;return np.clip((np.where(np.isfinite(x),x,med)-mean)/std,-6,6)

def threshold(scores,y):
 # Highest threshold attaining >=90% validation positive recall, no test tuning.
 pos=np.sort(scores[y==1]);allowed=int(np.floor(.1*len(pos)));return float(pos[min(allowed,len(pos)-1)]) if len(pos) else .5

def metrics(y,s,t):
 keep=s>=t;tp=int(((y==1)&keep).sum());fp=int(((y==0)&keep).sum());p=int((y==1).sum());neg=int((y==0).sum())
 return {'n':len(y),'positive_n':p,'legacy_or_review_negative_n':neg,'kept':int(keep.sum()),'tp':tp,'fp':fp,'fn':p-tp,'tn':neg-fp,'recall':tp/p if p else None,'precision':tp/(tp+fp) if tp+fp else None,'negative_rejection':(neg-fp)/neg if neg else None,'average_precision':float(average_precision_score(y,s)) if p and neg else None}

def export(model,prep,kind):
 med,mean,std=prep;d={'type':kind,'medians':med.tolist(),'means':mean.tolist(),'scales':std.tolist()}
 if kind=='logistic':d.update(intercept=float(model.intercept_[0]),coefficients=model.coef_[0].tolist())
 else:
  trees=[]
  for est in model.estimators_.ravel():
   t=est.tree_;trees.append({'left':t.children_left.tolist(),'right':t.children_right.tolist(),'feature':t.feature.tolist(),'threshold':t.threshold.tolist(),'value':t.value[:,0,0].tolist()})
  d.update(intercept=float(model._raw_predict_init(np.zeros((1,len(med))))[0,0]),learningRate=model.learning_rate,trees=trees)
 return d

def path_stats(candles,indices):
 c=np.asarray(candles,float);rr=[]
 for i in indices:
  deadline=c[i,0]+120*86400
  if c[-1,0]<deadline:continue
  end=int(np.searchsorted(c[:,0],deadline,side='right')-1);e=c[i,4];stop=c[i,3];best=e;stopped=False
  for j in range(i+1,end+1):
   if c[j,1]<=stop:stopped=True;break
   if c[j,3]<=stop:best=max(best,c[j,1]);stopped=True;break
   best=max(best,c[j,2])
  rr.append((stopped,best/e-1))
 if not rr:return {'n':0}
 a=np.asarray(rr,float);return {'n':len(a),'stop_rate':float(a[:,0].mean()),'mfe_median':float(np.median(a[:,1])),'mfe_mean':float(a[:,1].mean()),'mfe_p90':float(np.quantile(a[:,1],.9))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--annotations',default='/Users/zhangzheyu/Downloads/annotations-BTC-1D.json');ap.add_argument('--feedback');args=ap.parse_args()
 source=Path(args.annotations);doc=json.loads(source.read_text());assert doc.get('symbol')=='BTC' and doc.get('tf')=='1D'
 feedback=json.loads(Path(args.feedback).read_text()) if args.feedback else None
 fingerprint=hashlib.sha256(source.read_bytes()+(Path(args.feedback).read_bytes() if args.feedback else b'')+(ROOT/'apps/chanlun-web/data-view/data.js').read_bytes()+(ROOT/'apps/chanlun-web/js/13-ma20-ai-engine.js').read_bytes()+Path(__file__).read_bytes()).hexdigest()
 version=VERSION+'-'+fingerprint[:10]
 packed=json.loads(subprocess.check_output(['node',str(HERE/'features.cjs')],text=True));f=packed['features'];candles=packed['candles'];sig=set(packed['signals']);times=np.array([r['time'] for r in f['rows']],dtype=np.int64);allx=np.array([[np.nan if v is None else v for v in r['values']] for r in f['rows']]);lookup={int(t):i for i,t in enumerate(times)}
 lab,audit=labels(doc,feedback);missing=sorted(set(lab)-set(lookup));assert not missing,('Unmatched labels',missing)
 ids=np.array([lookup[t] for t in sorted(lab)]);x=allx[ids];y=np.array([lab[int(times[i])]['y'] for i in ids]);weights=np.array([lab[int(times[i])]['weight'] for i in ids]);dates=np.array([datetime.datetime.fromtimestamp(int(times[i]),datetime.timezone.utc).strftime('%Y-%m-%d') for i in ids])
 split={'train':dates<='2021-06-30','validation':(dates>='2022-01-01')&(dates<='2023-06-30'),'test':dates>='2024-01-01'}
 assert all(len(set(y[m]))==2 for m in split.values()),'Each chronological split needs both classes'
 prep=prep_fit(x[split['train']]);xt=trans(x,prep);allxt=trans(allx,prep)
 # Only two fixed baselines, no hyperparameter search or return optimization.
 configs={'logistic':LogisticRegression(C=.3,max_iter=2000,random_state=20261104),'gbdt':GradientBoostingClassifier(n_estimators=40,learning_rate=.05,max_depth=2,min_samples_leaf=12,random_state=20261104)}
 reports={};artifacts={};scores={}
 for kind,model in configs.items():
  model.fit(xt[split['train']],y[split['train']],sample_weight=weights[split['train']]);s=model.predict_proba(xt)[:,1];sc=model.predict_proba(allxt)[:,1];th=threshold(s[split['validation']],y[split['validation']]);reports[kind]={'threshold':th,'splits':{name:metrics(y[m],s[m],th) for name,m in split.items()}};artifacts[kind]=export(model,prep,kind);scores[kind]=sc
 # Validation-only selection. Test is reported once; failed gate never changes threshold.
 chosen=max(reports,key=lambda k:(reports[k]['splits']['validation']['precision'] or 0,reports[k]['splits']['validation']['average_precision'] or 0));t=reports[chosen]['threshold'];test=reports[chosen]['splits']['test'];passed=test['recall']>=.85 and test['negative_rejection']>=.2
 candidate=np.array([int(tt) in sig for tt in times]);knownpositive={t0 for t0,v in lab.items() if v['y']==1};positiveincandidate=knownpositive&sig
 def stage(mask):
  tt=times[mask];ret=set(map(int,tt[scores[chosen][mask]>=t]));return {'all_candidates':len(tt),'retained':len(ret),'known_positive_candidates':len(knownpositive&set(map(int,tt))),'retained_known_positive_candidates':len(ret&knownpositive)}
 testall=times>=datetime.datetime(2024,1,1,tzinfo=datetime.timezone.utc).timestamp();keep=candidate&(scores[chosen]>=t)
 report={'version':version,'featureVersion':FEATURES,'annotations_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'market_sha256':hashlib.sha256((ROOT/'apps/chanlun-web/data-view/data.js').read_bytes()).hexdigest(),'feedback_sha256':hashlib.sha256(Path(args.feedback).read_bytes()).hexdigest() if args.feedback else None,'sklearn_version':sklearn.__version__,'labels':{'positive':int(y.sum()),'weak_negative':sum(v['status']=='legacy_negative_weak' for v in lab.values()),'confirmed_new_review':sum(v['status']=='new_review_confirmed' for v in lab.values()),'unknown_records':len(audit)-len(lab),'unmatched':missing},'splitCounts':{n:{'n':int(m.sum()),'positive':int(y[m].sum()),'negative':int((y[m]==0).sum())} for n,m in split.items()},'excluded_boundary_records':int((~np.logical_or.reduce(list(split.values()))).sum()),'models':reports,'chosen':chosen,'gate_passed':bool(passed),'gate':'test recall >=85%, negative rejection >=20%; negatives mostly legacy, still provisional','candidate_coverage':{'manual_buys':len(knownpositive),'candidate_manual_buys':len(positiveincandidate),'outside_candidate_times':sorted(knownpositive-sig)},'stage_all':stage(candidate),'stage_test':stage(candidate&testall),'test120days':{'base':path_stats(candles,np.flatnonzero(candidate&testall)),'proposed_retained':path_stats(candles,np.flatnonzero(keep&testall))},'limitations':['Legacy negatives carry weight0.35 and are not fresh confirmations.','All annotations are retrospective; historical later segment is not prospective validation.','No cross-asset or intraday validation.','Scores are uncalibrated preference ranks, not profitability probabilities.','Current first-stage candidate pool cannot recover its own misses.','No return/stop/MFE fields are used as predictors or training labels.','180-day chronological boundary gaps; nearby same-market windows kept separated between fit/validation/test.']}
 model={'version':version,'featureVersion':FEATURES,'scope':{'symbol':'BTC','tf':'1D'},'trainedThrough':'2021-06-30','validationRange':['2022-01-01','2023-06-30'],'testFrom':'2024-01-01','selected':chosen,'threshold':t,'lowThreshold':t*.65,'gatePassed':bool(passed),'featureNames':f['names'],'models':artifacts,'report':report,'labelReference':{str(tt):v for tt,v in lab.items()},'auditReference':audit,'referenceDecisions':{str(tt):audit[str(tt)]['status'] for tt in times if str(tt) in audit}}
 (ROOT/'apps/chanlun-web/js/13-ma20-ai-model.js').write_text('window.MA20AIModel = '+json.dumps(model,ensure_ascii=False,allow_nan=False,separators=(',',':'))+';\n')
 out=ROOT/'reports/ma20-ai-2026-11-04';out.mkdir(parents=True,exist_ok=True);(out/('model-'+fingerprint[:10]+'.json')).write_text(json.dumps(model,ensure_ascii=False,allow_nan=False));(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False));(out/'labels.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));
 parity={'names':f['names'],'rows':[{'time':int(times[i]),'values':f['rows'][i]['values'],'scores':{k:float(scores[k][i]) for k in configs}} for i in range(len(times))]};(out/'parity.json').write_text(json.dumps(parity,allow_nan=False))
 # A frozen research record; frontend reads the model artifact, not this report.
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
