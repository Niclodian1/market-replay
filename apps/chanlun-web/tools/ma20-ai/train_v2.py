"""V2 controlled ablation + episode-purged forward checks + reserved blind groups.
No candidate-pool expansion, no optimization on future returns, no automatic filtering.
"""
from pathlib import Path
import json,hashlib,subprocess,datetime,argparse,collections
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from train import labels,prep_fit,trans,threshold,metrics,export,path_stats
ROOT=Path(__file__).resolve().parents[4];HERE=Path(__file__).resolve().parent

def group_times(times,gap_days=21):
 groups={};members={};gid=-1;prev=None
 for t in sorted(set(map(int,times))):
  if prev is None or t-prev>gap_days*86400:gid+=1
  groups[t]=gid;members.setdefault(gid,[]).append(t);prev=t
 return groups,members

def reserve_groups(signals,groups,members,strong_times,size=20,protected_times=None,eligible_times=None):
 strong_groups={groups[t] for t in strong_times};protected_groups={groups[t]for t in (protected_times or strong_times)}
 eligible=set(signals)-set(strong_times) if eligible_times is None else set(eligible_times)-set(strong_times)
 free={g:[t for t in members[g] if t in eligible]for g in members if g not in protected_groups};free={g:v for g,v in free.items()if v}
 rank=lambda g:hashlib.sha256(('ma20-ai-v2-blind:'+str(members[g][0])).encode()).hexdigest()
 # Prefer episodes with no strong labels. If warmup exclusions leave <20,
 # reserve a whole additional episode with old positives, but never new feedback.
 order=sorted([g for g in free if g not in strong_groups],key=rank)+sorted([g for g in free if g in strong_groups],key=rank)
 selected=[];reserved=[]
 for g in order:
  reserved.append(g);selected.extend(free[g])
  if len(selected)>=size:break
 if len(selected)<size:raise ValueError(f'Only {len(selected)} episode-isolated unknown candidates, need {size}')
 return sorted(selected[:size]),sorted(reserved)

def config(kind):
 return LogisticRegression(C=.3,max_iter=2000,random_state=20261104) if kind=='logistic' else GradientBoostingClassifier(n_estimators=40,learning_rate=.05,max_depth=2,min_samples_leaf=12,random_state=20261104)

def metric(y,s,t,strong):
 m=metrics(y,s,t);z=(y==0)&strong;m['confirmed_negative_n']=int(z.sum());m['confirmed_negative_rejected']=int((z&(s<t)).sum());m['confirmed_negative_rejection']=float((s[z]<t).mean()) if z.any() else None;return m

