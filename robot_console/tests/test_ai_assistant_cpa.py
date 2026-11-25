"""Loopback-only integration tests for the embedded Linux CPA and its console bridge."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastapi import FastAPI
from robot_console import plugins

REPO = Path(__file__).resolve().parents[1]
API = '/api/ext/ai_assistant'
NAME = 'codex-a@example.com.json'
SECRET = 'do-not-return-this-value'

# A separate executable, not a mock of urllib or Popen. Reject requests made
# with the wrong bearer; mimic CPA rewriting its secret to a hash on startup.
FAKE = r'''import json, os, re, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

config = Path(sys.argv[sys.argv.index('-config') + 1])
text = config.read_text()
port = int(re.search(r'^  port: (\d+)$', text, re.M)[1])
key = json.loads(re.search(r'^  secret-key: (.+)$', text, re.M)[1])
api = json.loads(re.search(r'^  api-keys: (.+)$', text, re.M)[1])[0]
assert key != 'fake-hash' and '  host: "127.0.0.1"' in text
assert '  allow-remote: false' in text and '  disable-control-panel: true' in text
config.write_text(text.replace(key, 'fake-hash'))
secret = 'do-not-return-this-value'
print(key, api, 'Bearer', secret, flush=True)
cred = dict(name='codex-a@example.com.json', type='codex', provider='codex', auth_index='13',
            access_token=secret, refresh_token=secret, id_token={'private':secret}, path='/private/file',
            PrivateKey=secret, access_token_expires_at='2030-01-01T00:00:00Z', siwc_bound=True)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_one(self):
        url = urlsplit(self.path)
        p = url.path
        q = parse_qs(url.query)
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        multipart = self.headers.get('Content-Type', '').startswith('multipart/form-data; boundary=')
        b = json.loads(raw) if raw and not multipart else {}
        status = 200
        if p == '/healthz':
            data = {'status':'ok'}
        elif self.headers.get('Authorization') != 'Bearer ' + (api if p == '/v1/models' else key):
            status, data = 401, {'error':'bad auth'}
        elif p == '/v1/models':
            data = {'data':[{'id':'one','owned_by':'cpa','secret':secret},{'id':'two'}]}
        elif not p.startswith('/v8/management/'):
            status, data = 404, {'error':'wrong API version'}
        elif p.endswith('/auth-url'):
            provider = q['provider'][0]
            data = {'url':'https://example.org/login?access_type=offline', 'state':'state-123', 'secret':secret}
            if provider in ('copilot','kimi','kimi-ai','meta','xai'):
                data.update(flow='device', user_code='AB-CD', expires_in=300)
        elif p.endswith('/oauth/status'):
            data = {'status':'wait'}
        elif p.endswith('/oauth/session'):
            data = {'cancelled':True}
        elif p.endswith('/siwc/bind'):
            data = {'url':'https://example.org/bind','state':'siwc-123','expires_in':300}
        elif p.endswith('/oauth/import'):
            assert multipart and b'Content-Disposition: form-data; name="file"; filename="upload.json"' in raw
            assert b'service_account' in raw and q == {'provider':['vertex']}
            data = {'auth-file':'/private/auths/vertex-project.json', 'private_key':secret}
        elif p.endswith('/verify'):
            data = {'status':'ok','verified':True,'credential':cred,'access_token':secret}
        elif p.endswith('/refresh') and b.get('name') == 'redirect.json':
            status, data = 302, {}
        elif p.endswith('/refresh') and b.get('name') == 'bad.json':
            status, data = 403, {'error':secret}
        elif p.endswith('/credentials') and self.command == 'GET':
            data = {'files':[cred, {'account_type':'api_key', 'account':secret, 'label':'Alias: '+secret}]}
        elif p.endswith(('/callback','/status','/refresh','/credentials','/fields')):
            data = {'ok':True, 'auth':cred}
        else:
            status, data = 404, {'error':'unsupported path'}
        with open('requests.jsonl','a') as f:
            f.write(json.dumps([self.command, p, q, b if not multipart else 'multipart'])+'\n')
        raw = json.dumps(data).encode()
        self.send_response(status)
        if status == 302:
            self.send_header('Location', '/never-follow')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    do_GET = do_POST = do_DELETE = do_PATCH = handle_one

HTTPServer(('127.0.0.1',port), Handler).serve_forever()
'''


class CPATests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        cfg = {'paths': {'data_root': str(self.base)}, 'plugins': {
            'dirs': [str(REPO / 'plugins')], 'state_file': str(self.base / 'state.json'),
            'data_dir': str(self.base / 'data')}}
        env = mock.patch.dict(os.environ, {plugins.NO_PLUGINS_ENV: '', 'ROBOT_CONSOLE_NO_CPA': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.manager = plugins.PluginManager(cfg)
        self.manager.set_enabled('ai_assistant', True)
        self.app = FastAPI()
        plugins.register_plugin_routes(self.app, self.manager)
        self.addCleanup(self.manager.shutdown)
        self.dir = self.base / 'data' / 'ai_assistant' / 'cpa'

    def call(self, method, path, data=None, status=200):
        code, result = asyncio.run(plugins.asgi_request(self.app, method, API + path, data))
        self.assertEqual(code, status, result)
        self.assertNotIn(SECRET, json.dumps(result))
        return result

    def wait(self, state):
        until = time.monotonic() + 8
        while True:
            st = self.call('GET', '/cpa/status')
            if st['state'] == state:
                return st
            if st['state'] == 'error' or time.monotonic() >= until:
                self.fail(f'Expected {state}, got {st}')
            time.sleep(0.04)

    def bundle(self, extra=None):
        payload = ('#!' + sys.executable + '\n' + FAKE).encode()
        archive = self.base / 'archive.tar.gz'
        with tarfile.open(archive, 'w:gz') as tf:
            member = tarfile.TarInfo('cli-proxy-api')
            member.size = len(payload)
            tf.addfile(member, io.BytesIO(payload))
            if extra:
                tf.addfile(extra)
        os.environ['ROBOT_CONSOLE_CPA_ARCHIVE_SHA256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        os.environ['ROBOT_CONSOLE_CPA_ARCHIVE_URL'] = 'http://127.0.0.1:9/unused'
        return archive

    def ready(self):
        archive = self.bundle()
        self.call('POST', '/cpa/install', {'archive': str(archive)})
        self.wait('stopped')
        self.call('POST', '/cpa/start', status=409)  # NO_CPA disables explicit start too.
        os.environ['ROBOT_CONSOLE_NO_CPA'] = ''
        self.assertEqual(self.call('POST', '/cpa/start')['state'], 'starting')
        return self.wait('ready')

    def recorded(self):
        return [json.loads(line) for line in (self.dir / 'requests.jsonl').read_text().splitlines()]

    def test_missing_and_validation(self):
        st = self.call('GET', '/cpa/status')
        self.assertEqual(st['state'], 'missing')
        self.assertTrue(st['port_hidden'])
        self.assertNotIn('port', st)
        r = self.call('GET', '/cpa/models', status=409)
        self.assertEqual(r['detail']['code'], 'plugin.ai_assistant.cpa_not_ready')
        for proxy in ['file:///bad', 'http://host:99999', 'socks5h://', 'http://a/b', 123]:
            self.call('PUT', '/cpa/settings', {'proxy_url': proxy}, 400)
        for proxy in ['http://host:8080', 'https://host', 'socks5://host:9000', 'socks5h://host:1080', '']:
            self.assertEqual(self.call('PUT', '/cpa/settings', {'proxy_url': proxy})['proxy_url'], proxy)
        self.call('PUT', '/cpa/settings', {'autostart': 'yes'}, 400)
        self.call('POST', '/cpa/install', {'version': 'latest'}, 400)
        self.call('DELETE', '/cpa/credentials?name=../bad', status=400)
        self.call('POST', '/cpa/oauth/start', {'provider': 'other'}, 400)
        for url in ('file://bad?state=ok', 'http://host/no-state', 'http://host/?state=', 'http://?state=x'):
            self.call('POST', '/cpa/oauth/callback', {'provider': 'codex', 'redirect_url': url}, 400)
        for text in ('x', '{}', ' ' * 65536):
            self.call('POST', '/cpa/vertex', {'filename': 'a.json', 'content': text}, 400)
        self.call('POST', '/cpa/vertex', [SECRET], 422)

    def test_log_rotation_and_autostart_guard(self):
        cpa = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.cpa']
        (self.dir / 'bin').mkdir(parents=True)
        (self.dir / 'bin/cli-proxy-api').touch()
        (self.dir / 'logs').mkdir()
        with mock.patch.object(cpa, 'LOG_LIMIT', 256):
            e = cpa.Engine(self.dir.parent)
        self.assertEqual(e.status()['state'], 'stopped')  # NO_CPA even with an installed executable.
        e._drain(io.BytesIO((f'[warn] access_token={SECRET}\n' * 100).encode()))
        logs = sorted(e.log.parent.iterdir())
        self.assertEqual([p.name for p in logs], ['engine.log', 'engine.log.1'])
        for path in logs:
            self.assertLessEqual(path.stat().st_size, 256)
            self.assertNotIn(SECRET, path.read_text())
        self.assertEqual(len(e.logs(2)), 2)
        self.assertEqual(e.logs(0), [])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux release')
    def test_checksum_and_unsafe_members(self):
        archive = self.bundle()
        os.environ['ROBOT_CONSOLE_CPA_ARCHIVE_SHA256'] = '0' * 64
        self.call('POST', '/cpa/install', {'archive': str(archive)})
        self.assertEqual(self.wait('error')['error']['code'], 'cpa_checksum')
        link = tarfile.TarInfo('extra')
        link.type, link.linkname = tarfile.SYMTYPE, 'cli-proxy-api'
        for member in (tarfile.TarInfo('../escape'), tarfile.TarInfo('/absolute'), link):
            archive = self.bundle(member)
            self.call('POST', '/cpa/install', {'archive': str(archive)})
            self.assertEqual(self.wait('error')['error']['code'], 'cpa_archive')
            self.assertFalse((self.dir / 'bin' / 'cli-proxy-api').exists())
            self.assertFalse((self.dir / 'escape').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux release')
    def test_http_install_and_process(self):
        blob = self.bundle().read_bytes()
        entered, release = threading.Event(), threading.Event()

        class Download(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                entered.set()
                release.wait(4)
                self.wfile.write(blob)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Download) as server:
            os.environ['ROBOT_CONSOLE_CPA_ARCHIVE_URL'] = f'http://127.0.0.1:{server.server_port}/archive'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                st = self.call('POST', '/cpa/install')
                self.assertEqual((st['state'], st['progress']['done']), ('installing', 0))
                self.assertTrue(entered.wait(3))
                self.call('POST', '/cpa/install', {}, 409)
            finally:
                release.set()
                server.shutdown()
                thread.join()
        self.wait('stopped')
        self.assertEqual((self.dir / 'bin/cli-proxy-api').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.dir / 'auths').stat().st_mode & 0o777, 0o700)
        meta = json.loads((self.dir / 'engine.json').read_text())
        self.assertGreaterEqual(meta['port'], 8327)
        os.environ['ROBOT_CONSOLE_NO_CPA'] = ''
        self.call('POST', '/cpa/start')
        pid = self.wait('ready')['pid']
        self.assertEqual((self.dir / 'config.yaml').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.dir / 'engine.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.call('GET', '/cpa/models')['models'][0], {'id': 'one', 'owned_by': 'cpa'})
        for key in (meta['api_key'], meta['management_key']):
            self.assertNotIn(key, json.dumps(self.call('GET', '/cpa/status')))
            self.assertNotIn(key, json.dumps(self.call('GET', '/cpa/logs')))
            self.assertNotIn(key, (self.dir / 'logs/engine.log').read_text())
        st = self.call('PUT', '/cpa/settings', {'proxy_url': 'http://user:password@localhost:3000', 'autostart': False})
        self.assertEqual((st['state'], st['proxy_url']), ('starting', 'http://[redacted]@localhost:3000'))
        newpid = self.wait('ready')['pid']
        self.assertNotEqual(newpid, pid)
        # Regression (R2-3): saving the masked proxy unchanged keeps the stored credentials and does not restart.
        st = self.call('PUT', '/cpa/settings', {'proxy_url': 'http://[redacted]@localhost:3000'})
        self.assertEqual(st.get('pid'), newpid)
        self.assertEqual(json.loads((self.dir / 'engine.json').read_text())['proxy_url'],
                         'http://user:password@localhost:3000')
        os.kill(newpid, signal.SIGTERM)
        st = self.wait('error')
        self.assertEqual(st['error']['code'], 'cpa_exit')
        self.assertTrue(st['log_tail'])
        self.call('POST', '/cpa/restart')
        pid = self.wait('ready')['pid']
        self.assertEqual(self.call('POST', '/cpa/stop')['state'], 'stopped')
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux release')
    def test_oauth_and_credentials(self):
        self.ready()
        for provider in ('codex', 'anthropic', 'antigravity', 'devin', 'xai', 'kimi', 'kimi-ai', 'meta', 'copilot'):
            body = {'provider': provider}
            if provider == 'codex':
                body.update(proxy_url='socks5h://localhost:9000', name=NAME)
            result = self.call('POST', '/cpa/oauth/start', body)
            method, path, query, forwarded = self.recorded()[-1]
            self.assertEqual(query['provider'], ['claude' if provider == 'anthropic' else provider])
            self.assertEqual(path, '/v8/management/oauth/auth-url')
            if provider in ('codex', 'anthropic', 'antigravity', 'devin', 'xai'):
                self.assertEqual(query['is_webui'], ['true'])
            else:
                self.assertNotIn('is_webui', query)
            if provider == 'codex':
                self.assertEqual((method, forwarded), ('POST', {'name': NAME, 'proxy_url': body['proxy_url']}))
                self.assertNotIn('proxy_url', query)
            else:
                self.assertEqual(method, 'GET')
            if provider in ('kimi', 'kimi-ai', 'meta', 'copilot', 'xai'):
                self.assertEqual((result['flow'], result['user_code'], result['expires_in']), ('device', 'AB-CD', 300))
            else:
                self.assertEqual(result['flow'], 'browser')
        self.assertEqual(self.call('GET', '/cpa/oauth/status?state=s1'), {'status': 'wait'})
        self.assertEqual(self.call('DELETE', '/cpa/oauth/session?state=s1'), {'cancelled': True})
        self.assertEqual(self.call('POST', '/cpa/oauth/siwc', {'name': NAME})['expires_in'], 300)
        redirect = {'provider': 'codex', 'redirect_url': 'http://localhost/?code=x&state=s1'}
        self.assertEqual(self.call('POST', '/cpa/oauth/callback', redirect), {'ok': True})
        self.assertEqual(self.recorded()[-1][-1], redirect)
        entry, key_entry = self.call('GET', '/cpa/credentials')['items']
        self.assertNotIn('account', key_entry)
        self.assertEqual(key_entry['label'], '[redacted]')
        self.assertEqual(set(entry), {'name', 'type', 'provider', 'auth_index', 'siwc_bound', 'access_token_expires_at'})
        toggle = {'name': NAME, 'disabled': True}
        self.assertEqual(self.call('PATCH', '/cpa/credentials/status', toggle), {'ok': True})
        self.assertEqual(self.recorded()[-1], ['PATCH', '/v8/management/credentials/status', {}, toggle])
        self.assertEqual(self.call('POST', '/cpa/credentials/refresh', {'name': NAME}), {'ok': True})
        self.assertEqual(self.call('POST', '/cpa/credentials/verify', {'name': NAME, 'model': 'one'})['credential'], entry)
        self.assertEqual(self.call('DELETE', '/cpa/credentials?name=a.json'), {'ok': True})
        self.assertEqual(self.recorded()[-1][2], {'name': ['a.json']})
        error = self.call('POST', '/cpa/credentials/refresh', {'name': 'bad.json'}, 502)['detail']
        self.assertEqual((error['code'], error['params']['status']), ('plugin.ai_assistant.cpa_error', 403))
        redirect_error = self.call('POST', '/cpa/credentials/refresh', {'name': 'redirect.json'}, 502)
        self.assertEqual(redirect_error['detail']['params']['status'], 302)
        self.assertFalse(any(row[1] == '/never-follow' for row in self.recorded()))
        vertex = {'filename': 'upload.json', 'content': json.dumps({'type': 'service_account', 'private_key': SECRET}),
                  'prefix': 'team'}
        self.assertEqual(self.call('POST', '/cpa/vertex', vertex), {'ok': True, 'name': 'vertex-project.json'})
        self.assertEqual(self.recorded()[-1][-1], {'name': 'vertex-project.json', 'prefix': 'team'})

    @unittest.skipUnless(sys.platform == 'linux', 'Linux release')
    def test_managed_profile_and_setup_autostart(self):
        self.ready()
        other = self.call('POST', '/providers', {'profile': {'name': 'other'}})
        p = self.call('POST', '/cpa/use')['profile']
        self.assertTrue(p['key_set'])
        self.assertNotIn('api_key', p)
        pid = p['id']
        self.assertEqual(p['name'], 'CPA (OAuth)')
        p['models'][0].update(label='Mine', effort='high', efforts=['low', 'high'])
        self.call('PUT', '/providers/' + pid, {'profile': p})
        self.call('POST', '/providers/' + other['id'] + '/activate', {})
        self.manager.shutdown()
        meta = json.loads((self.dir / 'engine.json').read_text())
        self.assertEqual(meta['cpa_profile_id'], pid)
        # Regression (R2-1): the engine's record of its profile is lost; providers.json still marks it engine-owned
        del meta['cpa_profile_id']
        # Simulate a persisted port migration and a fresh plugin load.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            meta['port'] = sock.getsockname()[1]
        (self.dir / 'engine.json').write_text(json.dumps(meta))
        self.app = FastAPI()
        new_manager = plugins.PluginManager(self.manager.cfg)
        plugins.register_plugin_routes(self.app, new_manager)
        self.addCleanup(new_manager.shutdown)
        self.wait('ready')
        self.assertTrue(next(x for x in self.call('GET', '/providers')['profiles'] if x['id'] == pid)['managed'])
        again = self.call('POST', '/cpa/use')['profile']
        self.assertEqual((again['id'], again['models']), (pid, p['models']))
        self.assertEqual(json.loads((self.dir / 'engine.json').read_text())['cpa_profile_id'], pid)
        # Regression (R2-1): latency probes of the engine-owned profile cannot aim its key at other addresses
        code, res = asyncio.run(plugins.asgi_request(self.app, 'POST', API + '/probe/latency',
                                                     {'id': pid, 'profile': again, 'urls': ['http://127.0.0.1:9/v1']}))
        self.assertEqual(code, 400)
        self.assertEqual(again['base_url'], f'http://127.0.0.1:{meta["port"]}/v1')
        self.assertTrue(again['managed'])
        listing = self.call('GET', '/providers')
        self.assertEqual((listing['active'], len(listing['profiles'])), (pid, 2))

        # Regression (Blocker 2): sync without activation keeps the user's chosen active service.
        self.call('POST', '/providers/' + other['id'] + '/activate', {})
        self.call('POST', '/cpa/use', {'activate': False})
        listing = self.call('GET', '/providers')
        self.assertEqual(listing['active'], other['id'])

        # Regression (Blocker 4): native CPA config disables request/error body dumps on disk.
        cfg_text = (self.dir / 'config.yaml').read_text(encoding='utf-8')
        self.assertIn('commercial-mode: true', cfg_text)
        self.assertIn('request-log: false', cfg_text)
        self.assertIn('error-logs-max-files: 0', cfg_text)

        # Regression (Blocker 1): if CPA stops and another server claims its old port, the assistant
        # must refuse to send chat/test/probe traffic to that port (and refuse endpoint edits on it).
        self.call('POST', '/cpa/use', {'activate': True})
        self.call('PUT', '/providers/' + pid, {'profile': {**again, 'base_url': 'http://127.0.0.1:9/v1',
                                                           'api_key': 'spoofed'}})
        self.call('POST', '/cpa/stop')
        impostor_hits = []

        class Impostor(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                impostor_hits.append(('POST', self.path))
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                impostor_hits.append(('GET', self.path))
                self.send_response(200)
                self.end_headers()

        impostor = ThreadingHTTPServer(('127.0.0.1', meta['port']), Impostor)
        threading.Thread(target=impostor.serve_forever, daemon=True).start()
        try:
            st = self.call('GET', '/status')
            self.assertEqual(st['problem']['code'], 'cpa_not_ready')
            for method, path, body in [('POST', '/chat', {'message': 'secret prompt'}),
                                       ('POST', '/test', {}),
                                       ('POST', '/probe/models', {'id': pid, 'profile': again})]:
                code, res = asyncio.run(plugins.asgi_request(self.app, method, API + path, body))
                self.assertEqual((code, res['detail']['code']), (409, 'plugin.ai_assistant.cpa_not_ready'))
            self.assertEqual(impostor_hits, [])
        finally:
            impostor.shutdown()
            impostor.server_close()


class LoginUrlTests(unittest.TestCase):
    """Real authorization URLs must pass, URLs carrying a credential must not (regression: Codex was refused
    because id_token_add_organizations contains 'token')."""

    def test_provider_urls(self):
        sys.path.insert(0, str(REPO / 'plugins'))
        try:
            from ai_assistant.backend import cpa_api
        finally:
            sys.path.pop(0)
        codex = ('https://auth.openai.com/oauth/authorize?client_id=app_x&code_challenge=c&code_challenge_method=S256'
                 '&codex_cli_simplified_flow=true&id_token_add_organizations=true&prompt=login'
                 '&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback&response_type=code&scope=openid&state=s1')
        siwc = ('https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client&id_token_hint=eyJ'
                '&login_hint=a%40b.c&resource=https%3A%2F%2Fapi.openai.com%2Fv1&state=s2')
        for url in (codex, siwc):
            self.assertEqual(cpa_api._login({'url': url, 'state': 's1'})['url'], url)
        for bad in ('https://x.example/cb?access_token=abc', 'https://x.example/cb#refresh_token=abc',
                    'https://x.example/cb?api_key=abc', 'https://x.example/cb?client_secret=abc',
                    'https://x.example/cb?token=abc'):
            with self.assertRaises(cpa_api.Upstream):
                cpa_api._login({'url': bad, 'state': 's1'})
