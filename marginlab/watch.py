"""Observation rules shared by offline replay and the live signal monitor."""
from collections import defaultdict
from datetime import datetime, timedelta
import math

import pandas as pd

from .coverage import ZONE
from .smile import build_smile


def add_comovement(frame):
    complete = (frame.timestamp.diff(5) == pd.Timedelta(minutes=5))
    for key in ('close', 'vix'):
        complete &= frame[key].rolling(6).count().eq(6)
    frame['spot5'] = (100 * (frame.close / frame.close.shift(5) - 1)).where(complete)
    frame['mvix5'] = frame.vix.diff(5).where(complete)
    for key in ('spot5', 'mvix5'):
        frame[key+'_limit'] = frame[key].abs().shift().rolling(250, min_periods=30).quantile(.75)


def corr_transition(states, x):
    if not math.isfinite(x):
        states.pop('corr', None)
        return None
    corr = states.setdefault('corr', {})
    old = corr.get('zone')
    if x <= -.60:
        target = 'negative'
    elif x >= .60:
        target = 'positive'
    elif (old == 'negative' and x < -.35) or (old == 'positive' and x > .35):
        target = old
    else:
        target = 'weak'
    corr['count'] = min(3, corr.get('count', 0)+1) if target == corr.get('candidate') else 1
    corr['candidate'] = target
    if corr['count'] < 3:
        return None
    corr['zone'] = target
    if (old, target) in [('negative', 'weak'), ('weak', 'positive')]:
        return dict(rule='correlation', direction=target, previous=old, value=x, level='context')
    return None


def interpolate(pairs, target):
    values = set()
    for a, b in zip(pairs, pairs[1:]):
        if a is None or b is None or a[0] == b[0]:
            continue
        if min(a[0], b[0]) <= target <= max(a[0], b[0]):
            values.add(round(a[1] + (b[1]-a[1])*(target-a[0])/(b[0]-a[0]), 8))
    return values.pop() if len(values) == 1 else None


def smile_wings(stamp, details, quotes):
    at = datetime.fromisoformat(stamp).replace(tzinfo=ZONE).timestamp()
    result = {}
    for term in build_smile(stamp, details, quotes)['terms']:
        forward = term['forward']
        years = (term['expiry']-at)/(365*86400)
        strikes = defaultdict(dict)
        for p in term['points']:
            strikes[p['strike']][p['side']] = p['iv']
        atm_pairs = []
        wings = {'CALL': [], 'PUT': []}
        for strike, sides in sorted(strikes.items()):
            call, put = sides.get('CALL'), sides.get('PUT')
            atm_pairs.append((strike, (call+put)/2) if call is not None and put is not None else None)
            for side, points in wings.items():
                if (side == 'CALL' and strike < forward) or (side == 'PUT' and strike > forward):
                    continue
                iv = sides.get(side)
                if iv is None:
                    points.append(None)
                    continue
                scale = iv / 100 * math.sqrt(years)
                d1 = math.log(forward/strike)/scale + scale/2
                delta = (1 + math.erf(d1/math.sqrt(2))) / 2
                points.append((delta if side == 'CALL' else 1-delta, iv))
        atm = interpolate(atm_pairs, forward)
        if atm is None:
            continue
        for side, points in wings.items():
            iv = interpolate(points, .25)
            if iv is not None:
                result[f"skew:{term['expiry_date']}:{side}"] = iv-atm
    return result


def wing_events(states, stamp, wings):
    history = states.setdefault('wing_history', {})
    t = datetime.fromisoformat(stamp)
    base = (t-timedelta(minutes=5)).isoformat()
    previous = (t-timedelta(minutes=1)).isoformat()
    five_ago = history.get(base, {})
    prior = set(states.get('wide_wings', [])) if previous in history else set()
    wide = {k for k, val in wings.items() if k in five_ago and val-five_ago[k] >= .5}
    confirmed = wide & prior
    new = confirmed - set(states.get('confirmed_wings', [])) if prior else set()
    states['wide_wings'] = sorted(wide)
    states['confirmed_wings'] = sorted(confirmed)
    states['wing_history'] = {k:v for k,v in history.items() if k > base}
    states['wing_history'][stamp] = wings
    return [dict(rule=k, direction='widening', level='context', previous=five_ago[k],
                 value=wings[k], change=wings[k]-five_ago[k]) for k in sorted(new)]


def observation_events(row, visit, anchor, states, wings):
    triggers = []
    active = states['active'] = {}
    breach = None
    if row.accumulated > row.upper + 1e-10:
        breach = 'up'
    elif row.accumulated < row.lower - 1e-10:
        breach = 'down'
    active['residual_watch'] = breach
    if breach and states.get('breach') != breach:
        up = breach == 'up'
        triggers.append(dict(rule='residual_watch', direction=breach, level='watch',
                             value=row.accumulated*100, threshold=(row.upper if up else row.lower)*100,
                             percentile=95 if up else 5))
    states['breach'] = breach
    movement = None
    if abs(row.spot5)>row.spot5_limit and abs(row.mvix5)>row.mvix5_limit and row.spot5*row.mvix5>0:
        movement = 'up' if row.mvix5 > 0 else 'down'
    active['comove'] = movement
    if movement and states.get('movement') != movement:
        triggers.append(dict(rule='comove', direction=movement, level='watch', spot=row.spot5,
                             mvix=row.mvix5, spot_limit=row.spot5_limit, mvix_limit=row.mvix5_limit))
    states['movement'] = movement
    if visit and visit['drift'] is not None and visit['drift'] != 0:
        baseline = row.vix / math.exp(visit['drift'])
        direction = 'up' if visit['drift']>0 else 'down'
        triggers.append(dict(rule='baseline', direction=direction, level='watch', baseline=baseline,
                             difference=row.vix-baseline, entry=visit['direction'],
                             matches=visit['matches'], anchor=anchor['price']))
        active['baseline'] = direction
    corr = corr_transition(states, row.corr_return)
    if corr:
        triggers.append(corr)
    triggers.extend(wing_events(states, row.endpoint, wings))
    return triggers
