# Known issues and iteration priorities

Reviewed 2026-11-24 (Asia/Shanghai) against deployed revision `2da9868`. This file tracks outstanding work. The [guide](guide.md) describes user-visible behavior; [operations](operations.md) and [deployment](deployment.md) describe the supported workflows. For the original November 9–19 investigation, use `git show 2da9868:docs/known-issues.md`. Its counts refer to particular database snapshots, not necessarily today's databases.

## November 24 local iteration

The deployed baseline for this review is `2da9868`. The following changes and measurements were verified locally. The [operations runbook](operations.md) gives the exact commands for preview, apply and verification.

| Item | Current result | Next action |
| --- | --- | --- |
| Replica synchronization | Incremental merge previews missing timestamps and conflicting keys, preserves local history and authentication, and backs up before applying. A second merge of the same export inserted zero rows in regression testing. Full replacement requires an explicit flag. | Use merge for routine sync. Inspect differing evidence by its product or symbol and timestamp. |
| November 17 IO/MO | A three-day, 2,142-row copy from the November 23 market archive was used for an end-to-end rehearsal. On November 17, IO recovered 134 of 237 rows and MO recovered 26 of 237. The applied keys exactly matched the preview, all raw quote hashes matched before and after, other rows were unchanged, and repeat replay found zero candidates. The legacy rate assumption was 2% where the stored rate was absent. | After production approval, take a fresh scoped preview on the server, compare its counts and hashes, back up, apply and repeat the audit. The production results are pending. |
| Maintenance diagnosis | A real SQLite write lock on an isolated database reproduced SQLITE_BUSY when an authenticated request needed to persist session activity. The request succeeded after release and revocation still took effect. SQLite errors now record result codes and operation/request durations. An independent command detects stale supervisor or health-log progress even if HTTP logs continue. | Correlate the new fields at the next failure. The November 23 errors predate these diagnostics and their cause remains unconfirmed. |
| Method page | The Chinese method notes now describe spot indices, MVIX, session carries, retention ownership and model Greeks. | Keep the method notes and chart guide in sync with future methodology changes. |

### Capacity measurement

On November 24 an isolated copy of the approximately 403 MiB local replica served authenticated HTTP through four Waitress threads on loopback. The benchmark used SSE 50 one-minute bars ending November 20, gzip and four simultaneous requests. Each cell below includes eight requests in two waves. Cold refers to an empty application analysis cache. Warm measurements preloaded each requested key. Distinct queries used four different residual horizons.

| Range and sample | Same cold p95 | Same warm p95 | Distinct cold p95 | Distinct warm p95 |
| --- | ---: | ---: | ---: | ---: |
| Nov 14–20, 1,200 bars | 296 ms | 139 ms | 333 ms | 145 ms |
| Oct 24–Nov 20, 4,800 bars | 787 ms | 426 ms | 962 ms | 427 ms |

Latencies include successful response transfer. In each distinct cold group, four of eight requests received HTTP 503 from the two-computation limit. All other groups completed successfully. Identical cold requests shared one computation per wave. These small samples characterize a four-client burst on the development machine.

Under concurrent writes, 16 of 16 identical month-range requests succeeded across four cold waves. Seven copied option-minute batches committed with a maximum of 29.4 ms and zero writer errors. This measures synthetic SQLite write duration under load. It excludes network quote arrival, upstream delays and the rest of the production collection loop.

In a temporary headless Chrome profile, the actual page rendered ten SVG charts. Across three redraws, the synchronous chart-rendering function took 13–17 ms for the week and 37–40 ms for the month. The subsequent double animation-frame wait took 13–16 ms and 5 ms respectively (rounded). JSON processing and transfer are outside the synchronous chart measurement.

**Next performance iteration.** Measure cold admission failures and response sizes in production after release. If different cold queries regularly collide, evaluate bounded queuing or a short retry with backoff. For long ranges, profile duplicated payload fields and serialization before changing chart rendering. Keep full-resolution inputs for analysis. These local timings alone do not justify splitting the collector and web service.

## Historical data: what remains missing

The following November 9–13 observations refer to the **local development database** as checked on November 24. The production MVIX range checked in this review starts on November 17. Do not infer a production outage from an older date that exists only in the local archive.

