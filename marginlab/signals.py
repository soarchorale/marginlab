"""Minute-close signal evaluation from a read-only market ledger."""
from datetime import datetime, timedelta
import json
import math
import sqlite3
import time
from pathlib import Path

import pandas as pd

from .analytics import DriftConfig, price_visits, rolling_metrics
from .coverage import ZONE
from .feishu import TTL, WATCH_TTL, card
from .watch import add_comovement, observation_events, smile_wings
from .logging_setup import event
from .tqfeed import PRODUCTS

VERSION = 'minute-v2'
CONFIG = DriftConfig()
MINUTE = timedelta(minutes=1)
TRADING_MINUTES = (*range(571, 691), *range(781, 901))
OBS_COLUMNS = ('endpoint', 'high', 'low', 'close', 'vix')
DDL = """
CREATE TABLE IF NOT EXISTS observations (
    product TEXT, endpoint TEXT, high REAL, low REAL, close REAL, vix REAL, quality TEXT,
    PRIMARY KEY(product,endpoint));
CREATE TABLE IF NOT EXISTS checkpoints (
    product TEXT, version TEXT, endpoint TEXT, states TEXT, PRIMARY KEY(product,version));
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY, product TEXT, endpoint TEXT, payload TEXT, status TEXT,
    created REAL, expires REAL, next_try REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
    finished REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS signal_triggers (
    event_id TEXT NOT NULL REFERENCES outbox(id) ON DELETE CASCADE,
    rule TEXT NOT NULL, direction TEXT NOT NULL, evidence TEXT NOT NULL,
    PRIMARY KEY(event_id,rule));
CREATE TABLE IF NOT EXISTS delivery_attempts (
    event_id TEXT NOT NULL REFERENCES outbox(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL, started REAL NOT NULL, finished REAL, outcome TEXT NOT NULL, reason TEXT,
    PRIMARY KEY(event_id,attempt));
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status,next_try);
"""


def open_market(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)


def open_state(path):
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA foreign_keys=ON')
    db.row_factory = sqlite3.Row
    return db


def initialize(db):
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(DDL)
    # The CLI holds the exclusive process lock before recovery.
    with db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(outbox)')}
        if 'tier' not in columns:
            db.execute("ALTER TABLE outbox ADD COLUMN tier TEXT NOT NULL DEFAULT 'confirmed'")
            db.execute('ALTER TABLE outbox ADD COLUMN context TEXT')
            db.execute('ALTER TABLE delivery_attempts ADD COLUMN payload TEXT')
            db.execute("UPDATE outbox SET status='suppressed',reason='rule_upgrade' WHERE status='pending'")
        db.execute("""INSERT OR IGNORE INTO checkpoints(product,version,endpoint,states)
                      SELECT product,?,endpoint,states FROM checkpoints WHERE version='minute-v1'""", (VERSION,))
        now = time.time()
        db.execute("UPDATE delivery_attempts SET outcome='unknown',reason='interrupted_send',finished=? WHERE outcome='sending'", (now,))
        db.execute("UPDATE outbox SET status='unknown',reason='interrupted_send',finished=? WHERE status='sending'", (now,))


def day_inputs(market, product, day, cursor, until):
    midnight = datetime.combine(day, datetime.min.time())
    endpoints = [midnight + timedelta(minutes=m) for m in TRADING_MINUTES]
    endpoints = [t for t in endpoints if cursor < t <= until]
    if not endpoints:
        return []
    first, last = endpoints[0], endpoints[-1]
    # Short, coherent snapshot even when the two collector commits arrive separately.
    with market:
        market.execute('BEGIN')
        bars = {r[0]: r[1:] for r in market.execute(
            """SELECT timestamp,high,low,close FROM tq_bars
               WHERE symbol=? AND timestamp BETWEEN ? AND ?""",
            (PRODUCTS[product]['index'], (first-MINUTE).isoformat(), (last-MINUTE).isoformat()))}
        snaps = {r[0]: r[1:] for r in market.execute(
            """SELECT timestamp,value,quality,json_extract(details,'$.sampling'),
                      json_extract(details,'$.market_heartbeat_epoch'),json_extract(details,'$.recovery'),details,quotes
               FROM tq_snapshots WHERE product=? AND timestamp BETWEEN ? AND ?""",
            (product, first.isoformat(), last.isoformat()))}
    if not bars and not snaps and (cursor.date() != day or cursor.hour == 0):
        return []
    rows = []
    for t in endpoints:
        bar = bars.get((t-MINUTE).isoformat())
        snap = snaps.get(t.isoformat())
        if (not bar or not snap) and (until-t).total_seconds() < 30:
            break
        high, low, close = bar or (None, None, None)
        valid_bar = all(x is not None and math.isfinite(x) and x > 0 for x in (high, low, close))
        vix = None
        wings = {}
        if not valid_bar:
            quality = 'missing_bar'
        elif not snap:
            quality = 'missing_snapshot'
        else:
            value, quality, sampling, heartbeat, recovery, details, quotes = snap
            if quality == 'valid':
                epoch = t.replace(tzinfo=ZONE).timestamp()
                if (sampling == 'last-known-before-boundary' and not recovery
                        and isinstance(heartbeat, (int, float)) and 0 <= epoch-heartbeat <= 10
                        and value is not None and math.isfinite(value) and value > 0
                        and t.strftime('%H:%M') not in ('14:58', '14:59', '15:00')):
                    vix = value
                    wings = smile_wings(t.isoformat(), details, quotes)
                else:
                    quality = 'non_live'
        rows.append((t.isoformat(), high, low, close, vix, quality, wings))
    return rows


