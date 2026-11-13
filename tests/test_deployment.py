import json
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path
import unittest
from marginlab.deployment import public_origin,write_private_json,load_config
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from backup_sqlite import backup
from build_release import build


class DeploymentTests(unittest.TestCase):
    def test_configuration_and_secret_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_private_json(root/'instance/server.json',{'public_origin':'https://margin.example.com/'})
            secret=root/'instance/tqauth.json'
            write_private_json(secret,{'username':'test','password':'not-real'})
            cfg=load_config(root)
            self.assertTrue(cfg['COOKIE_SECURE'])
            self.assertEqual(cfg['PUBLIC_ORIGIN'],'https://margin.example.com')
            self.assertEqual(secret.stat().st_mode&0o777,0o600)
        for url in ['http://public.example','https://user:password@example.com','https://example.com/path','file:///tmp/config']:
            with self.assertRaises(ValueError):public_origin(url)

    def test_wal_backup_keeps_market_data_but_not_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'live.sqlite3';target=Path(tmp)/'export.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('PRAGMA journal_mode=WAL')
                for table in ['tq_bars','admin','sessions','login_attempts','tq_lease']:
                    db.execute('CREATE TABLE '+table+' (value TEXT)')
                    db.execute('INSERT INTO '+table+' VALUES (?)',('test',))
                db.commit()
                backup(source,target)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_bars').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM admin').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM tq_lease').fetchone()[0],0)
            with self.assertRaises(FileExistsError):backup(source,target)

    def test_release_never_includes_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ['app.py','requirements.txt','instance/margin.sqlite3','tqauth.json','.env','marginlab/__init__.py','marginlab/__pycache__/a.pyc']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('test')
            output=root/'dist/test.tar.gz';build(root,output)
            with tarfile.open(output) as archive:names=archive.getnames()
            self.assertEqual(sorted(names),['marginlab/app.py','marginlab/marginlab/__init__.py','marginlab/requirements.txt'])

if __name__=='__main__':unittest.main()
