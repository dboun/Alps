#!/usr/bin/env python3
"""Interactive Linux installation for Alps."""
import getpass
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from app import ROOT, password_hash, write_json
import gateway

CADDYFILE = Path('/etc/caddy/Caddyfile')
SNIPPET = Path('/etc/caddy/alps.caddy')
IMPORT = 'import /etc/caddy/alps.caddy'
MARKER = '# Managed by Alps'
FIREWALL = Path('/etc/alps/firewall.nft')
FIREWALL_UNIT = Path('/etc/systemd/system/alps-firewall.service')
CADDY_DROPIN = Path('/etc/systemd/system/caddy.service.d/alps.conf')
TAILSCALE_DEFAULTS = Path('/etc/default/tailscaled')
TAILSCALE_NET = ipaddress.IPv4Network('100.64.0.0/10')
TAILSCALE_STEPS = '''
HTTPS certificates are not enabled for your tailnet yet. On any computer, in a browser:
  1. Open https://login.tailscale.com/admin/dns and sign in.
  2. Under "MagicDNS", select "Enable MagicDNS" (skip if already enabled).
  3. Under "HTTPS Certificates", select "Enable HTTPS" and confirm.
  Optional, before step 3: the machine name ({name}) becomes public. To change it, open
  https://login.tailscale.com/admin/machines, open this computer's "..." menu, and choose
  "Edit machine name". The tailnet name (tailXXXX) is random unless you renamed it.
'''


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def ask(prompt, default=''):
    answer = input(f'{prompt}' + (f' [{default}]' if default else '') + ': ').strip()
    return answer or default


def yes(prompt, default=True):
    while True:
        value = ask(prompt + (' (Y/n)' if default else ' (y/N)'), 'y' if default else 'n').lower()
        if value in ('y', 'yes', 'n', 'no'):
            return value in ('y', 'yes')
        print('Enter y or n.')


def select(prompt, choices, default):
    while True:
        value = ask(prompt + ' (' + '/'.join(choices) + ')', default).lower()
        if value in choices:
            return value
        print('Choose ' + ', '.join(choices) + '.')


def lan_ip():
    try:
        route = subprocess.check_output(['ip', '-4', 'route', 'get', '1.1.1.1'], text=True).split()
        candidate = route[route.index('src') + 1]
        if ipaddress.ip_address(candidate).is_private:
            return candidate
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        pass
    return ''


def interface_addresses():
    """[(interface, IPv4)] on this computer that may serve Alps: private or Tailscale, never loopback or public."""
    try:
        lines = subprocess.check_output(['ip', '-4', '-o', 'addr', 'show'], text=True).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []
    result = []
    for line in lines:
        parts = line.split()
        try:
            interface, network = parts[1].split('@')[0], ipaddress.IPv4Interface(parts[3])
            ip = network.ip
        except (IndexError, ValueError):
            continue
        # The interface name goes into firewall rules, so accept only plain names.
        # Container and VM bridges only reach this computer's own guests.
        bridge = re.match(r'(docker|br-|veth|virbr|vnet|cni|flannel|podman|lxcbr|lxdbr|incusbr)', interface)
        if not ip.is_loopback and not bridge and (ip.is_private or ip in TAILSCALE_NET) and re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface):
            result.append((interface, str(ip), str(network.network)))
    return result


def choose_lan(old, available):
    print('\nAddresses on this computer:')
    for interface, ip, _ in available:
        print(f'  {ip:<16} {interface}')
    default = old.get('lan') or (old.get('address') if old.get('bind') == '0.0.0.0' and 'access' not in old else '') or lan_ip()
    while True:
        ip = ask('Home network (LAN) address, usually on your Wi-Fi/Ethernet interface', default)
        if any(ip == item and ipaddress.IPv4Address(ip).is_private for _, item, _ in available):
            return ip
        print('Enter one of the private addresses listed above, for example 192.168.1.50.')


