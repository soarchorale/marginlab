import hashlib
import time
import unittest
from unittest.mock import patch
import pandas as pd
from support import app_for_test, login, form_token
from marginlab.storage import connect
from marginlab.cache import cached_live, prune, cutoff_for
from marginlab.data import demo


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self)
        self.client=self.app.test_client()

    def test_all_private_routes_require_login_without_calling_provider(self):
        with patch('marginlab.data.live') as provider:
            for path in ['/','/pricing','/chips','/settings','/method']:
                self.assertEqual(self.client.get(path).status_code,302,path)
            for path in ['/api/research','/api/stress','/api/frame','/api/regenerate','/api/options/value','/option/greek','/future/hv','/hdoption/rank']:
                response=self.client.post(path,json={}) if path in ['/api/frame','/api/regenerate'] else self.client.get(path)
                self.assertEqual(response.status_code,401,path)
            provider.assert_not_called()

    def test_session_rotation_csrf_cookie_and_revocation(self):
        csrf=form_token(self.client.get('/login'))
        old=self.client.get_cookie('margin_session').value
        response=self.client.post('/login',data=dict(username='admin',password='opsearch',csrf_token=csrf))
        self.assertEqual(response.status_code,302)
        token=self.client.get_cookie('margin_session').value
        self.assertNotEqual(token,old)
        self.assertIn('HttpOnly',response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax',response.headers['Set-Cookie'])
        with connect(self.app.config['DATABASE']) as db:
            self.assertIsNone(db.execute('SELECT * FROM sessions WHERE token_hash=?',(hashlib.sha256(old.encode()).hexdigest(),)).fetchone())
            self.assertIsNotNone(db.execute('SELECT * FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone())
        self.assertEqual(self.client.post('/api/regenerate',json={}).status_code,403)
        csrf=form_token(self.client.get('/settings'))
        self.assertEqual(self.client.post('/logout',data={'csrf_token':csrf},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/logout',data={'csrf_token':csrf}).status_code,302)
        self.client.set_cookie('margin_session',token)
        self.assertEqual(self.client.get('/api/research').status_code,401)

    def test_idle_and_absolute_expiry(self):
        for field,value in [('last_seen',time.time()-1801),('expires',time.time()-1)]:
            self.assertEqual(login(self.client).status_code,302)
            with connect(self.app.config['DATABASE']) as db:
                db.execute(f'UPDATE sessions SET {field}=? WHERE authenticated=1',(value,))
            self.assertEqual(self.client.get('/api/research').status_code,401)
            self.client.environ_base.pop('HTTP_X_CSRF_TOKEN',None)

    def test_bruteforce_limit_and_csrf_on_login(self):
        self.client.get('/login')
        self.assertEqual(self.client.post('/login',data={'username':'admin','password':'opsearch'}).status_code,403)
        for _ in range(5):
            self.assertEqual(login(self.client,password='wrong').status_code,401)
        self.assertEqual(login(self.client).status_code,429)

    def test_routes_render_only_active_module_and_no_market_link(self):
        login(self.client)
        for path,identifier in [('/','research-form'),('/pricing','pricing-form'),('/chips','cfgWindow')]:
            result=self.client.get(path)
            html=result.get_data(as_text=True)
            self.assertEqual(result.status_code,200)
            self.assertIn(identifier,html)
            self.assertNotIn('行情与排行榜',html)
            self.assertNotIn('data-view=',html)
            self.assertIn('no-store',result.headers['Cache-Control'])
        self.assertNotIn('research-form',self.client.get('/pricing').get_data(as_text=True))
        self.assertNotIn('pricing-form',self.client.get('/').get_data(as_text=True))
        self.assertEqual(self.client.get('/api/research?source=live&period=daily').status_code,405)

    def test_safe_next_secure_cookie_and_password_change(self):
        secure_app=app_for_test(self,COOKIE_SECURE=True)
        c=secure_app.test_client()
        self.assertIn('Secure',c.get('/login').headers['Set-Cookie'])
        self.assertEqual(login(self.client,next='//evil.example').headers['Location'],'/')
        result=self.app.test_cli_runner().invoke(args=['set-admin-password'],input='a-longer-new-password\na-longer-new-password\n')
        self.assertEqual(result.exit_code,0,result.output)
        self.assertEqual(self.client.get('/api/research').status_code,401)
        self.client.environ_base.pop('HTTP_X_CSRF_TOKEN',None)
        self.assertEqual(login(self.client).status_code,401)
        self.assertEqual(login(self.client,password='a-longer-new-password').status_code,302)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.app=app_for_test(self,MARKET_RETRY_SECONDS=0)
        self.calls=[]

    def provider(self,symbol,period,start,end):
        self.calls.append((start,end))
        times=pd.bdate_range(start,end)
        frame=demo(period='daily',count=len(times))
        frame.timestamp=times
        return frame

    def expire(self):
        with connect(self.app.config['DATABASE']) as db:
            db.execute('UPDATE market_sync SET success=0,attempted=0')

    def test_persistence_ttl_and_incremental_refresh(self):
        first,meta=cached_live(self.app,'510050','daily',fetcher=self.provider)
        self.assertTrue(meta['refreshed'])
        second,meta=cached_live(self.app,'510050','daily',fetcher=self.provider)
        self.assertEqual(len(self.calls),1)
        pd.testing.assert_frame_equal(first,second)
        self.expire()
        third,meta=cached_live(self.app,'510050','daily',fetcher=self.provider)
        self.assertEqual(len(self.calls),2)
        self.assertGreater(self.calls[-1][0],self.calls[0][0])
        self.assertEqual(len(third),len(first))
        with connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM market_bars').fetchone()[0],len(first))
        from marginlab import create_app
        restarted=create_app(dict(TESTING=True,DATABASE=self.app.config['DATABASE']))
        _,meta=cached_live(restarted,'510050','daily',fetcher=lambda *args:self.fail('should read persisted fresh cache'))
        self.assertFalse(meta['refreshed'])

    def test_stale_fallback_and_empty_failure(self):
        cached_live(self.app,'510050','daily',fetcher=self.provider)
        self.expire()
        def broken(*args):raise RuntimeError('upstream unavailable')
        frame,meta=cached_live(self.app,'510050','daily',fetcher=broken)
        self.assertGreaterEqual(len(frame),20)
        self.assertTrue(meta['stale'])
        self.assertIsNotNone(meta['last_error'])
        with self.assertRaises(RuntimeError):
            cached_live(self.app,'510300','daily',fetcher=broken)

    def test_retention_shrink_expand_and_range_clipping(self):
        cached_live(self.app,'510050','daily',fetcher=self.provider)
        with connect(self.app.config['DATABASE']) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('history_months','1')")
        cutoff=prune(self.app)
        with connect(self.app.config['DATABASE']) as db:
            self.assertGreaterEqual(db.execute('SELECT MIN(timestamp) FROM market_bars').fetchone()[0],cutoff)
            db.execute("UPDATE settings SET value='3' WHERE key='history_months'")
        _,meta=cached_live(self.app,'510050','daily',start='2000-01-01',fetcher=self.provider)
        self.assertTrue(meta['range_clipped'])
        self.assertEqual(self.calls[-1][0],cutoff_for(3))
        with self.assertRaises(ValueError):
            cached_live(self.app,'510050','daily',start='2000-01-01',end='2000-02-01',fetcher=self.provider)

    def test_refresh_lease_and_retry_throttle(self):
        cached_live(self.app,'510050','daily',fetcher=self.provider)
        self.expire()
        with connect(self.app.config['DATABASE']) as db:
            db.execute('UPDATE market_sync SET lease_until=?',(time.time()+30,))
        _,meta=cached_live(self.app,'510050','daily',fetcher=lambda *args:self.fail('lease should prevent fetch'))
        self.assertTrue(meta['refreshing'])
        self.assertTrue(meta['stale'])
        self.app.config['MARKET_RETRY_SECONDS']=60
        with connect(self.app.config['DATABASE']) as db:
            db.execute('UPDATE market_sync SET lease_until=0,attempted=?',(time.time(),))
        _,meta=cached_live(self.app,'510050','daily',fetcher=lambda *args:self.fail('backoff should prevent fetch'))
        self.assertTrue(meta['stale'])

    def test_settings_are_authenticated_csrf_protected_and_validated(self):
        c=self.app.test_client()
        self.assertEqual(c.post('/settings',data={'history_months':1}).status_code,302)
        login(c)
        self.assertEqual(c.post('/settings',data={'history_months':0,'refresh_seconds':900}).status_code,400)
        self.assertEqual(c.post('/settings',data={'history_months':6,'refresh_seconds':120}).status_code,302)
        from marginlab.cache import settings
        self.assertEqual(settings(self.app),dict(months=6,refresh_seconds=120))


if __name__=='__main__':unittest.main()
