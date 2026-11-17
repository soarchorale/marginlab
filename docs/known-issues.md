# Known issues and deferred work

Reviewed through 2026-11-17. Dated sections preserve historical findings; later implementation notes supersede them. Local changes are not production deployment or load-test evidence.

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

## Remote coverage audit (2026-11-14)

Production has complete 240-minute index/futures bar coverage for November 9-13, but MVIX remains incomplete. On November 9 each of 50, 300 and 1000 has 102 valid MVIX minutes. Deployment preserved the remote database. The subsequent local merge is recorded below.

The union of local and remote November 9 valid timestamps would cover 174 minutes for 50, 156 for 300 and 102 for 1000. This is a timestamp comparison only: overlapping values and quote provenance must be checked before import. The two datasets are complementary, not interchangeable backups.

The remote deployment uses nohup and automatic scheduled collection. It does not restart after a host reboot or process exit. A running HTTP listener alone does not prove fresh provider data. Check quote timestamps and persisted progress during the next trading session; weekends and times outside the configured weekday 09:20-15:10 window intentionally do not collect.

## Local market merge (2026-11-14)

A consistent, market-only export from production was merged into the local database after a full local backup. Production was not modified. Account, session, settings and lease tables were preserved locally. All 3,183 valid incoming MVIX values were recomputed against embedded quotes and matched. New local rows: 8,154 minute bars, 330,777 option-minute quotes and 2,951 snapshots (including insufficient-quality observations).

November 9 now has 174 valid MVIX minutes for 50, 156 for 300, and 102 for 1000; remaining gaps are 66, 84 and 138 minutes respectively. Stored bars now extend through November 13 at 14:59. Existing local valid snapshots were retained. Differences in 2,378 overlapping option-minute records and 103 snapshot records were logged, not silently overwritten; record differences do not necessarily imply differing MVIX values. Backup, report, merge script and conflict evidence are private artifacts under `instance/merge-audits/20261114-203539/`.

## Architecture and capacity review

- Standard entry points start the collector in background threads within the same process as Flask/Waitress. They do not isolate process failure, CPU pressure or database contention. Factory-only WSGI startup does not enable automatic collection and retains legacy demand-driven behavior.
- The production entry point uses one Waitress process with four request threads. `app.py` is the local Flask development server. There is no measured concurrent-user capacity.
- In automatic mode, market requests read SQLite without opening provider connections. As of the local November 17 update, cleanup is scheduled outside requests; authenticated reads still validate sessions against SQLite and periodically persist activity.
- Research results now share a bounded per-process cache keyed by parameters and transactional market revision. Identical concurrent computations coalesce. Full selected ranges remain available; large-range computation and browser rendering still need production load tests.
- SQLite uses WAL and a 10-second connection lock timeout. Shared write traffic can still block requests and the collector. Reads, session state, history maintenance and market writes share one database file.
- Persisted progress monitoring now distinguishes a stale monitor, stalled worker and delayed quotes. Lightweight time coverage is checked automatically. A stalled live thread is reported, not forcibly terminated; interrupted backfill-job recovery and quote-value validation remain deferred.
- The scheduler uses weekdays and a time window, not an exchange holiday calendar. Starting after the window does not automatically synchronize the last session.
- Authentication currently provides one administrator account, not a multi-user permission model.

## Earlier suggested implementation order (superseded by the November 17 status)

1. Move retention/session cleanup out of per-request handlers; throttle session activity writes while preserving expiration semantics.
2. Cache analysis by product, period, query range, model parameters and data revision. Coalesce identical computations and invalidate on backfill as well as new data. Bound computation and paginate/downsample display data without silently changing research semantics.
3. Introduce separate collector and web service entry points, with web workers explicitly forbidden to start provider subscriptions. Persist shared collector health and progress; add watchdogs and interrupted-job recovery.
4. Add freshness/coverage monitoring, explicit after-hours catch-up, request timeouts and production load tests with a full retained dataset, simultaneous collection, and authenticated clients.
5. If multi-user scale requires it, separate shared caching/session storage and evaluate PostgreSQL for multi-host deployment. Do not share a WAL SQLite file over a network filesystem.

## November 9 reconstruction diagnostics (2026-11-15)

After the local/remote merge, all remaining gaps fail the current full-candidate-chain gate. A second diagnostic preserves the known strike universe, leaves unavailable quotes null, and attempts the existing variance calculation. It yields 20 provisional values for 50 and 3 for 300. The other 46 gaps for 50 and 81 for 300 fail at K0; all 138 gaps for 1000 lack a usable call/put pair in at least one required term.

