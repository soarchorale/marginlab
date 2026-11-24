# Runtime and storage conventions

## Startup

```sh
cd /path/to/marginlab
PORT=5001 .pixi/envs/default/bin/python app.py
```

The local and production startup entry points explicitly enable autonomous collection. A plain WSGI import does not. Weekday subscriptions run from 09:20 to 15:10 in Asia/Shanghai, including lunch, for 50/IH/HO, 300/IF/IO, 1000/IM/MO, and 500/IC. IC has no matching index options, so product 500 has no MVIX.

The supervisor checks every five seconds and retries failures after at least 60 seconds. A SQLite lease prevents duplicate collectors. Completed minute bars are upserted approximately every five seconds; option quotes and MVIX are saved at minute boundaries. Startup/reconnect can import the latest 8,000 available minute bars without deleting older local history.

Weekdays are a subscription upper bound, not a complete exchange holiday calendar. Without a recent index heartbeat, no valid MVIX is generated; raw evidence and explicitly invalid samples may still be recorded. Retention runs hourly and at startup, outside page requests.

`MARGIN_AUTOCOLLECT=0` restores authenticated page-demand mode locally. `create_app()` stays network-inert for tests and offline tools. Page/API authentication is always required; browser logout does not stop autonomous collection.

No local cron, launchd, or startup task is installed automatically. Keep the service, machine, and network available. Optional service management is described in [Deployment](deployment.md); this update does not enable systemd.

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

`history_months` defaults to one calendar month, configurable to 1–36. Minute bars, option quote minutes, and MVIX share the retention cutoff. Deleting rows does not necessarily shrink the database file immediately.

Local queries are not capped at 10,000 SDK rows. Selected dates and retention apply before aggregation. Local minute data produces 5/15/30/60-minute and daily bars; daily aggregation requires 240 complete minutes. Daily MVIX takes the 15:00 observation. Charts and research use the full selected available range.

TqSdk supports daily-bar requests. The measured limitation is permission for dated historical Tick downloads, not the absence of daily K lines. Retention does not imply complete historical coverage. Data accumulates while collection runs.

Downtime/network loss/sleep can create unrecoverable quote gaps. Current quotes or trade closes cannot reconstruct historical bid/ask. Explicit `scripts/backfill_mvix.py` can recover only actual common coverage from recent 10,000-Tick serials. Script defaults follow the project root; manifests follow the chosen database directory. Explicit relative CLI paths remain caller-relative.

## Quote sampling and acceptance

Historical reconstruction verifies contiguous Tick IDs and uses the last record strictly before a minute boundary within the same trading session. Unchanged quotes do not expire just because 600 seconds elapsed. Later ticks bound coverage but never provide past prices. Invalid updates cannot be skipped. Lunch/overnight carry-forward is disabled.

Live sampling requires an index heartbeat no older than ten seconds to reuse unchanged option quotes. Without it, evidence may be saved but cannot become a valid minute snapshot. Startup snapshots without a heartbeat retain a 600-second age cap. Missing or insufficient observations are explicit; valid MVIX is reused.

Verified 2026-11-06 backfill: 50 has 124 observations (11:27–15:00 excluding lunch), 300 has 105 (13:16–15:00), and 1000 has 94 (13:27–15:00). These windows have no missing trading minutes but do not cover the entire trading day.

Confirm autonomous-collection status after startup and actual quote timestamps, completed bars, and MVIX quality during trading. A connection alone does not prove valid data at every minute.


## Collection monitoring and lightweight coverage (November 17 update)

The existing `python app.py` and `python scripts/serve.py` entry points retain their startup behavior. With automatic collection enabled (the default), the existing collector supervisor also publishes progress and performs maintenance. No additional process, cron job, service manager, or package is required. Factory-only WSGI startup does not start this maintenance loop. For `app.py`, `MARGIN_AUTOCOLLECT=0` disables it; the production script explicitly starts it. Keep one application process for this deployment; the cache and session activity buffer are process-local.

## Progress and coverage

Progress is published every 15 seconds. Reports distinguish worker-loop progress, received market timestamps, persisted index/futures bars, option evidence and the latest valid MVIX. During continuous trading, a 90-second stalled worker/quote threshold and 120-second completed-bar lag threshold apply, with opening grace. Lunch, closing auction and closed periods are classified separately. A stale monitor is detected after 45 seconds. These are diagnostics, not automatic termination of a stuck SDK thread; existing exception/retry behavior remains.

