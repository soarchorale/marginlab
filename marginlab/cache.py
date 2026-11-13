"""Bounded rolling history with incremental refresh and explicit stale fallback."""
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import BoundedSemaphore, Thread
from queue import Queue, Empty
import time
import pandas as pd
from .storage import connect
from .analytics import validate_bars

_slots = BoundedSemaphore(2)


def settings(app):
    with connect(app.config['DATABASE']) as db:
        saved = dict(db.execute('SELECT key,value FROM settings').fetchall())
    return dict(months=int(saved.get('history_months', app.config['HISTORY_MONTHS'])),
                refresh_seconds=int(saved.get('refresh_seconds', app.config['MARKET_REFRESH_SECONDS'])))


def cutoff_for(months):
    return (pd.Timestamp(datetime.now(ZoneInfo('Asia/Shanghai')).date())-pd.DateOffset(months=months)).strftime('%Y-%m-%d')


def prune(app):
    cutoff = cutoff_for(settings(app)['months'])
    with connect(app.config['DATABASE']) as db:
        db.execute('DELETE FROM market_bars WHERE timestamp<?', (cutoff,))
        db.execute('DELETE FROM tq_bars WHERE timestamp<?',(cutoff,))
        db.execute('DELETE FROM tq_snapshots WHERE timestamp<?',(cutoff,))
        db.execute('DELETE FROM tq_option_minutes WHERE timestamp<?',(cutoff,))
        db.execute('UPDATE market_sync SET coverage_start=? WHERE coverage_start<?', (cutoff,cutoff))
    return cutoff


def bounded_fetch(fetcher, args, timeout):
    if not _slots.acquire(blocking=False):
        raise RuntimeError('The market provider is processing other requests')
    output = Queue(maxsize=1)
    def run():
        try:
            output.put((True, fetcher(*args)))
        except Exception as exc:
            output.put((False, exc))
        finally:
            _slots.release()
    Thread(target=run, daemon=True).start()
    try:
        ok, value = output.get(timeout=timeout)
    except Empty as exc:
        raise RuntimeError('Market data request timed out') from exc
    if not ok:
        raise value
    return value


def cached_live(app, symbol, period, start=None, end=None, fetcher=None):
    from .data import live
    if period != 'daily':
        raise ValueError('The legacy online cache supports daily bars only; import full timestamps for intraday research')
    config = settings(app)
    cutoff = prune(app)
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    try:
        requested_start = pd.Timestamp(start or cutoff).strftime('%Y-%m-%d')
        requested_end = pd.Timestamp(end or today).strftime('%Y-%m-%d')
    except Exception as exc:
        raise ValueError('Invalid date format') from exc
    start, end = max(cutoff, requested_start), min(today, requested_end)
    if start > end:
        raise ValueError('The requested range is outside retention; adjust dates or increase the retention period')
    path, now = app.config['DATABASE'], time.time()
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO market_sync(symbol,period) VALUES(?,?)', (symbol,period))
        status = dict(db.execute('SELECT * FROM market_sync WHERE symbol=? AND period=?', (symbol,period)).fetchone())
        latest = db.execute('SELECT MAX(timestamp) FROM market_bars WHERE symbol=? AND period=?', (symbol,period)).fetchone()[0]
        backfill = not latest or not status['coverage_start'] or status['coverage_start'] > cutoff
        due = backfill or not status['success'] or now-status['success'] >= config['refresh_seconds']
        retry_ok = not status['attempted'] or now-status['attempted'] >= app.config['MARKET_RETRY_SECONDS']
        refresh = due and retry_ok and status['lease_until'] <= now
        if refresh:
            db.execute('UPDATE market_sync SET attempted=?,lease_until=? WHERE symbol=? AND period=?',
                       (now,now+app.config['MARKET_TIMEOUT']+15,symbol,period))
    refreshed = False
    if refresh:
        # Overlap seven calendar days to absorb provider corrections. Expanding retention backfills.
        fetch_start = cutoff if backfill else max(cutoff, (pd.Timestamp(latest)-pd.Timedelta(days=7)).strftime('%Y-%m-%d'))
        try:
            fresh = bounded_fetch(fetcher or live, (symbol,period,fetch_start,today), app.config['MARKET_TIMEOUT'])
            fresh = validate_bars(fresh, min_records=1)
            fresh = fresh[(fresh.timestamp >= pd.Timestamp(cutoff)) & (fresh.timestamp <= pd.Timestamp(today))]
            if fresh.empty:
                raise ValueError('The provider returned no valid records')
            rows = [(symbol,period,r.timestamp.strftime('%Y-%m-%d'),float(r.open),float(r.high),float(r.low),float(r.close),float(r.vix),float(r.volume) if hasattr(r,'volume') and pd.notna(r.volume) else None) for r in fresh.itertuples()]
            with connect(path) as db:
                db.executemany('INSERT OR REPLACE INTO market_bars VALUES(?,?,?,?,?,?,?,?,?)', rows)
                db.execute('UPDATE market_sync SET success=?,coverage_start=?,lease_until=0,last_error=NULL WHERE symbol=? AND period=?',
                           (time.time(),cutoff,symbol,period))
            refreshed = True
        except Exception as exc:
            app.logger.warning('Market refresh failed: %s', type(exc).__name__)
            with connect(path) as db:
                db.execute('UPDATE market_sync SET lease_until=0,last_error=? WHERE symbol=? AND period=?', ('Upstream market refresh failed or timed out',symbol,period))
    with connect(path) as db:
        rows = db.execute('SELECT timestamp,open,high,low,close,vix,volume FROM market_bars WHERE symbol=? AND period=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp', (symbol,period,start,end)).fetchall()
        status = dict(db.execute('SELECT * FROM market_sync WHERE symbol=? AND period=?', (symbol,period)).fetchone())
    if len(rows) < 20:
        raise RuntimeError(f'Only {len(rows)} valid cached bars are available; at least 20 are required. Retry later or import CSV')
    frame = validate_bars(pd.DataFrame([dict(row) for row in rows]))
    stale = bool(status['last_error']) or not status['success'] or time.time()-status['success']>=config['refresh_seconds']
    return frame, dict(storage='sqlite', stale=stale, refreshed=refreshed, refreshing=status['lease_until']>time.time(),
                       last_success=datetime.fromtimestamp(status['success'], ZoneInfo('Asia/Shanghai')).isoformat() if status['success'] else None,
                       last_error=status['last_error'], retention_months=config['months'], cutoff=cutoff,
                       effective_start=start,effective_end=end,range_clipped=start!=requested_start or end!=requested_end)


def cache_summary(app):
    prune(app)
    with connect(app.config['DATABASE']) as db:
        rows = db.execute('SELECT symbol,period,COUNT(*) AS count,MIN(timestamp) AS first,MAX(timestamp) AS last FROM market_bars GROUP BY symbol,period').fetchall()
    with connect(app.config['DATABASE']) as db:
        minute = db.execute("SELECT symbol,'1m' AS period,COUNT(*) AS count,MIN(timestamp) AS first,MAX(timestamp) AS last FROM tq_bars GROUP BY symbol").fetchall()
        snapshots = db.execute("SELECT 'MVIX-'||product AS symbol,'1m snapshot' AS period,COUNT(*) AS count,MIN(timestamp) AS first,MAX(timestamp) AS last FROM tq_snapshots GROUP BY product").fetchall()
    return [dict(row) for row in list(rows)+list(minute)+list(snapshots)]
