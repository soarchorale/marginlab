# marginlab

A private options research workbench using Flask, NumPy/Pandas, SQLite, and native SVG charts. It combines index futures monitoring, quote-based MVIX, volatility drift research, option pricing/stress tests, and a neutral minute-volume profile. It does not place orders.

In the examples below, `/path/to/marginlab` represents your local checkout; replace it with the actual project directory. The project and Python package are both named `marginlab`. Scripts derive default resource paths from their own location, not from the caller working directory. Explicit relative CLI arguments remain caller-relative.

## Run locally

```sh
cd /path/to/marginlab
.pixi/envs/default/bin/python app.py
# Default: http://127.0.0.1:5000
PORT=5001 .pixi/envs/default/bin/python app.py
```

Alternatively use `pixi run dev`. Pixi targets macOS ARM64. Moving an installed environment can leave generated command shebangs referencing the old directory; recreate it with Pixi if those commands fail rather than editing vendor binaries.

Standalone Python 3.11 installation:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

There is one dependency file, `requirements.txt`. Cloud deployment uses `scripts/setup-server.sh` and `scripts/start.sh`, with Waitress and an optional systemd service. See [Deployment](docs/deployment.md) for initialization, HTTPS, and SQLite migration.

## Live data and collection

TqSdk supplies 50 (IH/HO), 300 (IF/IO), 1000 (IM/MO), and 500 (IC). Product 500 has no matching index options and therefore no MVIX research. Legacy AKShare implementations are retained but their old online endpoints return HTTP 410.

The default background collector subscribes to all four products on weekdays 09:20–15:10 in Asia/Shanghai, retries failed connections, and incrementally writes SQLite. Login protects page/API access, not collection. Closing the browser does not stop collection. `MARGIN_AUTOCOLLECT=0` restores session-demand mode. The server, machine, and network must remain available; no local OS startup task is installed automatically. See [Operations](docs/operations.md).

## Research semantics

- MVIX uses the last known bid/ask midpoint before each completed minute boundary, ACT/365, and total-variance interpolation between expirations bracketing 30 days. It is not an official Cboe index. The default assumed annual risk-free rate is 2%, configurable with `MVIX_RATE`.
- Within verified contiguous historical Tick coverage, unchanged quotes do not expire after ten minutes. No future prices, trade-close substitution, or cross-session carry-forward is used. Invalid updates cannot be skipped to reuse older valid quotes.
- Live sampling requires an index heartbeat no older than ten seconds. Missing/invalid observations remain gaps. Valid persisted MVIX values are reused.
- Retention defaults to three calendar months, configurable to 1–36. This does not mean the existing data covers three complete months.
- Local minute bars aggregate into 5/15/30/60-minute and daily bars. Daily bars require 240 completed trading minutes. Actual futures contracts remain separate across rolls.
- The volume profile assigns minute volume to minute close. It does not infer trade direction or actual holder cost.
- Demo data is explicitly simulated. CSV requires `timestamp,open,high,low,close,vix`, with optional volume. Naive timestamps mean Asia/Shanghai. Volatility uses index points, not decimals. CSV sampling must match the selected period.

Drift analysis retains level and log-return correlations. The fixed anchor starts at bar 14 using ATR(14). Independent visits require a one-ATR excursion and at least four bars of separation. Same-direction visits use five observed trading dates and at least two historical matches. Displayed drift is `100 * (log(V) - median(log(previous V)))`.

The residual model fits asymmetric price responses using the previous 60 bars, sums residuals over 16 bars, and applies thresholds estimated from prior observations only. Warmup remains explicit. These labels are descriptive research hypotheses, not causal explanations or trading recommendations. No strategy backtest, standardized skew confirmation, automatic anchor relocation, or transaction-cost evaluation is implemented.

Pricing retains dividend-aware Black–Scholes Greeks including Vanna/Volga, exact scenario repricing, and a second-order Taylor approximation. A volatility shock of 0.05 means five percentage points. Model sensitivity charts are proxies, not actual position Greeks.

## Historical backfill

```sh
.pixi/envs/default/bin/python scripts/backfill_mvix.py
.pixi/envs/default/bin/python scripts/backfill_mvix.py --offline
```

Defaults follow the script's project root. Manifests are stored beside the selected database under `tqsdk-verification/history/`. The verified account supports recent 10,000-Tick buffers, not dated historical Tick downloads. Backfill uses current bracketing terms and actual shared coverage; it does not reconstruct expired contracts or arbitrary three-month histories. TqSdk does support daily bars. See [SDK verification](docs/tqsdk-volatility-review.md).

## Authentication and persistence

The administrator username is `admin`; passwords are scrypt-hashed. Cookies contain opaque tokens; SQLite stores their hashes. Sessions expire after 30 idle minutes or eight hours absolutely. Logout and password changes revoke sessions. Unsafe requests require CSRF and origin checks. Cookies are HttpOnly and SameSite=Lax; HTTPS deployment enables Secure cookies.

The production initializer prompts for hidden TqSdk and administrator passwords. Private configuration, runtime databases, diagnostics, and migration backups stay under ignored `instance/`. Never publish that directory or root `tqauth.json`.

```sh
.venv/bin/python -m flask --app app set-admin-password
```

Use `scripts/backup_sqlite.py` rather than copying a live WAL database's main file directly.

## Code map and APIs

| Path | Responsibility |
| --- | --- |
| `app.py` | Local server and collector startup |
| `marginlab/tqfeed.py` | Subscriptions and autonomous scheduling |
| `tqstore.py`, `history.py`, `cache.py` | SQLite persistence and retention |
| `mvix.py`, `analytics.py` | MVIX and causal drift calculations |
| `market.py`, `pricing.py` | Dashboard queries, Greeks, and stress tests |
| `auth.py`, `deployment.py` | Authentication and private server configuration |
| `templates/`, `static/` | Chinese browser copy and frontend assets |
| `scripts/`, `deploy/`, `tests/` | Deployment, diagnostics, and isolated tests |

Routes: `/`, `/pricing`, `/chips`, `/method`, `/settings`, `/login`, and POST `/logout`. Live research uses authenticated POST `/api/research`; volume profiles use POST `/api/chips`. Pricing APIs include `/api/greeks` and `/api/stress`. Hidden legacy code remains available in the repository.

## Verify and release

```sh
.pixi/envs/default/bin/python -m unittest discover -s tests -q
.pixi/envs/default/bin/python scripts/build_release.py
.pixi/envs/default/bin/python scripts/backup_sqlite.py --output instance/margin-transfer.sqlite3
```

Tests use temporary databases without authentication bypasses. Release packaging uses a source allowlist, excluding private/runtime files and environments. Existing backup targets are never overwritten; choose a fresh filename for later backups. Linux/systemd/TLS and trading-session operation require verification on the actual target host.

Documentation, comments, CLI prompts, and application-owned logs use English. Browser copy, UI state labels, and upstream schema keys retain their original language.
