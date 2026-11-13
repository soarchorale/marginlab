# TqSdk volatility verification and MVIX research conventions

Historical verification date: 2026-11-08. Interpreter: `.pixi/envs/default/bin/python`, Python 3.11.16, TqSdk 3.10.4. The requested tqsdk-trading-and-data skill was followed. Requests were read-only using TqSim; the probe did not modify production feeds or trading logic. Later production behavior is documented in operations.md.

## Observed outputs

Run `scripts/probe_tqsdk_volatility.py --auth-file tqauth.json`. Credentials are read locally and never printed. Inputs, outputs, and summaries are saved under project-root `instance/tqsdk-verification/volatility/`, without credentials. `scripts/check_tqsdk_volatility.py` verifies those artifacts offline.

Each index/futures pair used 240 completed one-minute bars. The final bar was conservatively excluded following the SDK forming-bar convention. Samples span November 5 at 14:59 through November 6 at 14:58, including overnight and lunch-transition returns. HV is the SDK's direct annualization of this sample, not cleaned single-day or 30-day realized volatility.

| Product | Index HV | Futures HV | Near-term call | Final IV |
| --- | ---: | ---: | --- | ---: |
| 50 | 27.2211% | 21.3345% | HO2611-C-2800 | 13.3791% |
| 300 | 36.0972% | 34.9986% | IO2611-C-4050 | 19.3454% |
| 1000 | 50.6488% | 51.7599% | MO2611-C-6500 | 35.8506% |

IV tests used option trade closes and index closes with an assumed 2% rate, not completed midpoint IV/MVIX. Each group had 120 rows from November 6 at 11:29 to 14:58, labeled by bar start. OPTION_IMPV returned a 120-by-1 DataFrame with column `impv`; get_impv returned a 120-element Series. Maximum difference was about 2.76e-13; independent Black–Scholes repricing error was below 6.1e-11 price points. This confirms numerical consistency, not model adequacy or synchronized quotes.

VOLATILITY_CURVE used three strikes of one expiration and CALL side, returning 120-by-4 data (`datetime` and three strike columns) with 360 finite IVs. It is not an automatic expiration-by-strike surface. Identical strikes across expirations or CALL/PUT groups can overwrite dictionary keys; group calls accordingly.

## Source review and controlled invalid-input checks

- get_his_volatility returns one decimal float for the whole input, annualized using 250 trading days and contract trading hours. Rolling history requires explicit windows. Independent error was below 1.1e-14.
- SDK option time to expiration uses ACT/360; MVIX uses ACT/365. Do not mix conventions implicitly.
- Replacing a close with NaN returned the initial IV guess 0.5 rather than missing data. This was a controlled test, not a claim that the observed market input had that defect.
- An unsolvable negative price produced NaN IV; VOLATILITY_CURVE then used later observations for linear interpolation. The product-50 full-sample value was 0.1380213656 versus prefix-only 0.1376601081. All three groups showed prefix inconsistency.
- Production research must reject invalid prices before solving, verify repricing residuals afterward, and forbid future-value interpolation into historical IV. Raw curve output must not directly trigger drift signals.

## Research design and later implementation

Research covers 50/300/1000 using HO/IO/MO. The previously discovered CSI 500 ETF option list came from TqSdk, not AKShare. The account could list contracts but lacked SSE.510500 underlying-quote permission, so that drift study was deferred. IC itself has no matching index options.

1. Use the last quote record before each common minute boundary, compute `(bid + ask) / 2`, and retain original time/quality evidence. Do not skip invalid updates to recover older valid quotes. No unbounded lunch/overnight carry-forward. The 09:30 bar corresponds to the 09:31 quote boundary.
2. This is backward as-of matching, not interpolation from a later minute. Trade OHLC cannot reconstruct historical bid/ask. Actual historical ticks/snapshots can, within coverage and permission limits.
3. Select actual expirations bracketing 30 days, interpolate `T * sigma^2`, divide by 30 days expressed in years, take the square root, and multiply by 100. Do not always use front/next calendar months, average IV directly, or silently extrapolate. Strict bracketing is a local project rule.
4. Use Cboe-style cross-strike variance replication, forward-price/K0 selection, and documented quote/zero-bid filters. Record the assumed rate, local expiration time, quality rules, and method version. MVIX-50/300/1000 are not official Cboe indices.
5. MVIX-30D is the primary drift series. Standardized ATM IV, 25-delta put/call skew, and HV are possible supporting measures. Strike selection and tail liquidity can change MVIX; predictive value still needs out-of-sample validation.
6. Use corresponding indices for primary drift research. Futures are dashboard instruments with basis/roll context; futures-based drift needs separate controls.

The initial design was subsequently implemented with midpoint MVIX and recent-Tick backfill. See [Operations](operations.md) for current sampling and coverage rather than treating this historical probe as a deployment guarantee.

## References

- https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.ta.html
- https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.tafunc.html
- https://cdn.cboe.com/resources/indices/Cboe_Volatility_Index_Mathematics_Methodology.pdf
