"""Lightweight time/coverage audit. No pricing validation or automatic repairs."""
import json
import time
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .storage import connect

ZONE=ZoneInfo('Asia/Shanghai')


def phase(now=None):
    now=now or datetime.now(ZONE)
    if now.tzinfo is None:now=now.replace(tzinfo=ZONE)
    now=now.astimezone(ZONE);m=now.hour*60+now.minute
    if now.weekday()>=5:return 'weekend'
    if m<570:return 'preopen'
    if 570<=m<690 or 780<=m<897:return 'continuous'
    if 690<=m<780:return 'lunch'
    if 897<=m<900:return 'closing_auction'
    return 'closed'


def audit_day(path,day,now=None,asof=None):
    from .tqfeed import PRODUCTS
    now=now or datetime.now(ZONE)
    if now.tzinfo is None:now=now.replace(tzinfo=ZONE)
    now=now.astimezone(ZONE)
    horizon=min(now,asof.astimezone(ZONE)) if asof else now
    base=datetime.fromisoformat(day).replace(tzinfo=ZONE)
    expected=[(base+timedelta(minutes=m)).replace(tzinfo=None).isoformat() for m in list(range(571,691))+list(range(781,901))
              if base.weekday()<5 and base+timedelta(minutes=m)<=horizon-timedelta(seconds=30)]
    allowed=set((base+timedelta(minutes=m)).replace(tzinfo=None).isoformat() for m in list(range(571,691))+list(range(781,901)))
    expected_set=set(expected)
    begin=day+'T00:00:00';end=(base+timedelta(days=1)).date().isoformat()+'T00:00:00'
    reports=[]
    with connect(path) as db:
        db.execute('BEGIN')
        version=db.execute('SELECT version FROM tq_dirty_days WHERE day=?',(day,)).fetchone()
        version=version[0] if version else 0
        for product,spec in PRODUCTS.items():
            bars=db.execute('SELECT symbol,timestamp FROM tq_bars WHERE timestamp>=? AND timestamp<? AND (symbol=? OR symbol LIKE ?)',
                            (begin,end,spec['index'],'CFFEX.'+spec['future'].split('.')[-1]+'%')).fetchall()
            index=set();future=set();contracts={};unexpected=[]
            for row in bars:
                try:stamp=(datetime.fromisoformat(row['timestamp'])+timedelta(minutes=1)).isoformat()
                except ValueError:unexpected.append(row['timestamp']);continue
                if stamp not in allowed:unexpected.append(row['timestamp'])
                if row['symbol']==spec['index']:index.add(stamp)
                else:
                    future.add(stamp);contracts[row['symbol']]=contracts.get(row['symbol'],0)+1
            snaps={r['timestamp']:r['quality'] for r in db.execute('SELECT timestamp,quality FROM tq_snapshots WHERE product=? AND timestamp>=? AND timestamp<?',(product,begin,end))}
            quote_counts={r[0]:r[1] for r in db.execute('SELECT timestamp,count(*) FROM tq_option_minutes WHERE product=? AND timestamp>=? AND timestamp<? GROUP BY timestamp',(product,begin,end))}
            observed=bool(bars)
            valid={t for t,q in snaps.items() if q=='valid'}
            refs={t for t,q in snaps.items() if q=='reference'}
            auction={t for t,q in snaps.items() if q=='auction'}
            invalid={t for t,q in snaps.items() if q not in ('valid','reference','auction')}
            carry=[day+'T'+t+':00' for t in ('14:58','14:59','15:00') if day+'T14:57:00' in valid and day+'T'+t+':00' in expected_set and snaps.get(day+'T'+t+':00') not in ('valid','reference')]
            report=dict(product=product,day=day,checked_at=now.isoformat(),version=version,
                source_asof=asof.isoformat() if asof else None,calendar='weekday_assumption_not_exchange_calendar',trading_day_evidence='observed' if observed else 'unconfirmed',
                expected=len(expected),final=horizon>=base+timedelta(hours=15,minutes=10),
                index_count=len(expected_set&index),future_count=len(expected_set&future),contracts=contracts,
                index_missing=sorted(expected_set-index),future_missing=sorted(expected_set-future),
                unexpected_bars=sorted(set(unexpected)),options_applicable=bool(spec['option']))
            if spec['option']:
                report.update(option_minutes=len(expected_set&quote_counts.keys()),quote_rows=sum(quote_counts.values()),
                    quote_missing=sorted(expected_set-quote_counts.keys()),mvix_valid=len(expected_set&valid),
                    mvix_reference=len(expected_set&refs),mvix_invalid=len(expected_set&invalid),auction_recorded=len(expected_set&auction),
                    auction_display_carry=len(carry),mvix_missing=sorted(expected_set-snaps.keys()),
                    unexpected_quotes=sorted(set(quote_counts)-allowed),unexpected_snapshots=sorted(set(snaps)-allowed))
            reports.append(report)
    # A newer writer cannot be silently certified by a report on an older snapshot.
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT version FROM tq_dirty_days WHERE day=?',(day,)).fetchone()
        current=current[0] if current else 0
        for report in reports:
            report['stale']=current!=version
            db.execute('INSERT OR REPLACE INTO tq_coverage VALUES(?,?,?,?,?)',
                       (report['product'],day,version,now.timestamp(),json.dumps(report,allow_nan=False)))
    from .logging_setup import event
    for report in reports:event('coverage_audit', **report)
    return reports