def metrics_frame(rows):
    frame = pd.DataFrame.from_records(rows, columns=OBS_COLUMNS)
    frame['timestamp'] = pd.to_datetime(frame.endpoint)
    for key in ('high', 'low', 'close', 'vix'):
        frame[key] = pd.to_numeric(frame[key], errors='coerce').astype(float)
    return frame


def evaluate(db, product, rows, states):
    start = rows[0][0]
    month = start[:7]
    if states.get('month') != month:
        states = {'month': month, 'residual': states.get('residual')}
    # Keep every bar in the window, including explicit gaps. The extra history
    # covers model training, accumulation, percentile history and persistence.
    count = CONFIG.window + CONFIG.horizon + 250 + CONFIG.persistence + 4
    fields = ','.join(OBS_COLUMNS)
    previous = db.execute(f"""SELECT {fields} FROM observations WHERE product=? AND endpoint<?
                              ORDER BY endpoint DESC LIMIT ?""", (product, start, count)).fetchall()
    fresh = [r[:5] for r in rows]
    frame = metrics_frame([*reversed(previous), *fresh])
    metrics = rolling_metrics(frame, CONFIG, 252*240)
    known = (metrics.accumulated.notna() & metrics.upper.notna()).rolling(CONFIG.persistence).sum()
    # One reproducible anchor per Shanghai calendar month, using recorded inputs.
    history = db.execute(f"""SELECT {fields} FROM observations WHERE product=?
                             AND endpoint>=? AND endpoint<? ORDER BY endpoint""",
                         (product, month+'-01', start)).fetchall()
    monthly = metrics_frame([*history, *fresh])
    ready = monthly.close.notna().rolling(CONFIG.atr_window).sum().eq(CONFIG.atr_window)
    visits = {}
    anchor = None
    if ready.any():
        first = int(ready.idxmax()) + 1 - CONFIG.atr_window
        series = monthly.iloc[first:].reset_index(drop=True)
        visit_list, _, anchor = price_visits(series, CONFIG, reset_on_gap=True)
        states['anchor'] = dict(endpoint=series.endpoint.iloc[anchor['index']],
                                price=anchor['price'], atr=anchor['atr'])
        visits = {series.endpoint.iloc[v['index']]: v for v in visit_list}
    for key in ('close', 'vix'):
        change = (metrics[key] / metrics[key].shift(CONFIG.horizon) - 1) * 100
        complete = metrics[key].rolling(CONFIG.horizon + 1).count() == CONFIG.horizon + 1
        metrics[key+'_change'] = change.where(complete)
    add_comovement(metrics)
    alerts = []
    for row in metrics.iloc[len(previous):].itertuples():
        visit = visits.get(row.endpoint)
        prior_active = states.get('active', {})
        triggers = observation_events(row, visit, anchor, states, rows[row.Index-len(previous)][-1])
        active = states['active']

        if known.iloc[row.Index] == CONFIG.persistence:
            state = {'异常升波': 'up', '异常降波': 'down'}.get(row.state, 'normal')
            active['residual'] = state
            if state != states.get('residual') and state != 'normal':
                up = state == 'up'
                triggers.append(dict(rule='residual', direction=state, value=row.accumulated*100,
                                     threshold=(row.upper if up else row.lower)*100,
                                     percentile=95 if up else 5))
            states['residual'] = state
        if visit and visit['drift'] is not None:
            side = visit['direction']
            state = visit['state']
            direction = 'up' if state.startswith('异常升波') else 'down'
            if state.endswith('已确认') and states.get(side) != state:
                triggers.append(dict(rule='revisit', direction=direction, value=100*visit['drift'],
                                     entry=side, matches=visit['matches'], anchor=anchor['price']))
            states[side] = state
        # A confirmed visit remains current only while spot stays in its band.
        if anchor and anchor['low'] <= row.close <= anchor['high'] and math.isfinite(row.vix):
            if visit:
                if visit['state'].endswith('已确认'):
                    active['revisit'] = 'up' if visit['drift'] > 0 else 'down'
            elif 'revisit' in prior_active:
                active['revisit'] = prior_active['revisit']
        since = states.get('since', {})
        states['since'] = {rule: since.get(rule, row.endpoint) if prior_active.get(rule)==direction else row.endpoint
                           for rule, direction in active.items() if direction}
        if triggers:
            tier = classify(triggers, states, product, row.endpoint)
            fields = ('vix','close','vix_change','close_change','corr_return')
            context = {k:float(getattr(row,k)) if math.isfinite(getattr(row,k)) else None for k in fields}
            context['corr_zone'] = states.get('corr', {}).get('zone')
            alerts.append((row.endpoint, context, triggers, tier))
    return states, alerts


