"""Exercise upgrade preservation and rollback without SSH or real processes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

spec=importlib.util.spec_from_file_location('release',Path(__file__).resolve().parents[1]/'deploy/release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_release import build


class ReleaseTests(unittest.TestCase):
    def exercise(self,success):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'live';source=base/'new';remote=base/'upload';proc=base/'proc'
            for p in (root/'instance',source,remote,proc):p.mkdir(parents=True)
            (root/'app.py').write_text('old code')
            (root/'tqauth.json').write_text('private credentials')
            (source/'app.py').write_text('new code')
            database=root/'instance/margin.sqlite3'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE settings(key TEXT,value TEXT)')
                db.execute("INSERT INTO settings VALUES('market_read_only','0')")
                db.execute('CREATE TABLE market(value TEXT)')
                db.execute("INSERT INTO market VALUES('keep')")
            (proc/'cwd').symlink_to(root,target_is_directory=True)
            (proc/'cmdline').write_bytes(b'/usr/bin/python3\0app.py\0')
            (proc/'environ').write_bytes(b'HOST=127.0.0.1\0PORT=5001\0')
            archive=remote/'release.tar.gz';build(source,archive)
            args=argparse.Namespace(directory=str(root),pid=12345,archive=str(archive),
                 digest=hashlib.sha256(archive.read_bytes()).hexdigest(),remote_python='/usr/bin/python3',bind='0.0.0.0',port=5001)
            def paths(value):return proc if str(value)=='/proc/12345' else Path(value)
            process=Mock(pid=23456);process.poll.return_value=None
            with patch.object(release,'Path',side_effect=paths),patch.object(release,'run'),patch.object(release,'stop_pid') as stop,patch.object(release,'healthy',return_value=success),patch.object(release.subprocess,'Popen',return_value=process) as popen:
                if success:release.remote(args)
                else:
                    with self.assertRaisesRegex(RuntimeError,'HTTP'):release.remote(args)
                    self.assertEqual(popen.call_count,2)
                    self.assertEqual(popen.call_args.kwargs['env']['HOST'],'127.0.0.1')
                stop.assert_any_call(12345)
            self.assertEqual((root/'app.py').read_text(),'new code' if success else 'old code')
            self.assertEqual((root/'tqauth.json').read_text(),'private credentials')
            with sqlite3.connect(database) as db:self.assertEqual(db.execute('SELECT value FROM market').fetchone()[0],'keep')
            self.assertEqual(len(list((root/'instance/releases').glob('*/before.sqlite3'))),1)

    def test_upgrade_preserves_runtime(self):self.exercise(True)
    def test_start_failure_restores_code_and_environment(self):self.exercise(False)
