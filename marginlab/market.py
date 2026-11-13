"""Authenticated TqSdk dashboard payloads. No provider calls on GET requests."""
import json
import pandas as pd
import numpy as np
from flask import current_app, g
from .storage import connect
from .tqfeed import PRODUCTS, Feed
from .tqstore import read_bars, statuses
from .data import records
from .analytics import analyze, DriftConfig


def request_feed(app,product):
    feed = app.extensions['tq_feed']
    feed.touch(product,g.auth_session['token_hash'])
    return feed


def resample(rows,period):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df.timestamp)
    # Bars are aligned separately per morning/afternoon session; never bridge lunch.
    n = 1440 if period == 'daily' else int(period)
    minutes = df.timestamp.dt.hour*60+df.timestamp.dt.minute
    offset = np.where(minutes<720,570,780)
    bucket = ((minutes-offset)//n)*n+offset
    df['bucket'] = df.timestamp.dt.normalize()+pd.to_timedelta(bucket,unit='m')
    if period == 'daily':
        df['bucket'] = df.timestamp.dt.normalize()
    grouped = df.groupby('bucket',sort=True)
    result = grouped.agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),
                         volume=('volume','sum'),open_oi=('open_oi','first'),close_oi=('close_oi','last'),count=('close','size'))
    # Require complete bins; daily requires both trading sessions.
    result = result[result['count'] == (240 if period=='daily' else n)].drop(columns='count')
    result.index.name='timestamp'
    return result.reset_index()


def payload(app,args):
    from .cache import prune,settings
    product = args.get('symbol','50')
    period = args.get('period','1')
    if product not in PRODUCTS or period not in ('1','5','15','30','60','daily'):
        raise ValueError('Unsupported index product or period')
    cutoff = prune(app)
    start = pd.Timestamp(args.get('start') or cutoff)
    end = pd.Timestamp(args.get('end') or pd.Timestamp.now(tz='Asia/Shanghai').date())+pd.Timedelta(days=1)
    if start.tzinfo or end.tzinfo or pd.isna(start) or pd.isna(end) or start>=end:
        raise ValueError('Invalid date range')
    start = max(start,pd.Timestamp(cutoff))
    feed = request_feed(app,product)
    status_map = statuses(app.config['DATABASE'])
    status = status_map.get(product,{})
    raw = read_bars(app.config['DATABASE'],PRODUCTS[product]['index'],start.isoformat(),end.isoformat(),limit=None)
    future = read_bars(app.config['DATABASE'],status.get('contract',''),start.isoformat(),end.isoformat(),limit=None)
    ib,fb = resample(raw,period),resample(future,period)
    with connect(app.config['DATABASE']) as db:
        snaps = [dict(r) for r in db.execute('SELECT timestamp,value,quality,details FROM tq_snapshots WHERE product=? AND timestamp>=? AND timestamp<? ORDER BY timestamp',
                                          (product,start.isoformat(),end.isoformat()))]
        job = db.execute('SELECT payload FROM tq_history_jobs WHERE product=?',(product,)).fetchone()
    research = None
    reason = 'MVIX 报价历史尚在积累；不会用当前报价回填历史'
    if not ib.empty:
        # Quote snapshot at 09:31 belongs to the 09:30 bar. Join exact endpoints only.
        if period == 'daily':
            endpoint = ib.timestamp+pd.Timedelta(hours=15)
        else:
            endpoint = ib.timestamp+pd.Timedelta(minutes=int(period))
        values = {pd.Timestamp(r['timestamp']):r['value'] for r in snaps if r['quality']=='valid'}
        ib['vix'] = [values.get(t,np.nan) for t in endpoint]
        # Focus the comparison on available paired history; retain internal gaps.
        if ib.vix.notna().any():
            ib = ib.loc[ib.vix.first_valid_index():].reset_index(drop=True)
        # Keep missing observations: deleting them would create artificial long returns.
        if len(ib) >= 20 and ib.vix.notna().sum() >= 20:
            from . import number
            cfg = DriftConfig(window=number(args,'window',60,20,500,True),horizon=number(args,'horizon',16,2,100,True),
                              correlation=number(args,'correlation',30,5,250,True),band=number(args,'band',.25,.05,.9),
                              gap=number(args,'gap',4,1,100,True),lookback_days=number(args,'lookback_days',5,1,250,True))
            result = analyze(ib,cfg,252 if period=='daily' else 252*240/int(period))
            research = dict(bars=records(result['bars']),visits=result['visits'],anchor=result['anchor'],
                meta=dict(symbol=product,name=PRODUCTS[product]['name'],period=period,source='live',count=len(ib),
                          asof=str(ib.timestamp.iloc[-1]),cache=None,settings=cfg.__dict__))
            reason = None
    if product == '500':
        reason = '中证500暂无对应股指期权；只显示期货行情，不开展 MVIX 漂移研究'
    latest = dict(snaps[-1]) if snaps else None
    valid = [r for r in snaps if r['quality']=='valid' and r['value'] is not None]
    latest_valid = dict(valid[-1]) if valid else None
    for snapshot in (latest, latest_valid):
        if snapshot:
            snapshot['details'] = json.loads(snapshot['details'])
    mvix_history = [dict(timestamp=r['timestamp'],value=r['value'] if r['quality']=='valid' else None) for r in snaps]
    if snaps:
        from .history import boundaries
        indexed = {r['timestamp']:r['value'] for r in mvix_history}
        grid = set(indexed) | {t.tz_localize(None).isoformat() for t in boundaries(snaps[0]['timestamp'],snaps[-1]['timestamp'])}
        mvix_history = [dict(timestamp=t,value=indexed.get(t)) for t in sorted(grid)]

    return dict(source='tqsdk',product=product,period=period,status=status,cards=list(status_map.values()),
                index_bars=records(ib.tail(1500)) if not ib.empty else [],
                future_bars=records(fb.tail(1500)) if not fb.empty else [],
                mvix=latest,mvix_latest_valid=latest_valid,mvix_history=mvix_history,
                mvix_backfill=json.loads(job['payload']) if job else None,
                mvix_cache=dict(storage='sqlite',valid_count=len(valid),total_count=len(snaps),
                                first=valid[0]['timestamp'] if valid else None,
                                latest=valid[-1]['timestamp'] if valid else None),
                research=research,research_reason=reason,
                collector=dict(state=feed.state,error=feed.error,automatic=feed.autonomous,
                               schedule='Asia/Shanghai weekdays 09:20-15:10',products=list(PRODUCTS)),
                cache=dict(retention_months=settings(app)['months'],cutoff=cutoff,
                           history_note='分钟历史按接口权限及可用窗口增量积累，保留期限不代表已覆盖完整历史',
                           display_limit=1500))
