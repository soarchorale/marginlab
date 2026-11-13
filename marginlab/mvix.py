"""MVIX v1: quote-mid variance replication, ACT/365, 30-day bracketing.

Local adaptation of Cboe mathematics; no time interpolation, extrapolation,
trade-price fallback or SDK IV surface interpolation.
"""
import math
from collections import defaultdict

METHOD = 'MVIX-1.0-mid-ACT365-bracket30'
YEAR = 365 * 86400


def term_variance(rows, at, rate):
    expiries = {r['expiry'] for r in rows}
    if len(expiries) != 1:
        raise ValueError('A single-term calculation contains multiple expirations')
    expiry = expiries.pop()
    t = (expiry-at)/YEAR
    if t <= 0:
        raise ValueError('The option has expired')
    chain = defaultdict(dict)
    for row in rows:
        k = float(row['strike'])
        side = row['side']
        if side not in ('CALL', 'PUT') or not math.isfinite(k) or k <= 0:
            continue
        if side in chain[k]:
            raise ValueError('Duplicate option side and strike within an expiration')
        chain[k][side] = row

    def mid(row):
        if not row:
            return None
        bid, ask = row.get('bid'), row.get('ask')
        if bid is None or ask is None or not all(math.isfinite(x) for x in (bid, ask)):
            return None
        if bid < 0 or ask < bid or ask <= 0:
            return None
        return (bid+ask)/2

    pairs = [(abs(mid(v['CALL'])-mid(v['PUT'])), k, mid(v['CALL']), mid(v['PUT']))
             for k,v in chain.items() if 'CALL' in v and 'PUT' in v and mid(v['CALL']) is not None and mid(v['PUT']) is not None]
    if not pairs:
        raise ValueError('No valid call/put quote pair at a common strike')
    _, k, call, put = min(pairs)
    forward = k+math.exp(rate*t)*(call-put)
    below = [k for k in chain if k <= forward]
    if forward <= 0 or not below:
        raise ValueError('The forward price is outside the strike range')
    k0 = max(below)
    pair = chain[k0]
    if mid(pair.get('CALL')) is None or mid(pair.get('PUT')) is None:
        raise ValueError('Missing call or put quote at K0')
    selected = [(k0, (mid(pair['CALL'])+mid(pair['PUT']))/2)]
    counts = {}
    for side, strikes in [('PUT', sorted((k for k in chain if k < k0), reverse=True)),
                          ('CALL', sorted(k for k in chain if k > k0))]:
        zeros = 0
        count = 0
        for strike in strikes:
            row = chain[strike].get(side)
            if row is None:
                raise ValueError('Missing option side; tail truncation cannot be verified')
            if row.get('bid') == 0 or row.get('ask') == 0:
                zeros += 1
                if zeros == 2:
                    break
                continue
            zeros = 0
            price = mid(row)
            if price is None:
                raise ValueError('Missing or crossed quote within the selected option chain')
            selected.append((strike,price))
            count += 1
        counts[side] = count
        if count < 2:
            raise ValueError('At least two valid out-of-the-money strikes are required on each side')
    selected.sort()
    contribution = 0.
    for i,(strike,price) in enumerate(selected):
        delta = (selected[1][0]-strike if i==0 else strike-selected[i-1][0] if i==len(selected)-1
                 else (selected[i+1][0]-selected[i-1][0])/2)
        contribution += delta*price/strike**2
    variance = 2*math.exp(rate*t)*contribution/t-(forward/k0-1)**2/t
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError('The calculated variance is invalid')
    return dict(expiry=expiry,t=t,variance=variance,forward=forward,k0=k0,
                strikes=[k for k,_ in selected],counts=counts)


def calculate(rows, at, rate=.02):
    if not math.isfinite(rate) or not -.1 <= rate <= .5:
        raise ValueError('The risk-free rate is invalid')
    target = at+30*86400
    groups = defaultdict(list)
    for row in rows:
        if row['expiry'] > at:
            groups[row['expiry']].append(row)
    near = [e for e in groups if e <= target]
    far = [e for e in groups if e >= target]
    if not near or not far:
        raise ValueError('No expirations bracket 30 days; extrapolation is disabled')
    e1,e2 = max(near),min(far)
    first = term_variance(groups[e1],at,rate)
    second = first if e1 == e2 else term_variance(groups[e2],at,rate)
    w = 1. if e1 == e2 else (e2-target)/(e2-e1)
    total = w*first['t']*first['variance']+(1-w)*second['t']*second['variance']
    return dict(value=100*math.sqrt(total/(30/365)),near=first,far=second,
                weight_near=w,rate=rate,rate_source='configured_assumption',method=METHOD)
