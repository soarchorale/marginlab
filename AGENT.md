# Language and workspace conventions

The local project root is `/path/to/marginlab`. Resolve runtime paths from `__file__` or explicit CLI arguments, not from the caller working directory. Keep documentation, source comments, docstrings, CLI prompts, and application-owned logs in English. Preserve Chinese browser copy, UI state labels, and upstream field names required by provider schemas. Do not rewrite private historical databases or third-party source files merely to translate text.

## Dashboard polling (2026-11-13)

Research polling runs every 15 seconds while the page is visible. Background requests preserve the current layout, opacity, export controls, and unchanged chart nodes. Only explicit submissions show the loading block and dim old results. Polls do not submit edited parameters until a successful manual submission; overlapping and superseded responses are ignored. A failed poll reports stale data without discarding the last successful view. Resize rendering is debounced through one handler. Keep this distinction when changing `static/js/lab.js`.

Known gaps and the 2026-11-14 capacity review are recorded in `docs/known-issues.md`. The user will supply missing historical data later; preserve the gaps until that data is available.

# marginlab: Project Design and Maintenance Guide

Production deployment: `scripts/setup-server.sh` creates Python 3.11 venv and runs interactive credential/admin initialization. `scripts/start.sh` executes Waitress + collector via `scripts/serve.py`; this explicitly starts the collector unlike a bare WSGI import. Private config lives in ignored `instance/`. `deploy/marginlab.service` is a Linux systemd template; not installed locally. Only `requirements.txt` remains. Root project name is marginlab; retain the marginlab Python package, not a package named src. Build a private-file-free archive with `scripts/build_release.py`; use `scripts/backup_sqlite.py` for WAL-consistent transfer copies. See `docs/deployment.md`.

## Current migration addendum (2026-11-08; supersedes legacy live-data descriptions below)

The user explicitly replaced the login-gated collection requirement with unattended collection. `app.py` starts `Feed.start_background()` by default (disable with `MARGIN_AUTOCOLLECT=0`). Weekdays Shanghai 09:20–15:10, all four products, automatic retries and hourly retention pruning. Web APIs remain authenticated but read SQLite without initiating subscriptions in automatic mode. App factory stays inert for tests/offline use; WSGI imports do not auto-start the collector. No OS cron/launchd was installed. Read `docs/operations.md` for authoritative current operations; old lifecycle descriptions below are superseded. Local queries no longer cap stored bars at 10,000; live minute quote evidence is saved separately as well as in MVIX snapshots.

Live dashboard uses TqSdk 3.10.4, index futures IH/IF/IM/IC and index options HO/IO/MO. IC has no matching index options. Active APIs are authenticated POST `/api/research` and `/api/chips`; legacy AKShare endpoints return 410. Chips show neutral minute volume, not inferred trade direction. Core modules: `tqfeed.py` (scheduled autonomous collector, legacy demand mode optional), `tqstore.py` (bars/snapshots), `mvix.py` (midquote variance replication), `market.py` (exact minute-end joins), `history.py` (historical backward-asof quotes), `scripts/backfill_mvix.py` (explicit administrator historical import).

Read the user-required TqSdk skill before SDK work. Read `SKILL.md` from your extracted `tqsdk-trading-and-data` skill directory; download and extract the official package if missing. Never reveal auth files or session tokens; no orders.

Historical Tick date downloads were permission-denied in the actual account; recent 10,000 Tick serials work. Backfill currently handles only current bracketing expiries and their common recent coverage. Missing chain/invalid/stale quotes remain gaps. Never substitute trade closes, future quotes or current prices. Tick strictly before minute end, same session/day. Historical v2 verifies contiguous Tick IDs and bounds coverage by the final event (session-close tolerance 1 second); unchanged quotes do not expire after 600 seconds. Live boundary sampling permits unchanged quotes only with an index heartbeat no older than 10 seconds; stale heartbeats invalidate snapshots. Startup snapshots without a heartbeat retain the 600-second cap. `tq_option_minutes` stores auditable quote evidence; `tq_snapshots` stores immutable valid values (failed entries can be repaired using historical evidence); `tq_history_jobs` records coverage. Application initialization creates all tables; rolling retention prunes bars, quotes and MVIX. Research trims only the leading interval without MVIX, keeps internal gaps and never calculates returns across them. UI displays history before drift warmup is complete. Tests must use isolated databases; preserve private runtime SQLite.

Last reviewed: 2026-11-07. This document describes the current implementation, not a proposed architecture. Use it to orient future work, then read the relevant modules before editing. Source code is authoritative if this guide becomes stale. `README.md` contains English usage and operational documentation.

