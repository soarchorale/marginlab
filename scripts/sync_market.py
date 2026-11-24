"""Export a consistent market-only snapshot or import it into a local replica."""
import argparse
from datetime import date, datetime, timezone
import calendar
import json
import os
from pathlib import Path
import sqlite3

TABLES=('tq_bars','tq_option_minutes','tq_snapshots','tq_status')


def cutoff(months):
    today=date.today();n=today.year*12+today.month-1-months;y,m=divmod(n,12)
    return date(y,m+1,min(today.day,calendar.monthrange(y,m+1)[1])).isoformat()


def export_market(database,output,months=1):
    start=cutoff(months)
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(output) as dst:
        src.execute('BEGIN')
        manifest=dict(exported_at=datetime.now(timezone.utc).isoformat(),cutoff=start,counts={})
        for table in TABLES:
            dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0])
            query='SELECT * FROM '+table+(' WHERE timestamp>=?' if table!='tq_status' else '')
            cur=src.execute(query,(start,) if table!='tq_status' else ())
            marks=','.join('?' for _ in cur.description);count=0
            while True:
                rows=cur.fetchmany(2000)
                if not rows:break
                dst.executemany('INSERT INTO '+table+' VALUES('+marks+')',rows);count+=len(rows)
            manifest['counts'][table]=count
        dst.execute('CREATE TABLE export_manifest(payload TEXT NOT NULL)')
        dst.execute('INSERT INTO export_manifest VALUES(?)',(json.dumps(manifest),))
    return manifest


def import_market(database,source,backup):
    database=Path(database).resolve();source=Path(source).resolve();backup=Path(backup).resolve()
    if len({database,source,backup})!=3:raise ValueError('Source, target and backup must differ')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src:
        if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Invalid source snapshot')
        manifest=json.loads(src.execute('SELECT payload FROM export_manifest').fetchone()[0])
        for table in TABLES:
            if src.execute('SELECT count(*) FROM '+table).fetchone()[0]!=manifest['counts'][table]:raise ValueError('Snapshot count mismatch')
    with sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=30) as dst:
        active=dst.execute('SELECT expires FROM tq_lease WHERE id=1').fetchone()
        import time
        if active and active[0]>time.time():raise RuntimeError('Stop the local collector before importing')
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(backup) as saved:dst.backup(saved)
        dst.execute('ATTACH DATABASE ? AS remote',(source.as_uri()+'?mode=ro',))
        dst.execute('BEGIN IMMEDIATE')
        for table in TABLES:
            local_cols=[r[1] for r in dst.execute('PRAGMA main.table_info('+table+')')]
            remote_cols=[r[1] for r in dst.execute('PRAGMA remote.table_info('+table+')')]
            if local_cols!=remote_cols:raise ValueError('Market schema mismatch: '+table)
            dst.execute('DELETE FROM main.'+table)
            dst.execute('INSERT INTO main.'+table+' SELECT * FROM remote.'+table)
        for table in ('tq_health','tq_coverage','tq_history_jobs'):
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():dst.execute('DELETE FROM '+table)
        dst.execute('DELETE FROM tq_lease WHERE expires<=?',(time.time(),))
        dst.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('market_read_only','1'),('history_months','1'),('last_remote_sync',json.dumps(manifest))])
        dst.commit()
    return manifest


