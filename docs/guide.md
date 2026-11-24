# Margin Lab: chart and module guide

Use this guide to answer three questions about a chart: **What was measured? Which controls change it? What does a missing point mean?** Start on the [research dashboard](#research-dashboard) for observed market data, use [pricing](#pricing-and-stress-testing) for hypothetical scenarios, and use [playback](#futures-volume-playback) to explore where futures volume traded.

## Research dashboard

The research page (`/`) opens on one-minute bars for the last seven Shanghai calendar dates, including today. Dates are inclusive. A weekend or an unopened session contributes no bars. Select a product, period, data source and interval, then submit the form. Chart-local parameter buttons submit the same research settings. The phase slider only changes the visible part of one chart.

| Selection | Futures charts and volume playback | Spot index used for drift | Index options used for MVIX |
| --- | --- | --- | --- |
| SSE 50 | IH | SSE 50 | HO |
| CSI 300 | IF | CSI 300 | IO |
| CSI 1000 | IM | CSI 1000 | MO |
| CSI 500 | IC | CSI 500 | None; no matching index-option MVIX |

**Sources.** Live mode reads stored market data from SQLite. A local read-only replica stops at its last imported observation. Demo mode uses a generated scenario; CSV mode uses the supplied rows rather than retrieving live history. A CSV needs `timestamp,open,high,low,close,vix`, with optional `volume`. Supply completed bars at the selected interval, and enter volatility in index points (20, not 0.20). The export button saves the current research result, including bars, visits, anchor and analysis settings; it is not a database or raw quote-chain backup.

**Time and units.** Futures and index bar timestamps mark the start of the bar. For example, the 09:30 one-minute spot bar aligns to the 09:31 option snapshot. Intraday bars are aggregated separately in each trading session; an incomplete bin is omitted. A daily bar requires 240 complete minutes. Increasing the bar size reduces the number of observations; one calendar week of daily bars cannot warm up a 30-bar correlation.

**Header and cards.** The live heading shows the selected product's latest stored quote time. This may be newer than the end of a historical query. The cards give the spot bar close, latest usable MVIX in the selected range, rolling return correlation (with level correlation alongside), and accumulated abnormal volatility. An earlier usable MVIX can still be displayed when the last sample failed; read its quality caption before treating it as current.

### Futures candles, volume and open interest

The candle chart uses only the named active futures contract, not a price series spliced across rolls. The horizontal axis is bar time; the vertical axis is futures price. The body shows open and close, and the wick shows high and low. Hover for OHLC and the timestamp. If the current contract has little history in the selected interval, its chart can be shorter than the spot chart.

The adjacent chart uses the same contract and period: **volume** on the left is the sum traded within the bar; **open interest** on the right is the position count at the end of the bar. Volume is a flow; open interest is a level. High volume with unchanged open interest does not identify buyer or seller direction.

### Volatility smile

This is a cross-section at one instant: the latest **valid** MVIX quote snapshot in the selected interval. The note gives its time. If the afternoon's snapshots were invalid, the smile can legitimately come from the morning. Choose an expiry; the colored call and put checkboxes double as the legend. The horizontal axis is strike and the vertical axis is annualized implied volatility in percent. Hover for the strike, IV, midpoint and quote time where available.

The vertical dashed marker gives the **latest spot close in the selected interval**, not the parity forward used to calculate IV. Its time can differ from the smile's. At each strike, IV is inverted from the bid/ask midpoint using a Black forward model, the term's parity forward, remaining time and rate. Missing, nonpositive or crossed quotes cannot supply a smile point; a midpoint outside the model's price bounds also has no usable IV. A valid aggregate MVIX does not require every smile point to exist.

### Spot price and volatility index

Time runs horizontally; spot price uses the left axis and MVIX index points the right. Hover to compare the same bar, not the heights of lines on unlike scales. MVIX is a local 30-day index: it uses eligible option midquotes, strike-spacing weights and total variance from monthly expiries bracketing 30 days. It is neither an official Cboe index nor the IV of a single contract.

An observed point, an explicitly tagged historical reference estimate and a display-only carry are different. At the closing auction (14:58, 14:59 and 15:00), the chart can display the last valid 14:57 value without creating three new observations. A documented lunch carry can bridge the break for display. Hover for provenance. Other missing quotes are not replaced by a straight-line estimate.

### Price–volatility trajectory and independent visits

Read this as a path, not a time series: **x = spot close, y = MVIX**. Moving upward at approximately the same x means higher implied risk pricing on the return to that price. The path color progresses from early to late within the visible selection. Dots mark independent returns to the shaded anchor band. The latest point fades only when the selection includes the latest bar and that point has a displayable value.

ATR (average true range) measures movement in **price points**. For each bar take the largest of high minus low, absolute high minus previous close, and absolute low minus previous close. This project averages 14 such ranges. The 14th bar's close fixes the anchor and its ATR fixes the price scale for the analysis.

| Control | Default | Effect |
| --- | --- | --- |
| Price band / ATR | 0.25 | Band half-width. At an anchor of 2,800 and ATR of 8, the band is 2,798–2,802. |
| Minimum revisit gap | 4 bars | A close must first move at least one full anchor ATR away. A subsequent outside-to-inside entry with usable volatility counts only after the minimum bar spacing. Remaining inside does not generate repeated visits. |
| Revisit history | 5 observed trading dates | Compare with earlier visits entering from the same direction, within the most recent five data dates including the visit date. At least two earlier matches are needed for a drift value. |
| Global dates / phase slider | Seven calendar dates / full selection | Global dates determine input bars and can change the anchor. The slider zooms the visible path and dots without changing the anchor, matches or other charts. |

For example, a move from 2,800 to 2,810 and back to 2,801 is an entry **from above**. It is not matched to a return from 2,790. Selecting one month globally and five trading dates for revisit history draws the whole month's path, while each visit looks back at most five observed dates, never before the selected start.

### Revisit ladder and event table

The ladder plots MVIX at independent visits against their position on the bar timeline, grouped by entry direction. Use the table to check the timestamp, price, direction and number of prior matches; the latest events appear first. The displayed same-price difference is **100 × [log(current MVIX) − median(log(previous matched MVIX))]**. Positive means higher pricing than the matched baseline. A blank difference can still be a valid visit with too few matches.

After enough prior same-direction drift observations (at least 30), the state can also flag a value above the prior 95th or below the prior 5th percentile. Confirmation requires a subsequent same-direction visit on the same side of the threshold. The comparison does not adjust for small price differences inside the band.

### Accumulated abnormal volatility

This chart asks whether volatility changed more or less than its usual response to the observed spot move. For each bar, fit log changes in MVIX to an intercept, negative spot log returns and positive spot log returns using the **previous** training window (default 60 bars). Subtract the fitted response from the observed volatility change. The line sums these residuals over the accumulation window (default 16 bars) and multiplies by 100.

Positive values mean MVIX rose more, or fell less, than the model implied. Dashed lines are prior rolling 5th and 95th percentiles (up to 250 bars, minimum 30 valid accumulated values). Three consecutive breaches mark an abnormal state. Missing inputs, warmup or a rank-deficient fit can leave a gap. This is a contemporaneous diagnostic, not a forecast of the bar used in the calculation.

### Correlation and co-movement

Both lines range from −1 to +1. Level correlation compares spot and MVIX **levels**; return correlation compares their **log changes**. The common window defaults to 30 bars. Level correlation needs 30 paired observations; return correlation ordinarily needs 31 paired levels to form 30 changes. No variation in one series makes Pearson correlation undefined.

Missing MVIX can invalidate multiple subsequent windows. Auction display carries are not new observations, so a gap can extend into the following morning even with complete minute-bar coverage. A labeled lunch carry is a display aid only. A correlation near −1 describes opposite movement; it does not establish causation or imply that selling volatility is profitable.

### Historical volatility and implied risk pricing

The spot historical-volatility line is the rolling sample standard deviation of spot log returns, using the correlation-window setting. Annualization multiplies by the square root of 252 for daily returns or of (252 × 240 / bar minutes) for intraday returns; the result is shown in percent. MVIX reflects option prices for a forward 30-day horizon and is shown in index points. Each vertical scale matches its line color. Read the tick values: with independent scales a visual crossing does not imply equal values.

### Vanna and Volga sensitivity path

These are model Greeks of a hypothetical call, recalculated at each usable bar: spot is that bar's close, strike is the first close of the range, time to expiry stays at 30/365 years, risk-free rate is 2%, dividend yield is zero and volatility is MVIX divided by 100. Vanna measures how delta changes with volatility; Volga measures how vega changes with volatility. Axes and curves are color-matched and independently scaled.

A Greek needs one usable price/volatility pair, not a rolling fit. The live research module first requires at least 20 index bars and 20 usable paired volatility observations to produce its analysis payload. Missing raw MVIX leaves a gap even if another chart displays an auction carry. Changing the global start can change the fixed strike and therefore the entire path. These are not the Greeks of a quoted HO, IO or MO contract or of a portfolio.

## Pricing and stress testing

On `/pricing`, choose call or put and enter spot, strike, remaining calendar days, volatility, rate and dividend yield. Percentage fields take percentages: enter 20 for 20% volatility. This calculator uses European Black–Scholes pricing with continuous yield; no live contract or position is selected.

The cards report theoretical price and sensitivities: delta to a spot move, gamma to changes in delta, vega to volatility, theta to time passage (annual and daily), and Vanna/Volga to mixed spot–volatility and second-order volatility moves. Vega is quoted per **1.0** change in decimal volatility: multiply by 0.01 for the first-order effect of one volatility percentage point.

The stress grid varies spot horizontally (relative percent) and volatility vertically (absolute percentage points). For example, +3% spot and +5 volatility points changes spot 100 to 103 and volatility 20% to 25%. Each cell shows the exactly repriced option value **minus its base price**. Hover for the shocked theoretical price, the second-order Taylor estimate and their difference. Time is held fixed; shocks producing nonpositive volatility are omitted. Values exclude multipliers, fees and hedging.

## Futures volume playback

On `/chips`, select a futures product and move the playback cursor or press Play. Both panels use the same trailing window ending at the selected minute; neither sees later bars. Window length defaults to 240 minutes, price-bin count to 40 and moving-average length to 20 bars.

- **Price and moving average:** time runs horizontally and futures price vertically. The price line is minute close; the second line is a simple mean of closes within the visible window. It starts only after enough bars have accumulated.
- **Volume at price:** the vertical axis groups minute closing prices into bins. Bar length shows total volume assigned to each bin. The five stacked shades split the window from earlier to later minutes. More bins improve price resolution but leave fewer observations per bin. Since each minute's entire volume is assigned to its close, the profile cannot identify trade direction, exact execution prices or holder cost basis.

## Method notes, settings and access

- **Method notes (`/method`):** explains same-price revisits, cumulative residuals, spot-index alignment, quote-derived MVIX, display-only closing-auction carries, retention and the assumptions behind the model sensitivities.
- **Settings (`/settings`):** the saved-data table lists series, frequency, row count and first/last timestamps. A snapshot row is not necessarily a valid MVIX. The editable refresh interval applies to the legacy daily cache. It does not set the live dashboard's 15-second visible-page polling interval or the deployment-owned retention period.
- **Login and logout:** sign in to read protected pages and APIs. Production collection runs independently of browser sessions. Closing the browser or logging out does not stop the production collector.
- **Retired tools:** market screening and ranking are hidden from navigation; their old live endpoints are retired. The active playback page uses stored futures bars, not the retained simulated frame generator.

For collection, coverage checks, logs and replica synchronization, see [Operations](operations.md). For release and recovery procedures, see [Deployment](deployment.md). For documented historical gaps and outstanding work, see [Known issues](known-issues.md).
