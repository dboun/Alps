#!/usr/bin/env python3
"""Give loopback-only web apps authenticated HTTPS through Alps."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import unicodedata
from urllib.parse import urlsplit
from app import ROOT, apps_lock, load_apps, normalize_url, write_json, import_icon, store_icon
import gateway
import network
import setup


def install_command():
    destination = Path.home() / '.local/bin/alps'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == ROOT / 'alps':
        return
    if destination.exists() or destination.is_symlink():
        raise ValueError(f'{destination} already exists. The launcher is available at {ROOT / "alps"}.')
    destination.symlink_to(ROOT / 'alps')
    print('CLI: ' + str(destination))


def slug_for(name):
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()).strip('-')
    if not slug or len(slug) > 60:
        raise ValueError('Use a name with letters or digits, up to 60 characters.')
    return slug


def check_upstream(value, config):
    url = urlsplit(normalize_url(value))
    if url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'} or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('Use a loopback HTTP origin, such as http://127.0.0.1:3000, without a path.')
    host = '127.0.0.1' if url.hostname == 'localhost' else url.hostname
    port = url.port or 80
    if host == '127.0.0.1' and port == config['port']:
        raise ValueError('Alps cannot expose its own backend.')
    matches = [(address, number) for address, number in network.listeners() if number == port]
    if not matches:
        raise ValueError('Start that app first, bound to 127.0.0.1 (or ::1), then expose it.')
    if any(not ipaddress.ip_address(address).is_loopback for address, _ in matches):
        raise ValueError('That port also listens outside loopback. Bind the app to 127.0.0.1 first so it cannot bypass Alps.')
    target = f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'
    connection = socket.create_connection((host, port), timeout=2)
    connection.close()
    return target


def select_port(requested, rows, upstream, config):
    used = {port for _, port in network.listeners()} | {row['port'] for row in rows}
    used |= {config['port'], urlsplit(upstream).port or 80}
    if requested is not None:
        if not 1024 <= requested <= 65535 or requested in used:
            raise ValueError('Choose an unused HTTPS port from 1024 to 65535, different from the upstream port.')
        return requested
    for port in range(18443, 19444):
        if port not in used:
            return port
    raise ValueError('No free port in the default range. Use --port with an unused port.')


def reload_caddy(config, rows):
    current = setup.read_system(setup.SNIPPET)
    if not current.startswith(setup.MARKER):
        raise ValueError('An installed Alps HTTPS site is required. Run ./install.sh first.')
    main = setup.read_system(setup.CADDYFILE)
    if setup.IMPORT not in [line.strip() for line in main.splitlines()]:
        raise ValueError('Caddy does not import the Alps site. Run ./install.sh first.')
    with tempfile.TemporaryDirectory(prefix='alps-proxy-') as folder:
        path = Path(folder) / 'alps.caddy'
        path.write_text(setup.caddy_text(config, rows))
        try:
            setup.run(['sudo', 'install', '-m', '644', str(path), str(setup.SNIPPET)])
            setup.run(['sudo', 'caddy', 'validate', '--config', str(setup.CADDYFILE), '--adapter', 'caddyfile'])
            # Restart, not reload: a reload fails when a listener moves from all addresses to one.
            setup.run(['sudo', 'systemctl', 'restart', 'caddy'])
        except (OSError, subprocess.CalledProcessError):
            path.write_text(current)
            setup.run(['sudo', 'install', '-m', '644', str(path), str(setup.SNIPPET)])
            subprocess.run(['sudo', 'systemctl', 'restart', 'caddy'], check=False)
            raise


def apply(config, data, rows, icon_overrides=None):
    previous = gateway.read_exposed(data)
    with apps_lock(data):
        apps = load_apps(data)
        # Publish the authorization registry first; Caddy fails closed if Alps is stopped.
        write_json(data / 'exposed.json', rows)
        try:
            reload_caddy(config, rows)
        except BaseException:
            write_json(data / 'exposed.json', previous)
            raise
        by_slug = {row['slug']: row for row in rows}
        result = []
        seen = set()
        for app in apps:
            slug = app.get('proxy')
            if not slug:
                result.append(app)
            elif slug in by_slug:
                app.update(url=gateway.public_url(config, by_slug[slug]), name=by_slug[slug]['name'])
                result.append(app); seen.add(slug)
        for row in rows:
            if row['slug'] not in seen:
                result.append(dict(id='proxy-' + row['slug'], name=row['name'], url=gateway.public_url(config,row),
                                   icon_path='', icon='', proxy=row['slug']))
        for app in result:
            if app.get('proxy') in (icon_overrides or {}):
                app['icon'], app['icon_path'] = icon_overrides[app['proxy']]
        try:
            write_json(data / 'apps.json', result)
        except BaseException:
            write_json(data / 'exposed.json', previous)
            reload_caddy(config, previous)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    expose = sub.add_parser('expose', help='Expose a running loopback website through Alps HTTPS')
    expose.add_argument('url', help='For example: http://127.0.0.1:3000')
    expose.add_argument('--name', required=True)
    expose.add_argument('--port', type=int, help='HTTPS port (default: first free port from 18443)')
    expose.add_argument('--icon', help='Optional server image path; ~ is accepted')
    remove = sub.add_parser('unexpose', help='Remove an HTTPS route and its launcher entry')
    remove.add_argument('slug', help='Slug shown by alps list')
    sub.add_parser('list', help='List exposed apps')
    sub.add_parser('apply', help='Reapply routes after changing the Alps network address')
    sub.add_parser('install-command', help='Add alps to ~/.local/bin')
    sub.add_parser('signout-all', help='Sign out and forget every device')
    args = parser.parse_args(argv)
    if args.command == 'install-command':
        install_command(); return
    config_path = ROOT / 'config.json'
    if not config_path.exists():
        raise ValueError('Run ./install.sh first.')
    config = json.loads(config_path.read_text())
    data = ROOT / 'data'
    data.mkdir(exist_ok=True, mode=0o700)
    # Serialize CLI transactions without holding up launcher reads during network checks.
    with (data / '.cli.lock').open('a') as lock:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = gateway.read_exposed(data)
        if args.command == 'list':
            for row in rows:
                print(f'{row["slug"]}\t{gateway.public_url(config,row)}\t{row["upstream"]}')
            return
        if args.command == 'signout-all':
            # Alps keeps sessions in memory and rewrites the file, so stop it first.
            active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'alps.service']).returncode == 0
            if active:
                setup.run(['systemctl', '--user', 'stop', 'alps.service'])
            else:
                try:
                    socket.create_connection(('127.0.0.1', config['port']), timeout=1).close()
                    raise ValueError('Alps is running outside its service. Stop it first, then run this again.')
                except OSError:
                    pass
            (data / 'sessions.json').unlink(missing_ok=True)
            if active:
                setup.run(['systemctl', '--user', 'start', 'alps.service'])
            print('Every device is signed out and forgotten.')
            return
        if not config.get('https') or config.get('host') != '127.0.0.1':
            raise ValueError('Alps must use HTTPS with a loopback backend. Run ./install.sh to configure it.')
        if not shutil.which('caddy'):
            raise ValueError('Caddy is required. Install it with: sudo apt install caddy')
        icons = {}
        if args.command == 'expose':
            name = args.name.strip()
            slug = slug_for(name)
            upstream = check_upstream(args.url, config)
            existing = next((row for row in rows if row['slug'] == slug), None)
            others = [row for row in rows if row['slug'] != slug]
            if existing and (args.port is None or args.port == existing['port']):
                port = existing['port']
            else:
                port = select_port(args.port, others, upstream, config)
            row = dict(slug=slug,name=name,upstream=upstream,port=port)
            rows = [row if item['slug'] == slug else item for item in rows] if existing else rows + [row]
            if args.icon:
                icons[slug] = (import_icon(args.icon,data/'icons'),args.icon)
            elif not existing:
                icon = network.favicon(upstream,lambda content,suffix:store_icon(content,suffix,data/'icons'))
                icons[slug] = (icon,'')
            apply(config,data,rows,icons)
            print('\n'.join(f'https://{host}:{port}' for host in gateway.hosts(config)))
            print('Sign in to Alps first. Keep the upstream app bound to loopback.')
            print('If your firewall is enabled, allow TCP port ' + str(port) + ' on the chosen networks.')
        elif args.command == 'unexpose':
            if not any(row['slug'] == args.slug for row in rows):
                raise ValueError('Unknown app. Run alps list to see the slugs.')
            apply(config,data,[row for row in rows if row['slug'] != args.slug])
        else:
            for row in rows:
                check_upstream(row['upstream'],config)
            apply(config,data,rows)


def entrypoint():
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit('Alps: ' + str(exc))
    except KeyboardInterrupt:
        raise SystemExit('\nCancelled.')


if __name__ == '__main__':
    entrypoint()