def experiment(lab,allx,times,lookup,groups,members,reserved,indices,signals):
 ids=np.array([lookup[t] for t in sorted(lab) if groups[t] not in reserved]);x=allx[ids][:,indices];y=np.array([lab[int(times[i])]['y'] for i in ids]);w=np.array([lab[int(times[i])]['weight'] for i in ids]);strong=np.array([lab[int(times[i])]['status']!='legacy_negative_weak' for i in ids]);gy=np.array([datetime.datetime.fromtimestamp(members[groups[int(times[i])]][-1],datetime.timezone.utc).year for i in ids]);folds=[];oof=[];oof_candidates=[];val_choices=[];thresholds={'logistic':[],'gbdt':[]}
 for year in range(2020,2027):
  tr=gy<=year-2;va=gy==year-1;te=gy==year
  if not te.any() or any(len(np.unique(y[m]))<2 for m in [tr,va]) or (y[tr]==1).sum()<3:continue
  prep=prep_fit(x[tr]);xx=trans(x,prep);models={};results={}
  for kind in ['logistic','gbdt']:
   model=config(kind);model.fit(xx[tr],y[tr],sample_weight=w[tr]);scores=model.predict_proba(xx)[:,1];th=threshold(scores[va],y[va]);results[kind]={'threshold':th,'validation':metric(y[va],scores[va],th,strong[va]),'check':metric(y[te],scores[te],th,strong[te])};models[kind]=(scores,th,model,prep);thresholds[kind].append(th)
  chosen=max(results,key=lambda k:(results[k]['validation']['precision'] or 0,results[k]['validation']['average_precision'] or 0,k=='logistic'));val_choices.append(chosen);scores,th,chosen_model,chosen_prep=models[chosen]
  folds.append({'checkYear':year,'train_n':int(tr.sum()),'validation_n':int(va.sum()),'check_n':int(te.sum()),'chosen':chosen,'models':results,'trainGroups':sorted({groups[int(times[i])] for i in ids[tr]}),'validationGroups':sorted({groups[int(times[i])] for i in ids[va]}),'checkGroups':sorted({groups[int(times[i])] for i in ids[te]})})
  candidate_ids=[i for i,t in enumerate(times) if int(t) in signals and groups[int(t)] not in reserved and datetime.datetime.fromtimestamp(members[groups[int(t)]][-1],datetime.timezone.utc).year==year]
  if candidate_ids:
   candidate_scores=chosen_model.predict_proba(trans(allx[candidate_ids][:,indices],chosen_prep))[:,1]
   for i,value in zip(candidate_ids,candidate_scores):oof_candidates.append({'i':int(i),'time':int(times[i]),'group':groups[int(times[i])],'score':float(value),'threshold':th,'retained':bool(value>=th),'foldYear':year})
  for j in np.flatnonzero(te):oof.append({'i':int(ids[j]),'time':int(times[ids[j]]),'group':groups[int(times[ids[j]])],'y':int(y[j]),'strong':bool(strong[j]),'score':float(scores[j]),'threshold':th,'retained':bool(scores[j]>=th),'foldYear':year})
 if not folds:raise ValueError('Insufficient temporal folds')
 yy=np.array([r['y']for r in oof]);keep=np.array([r['retained']for r in oof]);ss=np.array([r['strong']for r in oof]);margin=np.array([r['score']-r['threshold']for r in oof]);aggregate=metric(yy,margin,0,ss)
 chosen=collections.Counter(val_choices).most_common(1)[0][0];prep=prep_fit(x);xx=trans(x,prep);fitted={};scores_all={}
 for kind in ['logistic','gbdt']:
  model=config(kind);model.fit(xx,y,sample_weight=w);fitted[kind]=export(model,prep,kind);fitted[kind]['indices']=list(indices);scores_all[kind]=model.predict_proba(trans(allx[:,indices],prep))[:,1]
 return {'aggregate':aggregate,'folds':folds,'oof':oof,'oofCandidates':oof_candidates,'fit_n':len(ids),'fit_positive':int(y.sum()),'fit_confirmed_negative':int(((y==0)&strong).sum()),'fit_weak_negative':int(((y==0)&~strong).sum()),'fit_times':[int(times[i])for i in ids],'chosen':chosen,'threshold':float(np.median(thresholds[chosen])),'thresholdMethod':'median of validation-only thresholds across forward folds; raw score alignment unproven until blind review','thresholds':thresholds},fitted,scores_all