def choose_extra(old, available, lan):
    choices = [choice for choice in available if choice[1] != lan]
    if not choices:
        raise SystemExit('No other private or Tailscale address is on this computer. Connect Tailscale (or the network) and run ./install.sh again.')
    print('\nOther addresses on this computer:')
    for number, (interface, ip, _) in enumerate(choices, 1):
        print(f'  {number}. {ip:<16} {interface}' + ('  (Tailscale)' if ipaddress.IPv4Address(ip) in TAILSCALE_NET else ''))
    previous = [item['ip'] for item in old.get('extra', []) if any(item['ip'] == choice[1] for choice in choices)]
    while True:
        answer = ask('Also serve on (numbers or addresses, comma-separated)', ', '.join(previous))
        picked = []
        for token in filter(None, (part.strip() for part in answer.split(','))):
            match = choices[int(token) - 1] if token.isdigit() and 0 < int(token) <= len(choices) else next((item for item in choices if item[1] == token), None)
            if not match:
                break
            if match not in picked:
                picked.append(match)
        else:
            if picked:
                return [dict(interface=interface, ip=ip, network=network) for interface, ip, network in picked]
        print('Choose one or more of the numbers or addresses listed above.')


def tailscale_status():
    try:
        return json.loads(subprocess.check_output(['tailscale', 'status', '--json'], text=True, stderr=subprocess.DEVNULL))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {}


def choose_tailscale_name(old, extra):
    """The machine's *.ts.net name with a publicly trusted certificate, or {} to keep using the Alps certificate."""
    ts_ip = next((item['ip'] for item in extra if ipaddress.IPv4Address(item['ip']) in TAILSCALE_NET), None)
    if not ts_ip or not shutil.which('tailscale'):
        return {}
    print('\nTailscale can give this computer a name like mypc.tail1234.ts.net with a certificate')
    print('every browser already trusts: no warning and no Alps certificate to install on your devices.')
    print('The name becomes public in certificate logs, but only your Tailscale devices can open it.')
    if not yes('Use the Tailscale name with a trusted certificate?', bool(old.get('tailscale_name'))):
        return {}
    while True:
        status = tailscale_status()
        own = status.get('Self') or {}
        name = str(own.get('DNSName', '')).rstrip('.').lower()
        ips = [ip for ip in own.get('TailscaleIPs') or [] if ip == ts_ip or ipaddress.ip_address(ip).version == 6]
        if ts_ip not in ips:
            print(f'Tailscale reports another address than {ts_ip}. Check: tailscale ip')
        elif not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net', name):
            print('Tailscale did not report a ts.net name for this computer.')
        elif name in [domain.rstrip('.').lower() for domain in status.get('CertDomains') or []]:
            print(f'Tailscale name: {name}')
            return dict(tailscale_name=name, tailscale_ips=ips)
        print(TAILSCALE_STEPS.format(name=name.split('.')[0] or 'this computer'))
        if select('Retry after enabling it, or skip and keep using the Alps certificate', ['retry', 'skip'], 'retry') == 'skip':
            return {}


