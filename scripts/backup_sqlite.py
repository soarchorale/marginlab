"""Consistent SQLite backup including WAL; exclude local sessions and lease."""
import argparse
import os
from pathlib import Path
import sqlite3


def backup(source,target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('The backup destination must differ from the source database')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(target) as copied:
            original.backup(copied)
            tables={r[0] for r in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('sessions','login_attempts','tq_lease'):
                if table in tables:copied.execute('DELETE FROM '+table)
            copied.commit()
            copied.execute('VACUUM')
            if copied.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('Backup integrity check failed')
            copied.execute('PRAGMA journal_mode=DELETE')
    print('SQLite backup completed; local sessions and the collector lease were excluded.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=str(Path(__file__).resolve().parents[1]/'instance/margin.sqlite3'))
    parser.add_argument('--output',required=True)
    args=parser.parse_args();backup(args.source,args.output)
