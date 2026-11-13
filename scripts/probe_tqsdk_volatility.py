"""Read-only TqSdk volatility verification; writes market artifacts, never orders.

Run with the project interpreter and --auth-file tqauth.json.
Uses completed 1-minute bars; SDK defaults are measured, not endorsed for MVIX.
"""
import argparse
import json
import logging
import math
from pathlib import Path
import signal
import time

import numpy as np
import pandas as pd
from tqsdk import tafunc, ta, __version__
from probe_tqsdk import market_api, PAIRS


def finite(value):
    return float(value) if np.isfinite(value) else None


def summary(value):
    values = np.asarray(value, dtype=float)
    return dict(shape=list(values.shape), finite=int(np.isfinite(values).sum()),
                missing=int((~np.isfinite(values)).sum()), last=finite(values.reshape(-1)[-1]))


def completed(frame):
    # Conservatively omit the final SDK bar even outside trading hours.
    result = frame.iloc[:-1].copy()
    return result[result.datetime > 0].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auth-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'instance/tqsdk-verification/volatility')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    credentials = json.loads(args.auth_file.read_text())
    report = dict(sdk=__version__, captured_at=pd.Timestamp.now(tz='Asia/Shanghai').isoformat(),
                  rate_assumption=0.02, period_seconds=60, groups={})
    args.output.mkdir(parents=True, exist_ok=True)
    def save():
        (args.output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    def timeout(*_):
        raise TimeoutError('probe deadline')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(240)
    try:
        with market_api(credentials['username'], credentials['password']) as api:
            for name in ['50', '300', '1000']:
                print('Checking', name, flush=True)
                underlying, continuous = PAIRS[name]
                uq = api.get_quote(underlying)
                fq = api.get_quote(api.get_quote(continuous).underlying_symbol)
                calls = api.query_options(underlying, option_class='CALL', expired=False)
                quotes = api.get_quote_list(list(calls))
                eligible = [q for q in quotes if q.expire_datetime > time.time()+7*86400]
                expiry = min(q.expire_datetime for q in eligible)
                selected = sorted((q for q in eligible if q.expire_datetime == expiry),
                                  key=lambda q: abs(q.strike_price-uq.last_price))[:3]
                selected.sort(key=lambda q: q.strike_price)
                q = min(selected, key=lambda q: abs(q.strike_price-uq.last_price))
                index_bars = api.get_kline_serial(underlying, 60, data_length=241)
                future_bars = api.get_kline_serial(fq.instrument_id, 60, data_length=241)
                pair = api.get_kline_serial([q.instrument_id, underlying], 60, data_length=121)
                multi = api.get_kline_serial([s.instrument_id for s in selected]+[underlying],60,data_length=121)
                deadline = time.time()+30
                while time.time() < deadline:
                    api.wait_update(deadline=deadline)
                    if all((f.datetime > 0).sum() >= 100 for f in [index_bars,future_bars,pair,multi]):
                        break
                ib, fb, pb, mb = map(completed, [index_bars,future_bars,pair,multi])
                if min(map(len,[ib,fb,pb,mb])) < 20:
                    raise ValueError('Insufficient valid bars')
                hv = tafunc.get_his_volatility(ib, uq)
                f_hv = tafunc.get_his_volatility(fb, fq)
                iv = ta.OPTION_IMPV(pb,q,r=0.02)
                t = tafunc.get_t(pb,q.expire_datetime)
                raw = tafunc.get_impv(pb.close1,pb.close,q.strike_price,0.02,0.3,t,q.option_class)
                prices = tafunc.get_bs_price(pb.close1,q.strike_price,0.02,raw,t,q.option_class)
                curve = ta.VOLATILITY_CURVE(mb,{s.instrument_id:s for s in selected},underlying,r=0.02)
                # Test the SDK time interpolation with one deliberately missing quote.
                missing = mb.copy()
                middle = len(missing)//2
                missing.loc[middle,'close'] = np.nan
                full = ta.VOLATILITY_CURVE(missing,{s.instrument_id:s for s in selected},underlying,r=0.02)
                prefix = ta.VOLATILITY_CURVE(missing.iloc[:middle+1].copy(),{s.instrument_id:s for s in selected},underlying,r=0.02)
                strike = selected[0].strike_price
                result = dict(underlying=underlying,future=fq.instrument_id,option=q.instrument_id,
                    strikes=[s.strike_price for s in selected],expiry=q.expire_datetime,
                    start=str(pd.to_datetime(pb.datetime.iloc[0],unit='ns',utc=True).tz_convert('Asia/Shanghai')),
                    end=str(pd.to_datetime(pb.datetime.iloc[-1],unit='ns',utc=True).tz_convert('Asia/Shanghai')),
                    underlying_hv=finite(hv),future_hv=finite(f_hv),hv_bars=len(ib),trading_time=dict(uq.trading_time),
                    option_impv=summary(iv),get_impv=summary(raw),
                    iv_max_abs_difference=finite(np.nanmax(np.abs(iv.impv.to_numpy()-np.asarray(raw)))),
                    bs_reprice_max_abs_error=finite(np.nanmax(np.abs(np.asarray(prices)-pb.close.to_numpy()))),
                    curve=dict(shape=list(curve.shape),columns=list(curve.columns),finite=int(np.isfinite(curve.iloc[:,1:].to_numpy()).sum())),
                    interpolation_check=dict(injected_missing_row=middle,strike=strike,
                        full_value=finite(full[strike].iloc[middle]),prefix_value=finite(prefix[strike].iloc[middle])),
                    quote=dict(time=q.datetime,bid=finite(q.bid_price1),ask=finite(q.ask_price1)))
                report['groups'][name]=result
                ib.to_csv(args.output/f'{name}_index_bars.csv',index=False)
                fb.to_csv(args.output/f'{name}_future_bars.csv',index=False)
                pb.to_csv(args.output/f'{name}_option_pair.csv',index=False)
                pd.DataFrame({'datetime':pb.datetime,'OPTION_IMPV':iv.impv,'get_impv':raw,'repriced':prices}).to_csv(args.output/f'{name}_iv.csv',index=False)
                mb.to_csv(args.output/f'{name}_curve_input.csv',index=False)
                curve.to_csv(args.output/f'{name}_curve.csv',index=False)
                save()
                print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
    except Exception as exc:
        # Never expose provider payloads or authentication material.
        report['error_type']=type(exc).__name__
        save()
        print('Probe failed:',type(exc).__name__,flush=True)
        raise SystemExit(1)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
