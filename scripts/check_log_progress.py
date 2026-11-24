"""Compare the supervisor heartbeat with its daily log from outside the service."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo


def recent_lines(path):
    """Read newest complete records first, including lines crossing a block boundary."""
    with path.open('rb') as log:
        log.seek(0, 2)
        position = log.tell()
        remainder = b''
        while position:
            size = min(position, 64 * 1024)
            position -= size
            log.seek(position)
            lines = (log.read(size) + remainder).split(b'\n')
            remainder = lines[0]
            yield from reversed(lines[1:])
        yield remainder


def inspect(database, directory, now):
    products = {'50', '300', '500', '1000'}
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        checked = {p: json.loads(payload)['checked_at'] for p, payload in db.execute(
            'SELECT product, payload FROM tq_health') if p in products}
    heartbeat_age = round(now - min(checked.values())) if checked else None
    latest = {}
    # Include yesterday when the lookback spans midnight in Shanghai.
    for day in sorted({datetime.fromtimestamp(t, ZoneInfo('Asia/Shanghai')).date()
                       for t in (now, now - 900)}, reverse=True):
        log = Path(directory) / f'marginlab-{day}.log'
        if not log.exists():
            continue
        for line in recent_lines(log):
            try:
                row = json.loads(line)
                stamp = datetime.fromisoformat(row['time']).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if stamp < now - 900:
                break
            if row.get('event') == 'collector_health':
                product = row.get('details', {}).get('product')
                if product in products:
                    latest.setdefault(product, stamp)
            if latest.keys() == products:
                break
    if checked.keys() != products or heartbeat_age > 60:
        state = 'heartbeat_stale'
    elif latest.keys() != products:
        state = 'log_stale'
    else:
        state = 'ok'
    return dict(state=state, heartbeat_age_seconds=heartbeat_age,
                missing_heartbeats=sorted(products - checked.keys()),
                missing_recent_health_logs=sorted(products - latest.keys()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path)
    args = parser.parse_args()
    result = inspect(args.database, args.log_dir or args.database.parent / 'logs', time.time())
    print(json.dumps(result))
    raise SystemExit(result['state'] != 'ok')
