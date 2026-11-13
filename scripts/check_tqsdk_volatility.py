"""Offline numerical and missing-data checks on saved TqSdk probe artifacts."""
import json
from pathlib import Path
from types import SimpleNamespace
import math
import numpy as np
import pandas as pd
from tqsdk import ta


def main():
    folder = Path(__file__).resolve().parents[1]/'instance/tqsdk-verification/volatility'
    report = json.loads((folder/'summary.json').read_text())
    checks = {}
    for name, group in report['groups'].items():
        bars = pd.read_csv(folder/f'{name}_index_bars.csv')
        curve_input = pd.read_csv(folder/f'{name}_curve_input.csv')
        quotes = {}
        for i, strike in enumerate(group['strikes']):
            key = 'symbol' if i == 0 else f'symbol{i}'
            symbol = curve_input[key].iloc[0]
            quotes[symbol] = SimpleNamespace(strike_price=strike, expire_datetime=group['expiry'], option_class='CALL')
        middle = len(curve_input)//2
        strike = group['strikes'][0]
        cases = {}
        for label, value in [('nan',np.nan),('invalid_negative',-1e6)]:
            altered = curve_input.copy()
            altered.loc[middle,'close'] = value
            full = ta.VOLATILITY_CURVE(altered,quotes,group['underlying'],r=0.02)
            prefix = ta.VOLATILITY_CURVE(altered.iloc[:middle+1].copy(),quotes,group['underlying'],r=0.02)
            cases[label] = dict(full_value=float(full[strike].iloc[middle]),prefix_value=float(prefix[strike].iloc[middle]))
        manual_hv = np.log(bars.close).diff().dropna().std(ddof=1)*math.sqrt(250*240)
        # Independent BS repricing, without the SDK's pricing function.
        pair = pd.read_csv(folder/f'{name}_option_pair.csv')
        iv = pd.read_csv(folder/f'{name}_iv.csv').get_impv.to_numpy()
        strike_atm = float(group['option'].split('-')[-1])
        t = (group['expiry']-pair.datetime.to_numpy()/1e9-60)/(360*86400)
        s = pair.close1.to_numpy()
        d1 = (np.log(s/strike_atm)+(0.02+0.5*iv**2)*t)/(iv*np.sqrt(t))
        d2 = d1-iv*np.sqrt(t)
        cdf = np.vectorize(lambda x: (1+math.erf(x/math.sqrt(2)))/2)
        price = s*cdf(d1)-strike_atm*np.exp(-0.02*t)*cdf(d2)
        result = dict(manual_hv=float(manual_hv),hv_abs_error=float(abs(manual_hv-group['underlying_hv'])),
            independent_bs_error=float(np.max(np.abs(price-pair.close.to_numpy()))),
            bar_start=str(pd.to_datetime(bars.datetime.iloc[0],unit='ns',utc=True).tz_convert('Asia/Shanghai')),
            bar_end=str(pd.to_datetime(bars.datetime.iloc[-1],unit='ns',utc=True).tz_convert('Asia/Shanghai')),
            missing_value_cases=cases)
        assert result['hv_abs_error'] < 1e-10
        assert result['independent_bs_error'] < 1e-7
        assert cases['nan']['full_value'] == 0.5
        assert abs(cases['invalid_negative']['full_value']-cases['invalid_negative']['prefix_value']) > 1e-10
        checks[name] = result
    (folder/'offline_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(checks,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