| Date | Current evidence | Disposition |
| --- | --- | --- |
| Nov 6 | Historical imports started partway through the session. | Retain partial coverage unless contemporaneous, verifiable quote evidence becomes available. |
| Nov 9 | All three option products have 240 displayed observations. Genuine / workbook-reference counts: HO 174 / 66, IO 156 / 84, MO 102 / 138. | Closed as a display gap, **not** as a quote reconstruction. Reference estimates are not raw bid/ask or eligible smile inputs. Do not generalize the one-day trade-close exception. |
| Nov 10 and 12 | HO currently has 124 valid + 103 insufficient on Nov 10 and 176 valid + 51 insufficient on Nov 12. IO and MO each have 227 valid each day. Each series has 13 absent snapshots per day. An earlier local replay recovered 102 and 51 HO rows respectively; the Nov 10 13:01 timestamp failed verification. Those trial counts do not describe the current replica after a later authoritative import. | The new scoped local audit again found 102 and 51 candidates respectively. One Nov 10 failure had masked or unverified timestamps. Apply only after reviewing the scoped previews. Keep unverifiable timestamps and absent quote minutes missing. |
| Nov 13, 16 and 17 | The production HO repair on Nov 19 recovered 28, 5 and 159 failed rows respectively (192 total). The local Nov 13 HO series now has 229 valid snapshots; eight continuous-session minutes and three closing-auction snapshots were absent. Nov 13 also has six insufficient MO and one insufficient IO snapshot locally; recoverability was not tested in this review. | Do not rerun a blanket HO repair. Separate missing minutes from existing failed rows. See the scoped IO/MO rehearsal above. |

Independent price/quote reconciliation, a complete historical chain of expired contracts and verified recovery of every absent minute remain out of scope for the lightweight coverage audit. Futures coverage refers to the stored active contracts, not every listed expiry.

## Implemented and checked

- **Production release:** the SSH/nohup path has run on Linux; it verifies a source-only package, backs up the target, restarts it and checks HTTP readiness. The November 24 release passed 90 tests locally and remotely. Code rollback is supported; automatic destructive schema rollback is not. Production cleanup must distinguish redundant backups from the active database, WAL, settings and normal caches.
- **Logging and monitoring:** collector progress, five-minute coverage audits, market revisions, background cleanup and throttled session-activity writes are in production. The SDK import previously replaced the global logger registry. Keeping the application event logger fixes that outage; logs continued through the November 23 trading session and across midnight. Historical missing log events were not reconstructed.
- **Recent coverage:** each index and active future had 240/240 minute bars on Nov 18–20 and Nov 23. Each option product had quotes at all 240 endpoints, 237 valid MVIX values and three auction records. On Nov 24 the morning audit showed 120/120 bars, option minutes and valid MVIX per applicable series. The afternoon was not audited as a completed session here. IC has no matching index-option MVIX.
- **Charts and cache:** the weekly default range, chart-local controls, quote-mid smile with combined toggles/legend, spot marker, phase subrange, return visits, color-matched dual axes, gzip and bounded revision-keyed analysis cache are implemented. The November 24 live test returned 120 Vanna/Volga pairs for 120 morning SSE 50 bars and a cache hit on repeat. These are fixed-term model sensitivities, not single-contract Greeks. See the guide for display-only session carries and rolling-window warmup; neither is evidence of lost market data.

## Deferred decisions

- Keep **nohup** per the deployment choice. It does not restart after machine reboot or unexpected process exit. No systemd migration, shared cache, network SQLite or PostgreSQL migration is planned without a new operational requirement.
- The collection calendar uses weekdays and session times. Holiday awareness, explicit post-session catch-up, interrupted backfill recovery and stalled-worker restart remain open. Start with diagnostics: supervision does not prove every strike had a usable quote, and a restart cannot recreate missing historical bid/ask prices.
- Deployments create private rollback backups. Retention and restore drills are still operator-managed. Keep the current application database separate from backup cleanup; validate a local market-only archive before discarding redundant copies. The local replica is for development, not a source for overwriting production.
- The application supports one administrator. Multi-user roles, centralized logging and multiple service processes are separate projects. Prioritize them only if measured load or access requirements justify them.
