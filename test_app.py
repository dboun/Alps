"""Run with python3 -m unittest. Uses only temporary files and a loopback server."""
import http.client
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch
import app
import network
import gateway
import cli
import setup


class AlpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = dict(host='127.0.0.1', port=0, username='owner', salt='ab' * 16,
                           password_hash=app.password_hash('my-test-password', 'ab' * 16), https=True)
        self.server = app.AlpsServer(('127.0.0.1', 0), self.config, self.folder)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''
        self.host = '127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        request_headers = {'Host': self.host, 'X-Alps': '1', 'Origin': 'https://' + self.host}
        if self.cookie:
            request_headers['Cookie'] = self.cookie
        request_headers.update(headers or {})
        connection.request('GET' if body is None else 'POST', path,
                           None if body is None else json.dumps(body), request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        connection.close()
        return result

    def login(self):
        status, headers, _ = self.request('/api/login', dict(username='owner', password='my-test-password'))
        self.assertEqual(status, 200)
        self.assertIn('; Secure', headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.cookie = headers['Set-Cookie'].split(';')[0]

    def test_installed_app_files_and_frame_policy(self):
        status, headers, payload = self.request('/')
        self.assertRegex(payload.decode(), r'/shell\.js\?v=[0-9a-f]{12}')
        # Exposed apps (other ports) may be framed; Alps itself only by Alps, for sign-in redirects inside a tab.
        self.assertIn("frame-src https://127.0.0.1:*;", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        status, headers, _ = self.request('/manifest.webmanifest')
        self.assertEqual((status, headers['Content-Type']), (200, 'application/manifest+json'))
        self.assertEqual(self.request('/icon-512.png')[0], 200)

    def test_auth_crud_icons_and_persistence(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/api/apps')[0], 401)
        self.assertEqual(self.request('/api/login', dict(username='owner', password='wrong'))[0], 401)
        self.login()
        with patch.object(app, 'local_address', return_value='192.168.1.42'):
            status, _, payload = self.request('/api/session')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)['address'], '192.168.1.42')
        icon = self.folder / 'my icon.svg'
        icon.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        status, _, payload = self.request('/api/apps/save', dict(name='Terminal', url='192.168.1.2:8010/path?q=one', icon_path=str(icon)))
        self.assertEqual(status, 200)
        saved = json.loads(payload)[0]
        self.assertEqual(saved['url'], 'http://192.168.1.2:8010/path?q=one')
        self.assertEqual(self.request('/icons/' + saved['icon'])[0], 200)
        self.assertEqual(json.loads((self.folder / 'apps.json').read_text())[0], saved)
        restarted = app.AlpsServer(('127.0.0.1', 0), self.config, self.folder)
        self.assertEqual(restarted.apps[0], saved)
        restarted.server_close()
        icon.unlink()  # Copy remains usable and edits do not need the original file.
        status, _, payload = self.request('/api/apps/save', dict(saved, name='Aeterna'))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)[0]['name'], 'Aeterna')
        for address in ['javascript:alert(1)', 'http://bad<host>', 'http://999.999.999.999', 'http://%', 'https://user:password@home.local']:
            self.assertEqual(self.request('/api/apps/save', dict(name='Invalid', url=address))[0], 400)
        self.assertEqual(self.request('/api/apps/save', dict(name='Missing', url='http://a', icon_path='/not/here.svg'))[0], 400)
        self.assertEqual(self.request('/api/apps/save', dict(name='One', url='http://one'), {'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('/api/apps/delete', {'id': saved['id']})[0], 200)
        self.assertEqual(self.request('/icons/' + saved['icon'])[0], 404)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/apps')[0], 401)

    def test_reorder_favicon_and_port_privacy(self):
        self.assertEqual(self.request('/api/ports?host=127.0.0.1')[0], 401)
        self.login()
        for name in ['First', 'Second', 'Third']:
            self.request('/api/apps/save', {'name': name, 'url': 'http://localhost:11500'})
        ids = [item['id'] for item in self.server.apps]
        cells = [{'id': ident, 'x': 2 - index, 'y': 0} for index, ident in enumerate(ids)]
        self.assertEqual(self.request('/api/apps/layout', {'positions': cells})[0], 200)
        self.assertEqual([item['name'] for item in self.server.apps], ['Third', 'Second', 'First'])
        self.assertEqual(self.request('/api/apps/layout', {'positions': [cells[0]] * 3})[0], 400)
        self.assertEqual(self.request('/api/apps/layout', {'positions': [dict(cells[0], x=True)] + cells[1:]})[0], 400)
        self.assertEqual(self.request('/api/apps/layout', {'positions': [dict(cells[0], y=-1)] + cells[1:]})[0], 400)
        self.assertEqual(self.request('/api/apps/layout', {'positions': [dict(cells[0], id=[1])] + cells[1:]})[0], 400)
        self.assertEqual(self.request('/api/apps/size', {'id': ids[0], 'small': True})[0], 200)
        self.request('/api/apps/save', {'id': ids[0], 'name': 'First', 'url': 'http://localhost:11501'})
        first = next(item for item in self.server.apps if item['id'] == ids[0])
        self.assertEqual((first['small'], first['x'], first['y']), (True, 2, 0))
        self.assertEqual(self.request('/api/apps/size', {'id': ids[0], 'small': 'yes'})[0], 200)
        self.assertNotIn('small', next(item for item in self.server.apps if item['id'] == ids[0]))
        self.assertEqual(self.request('/api/apps/size', {'id': 'missing', 'small': True})[0], 404)
        self.assertEqual(self.request('/api/ports?host=192.0.2.1')[0], 400)
        with patch.object(network, 'discover_web', return_value=[{'port': 11500, 'scheme': 'http'}]):
            self.assertEqual(json.loads(self.request('/api/ports?host=127.0.0.1')[2])[0]['port'], 11500)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        with patch.object(network, 'favicon', side_effect=lambda url, store: store(svg, '.svg')):
            status, _, payload = self.request('/api/favicon', {'url':'http://localhost:11500'})
            self.assertEqual(status, 200)
            icon = json.loads(payload)['icon']
            self.assertEqual(self.request('/icons/' + icon)[0], 200)
            status, _, payload = self.request('/api/apps/save', {'name':'Icon', 'url':'http://localhost:11500', 'auto_icon': icon})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)[-1]['icon'], icon)
        with patch.object(app.Path, 'expanduser', return_value=self.folder/'icon.svg'):
            (self.folder/'icon.svg').write_bytes(svg)
            self.assertTrue(app.import_icon('~/icon.svg',self.folder/'icons'))

    def test_proxy_auth_and_cli_app_updates(self):
        self.config.update(address='192.168.1.42',url='https://192.168.1.42',bind='0.0.0.0')
        row=dict(slug='notebook',name='Notebook',port=18443,upstream='http://127.0.0.1:3000')
        with patch.object(cli,'reload_caddy'):
            cli.apply(self.config,self.folder,[row])
        headers={'X-Forwarded-Host':'192.168.1.42:18443','Origin':'https://192.168.1.42:18443'}
        status,response,_=self.request('/api/proxy-auth?app=notebook',headers=headers)
        self.assertEqual(status,302)
        self.assertEqual(response['Location'],'https://192.168.1.42/?open=notebook')
        saved=dict(self.config)
        self.config.update(access='ips',lan='',extra=[dict(interface='tailscale0',ip='100.70.0.5')])
        status,response,_=self.request('/api/proxy-auth?app=notebook',headers={'X-Forwarded-Host':'100.70.0.5:18443','Origin':'https://100.70.0.5:18443'})
        # Signing in happens on the address the app was opened on, so the cookie reaches the app.
        self.assertEqual((status,response['Location']),(302,'https://100.70.0.5/?open=notebook'))
        self.config.clear(); self.config.update(saved)
        self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers=dict(headers,**{'X-Forwarded-Method':'POST'}))[0],401)
        self.login()
        self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers=headers)[0],200)
        self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers=dict(headers,Origin='https://evil.test'))[0],403)
        self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers=dict(headers,**{'X-Forwarded-Host':'evil.test'}))[0],403)
        self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers=dict(headers,**{'X-Forwarded-Host':'192.168.1.42:18444'}))[0],403)
        self.config.update(access='lan+ips',lan='192.168.1.42',extra=[dict(interface='tailscale0',ip='100.70.0.5')],
                                  tailscale_name='pc.tail1.ts.net',tailscale_ips=['100.70.0.5'])
        for host in ['100.70.0.5:18443','pc.tail1.ts.net:18443']:
            self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers={'X-Forwarded-Host':host,'Origin':'https://'+host})[0],200)
            # The app's Origin must match the address it was opened on.
            self.assertEqual(self.request('/api/proxy-auth?app=notebook',headers={'X-Forwarded-Host':host,'Origin':'https://192.168.1.42:18443'})[0],403)
        self.assertEqual(json.loads(self.request('/api/session')[2])['hosts'],['192.168.1.42','100.70.0.5','pc.tail1.ts.net'])
        saved=json.loads(self.request('/api/apps')[2])[0]
        self.assertEqual(saved['proxy'],'notebook')
        self.assertEqual(self.request('/api/apps/delete',{'id':saved['id']})[0],400)
        self.assertEqual(self.request('/api/apps/save',dict(saved,url='http://elsewhere'))[0],400)
        with patch.object(cli,'reload_caddy',side_effect=ValueError('bad Caddy config')):
            with self.assertRaises(ValueError):
                cli.apply(self.config,self.folder,[])
        self.assertEqual(gateway.read_exposed(self.folder),[row])
        with patch.object(cli,'reload_caddy'):
            cli.apply(self.config,self.folder,[])
        self.assertEqual(json.loads(self.request('/api/apps')[2]),[])

    def test_host_csrf_and_rate_limit(self):
        self.assertEqual(self.request('/', headers={'Host': 'attacker.test'})[0], 403)
        self.assertEqual(self.request('/api/login', {}, {'X-Alps': ''})[0], 403)
        for _ in range(5):
            self.assertEqual(self.request('/api/login', {'username': 'x', 'password': 'bad'})[0], 401)
        self.assertEqual(self.request('/api/login', {'username': 'x', 'password': 'bad'})[0], 429)

    def test_known_device_sessions_last_and_survive_restart(self):
        self.login()
        # A new password signs everyone out; the stored key is not the token itself.
        stored = json.loads((self.folder / 'sessions.json').read_text())
        self.assertNotIn(self.cookie.split('=', 1)[1], json.dumps(stored))
        restarted = app.AlpsServer(('127.0.0.1', 0), self.config, self.folder)
        self.assertEqual(restarted.sessions, stored)
        restarted.server_close()
        # Idle sessions slide forward on use.
        key = next(iter(stored))
        with self.server.lock:
            self.server.sessions[key][0] = app.time.time() + 3600
        self.assertEqual(self.request('/api/apps')[0], 200)
        self.assertGreater(self.server.sessions[key][0], app.time.time() + app.SESSION_IDLE - 60)
        # Strangers exhaust the daily allowance; this browser still signs in after logging out.
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/apps')[0], 401)
        known, self.cookie = self.cookie, ''
        for _ in range(5):
            self.request('/api/login', {'username': 'x', 'password': 'bad'})
        self.assertEqual(self.request('/api/login', dict(username='owner', password='my-test-password'))[0], 429)
        self.cookie = known + '; ' + known
        self.assertEqual(self.request('/api/session')[2], self.request('/api/session', headers={'Cookie': ''})[2])
        self.cookie = 'x_' + known + '; a=b c; ' + known
        self.assertNotEqual(self.request('/api/session')[2], self.request('/api/session', headers={'Cookie': ''})[2])
        self.cookie = known
        self.login()
        self.assertNotEqual(self.cookie, known)
        self.assertNotIn(self.server.session_key(known.split('=', 1)[1]), self.server.sessions)
        # Remembered devices get the username; forgetting drops the record and the cookie.
        self.request('/api/logout', {})
        self.assertEqual(json.loads(self.request('/api/session')[2])['username'], 'owner')
        self.login()
        status, headers, _ = self.request('/api/logout', {'forget': True})
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(json.loads(self.request('/api/session')[2])['username'], '')
        # A remembered device can also be forgotten while signed out.
        self.server.attempts.clear()
        self.cookie = ''
        self.login()
        self.request('/api/logout', {})
        status, headers, _ = self.request('/api/forget-device', {})
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(json.loads(self.request('/api/session')[2])['username'], '')
        self.assertEqual(self.request('/api/forget-device', {}, {'X-Alps': ''})[0], 403)
        for _ in range(4):
            self.request('/api/login', {'username': 'x', 'password': 'bad'})
        # Forgotten, it shares the strangers' exhausted daily allowance.
        self.assertEqual(self.request('/api/login', dict(username='owner', password='my-test-password'))[0], 429)
        changed = dict(self.config, salt='cd' * 16, password_hash=app.password_hash('new-password-123', 'cd' * 16))
        renamed = app.AlpsServer(('127.0.0.1', 0), changed, self.folder)
        self.assertNotIn(renamed.session_key(self.cookie.split('=', 1)[1]), renamed.sessions)
        renamed.server_close()

    def test_setup_existing_password_and_caddy_config(self):
        previous = dict(self.config, bind='127.0.0.1', address='127.0.0.1', port=8020)
        with patch('builtins.input', side_effect=['', 'http', '8020', 'owner']), patch('getpass.getpass', return_value=''):
            config = setup.collect(previous)
        self.assertEqual(config['password_hash'], previous['password_hash'])
        self.assertEqual(config['url'], 'http://127.0.0.1:8020')
        interfaces = [('wlan0', '192.168.1.42', '192.168.1.0/24'), ('tailscale0', '100.70.0.5', '100.70.0.5/32'), ('docker0', '172.17.0.1', '172.17.0.0/16')]
        # Home-network access is HTTPS only; without Caddy, HTTP falls back to this computer.
        with patch('builtins.input', side_effect=['lan', '192.168.1.42', '8020', 'owner']), patch('getpass.getpass', return_value=''), \
                patch.object(setup, 'interface_addresses', return_value=interfaces), patch.object(setup.shutil, 'which', return_value='/usr/bin/caddy'):
            self.assertTrue(setup.collect(previous)['https'])
        with patch('builtins.input', side_effect=['lan', '192.168.1.42', 'http', '8020', 'owner']), patch('getpass.getpass', return_value=''), \
                patch.object(setup, 'interface_addresses', return_value=interfaces), patch.object(setup.shutil, 'which', return_value=None):
            fallback = setup.collect(previous)
        self.assertEqual((fallback['https'], fallback['bind'], fallback['host'], fallback['extra']), (False, '127.0.0.1', '127.0.0.1', []))
        # Addresses must belong to this computer; the second answer picks Tailscale by number.
        answers = ['lan+ips', '10.9.9.9', '100.70.0.5', '192.168.1.42', '9', '1', 'n', '8020', 'owner']
        with patch('builtins.input', side_effect=answers), patch('getpass.getpass', return_value=''), \
                patch.object(setup, 'interface_addresses', return_value=interfaces), patch.object(setup.shutil, 'which', return_value='/usr/bin/caddy'):
            config = setup.collect(previous)
        self.assertEqual((config['access'], config['lan'], config['extra']), ('lan+ips', '192.168.1.42', [dict(interface='tailscale0', ip='100.70.0.5', network='100.70.0.5/32')]))
        self.assertIn('100.70.0.5', config['allowed_hosts'])
        # Rerunning offers the previous choices as defaults.
        with patch('builtins.input', side_effect=['', '', '', 'n', '', '']), patch('getpass.getpass', return_value=''), \
                patch.object(setup, 'interface_addresses', return_value=interfaces), patch.object(setup.shutil, 'which', return_value='/usr/bin/caddy'):
            self.assertEqual(setup.collect(config)['extra'], config['extra'])
        text = setup.caddy_text(config, [])
        self.assertIn('https://localhost https://127.0.0.1 {\n\tbind 127.0.0.1\n', text)
        self.assertIn('https://192.168.1.42 {\n\tbind 192.168.1.42\n\ttls internal', text)
        self.assertIn('https://100.70.0.5 {\n\tbind 100.70.0.5\n\ttls internal\n\t@outside not remote_ip 100.64.0.0/10 fd7a:115c:a1e0::/48\n\thandle @outside {\n\t\tabort', text)
        self.assertNotIn('remote_ip', text.split('https://100.70.0.5')[0])
        self.assertIn('Requires=alps-firewall.service', setup.caddy_boot(config))
        self.assertNotIn('alps-firewall', setup.caddy_boot(dict(config, extra=[])))
        with self.assertRaises(ValueError):
            gateway.sites(dict(config, extra=[dict(interface='x" }\n', ip='100.70.0.5')]))
        self.assertIn('127.0.0.1:8020', text)
        only = dict(config, access='ips', lan='', address='100.70.0.5', tailscale_name='pc.tail1.ts.net', tailscale_ips=['100.70.0.5', 'fd7a::5'])
        text = setup.caddy_text(only, [])
        self.assertNotIn('192.168.1.42', text)
        self.assertIn('https://pc.tail1.ts.net {\n\tbind 100.70.0.5 [fd7a::5]\n\ttls {\n\t\tget_certificate tailscale\n\t}\n\t@outside', text)
        rules = setup.firewall_text(only)
        self.assertIn('ip daddr 100.70.0.5 iifname != { "lo", "tailscale0" } drop', rules)
        self.assertIn('ip6 daddr fd7a::5 iifname != { "lo", "tailscale0" } drop', rules)
        self.assertEqual(setup.firewall_text(dict(config, extra=[])), '')
        # Setups from before access modes keep their single LAN address, now with its own listener.
        legacy = dict(https=True, bind='0.0.0.0', address='192.168.1.42', port=8020)
        self.assertIn('https://192.168.1.42 {\n\tbind 192.168.1.42\n', setup.caddy_text(legacy, []))
        self.assertNotIn('192.168.1.42', setup.caddy_text(dict(legacy, bind='127.0.0.1'), []))


