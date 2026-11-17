"""Build and deploy the current checkout via SSH; run with --help first."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ('marginlab', 'scripts', 'static', 'templates', 'docs', 'deploy', 'tests')
FILES = ('app.py', 'requirements.txt', 'README.md', 'AGENT.md', 'pixi.toml', 'pixi.lock', '.gitignore', '.gitattributes')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def healthy(port, process):
    for _ in range(30):
        if process.poll() is not None:
            return False
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{port}/login',timeout=2) as response:
                if response.status==200:
                    time.sleep(2)
                    return process.poll() is None
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    return False


def stop_pid(pid):
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        stat=Path(f'/proc/{pid}/stat')
        if not stat.exists() or stat.read_text().split(') ')[-1].startswith('Z'):
            return
        time.sleep(1)
    raise RuntimeError('Process did not stop gracefully; no forced kill was attempted')


def remote(args):
    """Linux-only upgrade, preserving private runtime data and the environment."""
    import fcntl
    root=Path(args.directory).resolve()
    if not (root/'app.py').is_file():
        raise ValueError('Target must be an existing initialized deployment')
    with (root/'instance/deploy.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return upgrade(args,root)


def upgrade(args,root):
    pid=args.pid or int((root/'instance/service.pid').read_text())
    proc=Path(f'/proc/{pid}')
    command=[s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
    if (proc/'cwd').resolve()!=root or not any(Path(s).name in ('app.py','serve.py') for s in command):
        raise ValueError('PID does not identify a marginlab service in the target directory')
    environment=dict(s.decode().split('=',1) for s in (proc/'environ').read_bytes().split(b'\0') if b'=' in s)
    if environment.get('MARGIN_MARKET_READ_ONLY')=='1':
        raise ValueError('Refusing to deploy over a read-only replica')
    database=Path(environment.get('MARGIN_DATABASE',str(root/'instance/margin.sqlite3')))
    if not database.is_absolute():database=root/database
    if (root/'instance/server.json').exists() and database.resolve()!=root/'instance/margin.sqlite3':
        raise ValueError('server.json and the running database path disagree; reconcile configuration before upgrade')
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        setting=db.execute("SELECT value FROM settings WHERE key='market_read_only'").fetchone()
        if setting and setting[0]=='1':raise ValueError('Production collection is disabled in SQLite')
    package=Path(args.archive)
    if hashlib.sha256(package.read_bytes()).hexdigest()!=args.digest:
        raise ValueError('Uploaded archive checksum mismatch')
    stage=package.parent/'source';stage.mkdir()
    with tarfile.open(package) as archive:
        for entry in archive.getmembers():
            path=Path(entry.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0]!='marginlab' or not entry.isfile():
                raise ValueError('Unsafe archive member')
            rel=path.parts[1:]
            if not rel or rel[0] not in SOURCE+FILES:raise ValueError('Unexpected archive member')
        archive.extractall(stage,filter='data')
    source=stage/'marginlab'
    python=args.remote_python
    # Verify dependencies and isolated application tests before interrupting collection.
    run(python,'-m','pip','check',cwd=source,env=environment)
    run(python,'-m','unittest','discover','-s','tests','-q',cwd=source,env=environment)
    stamp=time.strftime('%Y%m%d-%H%M%S',time.gmtime())
    backup=root/'instance/releases'/stamp
    backup.mkdir(parents=True,mode=0o700)
    for name in SOURCE+FILES:
        old=root/name
        if old.is_dir():shutil.copytree(old,backup/name,ignore=shutil.ignore_patterns('__pycache__'))
        elif old.exists():shutil.copy2(old,backup/name)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db, sqlite3.connect(backup/'before.sqlite3') as dest:
        db.backup(dest)
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed')
    (backup/'release.json').write_text(json.dumps(dict(sha256=args.digest,port=args.port,bind=args.bind)))
    original_environment=environment.copy()
    environment.update(HOST=args.bind,PORT=str(args.port),MARGIN_AUTOCOLLECT='1')
    def launch(cmd,env):
        with (root/'instance/launcher.log').open('ab') as log:
            process=subprocess.Popen(['nohup',*cmd],cwd=root,env=env,stdin=subprocess.DEVNULL,
                                     stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (root/'instance/service.pid').write_text(str(process.pid)+'\n')
        return process
    stop_pid(pid)
    process=None
    try:
        for name in SOURCE+FILES:
            dest=root/name
            if dest.is_dir():shutil.rmtree(dest)
            elif dest.exists():dest.unlink()
            new=source/name
            if new.is_dir():shutil.copytree(new,dest)
            elif new.exists():shutil.copy2(new,dest)
        process=launch([python,'scripts/serve.py'],environment)
        if not healthy(args.port,process):raise RuntimeError('New service failed its local HTTP check')
    except Exception:
        if process and process.poll() is None:stop_pid(process.pid)
        for name in SOURCE+FILES:
            dest=root/name
            if dest.is_dir():shutil.rmtree(dest)
            elif dest.exists():dest.unlink()
            old=backup/name
            if old.is_dir():shutil.copytree(old,dest)
            elif old.exists():shutil.copy2(old,dest)
        restored=launch(command,original_environment)
        print('Code rollback started; database was preserved. PID:',restored.pid,flush=True)
        raise
    print('Deployment passed local HTTP check. PID:',process.pid,'Backup:',backup,flush=True)
    print('Inspect daily collector logs during trading; HTTP readiness does not prove market coverage.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',help='SSH alias or user@host; required unless --build-only')
    parser.add_argument('--directory',default='/opt/marginlab')
    parser.add_argument('--remote-python',default='/usr/bin/python3',help='Absolute path to the existing remote interpreter')
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=5001)
    parser.add_argument('--pid',type=int,help='Verified old service PID, required for the first managed upgrade')
    parser.add_argument('--build-only',action='store_true')
    parser.add_argument('--remote',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--archive',help=argparse.SUPPRESS)
    parser.add_argument('--digest',help=argparse.SUPPRESS)
    args=parser.parse_args()
    os.umask(0o077)
    if args.remote:return remote(args)
    if not args.build_only and (not args.host or args.host.startswith('-')):parser.error('--host is required')
    if not Path(args.remote_python).is_absolute():parser.error('--remote-python must be absolute')
    run(sys.executable,'-m','unittest','discover','-s','tests','-q',cwd=ROOT)
    sys.path.insert(0,str(ROOT/'scripts'))
    from build_release import build
    archive=ROOT/'dist'/('marginlab-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime())+'.tar.gz')
    build(ROOT,archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    print('Built:',archive,'SHA256:',digest,flush=True)
    if args.build_only:return
    temporary=subprocess.check_output(['ssh',args.host,'umask 077; mktemp -d /tmp/marginlab-release.XXXXXXXX'],text=True).strip()
    if not temporary.startswith('/tmp/marginlab-release.') or '/' in temporary[5:]:raise ValueError('Unexpected remote temporary directory')
    try:
        with archive.open('rb') as stream:
            run('ssh',args.host,'cat > '+shlex.quote(temporary+'/release.tar.gz'),stdin=stream)
        with Path(__file__).open('rb') as stream:
            run('ssh',args.host,'cat > '+shlex.quote(temporary+'/release.py'),stdin=stream)
        command=[args.remote_python,temporary+'/release.py','--remote','--archive',temporary+'/release.tar.gz',
                 '--digest',digest,'--directory',args.directory,'--remote-python',args.remote_python,
                 '--bind',args.bind,'--port',str(args.port)]
        if args.pid:command.extend(['--pid',str(args.pid)])
        run('ssh',args.host,shlex.join(command))
    finally:
        run('ssh',args.host,'rm -rf -- '+shlex.quote(temporary))


if __name__=='__main__':main()
