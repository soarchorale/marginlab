"""Run locally ON THE SERVER; passwords use hidden interactive input."""
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from marginlab import create_app
from marginlab.deployment import public_origin,write_private_json
from marginlab.storage import connect
from werkzeug.security import generate_password_hash


def main():
    root=Path(__file__).resolve().parents[1]
    print('Initialize the server: reset the administrator password and revoke sessions while preserving market history.')
    origin=public_origin(input('Public origin [http://127.0.0.1:5001]: ') or 'http://127.0.0.1:5001')
    username=input('TqSdk / Kuaiqi username: ').strip()
    password=getpass.getpass('TqSdk password (hidden): ')
    admin=getpass.getpass('New admin password (at least 12 characters): ')
    confirm=getpass.getpass('Confirm admin password: ')
    if not username or not password:raise ValueError('TqSdk username and password must not be empty')
    if len(admin)<12 or admin!=confirm:raise ValueError('Admin passwords must match and contain at least 12 characters')
    app=create_app({'DATABASE':str(root/'instance/margin.sqlite3')})
    with connect(app.config['DATABASE']) as db:
        db.execute('UPDATE admin SET password_hash=? WHERE id=1',(generate_password_hash(admin),))
        db.execute('DELETE FROM sessions');db.execute('DELETE FROM login_attempts')
    write_private_json(root/'instance/tqauth.json',dict(username=username,password=password))
    write_private_json(root/'instance/server.json',dict(public_origin=origin))
    print('Initialization complete. Credentials are stored in git-ignored instance/. Run scripts/start.sh.')

if __name__=='__main__':
    try:main()
    except (ValueError,EOFError,KeyboardInterrupt) as exc:
        print('Initialization failed: '+(str(exc) if isinstance(exc,ValueError) else 'Input cancelled'),file=sys.stderr)
        sys.exit(1)
