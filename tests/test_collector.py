import unittest
from unittest.mock import patch,MagicMock
from support import app_for_test,login
from marginlab.storage import connect
from marginlab.tqfeed import Feed,PRODUCTS
from marginlab.tqstore import read_bars


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self)
        self.feed=self.app.extensions['tq_feed']
        self.feed.autonomous=True

    def test_schedule_is_shanghai_time_and_preopens(self):
        for stamp,wanted in [('2026-11-09 09:19',False),('2026-11-09 09:20',True),
                              ('2026-11-09 15:09',True),('2026-11-09 15:10',False),
                              ('2026-11-08 10:00',False),('2026-11-09T01:30:00Z',True)]:
            self.assertEqual(Feed.collection_window(stamp),wanted)

    def test_background_does_not_depend_on_login_and_web_does_not_subscribe(self):
        with patch.object(self.feed,'collection_window',return_value=True):
            self.assertEqual(self.feed.active(),set(PRODUCTS))
            with patch('marginlab.tqfeed.threading.Thread') as thread:
                self.feed.touch('50','not-a-session')
                thread.assert_not_called()
        client=self.app.test_client()
        self.assertEqual(client.post('/api/research',json={'source':'live'}).status_code,401)
        login(client)
        with patch('marginlab.tqfeed.session') as session:
            result=client.post('/api/research',json={'source':'live','symbol':'50','period':'1'})
            self.assertEqual(result.status_code,200)
            self.assertTrue(result.json['collector']['automatic'])
            session.assert_not_called()
        self.feed.stop()
        self.assertEqual(self.feed.active(),set())

    def test_supervisor_prunes_without_browser_and_retries_after_backoff(self):
        path=self.app.config['DATABASE']
        with connect(path) as db:
            db.execute("INSERT INTO tq_bars(symbol,timestamp) VALUES('old','2000-01-01')")
        with patch.object(self.feed,'collection_window',return_value=False):
            self.feed.maintain()
        self.assertEqual(self.feed.state,'scheduled')
        with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_bars').fetchone()[0],0)
        with patch.object(self.feed,'collection_window',return_value=True),patch('marginlab.tqfeed.threading.Thread') as thread:
            self.feed.retry=float('inf');self.feed.maintain();thread.assert_not_called()
            self.feed.retry=0;self.feed.maintain();thread.return_value.start.assert_called_once()
            thread.return_value.is_alive.return_value=True
            self.feed.maintain();thread.return_value.start.assert_called_once()

    def test_local_history_can_exceed_sdk_serial_limit(self):
        path=self.app.config['DATABASE']
        with connect(path) as db:
            db.executemany('INSERT INTO tq_bars(symbol,timestamp) VALUES(?,?)',
                           [('test',f'2026-{i:06}') for i in range(10020)])
        self.assertEqual(len(read_bars(path,'test','2026','2027',limit=None)),10020)
        self.assertEqual(len(read_bars(path,'test','2026','2027',limit=10)),10)

if __name__=='__main__':unittest.main()
