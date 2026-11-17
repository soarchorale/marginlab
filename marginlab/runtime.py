"""Transactional market revisions and persisted operational metadata."""
from .storage import connect


def initialize(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS tq_data_revision(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL);
        INSERT OR IGNORE INTO tq_data_revision VALUES(1,0);
        CREATE TABLE IF NOT EXISTS tq_dirty_days(day TEXT PRIMARY KEY,version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tq_health(product TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tq_coverage(product TEXT,day TEXT,version INTEGER,checked_at REAL,payload TEXT,PRIMARY KEY(product,day));
        CREATE TABLE IF NOT EXISTS tq_maintenance_lease(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,expires REAL);
        CREATE INDEX IF NOT EXISTS tq_bars_time ON tq_bars(timestamp);
        CREATE INDEX IF NOT EXISTS tq_snapshots_time ON tq_snapshots(timestamp);
        ''')
        # Triggers cover collectors, repair scripts and direct SQL imports alike.
        # Operational/session writes deliberately do not change market revisions.
        for table in ('tq_bars','tq_snapshots','tq_option_minutes'):
            columns=[r['name'] for r in db.execute('PRAGMA table_info('+table+')')]
            changed=' OR '.join('OLD.'+col+' IS NOT NEW.'+col for col in columns)
            for action,record in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                when=' WHEN '+changed if action=='UPDATE' else ''
                old_day=("INSERT INTO tq_dirty_days VALUES(substr(OLD.timestamp,1,10),(SELECT version FROM tq_data_revision WHERE id=1)) ON CONFLICT(day) DO UPDATE SET version=excluded.version;" if action=='UPDATE' else '')
                db.executescript(f'''CREATE TRIGGER IF NOT EXISTS {table}_revision_{action.lower()}
                AFTER {action} ON {table}{when} BEGIN
                  UPDATE tq_data_revision SET version=version+1 WHERE id=1;
                  INSERT INTO tq_dirty_days VALUES(substr({record}.timestamp,1,10),(SELECT version FROM tq_data_revision WHERE id=1))
                    ON CONFLICT(day) DO UPDATE SET version=excluded.version;
                  {old_day}
                END;''')


def revision(path):
    with connect(path) as db:
        return db.execute('SELECT version FROM tq_data_revision WHERE id=1').fetchone()[0]
