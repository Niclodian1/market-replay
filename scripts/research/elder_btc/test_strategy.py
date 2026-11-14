import unittest
from unittest.mock import patch
from dataclasses import replace
import numpy as np
import pandas as pd
from strategy import Config,indicators,make_signals,backtest,ema

class ExecutionTests(unittest.TestCase):
    def fixture(self,rows):
        d=pd.DataFrame(rows,columns=['open','high','low','close'])
        d['date']=pd.date_range('2020-01-01',periods=len(d),tz='UTC');d['volume']=1
        d['atr']=1.;d['upper']=200.;d['safezone']=80.
        n=len(d);s={k:np.zeros(n,bool) for k in ['setup','exit']}
        s.update(price=np.full(n,100.),stop=np.full(n,90.),target=np.full(n,120.));s['setup'][0]=True
        return d,s
    def run_case(self,rows,cfg=None,ordering='adverse'):
        d,s=self.fixture(rows)
        with patch('strategy.make_signals',return_value=s):
            return backtest(d,cfg or Config(entry='market',trail=False,fee=0,slip=0),str(d.date.iloc[0].date()),str(d.date.iloc[-1].date()),ordering)
    def test_next_open_not_signal_close_and_costs(self):
        r=self.run_case([[100,101,99,100],[105,110,100,108]],Config(entry='market',trail=False))
        t=r['trades'][0];self.assertEqual(t['signal_date'],'2020-01-01');self.assertAlmostEqual(t['entry'],105*1.0005)
        self.assertAlmostEqual(t['exit'],108*.9995)
        self.assertAlmostEqual(r['equity'][-1]-100000,t['pnl'])
    def test_stop_target_same_bar_adverse(self):
        rows=[[100,101,99,100],[100,125,85,110]]
        self.assertEqual(self.run_case(rows)['trades'][0]['exit'],90)
        self.assertEqual(self.run_case(rows,ordering='favorable')['trades'][0]['exit'],120)
    def test_stop_gap_uses_open(self):
        r=self.run_case([[100,101,99,100],[100,110,95,105],[80,100,70,90]])
        self.assertEqual(r['trades'][0]['exit'],80);self.assertEqual(r['trades'][0]['reason'],'gap_stop')
    def test_open_target_gap(self):
        r=self.run_case([[100,101,99,100],[100,110,95,105],[125,130,115,125]])
        self.assertEqual(r['trades'][0]['exit'],125)
    def test_pending_stop_entry_ambiguity(self):
        rows=[[95,99,94,95],[95,110,85,105]];cfg=Config(entry='stop',trail=False,fee=0,slip=0)
        a=self.run_case(rows,cfg);b=self.run_case(rows,cfg,'favorable')
        self.assertEqual(a['trades'][0]['reason'],'stop');self.assertEqual(b['trades'][0]['reason'],'boundary_liquidation')
    def test_touch_cannot_survive_low_below_stop_without_target(self):
        rows=[[105,109,104,105],[105,110,85,105]];cfg=Config(entry='touch',trail=False,fee=0,slip=0)
        r=self.run_case(rows,cfg,'favorable');self.assertEqual(r['trades'][0]['reason'],'stop')
    def test_stop_entry_close_below_stop_forces_exit_even_favorable(self):
        rows=[[95,99,94,95],[95,110,85,88]];cfg=Config(entry='stop',trail=False,fee=0,slip=0)
        r=self.run_case(rows,cfg,'favorable');self.assertEqual(r['trades'][0]['reason'],'stop')
    def test_no_reentry_on_exit_bar(self):
        d,s=self.fixture([[100,101,99,100],[100,110,95,105],[100,130,85,110],[100,101,99,100]])
        s['setup'][1]=True
        with patch('strategy.make_signals',return_value=s):r=backtest(d,Config(entry='market',trail=False),'2020-01-01','2020-01-04')
        self.assertEqual(len(r['trades']),1)
    def test_intraday_high_cannot_ratchet_same_bar(self):
        r=self.run_case([[100,101,99,100],[100,119,95,110],[93,100,92,95]],Config(entry='market',trail=True,fee=0,slip=0))
        self.assertEqual(r['trades'][0]['exit_date'],'2020-01-03')
    def test_no_leverage_and_unit_risk_budget(self):
        r=self.run_case([[100,101,99,100],[100,110,95,105]],Config(entry='market',trail=False,risk=.02))
        t=r['trades'][0];self.assertLessEqual(t['cost'],100000+1e-8);self.assertLessEqual(t['risk_money'],2000+1e-8)
    def test_ema_seed_and_prefix(self):
        a=np.arange(1.,11.);e=ema(a,3);self.assertTrue(np.isnan(e[1]));self.assertEqual(e[2],2.)
        np.testing.assert_allclose(e[:7],ema(a[:7],3),equal_nan=True)
    def test_monthly_budget_stops_new_orders_and_resets(self):
        d,s=self.fixture([[100,110,85,100]]*40);s['setup'][:]=True
        with patch('strategy.make_signals',return_value=s):
            r=backtest(d,Config(entry='market',trail=False,risk=.02,fee=0,slip=0),'2020-01-01','2020-02-09')
        january=[t for t in r['trades'] if t['entry_date'].startswith('2020-01')]
        self.assertEqual(len(january),3);self.assertGreater(r['blocked_orders'],20)
        self.assertTrue(any(t['entry_date']=='2020-02-01' for t in r['trades']))
    def test_all_indicators_and_signals_prefix(self):
        rng=np.random.default_rng(1);n=850;c=100*np.exp(np.cumsum(rng.normal(0,.02,n)))
        d=pd.DataFrame(dict(date=pd.date_range('2017-01-01',periods=n,tz='UTC'),open=c,high=c*1.02,low=c*.98,close=c,volume=rng.uniform(1,10,n)))
        full=indicators(d)
        for k in [201,333,504,701,843]:
            part=indicators(d.iloc[:k])
            for col in ['fast','slow','hist','atr','force2','upper','lower','rsi','stoch','adx','divergence','weekly_color','weekly_ready']:
                np.testing.assert_allclose(full[col].iloc[:k],part[col],equal_nan=True,err_msg=col)
            a=make_signals(full,Config());b=make_signals(part,Config())
            for key in a:np.testing.assert_allclose(a[key][:k],b[key],equal_nan=True)

if __name__=='__main__':unittest.main()