def latest_report(path,product):
    with connect(path) as db:
        row=db.execute('SELECT c.payload,c.version,COALESCE(d.version,0) current_version FROM tq_coverage c LEFT JOIN tq_dirty_days d ON d.day=c.day WHERE product=? ORDER BY c.day DESC LIMIT 1',(product,)).fetchone()
    if not row:return None
    data=json.loads(row['payload']);data['stale']=row['version']!=row['current_version'];return data


class Maintenance:
    def __init__(self,app):
        self.app=app;self.path=app.config['DATABASE'];self.owner=uuid.uuid4().hex
        self.last_check=0;self.last_cleanup=0;self.error=None;self.last_phase=None

    def tick(self,now=None):
        started=time.monotonic()
        from .cache import cutoff_for,settings
        now=now or datetime.now(ZONE);epoch=now.timestamp()
        final_transition=phase(now)=='closed' and now.hour*60+now.minute>=910 and self.last_phase!='final'
        if epoch-self.last_check<300 and not final_transition:return
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT owner,expires FROM tq_maintenance_lease WHERE id=1').fetchone()
            if row and row['owner']!=self.owner and row['expires']>epoch:return
            db.execute('INSERT OR REPLACE INTO tq_maintenance_lease VALUES(1,?,?)',(self.owner,epoch+300))
        try:
            self.app.extensions['session_activity'].cleanup(epoch,self.app.config['AUTH_IDLE_TIMEOUT'])
            self.last_cleanup=epoch
            cutoff=cutoff_for(settings(self.app)['months'])
            today=now.date().isoformat()
            with connect(self.path) as db:
                # Existing deployments have no dirty-day entries until their first new write.
                recent=[r[0] for r in db.execute('SELECT DISTINCT substr(timestamp,1,10) day FROM tq_bars WHERE timestamp>=? ORDER BY day DESC LIMIT 7',((now-timedelta(days=21)).date().isoformat(),))]
                dirty=[r[0] for r in db.execute('''SELECT d.day FROM tq_dirty_days d
                    WHERE d.day>=? AND d.day<=?
                    AND (SELECT count(*) FROM tq_coverage c WHERE c.day=d.day AND c.version=d.version)<4
                    ORDER BY d.day LIMIT 2''',(cutoff,today))]
                recent=[day for day in recent if db.execute('SELECT count(*) FROM tq_coverage WHERE day=?',(day,)).fetchone()[0]<4]
            days=list(dict.fromkeys(([today] if now.weekday()<5 else [])+dirty+recent))[:4]
            asof=None
            if self.app.extensions['tq_feed'].read_only:
                with connect(self.path) as db:
                    sync=db.execute("SELECT value FROM settings WHERE key='last_remote_sync'").fetchone()
                if sync:asof=datetime.fromisoformat(json.loads(sync[0])['exported_at'])
            for day in days:
                if cutoff<=day<=today:audit_day(self.path,day,now,asof=asof)
            self.last_check=epoch;self.last_phase='final' if phase(now)=='closed' and now.hour*60+now.minute>=910 else phase(now);self.error=None
        except Exception as exc:
            from .logging_setup import event,sqlite_fields
            event('maintenance_failed', 40, error_type=type(exc).__name__,duration_ms=round((time.monotonic()-started)*1000),**sqlite_fields(exc))
            self.error='Maintenance failed ('+type(exc).__name__+')'
            self.last_check=epoch
        finally:
            with connect(self.path) as db:db.execute('DELETE FROM tq_maintenance_lease WHERE owner=?',(self.owner,))
