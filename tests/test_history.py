import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd
from support import app_for_test
from test_tq_migration import chain
from marginlab.history import initialize,minute_quotes,store_minutes,reconstruct
from marginlab.storage import connect
from marginlab.tqstore import save_snapshot
from marginlab.market import payload


class HistoryTests(unittest.TestCase):
    def test_live_unchanged_quote_requires_fresh_heartbeat_and_same_session(self):
        app=app_for_test(self);feed=app.extensions['tq_feed']
        at=pd.Timestamp('2026-11-06 14:00',tz='Asia/Shanghai').timestamp()
        rows=[dict(r,symbol=str(i),quote_epoch=at-1800) for i,r in enumerate(chain(at))]
        with patch.object(feed,'active',return_value={'50','300','1000'}):
            feed.snapshot('50',rows,at,market_epoch=at-1)
            feed.snapshot('300',rows,at,market_epoch=at-60)
            morning=[dict(r,quote_epoch=at-3*3600) for r in rows]
            feed.snapshot('1000',morning,at,market_epoch=at-1)
        with connect(app.config['DATABASE']) as db:
            quality=dict(db.execute('SELECT product,quality FROM tq_snapshots'))
        self.assertEqual(quality,{'50':'valid','1000':'insufficient'})
        with connect(app.config['DATABASE']) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tq_option_minutes WHERE product='50'").fetchone()[0],len(rows))

    def test_contiguous_history_keeps_unchanged_quote_without_future_prices(self):
        times=pd.to_datetime(['2026-11-06 13:01:00','2026-11-06 13:36:32']).tz_localize('Asia/Shanghai')
        ticks=pd.DataFrame(dict(id=[10,11],datetime=times.asi8,bid_price1=[.4,.5],ask_price1=[.8,.9]))
        rows=minute_quotes(ticks,{},'2026-11-06 13:02','2026-11-06 13:40',max_age=None)
        self.assertEqual(len(rows),35)
        self.assertTrue(all(r['bid']==.4 for r in rows))
        self.assertEqual(rows[-1]['timestamp'],'2026-11-06T13:36:00')
        self.assertTrue(all(r['quote_ns']==times[0].value for r in rows))
        ticks.loc[1,'id']=12
        with self.assertRaises(ValueError):minute_quotes(ticks,{},times[0],times[-1],max_age=None)

    def test_contiguous_history_does_not_skip_invalid_update_or_lunch(self):
        times=pd.to_datetime(['2026-11-06 11:29:59','2026-11-06 13:05:00','2026-11-06 13:06:00','2026-11-06 13:30:00']).tz_localize('Asia/Shanghai')
        ticks=pd.DataFrame(dict(id=[1,2,3,4],datetime=times.asi8,bid_price1=[1,2,float('nan'),3],ask_price1=[2,3,float('nan'),4]))
        rows=minute_quotes(ticks,{},times[0],times[-1],max_age=None)
        by_time={r['timestamp']:r for r in rows}
        self.assertNotIn('2026-11-06T13:01:00',by_time)
        self.assertEqual(by_time['2026-11-06T13:06:00']['bid'],2)
        self.assertIsNone(by_time['2026-11-06T13:20:00']['bid'])

    def test_api_keeps_missing_minutes_and_prunes_quote_evidence(self):
        app=app_for_test(self);path=app.config['DATABASE']
        day=pd.Timestamp.now(tz='Asia/Shanghai').normalize()
        while day.weekday()>=5:day-=pd.Timedelta(days=1)
        start=day.tz_localize(None)+pd.Timedelta(hours=10)
        for i in (1,3):
            save_snapshot(path,'50',(start+pd.Timedelta(minutes=i)).isoformat(),{'quality':'valid','value':20+i},[])
        with connect(path) as db:
            db.execute("INSERT INTO tq_option_minutes(product,symbol,timestamp) VALUES('50','old','2000-01-01T10:00:00')")
        with patch('marginlab.market.request_feed',return_value=app.extensions['tq_feed']):
            result=payload(app,{'symbol':'50','period':'1'})
        self.assertEqual([r['value'] for r in result['mvix_history']],[21,None,23])
        self.assertEqual(result['mvix_cache']['valid_count'],2)
        with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_option_minutes').fetchone()[0],0)

    def test_asof_never_uses_future_or_cross_session(self):
        times=pd.to_datetime(['2026-11-06 10:00:59','2026-11-06 10:01:01','2026-11-06 11:29:59']).tz_localize('Asia/Shanghai')
        ticks=pd.DataFrame(dict(datetime=times.asi8,bid_price1=[10,20,30],ask_price1=[12,22,32]))
        metadata=dict(symbol='test',expiry=1,strike=100,side='CALL')
        rows=minute_quotes(ticks,metadata,'2026-11-06 10:01','2026-11-06 13:01')
        by_time={r['timestamp']:r for r in rows}
        self.assertEqual(by_time['2026-11-06T10:01:00']['bid'],10)
        self.assertEqual(by_time['2026-11-06T10:02:00']['bid'],20)
        self.assertNotIn('2026-11-06T13:01:00',by_time)
        self.assertTrue(all(r['quote_ns'] < pd.Timestamp(r['timestamp'],tz='Asia/Shanghai').value for r in rows))

    def test_exact_boundary_tick_belongs_to_next_minute(self):
        at=pd.Timestamp('2026-11-06 10:01',tz='Asia/Shanghai')
        ticks=pd.DataFrame(dict(datetime=[at.value],bid_price1=[10],ask_price1=[12]))
        rows=minute_quotes(ticks,{},at,at+pd.Timedelta(minutes=1))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['timestamp'],'2026-11-06T10:02:00')

    def test_history_repairs_failed_snapshot_and_preserves_valid(self):
        app=app_for_test(self);path=app.config['DATABASE'];initialize(path)
        at=pd.Timestamp('2026-11-06 10:01',tz='Asia/Shanghai');stamp=at.tz_localize(None).isoformat()
        universe=[];samples=[]
        for i,r in enumerate(chain(at.timestamp())):
            meta=dict(r,symbol=str(i));universe.append(meta)
            samples.append(dict(meta,timestamp=stamp,quote_ns=at.value-1000000000,bid_volume=1,ask_volume=1))
        store_minutes(path,'50',samples)
        save_snapshot(path,'50',stamp,{'quality':'insufficient','value':None},[])
        result=reconstruct(path,'50',universe,at,at,rate=0)
        self.assertEqual(result['inserted'],1)
        with patch('marginlab.history.calculate') as compute:
            again=reconstruct(path,'50',universe,at,at,rate=0)
            self.assertEqual(again['cached_valid'],1);compute.assert_not_called()
        with connect(path) as db:
            row=db.execute('SELECT quality,value,quotes FROM tq_snapshots').fetchone()
            self.assertEqual(row['quality'],'valid');self.assertGreater(row['value'],0)
            self.assertIn('quote_ns',row['quotes'])

    def test_missing_contract_does_not_make_fake_curve(self):
        app=app_for_test(self);path=app.config['DATABASE'];initialize(path)
        at=pd.Timestamp('2026-11-06 10:01',tz='Asia/Shanghai')
        universe=[dict(r,symbol=str(i)) for i,r in enumerate(chain(at.timestamp()))]
        result=reconstruct(path,'50',universe,at,at)
        self.assertEqual(result['incomplete_chain'],1)
        with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_snapshots').fetchone()[0],0)


if __name__=='__main__':unittest.main()
