"""One demand-driven, read-only TqSdk loop per database, gated by live sessions."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
import numpy as np
import pandas as pd
from .storage import connect
from . import tqstore
from .mvix import calculate

PRODUCTS = {
 '50':dict(name='上证50',index='SSE.000016',future='KQ.m@CFFEX.IH',option='HO'),
 '300':dict(name='沪深300',index='SSE.000300',future='KQ.m@CFFEX.IF',option='IO'),
 '500':dict(name='中证500',index='SSE.000905',future='KQ.m@CFFEX.IC',option=None),
 '1000':dict(name='中证1000',index='SSE.000852',future='KQ.m@CFFEX.IM',option='MO'),
}


def timestamp(value):
    return pd.Timestamp(value).tz_localize('Asia/Shanghai').timestamp() if value else 0


def iso(seconds):
    return pd.Timestamp(seconds,unit='s',tz='UTC').tz_convert('Asia/Shanghai').tz_localize(None).isoformat()


def finite(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (TypeError,ValueError):
        return None


@contextmanager
def session(auth_file):
    from tqsdk import TqApi, TqAuth, TqSim
    username,password = os.environ.get('TQ_USERNAME'),os.environ.get('TQ_PASSWORD')
    if not username or not password:
        try:
            credentials = json.loads(Path(auth_file).read_text())
            username,password = credentials['username'],credentials['password']
        except (OSError,ValueError,KeyError):
            raise RuntimeError('Configure TQ_USERNAME/TQ_PASSWORD or a local tqauth.json file') from None
    loop = asyncio.new_event_loop()
    try:
        with TqApi(TqSim(),auth=TqAuth(username,password),debug=False,disable_print=True,loop=loop) as api:
            yield api
    finally:
        if not loop.is_closed():
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


class Feed:
    def __init__(self,app):
        self.app = app
        self.path = app.config['DATABASE']
        self.lock = threading.Lock()
        self.demands = {}
        self.thread = None
        self.retry = 0
        self.owner = uuid.uuid4().hex
        self.state = 'idle'
        self.error = None
        self.autonomous = False
        self.stop_event = threading.Event()
        self.supervisor = None
        self.last_prune = 0

    @staticmethod
    def collection_window(now=None):
        local = pd.Timestamp.now(tz='Asia/Shanghai') if now is None else pd.Timestamp(now)
        if local.tzinfo is None:
            local = local.tz_localize('Asia/Shanghai')
        local = local.tz_convert('Asia/Shanghai')
        return local.weekday()<5 and 560 <= local.hour*60+local.minute < 910

    def start_background(self):
        """Explicit service startup; never called merely by importing/creating an app."""
        self.autonomous = True
        if self.supervisor and self.supervisor.is_alive():
            return
        self.stop_event.clear()
        self.supervisor = threading.Thread(target=self.supervise,daemon=True,name='margin-collector-supervisor')
        self.supervisor.start()

    def maintain(self):
        from .cache import prune
        if time.time()-self.last_prune >= 3600:
            prune(self.app)
            self.last_prune = time.time()
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            if not self.collection_window():
                self.state = 'scheduled'
            elif time.time() >= self.retry:
                self.state = 'connecting'
                self.thread = threading.Thread(target=self.run,daemon=True,name='margin-tqsdk')
                self.thread.start()

    def supervise(self):
        while not self.stop_event.is_set():
            try:
                self.maintain()
            except Exception as exc:
                self.error = 'Collector scheduling failed ('+type(exc).__name__+')'
                self.state = 'error'
            self.stop_event.wait(5)

    def stop(self):
        self.stop_event.set()

    def touch(self,product,token_hash):
        if self.autonomous:
            return  # Web requests read SQLite; the supervisor owns subscriptions.
        with self.lock:
            self.demands[(product,token_hash)] = time.time()+90
            if (not self.thread or not self.thread.is_alive()) and time.time() >= self.retry:
                self.state = 'connecting'
                self.thread = threading.Thread(target=self.run,daemon=True,name='margin-tqsdk')
                self.thread.start()

    def active(self):
        if self.stop_event.is_set():
            return set()
        if self.autonomous:
            return set(PRODUCTS) if self.collection_window() else set()
        now = time.time()
        with self.lock:
            demands = dict(self.demands)
        with connect(self.path) as db:
            admin = db.execute('SELECT password_hash FROM admin WHERE id=1').fetchone()
            tag = hashlib.sha256(admin[0].encode()).hexdigest()
            tokens = {r[0] for r in db.execute('SELECT token_hash FROM sessions WHERE authenticated=1 AND expires>? AND last_seen>? AND credential_tag=?',
                      (now,now-self.app.config['AUTH_IDLE_TIMEOUT'],tag))}
        return {product for (product,token),expires in demands.items() if expires > now and token in tokens}

    def lease(self):
        now = time.time()
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner,expires FROM tq_lease WHERE id=1').fetchone()
            if row and row['owner'] != self.owner and row['expires'] > now:
                return False
            db.execute('INSERT OR REPLACE INTO tq_lease VALUES(1,?,?)',(self.owner,now+180))
        return True

    def group(self,api,product):
        spec = PRODUCTS[product]
        uq = api.get_quote(spec['index'])
        main = api.get_quote(spec['future'])
        if not main.underlying_symbol:
            raise RuntimeError('The active futures contract has not been resolved')
        fq = api.get_quote(main.underlying_symbol)
        group = dict(uq=uq,main=main,fq=fq,options=[],last_save=0,last_minute=0,trade_day=uq.datetime[:10],
                     ib=api.get_kline_serial(spec['index'],60,data_length=8000),
                     fb=api.get_kline_serial(fq.instrument_id,60,data_length=8000))
        if spec['option']:
            symbols = list(api.query_options(spec['index'],expired=False))
            # Metadata is static; subscribing only the two bracketing terms limits live traffic.
            info = api.query_symbol_info(symbols)
            at = timestamp(uq.datetime) or time.time()
            expiry_col = 'expire_datetime'
            expiries = sorted(set(float(x) for x in info[expiry_col] if math.isfinite(float(x)) and x > at))
            before = [e for e in expiries if e <= at+30*86400]
            after = [e for e in expiries if e >= at+30*86400]
            if not before or not after:
                group['reason'] = 'No expirations bracket the 30-day target'
            else:
                wanted = {max(before),min(after)}
                selected = info[info[expiry_col].isin(wanted)]['instrument_id'].tolist()
                group['options'] = api.get_quote_list(selected)
                group['expiries'] = wanted
        return group

    def quotes(self,group):
        return [dict(symbol=q.instrument_id,strike=finite(q.strike_price),side=q.option_class,
                     expiry=finite(q.expire_datetime),bid=finite(q.bid_price1),ask=finite(q.ask_price1),
                     bid_volume=finite(q.bid_volume1),ask_volume=finite(q.ask_volume1),
                     quote_time=q.datetime,quote_epoch=timestamp(q.datetime)) for q in group['options']]

    def snapshot(self,product,rows,at,market_epoch=None):
        # Return the persisted minute before any variance calculation on reconnect.
        cached = tqstore.get_snapshot(self.path,product,iso(at))
        if cached is not None:
            return cached
        # Require a real trading-minute boundary. Missing data is never time-interpolated.
        local = pd.Timestamp(at,unit='s',tz='UTC').tz_convert('Asia/Shanghai')
        minute = local.hour*60+local.minute
        if local.weekday() >= 5 or not (571 <= minute <= 690 or 781 <= minute <= 900):
            return
        age = self.app.config['TQ_QUOTE_MAX_AGE']
        # A fresh index heartbeat confirms the subscription is still receiving
        # market data. An option quote may remain unchanged for longer than 10m.
        healthy = market_epoch is not None and 0 <= at-market_epoch <= 10
        if market_epoch is not None and not healthy:
            # Weekday scheduling is an upper bound, not an exchange calendar.
            # No fresh market heartbeat => no holiday/disconnected pseudo-minute.
            return
        eligible = []
        invalid = 0
        for row in rows:
            r = dict(row)
            quote_local=pd.Timestamp(r['quote_epoch'] or 0,unit='s',tz='UTC').tz_convert('Asia/Shanghai')
            same_session=quote_local.date()==local.date() and (quote_local.hour<12)==(local.hour<12)
            invalid_time=(not r['quote_epoch'] or r['quote_epoch'] > at or not same_session
                          or (market_epoch is not None and not healthy)
                          or (not healthy and at-r['quote_epoch'] > age))
            if invalid_time:
                r['bid'],r['ask'] = None,None
                invalid += 1
            eligible.append(r)
        try:
            result = calculate(eligible,at,self.app.config['MVIX_RATE'])
            result['quality'] = 'valid'
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            result = dict(value=None,quality='insufficient',reason=str(exc))
        result.update(quote_count=len(rows),unusable_timestamps=invalid,max_quote_age_seconds=None if healthy else age,
                      market_heartbeat_epoch=market_epoch,unchanged_quotes_allowed=healthy,
                      boundary=iso(at),sampling='last-known-before-boundary')
        if product in self.active():
            from .history import store_minutes
            evidence = [dict(r,timestamp=iso(at),quote_ns=int(r['quote_epoch']*1e9))
                        for r in eligible if r['quote_epoch'] and r['quote_epoch'] <= at]
            store_minutes(self.path,product,evidence,source='tqsdk_live_minute_asof')
            tqstore.save_snapshot(self.path,product,iso(at),result,eligible)

    def persist(self,product,group,initial=False):
        if product not in self.active():
            return
        from .cache import cutoff_for,settings
        cutoff = cutoff_for(settings(self.app)['months'])
        now = time.time()
        for quote,key in [(group['uq'],'ib'),(group['fq'],'fb')]:
            frame = group[key].copy()
            # Completed bars only; historical session-close bars can be retained.
            frame = frame[(frame.datetime > 0) & (frame.datetime/1e9+60 <= now)]
            if quote.datetime and timestamp(quote.datetime) >= now-120 and len(frame) and frame.index[-1] == group[key].index[-1]:
                frame = frame.iloc[:-1]
            cols = ['open','high','low','close','volume','open_oi','close_oi']
            frame = frame[np.isfinite(frame[cols]).all(axis=1)]
            frame = frame[(frame.low > 0) & (frame.low <= frame[['open','close']].min(axis=1)) &
                          (frame.high >= frame[['open','close']].max(axis=1)) & (frame.volume >= 0)]
            if not frame.empty:
                tqstore.save_bars(self.path,quote.instrument_id,frame if initial else frame.tail(10),cutoff)
        fq,uq = group['fq'],group['uq']
        status = dict(product=product,**PRODUCTS[product],contract=fq.instrument_id,
                      index_price=finite(uq.last_price),future_price=finite(fq.last_price),
                      open_interest=finite(fq.open_interest),volume=finite(fq.volume),
                      quote_time=fq.datetime,synced_at=iso(now),option_count=len(group['options']))
        with connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO tq_status VALUES(?,?)',(product,json.dumps(status,allow_nan=False)))
        group['last_save'] = now

    def run(self):
        groups = {}
        try:
            if not self.active():
                return
            if not self.lease():
                self.state = 'shared'
                self.retry = time.time()+30
                return
            with session(self.app.config['TQ_AUTH_FILE']) as api:
                self.error = None
                while self.active():
                    active = self.active()
                    if not self.lease():
                        break
                    for product in active:
                        if product not in groups or groups[product]['uq'].datetime[:10] != groups[product]['trade_day']:
                            groups[product] = self.group(api,product)
                            deadline = time.time()+20
                            while time.time()<deadline and self.active() and not all((groups[product][key].datetime>0).sum()>1 for key in ['ib','fb']):
                                api.wait_update(deadline=min(deadline,time.time()+1))
                            self.persist(product,groups[product],True)
                            # At startup only reconstruct the latest *session close* snapshot,
                            # never any intraday history from present-day quotes.
                            g = groups[product]
                            at = timestamp(g['uq'].datetime)
                            local = pd.Timestamp(at,unit='s',tz='UTC').tz_convert('Asia/Shanghai')
                            if PRODUCTS[product]['option'] and local.hour == 14 and local.minute == 59 and at+60 < time.time():
                                self.snapshot(product,self.quotes(g),math.ceil(at/60)*60)
                    # Copy quote state before waiting; the minute boundary must not see later updates.
                    previous = {p:self.quotes(g) for p,g in groups.items() if p in active}
                    heartbeats = {p:timestamp(g['uq'].datetime) for p,g in groups.items() if p in active}
                    boundary = (int(time.time())//60+1)*60
                    api.wait_update(deadline=min(time.time()+1,boundary))
                    now = time.time()
                    for product,g in groups.items():
                        if product not in self.active():
                            continue
                        if now >= boundary and g['last_minute'] < boundary:
                            if PRODUCTS[product]['option']:
                                self.snapshot(product,previous[product],boundary,market_epoch=heartbeats[product])
                            g['last_minute'] = boundary
                        if now-g['last_save'] > 5:
                            if g['main'].underlying_symbol != g['fq'].instrument_id:
                                g['fq'] = api.get_quote(g['main'].underlying_symbol)
                                g['fb'] = api.get_kline_serial(g['fq'].instrument_id,60,data_length=8000)
                                self.persist(product,g,True)
                            else:
                                self.persist(product,g)
                    self.state = 'streaming'
        except Exception as exc:
            # Provider exceptions may contain auth payloads. Expose only class, never message.
            self.error = 'TqSdk connection or subscription failed ('+type(exc).__name__+'); check credentials, permissions, and connectivity before retrying'
            self.state = 'error'
            self.retry = time.time()+60
        finally:
            with connect(self.path) as db:
                db.execute('DELETE FROM tq_lease WHERE owner=?',(self.owner,))
            if self.state not in ('error','shared'):
                self.state = 'idle'