## Purpose and product scope

Margin Lab is a local, single-administrator options research workbench. It unifies the former root Flask application and the former `python/akoption.py` application into one application with shared navigation, authentication, and styling.

Its active areas are:

- Volatility research: ETF price/QVIX relationships, same-price volatility drift, residual accumulation, historical volatility, correlations, and model Vanna/Volga diagnostics.
- Pricing and stress testing: single-contract Black–Scholes pricing, Greeks, exact scenario repricing, and second-order Taylor approximation.
- Chip playback: simulated transaction distributions, time layers, moving averages, and playback controls.
- Administration: login, sessions, and adjustable historical market-data retention and refresh settings.

Market screening and rankings are intentionally hidden from navigation, but their implementation is retained. The application does not place orders or send external messages. Research labels are descriptive hypotheses, not validated trading recommendations.

## Runtime and commands

The stack is Python 3.11, Flask, NumPy, Pandas, SQLite, server-rendered HTML, plain JavaScript, and CSS. There is no Node build pipeline or ORM. Research charts use native SVG; chip playback uses ECharts 5 from a CDN.

```sh
pixi run dev
PORT=5001 pixi run dev
pixi run test

# Equivalent commands using the existing environment:
.pixi/envs/default/bin/python app.py
.pixi/envs/default/bin/python -m unittest discover -s tests -v

# Optional live-data dependencies:
uv pip install --python .pixi/envs/default/bin/python -r requirements.txt
```

`app.py` is the local development entry point; production uses `scripts/start.sh`. It binds to `127.0.0.1`, defaults to port 5000, and disables debug. Pixi currently targets macOS ARM64. The single requirements.txt includes TqSdk, Waitress and retained AKShare dependencies; install it in the Python environment. Cloud deployments use Python 3.11 venv instead of the macOS Pixi lock.

**Application creation has side effects:** it initializes the database/admin record and prunes expired market bars. For isolated checks, use `create_app(config={"DATABASE": temporary_path, "TESTING": True})`. Never point tests at the real runtime database. `TESTING` does not bypass authentication.

## Code map

| Location | Responsibility |
| --- | --- |
| `app.py` | Application instance and local startup |
| `marginlab/__init__.py` | Application factory, main routes, request validation, configuration, JSON errors |
| `marginlab/analytics.py` | Input validation, ATR, correlations, HV, causal residual model, return visits and drift |
| `marginlab/data.py` | Demo generation, CSV parsing, AKShare daily ETF/QVIX alignment, JSON serialization |
| `marginlab/pricing.py` | Dividend-aware Black–Scholes Greeks and stress calculations |
| `marginlab/chips.py` | Chip blueprint, frame/regeneration APIs, process-local data and locking |
| `marginlab/chip_data.py` | Simulated transactions, chip distributions, price/MA series |
| `marginlab/ranking.py` | Retained competition-ranking analysis |
| `marginlab/auth.py` | Authentication, sessions, CSRF, rate limiting, security headers, password CLI |
| `marginlab/bootstrap.py` | Initial administrator password hash; do not expose credentials in documentation |
| `marginlab/storage.py` | SQLite connections, tables, initialization and small schema migrations |
| `marginlab/cache.py` | Daily market persistence, retention, refresh leases, timeout and fallback handling |
| `templates/base.html` | Shared layout and navigation |
| `templates/index.html`, `templates/views/*.html` | Route-selected research, pricing, method, and retained hidden market views |
| `templates/chips.html`, `login.html`, `settings.html` | Chip playback, login, administration |
| `static/js/common.js` | Authenticated fetch/CSRF and session-related browser behavior |
| `static/js/lab.js` | Research/pricing charts and interactions, retained optional market handlers |
| `static/js/chips.js` | ECharts playback, chart alignment, frame requests |
| `static/css/lab.css`, `chips.css` | Shared light design and chip-specific styling |
| `tests/test_research.py` | Numerical, research, data-validation, chip and API checks |
| `tests/test_auth_cache.py`, `tests/support.py` | Auth/cache tests and isolated authenticated test setup |
| `instance/margin.sqlite3` | Private runtime state, ignored by version control; preserve it |

## Pages and API contracts

Each major area has its own server-rendered route. Opening pricing must not initialize research charts or fetch research market data.

