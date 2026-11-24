"""Operational regressions on disposable databases."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from marginlab.logging_setup import sqlite_fields
from marginlab.storage import connect
from scripts.check_log_progress import inspect
from scripts.sync_market import export_market, merge_market
from support import app_for_test, login


class ReplicaTests(unittest.TestCase):
    def test_merge_preview_apply_and_repeat(self):
        app = app_for_test(self, MARKET_READ_ONLY=True)
        path = Path(app.config['DATABASE'])
        export = path.with_name('market.db')
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=10)).isoformat()
        client = app.test_client()
        self.assertEqual(login(client).status_code, 302)
        with connect(path) as db:
            db.executemany('INSERT INTO tq_snapshots VALUES(?,?,?,?,?,?)', [
                ('50', today, None, 'insufficient', '{}', '[]'),
                ('300', today, 18, 'valid', '{}', '[]')])
            db.execute('INSERT INTO tq_status VALUES(?,?)', ('50', json.dumps(dict(quote_time=today))))
        export_market(path, export)
        with connect(path) as db:
            db.execute("UPDATE tq_snapshots SET value=20,quality='valid' WHERE product='50'")
            db.execute("DELETE FROM tq_snapshots WHERE product='300'")
            db.execute('INSERT INTO tq_snapshots VALUES(?,?,?,?,?,?)', ('50', old, 19, 'valid', '{}', '[]'))
            db.execute('UPDATE tq_status SET payload=?', (json.dumps(dict(quote_time=old)),))
        preview = merge_market(path, export)
        self.assertEqual(preview['tq_snapshots']['new_range'], [today, today])
        self.assertEqual(preview['tq_snapshots']['conflicts'], 1)
        self.assertEqual(preview['tq_status']['newer_status_updates'], 1)
        backup = path.with_name('before.db')
        with self.assertRaisesRegex(ValueError, 'replica'):
            merge_market(path, export, backup)
        with connect(path) as db:
            db.execute("INSERT INTO settings VALUES('market_read_only','1')")
        self.assertEqual(preview, merge_market(path, export, backup))
        with connect(backup) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tq_snapshots').fetchone()[0], 2)
        again = merge_market(path, export, path.with_name('repeat.db'))
        self.assertTrue(all(s['new'] == 0 for s in again.values()))
        self.assertEqual(again['tq_status']['newer_status_updates'], 0)
        with connect(path) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT value FROM tq_snapshots ORDER BY value')], [18,19,20])
        self.assertEqual(client.get('/method').status_code, 200)


class DiagnosticsTests(unittest.TestCase):
    def test_idle_session_write_under_lock_and_recovery(self):
        app = app_for_test(self)
        path = app.config['DATABASE']
        client = app.test_client()
        self.assertEqual(login(client).status_code, 302)
        with connect(path) as db:
            db.execute('UPDATE sessions SET last_seen=last_seen-120')
        original = sqlite3.connect
        def short_timeout(*args, **kwargs):
            return original(*args, **dict(kwargs, timeout=.02))
        with connect(path) as writer:
            writer.execute('BEGIN IMMEDIATE')
            with patch('marginlab.storage.sqlite3.connect', side_effect=short_timeout):
                with self.assertRaises(sqlite3.OperationalError) as failure:
                    client.get('/method')
            self.assertEqual(sqlite_fields(failure.exception)['sqlite_name'], 'SQLITE_BUSY')
        self.assertEqual(client.get('/method').status_code, 200)
        with connect(path) as db:
            db.execute('DELETE FROM sessions')
        self.assertEqual(client.get('/method').status_code, 302)

    def test_health_event_progress_across_midnight_with_unrelated_requests(self):
        app = app_for_test(self)
        path = Path(app.config['DATABASE'])
        zone = ZoneInfo('Asia/Shanghai')
        now = datetime(2026, 11, 22, 0, 2, tzinfo=zone)
        stamp = (now - timedelta(minutes=3)).isoformat()
        products = ('50','300','500','1000')
        with connect(path) as db:
            db.executemany('INSERT INTO tq_health VALUES(?,?)', [(p,json.dumps(dict(checked_at=now.timestamp()))) for p in products])
        current = path.parent / 'marginlab-2026-11-22.log'
        current.write_text(json.dumps(dict(event='http_request', time=now.isoformat())) + '\n')
        def status(offset=0):
            return inspect(path, path.parent, now.timestamp() + offset)['state']
        self.assertEqual(status(), 'log_stale')
        old = path.parent / 'marginlab-2026-11-21.log'
        old.write_text(''.join(json.dumps(dict(event='collector_health',time=stamp,details=dict(product=p)))+'\n' for p in products))
        self.assertEqual(status(), 'ok')
        self.assertEqual(status(65), 'heartbeat_stale')
