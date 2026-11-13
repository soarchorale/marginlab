"""Local SQLite persistence. Runtime files are private and excluded from source control."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
            authenticated INTEGER NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY, ip TEXT NOT NULL, attempted REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_time ON login_attempts(attempted);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS market_bars (symbol TEXT NOT NULL, period TEXT NOT NULL,
            timestamp TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, vix REAL, volume REAL,
            PRIMARY KEY(symbol,period,timestamp));
        CREATE TABLE IF NOT EXISTS market_sync (symbol TEXT NOT NULL, period TEXT NOT NULL,
            success REAL, attempted REAL, coverage_start TEXT, lease_until REAL NOT NULL DEFAULT 0,
            last_error TEXT, PRIMARY KEY(symbol,period));
        ''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(sessions)')}
        if 'credential_tag' not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN credential_tag TEXT NOT NULL DEFAULT ''")
    path.chmod(0o600)
