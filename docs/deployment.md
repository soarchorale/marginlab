# marginlab cloud deployment

## Layout and prerequisites

Replace `/path/to/marginlab` with your local checkout directory. Release archives extract to `marginlab/`; the importable package remains the inner `marginlab/` directory. Do not name the Python package `src`. A future standard src layout would be `src/marginlab/` with packaging metadata.

Scripts locate the project through `__file__` or their shell-script directory. Default paths survive relocation; explicit relative arguments remain caller-relative. Use Python 3.11 and the single `requirements.txt`. Direct versions are pinned; transitive versions are resolved by pip. The macOS Pixi lock is not a cloud deployment lock.

## 1. Prepare source and SQLite

```sh
cd /path/to/marginlab
.pixi/envs/default/bin/python -m unittest discover -s tests -q
.pixi/envs/default/bin/python scripts/build_release.py
.pixi/envs/default/bin/python scripts/backup_sqlite.py --output instance/margin-transfer.sqlite3
```

Output: `dist/marginlab-0.1.0.tar.gz` and its SHA256 file. The allowlisted source package excludes environments, credentials, runtime databases, diagnostics, and sessions. Git ignores `instance/`, root `tqauth.json`, `.env*`, and SQLite/WAL/SHM files. Ignore rules do not untrack committed files: inspect `git ls-files instance tqauth.json`, untrack with `git rm --cached` if needed, and rotate any exposed credentials.

Never copy only the main file of an actively written WAL database. The backup API creates a consistent copy, removes sessions/login failures/collector leases, compacts the copy, and checks integrity. It preserves market history and the administrator hash. Existing destinations are not overwritten; use a fresh filename for another backup.

The backup is still private. Transfer it separately over SSH/SCP, never inside a public source archive. New local observations after the backup are not automatically synchronized.

## 2. Install (Debian 12 / Python 3.11 example)

Replace `your-server` with the SSH destination. Upload from the local machine:

```sh
scp dist/marginlab-0.1.0.tar.gz dist/marginlab-0.1.0.tar.gz.sha256 your-server:/tmp/
scp instance/margin-transfer.sqlite3 your-server:/tmp/
```

On the server:

```sh
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv nginx
cd /tmp
sha256sum -c marginlab-0.1.0.tar.gz.sha256
sudo useradd --system --create-home --home-dir /var/lib/marginlab --shell /usr/sbin/nologin marginlab
sudo tar -xzf /tmp/marginlab-0.1.0.tar.gz -C /opt
sudo install -d -m 700 -o marginlab -g marginlab /opt/marginlab/instance
sudo install -m 600 -o marginlab -g marginlab /tmp/margin-transfer.sqlite3 /opt/marginlab/instance/margin.sqlite3
sudo chown -R marginlab:marginlab /opt/marginlab
cd /opt/marginlab
sudo -u marginlab bash scripts/setup-server.sh
```

For an empty new database, omit its copy command. Restore migrated data BEFORE initialization. For upgrades, stop the service and back up the cloud database before updating code; never overwrite newer cloud observations with an old transfer copy.

Initialization asks for the public HTTPS origin without a path, TqSdk/Kuaiqi credentials (not a trading funds account), and a new admin password of at least 12 characters with confirmation. Password input is hidden. Credentials go to `instance/tqauth.json`, site settings to `instance/server.json`, both mode 0600. Nothing is embedded in command arguments or the service unit. Initialization neither places orders nor verifies credentials over the network; the collector reports connection failures later.

Rerun `scripts/configure_server.py` to change configuration/reset the admin password, then restart the service. This revokes sessions and preserves market history. Remove uploaded temporary backup copies when no longer needed.

## 3. Production startup

Verify in the foreground:

```sh
sudo -u marginlab /opt/marginlab/scripts/start.sh
```

Then Ctrl+C and install systemd:

```sh
sudo install -m 644 deploy/marginlab.service /etc/systemd/system/marginlab.service
sudo systemctl daemon-reload
sudo systemctl enable --now marginlab
sudo systemctl status marginlab
sudo journalctl -u marginlab -n 100 --no-pager
```

`scripts/serve.py` runs single-process, multithreaded Waitress and explicitly starts collection. A plain `waitress-serve app:app` or generic multiprocess WSGI command does not execute that startup flow.

The service listens on `127.0.0.1:5001`. Add `deploy/nginx.conf.example` to your domain's HTTPS server configuration. Configure DNS/TLS for the actual host and match the initialized origin. Only the required SSH/HTTP/HTTPS ports need public access; do not expose port 5001. Waitress trusts only the local reverse proxy; see its [official usage guidance](https://docs.pylonsproject.org/projects/waitress/en/latest/runner.html).

Without a domain, initialize `http://127.0.0.1:5001` and use:

```sh
ssh -L 5001:127.0.0.1:5001 your-server
```

Open local `http://127.0.0.1:5001`. HTTPS origins enable Secure cookies, so plain HTTP cannot validate that login configuration.

## 4. Acceptance and backfill

The collector schedules four groups weekdays 09:20–15:10 Asia/Shanghai regardless of browser sessions. It retries connections, leases the database, and retains one calendar month by default. On weekends it waits.

Verify login, autonomous status, preserved MVIX history, and during trading the advancement of completed bars, option quotes, and MVIX. Missing permissions, downtime, and invalid quotes remain explicit gaps.

