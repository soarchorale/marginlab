"""Persisted collector progress, distinct from HTTP availability and quote validity."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from .storage import connect
from .coverage import phase


def publish(feed):
    from .tqfeed import PRODUCTS
    now=time.time()
    with feed.progress_lock:progress={p:dict(v) for p,v in feed.progress.items()}
    with connect(feed.path) as db:
        statuses={r['product']:json.loads(r['payload']) for r in db.execute('SELECT * FROM tq_status')}
        for product,spec in PRODUCTS.items():
            record=progress.get(product,{})
            status=statuses.get(product,{})
            def latest(table,where,args):
                return db.execute('SELECT MAX(timestamp) FROM '+table+' WHERE '+where,args).fetchone()[0]
            snapshot=db.execute('SELECT timestamp,quality,details FROM tq_snapshots WHERE product=? ORDER BY timestamp DESC LIMIT 1',(product,)).fetchone()
            record['latest_sample']=dict(timestamp=snapshot['timestamp'],quality=snapshot['quality'],reason=json.loads(snapshot['details']).get('reason')) if snapshot else None
            record.update(checked_at=now,worker_alive=bool(feed.thread and feed.thread.is_alive()),
                loop_at=feed.loop_at,worker_started_at=feed.worker_started_at,state=feed.state,error=feed.error,
                consecutive_failures=feed.failures,
                index_bar=latest('tq_bars','symbol=?',(spec['index'],)),
                future_bar=latest('tq_bars','symbol=?',(status.get('contract',''),)),
                option_minute=latest('tq_option_minutes','product=?',(product,)),
                mvix_valid=latest('tq_snapshots',"product=? AND quality='valid'",(product,)))
            db.execute('INSERT OR REPLACE INTO tq_health VALUES(?,?)',(product,json.dumps(record,allow_nan=False)))

    from .logging_setup import event
    previous=getattr(feed,'logged_health',{})
    for product,record in read_health(feed.path,now).items():
        sample=record.get('latest_sample') or {}
        key=(record['health_state'],record['phase'],sample.get('quality'),sample.get('reason'))
        old=previous.get(product)
        if not old or old[0]!=key or now-old[1]>=300:
            level=30 if record['health_state'] in ('monitor_stale','worker_stalled','connection_error','quotes_delayed','write_lag','last_error') or sample.get('quality')=='insufficient' else 20
            # Provider exception messages must never enter application logs.
            record.pop('error',None)
            event('collector_health',level,product=product,**record)
            previous[product]=(key,now)
    feed.logged_health=previous


def read_health(path,now=None):
    now=now or time.time();stage=phase(datetime.fromtimestamp(now,ZoneInfo('Asia/Shanghai')))
    with connect(path) as db:rows=db.execute('SELECT * FROM tq_health').fetchall()
    result={}
    for row in rows:
        record=json.loads(row['payload']);state='waiting'
        if now-record['checked_at']>45:state='monitor_stale'
        elif record.get('state')=='replica':state='replica'
        elif stage=='continuous':
            since_open=datetime.fromtimestamp(now,ZoneInfo('Asia/Shanghai'))
            minute=since_open.hour*60+since_open.minute
            grace=minute in (570,571,780,781)
            if record.get('worker_alive') and now-(record.get('loop_at') or record.get('worker_started_at') or 0)>90:state='worker_stalled'
            elif record.get('state')=='error':state='connection_error'
            elif not grace and now-record.get('market_epoch',0)>90:state='quotes_delayed'
            elif not grace and record.get('future_bar'):
                epoch=datetime.fromisoformat(record['future_bar']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()+60
                state='write_lag' if now-epoch>120 else 'healthy'
            else:state='warming_up'
        elif record.get('error'):state='last_error'
        record.update(health_state=state,phase=stage,calendar='weekday_assumption_not_exchange_calendar')
        result[row['product']]=record
    return result
