"""Synthetic trade records, chip distribution and playback price series."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# ============================================================
# 1. Generate simulated transactions
# ============================================================
def generate_data(n_records=1000, seed=42):
    rng = np.random.default_rng(seed)
    start_date = datetime.now() - timedelta(days=30)
    timestamps = [
        start_date + timedelta(minutes=i * 30)
        for i in range(n_records)
    ]
    base_price = 100
    prices = base_price + np.cumsum(rng.standard_normal(n_records) * 0.5)
    prices = np.clip(prices, 90, 110).tolist()
    volumes    = rng.integers(100, 10000, n_records).tolist()
    directions = rng.choice(
        [1, -1], n_records, p=[0.55, 0.45]
    ).tolist()

    return pd.DataFrame({
        'timestamp': timestamps,
        'price':     prices,
        'volume':    volumes,
        'direction': directions
    })


# ============================================================
# 2. Calculate the volume profile
# ============================================================
def compute_chip(df_window, cfg):
    """
    Calculate volume profile data from the current window and configuration.
    Return dictionaries ready for frontend rendering.
    """
    bins        = cfg.get('bins', 40)
    time_layers = cfg.get('time_layers', 10)
    scheme      = cfg.get('scheme', 1)

    p_min = cfg.get('hist_price_min') if cfg.get('hist_price_min') is not None else df_window['price'].min()
    p_max = cfg.get('hist_price_max') if cfg.get('hist_price_max') is not None else df_window['price'].max()
    if p_min > p_max:
        raise ValueError('The histogram upper price bound must exceed the lower bound')
    if p_min == p_max:
        p_min -= 1
        p_max += 1

    price_bins     = np.linspace(p_min, p_max, bins + 1)
    bin_centers    = (price_bins[:-1] + price_bins[1:]) / 2
    bin_width      = float(price_bins[1] - price_bins[0])
    time_bin_edges = np.linspace(0, 1, time_layers + 1)

    # Normalize timestamps
    df = df_window.copy()
    t_min = df['timestamp'].min()
    t_max = df['timestamp'].max()
    total_sec = (t_max - t_min).total_seconds()
    df['tw'] = df['timestamp'].apply(
        lambda t: (t - t_min).total_seconds() / total_sec
        if total_sec > 0 else 0
    )

    df_buy  = df[df['direction'] ==  1]
    df_sell = df[df['direction'] == -1]

    result = []

    for i, center in enumerate(bin_centers):
        p_lo, p_hi = price_bins[i], price_bins[i + 1]
        sb = df_buy[ (df_buy['price']  >= p_lo) & ((df_buy['price'] <= p_hi) if i == bins-1 else (df_buy['price'] < p_hi))]
        ss = df_sell[(df_sell['price'] >= p_lo) & ((df_sell['price'] <= p_hi) if i == bins-1 else (df_sell['price'] < p_hi))]

        layers = []
        for j in range(time_layers):
            t_lo, t_hi = time_bin_edges[j], time_bin_edges[j + 1]
            mid_t = float((t_lo + t_hi) / 2)

            bv = float(sb[(sb['tw'] >= t_lo) & ((sb['tw'] <= t_hi) if j == time_layers-1 else (sb['tw'] < t_hi))]['volume'].sum())
            sv = float(ss[(ss['tw'] >= t_lo) & ((ss['tw'] <= t_hi) if j == time_layers-1 else (ss['tw'] < t_hi))]['volume'].sum())

            layers.append({
                'time_mid': mid_t,
                'buy_vol':  bv,
                'sell_vol': sv,
                'net_vol':  bv - sv
            })

        result.append({
            'price_center': float(center),
            'price_low':    float(p_lo),
            'price_high':   float(p_hi),
            'bin_width':    bin_width,
            'layers':       layers,
            'total_buy':    float(sb['volume'].sum()),
            'total_sell':   float(ss['volume'].sum()),
        })

    return result


# ============================================================
# 3. Prepare price history through the current frame
# ============================================================
def compute_price_series(df_full, frame_end_idx, ma_window=20):
    """Return price and volume series through frame_end_idx."""
    df_slice = df_full.iloc[:frame_end_idx + 1].copy()

    prices     = df_slice['price'].tolist()
    volumes    = df_slice['volume'].tolist()
    directions = df_slice['direction'].tolist()
    timestamps = [t.strftime('%Y-%m-%d %H:%M') for t in df_slice['timestamp']]

    # Moving averages (only where enough observations are available)
    ma_values = df_slice['price'].rolling(window=ma_window).mean()
    ma = [round(v, 3) if not np.isnan(v) else None
          for v in ma_values.tolist()]

    return {
        'timestamps': timestamps,
        'prices':     [round(p, 3) for p in prices],
        'volumes':    volumes,
        'directions': directions,
        'ma':         ma,
        'ma_window':  ma_window,
    }