The same supervisor checks coverage on startup and every five minutes, plus the first cycle at or after 15:10 Shanghai time. Each pass handles today and a bounded backlog (up to four days); startup seeds up to seven recent stored days and later passes pick up imported/changed days. A SQLite lease avoids overlapping maintenance owners. Normal checks use only completed minute endpoints older than 30 seconds.

The expected weekday grid is 09:31–11:30 and 13:01–15:00 (240 endpoints). Stored minute-start bars are shifted by one minute for comparison. Futures coverage is the union of stored actual contracts, with per-contract counts; it does not certify every listed contract. Option-minute presence does not establish full-chain coverage. Reports separately count valid MVIX, authorized reference estimates, invalid samples, auction records and display carries. IC has no index-option MVIX expectation.

This first version does not include an exchange holiday calendar. Reports explicitly mark the weekday assumption and whether bars provide trading-day evidence. It does not prove price correctness, quote synchronization or complete strike/expiry coverage, and does not repair gaps. Coverage details are persisted in SQLite and emitted as daily `coverage_audit` log events; the research page no longer renders the operational panel.

Each audit reads one SQLite transaction snapshot. Market-table triggers record a revision and dirty day in the same transaction as inserts, changes and deletes, including direct SQL imports. Reports store that revision; subsequent writes mark the report stale until a later check. This provides a coherent audit of a specific database version, not a guarantee that upstream data is complete.

## Quote evidence

Minute-end quotes remain bid/ask evidence, not trade-close substitutes. Invalid timestamps, stale index heartbeats, previous-session quotes and auction observations never become valid merely because they were stored. Raw eligible and unverified evidence is retained in `tq_option_minutes` with distinct sources. Continuous snapshots preserve their exact masked calculation inputs. Historical reconstruction excludes unverified and auction evidence. Closing-auction MVIX carry remains a labeled display-only operation, not a stored valid observation.

## Request work and maintenance

Retention runs at startup and hourly in the existing supervisor; deployment retention changes take effect on restart. Session/expired-login-attempt cleanup runs with maintenance, outside page handlers. Authentication still checks database revocation, absolute/idle expiry and credentials on every protected request. Activity persistence is throttled to 60 seconds, with a bounded 4,096-session memory buffer. A process restart or buffer eviction may lose up to 60 seconds of activity and expire a session slightly early; it cannot extend absolute expiration or restore a revoked session.

Analysis cache limits: 16 entries, 32 MiB of serialized payloads, 300-second TTL, two distinct computations at once, and a 20-second wait for an identical running computation. These are current implementation defaults. Busy requests receive a retryable error. The owner computation is not forcibly cancelled after 20 seconds. Serialization, response copies and in-flight analysis consume additional memory outside the cache byte limit.

Keys include selected parameters, resolved date bounds, retention, interest rate, actual futures contract and market revision. Revisions cover collection and historical repair. Analysis reads a consistent SQLite snapshot; a version race retries up to three times. Health, coverage, jobs and collection status are refreshed outside the cached analysis. Metadata-only polls preserve unchanged charts; collection health and coverage details are reported through daily logs. No concurrency capacity claim is made without a production load test.

## Database and release

Initialization adds five small operational tables (`tq_data_revision`, `tq_dirty_days`, `tq_health`, `tq_coverage`, `tq_maintenance_lease`), nine market revision triggers, and timestamp indexes. Existing market tables and stored historical MVIX are preserved. Old coverage/dirty-day metadata follows retention. The global revision deliberately invalidates all products conservatively and adds write overhead; measure this before a much larger deployment.

Before a release, create a consistent backup using `python scripts/backup_sqlite.py --output /path/to/backup.sqlite3`. Stop the old process, install the approved version and restart with the existing command. Do not copy only the live main SQLite file while WAL writes are active. Do not deploy or share a live WAL database over a network filesystem. Local testing does not modify the remote production service.


## Local wake and restart recovery

After a persistence pause longer than 90 seconds, the collector saves all valid completed bars still available in its subscribed 8,000-bar serial, instead of only the latest ten. This can recover K lines but cannot recreate missing historical bid/ask snapshots. `app.py` now handles SIGTERM and waits briefly for collector/supervisor shutdown; a hard kill still leaves the existing lease to expire. The production entry point already handles graceful termination.

The progress panel explicitly marks the latest insufficient MVIX sample even when quote reception is healthy. No invalid bid is converted into an observed zero bid. Local sleep suspends collection regardless of page availability; leave the computer awake for uninterrupted local testing.


## Production collection and local replicas

