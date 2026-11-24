"""Daily rotation, concurrency and private HTTP logging checks."""
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from marginlab.logging_setup import DailyLogHandler, configure, event


class LoggingTests(unittest.TestCase):
    def test_shanghai_rotation_and_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            handler=DailyLogHandler(directory,days=2)
            self.addCleanup(handler.close)
            def emit(stamp):
                record=logging.LogRecord('test',20,'',0,'sample',(),None)
                record.created=datetime.fromisoformat(stamp).timestamp()
                handler.handle(record)
            emit('2026-11-15T15:59:00+00:00')
            emit('2026-11-15T16:00:00+00:00')
            self.assertEqual(len(list(Path(directory).glob('*.log'))),2)
            emit('2026-11-16T16:00:00+00:00')
            self.assertEqual(sorted(p.name for p in Path(directory).glob('*.log')),
                             ['marginlab-2026-11-16.log','marginlab-2026-11-17.log'])
            handler.close()

    def test_concurrent_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            handler=DailyLogHandler(directory)
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda i:handler.handle(logging.LogRecord('test',20,'',0,str(i),(),None)),range(200)))
                rows=[json.loads(line) for line in next(Path(directory).glob('*.log')).read_text().splitlines()]
                self.assertEqual(len(rows),200)
                self.assertEqual(len({r['event'] for r in rows}),200)
            finally:handler.close()

    def test_http_privacy_and_duplicate_configuration(self):
        names=('marginlab','werkzeug','waitress')
        saved={n:(logging.getLogger(n).handlers[:],logging.getLogger(n).level,logging.getLogger(n).propagate) for n in names}
        with tempfile.TemporaryDirectory() as directory:
            try:
                app=Flask('marginlab')
                app.config.update(LOG_DIR=directory,LOG_RETENTION_DAYS=30)
                configure(app)
                other=Flask('marginlab');other.config.update(app.config);configure(other)
                self.assertEqual(len(app.logger.handlers),1)
                @app.get('/probe')
                def probe():return 'ok'
                @app.get('/failure')
                def failure():raise RuntimeError('secret_exception_value')
                client=app.test_client()
                with patch.object(logging.Logger, 'manager', logging.Manager(logging.root)):
                    event('collector_health', product='50')
                    client.get('/probe?password=secret_query',headers={'Authorization':'secret_header'})
                client.get('/failure')
                text=next(Path(directory).glob('*.log')).read_text()
                self.assertNotIn('secret_',text)
                rows=[json.loads(line) for line in text.splitlines()]
                self.assertTrue(any(r['event']=='collector_health' and r['details']['product']=='50' for r in rows))
                requests=[r for r in rows if r['event']=='http_request']
                self.assertEqual(len(requests),2)
                self.assertEqual(requests[0]['details']['route'],'/probe')
                self.assertTrue(any(r.get('exception_type')=='RuntimeError' for r in rows))
            finally:
                for name,(handlers,level,propagate) in saved.items():
                    logger=logging.getLogger(name)
                    for handler in logger.handlers:
                        if isinstance(handler,DailyLogHandler):handler.close()
                    logger.handlers=handlers;logger.setLevel(level);logger.propagate=propagate