These 23 provisional values are not promoted to valid snapshots. Missing call/put pairs leave the minimum-price-difference forward-reference selection unverified. For example, 50 at 10:52 uses strike 2750 but lacks complete near-term pairs at 2700, 2800 and 2850. The 300 candidates at 13:17-13:19 lack the near-term pair at 4150. A computational result from a reduced reference universe is not proof of the historical complete-chain value. This audit does not prove that every missing contract is always necessary; the full-chain gate is a conservative policy that warrants a separate methodology review.

Diagnostic candidates and the strict coverage audit are stored privately in `instance/mvix-diagnostics/20261115/`. No formal MVIX rows or production files were changed. Do not substitute neighboring-minute prices or interpolate MVIX into the valid series without an explicit, separately labeled estimation methodology.


## Authorized reference repair (2026-11-15)

The November 9 workbook closes were explicitly authorized as substitute mids for missing MVIX only. The offline `scripts/import_reference_mvix.py` added 66 / 84 / 138 reference snapshots for 50 / 300 / 1000, respectively, bringing each day to 240 values. Existing valid snapshots and raw TqSdk quote evidence were preserved. A consistent backup and import report live under ignored `instance/reference-imports/`.

These snapshots have `quality=reference`; APIs, exports and chart tooltips preserve the distinction. Workbook time labels are assumed to be minute endpoints (09:30 and 13:00 excluded). Genuine usable stored quotes take precedence. Unpaired strikes are excluded from this reference calculation and recorded per snapshot. Stale trade closes, incomplete strike coverage and unverified zero-bid tail rules make these research estimates, not recovered historical midquotes. Analyses containing these points inherit that limitation. Future automatic collection continues to require TqSdk bid/ask quotes; the import is never called by the collector. Historical tick reconstruction can replace reference snapshots with verified complete quote evidence.


## November 10 and 12 coverage audit (2026-11-15)

Read-only reconstruction checks found no recoverable full-chain minutes in the remaining gaps. On November 10, valid counts out of 240 are 124 (50), 227 (300), and 227 (1000). On November 12 they are 176, 227, and 227. Each day has 13 missing-chain minutes for every product. The additional 50 gaps are 103 and 51 invalid-quote minutes respectively. Example: HO2611-P-2450 has a null bid and ask 0.4 at November 10 09:34 and November 12 09:33. Missing bid evidence is not assumed to be an observed zero bid. The November 9 reference workbook does not cover these dates. No synthetic repair was applied.

Dense MVIX charts now use a darker 2.2px line and markers only for isolated observations. True gaps remain discontinuous; return-visit scatter markers are unchanged.


## Session display and option smiles (2026-11-16, local only)

The standalone snapshot chart is replaced by a latest-valid-snapshot smile, with separate call/put toggles and an expiry selector. IV uses Black forward pricing, a parity forward from the same MVIX term, ACT/365, and the snapshot rate. Only genuine valid snapshots are eligible; reference imports and display carries never supply smile inputs. Missing/zero/crossed bids and prices outside model bounds remain gaps. The display always identifies its snapshot timestamp and expiry.

Raw SQLite observations are unchanged. The API supplies derived display fields: missing 14:58, 14:59 and 15:00 endpoints may reuse the same day's exact 14:57 valid MVIX, with original time and a closing-auction label. No earlier-day or reference-only fallback is allowed, and available observations take precedence. Missing first afternoon one-minute MVIX may display the 11:30 endpoint when that immediate morning predecessor exists. Pure lunch time is already compressed out of the trading-bar axis. Correlation warmup caused solely by this opening gap can display the last morning coefficient with a carry label; another missing trading observation terminates that carry. Raw correlations and drift inputs are never filled. Real intraday gaps remain visible. These display rules apply on reads to both existing history and future collection.

The phase chart retains its historical gradient and marks the final displayed observation with a CSS pulse (static when reduced motion is requested). If the final observation has no displayable MVIX, no stale point is presented as current.


## Unified selected range (2026-11-16, local only)

All research and futures charts now retain the entire requested date range within configured retention. The 160-candle frontend slice, 1500-row response cap, and leading MVIX-gap trimming were removed. Missing observations remain missing. The phase chart shows its selected interval explicitly; its marker animates opacity only. The smile remains a cross-sectional view of the latest valid snapshot inside that same selected interval, with its timestamp displayed.


## Smile reference and phase subrange (2026-11-17, local only)

