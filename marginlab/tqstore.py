"""Minute bars and auditable quote snapshots, separate from legacy daily cache."""
import json
import pandas as pd
from .storage import connect

DDL = '''
CREATE TABLE IF NOT EXISTS tq_bars(symbol TEXT, timestamp TEXT, open REAL, high REAL,
 low REAL, close REAL, volume REAL, open_oi REAL, close_oi REAL, PRIMARY KEY(symbol,timestamp));
CREATE TABLE IF NOT EXISTS tq_snapshots(product TEXT, timestamp TEXT, value REAL, quality TEXT,
 details TEXT, quotes TEXT, PRIMARY KEY(product,timestamp));
CREATE TABLE IF NOT EXISTS tq_status(product TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS tq_lease(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, expires REAL);
'''


def initialize(path):
    with connect(path) as db:
        db.executescript(DDL)


def save_bars(path, symbol, frame, cutoff):
    rows = []
    for r in frame.itertuples():
        stamp = pd.Timestamp(r.datetime,unit='ns',tz='UTC').tz_convert('Asia/Shanghai').tz_localize(None)
        if stamp.isoformat() < cutoff:
            continue
        rows.append((symbol,stamp.isoformat(),float(r.open),float(r.high),float(r.low),float(r.close),
                     float(r.volume),float(r.open_oi),float(r.close_oi)))
    with connect(path) as db:
        db.executemany('INSERT OR REPLACE INTO tq_bars VALUES(?,?,?,?,?,?,?,?,?)', rows)


def save_snapshot(path,product,stamp,result,quotes):
    with connect(path) as db:
        # Never rewrite a past minute using a later snapshot.
        db.execute('INSERT OR IGNORE INTO tq_snapshots VALUES(?,?,?,?,?,?)',
                   (product,stamp,result.get('value'),result['quality'],json.dumps(result,allow_nan=False),json.dumps(quotes,allow_nan=False)))


def get_snapshot(path, product, stamp):
    """An existing minute is immutable, including its quality and quote evidence."""
    with connect(path) as db:
        row = db.execute('SELECT timestamp,value,quality,details FROM tq_snapshots WHERE product=? AND timestamp=?',
                         (product,stamp)).fetchone()
    return dict(row) if row else None


def read_bars(path,symbol,start,end,limit=2500):
    with connect(path) as db:
        rows = db.execute('SELECT * FROM (SELECT * FROM tq_bars WHERE symbol=? AND timestamp>=? AND timestamp<? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp',
                          (symbol,start,end,-1 if limit is None else limit)).fetchall()
    return [dict(r) for r in rows]


def statuses(path):
    with connect(path) as db:
        return {r['product']:json.loads(r['payload']) for r in db.execute('SELECT * FROM tq_status')}
