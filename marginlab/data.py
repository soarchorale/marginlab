"""Explicit demo/CSV/AKShare providers; no silent demo fallback."""
from datetime import datetime
from zoneinfo import ZoneInfo
import io
import json
import numpy as np
import pandas as pd
from .analytics import validate_bars

SYMBOLS = {'510050': ('上证50ETF', '50etf'), '510300': ('沪深300ETF', '300etf'), '50': ('上证50',None), '300': ('沪深300',None), '500': ('中证500',None), '1000': ('中证1000',None)}


def demo(symbol='510050', period='15', scenario='rising', count=640):
    rng = np.random.default_rng(42)
    base = {'510050':2.8,'510300':4.1,'50':2800,'300':4000,'500':6500,'1000':6500}[symbol]
    if period == 'daily':
        times = pd.bdate_range('2024-01-02', periods=count)
    else:
        minutes = int(period)
        times = []
        for day in pd.bdate_range('2025-01-02', periods=100):
            for hour in [9.5, 13.]:
                start = day + pd.Timedelta(hours=hour)
                times.extend(start + pd.Timedelta(minutes=m) for m in range(minutes, 121, minutes))
            if len(times) >= count:
                break
        times = pd.DatetimeIndex(times[:count])
    n = len(times)
    t = np.arange(n)
    close = base * np.exp(.012*np.sin(2*np.pi*t/32)+.0015*np.sin(t/7))
    drift = {'rising': .001, 'falling': -.00065, 'neutral': 0}[scenario] * np.maximum(t-160, 0)
    vix = 19*np.exp(-3*np.log(close/base)+drift+rng.normal(0,.003,n))
    opens = np.r_[close[0],close[:-1]]
    df = pd.DataFrame(dict(timestamp=times, open=opens, high=np.maximum(opens,close)+base*.0007, low=np.minimum(opens,close)-base*.0007, close=close, vix=vix, volume=rng.integers(1000,10000,n)))
    return df


def csv_bars(content):
    try:
        df = pd.read_csv(io.StringIO(content))
    except Exception as exc:
        raise ValueError('Unable to parse CSV') from exc
    return validate_bars(df)


def ak_module():
    try:
        import akshare
        return akshare
    except ImportError as exc:
        raise RuntimeError('Install project dependencies: pip install -r requirements.txt') from exc


def live(symbol, period, start, end):
    if period != 'daily':
        raise ValueError('The intraday QVIX endpoint lacks verifiable dates; import a CSV with complete timestamps. This legacy feed supports daily bars only.')
    ak = ak_module()
    prices = ak.fund_etf_hist_em(symbol=symbol, period='daily', start_date=start.replace('-',''), end_date=end.replace('-',''), adjust='')
    vols = getattr(ak, 'index_option_'+SYMBOLS[symbol][1]+'_qvix')()
    prices = prices.rename(columns={'日期':'timestamp','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume'})
    vols = vols.rename(columns={'date':'timestamp','close':'vix'})
    prices['timestamp'] = pd.to_datetime(prices.timestamp)
    vols['timestamp'] = pd.to_datetime(vols.timestamp)
    prices = prices[(prices.timestamp >= pd.Timestamp(start)) & (prices.timestamp <= pd.Timestamp(end))]
    vols = vols[(vols.timestamp >= pd.Timestamp(start)) & (vols.timestamp <= pd.Timestamp(end))][['timestamp','vix']].drop_duplicates()
    prices = prices.drop_duplicates()
    if prices.timestamp.duplicated().any() or vols.timestamp.duplicated().any():
        raise ValueError('Conflicting quotes for the same date prevent reliable one-to-one alignment')
    df = prices.merge(vols, on='timestamp', validate='one_to_one')
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    cutoff = pd.Timestamp(now.date())
    df = df[(df.timestamp >= pd.Timestamp(start)) & (df.timestamp <= pd.Timestamp(end))]
    df = df[df.timestamp < cutoff] if now.hour < 15 else df[df.timestamp <= cutoff]
    return validate_bars(df, min_records=1)


def records(df):
    return json.loads(df.to_json(orient='records', date_format='iso', force_ascii=False))