def collect(old):
    print('\nWho can open Alps?')
    print('  local    this computer only')
    print('  lan      your home network')
    print('  lan+ips  your home network and chosen addresses, such as Tailscale')
    print('  ips      only chosen addresses, such as Tailscale; the home network cannot connect')
    access = select('Access', ['local', 'lan', 'lan+ips', 'ips'], old.get('access') or ('local' if old.get('bind') == '127.0.0.1' else 'lan'))
    bind = '127.0.0.1' if access == 'local' else '0.0.0.0'
    lan, extra, tailscale = '', [], {}
    if access != 'local':
        available = interface_addresses()
        if access in ('lan', 'lan+ips'):
            lan = choose_lan(old, available)
        if access in ('lan+ips', 'ips'):
            extra = choose_extra(old, available, lan)
            tailscale = choose_tailscale_name(old, extra)
    address = lan or (extra[0]['ip'] if extra else '127.0.0.1')
    if bind == '0.0.0.0':
        # Plain HTTP would send the password and session cookie across the LAN.
        print('Home-network access uses HTTPS, so your password is never sent unencrypted.')
        protocol = 'https'
    else:
        protocol = select('Protocol: HTTPS uses a private certificate on port 443', ['http', 'https'], 'https' if old.get('https', True) else 'http')
    if protocol == 'https':
        while not shutil.which('caddy'):
            print('\nCaddy is not installed. In another terminal, run:\n  sudo apt update\n  sudo apt install caddy')
            print('Other distributions: https://caddyserver.com/docs/install')
            choice = select('Then retry, use HTTP on this computer only for now, or quit', ['retry', 'http', 'quit'], 'quit')
            if choice == 'quit':
                raise SystemExit('No settings changed. Run ./install.sh after installing Caddy.')
            if choice == 'http':
                protocol, access, bind, address, lan, extra, tailscale = 'http', 'local', '127.0.0.1', '127.0.0.1', '', [], {}
                break
    while True:
        try:
            port = int(ask('Alps backend port (HTTPS stays on 443)' if protocol == 'https' else 'HTTP port', str(old.get('port', 8020))))
            if not 1024 <= port <= 65535:
                raise ValueError()
            break
        except ValueError:
            print('Choose a port from 1024 to 65535, such as 8020.')
    while True:
        username = ask('Username', old.get('username', getpass.getuser()))
        if username and len(username) <= 80:
            break
        print('Enter a username up to 80 characters.')
    while True:
        password = getpass.getpass('Password (Enter to keep current): ' if old.get('password_hash') else 'Password (at least 12 characters): ')
        if not password and old.get('password_hash'):
            salt, hashed = old['salt'], old['password_hash']
            break
        if len(password) < 12:
            print('Use at least 12 characters.')
        elif password != getpass.getpass('Confirm password: '):
            print('Passwords do not match. Try again.')
        else:
            salt = secrets.token_hex(16)
            hashed = password_hash(password, salt)
            break
    https = protocol == 'https'
    config = dict(access=access, bind=bind, address=address, lan=lan, extra=extra, **tailscale,
                  host='127.0.0.1' if https else bind, port=port, https=https, username=username, salt=salt,
                  password_hash=hashed, url=f'{protocol}://{address}' + ('' if https else f':{port}'))
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if https:
        hosts.update(['localhost', '127.0.0.1', *gateway.hosts(config)])
    config['allowed_hosts'] = sorted(hosts)
    return config


def caddy_text(config, rows=None):
    proxy = f'\treverse_proxy 127.0.0.1:{config["port"]}\n'
    text = f'{MARKER}\n' + gateway.site_block('https://localhost https://127.0.0.1', ['127.0.0.1'], True, proxy)
    for host, binds, private, sources in gateway.sites(config):
        if host != '127.0.0.1':
            text += gateway.site_block('https://' + host, binds, private, proxy, sources)
    return text + gateway.caddy_blocks(config, gateway.read_exposed(ROOT / 'data') if rows is None else rows)


def read_system(path):
    try:
        return path.read_text() if path.exists() else ''
    except PermissionError:
        return subprocess.check_output(['sudo', 'cat', str(path)], text=True)


def restore_caddy(backups):
    for path, backup in backups.items():
        if backup:
            run(['sudo', 'cp', '-a', backup, str(path)])
        else:
            run(['sudo', 'rm', '-f', str(path)])
    subprocess.run(['sudo', 'systemctl', 'restart', 'caddy'], check=False)


def install_system_file(path, text, check=None, mode='644'):
    """Install root-owned text with sudo when it changed; check(temporary path) runs first."""
    if read_system(path) == text:
        return False
    with tempfile.TemporaryDirectory(prefix='alps-') as folder:
        temporary = Path(folder) / path.name
        temporary.write_text(text)
        if check:
            check(temporary)
        run(['sudo', 'install', '-D', '-m', mode, str(temporary), str(path)])
    return True


