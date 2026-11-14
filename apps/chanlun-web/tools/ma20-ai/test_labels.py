"""Tests material label policy and threshold choice; no file or model mutation."""
import importlib.util,unittest
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location('ai_train',Path(__file__).with_name('train.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def it(source,decision,time=100,**extra):return {'source':source,'decision':decision,'features':{'time':time},**extra}
class LabelPolicy(unittest.TestCase):
 def test_manual_overrides_old_no(self):
  labels,a=module.labels({'items':[it('ma20','no'),it('manual_buy_v1','yes',manual={})]});self.assertEqual(labels[100]['y'],1);self.assertEqual(labels[100]['weight'],1)
 def test_unknown_and_draft_are_not_negative(self):
  labels,a=module.labels({'items':[it('manual_buy_v1',None,manual={}),it('ma20','yes',200)]});self.assertEqual(labels,{});self.assertNotIn(300,labels)
 def test_legacy_conflict_unknown_and_no_weak(self):
  labels,a=module.labels({'items':[it('ma20','no'),it('zzy','yes'),it('ma20','no',200)]});self.assertNotIn(100,labels);self.assertEqual(labels[200]['weight'],.35)
 def test_new_review_confirmed_and_manual_conflict_visible(self):
  doc={'items':[it('ma20','no'),it('manual_buy_v1','yes',200,manual={})]};fb={'symbol':'BTC','tf':'1D','items':[it('ma20_ai_v1','yes'),it('ma20_ai_v1','no',200)]}
  labels,a=module.labels(doc,fb);self.assertEqual(labels[100]['y'],1);self.assertEqual(labels[100]['weight'],1);self.assertNotIn(200,labels);self.assertEqual(a['200']['status'],'current_conflict_unknown')
 def test_uncertain_review_and_archive_do_not_reuse_legacy_negative(self):
  doc={'items':[it('ma20','no'),it('ma20','no',200),it('manual_buy_v1','yes',200,manual={'archived':True})]};fb={'symbol':'BTC','tf':'1D','items':[it('ma20_ai_v1','skip')]}
  lab,a=module.labels(doc,fb);self.assertEqual(lab,{});self.assertEqual(a['100']['status'],'new_review_uncertain_unknown')
 def test_ai_review_priority_over_older_v3_sorted_later(self):
  doc={'items':[it('ma20','no')]};fb={'symbol':'BTC','tf':'1D','items':[it('ma20_ai_v1','yes'),it('ma20_structure_v3','no')]}
  lab,a=module.labels(doc,fb);self.assertEqual(lab[100]['y'],1)
 def test_cross_asset_feedback_rejected(self):
  with self.assertRaises(ValueError):module.labels({'items':[]},{'symbol':'ETH','tf':'1D','items':[]})
 def test_threshold_only_uses_passed_validation(self):
  scores=np.array([.1,.2,.3,.4,.5,.6,.7,.8,.9,1.,.95]);y=np.array([1]*10+[0]);t=module.threshold(scores,y);self.assertEqual(t,.2);self.assertEqual(module.metrics(y,scores,t)['recall'],.9)
if __name__=='__main__':unittest.main()
