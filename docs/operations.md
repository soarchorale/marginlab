# Runtime and storage conventions

## Startup

```sh
cd /path/to/marginlab
PORT=5001 .pixi/envs/default/bin/python app.py
```

The local and production startup entry points explicitly enable autonomous collection. A plain WSGI import does not. Weekday subscriptions run from 09:20 to 15:10 in Asia/Shanghai, including lunch, for 50/IH/HO, 300/IF/IO, 1000/IM/MO, and 500/IC. IC has no matching index options, so product 500 has no MVIX.

The supervisor checks every five seconds and retries failures after at least 60 seconds. A SQLite lease prevents duplicate collectors. Completed minute bars are upserted approximately every five seconds; option quotes and MVIX are saved at minute boundaries. Startup/reconnect can import the latest 8,000 available minute bars without deleting older local history.

Weekdays are a subscription upper bound, not a complete exchange holiday calendar. Without a recent index heartbeat, no new holiday/disconnected-feed MVIX minutes are generated. Retention runs hourly, at startup, and during relevant page requests.

`MARGIN_AUTOCOLLECT=0` restores authenticated page-demand mode locally. `create_app()` stays network-inert for tests and offline tools. Page/API authentication is always required; browser logout does not stop autonomous collection.

No local cron, launchd, or startup task is installed automatically. Keep the service, machine, and network available. Explicitly install the Linux systemd template for startup/recovery as described in [Deployment](deployment.md).

## Storage and read/write separation

In autonomous mode, dashboard APIs query SQLite rather than synchronously subscribing on a cache miss. The collector continues retrieving new data even when history already exists. Missing data remains empty until written. Demo, CSV, and pricing are separate paths.

The default database is `instance/margin.sqlite3`, using WAL, transactions, unique keys, and mode 0600.

| Table | Contents |
| --- | --- |
| `tq_bars` | Index/actual futures minute OHLC, volume, open interest; unique symbol/timestamp; accepts bar corrections |
| `tq_option_minutes` | Per-option minute-end bid/ask and sizes, original quote timestamp, expiry, strike, side, source; unique product/symbol/timestamp |
| `tq_snapshots` | MVIX, quality, method, rate, expiration weights, quote evidence; unique product/timestamp; valid values immutable |
| `tq_status` | Latest actual contract, price, quote time, and synchronization state |
| `tq_history_jobs` | Latest explicit historical backfill and coverage limits |
| `tq_lease` | Collector ownership and expiry |
| `settings` | Retention and administrator settings |
| `admin`, `sessions`, `login_attempts` | Authentication data |

`market_bars` and `market_sync` are legacy AKShare compatibility tables. The current feed does not save every option's traded OHLC, all expirations, or all raw ticks; it saves the two MVIX terms' minute quote evidence. Futures contracts are not spliced across rolls.

## Retention and aggregation

`history_months` defaults to three calendar months, configurable to 1–36. Minute bars, option quote minutes, and MVIX share the retention cutoff. Deleting rows does not necessarily shrink the database file immediately.

Local queries are not capped at 10,000 SDK rows. Selected dates and retention apply before aggregation. Local minute data produces 5/15/30/60-minute and daily bars; daily aggregation requires 240 complete minutes. Daily MVIX takes the 15:00 observation. Some raw chart panels display only the latest 1,500 bars; research uses the selected available range.

TqSdk supports daily-bar requests. The measured limitation is permission for dated historical Tick downloads, not the absence of daily K lines. Retention does not imply complete historical coverage. Data accumulates while collection runs.

Downtime/network loss/sleep can create unrecoverable quote gaps. Current quotes or trade closes cannot reconstruct historical bid/ask. Explicit `scripts/backfill_mvix.py` can recover only actual common coverage from recent 10,000-Tick serials. Script defaults follow the project root; manifests follow the chosen database directory. Explicit relative CLI paths remain caller-relative.

## Quote sampling and acceptance

Historical reconstruction verifies contiguous Tick IDs and uses the last record strictly before a minute boundary within the same trading session. Unchanged quotes do not expire just because 600 seconds elapsed. Later ticks bound coverage but never provide past prices. Invalid updates cannot be skipped. Lunch/overnight carry-forward is disabled.

Live sampling requires an index heartbeat no older than ten seconds to reuse unchanged option quotes. Without it, no new minute snapshot is saved. Startup snapshots without a heartbeat retain a 600-second age cap. Missing or insufficient observations are explicit; valid MVIX is reused.

Verified 2026-11-06 backfill: 50 has 124 observations (11:27–15:00 excluding lunch), 300 has 105 (13:16–15:00), and 1000 has 94 (13:27–15:00). These windows have no missing trading minutes but do not cover the entire trading day.

Confirm autonomous-collection status after startup and actual quote timestamps, completed bars, and MVIX quality during trading. A connection alone does not prove valid data at every minute.
