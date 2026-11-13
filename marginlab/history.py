"""Reconstruct minute MVIX only from timestamped historical bid/ask ticks.

Stores the actual last tick before each boundary, not a price interpolation.
Recent-buffer imports are explicitly limited by provider coverage; no fake backfill.
"""
import json
import math
from collections import Counter
import numpy as np
import pandas as pd
from .mvix import calculate, METHOD
from .storage import connect
from .tqstore import get_snapshot, save_snapshot

ASOF_SOURCE = 'tqsdk_recent_tick_10000_contiguous_asof_v2'

DDL = '''
CREATE TABLE IF NOT EXISTS tq_option_minutes(
 product TEXT, symbol TEXT, timestamp TEXT, quote_ns INTEGER, expiry REAL, strike REAL,
 side TEXT, bid REAL, ask REAL, bid_volume REAL, ask_volume REAL, source TEXT,
 PRIMARY KEY(product,symbol,timestamp));
CREATE INDEX IF NOT EXISTS tq_option_minutes_time ON tq_option_minutes(product,timestamp);
CREATE TABLE IF NOT EXISTS tq_history_jobs(product TEXT PRIMARY KEY,payload TEXT);
'''


def initialize(path):
    with connect(path) as db:
        db.executescript(DDL)


def boundaries(start,end):
    a,b=pd.Timestamp(start),pd.Timestamp(end)
    if a.tzinfo is None:a=a.tz_localize('Asia/Shanghai')
    if b.tzinfo is None:b=b.tz_localize('Asia/Shanghai')
    times=pd.date_range(a.ceil('min'),b.floor('min'),freq='min')
    minute=times.hour*60+times.minute
    return times[(times.weekday<5)&(((minute>=571)&(minute<=690))|((minute>=781)&(minute<=900)))]


def minute_quotes(ticks,metadata,start,end,max_age=600):
    """Backward as-of, strictly before boundary; do not borrow future/lunch ticks."""
    if max_age is None:
        # An unchanged quote does not expire merely because no new event arrived.
        # This mode is only for a ready, contiguous historical serial, never live
        # snapshots or a disconnected feed. Later ticks bound coverage, not prices.
        serial=ticks.loc[ticks.datetime>0]
        if ('id' not in serial or serial.empty or not serial.id.notna().all()
                or not (serial.id.diff().dropna()==1).all()
                or not serial.datetime.is_monotonic_increasing):
            raise ValueError('Historical Tick IDs are not contiguous; quote carry-forward coverage cannot be verified')
        last=pd.Timestamp(int(serial.datetime.iloc[-1]),unit='ns',tz='UTC').tz_convert('Asia/Shanghai')
        covered=last.floor('min')
        close=last.ceil('min')
        if (close.hour,close.minute) in ((11,30),(15,0)) and (close-last).total_seconds()<=1:
            covered=close
        requested=pd.Timestamp(end)
        if requested.tzinfo is None:requested=requested.tz_localize('Asia/Shanghai')
        end=min(requested,covered)
    df=ticks.loc[ticks.datetime>0].sort_values('datetime').drop_duplicates('datetime',keep='last')
    if df.empty:return []
    ns=df.datetime.to_numpy(dtype=np.int64)
    result=[]
    def num(value):
        try:return float(value) if math.isfinite(float(value)) else None
        except (ValueError,TypeError):return None
    for stamp in boundaries(start,end):
        i=int(np.searchsorted(ns,stamp.value,side='left'))-1
        if i<0:continue
        quote=df.iloc[i]
        quote_at=pd.Timestamp(int(ns[i]),unit='ns',tz='UTC').tz_convert('Asia/Shanghai')
        # 11:30 is still the morning endpoint; 13:01 starts the afternoon.
        same_session=(quote_at.date()==stamp.date() and (quote_at.hour<12)==(stamp.hour<12))
        if not same_session or (max_age is not None and (stamp.value-int(ns[i]))/1e9>max_age):continue
        result.append(dict(metadata,timestamp=stamp.tz_localize(None).isoformat(),quote_ns=int(ns[i]),
                           bid=num(quote.bid_price1),ask=num(quote.ask_price1),
                           bid_volume=num(quote.get('bid_volume1')),ask_volume=num(quote.get('ask_volume1'))))
    return result


def store_minutes(path,product,rows,source='tqsdk_recent_tick_10000'):
    with connect(path) as db:
        db.executemany('''INSERT INTO tq_option_minutes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product,symbol,timestamp) DO UPDATE SET source=excluded.source
            WHERE tq_option_minutes.quote_ns=excluded.quote_ns
              AND tq_option_minutes.bid IS excluded.bid AND tq_option_minutes.ask IS excluded.ask''',
            [(product,r['symbol'],r['timestamp'],r['quote_ns'],r['expiry'],r['strike'],r['side'],
              r['bid'],r['ask'],r.get('bid_volume'),r.get('ask_volume'),source) for r in rows])


def reconstruct(path,product,universe,start,end,rate=.02):
    """Require complete known candidate chain. Existing valid minutes are immutable."""
    by_time={}
    with connect(path) as db:
        records=db.execute('SELECT * FROM tq_option_minutes WHERE product=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,symbol',
                           (product,pd.Timestamp(start).tz_localize(None).isoformat(),pd.Timestamp(end).tz_localize(None).isoformat())).fetchall()
    for r in records:by_time.setdefault(r['timestamp'],{})[r['symbol']]=dict(r)
    counts=Counter();valid_times=[]
    for stamp in boundaries(start,end):
        key=stamp.tz_localize(None).isoformat();at=stamp.timestamp()
        existing=get_snapshot(path,product,key)
        if existing and existing['quality']=='valid':
            counts['cached_valid']+=1;valid_times.append(key);continue
        # Select expirations for each historical minute, not by today's DTE.
        expiries={r['expiry'] for r in universe if r['expiry']>at}
        near=[e for e in expiries if e<=at+30*86400];far=[e for e in expiries if e>=at+30*86400]
        if not near or not far:counts['no_bracket']+=1;continue
        required=[r for r in universe if r['expiry'] in {max(near),min(far)}]
        available=by_time.get(key,{})
        if any(r['symbol'] not in available for r in required):
            counts['incomplete_chain']+=1;continue
        rows=[available[r['symbol']] for r in required]
        try:
            result=calculate(rows,at,rate)
        except (ValueError,TypeError,KeyError,OverflowError):
            counts['invalid_quotes']+=1;continue
        result.update(quality='valid',sampling='historical-tick-backward-asof',boundary=key,
                      source=sorted({r['source'] for r in rows}),quote_count=len(rows),method=METHOD,
                      oldest_quote_age_seconds=max((stamp.value-r['quote_ns'])/1e9 for r in rows))
        # A previous failed live minute may be repaired only with historical timestamped evidence.
        with connect(path) as db:
            db.execute('INSERT INTO tq_snapshots VALUES(?,?,?,?,?,?) ON CONFLICT(product,timestamp) DO UPDATE SET value=excluded.value,quality=excluded.quality,details=excluded.details,quotes=excluded.quotes WHERE tq_snapshots.quality<>?',
                       (product,key,result['value'],'valid',json.dumps(result,allow_nan=False),json.dumps(rows,allow_nan=False),'valid'))
        counts['inserted']+=1;valid_times.append(key)
    return dict(counts,first=valid_times[0] if valid_times else None,last=valid_times[-1] if valid_times else None)


def save_job(path,product,payload):
    with connect(path) as db:
        db.execute('INSERT OR REPLACE INTO tq_history_jobs VALUES(?,?)',(product,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