| Route | Purpose |
| --- | --- |
| `/` | Volatility research |
| `/pricing` | Pricing and stress testing |
| `/chips` | Chip playback |
| `/method` | Indicator explanations |
| `/settings` | GET settings form; POST saves retention/refresh settings |
| `/login` | GET login form; POST authentication |
| `/logout` | POST session revocation |
| `/api/research` | GET/POST research; live source requires POST |
| `/api/greeks`, `/option/greek` | Greeks JSON |
| `/api/stress`, `/option/pressure` | Single-contract stress JSON |
| `/api/frame`, `/api/regenerate` | POST chip frame and regenerated simulation |
| `/api/options/<kind>`, `/options/<kind>` | Retained `value`, `risk`, `history` tools |
| `/api/futures`, `/future/hv` | Retained futures daily historical volatility |
| `/api/rank`, `/hdoption/rank` | Retained ranking analysis |

Legacy `/correlation`, `/correlation/day`, `/option/vanna`, and `/option/volga` redirect to `/`. There is no exposed market page route/navigation entry, although `templates/views/market.html`, frontend handlers, and protected backend APIs remain. The options `history` endpoint returns the current value table, not a historical time series.

Research accepts `source`, `symbol`, `period`, `scenario`, `window`, `horizon`, `correlation`, `band`, `gap`, `lookback_days`, `start`, `end`, and `csv`. Sources are `demo`, `csv`, and `live`; symbols are `510050` and `510300`; periods are `5`, `15`, `30`, `60`, and `daily`. Live supports daily only. The response has `bars`, `visits`, `anchor`, and `meta`, including source, parameters, timestamps, notes, and cache status. Keep exports reproducible by preserving this metadata.

Validation errors return JSON 400 and provider/runtime errors JSON 503. Requests are limited to 4 MiB by default. Unauthorized APIs return 401; protected pages redirect to login.

## Data semantics

- Required input columns: `timestamp,open,high,low,close,vix`; `volume` is optional. Values must be finite, prices/QVIX positive, OHLC consistent, and timestamps unique. Research ordinarily requires 20–10,000 rows.
- `vix=20` means 20 index points, not decimal volatility 0.20. QVIX is an externally sourced index, not the IV of a single option.
- Timezone-aware timestamps are converted to Asia/Shanghai; naive timestamps are treated as exchange-local time. CSV import does not resample. The selected period must match the supplied completed bars.
- Live daily data uses unadjusted ETF prices and the corresponding AKShare QVIX series. Filter dates, remove identical duplicates, reject conflicting same-date rows, then join one-to-one on full dates. Never forward-fill missing QVIX or join different days by time of day.
- The current day is excluded before 15:00 Shanghai time. Unadjusted prices still have dividend effects. Intraday QVIX feeds without verifiable dates are deliberately unsupported.
- Demo data is deterministic simulation, not a market fallback. Its dates do not implement the exchange holiday calendar. Provider errors must never silently switch the source to demo.
- Undefined correlations, warmup values, and unavailable estimates serialize as JSON `null`, not fabricated zeros or nonstandard NaN/Infinity.

## Volatility research definitions

Let `r = diff(log(close))` and `q = diff(log(vix))`. Preserve both price-level correlation and log-return correlation; neither identifies the cause of a market move.

Historical volatility is the sample standard deviation of log returns, annualized and multiplied by 100. Annual observations are 252 for daily bars and `252 * 240 / minutes` for ETF intraday bars. ATR is a 14-bar simple rolling average of true range, not Wilder smoothing.

### Same-price return visits

The first anchor is established at the 14th bar using that bar's close and ATR. Both anchor and band width remain fixed. Defaults:

- Matching band: anchor ±0.25 ATR.
- An independent return requires an excursion of at least 1 ATR and a gap of at least 4 bars since the previous visit.
- Record only the first re-entry after an excursion; distinguish entry from above versus below.
- Compare with prior same-direction visits within the last 5 distinct trading dates present in the data; require at least 2 matches.
- `D = log(current QVIX) - median(log(matched previous QVIX))`.

The UI displays `100 * D`, **not a difference in QVIX points**. `exp(D) - 1` expresses the relative ratio change. Return-event anomaly thresholds use prior valid same-direction D observations, with at least 30 samples. This threshold history is distinct from the five-day visit-matching window. A subsequent same-direction independent event must confirm the same anomaly side. The tag belongs to the event; it is not an indefinitely active market alarm.

The anchor is tied to the start of the input dataset. Appending future rows preserves historical results, but changing the selected start date or removing old rows through retention can change the anchor and subsequent results. No automatic anchor relocation is implemented.

### Accumulated abnormal volatility

Fit `q = alpha + beta_down * min(r, 0) + beta_up * max(r, 0)` using only the previous `window` bars (default 60), excluding the current bar. Skip rank-deficient fits. The current residual is observed q minus the fitted response to the current r, so this is synchronous detection after a bar closes, not prediction of that bar.