class CliTests(unittest.TestCase):
    def test_rejects_nonlocal_and_public_upstreams(self):
        config={'port':8020}
        for url in ['http://192.168.1.42:3000','https://127.0.0.1:3000','http://127.0.0.1:3000/path','http://127.0.0.1:8020']:
            with self.assertRaises(ValueError):
                cli.check_upstream(url,config)
        with patch.object(network,'listeners',return_value={('0.0.0.0',3000)}):
            with self.assertRaisesRegex(ValueError,'bypass Alps'):
                cli.check_upstream('http://127.0.0.1:3000',config)
        with patch.object(network,'listeners',return_value={('127.0.0.1',3000)}),patch.object(cli.socket,'create_connection'):
            self.assertEqual(cli.check_upstream('http://localhost:3000',config),'http://127.0.0.1:3000')
        with patch.object(network,'listeners',return_value={('127.0.0.1',18443)}):
            self.assertEqual(cli.select_port(None,[],'http://127.0.0.1:3000',config),18444)
            with self.assertRaises(ValueError):
                cli.select_port(18443,[],'http://127.0.0.1:3000',config)

    def test_proxy_config_checks_auth_and_hides_session_cookie(self):
        config=dict(address='192.168.1.42',bind='0.0.0.0',port=8020,url='https://192.168.1.42')
        block=gateway.caddy_blocks(config,[dict(slug='notes',port=18443,upstream='http://127.0.0.1:3000')])
        self.assertIn('forward_auth 127.0.0.1:8020',block)
        self.assertIn('uri /api/proxy-auth?app=notes',block)
        auth, proxy = block.split('\treverse_proxy ', 1)
        self.assertNotIn('header_up Origin', auth)
        self.assertIn('header_up Origin "^.+$" "http://127.0.0.1:3000"', proxy)
        self.assertIn('header_up Cookie',block)
        self.assertIn('alps_session=',block)
        # An empty replacement makes Caddy set the header to the pattern instead.
        self.assertIn('alps_session=[^;]*" "$1"', block)
        # Only the app itself and Alps pages may frame the app, whatever framing headers the app sends.
        self.assertIn('header +Content-Security-Policy "frame-ancestors \'self\' https://192.168.1.42"', auth)
        self.assertIn('header_down -X-Frame-Options', proxy)
        self.assertIn('header_down Content-Security-Policy "frame-ancestors[^;]*" "frame-ancestors *"', proxy)
        self.assertEqual(cli.slug_for('Car search'),'car-search')


