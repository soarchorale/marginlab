"""Black–Scholes with continuous dividend yield, annual units throughout."""
from math import erf, exp, log, pi, sqrt, isfinite


def greeks(S=3., K=3., T=30/365, r=.02, q=0., sigma=.2, kind='call'):
    if kind not in ('call', 'put'):
        raise ValueError('type must be call or put')
    if not all(isfinite(x) for x in (S,K,T,r,q,sigma)) or min(S,K,T,sigma) <= 0:
        raise ValueError('S, K, T, and sigma must be finite positive values')
    d1 = (log(S/K)+(r-q+sigma*sigma/2)*T)/(sigma*sqrt(T))
    d2 = d1-sigma*sqrt(T)
    cdf = lambda x: (1+erf(x/sqrt(2)))/2
    pdf = exp(-d1*d1/2)/sqrt(2*pi)
    ds, dk = exp(-q*T), exp(-r*T)
    sign = 1 if kind == 'call' else -1
    price = sign*(S*ds*cdf(sign*d1)-K*dk*cdf(sign*d2))
    theta = -S*ds*pdf*sigma/(2*sqrt(T))-sign*r*K*dk*cdf(sign*d2)+sign*q*S*ds*cdf(sign*d1)
    return dict(fair_price=price, delta=sign*ds*cdf(sign*d1), gamma=ds*pdf/(S*sigma*sqrt(T)), theta=theta, theta_day=theta/365, vega=S*ds*pdf*sqrt(T), vanna=-ds*pdf*d2/sigma, volga=S*ds*sqrt(T)*pdf*d1*d2/sigma)


def taylor(price, ds, dv, g):
    return price + ds*g['delta']+dv*g['vega']+ds*ds*g['gamma']/2+dv*dv*g['volga']/2+ds*dv*g['vanna']


def stress(params):
    base = greeks(**params)
    rows = []
    for ds in [-.05, -.03, -.01, 0, .01, .03, .05]:
        for dv in [-.05, -.03, -.01, 0, .01, .03, .05]:
            if params['sigma'] + dv <= 0:
                continue
            exact = greeks(**dict(params, S=params['S']*(1+ds), sigma=params['sigma']+dv))['fair_price']
            approx = taylor(base['fair_price'], params['S']*ds, dv, base)
            rows.append(dict(price_change=ds, vol_change=dv, exact=exact, approximation=approx, pnl=exact-base['fair_price']))
    return dict(greeks=base, scenarios=rows)
