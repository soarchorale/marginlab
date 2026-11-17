"""Quote-mid smile using Black forward IV and snapshot put-call parity forwards."""
from functools import lru_cache
import json
import math
import pandas as pd


def forward_price(forward, strike, years, rate, sigma, side):
    if sigma <= 0:
        return math.exp(-rate*years)*max((forward-strike)*(1 if side=='CALL' else -1),0)
    scale=sigma*math.sqrt(years)
    d1=math.log(forward/strike)/scale+scale/2
    d2=d1-scale
    normal=lambda x:(1+math.erf(x/math.sqrt(2)))/2
    sign=1 if side=='CALL' else -1
    return math.exp(-rate*years)*sign*(forward*normal(sign*d1)-strike*normal(sign*d2))


def implied_volatility(mid, forward, strike, years, rate, side):
    if side not in ('CALL','PUT') or not all(math.isfinite(x) for x in (mid,forward,strike,years,rate)) or min(mid,forward,strike,years)<=0:
        return None
    floor=forward_price(forward,strike,years,rate,0,side)
    ceiling=math.exp(-rate*years)*(forward if side=='CALL' else strike)
    if not floor+1e-9 < mid < ceiling-1e-9:
        return None
    lo,hi=1e-7,5.
    if forward_price(forward,strike,years,rate,hi,side)<mid:
        return None
    for _ in range(70):
        sigma=(lo+hi)/2
        if forward_price(forward,strike,years,rate,sigma,side)<mid:lo=sigma
        else:hi=sigma
    return (lo+hi)/2*100


@lru_cache(maxsize=12)
def build_smile(timestamp, details_json, quotes_json):
    details=json.loads(details_json)
    quotes=json.loads(quotes_json)
    rate=details.get('rate',.02)
    at=pd.Timestamp(timestamp).tz_localize('Asia/Shanghai').timestamp()
    terms=[]
    for key in ('near','far'):
        term=details.get(key,{})
        expiry=term.get('expiry');forward=term.get('forward')
        if not expiry or not forward or any(x['expiry']==expiry for x in terms):continue
        years=(expiry-at)/(365*86400)
        if years<=0:continue
        points=[];excluded=0
        for row in sorted((r for r in quotes if r.get('expiry')==expiry),key=lambda r:(r['strike'],r['side'])):
            bid,ask=row.get('bid'),row.get('ask')
            good=bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask) and 0<bid<=ask
            mid=(bid+ask)/2 if good else None
            iv=implied_volatility(mid,forward,row['strike'],years,rate,row['side']) if good else None
            if iv is None:excluded+=1
            points.append(dict(symbol=row.get('symbol'),strike=row['strike'],side=row['side'],iv=iv,
                               bid=bid,ask=ask,mid=mid,quote_time=row.get('quote_time') or (
                                   pd.Timestamp(row['quote_ns'],unit='ns',tz='UTC').tz_convert('Asia/Shanghai').isoformat() if row.get('quote_ns') else None)))
        terms.append(dict(expiry=expiry,expiry_date=pd.Timestamp(expiry,unit='s',tz='UTC').tz_convert('Asia/Shanghai').date().isoformat(),
                          forward=forward,rate=rate,points=points,excluded=excluded))
    return dict(timestamp=timestamp,model='Black forward IV; put-call parity forward; ACT/365',terms=terms)
