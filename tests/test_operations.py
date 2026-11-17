import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pandas as pd
from support import app_for_test,login
from test_tq_migration import chain
from marginlab.storage import connect,read_snapshot
from marginlab.runtime import revision
from marginlab.analysis_cache import AnalysisCache
from marginlab.coverage import audit_day,latest_report,phase
from marginlab.health import publish,read_health
from marginlab.tqstore import save_snapshot,save_bars
from marginlab.history import reconstruct

ZONE=ZoneInfo('Asia/Shanghai')


class CacheTests(unittest.TestCase):
    def test_coalesces_concurrent_requests_and_returns_isolated_results(self):
        cache=AnalysisCache();started=threading.Event();release=threading.Event();calls=[]
        def calculate():
            calls.append(1);started.set();release.wait(2);return {'values':[1]}
        with ThreadPoolExecutor(max_workers=5) as pool:
            first=pool.submit(cache.get,'key',calculate);self.assertTrue(started.wait(1))
            others=[pool.submit(cache.get,'key',calculate) for _ in range(4)]
            release.set();results=[first.result()]+[x.result() for x in others]
        self.assertEqual(len(calls),1)
        results[0][0]['values'].append(2)
        self.assertEqual(cache.get('key',calculate)[0],{'values':[1]})

    def test_capacity_expiry_and_failure_are_bounded(self):
        cache=AnalysisCache(max_bytes=100,max_entries=1,ttl=1)
        with patch('marginlab.analysis_cache.time.monotonic',return_value=0):
            cache.get('a',lambda:{'a':1});cache.get('b',lambda:{'b':1})
        self.assertNotIn('a',cache.entries);self.assertLessEqual(cache.bytes,100)
        with patch('marginlab.analysis_cache.time.monotonic',return_value=2):
            self.assertEqual(cache.get('b',lambda:{'b':2})[1],'miss')
        with self.assertRaises(ValueError):cache.get('bad',lambda:(_ for _ in ()).throw(ValueError()))
        self.assertFalse(cache.pending)
        self.assertEqual(cache.get('bad',lambda:3)[0],3)

    def test_distinct_work_is_bounded_and_waiter_can_timeout(self):
        cache=AnalysisCache(max_computations=1,wait_seconds=.01)
        ready=threading.Event();release=threading.Event()
        def calculate():ready.set();release.wait(2);return 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            work=pool.submit(cache.get,'a',calculate);self.assertTrue(ready.wait(1))
            with self.assertRaises(RuntimeError):cache.get('b',lambda:2)
            with self.assertRaises(RuntimeError):cache.get('a',lambda:2)
            release.set();self.assertEqual(work.result()[0],1)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self);self.path=self.app.config['DATABASE'];self.feed=self.app.extensions['tq_feed']
        self.day='2026-11-16'

    def bar(self,stamp,symbol='SSE.000016'):
        with connect(self.path) as db:db.execute('INSERT INTO tq_bars VALUES(?,?,?,?,?,?,?,?,?)',(symbol,stamp,100,102,99,101,1,1,1))

    def test_revisions_cover_direct_import_edits_deletes_and_rollback(self):
        first=revision(self.path);stamp=self.day+'T09:30:00';self.bar(stamp)
        second=revision(self.path);self.assertGreater(second,first)
        with connect(self.path) as db:
            db.execute('UPDATE tq_bars SET close=close')
            db.execute("INSERT INTO settings VALUES('example','1')")
        self.assertEqual(revision(self.path),second)
        with self.assertRaises(ValueError):
            with connect(self.path) as db:
                db.execute('UPDATE tq_bars SET close=102');raise ValueError()
        self.assertEqual(revision(self.path),second)
        with connect(self.path) as db:db.execute('UPDATE tq_bars SET close=102')
        third=revision(self.path);self.assertGreater(third,second)
        with connect(self.path) as db:db.execute('DELETE FROM tq_bars')
        self.assertGreater(revision(self.path),third)

    def test_snapshot_reads_are_consistent_while_writer_commits(self):
        self.bar(self.day+'T09:30:00')
        with read_snapshot(self.path):
            before=revision(self.path)
            with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(self.bar,self.day+'T09:31:00').result()
            self.assertEqual(revision(self.path),before)
            with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM tq_bars').fetchone()[0],1)
        self.assertGreater(revision(self.path),before)

    def test_coverage_separates_reference_invalid_auction_and_absence(self):
        self.bar(self.day+'T09:30:00');self.bar(self.day+'T09:30:00','CFFEX.IH2612')
        for at,quality in [('09:31','valid'),('09:32','reference'),('09:33','insufficient'),('14:57','valid'),('15:00','auction')]:
            save_snapshot(self.path,'50',self.day+'T'+at+':00',{'quality':quality,'value':18 if quality in ('valid','reference') else None},[])
        with connect(self.path) as db:db.execute("INSERT INTO tq_option_minutes(product,symbol,timestamp) VALUES('50','test',?)",(self.day+'T09:31:00',))
        reports=audit_day(self.path,self.day,datetime(2026,11,16,15,11,tzinfo=ZONE))
        r=next(r for r in reports if r['product']=='50')
        self.assertEqual((r['expected'],r['index_count'],r['future_count'],r['option_minutes']),(240,1,1,1))
        self.assertEqual((r['mvix_valid'],r['mvix_reference'],r['mvix_invalid'],r['auction_recorded'],r['auction_display_carry']),(2,1,1,1,3))
        self.assertEqual(len(r['mvix_missing']),235);self.assertFalse(r['stale'])
        self.bar(self.day+'T09:31:00')
        self.assertTrue(latest_report(self.path,'50')['stale'])
        self.assertFalse(next(r for r in reports if r['product']=='500')['options_applicable'])

    def test_coverage_grace_and_calendar_uncertainty(self):
        early=audit_day(self.path,self.day,datetime(2026,11,16,9,31,10,tzinfo=ZONE))[0]
        self.assertEqual(early['expected'],0)
        later=audit_day(self.path,self.day,datetime(2026,11,16,9,31,40,tzinfo=ZONE))[0]
        self.assertEqual(later['expected'],1);self.assertEqual(later['trading_day_evidence'],'unconfirmed')
        self.assertEqual(phase(datetime(2026,11,16,12,0,tzinfo=ZONE)),'lunch')
        self.assertEqual(phase(datetime(2026,11,16,14,58,tzinfo=ZONE)),'closing_auction')
        self.assertEqual(phase(datetime(2026,11,15,10,0,tzinfo=ZONE)),'weekend')

    def test_maintenance_lease_and_final_check_do_not_run_every_five_seconds(self):
        now=datetime(2026,11,16,15,11,tzinfo=ZONE);m=self.feed.maintenance
        with connect(self.path) as db:db.execute("INSERT INTO tq_maintenance_lease VALUES(1,'other',?)",(now.timestamp()+30,))
        with patch('marginlab.coverage.audit_day') as audit:
            m.tick(now);audit.assert_not_called()
            m.tick(now+timedelta(seconds=31));self.assertEqual(audit.call_count,1)
            m.tick(now+timedelta(seconds=36));self.assertEqual(audit.call_count,1)

    def test_stale_and_auction_evidence_never_become_valid_history(self):
        at=pd.Timestamp(self.day+'T14:58',tz='Asia/Shanghai').timestamp()
        rows=[dict(r,symbol=str(i),quote_epoch=at-1) for i,r in enumerate(chain(at))]
        with patch.object(self.feed,'active',return_value={'50'}):self.feed.snapshot('50',rows,at,market_epoch=at-65)
        with connect(self.path) as db:
            r=db.execute('SELECT quality,quotes FROM tq_snapshots').fetchone()
            self.assertEqual(r['quality'],'auction');self.assertEqual(json.loads(r['quotes'])[0]['bid'],rows[0]['bid'])
            self.assertEqual(db.execute('SELECT count(*) FROM tq_option_minutes').fetchone()[0],len(rows))
        universe=[{k:r[k] for k in ('symbol','expiry','strike','side')} for r in rows]
        result=reconstruct(self.path,'50',universe,self.day+'T14:58',self.day+'T14:58')
        self.assertEqual(result.get('inserted',0),0)

    def test_previous_session_quotes_are_unverified_even_with_fresh_heartbeat(self):
        at=pd.Timestamp(self.day+'T13:01',tz='Asia/Shanghai').timestamp()
        rows=[dict(r,symbol=str(i),quote_epoch=at-7200) for i,r in enumerate(chain(at))]
        with patch.object(self.feed,'active',return_value={'50'}):
            self.feed.snapshot('50',rows,at,market_epoch=at-1)
        with connect(self.path) as db:
            self.assertEqual({r[0] for r in db.execute('SELECT source FROM tq_option_minutes')}, {'tqsdk_live_unverified_evidence'})
            saved=db.execute('SELECT quality,quotes FROM tq_snapshots').fetchone()
            self.assertEqual(saved['quality'],'insufficient')
            self.assertIsNone(json.loads(saved['quotes'])[0]['bid'])

    def test_replica_coverage_stops_at_export_time(self):
        now=datetime(2026,11,16,10,0,tzinfo=ZONE)
        r=audit_day(self.path,self.day,now,asof=now-timedelta(minutes=20))[0]
        self.assertEqual(r['expected'],9)
        self.assertEqual(r['checked_at'],now.isoformat())
        self.assertIsNotNone(r['source_asof'])

    def test_read_only_blocks_scheduled_and_page_demand_collection(self):
        app=app_for_test(self,MARKET_READ_ONLY=True);feed=app.extensions['tq_feed']
        with patch('marginlab.tqfeed.threading.Thread') as thread:
            feed.maintain();feed.touch('50','test')
            self.assertEqual(feed.active(),set());thread.assert_not_called()
        self.assertEqual(feed.state,'replica')

    def test_market_sync_is_authoritative_and_preserves_authentication(self):
        from scripts.sync_market import export_market,import_market
        from pathlib import Path
        self.bar(self.day+'T09:30:00')
        parent=Path(self.path).parent;source=parent/'remote.sqlite3';backup=parent/'backup.sqlite3'
        report=export_market(self.path,source)
        self.bar(self.day+'T09:31:00')
        with connect(self.path) as db:before=list(db.execute('SELECT * FROM admin'))
        import_market(self.path,source,backup)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tq_bars').fetchone()[0],1)
            self.assertEqual(list(db.execute('SELECT * FROM admin')),before)
            self.assertEqual(dict(db.execute('SELECT key,value FROM settings'))['market_read_only'],'1')
            self.assertEqual(dict(db.execute('SELECT key,value FROM settings'))['history_months'],'1')
        with connect(source) as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='admin'").fetchone())
        with connect(backup) as db:self.assertEqual(db.execute('SELECT count(*) FROM tq_bars').fetchone()[0],2)
        self.assertEqual(report['counts']['tq_bars'],1)

    def test_local_entrypoint_sigterm_stops_and_joins_collector(self):
        import runpy,signal
        from unittest.mock import MagicMock
        fake=MagicMock();feed=fake.extensions['tq_feed'];handlers={}
        def run(**kwargs):handlers[signal.SIGTERM](signal.SIGTERM,None)
        fake.run.side_effect=run
        with patch('marginlab.create_app',return_value=fake),patch('signal.signal',side_effect=lambda sig,fn:handlers.update({sig:fn})):
            with self.assertRaises(SystemExit):runpy.run_path('app.py',run_name='__main__')
        feed.stop.assert_called_once()
        feed.thread.join.assert_called_once_with(timeout=10)
        feed.supervisor.join.assert_called_once_with(timeout=5)

    def test_persist_recovers_available_bars_after_long_pause(self):
        from types import SimpleNamespace
        now=pd.Timestamp(self.day+'T10:30',tz='Asia/Shanghai').timestamp()
        frame=pd.DataFrame([dict(datetime=int((now-3600+i*60)*1e9),open=100,high=102,low=99,close=101,volume=1,open_oi=1,close_oi=1) for i in range(60)])
        quote=SimpleNamespace(datetime='',instrument_id='test',last_price=101,open_interest=1,volume=1)
        group=dict(uq=quote,fq=quote,ib=frame,fb=frame,options=[],last_save=now-1200)
        with patch.object(self.feed,'active',return_value={'50'}),patch('marginlab.tqfeed.time.time',return_value=now),patch('marginlab.tqfeed.tqstore.save_bars') as save:
            self.feed.persist('50',group)
            self.assertEqual(len(save.call_args.args[2]),60)
            self.feed.persist('50',group)
            self.assertEqual(len(save.call_args.args[2]),10)

    def test_health_distinguishes_stuck_worker_from_lunch(self):
        now=datetime(2026,11,16,10,0,tzinfo=ZONE).timestamp()
        self.feed.thread=type('Worker',(),{'is_alive':lambda self:True})()
        self.feed.loop_at=now-100;self.feed.worker_started_at=now-200
        with patch('marginlab.health.time.time',return_value=now):publish(self.feed)
        self.assertEqual(read_health(self.path,now)['50']['health_state'],'worker_stalled')
        lunch=datetime(2026,11,16,12,0,tzinfo=ZONE).timestamp()
        with patch('marginlab.health.time.time',return_value=lunch):publish(self.feed)
        self.assertEqual(read_health(self.path,lunch)['50']['health_state'],'waiting')

    def test_requests_do_not_prune_or_recompute_identical_analysis(self):
        c=self.app.test_client();login(c)
        from marginlab import market
        with patch.object(self.feed,'touch'),patch('marginlab.cache.prune') as cleanup,patch('marginlab.market._build_payload',wraps=market._build_payload) as build:
            c.get('/settings')
            a=c.post('/api/research',json={'source':'live','symbol':'50'}).json
            b=c.post('/api/research',json={'source':'live','symbol':'50'}).json
            self.assertEqual(build.call_count,1);cleanup.assert_not_called()
            self.assertEqual((a['analysis_cache']['state'],b['analysis_cache']['state']),('miss','hit'))
            self.bar(self.day+'T09:30:00')
            c.post('/api/research',json={'source':'live','symbol':'50'})
            self.assertEqual(build.call_count,2)

    def test_session_activity_is_throttled_but_revocation_remains_immediate(self):
        c=self.app.test_client();base=time.time()
        with patch('marginlab.auth.time.time',return_value=base):login(c)
        with connect(self.path) as db:before=db.execute('SELECT last_seen FROM sessions WHERE authenticated=1').fetchone()[0]
        with patch('marginlab.auth.time.time',return_value=base+10):self.assertEqual(c.get('/pricing').status_code,200)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT last_seen FROM sessions WHERE authenticated=1').fetchone()[0],before)
        with patch('marginlab.auth.time.time',return_value=base+61):self.assertEqual(c.get('/pricing').status_code,200)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT last_seen FROM sessions WHERE authenticated=1').fetchone()[0],base+61)
            db.execute('DELETE FROM sessions')
        self.assertEqual(c.get('/pricing').status_code,302)

    def test_expired_login_attempts_do_not_block_without_cleanup(self):
        c=self.app.test_client()
        with connect(self.path) as db:db.executemany('INSERT INTO login_attempts(ip,attempted) VALUES(?,?)',[('127.0.0.1',time.time()-1000)]*35)
        self.assertEqual(login(c).status_code,302)


if __name__=='__main__':unittest.main()
