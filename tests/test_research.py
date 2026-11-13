import unittest
import numpy as np
import pandas as pd
from support import app_for_test, login
from marginlab.analytics import analyze, DriftConfig, validate_bars
from marginlab.data import demo, live
from unittest.mock import patch
from types import SimpleNamespace
from marginlab.pricing import greeks
from marginlab.chip_data import compute_chip


class ResearchTests(unittest.TestCase):
    def client(self):
        client = app_for_test(self).test_client()
        self.assertEqual(login(client).status_code,302)
        return client

    def test_future_data_does_not_change_past(self):
        frame=demo(); prefix=analyze(frame.iloc[:300]); full=analyze(frame)
        pd.testing.assert_frame_equal(prefix['bars'],full['bars'].iloc[:300])
        self.assertEqual(prefix['visits'],[r for r in full['visits'] if r['index']<300])
        self.assertEqual(prefix['anchor'],full['anchor'])

    def test_exact_normal_response_has_zero_residual(self):
        frame=demo(); frame['vix']=20*np.exp(-3*np.log(frame.close/frame.close.iloc[0]))
        result=analyze(frame)['bars']
        self.assertLess(result.residual.dropna().abs().max(),1e-12)
        self.assertNotIn('异常升波',result.state.values)
        self.assertNotIn('异常降波',result.state.values)

    def test_drift_sign_and_independence(self):
        for scenario,sign in [('rising',1),('falling',-1)]:
            frame=demo(scenario=scenario);result=analyze(frame)
            visits=result['visits'];tail=[v['drift'] for v in visits if v['index']>300 and v['drift'] is not None]
            self.assertTrue(tail)
            self.assertGreater(sign*np.median(tail),0)
            for a,b in zip(visits,visits[1:]):
                self.assertGreaterEqual(b['index']-a['index'],4)
                path=frame.close.iloc[a['index']:b['index']]
                self.assertGreaterEqual((path-result['anchor']['price']).abs().max(),result['anchor']['atr'])

    def test_constant_market_is_insufficient(self):
        frame=demo();frame[['open','high','low','close']]=3.;frame.vix=20.
        result=analyze(frame)
        self.assertTrue(result['bars'].corr_return.isna().all())
        self.assertTrue(result['bars'].residual.isna().all())
        self.assertEqual(result['visits'],[])

    def test_chip_volume_conservation_including_boundaries(self):
        frame=pd.DataFrame(dict(timestamp=pd.date_range('2025-01-01',periods=4),price=[1,2,3,3],volume=[10,20,30,40],direction=[1,-1,1,-1]))
        result=compute_chip(frame,dict(bins=2,time_layers=2))
        self.assertEqual(sum(r['total_buy']+r['total_sell'] for r in result),100)
        self.assertEqual(sum(l['buy_vol']+l['sell_vol'] for r in result for l in r['layers']),100)

    def test_greeks_match_finite_differences_and_dividend_parity(self):
        args=dict(S=3,K=3.1,T=.3,r=.02,q=.015,sigma=.25)
        g=greeks(**args);h=1e-4
        price=lambda **changes:greeks(**dict(args,**changes))['fair_price']
        self.assertAlmostEqual(g['delta'],(price(S=3+h)-price(S=3-h))/(2*h),places=6)
        self.assertAlmostEqual(g['vega'],(price(sigma=.25+h)-price(sigma=.25-h))/(2*h),places=6)
        put=greeks(**args,kind='put')['fair_price']
        self.assertAlmostEqual(g['fair_price']-put,3*np.exp(-.015*.3)-3.1*np.exp(-.02*.3))

    def test_validation_duplicates_invalid_prices(self):
        frame=demo().iloc[:30].copy();frame.loc[1,'timestamp']=frame.loc[0,'timestamp']
        with self.assertRaises(ValueError):validate_bars(frame)
        frame=demo().iloc[:30].copy();frame.loc[1,'vix']=0
        with self.assertRaises(ValueError):validate_bars(frame)

    def test_live_join_uses_dates_and_never_forward_fills(self):
        dates=pd.bdate_range('2024-01-01',periods=30)
        prices=pd.DataFrame({'日期':dates,'开盘':3.,'最高':3.1,'最低':2.9,'收盘':3.,'成交量':100})
        vols=pd.DataFrame({'date':dates.delete(4),'close':20.})
        # Identical duplicates are safe; conflicting dates outside the requested range do not contaminate it.
        vols=pd.concat([vols,vols.iloc[:1],pd.DataFrame({'date':[pd.Timestamp('2020-01-01')]*2,'close':[20.,21.]})],ignore_index=True)
        provider=SimpleNamespace(fund_etf_hist_em=lambda **kwargs:prices,index_option_50etf_qvix=lambda:vols)
        with patch('marginlab.data.ak_module',return_value=provider):
            frame=live('510050','daily','2024-01-01','2024-03-01')
        self.assertEqual(len(frame),29)
        self.assertNotIn(dates[4],frame.timestamp.tolist())

    def test_csv_timezone_and_roundtrip(self):
        frame=demo().iloc[:30].copy()
        frame['timestamp']=frame.timestamp.dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC')
        valid=validate_bars(frame)
        self.assertEqual(valid.timestamp.iloc[0],demo().timestamp.iloc[0])
        c=self.client()
        response=c.post('/api/research',json={'source':'csv','csv':valid.to_csv(index=False)})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['meta']['source'],'csv')

    def test_ranking_stats_do_not_use_future_appearances(self):
        from marginlab.ranking import fetch_ranking
        def row(user,rank,profit):
            return dict(userName=user,sortID=rank,totalProfit=profit,commission=1)
        days={'20240101':[row('A',2,10),row('B',1,20)],'20240102':[row('B',1,30)],'20240103':[row('A',1,40)]}
        with patch('marginlab.ranking.day_rows',side_effect=lambda day,*args:days[day]):
            rows=fetch_ranking('2024-01-01','2024-01-03')
        first=next(r for r in rows if r['userName']=='A' and r['curDate']=='2024-01-01')
        final=next(r for r in rows if r['userName']=='A' and r['curDate']=='2024-01-03')
        self.assertEqual(first['days_on_list'],1)
        self.assertEqual(first['best_rank'],2)
        self.assertEqual(final['rank_change'],'重新进榜')
        self.assertEqual(final['days_on_list'],2)
        self.assertEqual(final['profit_change'],30)

    def test_api_contracts_and_errors(self):
        c=self.client()
        for path in ['/','/chips','/api/research','/api/stress','/option/greek?type=put']:
            self.assertEqual(c.get(path).status_code,200,path)
        for body in [dict(window=0),dict(horizon='NaN'),dict(source='bad'),dict(source='csv',csv='bad'),dict(source='live',symbol='unsupported',period='15')]:
            self.assertEqual(c.post('/api/research',json=body).status_code,400)
        self.assertEqual(c.post('/api/frame',json={'frame_idx':-1}).status_code,400)
        self.assertEqual(c.post('/api/frame',json={'frame_idx':0}).status_code,200)
        self.assertEqual(c.post('/api/regenerate',json={'n_records':0}).status_code,400)
        self.assertEqual(c.get('/option/greek?type=oops').status_code,400)
        payload=c.get('/api/research').data
        self.assertNotIn(b'NaN',payload)
        self.assertNotIn(b'Infinity',payload)


if __name__=='__main__':unittest.main()