Production keeps automatic collection enabled. Local development now uses the remote service as its authoritative market source. Set `MARGIN_MARKET_READ_ONLY=1` to prevent both scheduled and page-demand provider subscriptions. The local SQLite setting `market_read_only=1` also enforces this mode, including after restarting without the environment variable. Either switch being enabled is sufficient. To intentionally restore collection, clear both switches. `MARGIN_AUTOCOLLECT=0` alone is not read-only: it permits demand-driven collection.

The supervisor may still run local cleanup and coverage audits in replica mode. Its status describes the local replica, not the remote process. A local coverage gap never initiates provider backfill. No scheduled synchronization job is installed; repeat the explicit snapshot/import workflow when fresh local data is needed.

On the production host, export a consistent snapshot of the four market tables. Transfer the private export over SSH/SCP. It contains the export timestamp and table counts and excludes accounts, sessions and collector leases.

```sh
python scripts/sync_market.py export --database instance/margin.sqlite3 --output /path/to/market.sqlite3 --months 1
```

For routine development sync, stop the local preview and run these commands on the initialized local replica. Use a new backup path for each apply.

```sh
python scripts/sync_market.py merge --database instance/margin.sqlite3 --source /path/to/market.sqlite3
python scripts/sync_market.py merge --database instance/margin.sqlite3 --source /path/to/market.sqlite3 --apply --backup /path/to/local-before.sqlite3
```

The first command previews new row counts, the first and last new timestamps, conflict counts and up to five example keys per table. Apply requires the persisted replica flag and an idle collector. It inserts missing keys in one transaction and keeps existing bars, quote evidence and snapshots on conflicts, including locally recovered values. Conflicting snapshots require a separate review even when the incoming quality is higher. Product status advances when its incoming quote time is newer. Repeating an export merge inserts zero additional observations. Triggers invalidate analysis revisions and coverage for changed days. The local retention policy still applies at service startup and during maintenance.

For first-time initialization or intentional full replacement on a stopped local instance, use `python scripts/sync_market.py import --replace --database instance/margin.sqlite3 --source /path/to/market.sqlite3 --backup /path/to/local-before.sqlite3`. Replacement resets local market tables and coverage, health and backfill metadata. It retains local authentication, enables replica mode and sets one-month retention.

One calendar month is the default retention. Existing databases with an explicit `history_months` setting keep that setting until changed. The November 17 configuration change sets it to `1` on production and the local replica. Production code and process are not redeployed or restarted for this setting; existing scheduled pruning applies it. This is a rolling calendar-month cutoff, not a fixed 30-day duration.


Replica coverage uses the export time as its upper bound and keeps the actual audit time separately. Minutes after that snapshot are not reported as lost provider data. Do not copy a local replica database back to production: it contains the local `market_read_only=1` setting. Production retains its own database and settings during a code release. If intentionally enabling a former replica, set `market_read_only` to `0` and remove or disable `MARGIN_MARKET_READ_ONLY` before restarting.


## MVIX v1.1 peripheral null-quote handling

`MVIX-1.1-mid-ACT365-bracket30-null-strikes` validates the parity forward and K0 pair before removing peripheral strikes with null/non-finite quotes from both option sides. Strike-spacing weights are recomputed from the remaining selected strikes. Missing central pairs, crossed selected quotes, unverified timestamps and insufficient remaining wings still fail. A null quote is never converted to zero. Explicit zero-bid/zero-ask tail handling is unchanged, as is the project's conservative requirement for two usable OTM strikes on each side.

This follows the ordering in Cboe Volatility Index Mathematics Methodology, section 3(a)(ii)–(iv): https://cdn.cboe.com/resources/indices/Cboe_Volatility_Index_Mathematics_Methodology.pdf. MVIX remains a local monthly-contract adaptation, not the official VIX benchmark. Changes in selected strikes can affect the index independently of changes in quoted prices; term details retain excluded strikes, reasons, selected strikes and method version.

For a scoped offline audit, use inclusive Shanghai dates and one or more products. The example supplies the 2% legacy rate only for rows lacking a recorded rate. Inspect recovered keys, audit totals and rejection reasons before applying.

```sh
python scripts/repair_saved_mvix.py --database instance/margin.sqlite3 --product 300 --product 1000 --start 2026-11-17 --end 2026-11-17 --legacy-rate 0.02 --report /path/to/preview.json
python scripts/repair_saved_mvix.py --database instance/margin.sqlite3 --product 300 --product 1000 --start 2026-11-17 --end 2026-11-17 --legacy-rate 0.02 --preview-report /path/to/preview.json --apply --backup /path/to/before.sqlite3 --report /path/to/applied.json
```