def reject_consumed_blind(*docs):
 if any(it.get('source')=='ma20_ai_v2' and it.get('decision') is not None for doc in docs for it in doc.get('items',[])):
  raise ValueError('V2 blind feedback must be evaluated against its frozen snapshot first; do not retrain V2 on its blind answers.')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--annotations',default='/Users/zhangzheyu/Downloads/annotations-BTC-1D.json');ap.add_argument('--feedback',default='/Users/zhangzheyu/Downloads/annotations-BTC-1D (1).json');args=ap.parse_args();p=Path(args.annotations);q=Path(args.feedback);doc=json.loads(p.read_text());feedback=json.loads(q.read_text());assert doc['symbol']=='BTC' and doc['tf']=='1D'
 reject_consumed_blind(doc,feedback)
 packed=json.loads(subprocess.check_output(['node',str(HERE/'features.cjs'),'--structure'],text=True));f=packed['features'];candles=packed['candles'];sig=set(packed['signals']);times=np.array([r['time'] for r in f['rows']],dtype=np.int64);x=np.array([[np.nan if v is None else v for v in r['values']]for r in f['rows']]);lookup={int(t):i for i,t in enumerate(times)}
 old,audit_old=labels(doc);lab,audit=labels(doc,feedback);assert not (set(lab)-set(lookup));strong={t for t,v in lab.items()if v['weight']==1.};group,members=group_times(set(sig)|set(audit));protected={it['features']['time']for it in feedback['items']if it.get('source')=='ma20_ai_v1'}
 blind,reserved=reserve_groups(sig,group,members,strong,protected_times=protected,eligible_times={t for t in sig if lookup[t]>=120})
 cases={'original_labels':(old,list(range(26))),'updated_labels':(lab,list(range(26))),'updated_structure':(lab,list(range(len(f['names']))))};experiments={};modelsets={};predictions={}
 for name,(y,idx)in cases.items():
  result,models,scores=experiment(y,x,times,lookup,group,members,set(reserved),idx,sig);experiments[name]=result;modelsets[name]=models;predictions[name]=scores;print(name,result['aggregate'],flush=True)
 # Development-only choice. Prefer simpler features unless structure improves negative
 # rejection by at least5pp while preserving recall>=85% and >=baseline-2pp.
 b=experiments['updated_labels']['aggregate'];s=experiments['updated_structure']['aggregate'];selected='updated_structure' if s['recall']>=.85 and s['recall']>=b['recall']-.02 and s['negative_rejection']>=b['negative_rejection']+.05 else 'updated_labels'
 result=experiments[selected];kind=result['chosen'];th=result['threshold'];sc=predictions[selected][kind];fit=set(result['fit_times']);reserved_times=sorted(t for t in group if group[t] in reserved)
 sha=hashlib.sha256(p.read_bytes()+q.read_bytes()+(ROOT/'apps/chanlun-web/data-view/data.js').read_bytes()+(ROOT/'apps/chanlun-web/js/13-ma20-ai-structure.js').read_bytes()+(ROOT/'apps/chanlun-web/js/13-ma20-ai-engine.js').read_bytes()+Path(__file__).read_bytes()).hexdigest();version='MA20-AI-V2-2026-11-04-'+sha[:10]
 oldmodel=json.loads((ROOT/'apps/chanlun-web/js/13-ma20-ai-model.js').read_text().split(' = ',1)[1].strip().rstrip(';'))
 # V1 feedback predictions were recorded before this feedback, not recomputed/fitted.
 fresh=[it for it in feedback['items']if it.get('source')=='ma20_ai_v1'];frozen={'n':len(fresh),'tp':sum(it['decision']=='yes' and it['algorithm']['score']>=it['algorithm']['threshold']for it in fresh),'fp':sum(it['decision']=='no' and it['algorithm']['score']>=it['algorithm']['threshold']for it in fresh),'fn':sum(it['decision']=='yes' and it['algorithm']['score']<it['algorithm']['threshold']for it in fresh),'tn':sum(it['decision']=='no' and it['algorithm']['score']<it['algorithm']['threshold']for it in fresh)}
 curve={}
 for name,e in experiments.items():
  rr=e['oofCandidates'];base=path_stats(candles,[v['i']for v in rr if v['time'] in sig]);keep=path_stats(candles,[v['i']for v in rr if v['time']in sig and v['retained']]);curve[name]={'base_candidates':base,'retained_candidates':keep}
 # Inspect automatic correction region vs 37 manually provided intervals, never predictors.
 regions=[]
 for it in doc['items']:
  iv=it.get('manualEvidence',{}).get('interval');t=it.get('features',{}).get('time')
  if it.get('source')!='manual_buy_v1' or it.get('decision')!='yes' or not iv or t not in lookup:continue
  row=f['rows'][lookup[t]];a=row.get('anchors');start=iv.get('startTime');end=iv.get('endTime');lo=a.get('adjustStart') if a else None;hi=a.get('adjustEnd') if a else None
  if not lo or not hi:continue
  hand={int(tt)for tt in times if start<=tt<=end};auto={int(tt)for tt in times if lo<=tt<=hi};inter=len(hand&auto);union=len(hand|auto);contract=row['values'][30];regions.append({'time':t,'date':it['time'],'handBars':len(hand),'autoBars':len(auto),'iou':inter/union if union else None,'handContraction':iv['lastVsFirstTrueRange'],'autoContraction':contract,'sameContractionDirection':None if contract is None else (contract<1)==(iv['lastVsFirstTrueRange']<1)})
 comparable=[v for v in regions if v['sameContractionDirection'] is not None];region_summary={'n':len(regions),'medianIoU':float(np.median([v['iou']for v in regions])) if regions else None,'directionComparable':len(comparable),'sameContractionDirection':sum(v['sameContractionDirection']for v in comparable),'rows':regions}
 feedback_rows=[]
 for it in fresh:
  t=it['features']['time'];row=f['rows'][lookup[t]];feedback_rows.append({'time':t,'date':it['time'],'decision':it['decision'],'note':it.get('note',''),'reasons':it.get('reasons',[]),'group':group[t],'oldScore':it['algorithm']['score'],'newScore':float(sc[lookup[t]]),'newRetained':bool(sc[lookup[t]]>=th),'usedForFit':t in fit,'structure':dict(zip(f['names'][26:],row['values'][26:])),'anchors':row['anchors']})
 report={'version':version,'labels':{'positive':sum(v['y']==1 for v in lab.values()),'confirmedNegative':sum(v['y']==0 and v['weight']==1 for v in lab.values()),'weakNegative':sum(v['status']=='legacy_negative_weak'for v in lab.values()),'freshFeedback':len(fresh)},'groupPolicy':'Union of labels/candidates; adjacent times<=21calendar days form one episode; whole episode excluded at split boundaries','groups':len(members),'reservedGroups':reserved,'reservedAllTimes':reserved_times,'blindReviewTimes':blind,'fitTimes':sorted(fit),'frozenV1Feedback':frozen,'experiments':experiments,'selectedFeatureSet':selected,'selectedModel':kind,'gatePassed':False,'gateStatus':'pending_new_group_blind_labels','reservationPolicy':'Minimum120 bars context; prefer episodes without strong labels, then reserve additional episodes without any new20 feedback. All labels in reserved episodes excluded from fit and checks.','selectionRule':'Prefer updated26 unless33 has recall>=85%, recall>=26-2pp, negative rejection>=26+5pp on development forward checks','developmentPath120days':curve,'manualRegionAudit':region_summary,'feedbackRows':feedback_rows,'inputs':{'annotationsSha256':hashlib.sha256(p.read_bytes()).hexdigest(),'feedbackSha256':hashlib.sha256(q.read_bytes()).hexdigest(),'marketSha256':hashlib.sha256((ROOT/'apps/chanlun-web/data-view/data.js').read_bytes()).hexdigest()},'limitations':['All20 feedback labels are development, no longer an independent test.','Forward checks include mostly low-confidence legacy negatives; only7 strong negatives, with3 from one March2024 episode.','Final fit uses all development episodes; threshold is median validation threshold, score alignment awaits blind validation.','Reserved group labels are excluded from every fit/validation/check; this is new-label holdout, not new-market prospective evidence.','Automatic rolling-extrema correction anchors are proxies, not validated hand-drawn structure boundaries.','Candidate coverage and filtering recall are separate; original6 pool misses remain.']}
 model={'version':version,'featureVersion':f['version'],'scope':{'symbol':'BTC','tf':'1D'},'trainedThrough':datetime.datetime.fromtimestamp(max(fit),datetime.timezone.utc).strftime('%Y-%m-%d'),'validationRange':['development','group-forward'],'testFrom':None,'selected':kind,'threshold':th,'lowThreshold':th*.65,'gatePassed':False,'featureNames':f['names'],'models':modelsets[selected],'report':report,'labelReference':{str(t):v for t,v in lab.items()},'auditReference':audit,'blindReviewTimes':blind,'reservedAllTimes':reserved_times,'groupReference':{str(t):g for t,g in group.items()},'fitTimes':sorted(fit),'previousVersion':oldmodel['version'],'previousThreshold':oldmodel['threshold']}
 out=ROOT/'reports/ma20-ai-v2-2026-11-04';out.mkdir(parents=True,exist_ok=True)
 (out/'inputs.json').write_text(json.dumps(packed,ensure_ascii=False));(out/'annotations.json').write_bytes(p.read_bytes());(out/'feedback.json').write_bytes(q.read_bytes());(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False));(out/'model.json').write_text(json.dumps(model,ensure_ascii=False,allow_nan=False));(out/'v1-model.json').write_text(json.dumps(oldmodel,ensure_ascii=False,allow_nan=False))
 (ROOT/'apps/chanlun-web/js/13-ma20-ai-model-v2.js').write_text('window.MA20AIModel = '+json.dumps(model,ensure_ascii=False,allow_nan=False,separators=(',',':'))+';\n')
 parity={'names':f['names'],'rows':[{'time':int(times[i]),'values':f['rows'][i]['values'],'scores':{k:float(predictions[selected][k][i])for k in ['logistic','gbdt']}}for i in range(len(times))]};(out/'parity.json').write_text(json.dumps(parity,allow_nan=False))
 print('SELECTED',selected,kind,'blind20',len(blind),'fit',len(fit),'strong negative',result['fit_confirmed_negative']);print('REGION',region_summary['medianIoU'],region_summary['sameContractionDirection'],region_summary['directionComparable']);print('saved',out)
if __name__=='__main__':main()
