# Known issues and deferred work

Reviewed on 2026-11-14. Findings describe the inspected source, not a production load-test result.

## Deferred historical MVIX repair

The user will supply replacement historical data later. Preserve the existing database and valid snapshots. Do not interpolate over these gaps or substitute current quotes or trade closes. No further provider backfill is requested at this stage.

Audit of the local SQLite database on 2026-11-13:

| Product | Missing MVIX minute endpoints on 2026-11-09 (Asia/Shanghai) | Valid minutes |
| --- | --- | --- |
| 50 / HO | 09:31-11:22 | 128 |
| 300 / IO | 09:31-11:30 and 13:01-13:19 | 101 |
| 1000 / MO | 09:31-11:30 and 13:01-15:00 | 0 |

All four indices and futures have 240 minute bars on 2026-11-09. The 50 and 300 gaps reflect incomplete historical quote-chain coverage in the recent 10,000-Tick buffers. The 1000 import stopped after 32 of 142 contracts, leaving a stale `running` job status. November 6 also has partial MVIX coverage: 50 starts at 11:27 (124 values), 300 at 13:16 (105 values), and 1000 at 13:27 (94 values). There is no corresponding IC index-option MVIX series.

When replacement data arrives, validate contract identity, expiry, strike, option side, quote timestamps/timezone, bid/ask prices and source coverage before reconstructing minute-end MVIX. Keep provenance, avoid future quotes, and audit any conflict with existing records before replacing them.

## Architecture and capacity review

- Standard entry points start the collector in background threads within the same process as Flask/Waitress. They do not isolate process failure, CPU pressure or database contention. Factory-only WSGI startup does not enable automatic collection and retains legacy demand-driven behavior.
- The production entry point uses one Waitress process with four request threads. `app.py` is the local Flask development server. There is no measured concurrent-user capacity.
- In automatic mode, market requests read SQLite rather than opening provider connections, but they are not read-only transactions: authentication deletes expired records and updates session activity, and research requests call retention pruning.
- Research requests reload the selected range and recalculate resampling, rolling regressions and other diagnostics. Identical users do not share a cached result. The 1,500-bar display cap does not limit the full research computation or all response arrays.
- SQLite uses WAL and a 10-second connection lock timeout. Shared write traffic can still block requests and the collector. Reads, session state, history maintenance and market writes share one database file.
- The collector supervisor checks whether its worker is alive; it lacks an independent progress watchdog to recover a live but stuck worker. Data completeness and stale backfill-job detection are not yet implemented.
- The scheduler uses weekdays and a time window, not an exchange holiday calendar. Starting after the window does not automatically synchronize the last session.
- Authentication currently provides one administrator account, not a multi-user permission model.

## Suggested implementation order

1. Move retention/session cleanup out of per-request handlers; throttle session activity writes while preserving expiration semantics.
2. Cache analysis by product, period, query range, model parameters and data revision. Coalesce identical computations and invalidate on backfill as well as new data. Bound computation and paginate/downsample display data without silently changing research semantics.
3. Introduce separate collector and web service entry points, with web workers explicitly forbidden to start provider subscriptions. Persist shared collector health and progress; add watchdogs and interrupted-job recovery.
4. Add freshness/coverage monitoring, explicit after-hours catch-up, request timeouts and production load tests with a full retained dataset, simultaneous collection, and authenticated clients.
5. If multi-user scale requires it, separate shared caching/session storage and evaluate PostgreSQL for multi-host deployment. Do not share a WAL SQLite file over a network filesystem.