def pinned_addresses(config):
    """[(ip, interface)] that must only be reached through their own interface."""
    if config.get('bind') != '0.0.0.0':
        return []
    pinned = [(item['ip'], item['interface']) for item in config.get('extra', [])]
    if config.get('tailscale_name'):
        interface = next(item['interface'] for item in config['extra'] if item['ip'] in config['tailscale_ips'])
        pinned += [(ip, interface) for ip in config['tailscale_ips'] if ':' in ip]
    return pinned


def firewall_text(config):
    # Linux accepts traffic for any of its addresses on any interface. Without this, a LAN device
    # could route packets for the Tailscale address to this computer's Wi-Fi and reach Alps.
    rules = ''.join(f'\t\t{"ip6" if ":" in ip else "ip"} daddr {ip} iifname != {{ "lo", "{interface}" }} drop\n'
                    for ip, interface in pinned_addresses(config))
    if not rules:
        return ''
    return (f'#!/usr/sbin/nft -f\n{MARKER}\ntable inet alps\ndelete table inet alps\ntable inet alps {{\n'
            '\tchain input {\n\t\ttype filter hook input priority filter - 5; policy accept;\n' + rules + '\t}\n}\n')


def configure_firewall(config):
    text = firewall_text(config)
    if not text:
        if FIREWALL_UNIT.exists():
            # Drop Caddy's dependency first, or stopping the rule would stop Caddy too.
            if CADDY_DROPIN.exists():
                install_system_file(CADDY_DROPIN, caddy_boot(config))
                run(['sudo', 'systemctl', 'daemon-reload'])
            run(['sudo', 'systemctl', 'disable', '--now', FIREWALL_UNIT.name])
            run(['sudo', 'rm', '-f', str(FIREWALL_UNIT), str(FIREWALL)])
            run(['sudo', 'systemctl', 'daemon-reload'])
        return
    nft = shutil.which('nft') or '/usr/sbin/nft'
    if not Path(nft).exists():
        raise ValueError('Chosen addresses need nftables for their firewall rule. Run: sudo apt install nftables')
    if previous := read_system(FIREWALL):
        if not previous.startswith('#!/usr/sbin/nft -f\n' + MARKER):
            raise ValueError(f'{FIREWALL} is not managed by Alps. Move it before continuing.')
    print('Adding a firewall rule so chosen addresses only accept traffic from their own network (needs sudo).')
    install_system_file(FIREWALL, text, lambda path: run(['sudo', nft, '-c', '-f', str(path)]))
    unit = ('[Unit]\nDescription=Alps: chosen addresses accept traffic only from their own network\n'
            'DefaultDependencies=no\nWants=network-pre.target\nBefore=network-pre.target caddy.service shutdown.target\n'
            # nftables.service starts with "flush ruleset"; apply again after it.
            'After=nftables.service\nPartOf=nftables.service\nConflicts=shutdown.target\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            f'ExecStart={nft} -f {FIREWALL}\nExecReload={nft} -f {FIREWALL}\nExecStop={nft} delete table inet alps\n\n'
            '[Install]\nWantedBy=sysinit.target\n')
    install_system_file(FIREWALL_UNIT, unit)
    run(['sudo', 'systemctl', 'daemon-reload'])
    run(['sudo', 'systemctl', 'enable', FIREWALL_UNIT.name])
    run(['sudo', 'systemctl', 'reload-or-restart', FIREWALL_UNIT.name])


