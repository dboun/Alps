#!/usr/bin/env python3
"""Alps: a small, private home-network launcher. Python standard library only."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
from pathlib import Path
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import network
import gateway

ROOT = Path(__file__).resolve().parent
COOKIE = 'alps_session'
MAX_ICON = 2 * 1024 * 1024
SESSION_IDLE = 90 * 86400
DEVICE_TTL = 400 * 86400  # Browsers cap cookie lifetime at 400 days.


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


@contextmanager
def apps_lock(data):
    with (Path(data) / '.apps.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load_apps(data):
    path = Path(data) / 'apps.json'
    return json.loads(path.read_text()) if path.exists() else []


def local_address(fallback='127.0.0.1'):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            # This selects a local interface; UDP connect sends no packet.
            route.connect(('1.1.1.1', 80))
            return route.getsockname()[0]
    except OSError:
        return fallback


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def normalize_url(value):
    value = str(value).strip()
    if not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Enter an app address, such as http://192.168.1.20:8010.')
    if '://' not in value:
        value = 'http://' + value
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError()
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError()
        hostname = url.hostname.encode('idna').decode('ascii').rstrip('.')
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            if (len(hostname) > 253 or hostname.split('.')[-1].isdigit() or
                    any(not re.fullmatch(r'[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?', label) for label in hostname.split('.'))):
                raise ValueError()
    except ValueError:
        raise ValueError('Use an HTTP or HTTPS address without a username or password.') from None
    return value


def import_icon(value, directory):
    value = str(value).strip()
    if not value:
        return ''
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('Use the full icon path on the computer running Alps, or leave it empty.')
    try:
        with path.open('rb') as stream:
            content = stream.read(MAX_ICON + 1)
    except OSError:
        raise ValueError('Cannot read that icon. Check its path and file permissions.') from None
    return store_icon(content, path.suffix.lower(), directory)


def store_icon(content, suffix, directory):
    signatures = {'.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff', '.gif': b'GIF', '.ico': b'\0\0\1\0'}
    valid = (suffix in signatures and content.startswith(signatures[suffix]))
    valid |= suffix == '.webp' and content.startswith(b'RIFF') and content[8:12] == b'WEBP'
    if suffix == '.svg':
        import xml.etree.ElementTree as ET
        try:
            valid = ET.fromstring(content).tag in ('svg', '{http://www.w3.org/2000/svg}svg')
        except ET.ParseError:
            valid = False
    if not valid or len(content) > MAX_ICON:
        raise ValueError('Choose a PNG, JPEG, GIF, WebP, SVG, or ICO image under 2 MB.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(content).hexdigest()[:32] + suffix
    target = directory / name
    if not target.exists():
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
    return name


class AlpsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, config, data):
        self.config = config
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.sessions_file = self.data / 'sessions.json'
        # Token HMAC -> [signed in until, known device until]. Survives restarts.
        try:
            self.sessions = json.loads(self.sessions_file.read_text())
        except (OSError, ValueError):
            self.sessions = {}
        if not isinstance(self.sessions, dict) or not all(
                isinstance(record, list) and len(record) == 2 and all(isinstance(stamp, (int, float)) for stamp in record)
                for record in self.sessions.values()):
            self.sessions = {}
        self.device_attempts = {}
        self.pending_icons = set()
        self.discovery_cache = {}
        self.attempts = []
        self.apps = load_apps(self.data)
        super().__init__(address, Handler)
        port = self.server_address[1]
        self.allowed_hosts = set(config.get('allowed_hosts', [])) | {f'localhost:{port}', f'127.0.0.1:{port}'}
        try:
            hosts = gateway.hosts(config) if config.get('https') else []
        except (KeyError, ValueError):
            hosts = []
        # Installed-app tabs frame exposed apps, which listen on other ports of these hosts. Alps itself
        # may be framed only by Alps: an app's sign-in redirect lands on Alps inside its tab.
        frames = ' '.join(f'https://{f"[{host}]" if ":" in host else host}:*' for host in hosts) or "'none'"
        self.csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                    f"frame-src {frames}; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'")

    def session_key(self, token):
        # Keyed by the password hash, so a new password signs every device out.
        return hmac.new(self.config['password_hash'].encode(), token.encode(), hashlib.sha256).hexdigest()

    def save_sessions(self):
        try:
            write_json(self.sessions_file, self.sessions)
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'Alps'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # No URLs, credentials, or icon paths in request logs.
        pass

    def respond(self, status, value, mime='application/json', cookie=None, cache=None, location=None):
        if mime == 'application/json':
            value = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', cache or 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', self.server.csp)
        if location:
            self.send_header('Location', location)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        try:
            self.wfile.write(value)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def cookie_token(self):
        # Exact-name match. A second alps_session can be planted by an exposed app on another path; trust neither.
        raw = '; '.join(self.headers.get_all('Cookie') or [])
        values = [value.strip() for name, _, value in (part.partition('=') for part in raw.split(';')) if name.strip() == COOKIE]
        return values[0] if len(values) == 1 else ''

    def known_device(self):
        token = self.cookie_token()
        with self.server.lock:
            record = self.server.sessions.get(self.server.session_key(token)) if token else None
            return bool(record) and record[1] > time.time()

    def session(self):
        token = self.cookie_token()
        if not token:
            return None
        key, now = self.server.session_key(token), time.time()
        with self.server.lock:
            record = self.server.sessions.get(key)
            if not record or record[0] <= now:
                return None
            # Slide the idle window on use, writing at most once a day.
            if record[0] < now + SESSION_IDLE - 86400:
                self.server.sessions[key] = [now + SESSION_IDLE, now + DEVICE_TTL]
                self.server.save_sessions()
        return token

    def host_ok(self):
        return self.headers.get('Host', '') in self.server.allowed_hosts

    def do_GET(self):
        if not self.host_ok():
            return self.respond(403, {'error': 'Address not allowed. Run ./install.sh to update the network address.'})
        path = urlsplit(self.path).path
        public_files = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js', '/shell.js': 'shell.js', '/favicon.svg': 'favicon.svg',
                        '/apple-touch-icon.png': 'apple-touch-icon.png', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png', '/manifest.webmanifest': 'manifest.webmanifest'}
        if path in public_files:
            file = ROOT / 'static' / public_files[path]
            if path == '/':
                # Content-hash asset URLs so browsers can cache them without revalidating.
                html = re.sub(r'/(style\.css|app\.js|shell\.js)\?v=\w+', lambda m: f'/{m[1]}?v=' + hashlib.sha256((ROOT / 'static' / m[1]).read_bytes()).hexdigest()[:12], file.read_text())
                return self.respond(200, html.encode(), 'text/html')
            # Over plain HTTP a LAN attacker could plant a script; don't let it persist for a year.
            immutable = self.server.config.get('https') and path in ('/style.css', '/app.js', '/shell.js') and parse_qs(urlsplit(self.path).query).get('v')
            mime = 'application/manifest+json' if file.suffix == '.webmanifest' else mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            return self.respond(200, file.read_bytes(), mime,
                                cache='private, max-age=31536000, immutable' if immutable else 'private, max-age=3600')
        if path == '/api/proxy-auth':
            slug = parse_qs(urlsplit(self.path).query).get('app', [''])[0]
            try:
                row = next((row for row in gateway.read_exposed(self.server.data) if row['slug'] == slug), None)
            except (OSError, ValueError, KeyError):
                row = None
            if not row or not self.server.config.get('https'):
                return self.respond(403, {'error': 'This app is not exposed through Alps.'})
            # Caddy sets X-Forwarded-Host from the request; only this app's own addresses pass.
            original_host = self.headers.get('X-Forwarded-Host', '')
            if original_host not in {f'{host}:{row["port"]}' for host in gateway.hosts(self.server.config)}:
                return self.respond(403, {'error': 'Unrecognized app address.'})
            origin = self.headers.get('Origin')
            if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != ('https', original_host):
                return self.respond(403, {'error': 'Request origin rejected.'})
            if self.session():
                return self.respond(200, {'ok': True})
            if self.headers.get('X-Forwarded-Method', 'GET') not in ('GET', 'HEAD'):
                return self.respond(401, {'error': 'Sign in to Alps first.'})
            return self.respond(302, {'error': 'Sign in to Alps first.'}, location='https://' + original_host.rsplit(':', 1)[0] + '/?open=' + slug)
        if path == '/api/session':
            authenticated = bool(self.session())
            return self.respond(200, {
                'authenticated': authenticated,
                # Known devices get the username prefilled after signing out.
                'username': self.server.config['username'] if authenticated or self.known_device() else '',
                'address': local_address(self.server.config.get('address', '127.0.0.1')) if authenticated else '',
                'hosts': gateway.hosts(self.server.config) if authenticated and self.server.config.get('https') else [],
            })
        if not self.session():
            return self.respond(401, {'error': 'Sign in to continue.'})
        if path == '/api/ports':
            host = parse_qs(urlsplit(self.path).query).get('host', [''])[0]
            local = local_address(self.server.config.get('address', '127.0.0.1'))
            if host not in {local, '127.0.0.1', self.server.config.get('address'), *gateway.network_ips(self.server.config)}:
                return self.respond(400, {'error': 'Only this computer can be checked for web ports.'})
            with self.server.lock:
                cached = self.server.discovery_cache.get(host)
            if cached and cached[0] > time.time() - 15:
                return self.respond(200, cached[1])
            result = network.discover_web(host, excluded={self.server.server_port})
            with self.server.lock:
                self.server.discovery_cache[host] = (time.time(), result)
            return self.respond(200, result)
        if path == '/api/apps':
            with self.server.lock:
                self.server.apps = load_apps(self.server.data)
                return self.respond(200, self.server.apps)
        if path.startswith('/icons/'):
            name = path.removeprefix('/icons/')
            with self.server.lock:
                self.server.apps = load_apps(self.server.data)
                allowed = {app.get('icon') for app in self.server.apps} | self.server.pending_icons
            if name in allowed and name and Path(name).name == name:
                file = self.server.data / 'icons' / name
                if file.is_file():
                    # Icon names are content hashes, so they never change.
                    return self.respond(200, file.read_bytes(), mimetypes.guess_type(name)[0] or 'image/x-icon', cache='private, max-age=31536000, immutable')
        return self.respond(404, {'error': 'Not found.'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.host_ok() or self.headers.get('X-Alps') != '1' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
            return self.respond(403, {'error': 'Request rejected. Reload Alps and try again.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                return self.respond(413, {'error': 'Request is too large or empty.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected an object.')
        except (ValueError, UnicodeError):
            return self.respond(400, {'error': 'Invalid request.'})
        path = urlsplit(self.path).path
        if path == '/api/login':
            old = self.cookie_token()
            old_key = self.server.session_key(old) if old else ''
            with self.server.lock:
                now = time.time()
                # Browsers that signed in before get their own allowance; new ones share a strict daily one.
                known = self.server.sessions.get(old_key, [0, 0])[1] > now
                attempts = self.server.device_attempts.setdefault(old_key, []) if known else self.server.attempts
                attempts[:] = [stamp for stamp in attempts if stamp > now - 86400]
                if len(attempts) >= (10 if known else 5):
                    return self.respond(429, {'error': 'Too many attempts. Try again tomorrow, or restart Alps.'})
                attempts.append(now)
            config = self.server.config
            username = str(body.get('username', ''))
            password = str(body.get('password', ''))
            valid_password = hmac.compare_digest(password_hash(password, config['salt']), config['password_hash'])
            if not hmac.compare_digest(username.encode(), config['username'].encode()) or not valid_password:
                return self.respond(401, {'error': 'Check your username and password.'})
            token = secrets.token_urlsafe(32)
            with self.server.lock:
                self.server.sessions = {key: record for key, record in self.server.sessions.items() if record[1] > now and key != old_key}
                self.server.sessions[self.server.session_key(token)] = [now + SESSION_IDLE, now + DEVICE_TTL]
                self.server.device_attempts.pop(old_key, None)
                self.server.save_sessions()
            secure = '; Secure' if config.get('https') else ''
            return self.respond(200, {'ok': True}, cookie=f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={DEVICE_TTL}{secure}')
        clear_cookie = f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0' + ('; Secure' if self.server.config.get('https') else '')
        if path == '/api/forget-device':
            # Works signed out: it only drops the record for the cookie this browser sends.
            token = self.cookie_token()
            with self.server.lock:
                key = self.server.session_key(token) if token else ''
                self.server.device_attempts.pop(key, None)
                if self.server.sessions.pop(key, None):
                    self.server.save_sessions()
            return self.respond(200, {'ok': True}, cookie=clear_cookie)
        token = self.session()
        if not token:
            return self.respond(401, {'error': 'Sign in to continue.'})
        if path == '/api/logout':
            # Remembering keeps the cookie, so this browser stays a known device.
            forget = body.get('forget') is True
            with self.server.lock:
                key = self.server.session_key(token)
                if forget:
                    self.server.sessions.pop(key, None)
                elif key in self.server.sessions:
                    self.server.sessions[key][0] = 0
                self.server.save_sessions()
            return self.respond(200, {'ok': True}, cookie=clear_cookie if forget else None)
        if path == '/api/favicon':
            try:
                url = normalize_url(body.get('url', ''))
                icon = network.favicon(url, lambda content, suffix: store_icon(content, suffix, self.server.data / 'icons'))
                if icon:
                    with self.server.lock:
                        self.server.pending_icons.add(icon)
                return self.respond(200, {'icon': icon})
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
        try:
            with self.server.lock, apps_lock(self.server.data):
                apps = load_apps(self.server.data)
                if path == '/api/apps/save':
                    ident = str(body.get('id') or '')
                    existing = next((app for app in apps if app['id'] == ident), None)
                    if ident and not existing:
                        return self.respond(404, {'error': 'That app was removed. Reload the page.'})
                    name = str(body.get('name', '')).strip()
                    if not name or len(name) > 80:
                        raise ValueError('Give the app a name, up to 80 characters.')
                    url = normalize_url(body.get('url', ''))
                    if existing and existing.get('proxy') and url != existing['url']:
                        raise ValueError('This HTTPS address is managed by the Alps CLI.')
                    icon_path = str(body.get('icon_path', '')).strip()
                    if len(icon_path) > 4096:
                        raise ValueError('The icon path is too long.')
                    automatic = str(body.get('auto_icon', ''))
                    if automatic and not icon_path:
                        if automatic not in self.server.pending_icons and not (existing and existing['icon'] == automatic):
                            raise ValueError('The suggested icon expired. Leave the address field to look it up again.')
                        icon = automatic
                    else:
                        icon = existing['icon'] if existing and icon_path and existing.get('icon_path') == icon_path else import_icon(icon_path, self.server.data / 'icons')
                    app = {'id': ident or secrets.token_hex(8), 'name': name, 'url': url, 'icon_path': icon_path, 'icon': icon}
                    for key in ('proxy', 'small', 'x', 'y'):
                        if existing and key in existing:
                            app[key] = existing[key]
                    if existing:
                        apps[apps.index(existing)] = app
                    else:
                        apps.append(app)
                elif path == '/api/apps/layout':
                    positions = body.get('positions')
                    by_id = {item['id']: item for item in apps}
                    if not isinstance(positions, list) or len(positions) != len(apps) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in positions) or {item.get('id') for item in positions} != set(by_id):
                        raise ValueError('The app list changed. Reload Alps before arranging.')
                    # Cells are grid columns and half-height rows; bool is an int subclass, so check the exact type.
                    if any(type(item.get('x')) is not int or type(item.get('y')) is not int or not 0 <= item['x'] < 24 or not 0 <= item['y'] < 2000 for item in positions):
                        raise ValueError('That spot is outside the grid.')
                    for item in positions:
                        by_id[item['id']].update(x=item['x'], y=item['y'])
                    # Keep the stored order in reading order; narrow screens and search flow in this order.
                    apps.sort(key=lambda item: (item['y'], item['x']))
                elif path == '/api/apps/size':
                    existing = next((app for app in apps if app['id'] == str(body.get('id', ''))), None)
                    if not existing:
                        return self.respond(404, {'error': 'That app was removed. Reload the page.'})
                    if body.get('small') is True:
                        existing['small'] = True
                    else:
                        existing.pop('small', None)
                elif path == '/api/apps/delete':
                    ident = str(body.get('id', ''))
                    if any(app['id'] == ident and app.get('proxy') for app in apps):
                        raise ValueError('Use alps unexpose in a terminal to remove this HTTPS app.')
                    if not any(app['id'] == ident for app in apps):
                        return self.respond(404, {'error': 'That app was already removed.'})
                    apps = [app for app in apps if app['id'] != ident]
                else:
                    return self.respond(404, {'error': 'Not found.'})
                write_json(self.server.data / 'apps.json', apps)
                self.server.apps = apps
                return self.respond(200, apps)
        except ValueError as exc:
            return self.respond(400, {'error': str(exc)})
        except OSError:
            return self.respond(500, {'error': 'Could not save. Check free space and permissions on the Alps data folder.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    if not args.config.exists():
        parser.error('Run ./install.sh first.')
    config = json.loads(args.config.read_text())
    if not config.get('username') or not config.get('password_hash'):
        parser.error('A username and password are required. Run ./install.sh.')
    # HTTPS goes through Caddy, so the backend itself never needs to listen beyond this computer.
    try:
        loopback = ipaddress.ip_address(config.get('host', '')).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error('The Alps backend must listen on 127.0.0.1. Run ./install.sh to fix the setup.')
    server = AlpsServer((config['host'], config['port']), config, args.data)
    print('Alps is running: ' + config.get('url', f"http://127.0.0.1:{config['port']}"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