The smile reference line uses the last index bar close in the selected date interval, not the futures price or the option parity forward. Its timestamp is shown separately from the smile snapshot. Phase dual range controls affect only the trajectory and return-visit markers in that panel. The selected subrange gets its own early-to-late gradient, retains gaps, and persists across unchanged-scope refreshes. A selection ending at the latest bar follows appended data; historical selections retain their timestamps. The opacity pulse appears only when the subrange includes the latest locally stored index bar for the product. Historical global date ranges do not pulse.


## Operational hardening (2026-11-17, local only)

Five improvements are implemented: persisted collector progress, lightweight time/coverage checks, raw quote evidence separated from calculation eligibility, background cleanup with throttled session activity, and bounded coalesced analysis caching. See [Operations](operations.md) for triggers, limits and deployment implications.

Remaining work: exchange holiday calendar, explicit after-hours catch-up, recovery of stalled workers/interrupted backfills, stronger quote validation and independent reconciliation, production load tests, process isolation, and optional service management. No systemd unit or new process is introduced. Coverage reports identify missing observations; they neither recover unavailable quotes nor certify numerical correctness.


## Live-session verification and local replica policy (2026-11-17)

At 10:37 Shanghai time, production had all 67 expected completed index and actual-futures bars for all four products, and 67 MVIX samples for each option product. Valid/insufficient counts were 40/27 (50), 63/4 (300), and 65/2 (1000). A locally observed HO2611-P-2450 missing bid explains ongoing 50 calculation rejection; missing bids remain missing, not observed zero bids.

Local morning gaps correlated with macOS sleep/wake events. The local development database is now an authoritative replica of exported remote market tables, with live subscriptions disabled through `market_read_only=1`. Do not attempt provider repair merely to complete this local replica. Production remains the collection source; local tests and coverage reports do not imply the new monitoring code has been deployed there.

The remote retention setting was changed to one calendar month without a restart or code deployment. Local defaults and replica retention use the same period. Market-only sync takes a consistent remote snapshot, preserves local authentication, backs up before replacement and labels its export time. Future syncs are explicit, not an installed recurring task.


## MVIX null-strike repair (2026-11-17, local only)

The old peripheral-null handling was more conservative than Cboe's documented strike-selection ordering: it aborted on a peripheral null instead of excluding the null-quote strike. MVIX v1.1 now removes these strikes after validating K0, recomputes spacing weights and records exclusions. K0 failures, crossed selected quotes and unverified timestamps still fail. Raw nulls are never replaced by zero.

A fresh consistent production export at 13:19:17 Shanghai time was imported into the local read-only replica. Replay recovered 300 previously insufficient snapshots: 252 for 50, 39 for 300 and 9 for 1000. All 4,330 pre-existing valid numerical results were unchanged under the new calculation; their stored rows and the 288 reference estimates were preserved. Raw quote JSON was verified unchanged. Old failed snapshots lacked a recorded interest rate, so replay explicitly used the previously configured 2% assumption and recorded this provenance.

All three option products now have 139 valid minutes through 13:19 on November 17. Historical repairs include 102 minutes for 50 on November 10 and 51 on November 12. The 50 snapshot at November 10 13:01 remains invalid because its timestamp evidence was masked/unverified. Entirely absent historical snapshots remain absent; this repair does not establish complete historical coverage.

Private backups and audit files are stored under ignored `instance/` (including `mvix-repair-applied-20261117.json`). The repair tool is idempotent and preserves the original failure in recovery metadata. Repaired chart points identify their method and exclusion count. Production code and history were not changed. A subsequent authoritative remote import replaces local derived repairs until a separately approved production release includes the new method and repair.


## November 17 UI, logs and release preparation

Chart-local analysis controls and inline global date bounds are implemented and browser-verified. The collection/coverage panel was deliberately removed; checks continue in the supervisor and daily log files. Logging tests cover Shanghai midnight rotation, retention, concurrent records and HTTP/exception privacy. A source-only SSH/nohup upgrade script is prepared with checksums, pre-stop validation, backups, PID ownership checks and startup-failure code restoration.

The release script does not repair historical MVIX automatically, install dependencies, initialize a clean server, or prove market coverage from an HTTP readiness check. The remote Linux upgrade path still requires target-host validation during the next explicitly approved release. No production deployment was performed for this preparation. Backups under instance/releases require manual retention management. Multi-process log rotation, centralized log storage, an exchange calendar and a full concurrency load test remain deferred.

Repository cleanup removed reproducible bytecode caches, Finder metadata and the superseded 0.1.0 release archive/checksum. Hidden ranking/market implementations and SDK verification tools remain intentionally retained. Private databases, credentials, reference imports and audit backups were preserved.
