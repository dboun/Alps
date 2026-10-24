"""Caddy routes configured only by the Alps CLI."""
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def read_exposed(data):
    path = Path(data) / 'exposed.json'
    return json.loads(path.read_text()) if path.exists() else []


def network_ips(config):
    if config.get('bind') != '0.0.0.0':
        return []
    if 'access' not in config:  # Setups from before access modes had one LAN address.
        return [config['address']]
    return ([config['lan']] if config.get('lan') else []) + [item['ip'] for item in config.get('extra', [])]


TAILSCALE_SOURCES = ['100.64.0.0/10', 'fd7a:115c:a1e0::/48']


def checked(config):
    """Values that reach root-owned Caddy and nftables files, revalidated in case config.json was edited."""
    for ip in network_ips(config) + list(config.get('tailscale_ips', [])):
        ipaddress.ip_address(ip)
    for item in config.get('extra', []):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', item['interface']):
            raise ValueError('Invalid interface in config.json. Run ./install.sh again.')
        if item.get('network'):
            ipaddress.ip_network(item['network'])
    if config.get('tailscale_name') and not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net', config['tailscale_name']):
        raise ValueError('Invalid Tailscale name in config.json. Run ./install.sh again.')
    return config


def sites(config):
    """(hostname, listen addresses, private certificate, allowed client networks or None) per public
    address; the first is the main one."""
    checked(config)
    if config.get('bind') != '0.0.0.0':
        return [('127.0.0.1', ['127.0.0.1'], True, None)]
    # Each site listens only on its own address, so another address's Host header cannot reach it.
    lan = config.get('lan') if 'access' in config else config['address']
    result = [(lan, [lan], True, None)] if lan else []
    for item in config.get('extra', []):
        # Backs up the firewall rule: clients must come from the chosen network itself.
        sources = TAILSCALE_SOURCES if ipaddress.ip_address(item['ip']) in ipaddress.ip_network(TAILSCALE_SOURCES[0]) else [item['network']] if item.get('network') else None
        result.append((item['ip'], [item['ip']], True, sources))
    if config.get('tailscale_name'):
        result.append((config['tailscale_name'], config['tailscale_ips'], False, TAILSCALE_SOURCES))
    return result


def hosts(config):
    return [site[0] for site in sites(config)]


def site_block(address, binds, private, body, sources=None):
    listen = ' '.join(f'[{ip}]' if ':' in ip else ip for ip in binds)
    tls = '\ttls internal\n' if private else '\ttls {\n\t\tget_certificate tailscale\n\t}\n'
    if sources:
        # Caddy orders forward_auth before handle, so the rest must sit in its own exclusive handle.
        nested = ''.join('\t' + line for line in body.splitlines(keepends=True))
        body = f'\t@outside not remote_ip {" ".join(sources)}\n\thandle @outside {{\n\t\tabort\n\t}}\n\thandle {{\n{nested}\t}}\n'
    return f'{address} {{\n\tbind {listen}\n{tls}{body}}}\n'


def public_url(config, row):
    return f'https://{config["address"]}:{row["port"]}'


def caddy_blocks(config, rows):
    blocks = []
    # Only the app itself and Alps pages (installed-app tabs) may frame it. Browsers enforce every CSP header, so
    # this one applies whatever the app sends; the app's own frame-ancestors and X-Frame-Options are neutralized.
    ancestors = ' '.join(f'https://[{host}]' if ':' in host else f'https://{host}' for host in hosts(config))
    for row in rows:
        upstream = urlsplit(row['upstream'])
        body = (f'\theader +Content-Security-Policy "frame-ancestors \'self\' {ancestors}"\n'
                f'\tforward_auth 127.0.0.1:{config["port"]} {{\n'
                f'\t\turi /api/proxy-auth?app={row["slug"]}\n'
                f'\t\theader_up Host {urlsplit(config["url"]).netloc}\n\t}}\n'
                f'\treverse_proxy {row["upstream"]} {{\n'
                f'\t\theader_up Host {upstream.netloc}\n'
                # forward_auth checks the original public Origin before this rewrite.
                f'\t\theader_up Origin "^.+$" "{row["upstream"]}"\n'
                '\t\theader_up Cookie "(^|;[ ]*)alps_session=[^;]*" "$1"\n'
                '\t\theader_down Set-Cookie "^[[:space:]]*alps_session[[:space:]]*=" "alps_upstream_session="\n'
                '\t\theader_down -X-Frame-Options\n'
                '\t\theader_down Content-Security-Policy "frame-ancestors[^;]*" "frame-ancestors *"\n'
                '\t}\n')
        blocks.append(f'\n# Alps app: {row["slug"]}\n')
        blocks.extend(site_block(f'https://{host}:{row["port"]}', binds, private, body, sources) for host, binds, private, sources in sites(config))
    return ''.join(blocks)
