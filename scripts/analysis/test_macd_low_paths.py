import math
import random
import unittest
from macd_low_paths import evaluate

class Paths(unittest.TestCase):
    def eval(self, future, **kwargs):
        return evaluate([[0,99,101,95,100],*future],0,95,**kwargs)['windows']['end']
    def test_no_credit_entry_bar(self):
        r=self.eval([])
        self.assertEqual(r['mfe_R'],[0,0])
        self.assertEqual(r['statuses'],['live'])
    def test_target_and_stop_same_bar(self):
        r=self.eval([[1,101,115,94,110],[2,110,200,100,190]])
        self.assertEqual(r['loss_stop'],[0,1])
        self.assertEqual(r['breakeven_stop'],[0,1])
        self.assertEqual(r['open'],[0,0])
        self.assertEqual(r['mfe_R'],[.2,3])
        self.assertEqual(r['pnl_R'],[-1,0])
    def test_early_be_cannot_guarantee_full_high(self):
        r=self.eval([[1,101,115,98,112],[2,112,150,110,140]])
        self.assertEqual(r['mfe_R'],[1.5,10])
        self.assertEqual(r['breakeven_stop'],[0,1])
        self.assertEqual(r['open'],[0,1])
    def test_prior_protected_stop_and_high(self):
        r=self.eval([[1,108,110,106,109],[2,109,130,99,120]])
        self.assertEqual(r['breakeven_stop'],[1,1])
        self.assertEqual(r['mfe_R'],[2,6])
        self.assertEqual(r['pnl_R'],[0,0])
    def test_gap_below_be_can_still_lose(self):
        r=self.eval([[1,108,110,106,109],[2,97,140,96,130]])
        self.assertEqual(r['mfe_R'],[2,2])
        self.assertEqual(r['negative_exit'],[1,1])
        self.assertEqual(r['pnl_R'],[-.6,-.6])
    def test_gap_initial_stop(self):
        r=self.eval([[1,92,120,90,110]])
        self.assertEqual(r['pnl_R'],[-1.6,-1.6])
        self.assertEqual(r['armed'],[0,0])
    def test_finer_data_resolves_initial_stop_first(self):
        bars=[[0,99,101,95,100],[10,101,115,94,110]]
        finer=lambda j:[[10,101,102,94,96],[11,96,115,96,110]]
        r=evaluate(bars,0,95,finer=finer)['windows']['end']
        self.assertEqual(r['loss_stop'],[1,1])
        self.assertEqual(r['breakeven_stop'],[0,0])
        self.assertEqual(r['mfe_R'],[.2,.4])
    def test_incomplete_windows_stay_incomplete_even_when_stopped(self):
        r=evaluate([[0,99,101,95,100],[1,100,101,94,96]],0,95)
        self.assertFalse(r['windows']['60']['complete'])
    def test_envelope_contains_concrete_random_intrabar_paths(self):
        random.seed(1729)
        for _ in range(300):
            price=100.0;bars=[[0,99,101,95,100]];paths=[]
            for k in range(1,9):
                op=max(5,price+random.uniform(-5,5))
                points=[op]
                for j in range(9):points.append(max(5,points[-1]+random.uniform(-5,5)))
                bars.append([k,op,max(points),min(points),points[-1]])
                paths.append(points);price=points[-1]
            status='live';protected=False;mfe=0.0
            for points in paths:
                active=100 if protected else 95
                if points[0]<=active:
                    status='be' if protected else 'sl';break
                if points[0]>=107.5:protected=True
                mfe=max(mfe,points[0]-100)
                for px in points[1:]:
                    active=100 if protected else 95
                    if px<=active:
                        status='be' if protected else 'sl';break
                    if px>=107.5:protected=True
                    mfe=max(mfe,px-100)
                if status!='live':break
            model=evaluate(bars,0,95)['windows']['end']
            compatible=[c for c in model['cases'] if c['status']==status]
            self.assertTrue(compatible,(status,bars,model))
            self.assertTrue(any(c['mfe_R'][0]-1e-10<=mfe/5<=c['mfe_R'][1]+1e-10 for c in compatible),(mfe,status,bars,model))

    def test_no_be_baseline(self):
        r=self.eval([[1,101,115,98,112],[2,112,150,110,140]],trigger_r=math.inf)
        self.assertEqual(r['armed'],[0,0])
        self.assertEqual(r['mfe_R'],[10,10])

if __name__=='__main__':unittest.main()