`A` sums residuals over `horizon` bars (default 16). Thresholds are the 5th/95th percentiles of up to 250 **prior** A values, requiring 30 valid samples. Default confirmation requires 3 consecutive breaches. Keep the warmup state explicit. All training, thresholds, and event matching must avoid future information.

The current model does not correct small within-band price differences, stratify by time of day/return interval, control event/expiry/skew effects, or perform strategy backtests. Residuals can reflect model omissions. Do not relabel them as proven external intervention or automatically infer a profitable long/short volatility trade.

### Research presentation

The research page combines price/QVIX history, a time-colored price–QVIX phase trajectory, residual accumulation with thresholds, both correlations, return-event steps/table, HV comparison, and Vanna/Volga diagnostics. The phase plot and return visits reveal changes in QVIX at comparable price levels. Preserve source labels, units, warmup states, tooltips, and stale-data visibility when redesigning charts.

## Pricing, Vanna, Volga and stress

`pricing.py` retains Black–Scholes call/put calculations with continuous dividend yield: fair price, Delta, Gamma, Theta, daily Theta, Vega, Vanna, and Volga.

- `T` is in years; rates, dividend yield, and sigma are annual decimals.
- Theta is annual; `theta_day` is Theta / 365. Vega is per sigma change of 1.0, not per one percentage point.
- Taylor approximation: `P + dS*Delta + dSigma*Vega + 0.5*dS^2*Gamma + 0.5*dSigma^2*Volga + dS*dSigma*Vanna`.
- Stress uses seven relative spot shocks and seven absolute sigma shocks from −0.05 to +0.05. A sigma shock of +0.05 means +5 volatility percentage points. Skip scenarios with nonpositive resulting sigma. Compare exact and approximate prices/P&L at fixed maturity.
- Research Vanna/Volga charts use a call with strike equal to the first input close, constant 30-day maturity, 2% rate, zero dividend yield, and sigma = QVIX / 100. These are model proxies, not actual contract Greeks or an aging position.

The old full-option-chain Excel stress export and experimental self-computed VIX engine are not part of the current implementation.

## SQLite market cache and lifecycle

SQLite uses WAL and a connection timeout. Tables separate `admin`, `sessions`, `login_attempts`, `settings`, `market_bars`, and `market_sync`. Market rows are unique by symbol, period, and timestamp. Initialization is idempotent; schema changes currently live in `storage.py`, without a migration framework.

Only live daily research data is persisted. Demo/CSV input, chip simulations, and the retained options/futures/ranking APIs do not use this historical market cache.

- Default retention: 3 calendar months, configurable to 1–36 in settings. This is not a fixed 90-day interval. Stored settings override initial defaults.
- Refresh interval: default 900 seconds, configurable to 60–86,400. Recent successful data is reused; authenticated live research POST requests trigger due refreshes. There is no background polling scheduler.
- Initial fetch covers the retained range. Incremental fetch overlaps the latest saved date by 7 calendar days and upserts corrections. Increasing retention backfills missing coverage.
- Requests are clipped to retention/today and disclose clipping. Wholly expired ranges fail. Pruning runs at startup and relevant page/cache/settings operations; nothing runs while the application is stopped. Deleted SQLite pages need not immediately reduce file size.
- SQLite leases prevent duplicate refreshes for the same key. Failed attempts have a 60-second retry delay. Provider waits default to 20 seconds, with at most two unfinished worker slots per process.
- A timeout stops waiting, **not the underlying blocked provider thread**. Its slot remains occupied until it finishes; late results are not committed by the timed-out caller.
- On provider failure or concurrent refresh, at least 20 cached bars in the requested interval are required for fallback; otherwise return an error. Expose stale state, latest data date, last success/error, and effective range through `meta.cache`.

A recent successful request is not proof that the upstream's latest bar is current. Three months of daily data can be too short for default regression plus accumulation plus percentile warmup; keep “insufficient samples” visible or change windows/retention explicitly.

## Authentication and configuration

All research pages and both current/legacy data APIs are protected by server-side authentication. Static assets and login are public. Authenticate before any protected upstream fetch. The single username is `admin`; bootstrap stores a scrypt hash, never a plaintext password. Existing credentials are not overwritten on startup.

Sessions use a random opaque cookie (`margin_session`); SQLite stores only its SHA-256 token hash. Login rotates the anonymous session; logout revokes the authenticated session. Sessions also bind to the current credential hash, so obsolete credentials cannot retain valid sessions. Defaults are a 10-minute anonymous login session, 30-minute authenticated idle timeout, and 8-hour absolute lifetime. Restarting the server does not extend expiration.

