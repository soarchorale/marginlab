"""Run the standalone minute signal monitor. Observation is the default."""
import argparse
from contextlib import ExitStack, closing
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from marginlab.cache import cutoff_for
from marginlab.coverage import ZONE
from marginlab.feishu import dispatch_one, load_webhook
from marginlab.logging_setup import DailyLogHandler, EVENT_LOGGER, event
from marginlab.signals import advance, initialize, open_market, open_state
from marginlab.tqfeed import PRODUCTS


def sender(state, path, stop):
    with closing(open_state(state)) as db:
        while not stop.is_set():
            try:
                worked = dispatch_one(db, path)
            except sqlite3.OperationalError as exc:
                event('signal_delivery_database_error', logging.ERROR, error_type=type(exc).__name__)
                worked = False
            stop.wait(0.65 if worked else 0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', type=Path, default=ROOT/'instance/margin.sqlite3')
    parser.add_argument('--state', type=Path, default=ROOT/'instance/signals.sqlite3')
    parser.add_argument('--send', action='store_true', help='Enable production webhook delivery')
    parser.add_argument('--once', action='store_true', help='Run one observation pass and exit')
    parser.add_argument('--preview', action='store_true', help='Print the latest recorded card after one pass')
    args = parser.parse_args()
    if args.send and (args.once or args.preview):
        parser.error('Single-pass and preview modes only support observation')
    if args.market.resolve() == args.state.resolve() or (
            args.state.exists() and args.market.exists() and args.state.samefile(args.market)):
        parser.error('Market and signal databases must be different files')
    os.umask(0o077)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    stop = Event()
    with ExitStack() as stack:
        market = stack.enter_context(closing(open_market(args.market)))
        path = None
        if args.send:
            replica = market.execute("SELECT value FROM settings WHERE key='market_read_only'").fetchone()
            if os.environ.get('MARGIN_MARKET_READ_ONLY') == '1' or (replica and replica[0] == '1'):
                parser.error('Delivery is disabled on a market replica')
            try:
                path = load_webhook(ROOT/'feishu.json')
            except ValueError as exc:
                parser.error(str(exc))
        lock = stack.enter_context(open(str(args.state)+'.lock', 'a'))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another signal monitor owns this state database')
        db = stack.enter_context(closing(open_state(args.state)))
        initialize(db)
        handler = DailyLogHandler(args.state.parent/'signal-logs', int(os.getenv('MARGIN_LOG_RETENTION_DAYS', '30')))
        EVENT_LOGGER.addHandler(handler)
        EVENT_LOGGER.setLevel(logging.INFO)
        stack.callback(handler.close)
        stack.callback(EVENT_LOGGER.removeHandler, handler)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        live_since = time.time() if path else None
        thread = None
        if path:
            thread = Thread(target=sender, args=(args.state, path, stop), name='feishu-sender')
            thread.start()
            stack.callback(thread.join)
            stack.callback(stop.set)
        event('signal_monitor_started', delivery=bool(path))
        last_pruned = None
        while not stop.is_set():
            now = datetime.now(ZONE)
            for product, spec in PRODUCTS.items():
                if spec['option'] and not stop.is_set():
                    try:
                        advance(market, db, product, now, live_since)
                    except sqlite3.OperationalError as exc:
                        event('signal_read_failed', logging.ERROR, product=product, error_type=type(exc).__name__)
                        if args.once or args.preview:
                            raise
            if last_pruned != now.date():
                # Keep the entire current anchor month and rolling warmup.
                cutoff = cutoff_for(1)
                with db:
                    db.execute('DELETE FROM observations WHERE endpoint<?', (cutoff,))
                    db.execute("DELETE FROM outbox WHERE endpoint<? AND status NOT IN ('pending','sending')", (cutoff,))
                last_pruned = now.date()
            if args.preview:
                row = db.execute('SELECT payload FROM outbox ORDER BY endpoint DESC,id LIMIT 1').fetchone()
                print(json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2) if row else 'No signal in retained observations.')
            if args.once or args.preview:
                break
            if thread and not thread.is_alive():
                raise RuntimeError('Signal delivery thread stopped')
            stop.wait(1)
        event('signal_monitor_stopped')


if __name__ == '__main__':
    main()