class NetworkTests(unittest.TestCase):
    def test_gmail_uses_only_fixed_https_icon_source(self):
        from email.message import Message
        from io import BytesIO
        from unittest.mock import Mock
        from urllib.request import Request
        icon_url = 'https://ssl.gstatic.com/ui/v1/icons/mail/rfr/gmail.ico'
        content = b'\0\0\1\0test-icon'
        opener = Mock()

        def response(request, timeout):
            self.assertEqual(request.full_url, icon_url)
            self.assertIsNone(request.get_header('Cookie'))
            self.assertIsNone(request.get_header('Authorization'))
            result = BytesIO(content)
            result.headers = Message()
            result.headers['Content-Type'] = 'image/x-icon'
            result.geturl = lambda: icon_url
            return result

        opener.open.side_effect = response
        with patch.object(network.socket, 'getaddrinfo', return_value=[]), patch.object(network.urllib.request, 'build_opener', return_value=opener):
            for url in ['http://mail.google.com', 'https://mail.google.com/mail/u/0/#inbox']:
                with self.subTest(url=url):
                    self.assertEqual(network.favicon(url, lambda data, suffix: (data, suffix)), (content, '.ico'))
            self.assertEqual(opener.open.call_count, 2)
            opener.open.side_effect = ValueError('Unavailable')
            for url in ['https://mail.google.com.attacker.test', 'https://mail.google.com:8443']:
                opener.open.reset_mock()
                self.assertEqual(network.favicon(url, lambda *args: 'bad'), '')
                self.assertTrue(all(call.args[0].full_url != icon_url for call in opener.open.call_args_list))
        redirect = network.SameSiteRedirect(network.origin(icon_url))
        with self.assertRaises(ValueError):
            redirect.redirect_request(Request(icon_url), None, 302, '', {}, 'http://127.0.0.1/private')

    def test_only_local_web_listeners_are_suggested_highest_first(self):
        sockets = {('0.0.0.0',11500), ('0.0.0.0',8765), ('127.0.0.1',9000),
                   ('0.0.0.0',5432), ('0.0.0.0',12500), ('192.0.2.1',15000)}
        def probe(host, port):
            return {'port':port,'scheme':'http'} if port != 12500 else None
        with patch.object(network,'listeners',return_value=sockets), patch.object(network,'probe_web',side_effect=probe):
            self.assertEqual([item['port'] for item in network.discover_web('192.168.1.42')],[11500,8765])
            self.assertIn(9000,[item['port'] for item in network.discover_web('127.0.0.1')])

    def test_favicon_fetches_same_site_without_cookies(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.requests.append((self.path,self.headers.get('Cookie')))
                if self.path == '/':
                    content=b'<link rel="icon" href="/logo.svg">'; mime='text/html'
                elif self.path == '/logo.svg':
                    content=b'<svg xmlns="http://www.w3.org/2000/svg"/>'; mime='image/svg+xml'
                else:
                    self.send_error(404); return
                self.send_response(200); self.send_header('Content-Type',mime); self.end_headers(); self.wfile.write(content)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Site); server.requests=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            result=network.favicon(f'http://127.0.0.1:{server.server_port}/',lambda content,suffix: suffix)
            self.assertEqual(result,'.svg')
            self.assertEqual(server.requests,[('/',None),('/logo.svg',None)])
            with patch.object(network.socket,'getaddrinfo',return_value=[(None,None,None,None,('169.254.169.254',80))]):
                self.assertEqual(network.favicon('http://metadata/',lambda *args: 'bad'),'')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class SetupTests(unittest.TestCase):
    def test_caddy_preserves_other_sites_and_restores_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            main = Path(folder) / 'Caddyfile'
            snippet = Path(folder) / 'alps.caddy'
            original = 'other.home {\n    tls internal\n    respond "hello"\n}\n'
            main.write_text(original)
            config = dict(https=True, bind='0.0.0.0', address='192.168.1.42', port=8020, url='https://192.168.1.42')

            def fake_run(args, **kwargs):
                if args[1] == 'cp':
                    shutil.copy2(args[-2], args[-1])
                elif args[1] == 'install' and '-d' not in args:
                    shutil.copyfile(args[-2], args[-1])
                elif args[1] == 'rm':
                    Path(args[-1]).unlink(missing_ok=True)
                return subprocess.CompletedProcess(args, 0)

            with patch.object(setup, 'CADDYFILE', main), patch.object(setup, 'SNIPPET', snippet), patch.object(setup, 'CADDY_DROPIN', Path(folder) / 'alps.conf'), \
                    patch.object(setup, 'yes', return_value=True), patch.object(setup, 'run', side_effect=fake_run):
                setup.configure_caddy(config)
                self.assertIn(original.rstrip(), main.read_text())
                self.assertEqual(main.read_text().count(setup.IMPORT), 1)
                self.assertIn('tls internal', snippet.read_text())
                setup.configure_caddy(config)
                self.assertEqual(main.read_text().count(setup.IMPORT), 1)
                previous_main, previous_snippet = main.read_bytes(), snippet.read_bytes()

                def fail_validate(args, **kwargs):
                    if args[1:3] == ['caddy', 'validate']:
                        raise subprocess.CalledProcessError(1, args)
                    return fake_run(args, **kwargs)

                with patch.object(setup, 'run', side_effect=fail_validate), patch.object(setup.subprocess, 'run'):
                    with self.assertRaises(subprocess.CalledProcessError):
                        setup.configure_caddy(dict(config, address='192.168.1.43'))
                self.assertEqual(main.read_bytes(), previous_main)
                self.assertEqual(snippet.read_bytes(), previous_snippet)
                setup.configure_caddy(dict(config, https=False))
                self.assertNotIn(setup.IMPORT, main.read_text())
                self.assertIn(original.rstrip(), main.read_text())

    def test_missing_caddy_exits_before_changing_settings(self):
        with patch('builtins.input', side_effect=['local', 'https', 'quit']), patch.object(setup.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(SystemExit, 'No settings changed'):
                setup.collect({})


if __name__ == '__main__':
    unittest.main()