Cookies are HttpOnly, SameSite=Lax, host-only, and Path=/. Secure is configurable for HTTPS. Unsafe methods require session-bound CSRF via `X-CSRF-Token` or form field, with Origin/Fetch Metadata checks. `common.js` supplies the fetch helper and handles 401 responses and browser back/forward restoration. Do not store passwords or bearer credentials in localStorage.

Login attempts are transactionally limited in SQLite to 5 per IP and 30 globally per 15 minutes. The application does not trust forwarded IP headers by default. Nonstatic responses use private/no-store caching and defensive headers. These controls do not replace HTTPS or constitute a comprehensive XSS defense.

```sh
# Interactive password change; requires at least 12 characters and revokes sessions:
.pixi/envs/default/bin/python -m flask --app app set-admin-password
```

| Setting | Meaning/default |
| --- | --- |
| `PORT` | Local startup port, 5000 |
| `MARGIN_DATABASE` | Database path, `instance/margin.sqlite3` |
| `MARGIN_HISTORY_MONTHS` | Initial retention, 3; persisted settings take precedence |
| `MARGIN_COOKIE_SECURE=1` | Send session cookie only over HTTPS |
| `MARGIN_PUBLIC_ORIGIN` | Exact public origin for origin validation |
| Factory `MARKET_REFRESH_SECONDS` | Initial refresh interval, 900 |
| Factory `MARKET_RETRY_SECONDS`, `MARKET_TIMEOUT` | Retry delay 60 seconds, provider wait 20 seconds |
| Factory `AUTH_IDLE_TIMEOUT`, `AUTH_MAX_AGE` | Session limits in seconds, 1800 / 28800 |

Public deployment requires a strong changed password, HTTPS, a production WSGI server, and correct secure-cookie/origin configuration. This remains a single-admin application without MFA. New private runtime directories/files use restrictive permissions; never publish or casually delete the database or its backups.

## UI and chip-playback invariants

Use Chinese product labels, a unified light palette, white panels, teal primary accents, orange secondary accents, and shared sidebar navigation. Do not reintroduce extra arrow decorations on navigation links. Keep research and pricing as separate routes. Hidden market functionality should stay hidden unless requested.

Chip playback retains three display schemes, time layers, MA, playback controls, and existing API shape. Both price and chip distribution share the same price axis; preserve volume conservation including maximum-price and final-time boundaries. Frontend request sequencing prevents stale frames from replacing newer ones.

Chip transactions are generated simulation held in process memory behind a lock. Regeneration affects that process's shared dataset; it is not per-session, persisted, or synchronized across WSGI workers. Keep this distinction explicit in future real-data or multiworker designs.

## Preservation and historical cleanup

The repository has already removed obsolete `python/` and `legacy/` trees, duplicate entry points, old generated images/spreadsheets/pickle files, and obsolete dependencies. Chip generation moved into `marginlab/chip_data.py`; chip assets are now `chips.js` and `chips.css`.

Retained capabilities include HV, correlations, drift, all listed Greeks including Vanna/Volga, Taylor/stress calculations, chip playback, and hidden market/ranking implementations. The experimental old option-chain VIX calculation was not migrated because its formulas needed correction. Do not equate externally sourced QVIX with preservation of that old implementation.

Current files are not a complete archival copy of every original source or export. Confirm version-control history or backups before promising restoration. Do not delete retained hidden code merely because it has no current navigation link. Preserve `instance/`, `.pixi/`, and user tooling/configuration directories during any future cleanup unless explicitly authorized.

## Verification and future changes

Use the existing unittest suite for computational, API, authentication, or cache changes. It covers causal prefix behavior, drift direction and visits, numerical Greeks, dividend parity, finite JSON, chip boundaries, input alignment, session/CSRF/revocation/rate limiting, and cache TTL/incremental/fallback/retention behavior. Test support creates a temporary database and performs real login; do not add an auth bypass for tests.

External-provider and visual checks are separate from unit tests. README records a historical 50ETF/QVIX alignment check, but this does not guarantee present provider availability. Real browser visual/click acceptance and individual hidden-provider endpoint checks remain incomplete in the documented handoff. Do not claim fresh validation without running it.

For future design, keep these unresolved areas explicit: dated intraday ingestion, anchor lifecycle across rolling windows, within-band adjustment, standardized expiry/Delta skew measurements, walk-forward validation with costs, persistent chip datasets for multiple workers, and stronger cancellation of blocked providers. None is currently implemented. Any background refresh design must preserve the user's requirement that market refresh occurs only after authentication.

Update this guide when routes, units, data ownership, auth, retention, or feature availability changes. A focused change should normally require reading only the modules identified above, their relevant tests, and the corresponding template/script.
