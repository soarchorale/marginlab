"""Boundary, delivery and migration regressions for the two-tier monitor."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from marginlab import feishu, signals, watch
from marginlab.coverage import ZONE


class ObservationPolicyTests(unittest.TestCase):
    base = datetime(2026,11,26,10,57,tzinfo=ZONE)
    stamp = base.replace(tzinfo=None).isoformat()

    def setUp(self):
        self.db = signals.open_state(':memory:')
        self.addCleanup(self.db.close)
        signals.initialize(self.db)
        self.now = self.base.timestamp()
        self.transport = Mock(return_value=('sent','accepted'))

    def add(self, rule, product='50'):
        confirmed = rule == 'residual'
        level = 'confirmed' if confirmed else 'watch'
        item = dict(rule=rule,direction='down',level=level,policy='notify',since=self.stamp,
                    threshold=-1.2,value=-1.3,percentile=5)
        ctx = dict(vix=19.,close=2800.,close_change=None,vix_change=None,corr_return=None)
        event_id = f'{signals.VERSION}/{product}/{self.stamp}'
        with self.db:
            self.db.execute("""INSERT INTO outbox(id,product,endpoint,context,tier,payload,status,expires,created)
                              VALUES(?,?,?,?,?,'{}','pending',?,?)""",
                            (event_id,product,self.stamp,json.dumps(ctx),level,
                             self.now+(120 if confirmed else 30),self.now+1))
            self.db.execute('INSERT INTO signal_triggers VALUES(?,?,?,?)',(event_id,rule,'down',json.dumps(item)))
            self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',
                            (product,signals.VERSION,self.stamp,
                             json.dumps({'active':{rule:'down'},'since':{rule:self.stamp}})))
        return event_id

    def send(self, age):
        return feishu.dispatch_one(self.db,'path',self.transport,lambda:self.now+age)

    def test_preview_marks_title_and_summary_without_mutating_delivery(self):
        payload = {'msg_type':'interactive','card':{'header':{'template':'blue',
                   'title':{'tag':'plain_text','content':'盯盘观察'}}}}
        preview = feishu.preview_card(payload)
        self.assertEqual(preview['card']['config']['summary']['content'], '【本地测试】盯盘观察')
        self.assertEqual(preview['card']['header']['title']['content'], '【本地测试】盯盘观察')
        self.assertEqual(feishu.preview_card(preview), preview)
        self.assertNotIn('config', payload['card'])
        self.assertEqual(payload['card']['header']['title']['content'], '盯盘观察')

    def test_retry_rebuilds_badge_and_records_each_actual_payload(self):
        self.add('residual_watch')
        self.transport.side_effect = [('retry','rate_limited'),('sent','accepted')]
        self.send(4)
        self.send(18)
        bodies = [r[0] for r in self.db.execute('SELECT payload FROM delivery_attempts ORDER BY attempt')]
        self.assertIn('及时 · 4 秒',bodies[0])
        self.assertIn('稍有延迟 · 18 秒',bodies[1])
        self.assertIn('10:57:18',bodies[1])
        self.assertEqual(bodies[1],self.transport.call_args.args[1])

    def test_tls_delay_can_expire_observation_before_post(self):
        self.add('residual_watch')
        clock = Mock(side_effect=[self.now+5,self.now+31,self.now+31])
        with patch.object(feishu.http.client,'HTTPSConnection') as conn:
            feishu.dispatch_one(self.db,'path',clock=clock)
            conn.return_value.request.assert_not_called()
        self.assertEqual(tuple(self.db.execute('SELECT status,reason FROM outbox').fetchone()),('expired','ttl'))
        self.assertIsNone(self.db.execute('SELECT payload FROM delivery_attempts').fetchone()[0])

    def test_long_delay_waits_for_complete_minute_then_revalidates(self):
        self.add('residual')
        self.send(60)
        self.transport.assert_not_called()
        with self.db:
            self.db.execute("UPDATE checkpoints SET endpoint='2026-11-26T10:58:00'")
        self.send(65)
        body = self.transport.call_args.args[1]
        self.assertIn('延迟确认 · 65 秒',body)
        self.assertIn('10:58:00',body)

    def test_reversal_suppresses_old_card(self):
        self.add('residual')
        with self.db:
            self.db.execute("UPDATE checkpoints SET endpoint='2026-11-26T10:58:00',states=?",
                            (json.dumps({'active':{'residual':'up'},'since':{}}),))
        self.send(65)
        self.transport.assert_not_called()
        self.assertEqual(self.db.execute('SELECT reason FROM outbox').fetchone()[0],'superseded')

    def test_confirmation_priority_and_watch_quota(self):
        self.add('residual_watch','50')
        self.add('residual','300')
        with self.db:
            for i in range(3):
                self.db.execute("""INSERT INTO outbox(id,tier,status,finished) VALUES(?,'watch','sent',?)""",
                                (f'prior{i}',self.now-i))
        self.send(4)
        self.assertIn('沪深300',self.transport.call_args.args[1])
        self.send(5)
        self.assertEqual(self.transport.call_count,1)
        self.assertEqual(self.db.execute("SELECT reason FROM outbox WHERE product='50'").fetchone()[0],'watch_limit')

    def test_same_direction_cooldown_and_upgrade_preserve_origin(self):
        states={'since':{}}
        def classify(minute,rule,level,direction='down'):
            t=dict(rule=rule,level=level,direction=direction)
            stamp=(self.base+timedelta(minutes=minute)).replace(tzinfo=None).isoformat()
            tier=signals.classify([t],states,'1000',stamp)
            return tier,t
        _,first=classify(0,'residual_watch','watch')
        tier,second=classify(2,'residual_watch','watch')
        self.assertEqual((tier,second['policy']),('record','cooldown'))
        tier,strong=classify(4,'residual','confirmed')
        self.assertEqual((tier,strong['related_event']),('confirmed',second['related_event']))
        self.assertEqual(classify(5,'residual_watch','watch','up')[0],'watch')

    def test_fixed_expiry_wings_and_missing_minutes(self):
        states={}
        put='skew:2026-12-18:PUT'
        call='skew:2027-01-15:CALL'
        events=[]
        for i in range(10):
            stamp=(self.base+timedelta(minutes=i)).replace(tzinfo=None).isoformat()
            wings={put:0.2 if i<5 else .85,call:.1}
            events.extend(watch.wing_events(states,stamp,wings))
        self.assertEqual([x['rule'] for x in events],[put])
        self.assertEqual(events[0]['level'],'context')
        self.assertIsNone(watch.interpolate([(.12,12),None,(.4,18)],.25))

    def test_correlation_zone_requires_three_and_resets_on_gap(self):
        states={}
        events=[watch.corr_transition(states,x) for x in [-.7]*3+[-.5,-.34,-.4,-.3,-.2,-.1]+[.7]*3]
        self.assertEqual([(t['previous'],t['direction']) for t in events if t],
                         [('negative','weak'),('weak','positive')])
        self.assertIsNone(watch.corr_transition(states,float('nan')))
        self.assertNotIn('corr',states)

    def test_upgrade_preserves_old_events_and_progress(self):
        with closing(sqlite3.connect(':memory:')) as old:
            old.executescript(signals.DDL)
            old.execute("INSERT INTO checkpoints VALUES('50','minute-v1',?,'{}')",(self.stamp,))
            old.execute("INSERT INTO outbox(id,status,payload) VALUES('old','sent','original')")
            old.commit()
            signals.initialize(old)
            signals.initialize(old)
            self.assertEqual(old.execute("SELECT endpoint FROM checkpoints WHERE version=?",
                                         (signals.VERSION,)).fetchone()[0],self.stamp)
            self.assertEqual(old.execute("SELECT status,payload FROM outbox").fetchone(),('sent','original'))
