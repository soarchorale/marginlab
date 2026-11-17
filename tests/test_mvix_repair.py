import copy
import json
import math
from pathlib import Path
import unittest
import pandas as pd
from support import app_for_test
from test_tq_migration import chain
from marginlab.mvix import calculate,METHOD
from marginlab.mvix_repair import replay,apply_repairs
from marginlab.storage import connect
from marginlab.tqstore import save_snapshot


def wide_chain(at):
    rows=chain(at)
    for strike,put in [(60,.4),(70,.6)]:
        for side,price in [('CALL',put+100-strike),('PUT',put)]:
            rows.append(dict(expiry=at+30*86400,strike=strike,side=side,bid=price-.1,ask=price+.1))
    return [dict(r,symbol=str(i),quote_epoch=at-2) for i,r in enumerate(rows)]


class NullStrikeTests(unittest.TestCase):
    def test_null_wing_is_removed_from_both_sides_and_weights_recomputed(self):
        at=1700000000;rows=wide_chain(at)
        next(r for r in rows if r['strike']==80 and r['side']=='CALL')['bid']=None
        before=copy.deepcopy(rows);result=calculate(rows,at,0)
        terms=result['near'];self.assertEqual(terms['k0'],100)
        self.assertEqual(terms['strikes'],[60,70,90,100,110,120])
        expected=2/(30/365)*sum(delta*mid/k**2 for delta,mid,k in [(10,.4,60),(15,.6,70),(15,2,90),(10,4,100),(10,2,110),(10,1,120)])
        self.assertAlmostEqual(result['value'],100*math.sqrt(expected),places=10)
        self.assertEqual(terms['excluded_strikes'],[dict(strike=80.,sides=['CALL'],reason='null_quote')])
        self.assertEqual(rows,before);self.assertEqual(result['method'],METHOD)

    def test_k0_null_and_crossed_wing_still_fail(self):
        at=1700000000;rows=wide_chain(at)
        next(r for r in rows if r['strike']==100 and r['side']=='PUT')['ask']=None
        with self.assertRaisesRegex(ValueError,'K0'):calculate(rows,at,0)
        rows=wide_chain(at);r=next(r for r in rows if r['strike']==80 and r['side']=='PUT');r['bid']=r['ask']+1
        with self.assertRaisesRegex(ValueError,'crossed'):calculate(rows,at,0)

    def test_null_does_not_become_zero_and_sparse_wing_stays_invalid(self):
        at=1700000000;rows=chain(at)
        next(r for r in rows if r['strike']==80 and r['side']=='PUT')['bid']=None
        with self.assertRaisesRegex(ValueError,'At least two'):calculate(rows,at,0)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self);self.path=self.app.config['DATABASE']
        self.stamp='2026-11-17T13:03:00'
        self.at=pd.Timestamp(self.stamp,tz='Asia/Shanghai').timestamp()
        self.rows=wide_chain(self.at)
        next(r for r in self.rows if r['strike']==80 and r['side']=='PUT')['bid']=None
        self.details=dict(quality='insufficient',value=None,reason='Missing or crossed quote within the selected option chain',
                          unusable_timestamps=0,quote_count=len(self.rows),market_heartbeat_epoch=self.at-3,rate=.02)

    def save(self,product='50',details=None,rows=None):
        save_snapshot(self.path,product,self.stamp,self.details if details is None else details,self.rows if rows is None else rows)

    def test_apply_preserves_raw_evidence_valid_and_reference_and_is_idempotent(self):
        self.save();clean=wide_chain(self.at);valid=calculate(clean,self.at,.02);valid['quality']='valid'
        self.save('300',valid,clean);self.save('1000',dict(quality='reference',value=22),clean)
        with connect(self.path) as db:before=[tuple(r) for r in db.execute('SELECT * FROM tq_snapshots ORDER BY product')]
        report,plans=replay(self.path);self.assertEqual(len(plans),1)
        self.assertEqual(report['products']['300']['valid_unchanged'],1)
        backup=Path(self.path).with_name('before.sqlite3');self.assertEqual(apply_repairs(self.path,plans,backup),1)
        with connect(self.path) as db:
            r=db.execute("SELECT * FROM tq_snapshots WHERE product='50'").fetchone();d=json.loads(r['details'])
            self.assertEqual(r['quality'],'valid');self.assertEqual(r['quotes'],plans[0][0]['quotes'])
            self.assertEqual(d['method'],METHOD);self.assertEqual(d['recovery']['previous']['quality'],'insufficient')
            self.assertNotIn('reason',d)
            after=[tuple(r) for r in db.execute("SELECT * FROM tq_snapshots WHERE product<>'50' ORDER BY product")]
            self.assertEqual(after,[r for r in before if r[0]!='50'])
        with connect(backup) as db:self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM tq_snapshots ORDER BY product')],before)
        self.assertEqual(replay(self.path)[1],[])

    def test_time_invalid_and_unknown_rate_are_not_repaired(self):
        cases=[dict(unusable_timestamps=1),dict(market_heartbeat_epoch=self.at-90),dict(evidence_verified=False)]
        for i,extra in enumerate(cases):self.save(str(i),dict(self.details,**extra))
        future=copy.deepcopy(self.rows);future[0]['quote_epoch']=self.at+1;self.save('future',rows=future)
        details=dict(self.details);details.pop('rate');self.save('rate',details)
        self.assertEqual(replay(self.path)[1],[])
        report,plans=replay(self.path,.02);self.assertEqual(len(plans),1)
        self.assertEqual(plans[0][1]['recovery']['rate_basis'],'explicit_legacy_assumption')

    def test_changed_valid_is_reported_not_overwritten_and_races_abort(self):
        rows=wide_chain(self.at);valid=calculate(rows,self.at,.02);valid['quality']='valid'
        next(r for r in rows if r['strike']==80 and r['side']=='CALL')['bid']=None
        self.save('300',valid,rows);self.save()
        report,plans=replay(self.path);self.assertEqual(report['products']['300']['valid_changed_preserved'],1)
        self.assertEqual(len(report['valid_changes']),1)
        with connect(self.path) as db:db.execute("UPDATE tq_snapshots SET details='{}' WHERE product='50'")
        with self.assertRaisesRegex(RuntimeError,'changed'):apply_repairs(self.path,plans,Path(self.path).with_name('race.sqlite3'))
        with connect(self.path) as db:self.assertEqual(db.execute("SELECT quality FROM tq_snapshots WHERE product='50'").fetchone()[0],'insufficient')

    def test_live_time_mask_is_not_bypassed_by_null_strike_removal(self):
        from unittest.mock import patch
        rows=wide_chain(self.at);rows[-1]['quote_epoch']=self.at-3*3600
        feed=self.app.extensions['tq_feed']
        with patch.object(feed,'active',return_value={'50'}):feed.snapshot('50',rows,self.at,market_epoch=self.at-1)
        with connect(self.path) as db:
            row=db.execute('SELECT quality,details FROM tq_snapshots').fetchone()
            self.assertEqual(row['quality'],'insufficient');self.assertIn('timestamps',json.loads(row['details'])['reason'])
