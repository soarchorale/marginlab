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

The collector schedules four groups weekdays 09:20–15:10 Asia/Shanghai regardless of browser sessions. It retries connections, leases the database, and retains three calendar months by default. On weekends it waits.

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
