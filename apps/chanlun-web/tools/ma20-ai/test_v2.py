import json,unittest
from pathlib import Path
from train_v2 import group_times,reserve_groups,reject_consumed_blind,ROOT
from evaluate_blind import evaluate
OUT=ROOT/'reports/ma20-ai-v2-2026-11-04'
class V2Policy(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.m=json.loads((OUT/'model.json').read_text());cls.r=cls.m['report'];cls.parity={r['time']:r for r in json.loads((OUT/'parity.json').read_text())['rows']}
 def test_episode_and_reservations(self):
  groups,members=group_times([0,10*86400,22*86400,60*86400]);self.assertEqual(groups[0],groups[22*86400]);self.assertNotEqual(groups[0],groups[60*86400])
  reserved=set(self.m['reservedAllTimes']);self.assertFalse(reserved&set(self.m['fitTimes']));self.assertEqual(len(self.m['blindReviewTimes']),20)
  f=json.loads((OUT/'feedback.json').read_text());fresh={x['features']['time']for x in f['items']if x['source']=='ma20_ai_v1'};self.assertFalse(fresh&reserved);self.assertTrue(fresh<=set(self.m['fitTimes']))
  g=self.m['groupReference'];self.assertEqual(g['1710633600'],g['1710892800']);self.assertEqual(g['1710892800'],g['1711324800'])
  for e in self.r['experiments'].values():
   self.assertFalse(reserved&set(e['fit_times']))
   for fold in e['folds']:
    a,b,c=[set(fold[k])for k in ['trainGroups','validationGroups','checkGroups']];self.assertFalse(a&b or a&c or b&c);self.assertFalse(set(self.r['reservedGroups'])&(a|b|c))
 def make_item(self,t,first='yes',current='no'):
  score=self.parity[t]['scores'][self.m['selected']]
  return {'source':'ma20_ai_v2','features':{'time':t},'decision':current,'algorithm':{'version':self.m['version'],'blindReview':{'firstDecision':first,'firstModelVersion':self.m['version'],'firstScore':score,'firstThreshold':self.m['threshold']}}}
 def test_first_judgment_is_frozen(self):
  t=self.m['blindReviewTimes'][0];r=evaluate(self.m,self.parity,{'symbol':'BTC','tf':'1D','items':[self.make_item(t)]});self.assertEqual(r['positive'],1);self.assertEqual(r['negative'],0);self.assertEqual(r['revisedAfterExposure'],1);self.assertFalse(r['automaticPromotion'])
 def test_wrong_version_or_drift_excluded(self):
  t=self.m['blindReviewTimes'][0];a=self.make_item(t);a['algorithm']['version']='other';r=evaluate(self.m,self.parity,{'symbol':'BTC','tf':'1D','items':[a]});self.assertEqual(r['received'],0);self.assertEqual(len(r['problems']),1)
 def test_unknown_never_becomes_negative(self):
  items=[self.make_item(t,'skip','yes')for t in self.m['blindReviewTimes']];r=evaluate(self.m,self.parity,{'symbol':'BTC','tf':'1D','items':items});self.assertEqual(r['firstUncertain'],20);self.assertEqual(r['negative'],0);self.assertEqual(r['positive'],0)
 def test_no_retraining_on_blind(self):
  with self.assertRaises(ValueError):reject_consumed_blind({'items':[{'source':'ma20_ai_v2','decision':'yes'}]})
if __name__=='__main__':unittest.main()