def allow_tailscale_certificates():
    """Let the caddy user ask tailscaled for this computer's ts.net certificate."""
    current = read_system(TAILSCALE_DEFAULTS)
    lines = current.splitlines()
    if 'TS_PERMIT_CERT_UID=caddy' in [line.strip() for line in lines]:
        return
    manual = ('Let Caddy fetch the Tailscale certificate: set TS_PERMIT_CERT_UID=caddy in the tailscaled service '
              'environment, restart tailscaled, then run ./install.sh again.')
    if not TAILSCALE_DEFAULTS.exists() or any(line.strip().startswith('TS_PERMIT_CERT_UID=') for line in lines):
        raise ValueError(manual)
    print('Caddy needs permission to fetch the certificate from Tailscale. This restarts Tailscale,')
    print('which drops Tailscale connections (including an SSH session over Tailscale) for a few seconds.')
    if not yes(f'Add TS_PERMIT_CERT_UID=caddy to {TAILSCALE_DEFAULTS} and restart Tailscale?'):
        raise ValueError(manual)
    # Keep its permissions: it can hold a Tailscale auth key.
    mode = subprocess.check_output(['stat', '-c', '%a', str(TAILSCALE_DEFAULTS)], text=True).strip()
    install_system_file(TAILSCALE_DEFAULTS, current.rstrip('\n') + '\n\n# Lets Caddy fetch the ts.net certificate for Alps.\nTS_PERMIT_CERT_UID=caddy\n', mode=mode)
    run(['sudo', 'systemctl', 'restart', 'tailscaled'])
    for _ in range(30):
        if (tailscale_status().get('BackendState')) == 'Running':
            return
        time.sleep(1)


def caddy_boot(config):
    # Caddy listens on specific addresses; Tailscale's may appear after Caddy starts.
    firewall = 'Requires=alps-firewall.service\nAfter=alps-firewall.service\n' if pinned_addresses(config) else ''
    return f'# Managed by Alps\n[Unit]\nAfter=tailscaled.service\n{firewall}\n[Service]\nRestart=on-failure\nRestartSec=5\n'


def configure_caddy(config):
    current = read_system(CADDYFILE)
    previous = read_system(SNIPPET)
    if previous and not previous.startswith(MARKER):
        raise ValueError(f'{SNIPPET} is not managed by Alps. Choose another filename before continuing.')
    if config['https']:
        if current.strip() and not yes(f'Caddy already has settings at {CADDYFILE}. Add/update Alps and keep existing sites?'):
            raise SystemExit('Caddy was not changed.')
        if previous and not yes(f'Update the existing Alps HTTPS setup at {SNIPPET}?'):
            raise SystemExit('Caddy was not changed.')
        new_snippet = caddy_text(config)
        new_main = current if IMPORT in [line.strip() for line in current.splitlines()] else current.rstrip() + '\n\n' + IMPORT + '\n'
    elif previous and IMPORT in [line.strip() for line in current.splitlines()]:
        if not yes('Switching to HTTP. Disable the existing Alps HTTPS site, keeping other Caddy sites?'):
            raise SystemExit('Setup cancelled. Existing HTTPS was kept.')
        new_main = '\n'.join(line for line in current.splitlines() if line.strip() != IMPORT) + '\n'
        new_snippet = ''
    else:
        return
    print('Caddy changes need sudo. Existing files will be backed up.')
    stamp = str(time.time_ns())
    backups = {CADDYFILE: None, SNIPPET: None}
    for path in [CADDYFILE, SNIPPET]:
        if path.exists():
            backup = str(path) + '.alps-backup-' + stamp
            run(['sudo', 'cp', '-a', str(path), backup])
            backups[path] = backup
    try:
        with tempfile.TemporaryDirectory(prefix='alps-caddy-') as folder:
            main = Path(folder) / 'Caddyfile'
            snippet = Path(folder) / 'alps.caddy'
            main.write_text(new_main)
            snippet.write_text(new_snippet)
            run(['sudo', 'install', '-d', '-m', '755', '/etc/caddy'])
            run(['sudo', 'install', '-m', '644', str(snippet), str(SNIPPET)])
            run(['sudo', 'install', '-m', '644', str(main), str(CADDYFILE)])
            run(['sudo', 'caddy', 'validate', '--config', str(CADDYFILE), '--adapter', 'caddyfile'])
            if config['https']:
                install_system_file(CADDY_DROPIN, caddy_boot(config))
                run(['sudo', 'systemctl', 'daemon-reload'])
            run(['sudo', 'systemctl', 'enable', 'caddy'])
            # A reload cannot move a listener from all addresses to one address; restart instead.
            run(['sudo', 'systemctl', 'restart', 'caddy'])
    except (OSError, subprocess.CalledProcessError):
        print('Caddy could not apply Alps. Restoring the previous Caddy files.')
        restore_caddy(backups)
        print('Port 443 may already belong to another site or server. Check: sudo journalctl -u caddy -n 40')
        raise
    for backup in backups.values():
        if backup:
            print('Backup: ' + backup)
    return backups


