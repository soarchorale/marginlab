import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from marginlab import feishu, signals
from marginlab.coverage import ZONE
from marginlab.tqstore import DDL


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.market = sqlite3.connect(':memory:')
        self.market.executescript(DDL)
        self.addCleanup(self.market.close)
        self.db = self.state('signals')
        self.day = datetime(2026, 11, 25)
        self.starts = [self.day+timedelta(minutes=m-1) for m in signals.TRADING_MINUTES]

    def state(self, name):
        db = signals.open_state(Path(self.folder.name)/name)
        signals.initialize(db)
        self.addCleanup(db.close)
        return db

    def point(self, i, price=2800, vol=18, bar=True, quality='valid', **metadata):
        start = self.starts[i]
        end = start + signals.MINUTE
        if bar:
            self.market.execute('INSERT INTO tq_bars VALUES(?,?,?,?,?,?,?,?,?)',
                                ('SSE.000016', start.isoformat(), price, price+1, price-1, price, 0, 0, 0))
        details = dict(sampling='last-known-before-boundary',
                       market_heartbeat_epoch=end.replace(tzinfo=ZONE).timestamp()-1)
        details.update(metadata)
        self.market.execute('INSERT INTO tq_snapshots VALUES(?,?,?,?,?,?)',
                            ('50', end.isoformat(), vol, quality, json.dumps(details), '[]'))
        self.market.commit()
        return end.replace(tzinfo=ZONE)

    def test_wait_for_both_inputs_and_preserve_first_finalization(self):
        self.point(0)
        end = self.point(1, bar=False)
        signals.advance(self.market, self.db, '50', end)
        self.assertEqual(self.db.execute('select count(*) from observations').fetchone()[0], 1)
        signals.advance(self.market, self.db, '50', end+timedelta(seconds=31))
        self.assertEqual(self.db.execute('select quality from observations order by endpoint desc').fetchone()[0],
                         'missing_bar')
        self.market.execute('insert into tq_bars values(?,?,?,?,?,?,?,?,?)',
                            ('SSE.000016', self.starts[1].isoformat(), 10, 11, 9, 10, 0, 0, 0))
        self.market.commit()
        signals.advance(self.market, self.db, '50', end+timedelta(seconds=32))
        self.assertEqual(self.db.execute('select count(vix) from observations').fetchone()[0], 1)

    def test_late_bar_inside_grace_is_accepted(self):
        self.point(0)
        end = self.point(1, bar=False)
        signals.advance(self.market, self.db, '50', end)
        self.market.execute('insert into tq_bars values(?,?,?,?,?,?,?,?,?)',
                            ('SSE.000016', self.starts[1].isoformat(), 10, 11, 9, 10, 0, 0, 0))
        self.market.commit()
        signals.advance(self.market, self.db, '50', end+timedelta(seconds=4))
        self.assertEqual(self.db.execute('select count(vix) from observations').fetchone()[0], 2)

    def test_reject_ineligible_quotes_and_skip_lunch(self):
        self.point(0)
        self.point(1, quality='reference')
        self.point(2, sampling='historical-tick-backward-asof')
        self.point(3, market_heartbeat_epoch=None)
        self.point(4, recovery={'method': 'saved'})
        self.point(5, market_heartbeat_epoch=1)
        last = self.point(239)
        signals.advance(self.market, self.db, '50', last)
        self.assertEqual(tuple(self.db.execute('select count(*),count(vix) from observations').fetchone()), (240, 1))

    def test_restart_incremental_matches_replay_and_never_requeues_history(self):
        rng = np.random.default_rng(7)
        price, vol = 2800., 18.
        for i in range(240):
            r = rng.normal(0, .0004)
            price *= np.exp(r)
            vol *= np.exp(-3*r + rng.normal(0, .001) + (.018 if 152 <= i < 159 else 0))
            end = self.point(i, price, vol)
        signals.advance(self.market, self.db, '50', end)
        other = self.state('resumed')
        signals.advance(self.market, other, '50', self.starts[150].replace(tzinfo=ZONE))
        signals.initialize(other)
        signals.advance(self.market, other, '50', end)
        signals.advance(self.market, other, '50', end)
        sql = 'select id,status,payload from outbox order by id'
        cards = self.db.execute(sql).fetchall()
        self.assertTrue(cards)
        details = self.db.execute('select event_id,rule,direction,evidence from signal_triggers order by event_id,rule').fetchall()
        self.assertTrue(details)
        self.assertEqual(details, other.execute('select * from signal_triggers order by event_id,rule').fetchall())
        self.assertIn('threshold', json.loads(details[0]['evidence']))
        self.assertEqual(cards, other.execute(sql).fetchall())
        self.assertTrue(all(r['status'] == 'observed' for r in cards))
        self.assertEqual(self.db.execute('select * from checkpoints').fetchall(),
                         other.execute('select * from checkpoints').fetchall())

    def test_confirmed_visit_reuses_anchor_and_suppresses_repeat(self):
        for i in range(238):
            phase = (i-14) % 4
            price = 2804. if i >= 14 and phase < 3 else 2800.
            vol = 18. if i < 185 else 18. + (i-184)*.15
            end = self.point(i, price, vol)
        signals.advance(self.market, self.db, '50', end)
        cards = self.db.execute("select payload from outbox where payload like '%同价位升波已确认%'").fetchall()
        self.assertEqual(len(cards), 1)
        anchor = json.loads(self.db.execute('select states from checkpoints').fetchone()[0])['anchor']
        self.assertEqual(anchor, dict(endpoint='2026-11-25T09:44:00', price=2800., atr=2.))

    def test_input_state_and_notification_rollback_together(self):
        rng = np.random.default_rng(21)
        for i in range(240):
            end = self.point(i, 2800 + rng.normal(), 18 + rng.normal()*.08 + max(0, min(7, i-160)))
        self.db.execute("""create trigger abort_send before insert on outbox
                           begin select raise(abort, 'simulated failure'); end""")
        with self.assertRaises(sqlite3.IntegrityError):
            signals.advance(self.market, self.db, '50', end)
        for table in ('checkpoints', 'observations', 'outbox'):
            self.assertEqual(self.db.execute(f'select count(*) from {table}').fetchone()[0], 0)

    def test_card_combines_rules_and_keeps_comparisons(self):
        row = SimpleNamespace(vix=19, close=2800, close_change=.07, vix_change=2, corr_return=-.5)
        triggers = [dict(rule='residual', direction='up', value=2, percentile=95, threshold=1.1),
                    dict(rule='revisit', direction='up', value=3, matches=4, anchor=2801, entry='上行进入')]
        payload = feishu.card('50', '2026-11-25T10:30:00', row, triggers)
        self.assertEqual(payload['card']['schema'], '2.0')
        content = json.dumps(payload, ensure_ascii=False)
        for expected in ('+2.00', '+1.10', '2,801', '4 次', '10:30:00', '-0.50'):
            self.assertIn(expected, content)

    def queued(self):
        self.db.execute("""insert into outbox (id, product, endpoint, payload, status, created, expires)
                           values('test','50','closed','{}','pending',100,220)""")
        self.db.commit()

    def test_retry_only_on_explicit_safe_outcome(self):
        self.queued()
        transport = Mock(side_effect=[('retry','rate_limited'), ('sent','accepted')])
        self.assertTrue(feishu.dispatch_one(self.db, 'path', transport, lambda: 101))
        self.assertFalse(feishu.dispatch_one(self.db, 'path', transport, lambda: 102))
        self.assertTrue(feishu.dispatch_one(self.db, 'path', transport, lambda: 104))
        self.assertFalse(feishu.dispatch_one(self.db, 'path', transport, lambda: 110))
        self.assertEqual(transport.call_count, 2)

    def test_interrupted_dispatch_becomes_unknown_without_retry(self):
        self.queued()
        with self.assertRaises(KeyboardInterrupt):
            feishu.dispatch_one(self.db, 'path', Mock(side_effect=KeyboardInterrupt), lambda: 101)
        signals.initialize(self.db)
        self.assertEqual(self.db.execute('select status from outbox').fetchone()[0], 'unknown')
        self.assertFalse(feishu.dispatch_one(self.db, 'path', Mock(), lambda: 105))
        self.assertEqual(tuple(self.db.execute('select outcome,reason from delivery_attempts').fetchone()),
                         ('unknown', 'interrupted_send'))

    def test_group_limit_persists_across_restart_and_suppressed_cards_never_retry(self):
        transport = Mock(side_effect=[('unknown', 'timeout')] + [('sent', 'accepted')] * 6)
        for i in range(10):
            self.db.execute("""insert into outbox(id,product,endpoint,payload,status,created,expires)
                               values(?,?,?,'{}','pending',100,220)""", (str(i),str(i%3),str(i)))
        self.db.commit()
        for i in range(10):
            if i == 5:
                signals.initialize(self.db)
            feishu.dispatch_one(self.db, 'path', transport, lambda: 101)
        self.assertEqual(transport.call_count, 6)
        self.assertEqual(self.db.execute("select count(*) from outbox where status='suppressed'").fetchone()[0], 4)
        self.assertEqual(self.db.execute('select count(*) from delivery_attempts').fetchone()[0], 6)
        self.db.execute("""insert into outbox(id,product,endpoint,payload,status,created,expires)
                           values('new','50','late','{}','pending',701,821)""")
        self.db.commit()
        self.assertTrue(feishu.dispatch_one(self.db, 'path', transport, lambda: 702))
        self.assertFalse(feishu.dispatch_one(self.db, 'path', transport, lambda: 703))

    def test_attempt_ceiling_and_cascading_retention(self):
        self.queued()
        send = Mock(return_value=('retry','rate_limited'))
        for t in (101, 105, 111, 140):
            feishu.dispatch_one(self.db, 'path', send, lambda: t)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(tuple(self.db.execute('select status,reason from outbox').fetchone()),
                         ('failed','attempt_limit'))
        attempts = self.db.execute('select attempt,outcome,reason from delivery_attempts order by attempt')
        self.assertEqual([tuple(row) for row in attempts], [(i,'retry','rate_limited') for i in (1,2,3)])
        with self.db:
            self.db.execute("delete from outbox where id='test'")
        self.assertEqual(self.db.execute('select count(*) from delivery_attempts').fetchone()[0], 0)

    def test_secret_config_file_and_environment_override(self):
        secret = Path(self.folder.name)/'feishu.json'
        base = 'https://open.feishu.cn/open-apis/bot/v2/hook/'
        secret.write_text(json.dumps({'webhook': base+'test-a'}))
        with patch.dict('os.environ', {}, clear=True):
            self.assertTrue(feishu.load_webhook(secret).endswith('/test-a'))
            with patch.dict('os.environ', {'MARGIN_FEISHU_WEBHOOK': base+'test-b'}):
                self.assertTrue(feishu.load_webhook(secret).endswith('/test-b'))
            secret.write_text('null')
            with self.assertRaises(ValueError):
                feishu.load_webhook(secret)

    def test_expired_signal_never_leaves_process(self):
        self.queued()
        transport = Mock()
        self.assertFalse(feishu.dispatch_one(self.db, 'path', transport, lambda: 220))
        transport.assert_not_called()

    def test_webhook_validates_application_result_and_never_follows_redirect(self):
        with patch.object(feishu.http.client, 'HTTPSConnection') as client:
            response = client.return_value.getresponse.return_value
            response.status = 200
            for raw, expected in [(b'{"code":0}', 'sent'), (b'{"code":19022}', 'failed'),
                                  (b'{"code":9499}', 'retry'), (b'{}', 'unknown'), (b'bad', 'unknown')]:
                response.read.return_value = raw
                self.assertEqual(feishu.post('/redacted', '{}')[0], expected)
            client.return_value.getresponse.side_effect = TimeoutError()
            self.assertEqual(feishu.post('/redacted', '{}')[0], 'unknown')
        with self.assertRaises(ValueError):
            feishu.webhook_path('https://other.example/open-apis/bot/v2/hook/fake')