```sh
.venv/bin/python scripts/backfill_mvix.py --auth-file instance/tqauth.json
```

Linux/systemd/Nginx/DNS/TLS and actual trading-session behavior need target-host verification. Local checks do not substitute for it. Preparing a release does not upload files, create remote accounts, or install local system services automatically.

## Installation troubleshooting

Setup requires Python 3.11, validates existing virtual environments, and upgrades pip before installing dependencies. A `pip 9.0.3` error with a truncated Flask version list suggests an old interpreter/installer or a stale package index; it does not establish that Flask 3.1.3 is unavailable. Inspect the actual environment on the server:

```sh
python3.11 --version
sudo -u marginlab .venv/bin/python --version
sudo -u marginlab .venv/bin/python -m pip --version
```

If the existing virtual environment uses another Python version, stop the service and rename `.venv` to an unused backup name, then rerun the updated setup script. Do not remove `instance/` or credential files. For a custom Python installation:

```sh
sudo -u marginlab env PYTHON_BIN=/absolute/path/to/python3.11 bash scripts/setup-server.sh
```

If Python 3.11 and an upgraded pip still cannot find a pinned release, inspect the configured package index locally (`.venv/bin/python -m pip config debug`). Do not share index URLs containing credentials. Use a current trusted mirror or explicitly select PyPI for setup:

```sh
sudo -u marginlab env PIP_INDEX_URL=https://pypi.org/simple bash scripts/setup-server.sh
```


## Authoritative production database

Production is the authoritative collection source. Local development may run with `MARGIN_MARKET_READ_ONLY=1` and a persisted `market_read_only=1` setting. Code releases must preserve the production database; do not upload the local replica database over it. Both read-only switches must be disabled to enable collection. Use the market-only snapshot workflow in [Operations](operations.md) to synchronize from production to development. The November 17 retention policy is one calendar month.

Application diagnostics now rotate daily in `instance/logs/` (Asia/Shanghai), with 30 days retained by default. Existing nohup commands and process ownership remain unchanged; use the daily files instead of searching nohup output for routine HTTP, collector and coverage events. Set `MARGIN_LOG_DIR` and `MARGIN_LOG_RETENTION_DAYS` before startup to override these defaults. See `docs/operations.md` for event fields and privacy details. Keep `instance/` private and excluded from releases.


## Repeatable nohup upgrades

The new `deploy/release.py` upgrades an **existing Linux deployment**, using its already installed Python dependencies and existing credentials. It does not run setup or change passwords. The legacy setup script remains available for clean installations; an existing Python 3.13 environment can be selected explicitly. `scripts/serve.py` loads `instance/server.json` if present, otherwise it uses the application's existing environment/default settings. HOST/PORT select the listener; defaults remain 127.0.0.1:5001.

```sh
# Local checkout: test and package only, with no SSH or service restart.
python3 deploy/release.py --build-only

# After release approval: replace the placeholders with the actual SSH alias,
# remote interpreter and verified current service PID.
python3 deploy/release.py --host your-server \
  --directory /opt/marginlab --remote-python /absolute/path/to/python3.13 \
  --pid 12345 --bind 0.0.0.0 --port 5001
```

Use the local project interpreter if `python3` does not have the test dependencies. A loopback bind is recommended behind a reverse proxy; direct IP access requires an explicit public bind and suitable firewall rules. This command does not change firewall rules or TLS. Verify the public login URL separately after deployment.

The first managed upgrade requires `--pid` unless `instance/service.pid` already exists. Later upgrades can reuse that PID file. Linux `/proc` must confirm that the process belongs to this project directory and runs app.py or serve.py; an unrelated/stale PID fails safely. Run SSH as the service owner (or an already authorized administrator). The existing process environment is inherited privately; neither passwords nor tokens are printed. A SQLite or environment read-only flag causes rejection instead of silently disabling production collection.

The workflow is:

1. Run local tests and build a timestamped source-only archive of the current working tree, including uncommitted files. No commit or push is required. Private runtime files are excluded.
2. Upload to a private temporary directory through SSH, verify SHA256 and archive paths, then run dependency consistency checks and isolated tests with the remote interpreter before stopping collection. Dependencies are not installed automatically.
3. Lock deployment, save existing source and a consistent SQLite backup under `instance/releases/<UTC timestamp>/`, and stop the verified service gracefully. A hung process aborts without SIGKILL.
4. Replace only managed source paths, preserving instance/, environments and root credentials. Start `scripts/serve.py` with nohup and Waitress, record its PID and check local `/login` readiness. Collection uses the existing background supervisor; no extra daemon or systemd installation is needed.
5. On replacement/startup failure, restore the old source and start the prior command with its original environment. Keep the live database as-is; never erase newer market observations with the backup. The script reports failure so the operator can verify the restored process.

Routine diagnostics remain in daily logs. `instance/launcher.log` holds startup/third-party console output. Source and database backups remain private and are not pruned automatically. An upgrade creates a short collection interruption; deploy outside trading when possible. A local HTTP success proves web startup only, not collector health, external IP reachability or complete quotes. Inspect subsequent `collector_health` and `coverage_audit` events during trading.

Automatic code rollback is intended for this version's compatible schema initialization. Future destructive schema migrations require an explicit migration/restore plan; the script does not promise database rollback. Repairing historical MVIX is a separate audited operation and is not automatically run during release.
