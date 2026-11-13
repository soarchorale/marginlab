"""Opaque, revocable server-side sessions; no credentials in browser storage."""
import hashlib
import hmac
import secrets
import time
import click
from flask import g, request, render_template, redirect, url_for, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from .storage import connect
from .bootstrap import DEFAULT_ADMIN_HASH


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def install_auth(app):
    path = app.config['DATABASE']
    with connect(path) as db:
        db.execute('INSERT OR IGNORE INTO admin VALUES(1,?)', (DEFAULT_ADMIN_HASH,))

    def new_session(authenticated=False, verified_hash=None):
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        lifetime = app.config['AUTH_MAX_AGE'] if authenticated else 600
        with connect(path) as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)', (digest(token),csrf,int(authenticated),now,now,now+lifetime,digest(verified_hash) if verified_hash else ''))
        return token, csrf

    def cookie(response, token, authenticated=False):
        response.set_cookie('margin_session', token, max_age=app.config['AUTH_MAX_AGE'] if authenticated else 600,
                            httponly=True, secure=app.config['COOKIE_SECURE'], samesite='Lax', path='/')
        return response

    def safe_next(value):
        # Fixed local allowlist prevents open redirects, including encoded backslashes.
        return value if value in ['/', '/pricing', '/chips', '/method', '/settings'] else '/'

    @app.before_request
    def guard():
        if request.endpoint == 'static':
            return
        now = time.time()
        token = request.cookies.get('margin_session', '')
        g.auth_session = None
        with connect(path) as db:
            db.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<=?', (now, now-app.config['AUTH_IDLE_TIMEOUT']))
            db.execute('DELETE FROM login_attempts WHERE attempted<?', (now-900,))
            if token and len(token) <= 128:
                g.auth_session = db.execute('SELECT * FROM sessions WHERE token_hash=?', (digest(token),)).fetchone()
                if g.auth_session and g.auth_session['authenticated']:
                    current_hash = db.execute('SELECT password_hash FROM admin WHERE id=1').fetchone()[0]
                    if not hmac.compare_digest(g.auth_session['credential_tag'], digest(current_hash)):
                        db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
                        g.auth_session = None
        if request.endpoint != 'login' and not (g.auth_session and g.auth_session['authenticated']):
            if request.path.startswith(('/api/', '/option/', '/options/', '/future/', '/hdoption/', '/correlation')):
                return jsonify(error='登录已过期，请重新登录', login_url='/login'), 401
            return redirect(url_for('login', next=safe_next(request.path)))
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            expected_origin = app.config.get('PUBLIC_ORIGIN') or request.host_url.rstrip('/')
            csrf = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if (origin and origin != expected_origin) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='拒绝跨站请求'), 403
            if not g.auth_session or not hmac.compare_digest(g.auth_session['csrf'], csrf):
                return jsonify(error='请求验证失效，请刷新页面后重试'), 403
        if g.auth_session:
            with connect(path) as db:
                db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now, g.auth_session['token_hash']))

    @app.context_processor
    def auth_context():
        session = getattr(g, 'auth_session', None)
        return dict(csrf_token=session['csrf'] if session else '')

    @app.after_request
    def security_headers(response):
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store, private'
            response.headers['Vary'] = 'Cookie'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if app.config['COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        destination = safe_next(request.values.get('next', '/'))
        if g.auth_session and g.auth_session['authenticated']:
            return redirect(destination)
        if request.method == 'GET':
            if g.auth_session:
                return render_template('login.html', next=destination)
            token, csrf = new_session()
            response = make_response(render_template('login.html', csrf_token=csrf, next=destination))
            return cookie(response, token)
        now, ip = time.time(), request.remote_addr or 'unknown'
        # Reserve an attempt before expensive hashing; transaction prevents concurrent limit bypass.
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            per_ip = db.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0]
            if per_ip >= 5 or total >= 30:
                response = make_response(render_template('login.html', error='尝试次数过多，请在 15 分钟后重试。', next=destination), 429)
                response.headers['Retry-After'] = '900'
                return response
            db.execute('INSERT INTO login_attempts(ip,attempted) VALUES(?,?)', (ip,now))
            password_hash = db.execute('SELECT password_hash FROM admin WHERE id=1').fetchone()[0]
        password = request.form.get('password', '')
        valid = check_password_hash(password_hash, password[:1024])
        if not valid or len(password)>1024 or request.form.get('username') != 'admin':
            return render_template('login.html', error='用户名或密码不正确。', next=destination), 401
        with connect(path) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (g.auth_session['token_hash'],))
            db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
        token, _ = new_session(True, password_hash)
        return cookie(redirect(destination), token, True)

    @app.post('/logout')
    def logout():
        with connect(path) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (g.auth_session['token_hash'],))
        response = redirect('/login')
        response.delete_cookie('margin_session', path='/', secure=app.config['COOKIE_SECURE'], httponly=True, samesite='Lax')
        return response

    @app.cli.command('set-admin-password')
    @click.password_option(confirmation_prompt=True)
    def set_admin_password(password):
        """Replace the admin password and revoke every login session."""
        if len(password) < 12:
            raise click.ClickException('The new password must contain at least 12 characters')
        with connect(path) as db:
            db.execute('UPDATE admin SET password_hash=? WHERE id=1', (generate_password_hash(password),))
            db.execute('DELETE FROM sessions')
        click.echo('Administrator password updated; all sessions have been revoked.')