Apply checks the scope, method, rate, recovered entries and hash of the candidate rows against the preview. It creates a consistent backup, checks each original row again under the write transaction and aborts if it changed. Use new filenames and repeat the first command with a fresh report path to verify zero remaining candidates. An unscoped audit is available for inspection.

The repair never changes raw quote JSON, existing valid results or authorized reference estimates. Repaired rows keep the prior failure details, quote hash, rate provenance and new method version. Missing entire minutes are not fabricated. Expired, future, cross-session, stale-heartbeat and masked-timestamp observations are excluded from repair. Repeated repair is idempotent. Chart tooltips identify reconstructed observations.

Use a remote-sourced replica or disposable copy for rehearsals. A production release and a production data repair are separate operator actions. Routine incremental merges preserve local derived repairs.

## Daily application logs

Both `python app.py` and `python scripts/serve.py` configure structured JSON-line logs in `instance/logs/marginlab-YYYY-MM-DD.log` by default (next to the configured database). Dates and timestamps use Asia/Shanghai. Rotation happens on the first event of each new day; the same operation removes application log files older than the configured retention. `MARGIN_LOG_RETENTION_DAYS` defaults to 30, independently of market-data retention. `MARGIN_LOG_DIR` overrides the directory; relative paths resolve from the repository root. New directories/files use permissions 0700/0600. Use one service process per log directory.

Events include `service_started`, `service_stopping`, `http_request`, `collector_health`, `coverage_audit`, and collector/maintenance failures. Health is logged on status changes and at least every five minutes while the supervisor runs. Coverage audits retain their existing startup, periodic and post-close schedule, with per-product counts, missing timestamps and snapshot versions. The operational panel has been removed from the dashboard; the background checks remain active. Local replica checks still use the remote export timestamp.

Request logs contain the route template, method, status and duration, without query strings, request bodies, credentials or cookies. Provider failures log the exception class without the exception message. Application exception traces retain file basenames, line numbers and function names without exception values or source text.

```sh
# Run from the repository root; follow today's application log.
tail -f "instance/logs/marginlab-$(TZ=Asia/Shanghai date +%F).log"
```

After midnight, reopen the new daily file when following logs manually. Existing nohup/start commands still work: no additional process or cron is required. The nohup output may still contain Python startup failures or third-party console banners; routine application diagnostics belong in the daily files. File logging is not remote log shipping, and no process can write logs while stopped.


## Dashboard simplification and request transport

The default live research range is seven Shanghai calendar dates, including the end date. For example, November 18 selects November 12–18. The default bar interval remains one minute. Manual date selection still supports the configured retention window; the shorter display range does not prune stored history.

The dashboard keeps the latest selected-product quote timestamp in its heading, removes the four-product spot cards and operational prose, and retains data-quality provenance in chart tooltips. The settings page only edits the legacy daily-cache refresh interval; research polling remains 15 seconds. Retention submissions are rejected server-side. Set MARGIN_HISTORY_MONTHS at deployment to override the persisted retention setting; absent that override, existing installations retain their stored policy (default one month).

Research results remain in the bounded, version-keyed process cache for up to 300 seconds. Market revisions invalidate results immediately. Page navigation still makes an authenticated request and rebuilds charts; there is no browser-persisted research cache. A loading indicator appears only after 350 milliseconds. Research JSON uses gzip when accepted; login/settings responses are not compressed by this hook. Local November 17 baseline: about 0.46 seconds cold and 0.11 seconds warm, with a 6.1 MB uncompressed one-month payload. These are local measurements, not remote concurrency guarantees.

## Comparing supervisor and log progress

Run `python scripts/check_log_progress.py --database instance/margin.sqlite3` on the service host. Supply `--log-dir` if the daily logs are elsewhere. This on-demand command reads SQLite and the daily files independently of Flask and writes JSON to stdout. A nonzero exit indicates stale progress or a read failure. It checks every product's supervisor heartbeat (60 seconds) and its latest collector health event (15 minutes), including records across Shanghai midnight. An HTTP request logged in the meantime cannot mask a missing collector log. The check remains meaningful during lunch and weekends because the supervisor reports even without trades. Quote quality and provider coverage are separate checks.

For a maintenance failure, correlate the Shanghai timestamp, process and thread with nearby HTTP status and duration. SQLite exceptions include their numeric result code and symbolic name when available. Scheduler and maintenance failures include elapsed milliseconds. A long writer transaction can block a due session-activity update until its busy timeout. Schedule bulk work offline or in a quiet window. Revocation and session expiry continue to be checked on requests.