def classify(triggers, states, product, stamp):
    """Keep every transition, tagging which evidence can contribute to a notification."""
    recent = states.setdefault('watch_episodes', {})
    confirmed = {item['rule'] for item in triggers if item.get('level', 'confirmed') == 'confirmed'}
    parents = {'residual':'residual_watch', 'revisit':'baseline'}
    epoch = datetime.fromisoformat(stamp).replace(tzinfo=ZONE).timestamp()
    eligible = False
    for item in triggers:
        rule = item['rule']
        level = item.setdefault('level', 'confirmed')
        item['since'] = states['since'].get(rule)
        if level == 'context':
            item['policy'] = 'record_only'
        elif level == 'confirmed':
            parent = recent.get(parents[rule])
            if parent and parent['direction'] == item['direction'] and parent['stamp'][:10] == stamp[:10]:
                item['related_event'] = parent['id']
            item['policy'] = 'notify'
        else:
            last = recent.get(rule)
            if rule in {parents[k] for k in confirmed}:
                item['policy'] = 'covered_by_confirmation'
            elif last and last['direction'] == item['direction'] and epoch-last['epoch'] < 600:
                item.update(policy='cooldown', related_event=last['id'])
            else:
                item['policy'] = 'notify'
                recent[rule] = dict(id=f'{VERSION}/{product}/{stamp}', direction=item['direction'],
                                    stamp=stamp, epoch=epoch)
                eligible = True
    return 'confirmed' if confirmed else 'watch' if eligible else 'record'


def advance(market, db, product, now, live_since=None):
    now = now.astimezone(ZONE).replace(tzinfo=None)
    checkpoint = db.execute('SELECT endpoint,states FROM checkpoints WHERE product=? AND version=?',
                            (product, VERSION)).fetchone()
    if checkpoint:
        cursor = datetime.fromisoformat(checkpoint[0])
        states = json.loads(checkpoint[1])
    else:
        first = market.execute('SELECT min(timestamp) FROM tq_bars WHERE symbol=?',
                               (PRODUCTS[product]['index'],)).fetchone()[0]
        if not first:
            return
        cursor = max(datetime.fromisoformat(first).replace(hour=0, minute=0),
                     (now-timedelta(days=31)).replace(hour=0, minute=0, second=0, microsecond=0))
        states = {}
    day = cursor.date()
    while day <= now.date():
        if day.weekday() < 5:
            rows = day_inputs(market, product, day, cursor, now)
            if rows:
                ready_at = time.time()
                states, alerts = evaluate(db, product, rows, states)
                created = time.time()
                with db:
                    db.executemany('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)',
                                   [(product, *r[:-1]) for r in rows])
                    for stamp, context, triggers, tier in alerts:
                        event_id = f'{VERSION}/{product}/{stamp}'
                        boundary = datetime.fromisoformat(stamp).replace(tzinfo=ZONE).timestamp()
                        ttl = WATCH_TTL if tier == 'watch' else TTL
                        status, reason = 'observed', None
                        if tier == 'record':
                            reason = 'cooldown' if any(t['policy']=='cooldown' for t in triggers) else 'record_only'
                        elif live_since is not None and boundary >= live_since:
                            fresh = created <= boundary+ttl if tier=='watch' else created < boundary+ttl
                            status = 'pending' if fresh else 'expired'
                            reason = 'ttl' if status=='expired' else None
                        display = [t for t in triggers if t['policy']=='notify'] or triggers
                        payload = card(product, stamp, context, display)
                        inserted = db.execute("""INSERT OR IGNORE INTO outbox
                                      (id,product,endpoint,payload,status,created,expires,tier,context,reason)
                                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                   (event_id, product, stamp,
                                    json.dumps(payload, ensure_ascii=False, allow_nan=False),
                                    status, created, boundary+ttl, tier, json.dumps(context,allow_nan=False), reason))
                        if inserted.rowcount:
                            db.executemany('INSERT INTO signal_triggers VALUES(?,?,?,?)',
                                           [(event_id, item['rule'], item['direction'],
                                             json.dumps(item, ensure_ascii=False, allow_nan=False))
                                            for item in triggers])
                    db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',
                               (product, VERSION, rows[-1][0], json.dumps(states)))
                event('signal_minutes_processed', product=product, endpoint=rows[-1][0],
                      minutes=len(rows), alerts=len(alerts), quality=rows[-1][-2],
                      compute_ms=round((created-ready_at)*1000),
                      input_lag_ms=round((ready_at-datetime.fromisoformat(rows[-1][0]).replace(tzinfo=ZONE).timestamp())*1000))
        day += timedelta(days=1)
