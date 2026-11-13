"""Causal volatility diagnostics. All thresholds and fits exclude the current bar."""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DriftConfig:
    window: int = 60
    horizon: int = 16
    correlation: int = 30
    atr_window: int = 14
    band: float = .25
    excursion: float = 1.
    gap: int = 4
    lookback_days: int = 5
    min_matches: int = 2
    threshold_samples: int = 30
    persistence: int = 3


def validate_bars(frame, min_records=20):
    required = {'timestamp', 'open', 'high', 'low', 'close', 'vix'}
    if not required.issubset(frame.columns):
        raise ValueError('Missing columns: ' + ', '.join(sorted(required - set(frame.columns))))
    df = frame.copy()
    df['timestamp'] = pd.to_datetime(df.timestamp, errors='raise')
    if df.timestamp.dt.tz is not None:
        df['timestamp'] = df.timestamp.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
    for col in required - {'timestamp'}:
        df[col] = pd.to_numeric(df[col], errors='raise')
    values = df[list(required - {'timestamp'})].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Prices and volatility must be finite positive values')
    if df.timestamp.isna().any() or df.timestamp.duplicated().any():
        raise ValueError('Missing or duplicate timestamps; align complete trading timestamps first')
    if ((df.high < df[['open', 'close', 'low']].max(axis=1)) | (df.low > df[['open', 'close']].min(axis=1))).any():
        raise ValueError('Inconsistent OHLC high/low values')
    if len(df) < min_records or len(df) > 10000:
        raise ValueError(f'Data must contain {min_records}-10000 completed bars')
    return df.sort_values('timestamp').reset_index(drop=True)


def analyze(df, cfg=DriftConfig(), bars_per_year=252 * 16):
    df = df.copy().reset_index(drop=True)
    p, v = df.close, df.vix
    r, q = np.log(p).diff(), np.log(v).diff()
    tr = pd.concat([df.high-df.low, (df.high-p.shift()).abs(), (df.low-p.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(cfg.atr_window).mean()
    level = p.rolling(cfg.correlation).corr(v).clip(-1, 1)
    returns = r.rolling(cfg.correlation).corr(q).clip(-1, 1)
    hv = r.rolling(cfg.correlation).std() * np.sqrt(bars_per_year) * 100
    x = np.column_stack([np.ones(len(df)), r.clip(upper=0), r.clip(lower=0)])
    residual = np.full(len(df), np.nan)
    expected = residual.copy()
    for i in range(cfg.window + 1, len(df)):
        train_x, train_y = x[i-cfg.window:i], q.iloc[i-cfg.window:i].to_numpy()
        if not np.isfinite(train_x).all() or not np.isfinite(train_y).all() or not np.isfinite(x[i]).all() or not np.isfinite(q.iloc[i]) or np.linalg.matrix_rank(train_x) < 3:
            continue
        coef = np.linalg.lstsq(train_x, train_y, rcond=None)[0]
        expected[i] = x[i] @ coef
        residual[i] = q.iloc[i] - expected[i]
    accumulated = pd.Series(residual).rolling(cfg.horizon).sum()
    upper = accumulated.shift().rolling(250, min_periods=cfg.threshold_samples).quantile(.95)
    lower = accumulated.shift().rolling(250, min_periods=cfg.threshold_samples).quantile(.05)
    up = (accumulated > upper + 1e-10).rolling(cfg.persistence).sum() == cfg.persistence
    down = (accumulated < lower - 1e-10).rolling(cfg.persistence).sum() == cfg.persistence
    states = np.where(up, '异常升波', np.where(down, '异常降波', np.where(upper.isna(), '样本积累中', '常态区间')))

    # One fixed anchor, known at the first ATR-ready bar; never chosen retrospectively.
    anchor_idx = cfg.atr_window - 1
    anchor, width = float(p.iloc[anchor_idx]), float(atr.iloc[anchor_idx])
    visits, drift = [], np.full(len(df), np.nan)
    away, side, last_visit, prev_inside = False, 0, anchor_idx, True
    trading_days = list(dict.fromkeys(df.timestamp.dt.date))
    day_rank = {d: i for i, d in enumerate(trading_days)}
    for i in range(anchor_idx + 1, len(df)):
        distance = float(p.iloc[i] - anchor)
        inside = abs(distance) <= cfg.band * width
        if abs(distance) >= cfg.excursion * width:
            away, side = True, (1 if distance > 0 else -1)
        if inside and not prev_inside and away and i-last_visit >= cfg.gap and np.isfinite(v.iloc[i]):
            direction = '下行进入' if side > 0 else '上行进入'
            day = day_rank[df.timestamp.iloc[i].date()]
            matches = [e for e in visits if e['direction'] == direction and day-day_rank[df.timestamp.iloc[e['index']].date()] < cfg.lookback_days]
            value = float(np.log(v.iloc[i]) - np.median([np.log(e['vix']) for e in matches])) if len(matches) >= cfg.min_matches else None
            drift[i] = value if value is not None else np.nan
            previous = [e for e in visits if e['direction'] == direction and e['drift'] is not None]
            history = [e['drift'] for e in previous]
            state = '样本不足'
            if value is not None:
                state = '基准抬升' if value > 0 else '基准回落' if value < 0 else '持平'
                if len(history) >= cfg.threshold_samples:
                    sign = '异常升波' if value > np.quantile(history, .95) else '异常降波' if value < np.quantile(history, .05) else '常态区间'
                    state = sign + (' · 已确认' if previous and previous[-1]['state'].startswith(sign) and sign != '常态区间' else ' · 待回访确认')
            visits.append(dict(index=i, timestamp=str(df.timestamp.iloc[i]), price=float(p.iloc[i]), vix=float(v.iloc[i]), direction=direction, matches=len(matches), drift=value, state=state))
            away, last_visit = False, i
        prev_inside = inside
    out = df.assign(corr_level=level, corr_return=returns, hv=hv, residual=residual, expected=expected, accumulated=accumulated, upper=upper, lower=lower, drift=drift, state=states)
    return dict(bars=out, visits=visits, anchor=dict(price=anchor, atr=width, low=anchor-cfg.band*width, high=anchor+cfg.band*width, index=anchor_idx))