def service_text():
    def quoted(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    return ('[Unit]\nDescription=Alps home launcher\nAfter=network.target\n\n'
            '[Service]\nType=simple\n' + f'WorkingDirectory={str(ROOT).replace("%", "%%")}\n'
            f'ExecStart={quoted(sys.executable)} {quoted(ROOT / "app.py")}\n'
            'Restart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=yes\n\n'
            '[Install]\nWantedBy=default.target\n')


def print_access(config):
    print('\nOpen ' + config['url'])
    if config['https']:
        for host in gateway.hosts(config)[1:]:
            print('  or https://' + host)
    if config.get('tailscale_name'):
        print('\nhttps://' + config['tailscale_name'] + ' uses a certificate every browser trusts; no import needed there.')
        print('The first visit can take a few seconds while Caddy fetches it from Tailscale.')
    if config['bind'] == '0.0.0.0':
        if config.get('lan') or 'access' not in config:
            print('Reserve this computer\'s LAN IP in your router. Do not enable router port forwarding.')
        if config.get('access') == 'ips':
            print('Devices on your home network cannot connect; only the chosen addresses above work.')
        print('If your firewall is on, allow TCP port ' + ('443 (and 80 for HTTP redirects)' if config['https'] else str(config['port'])) + ' on the chosen networks.')
    if config['https']:
        certificate = '/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt'
        print('\nFor the IP addresses, Caddy creates a private certificate; no public certificate or domain is needed.')
        print('Certificate: ' + certificate)
        print('Trust it on this Ubuntu/Debian computer:\n  sudo install -m 644 ' + certificate + ' /usr/local/share/ca-certificates/alps-caddy.crt\n  sudo update-ca-certificates')
        print('\nTo copy the certificate to another computer, first export the public certificate here:')
        destination = ROOT / 'alps-root.crt'
        print('  sudo install -m 644 ' + certificate + ' ' + shlex.quote(str(destination)))
        print('Then run on the other computer (replace the placeholders; SSH must be enabled on the Alps computer):')
        source = '<linux-user>@<alps-computer-ip>:' + shlex.quote(str(destination))
        print('  scp ' + shlex.quote(source) + ' ./alps-root.crt')
        print('Trust it there (Ubuntu/Debian):\n  sudo install -m 644 alps-root.crt /usr/local/share/ca-certificates/alps-caddy.crt\n  sudo update-ca-certificates')
        print('You can also transfer alps-root.crt by USB. On phones/tablets, install it as a trusted CA certificate.')
        print('On iPhone/iPad, also enable full trust in Settings > General > About > Certificate Trust Settings.')
        print('Chrome on Linux: open chrome://certificate-manager/localcerts/usercerts; under Trusted Certificates choose Import and select alps-root.crt. Fully quit and reopen Chrome.')
        print('Older Chrome: chrome://settings/certificates > Authorities > Import; trust the CA to identify websites.')
        print('Firefox on Linux: Settings > Privacy & Security > Certificates > View Certificates > Authorities > Import alps-root.crt; trust it to identify websites.')
        print('Copy only root.crt, never root.key. A browser with its own certificate store may need an import too.')
        print('Until trusted, a certificate warning is expected. Use the HTTPS bookmark, not the backend HTTP address.')
    else:
        print('HTTP works on this computer only. Run ./install.sh again to switch to HTTPS for home-network access.')
    print('\nStop: ./install.sh --stop\nLogs: journalctl --user -u alps.service -n 30')


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run ./install.sh as your normal user, without sudo.')
    if sys.argv[1:] == ['--stop']:
        run(['systemctl', '--user', 'disable', '--now', 'alps.service'])
        print('Alps stopped. Apps and settings were kept. The Caddy site was kept for the next start.')
        return
    if sys.argv[1:]:
        raise SystemExit('Usage: ./install.sh [--stop]')
    if not shutil.which('systemctl'):
        raise SystemExit('Alps installation needs Linux with systemd.')
    run(['systemctl', '--user', 'show-environment'], stdout=subprocess.DEVNULL)
    path = ROOT / 'config.json'
    old = json.loads(path.read_text()) if path.exists() else {}
    print('Alps — your home-network app launcher\n')
    reuse = False
    if old:
        print('Existing setup: ' + ', '.join(['https://' + host for host in gateway.hosts(old)] if old.get('https') else [old.get('url', 'unknown address')]))
        print('Choose change to set who can connect, for example adding Tailscale.')
        choice = select('Keep setup, change it, or quit (saved apps are kept)', ['keep', 'change', 'quit'], 'keep')
        if choice == 'quit':
            return
        reuse = choice == 'keep'
    config = old if reuse else collect(old)
    if config.get('host') != '127.0.0.1' or (not config['https'] and config['bind'] != '127.0.0.1'):
        raise SystemExit('HTTP is only allowed for this computer. Run ./install.sh, choose change, and pick HTTPS or 127.0.0.1.')
    if config['https'] and not shutil.which('caddy'):
        raise SystemExit('Caddy is missing. Run: sudo apt install caddy\nThen run ./install.sh again. No settings changed.')
    if not config['https'] and gateway.read_exposed(ROOT / 'data'):
        raise ValueError('Remove exposed apps with alps unexpose before switching Alps to HTTP.')
    print('\nWill serve ' + config['url'] + ' as ' + config['username'] + '.')
    unit_folder = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'
    unit_folder.mkdir(parents=True, exist_ok=True)
    unit = unit_folder / 'alps.service'
    if unit.exists() and 'Description=Alps home launcher' not in unit.read_text():
        raise SystemExit(f'{unit} belongs to another service. Rename that service before installing Alps.')
    if config['https']:
        # The rule must exist before Caddy listens on the chosen addresses.
        configure_firewall(config)
        if config.get('tailscale_name'):
            allow_tailscale_certificates()
    # Caddy validates and restores on failure before changing the app credentials/service.
    caddy_backups = configure_caddy(config)
    was_active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'alps.service']).returncode == 0
    if was_active:
        run(['systemctl', '--user', 'stop', 'alps.service'])
    previous_config = path.read_bytes() if path.exists() else None
    previous_unit = unit.read_bytes() if unit.exists() else None
    try:
        write_json(path, config)
        unit.write_text(service_text())
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'enable', '--now', 'alps.service'])
        time.sleep(.6)
        run(['systemctl', '--user', 'is-active', '--quiet', 'alps.service'])
    except (OSError, subprocess.CalledProcessError):
        print('Alps could not start. Restoring its previous settings. Check journalctl --user -u alps.service -n 30')
        if previous_config is not None:
            path.write_bytes(previous_config)
        else:
            path.unlink(missing_ok=True)
        if previous_unit is not None:
            unit.write_bytes(previous_unit)
        else:
            subprocess.run(['systemctl', '--user', 'disable', '--now', 'alps.service'], check=False)
            unit.unlink(missing_ok=True)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
        if was_active:
            subprocess.run(['systemctl', '--user', 'restart', 'alps.service'], check=False)
        if caddy_backups:
            restore_caddy(caddy_backups)
        raise
    linger = subprocess.run(['loginctl', 'show-user', getpass.getuser(), '-p', 'Linger', '--value'], capture_output=True, text=True) if shutil.which('loginctl') else None
    if linger and linger.stdout.strip() != 'yes':
        if yes('Start Alps at boot, even before you log in?'):
            if subprocess.run(['loginctl', 'enable-linger', getpass.getuser()]).returncode:
                print('An administrator can enable boot startup with: sudo loginctl enable-linger ' + shlex.quote(getpass.getuser()))
    from cli import install_command
    install_command()
    print_access(config)


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nSetup cancelled.')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit('Setup stopped: ' + str(exc))
