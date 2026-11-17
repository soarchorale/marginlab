"""Replay saved MVIX evidence; only failed, time-verified snapshots may be repaired."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
from .mvix import calculate, METHOD

ZONE = ZoneInfo('Asia/Shanghai')


def verified_evidence(stamp, details, rows):
    local = datetime.fromisoformat(stamp).replace(tzinfo=ZONE)
    at = local.timestamp()
    minute = local.hour*60+local.minute
    if local.weekday() >= 5 or not (571 <= minute <= 690 or 781 <= minute <= 897):
        raise ValueError('Outside continuous-trading minute endpoints')
    if details.get('unusable_timestamps') != 0 or details.get('evidence_verified') is False:
        raise ValueError('Unverified or masked quote timestamps')
    if not rows or details.get('quote_count') != len(rows):
        raise ValueError('Incomplete saved quote evidence')
    heartbeat = details.get('market_heartbeat_epoch')
    healthy = heartbeat is not None and 0 <= at-heartbeat <= 10
    if heartbeat is not None and not healthy:
        raise ValueError('Stale market heartbeat')
    age_limit = details.get('max_quote_age_seconds')
    if not healthy and (age_limit is None or not math.isfinite(age_limit) or age_limit <= 0):
        raise ValueError('No bounded quote-age evidence')
    for row in rows:
        epoch = row.get('quote_epoch') or (row.get('quote_ns',0)/1e9)
        if not epoch or not math.isfinite(epoch) or epoch > at:
            raise ValueError('Missing or future quote timestamp')
        quoted = datetime.fromtimestamp(epoch,ZONE)
        if quoted.date()!=local.date() or (quoted.hour<12)!=(local.hour<12):
            raise ValueError('Cross-session quote timestamp')
        if not healthy and at-epoch > age_limit:
            raise ValueError('Quote age exceeds the recorded bound')
    return at


def replay(path, legacy_rate=None):
    summary=defaultdict(Counter);changes=[];plans=[];rejections=Counter()
    created=datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        for saved in db.execute('SELECT * FROM tq_snapshots ORDER BY product,timestamp'):
            row=dict(saved);product=row['product'];quality=row['quality'];stats=summary[product]
            stats['total']+=1
            if quality not in ('valid','insufficient'):
                stats['preserved_'+quality]+=1;continue
            details=json.loads(row['details']);quotes=json.loads(row['quotes'])
            try:
                at=datetime.fromisoformat(row['timestamp']).replace(tzinfo=ZONE).timestamp()
                if quality=='insufficient':at=verified_evidence(row['timestamp'],details,quotes)
                rate=details.get('rate',legacy_rate)
                if rate is None:raise ValueError('Missing recorded rate; supply an explicit legacy rate')
                result=calculate(quotes,at,rate)
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                stats['valid_replay_failed' if quality=='valid' else 'unresolved']+=1
                rejections[str(exc)]+=1;continue
            if quality=='valid':
                if math.isclose(result['value'],row['value'],rel_tol=1e-10,abs_tol=1e-10):
                    stats['valid_unchanged']+=1
                else:
                    stats['valid_changed_preserved']+=1
                    changes.append(dict(product=product,timestamp=row['timestamp'],old=row['value'],new=result['value'],delta=result['value']-row['value']))
                continue
            # Retain sampling evidence and the former failure, without changing raw quotes.
            updated=dict(details);updated.pop('reason',None);updated.update(result,quality='valid')
            updated['recovery']=dict(kind='saved-quote-null-strike-replay',method=METHOD,created_at=created,
                previous=dict(value=row['value'],quality=quality,details=details),
                raw_quotes_sha256=hashlib.sha256(row['quotes'].encode()).hexdigest(),
                rate_basis='recorded' if 'rate' in details else 'explicit_legacy_assumption')
            plans.append((row,updated));stats['recoverable']+=1
    report=dict(method=METHOD,created_at=created,legacy_rate=legacy_rate,
                products={k:dict(v) for k,v in summary.items()},rejection_reasons=dict(rejections),
                valid_changes=changes,recovered=[dict(product=r['product'],timestamp=r['timestamp'],value=d['value'],
                    excluded_near=d['near']['excluded_strikes'],excluded_far=d['far']['excluded_strikes']) for r,d in plans])
    return report,plans


def apply_repairs(path,plans,backup):
    path=Path(path).resolve();backup=Path(backup).resolve()
    if path==backup:raise ValueError('Backup must differ from the live database')
    with sqlite3.connect(path,timeout=30) as db:
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(backup) as saved:db.backup(saved)
        db.execute('BEGIN IMMEDIATE')
        for original,result in plans:
            current=db.execute('SELECT value,quality,details,quotes FROM tq_snapshots WHERE product=? AND timestamp=?',
                               (original['product'],original['timestamp'])).fetchone()
            expected=tuple(original[k] for k in ('value','quality','details','quotes'))
            if current!=expected or original['quality']!='insufficient':
                raise RuntimeError('Snapshot changed since replay; no repairs committed')
            db.execute('UPDATE tq_snapshots SET value=?,quality=?,details=? WHERE product=? AND timestamp=?',
                (result['value'],'valid',json.dumps(result,allow_nan=False),original['product'],original['timestamp']))
        db.commit()
    return len(plans)
