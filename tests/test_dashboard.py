"""Dashboard transport and deployment-owned retention checks."""
import gzip
import json
import unittest
from support import app_for_test,login
from marginlab.storage import connect
from marginlab.cache import settings


class DashboardTests(unittest.TestCase):
    def test_compression_negotiation(self):
        app=app_for_test(self);client=app.test_client();login(client)
        url='/api/research?source=demo&symbol=510050&period=15'
        response=client.get(url,headers={'Accept-Encoding':'gzip'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Content-Encoding'],'gzip')
        self.assertIn('Accept-Encoding',response.headers['Vary'])
        self.assertIn('bars',json.loads(gzip.decompress(response.data)))
        plain=client.get(url,headers={'Accept-Encoding':'gzip;q=0'})
        self.assertNotIn('Content-Encoding',plain.headers)
        self.assertIn('bars',plain.json)
        self.assertNotIn('Content-Encoding',client.get('/settings',headers={'Accept-Encoding':'gzip'}).headers)

    def test_deployment_retention_overrides_saved_setting(self):
        app=app_for_test(self,HISTORY_MONTHS=2,HISTORY_MONTHS_OVERRIDE='2')
        with connect(app.config['DATABASE']) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('history_months','6')")
        self.assertEqual(settings(app)['months'],2)
        client=app.test_client();login(client)
        text=client.get('/settings').get_data(as_text=True)
        self.assertNotIn('name="history_months"',text)
        self.assertNotIn('登录与会话',text)
        self.assertEqual(client.post('/settings',data={'history_months':1,'refresh_seconds':120}).status_code,400)
        self.assertEqual(settings(app)['months'],2)