def merge_market(database, source, backup=None):
    """Insert absent market evidence. Keep local evidence on a key conflict."""
    database = Path(database).resolve()
    source = Path(source).resolve()
    if source == database:
        raise ValueError('Source and target must differ')
    mode = 'rw' if backup else 'ro'
    with sqlite3.connect(database.as_uri() + '?mode=' + mode, uri=True, timeout=30) as db:
        db.execute('ATTACH DATABASE ? AS incoming', (source.as_uri() + '?mode=ro',))
        if db.execute('PRAGMA incoming.quick_check').fetchone() != ('ok',):
            raise ValueError('Invalid snapshot')
        manifest = json.loads(db.execute('SELECT payload FROM incoming.export_manifest').fetchone()[0])
        if backup:
            replica = db.execute("SELECT value FROM settings WHERE key='market_read_only'").fetchone()
            active = db.execute("SELECT 1 FROM tq_lease WHERE id=1 AND expires>strftime('%s','now')").fetchone()
            if replica != ('1',) or active:
                raise ValueError('Merge requires an initialized local replica with no active collector')
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(backup) as saved:
                db.backup(saved)
        db.execute('BEGIN IMMEDIATE' if backup else 'BEGIN')
        stats = {}
        for table in TABLES:
            schema = db.execute(f'PRAGMA main.table_info({table})').fetchall()
            if not schema or schema != db.execute(f'PRAGMA incoming.table_info({table})').fetchall():
                raise ValueError('Schema mismatch: ' + table)
            keys = [row[1] for row in schema if row[5]]
            values = [row[1] for row in schema if not row[5]]
            join_keys = ' AND '.join(f's.{k}=t.{k}' for k in keys)
            different = ' OR '.join(f's.{col} IS NOT t.{col}' for col in values)
            joined = f' FROM incoming.{table} s LEFT JOIN main.{table} t ON {join_keys}'
            missing = f't.{keys[0]} IS NULL'
            conflict = f't.{keys[0]} IS NOT NULL AND ({different})'
            fields = ['count(*)', f'count(CASE WHEN {missing} THEN 1 END)',
                      f'count(CASE WHEN {conflict} THEN 1 END)']
            if table != 'tq_status':
                fields.extend([f'min(CASE WHEN {missing} THEN s.timestamp END)',
                               f'max(CASE WHEN {missing} THEN s.timestamp END)'])
            count, new, conflicts, *bounds = db.execute('SELECT ' + ','.join(fields) + joined).fetchone()
            if count != manifest['counts'][table]:
                raise ValueError('Snapshot count mismatch: ' + table)
            stats[table] = dict(incoming=count, new=new, conflicts=conflicts)
            if bounds:
                stats[table]['new_range'] = bounds
            if conflicts:
                sample = 'SELECT ' + ','.join('s.' + k for k in keys) + joined + ' WHERE ' + conflict + ' LIMIT 5'
                stats[table]['conflict_examples'] = [dict(zip(keys, row)) for row in db.execute(sample)]
            if backup and new:
                db.execute(f'INSERT INTO main.{table} SELECT s.*' + joined + ' WHERE ' + missing)
            if table == 'tq_status':
                newer = """WHERE t.product=s.product AND json_extract(s.payload,'$.quote_time')>
                           coalesce(json_extract(t.payload,'$.quote_time'),'')"""
                stats[table]['newer_status_updates'] = db.execute(
                    'SELECT count(*) FROM main.tq_status t, incoming.tq_status s ' + newer).fetchone()[0]
                if backup:
                    db.execute('UPDATE main.tq_status AS t SET payload=s.payload FROM incoming.tq_status AS s ' + newer)
        if backup:
            old = db.execute("SELECT value FROM settings WHERE key='last_remote_sync'").fetchone()
            if not old or datetime.fromisoformat(manifest['exported_at']) > datetime.fromisoformat(json.loads(old[0])['exported_at']):
                db.execute("INSERT OR REPLACE INTO settings VALUES('last_remote_sync',?)", (json.dumps(manifest),))
        return stats


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export','import','merge'])
    parser.add_argument('--apply',action='store_true',help='Apply a merge; default is a read-only preview')
    parser.add_argument('--replace',action='store_true',help='Explicitly replace all four local market tables')
    parser.add_argument('--database',required=True)
    parser.add_argument('--output');parser.add_argument('--source');parser.add_argument('--backup')
    parser.add_argument('--months',type=int,default=1,choices=range(1,37))
    args=parser.parse_args()
    if args.action=='export':
        if not args.output:parser.error('--output is required')
        result=export_market(args.database,args.output,args.months)
    elif args.action=='merge':
        if not args.source or (args.apply and not args.backup):parser.error('Merge requires --source and apply requires --backup')
        result=merge_market(args.database,args.source,args.backup if args.apply else None)
    else:
        if not args.replace:parser.error('Use merge for incremental sync, or --replace for full replacement')
        if not args.source or not args.backup:parser.error('--source and --backup are required')
        result=import_market(args.database,args.source,args.backup)
    print(json.dumps(result))
