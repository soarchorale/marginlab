"""Production configuration helpers: no network or secrets in command arguments."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def public_origin(value):
    value=value.strip().rstrip('/')
    parsed=urlsplit(value)
    if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Enter a complete public origin without a path, such as https://margin.example.com')
    if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('Public origins require HTTPS; use http://127.0.0.1:5001 for local access')
    return value


def write_private_json(path,payload):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def load_config(root):
    root=Path(root)
    saved=json.loads((root/'instance/server.json').read_text())
    origin=public_origin(saved['public_origin'])
    credentials=root/'instance/tqauth.json'
    auth=json.loads(credentials.read_text())
    if not auth.get('username') or not auth.get('password'):
        raise ValueError('TqSdk credentials have not been initialized')
    return dict(DATABASE=str(root/'instance/margin.sqlite3'),TQ_AUTH_FILE=str(credentials),
                PUBLIC_ORIGIN=origin,COOKIE_SECURE=origin.startswith('https://'))
