import json
import math
import time
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from support import app_for_test, login
from marginlab.mvix import calculate,term_variance
from marginlab.tqstore import save_snapshot,save_bars,read_bars
from marginlab.storage import connect
from marginlab.market import resample
from marginlab.analytics import analyze
from marginlab.data import demo


def chain(at,days=30):
    rows=[]
    otm={80:1.,90:2.,100:4.,110:2.,120:1.}
    for k,v in otm.items():
        c,p=(v+100-k,v) if k<100 else (v,v+k-100)
        for side,price in [('CALL',c),('PUT',p)]:
            rows.append(dict(expiry=at+days*86400,strike=k,side=side,bid=price-.1,ask=price+.1))
    return rows


class MVIXTests(unittest.TestCase):
    def test_hand_calculated_variance_and_bracket(self):
        at=1700000000
        expected=2/(30/365)*sum(10*p/k**2 for k,p in [(80,1),(90,2),(100,4),(110,2),(120,1)])
        single=calculate(chain(at),at,0)
        self.assertAlmostEqual(single['value'],100*math.sqrt(expected),places=10)
        self.assertAlmostEqual(single['near']['forward'],100)
        double=calculate(chain(at,20)+chain(at,40),at,0)
        self.assertAlmostEqual(double['value'],single['value'],places=10)
        self.assertAlmostEqual(double['weight_near'],.5)
        with self.assertRaises(ValueError):calculate(chain(at,2)+chain(at,23),at,0)

    def test_zero_bid_tail_and_invalid_interior(self):
        at=1700000000
        rows=chain(at)
        base=calculate(rows,at,0)['value']
        for k in [70,60,50]:
            for side in ['CALL','PUT']:
                # Tail CALL is ITM; preserve parity so F selection is unaffected.
                p=0 if k in [70,60] else 1
                price=p+100-k if side=='CALL' else p
                rows.append(dict(expiry=at+30*86400,strike=k,side=side,bid=price,ask=price+.2))
        self.assertAlmostEqual(calculate(rows,at,0)['value'],base)
        bad=chain(at);bad[3]['ask']=None
        with self.assertRaises(ValueError):calculate(bad,at,0)

    def test_missing_mvix_never_creates_returns_across_gap(self):
        df=demo();df.loc[100:110,'vix']=np.nan
        result=analyze(df)['bars']
        self.assertTrue(result.residual.iloc[100:172].isna().all())
        self.assertTrue(result.corr_return.iloc[110:120].isna().all())
        prefix=analyze(df.iloc[:300])
        pd.testing.assert_frame_equal(prefix['bars'],result.iloc[:300])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self)
        self.c=self.app.test_client()
        self.feed=self.app.extensions['tq_feed']

    def test_auth_post_gate_and_logout_stops_demand(self):
        with patch.object(self.feed,'touch') as touch:
            self.assertEqual(self.c.post('/api/research',json={'source':'live','symbol':'50'}).status_code,401)
            self.assertFalse(touch.called)
            login(self.c)
            self.assertEqual(self.c.get('/api/research?source=live&symbol=50').status_code,405)
            self.assertFalse(touch.called)
            response=self.c.post('/api/research',json={'source':'live','symbol':'50','period':'1'})
            self.assertEqual(response.status_code,200)
            self.assertIsNone(response.json['research'])
            self.assertEqual(response.json['source'],'tqsdk')
            touch.assert_called_once()
            product,token=touch.call_args.args
            self.feed.demands[(product,token)]=time.time()+90
            self.assertEqual(self.feed.active(),{'50'})
            self.c.post('/logout')
            self.assertEqual(self.feed.active(),set())

    def test_minute_storage_retention_and_no_future_snapshot(self):
        now=pd.Timestamp.now(tz='Asia/Shanghai').tz_localize(None).floor('min')
        frame=pd.DataFrame(dict(datetime=[now.tz_localize('Asia/Shanghai').value, (now+pd.Timedelta(minutes=1)).tz_localize('Asia/Shanghai').value],open=[1,2],high=[1,2],low=[1,2],close=[1,2],volume=[3,4],open_oi=[5,6],close_oi=[5,6]))
        save_bars(self.app.config['DATABASE'],'test',frame,'2020-01-01')
        self.assertEqual(len(read_bars(self.app.config['DATABASE'],'test','2020','2100')),2)
        stamp=now.isoformat()
        save_snapshot(self.app.config['DATABASE'],'50',stamp,{'value':20,'quality':'valid'},[])
        save_snapshot(self.app.config['DATABASE'],'50',stamp,{'value':99,'quality':'valid'},[])
        with connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT value FROM tq_snapshots').fetchone()[0],20)
        save_snapshot(self.app.config['DATABASE'],'50','2000-01-01T15:00:00',{'value':10,'quality':'valid'},[])
        from marginlab.cache import prune
        prune(self.app)
        with connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_snapshots').fetchone()[0],1)

    def test_session_resampling_excludes_partial_and_preserves_volume(self):
        dates=list(pd.date_range('2026-11-06 09:30',periods=120,freq='min'))+list(pd.date_range('2026-11-06 13:00',periods=120,freq='min'))
        rows=[dict(timestamp=t.isoformat(),open=1,high=2,low=1,close=2,volume=1,open_oi=3,close_oi=4) for t in dates]
        out=resample(rows,'60')
        self.assertEqual(len(out),4)
        self.assertEqual(out.volume.sum(),240)
        self.assertEqual(out.timestamp.dt.hour.tolist(),[9,10,13,14])
        self.assertEqual(len(resample(rows[:-1],'60')),3)
        self.assertEqual(len(resample(rows,'daily')),1)

    def test_snapshot_filters_future_quotes_and_revocation(self):
        at=pd.Timestamp('2026-11-06 10:01',tz='Asia/Shanghai').timestamp()
        rows=chain(at)
        for r in rows:r.update(quote_epoch=at+1,quote_time='future')
        with patch.object(self.feed,'active',return_value={'50'}):
            self.feed.snapshot('50',rows,at)
        with connect(self.app.config['DATABASE']) as db:
            saved=db.execute('SELECT value,quality FROM tq_snapshots').fetchone()
            self.assertIsNone(saved['value']);self.assertEqual(saved['quality'],'insufficient')
        with patch.object(self.feed,'active',return_value=set()):
            self.feed.snapshot('50',rows,at+60)
        with connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_snapshots').fetchone()[0],1)

    def test_cached_minute_skips_calculation_after_restart(self):
        at=pd.Timestamp('2026-11-06 15:00',tz='Asia/Shanghai').timestamp()
        save_snapshot(self.app.config['DATABASE'],'50','2026-11-06T15:00:00',
                      {'value':17.52,'quality':'valid'},[])
        from marginlab.tqfeed import Feed
        restarted=Feed(self.app)
        with patch('marginlab.tqfeed.calculate') as compute:
            for _ in range(3):
                result=restarted.snapshot('50',[],at)
                self.assertEqual(result['value'],17.52)
            compute.assert_not_called()

    def test_cached_mvix_visible_without_research_warmup_or_connection(self):
        login(self.c)
        date=pd.Timestamp.now(tz='Asia/Shanghai').date().isoformat()
        save_snapshot(self.app.config['DATABASE'],'50',date+'T10:01:00',
                      {'value':17.52,'quality':'valid'},[])
        save_snapshot(self.app.config['DATABASE'],'50',date+'T10:02:00',
                      {'value':None,'quality':'insufficient','reason':'missing'},[])
        self.feed.state='error';self.feed.error='offline'
        with patch.object(self.feed,'touch'):
            data=self.c.post('/api/research',json={'source':'live','symbol':'50','period':'1'}).json
        self.assertIsNone(data['research'])
        self.assertEqual(data['mvix_latest_valid']['value'],17.52)
        self.assertEqual(data['mvix']['quality'],'insufficient')
        self.assertEqual(data['mvix_cache']['valid_count'],1)
        self.assertEqual(data['mvix_history'],[
            {'timestamp':date+'T10:01:00','value':17.52},
            {'timestamp':date+'T10:02:00','value':None}])

    def test_chips_unknown_direction_and_old_sources_disabled(self):
        login(self.c)
        with patch.object(self.feed,'touch'):
            response=self.c.post('/api/chips',json={'symbol':'50'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['direction'],'unknown')
        self.assertEqual(self.c.get('/api/options/value').status_code,410)
        self.assertEqual(self.c.get('/api/futures').status_code,410)
        html=self.c.get('/chips').text
        self.assertIn('统一展示',html)
        self.assertNotIn('买入→右',html)


if __name__=='__main__':unittest.main()
