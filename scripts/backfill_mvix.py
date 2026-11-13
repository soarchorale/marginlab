"""Explicit administrator CLI: backfill available recent historical quote MVIX.

No orders, no AKShare, no historical close-price substitution. Credentials stay
local. Market artifacts in SQLite permit offline reconstruction and resuming.
Full dated Tick download requires a separate TqSdk entitlement (see access probe).
"""
import argparse
import gc
import json
import logging
import os
from pathlib import Path
import sys
import time
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from marginlab.tqfeed import session,PRODUCTS,timestamp
from marginlab.storage import connect
from marginlab.tqstore import initialize as init_market,save_bars
from marginlab.history import initialize,minute_quotes,store_minutes,reconstruct,save_job,ASOF_SOURCE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--products',nargs='+',choices=['50','300','1000'],default=['50','300','1000'])
    parser.add_argument('--database',default=str(ROOT/'instance/margin.sqlite3'))
    parser.add_argument('--auth-file',default=str(ROOT/'tqauth.json'))
    parser.add_argument('--batch-size',type=int,default=8)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--rate',type=float,default=float(os.environ.get('MVIX_RATE','0.02')))
    args=parser.parse_args()
    if not 1<=args.batch_size<=16:parser.error('batch-size must be 1–16')
    if not -.1<=args.rate<=.5:parser.error('rate must be finite and between -0.1 and 0.5')
    logging.disable(logging.CRITICAL)
    path=args.database;init_market(path);initialize(path)
    manifests=Path(path).resolve().parent/'tqsdk-verification/history';manifests.mkdir(parents=True,exist_ok=True)
    for product in args.products:
        spec=PRODUCTS[product];manifest=manifests/f'{product}_manifest.json'
        if args.offline:
            saved=json.loads(manifest.read_text());universe=saved['universe'];start=pd.Timestamp(saved['start']);end=pd.Timestamp(saved['end'])
        else:
            print('Discovering',product,flush=True)
            with session(args.auth_file) as api:
                uq=api.get_quote(spec['index']);at=timestamp(uq.datetime)
                info=api.query_symbol_info(list(api.query_options(spec['index'],expired=False)))
                target=at+30*86400
                exps=sorted(set(float(v) for v in info.expire_datetime if v>at))
                wanted={max(e for e in exps if e<=target),min(e for e in exps if e>=target)}
                universe=[dict(symbol=r.instrument_id,expiry=float(r.expire_datetime),strike=float(r.strike_price),side=r.option_class)
                          for r in info.itertuples() if r.expire_datetime in wanted]
                end=pd.Timestamp(at,unit='s',tz='UTC').tz_convert('Asia/Shanghai').ceil('min')
                # Recent buffers cover different spans; collect up to configured retention,
                # while expiry bracketing and complete-chain tests define valid coverage.
                with connect(path) as db:
                    setting=db.execute("SELECT value FROM settings WHERE key='history_months'").fetchone()
                months=int(setting[0]) if setting else 3
                start=end.normalize()-pd.DateOffset(months=months)
                for symbol in [spec['index'],api.get_quote(spec['future']).underlying_symbol]:
                    bars=api.get_kline_serial(symbol,60,data_length=8000)
                    deadline=time.time()+30
                    while time.time()<deadline:
                        api.wait_update(deadline=min(deadline,time.time()+1))
                        if (bars.datetime>0).sum()>100:break
                    valid=bars[(bars.datetime>0)&(bars.datetime/1e9+60<=time.time())].copy()
                    save_bars(path,symbol,valid,start.tz_localize(None).isoformat())
            manifest.write_text(json.dumps(dict(product=product,start=start.isoformat(),end=end.isoformat(),universe=universe),indent=2))
            job=dict(state='running',mode='recent_tick_10000',requested_start=start.isoformat(),requested_end=end.isoformat(),total_contracts=len(universe),completed_contracts=0,
                     limitation='Dated historical Tick downloads are unavailable for this account; backfill uses the common coverage of the latest 10,000 ticks.')
            save_job(path,product,job)
            for offset in range(0,len(universe),args.batch_size):
                batch=universe[offset:offset+args.batch_size]
                # Immutable historical end point: completed batches can be resumed without network.
                with connect(path) as db:
                    have={r[0] for r in db.execute('SELECT DISTINCT symbol FROM tq_option_minutes WHERE product=? AND timestamp=? AND source=?',(product,end.tz_localize(None).isoformat(),ASOF_SOURCE))}
                pending=[r for r in batch if r['symbol'] not in have]
                if pending:
                    with session(args.auth_file) as api:
                        frames={r['symbol']:api.get_tick_serial(r['symbol'],data_length=10000) for r in pending}
                        deadline=time.time()+45
                        while time.time()<deadline:
                            api.wait_update(deadline=min(deadline,time.time()+1))
                            if all(api.is_serial_ready(f) for f in frames.values()):break
                        for meta in pending:
                            if not api.is_serial_ready(frames[meta['symbol']]):
                                raise RuntimeError('Historical Tick serial is not ready')
                            frame=frames[meta['symbol']].copy()
                            # Min timestamp is a coverage bound, never a license to backfill earlier.
                            valid=frame[frame.datetime>0]
                            if valid.empty:continue
                            first=pd.Timestamp(int(valid.datetime.min()),unit='ns',tz='UTC').tz_convert('Asia/Shanghai')
                            sampled=minute_quotes(valid,meta,max(start,first),end,max_age=None)
                            store_minutes(path,product,sampled,source=ASOF_SOURCE)
                    del frames
                    gc.collect()
                job['completed_contracts']=min(offset+len(batch),len(universe));save_job(path,product,job)
                print(product,job['completed_contracts'],'/',len(universe),flush=True)
        result=reconstruct(path,product,universe,start,end,rate=args.rate)
        job=dict(state='complete',mode='recent_tick_10000',result=result,
                 limitation='Dated historical Tick downloads are not authorized; only minutes covered by all required contracts are included.')
        save_job(path,product,job)
        print('RESULT',product,json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Backfill failed:',type(exc).__name__,flush=True)
        raise SystemExit(1)
