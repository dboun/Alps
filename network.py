"""Bounded web-listener discovery and same-site favicon lookup."""
from concurrent.futures import ThreadPoolExecutor
import http.client
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import struct
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

# Skip common infrastructure/admin protocols, including HTTP control APIs.
EXCLUDED_PORTS = {22, 25, 53, 110, 111, 135, 139, 143, 389, 445, 465, 587, 636,
                  993, 995, 1433, 1521, 2049, 2375, 2376, 3306, 5432, 5672, 5900,
                  6379, 6443, 9042, 9092, 9200, 9300, 11211, 27017}


def listeners():
    result = set()
    for family, filename in [(socket.AF_INET, '/proc/net/tcp'), (socket.AF_INET6, '/proc/net/tcp6')]:
        try:
            rows = Path(filename).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if fields[3] != '0A':
                continue
            address, port = fields[1].split(':')
            packed = bytes.fromhex(address)
            if family == socket.AF_INET:
                packed = packed[::-1]
            else:
                packed = b''.join(packed[i:i+4][::-1] for i in range(0, 16, 4))
            result.add((socket.inet_ntop(family, packed), int(port, 16)))
    return result


def probe_web(host, port):
    for scheme in (('https', 'http') if port in {443, 8443} else ('http', 'https')):
        connection = None
        try:
            if scheme == 'https':
                # Identification only, never sends credentials or accepts this trust for browsing.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                connection = http.client.HTTPSConnection(host, port, timeout=.4, context=context)
            else:
                connection = http.client.HTTPConnection(host, port, timeout=.4)
            connection.request('HEAD', '/', headers={'User-Agent': 'Alps local web discovery', 'Connection': 'close'})
            response = connection.getresponse()
            if 'text/html' in response.getheader('Content-Type', '').lower() or 300 <= response.status < 400:
                return {'port': port, 'scheme': scheme}
        except (OSError, http.client.HTTPException):
            pass
        finally:
            if connection:
                connection.close()
    return None


def discover_web(host, excluded=()):
    # Only probes sockets already listening on this machine, never a remote port range.
    addresses = {host, '0.0.0.0', '::'}
    if ipaddress.ip_address(host).is_loopback:
        addresses |= {'127.0.0.1', '::1'}
    ports = sorted({port for address, port in listeners()
                    if address in addresses and port not in EXCLUDED_PORTS and port not in excluded
                    and (port >= 1024 or port in {80, 443})}, reverse=True)[:128]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = pool.map(lambda port: probe_web(host, port), ports)
        return sorted((result for result in results if result), key=lambda result: result['port'], reverse=True)


class IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and 'icon' in attrs.get('rel', '').lower().split() and attrs.get('href'):
            self.links.append(attrs['href'])


class SameSiteRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if origin(newurl) != self.origin:
            raise ValueError('Cross-site icon redirect rejected.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def favicon(url, store):
    """Return a copied icon, or empty if absent. No cookies or credentials are forwarded."""
    site = origin(url)
    # Gmail's unauthenticated page and favicon both redirect off-site. Use only
    # this fixed public asset; never trust an arbitrary cross-site icon URL.
    gmail_icon = 'https://ssl.gstatic.com/ui/v1/icons/mail/rfr/gmail.ico' if site in {
        ('http', 'mail.google.com', 80), ('https', 'mail.google.com', 443)} else ''
    if gmail_icon:
        site = origin(gmail_icon)
    try:
        addresses = socket.getaddrinfo(site[1], site[2], type=socket.SOCK_STREAM)
        if any(ipaddress.ip_address(item[4][0]).is_link_local or ipaddress.ip_address(item[4][0]).is_multicast
               or ipaddress.ip_address(item[4][0]).is_unspecified for item in addresses):
            return ''
    except OSError:
        return ''
    context = ssl.create_default_context()
    # Trust this installation's Caddy CA for its own local HTTPS apps.
    for certificate in [Path(__file__).parent / 'alps-root.crt', Path('/usr/local/share/ca-certificates/alps-caddy.crt')]:
        try:
            if certificate.is_file():
                context.load_verify_locations(cafile=str(certificate))
        except (OSError, ssl.SSLError):
            pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameSiteRedirect(site), urllib.request.HTTPSHandler(context=context))

    def fetch(address, limit):
        if origin(address) != site:
            raise ValueError('Icons must come from the app itself.')
        request = urllib.request.Request(address, headers={'User-Agent': 'Alps favicon lookup', 'Accept': 'text/html,image/*'})
        with opener.open(request, timeout=1.5) as response:
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError('Response too large.')
            return content, response.headers.get_content_type(), response.geturl()

    candidates = [gmail_icon] if gmail_icon else []
    try:
        if not gmail_icon:
            html, mime, final_url = fetch(url, 192 * 1024)
            if mime == 'text/html':
                parser = IconLinks()
                parser.feed(html.decode('utf-8', errors='replace'))
                candidates = [urljoin(final_url, link) for link in parser.links[:3]]
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        pass
    if not gmail_icon:
        candidates.append(urljoin(url, '/favicon.ico'))
    for candidate in dict.fromkeys(candidates):
        try:
            content, mime, _ = fetch(candidate, 2 * 1024 * 1024)
            suffix = {'image/svg+xml': '.svg', 'image/png': '.png', 'image/jpeg': '.jpg',
                      'image/gif': '.gif', 'image/webp': '.webp', 'image/x-icon': '.ico',
                      'image/vnd.microsoft.icon': '.ico'}.get(mime, Path(urlsplit(candidate).path).suffix.lower())
            return store(content, suffix)
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            continue
    return ''
