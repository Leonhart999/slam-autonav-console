"""Install and supervise the pinned, loopback-only CLIProxyAPI child."""
import base64
import contextlib
import hashlib
import http.client
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import secrets
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib import error, request
from urllib.parse import unquote, urlsplit, urlunsplit

VERSION = '8.1.2'
RELEASE = 'https://github.com/router-for-me/CLIProxyAPI/releases/download/v' + VERSION
PINS = {
    'x86_64': ('amd64', '851e41c9a087b60e6586fec0c6be821b61921e8da0e84b9490ef542042db0756'),
    'aarch64': ('aarch64', '41245fa2af02d68c69376afc6360af49b58c3b2368d8cec1d61f0e43de14d580'),
}
# auth_files_oauth_callback.go:19-20 and internal/auth/antigravity/constants.go:8.
CALLBACK_PORTS = {'codex': 1455, 'claude': 54545, 'antigravity': 51121}
LIMIT = 200 * 1024 * 1024
LOG_LIMIT = 2 * 1024 * 1024


class Problem(Exception):
    def __init__(self, code, detail, status=400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def proxy_url(value):
    if value == '':
        return value
    try:
        if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\x00-\x1f\x7f\\]', value):
            raise ValueError
        u = urlsplit(value)
        if (u.scheme not in ('http', 'https', 'socks5', 'socks5h') or not u.hostname or u.netloc.endswith(':')
                or u.port == 0 or (u.scheme.startswith('socks') and not u.port)
                or u.path not in ('', '/') or u.query or u.fragment):
            raise ValueError
        return urlunsplit((u.scheme, u.netloc, u.path, '', ''))
    except ValueError:
        raise Problem('cpa_invalid', 'Invalid proxy_url') from None


MASK = '[redacted]'


def masked_proxy(value):
    """The proxy as the browser may see it: user:password replaced by [redacted]."""
    if not value:
        return ''
    u = urlsplit(value)
    if '@' not in u.netloc:
        return value
    return urlunsplit(u._replace(netloc=MASK + '@' + u.netloc.rsplit('@', 1)[-1]))


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # Never forward a loopback bearer header to a redirect destination.


def local_opener():
    return request.build_opener(request.ProxyHandler({}), NoRedirect())


def _socks_connect(proxy, target, timeout, source_address=None):
    """SOCKS5 CONNECT for urllib downloads (the Go child handles its own proxy)."""
    sock = socket.create_connection((proxy.hostname, proxy.port), timeout, source_address)

    def read(n):
        result = bytearray()
        while len(result) < n:
            part = sock.recv(n - len(result))
            if not part:
                raise OSError('Proxy disconnected')
            result.extend(part)
        return bytes(result)

    try:
        method = b'\x02' if proxy.username is not None else b'\x00'
        sock.sendall(b'\x05\x01' + method)
        if read(2) != b'\x05' + method:
            raise OSError('Proxy negotiation failed')
        if method == b'\x02':
            user = unquote(proxy.username).encode()
            password = unquote(proxy.password or '').encode()
            sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
            if read(2) != b'\x01\x00':
                raise OSError('Proxy authentication failed')
        host, port = target
        if proxy.scheme == 'socks5h':
            name = host.encode('idna')
            addr = b'\x03' + bytes([len(name)]) + name
        else:
            family, _, _, _, resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
            addr = (b'\x01' if family == socket.AF_INET else b'\x04') + socket.inet_pton(family, resolved[0])
        sock.sendall(b'\x05\x01\x00' + addr + struct.pack('!H', port))
        head = read(4)
        if head[:3] != b'\x05\x00\x00':
            raise OSError('Proxy connection failed')
        size = read(1)[0] if head[3] == 3 else {1: 4, 4: 16}.get(head[3])
        if size is None:
            raise OSError('Invalid proxy response')
        read(size + 2)
        return sock
    except BaseException:
        sock.close()
        raise


class _TLSStream(io.RawIOBase):
    """Origin TLS records transported inside an already verified TLS-to-proxy socket."""
    def __init__(self, transport, context, host):
        self.transport = transport
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = context.wrap_bio(self.incoming, self.outgoing, server_hostname=host)
        self.run(self.tls.do_handshake)

    def run(self, fn, *args):
        while True:
            try:
                return fn(*args)
            except ssl.SSLWantReadError:
                if self.outgoing.pending:
                    self.transport.sendall(self.outgoing.read())
                data = self.transport.recv(65536)
                if data:
                    self.incoming.write(data)
                else:
                    self.incoming.write_eof()
            finally:
                if self.outgoing.pending:
                    self.transport.sendall(self.outgoing.read())

    def readable(self):
        return True

    def readinto(self, buf):
        try:
            return self.run(self.tls.read, len(buf), buf)
        except ssl.SSLZeroReturnError:
            return 0

    def close(self):
        self.transport.close()
        super().close()


class _TunnelSocket:
    # urllib always sends Connection: close. Once HTTPResponse calls makefile,
    # the response's file owns the stream until it finishes reading the body.
    def __init__(self, stream):
        self.stream, self.transferred = stream, False

    def sendall(self, data):
        data = memoryview(data)
        while data:
            data = data[self.stream.run(self.stream.tls.write, data):]

    def makefile(self, mode):
        self.transferred = True
        return io.BufferedReader(self.stream)

    def close(self):
        if not self.transferred:
            self.stream.close()


def _proxy_auth(proxy):
    if proxy.username is None:
        return {}
    creds = (unquote(proxy.username) + ':' + unquote(proxy.password or '')).encode()
    return {'Proxy-Authorization': 'Basic ' + base64.b64encode(creds).decode('ascii')}


def _https_tunnel(proxy, host, port, timeout):
    # urllib's built-in ProxyHandler does not establish TLS to an HTTPS
    # proxy before sending CONNECT (including its Proxy-Authorization header).
    context = ssl.create_default_context()
    raw = socket.create_connection((proxy.hostname, proxy.port or 443), timeout)
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(raw.close)
        secure = context.wrap_socket(raw, server_hostname=proxy.hostname)
        cleanup.callback(secure.close)
        outer = http.client.HTTPConnection(proxy.hostname, timeout=timeout)
        outer.set_tunnel(host, port, headers=_proxy_auth(proxy))
        outer.sock = secure
        outer._tunnel()
        stream = _TLSStream(secure, context, host)
        cleanup.pop_all()
        return _TunnelSocket(stream)


def _download_opener(proxy):
    class Tunnel(request.HTTPSHandler):
        def https_open(self, req):
            def connect(*args, **kwargs):
                conn = http.client.HTTPSConnection(*args, **kwargs)
                if not proxy:
                    return conn
                address = urlsplit(proxy)
                if address.scheme == 'http':
                    # Tunnel headers belong to this hop, not a redirected GET.
                    forward = http.client.HTTPSConnection(address.hostname, address.port or 80, **kwargs)
                    forward.set_tunnel(conn.host, conn.port, headers=_proxy_auth(address))
                    return forward
                if address.scheme == 'https':
                    def connect_tls():
                        conn.sock = _https_tunnel(address, conn.host, conn.port, conn.timeout)
                    conn.connect = connect_tls
                else:
                    conn._create_connection = lambda *a: _socks_connect(address, *a)
                return conn
            return self.do_open(connect, req)

    class NoPlain(request.HTTPHandler):
        def http_open(self, req):
            raise OSError('HTTPS required')

    return request.build_opener(request.ProxyHandler({}), Tunnel(), NoPlain())


def _release():
    arch = platform.machine().lower()
    if platform.system() != 'Linux' or arch not in PINS:
        raise Problem('cpa_platform', 'Only Linux x86_64 and aarch64 are supported')
    suffix, sha = PINS[arch]
    # Test fixture only: both env vars and a loopback HTTP URL are required;
    # neither digest nor URL is accepted via the public API.
    if 'unittest' in sys.modules and 'ROBOT_CONSOLE_CPA_ARCHIVE_URL' in os.environ:
        url = os.environ['ROBOT_CONSOLE_CPA_ARCHIVE_URL']
        expected = os.environ.get('ROBOT_CONSOLE_CPA_ARCHIVE_SHA256', '')
        if (urlsplit(url).hostname != '127.0.0.1' or urlsplit(url).scheme != 'http'
                or not re.fullmatch('[0-9a-f]{64}', expected)):
            raise Problem('cpa_test_fixture', 'Invalid loopback archive fixture')
        return url, expected
    return f'{RELEASE}/CLIProxyAPI_{VERSION}_linux_{suffix}.tar.gz', sha


@contextlib.contextmanager
def _temporary(directory):
    fd, name = tempfile.mkstemp(dir=directory)
    os.close(fd)
    try:
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def _write(path, text):
    with _temporary(path.parent) as tmp:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)


def _extract(archive, out):
    found = False
    expanded = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            path = PureWindowsPath(member.name)
            expanded += member.size
            if (path.anchor or '..' in path.parts or '\\' in member.name or member.size < 0
                    or member.size > LIMIT or expanded > 2 * LIMIT or not (member.isfile() or member.isdir())):
                raise Problem('cpa_archive', 'Unsafe archive member')
            if member.name == 'cli-proxy-api':
                if found or not member.isfile() or not member.size:
                    raise Problem('cpa_archive', 'Invalid executable member')
                with tar.extractfile(member) as src, out.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                found = True
    if not found:
        raise Problem('cpa_archive', 'Missing cli-proxy-api archive member')


def _port():
    for port in range(8327, 65536):
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise Problem('cpa_port', 'No free loopback port')


def _background(fn, *args):
    thread = threading.Thread(target=fn, args=args, daemon=True, name='embedded-cpa-' + fn.__name__)
    thread.start()
    return thread


# Project untrusted output onto fixed diagnostics before it reaches disk. Raw
# lines can include upstream bodies, callback parameters or multiline PEM keys.
def _log_message(line):
    for needle, message in ((b'address already in use', 'Listen port is in use'),
                            (b'permission denied', 'Permission denied'),
                            (b'connection refused', 'Connection refused'),
                            (b'cliproxyapi version:', 'CLIProxyAPI version ' + VERSION),
                            (b'api server started', 'API server started'),
                            (b'failed to load config', 'Failed to load configuration'),
                            (b'waiting for authentication callback', 'Waiting for OAuth callback'),
                            (b'authentication successful!', 'OAuth credential saved'),
                            (b'authentication failed', 'OAuth authentication failed')):
        if needle in line.lower():
            return message
    http_event = re.search(rb'\b([1-5]\d\d) \|[^|]*\|[^|]*\| (GET|POST|PATCH|DELETE|HEAD) +"/', line)
    if http_event:
        return 'HTTP ' + http_event[2].decode() + ' -> ' + http_event[1].decode()
    level = re.search(rb'\[(info|warn|error|fatal|debug) *\]', line, re.I)
    return 'CPA ' + (level[1].decode().lower() if level else 'output') + ' (details withheld)'


class Engine:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / 'cpa'
        self.binary = self.root / 'bin' / 'cli-proxy-api'
        self.config = self.root / 'config.yaml'
        self.auths = self.root / 'auths'
        self.log = self.root / 'logs' / 'engine.log'
        self.meta = self.root / 'engine.json'
        self.lock = threading.RLock()
        self.log_handler = RotatingFileHandler(self.log, maxBytes=LOG_LIMIT, backupCount=1, delay=True, encoding='utf-8')
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%d %H:%M:%S'))
        self.proc = self.progress = self.error = self.install_id = self.start_id = None
        self.invalid_metadata = False
        self.last_retry = float('-inf')
        self.state = 'stopped' if self.binary.is_file() else 'missing'
        self.data = {'version': VERSION, 'autostart': True, 'proxy_url': ''}
        try:
            if self.meta.exists():
                saved = json.loads(self.meta.read_text(encoding='utf-8'))
                if not isinstance(saved, dict) or saved.get('version') != VERSION:
                    raise ValueError
                if ('port' in saved and (type(saved['port']) is not int or not 1 <= saved['port'] <= 65535)
                        or type(saved.get('autostart', True)) is not bool
                        or any(not isinstance(saved[k], str) for k in ('api_key', 'management_key') if k in saved)):
                    raise ValueError
                saved['proxy_url'] = proxy_url(saved.get('proxy_url', ''))
                self.data.update(saved)
            if self.state == 'stopped' and self.data['autostart'] and not self.disabled:
                self.start()  # Schedules a worker; no process/network wait on setup.
        except (OSError, ValueError, Problem):
            self.invalid_metadata = True
            self._fail('cpa_metadata', 'Cannot read engine metadata')

    @property
    def disabled(self):
        return os.environ.get('ROBOT_CONSOLE_NO_CPA') == '1'

    def _save(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(self.meta, json.dumps(self.data, indent=2) + '\n')

    def _prepare(self):
        for d in (self.binary.parent, self.auths, self.log.parent):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.auths.chmod(0o700)
        if 'port' not in self.data:
            self.data['port'] = _port()
        for key in ('api_key', 'management_key'):
            if not self.data.get(key):
                self.data[key] = secrets.token_urlsafe(32)
        self._save()

    def _config(self, starting=False):
        # Outside the brief startup handoff the config needs no plaintext
        # management secret. A stopped config disables management altogether.
        management_key = self.data['management_key'] if starting else ''
        q = json.dumps  # JSON-quoted strings are valid YAML scalars.
        d = self.data
        _write(self.config, 'config-version: 8\nserver:\n  host: "127.0.0.1"\n'
               f'  port: {d["port"]}\n  commercial-mode: true\nmanagement:\n  allow-remote: false\n'
               f'  secret-key: {q(management_key)}\n  disable-control-panel: true\n'
               f'access:\n  api-keys: [{q(d["api_key"])}]\noauth:\n  auth-dir: {q(str(self.auths))}\n'
               f'requests:\n  proxy-url: {q(d["proxy_url"])}\n'
               'observability:\n  logs:\n    logging-to-file: false\n'
               '    request-log: false\n    error-logs-max-files: 0\n')

    def _fail(self, code, detail):
        self.state, self.error = 'error', {'code': code, 'detail': detail}

    def status(self):
        with self.lock:
            out = {'state': self.state, 'version': VERSION, 'port_hidden': True,
                   'proxy_url': masked_proxy(self.data['proxy_url']),
                   'autostart': self.data['autostart'],
                   'callback_ports': [{'provider': k, 'port': v} for k, v in CALLBACK_PORTS.items()]}
            if self.progress is not None:
                out['progress'] = self.progress.copy()
            if self.proc and self.proc.poll() is None:
                out.update(pid=self.proc.pid, uptime_s=int(time.monotonic() - self.started))
            if self.error:
                out.update(error=self.error.copy(), log_tail=self.logs(40))
            return out

    def install(self, archive=None, proxy=None):
        if archive is not None and (not isinstance(archive, str) or not Path(archive).is_absolute()):
            raise Problem('cpa_invalid', 'Expected an absolute archive path')
        if proxy is not None:
            proxy = proxy_url(proxy)
        with self.lock:
            if self.state in ('installing', 'ready', 'starting'):
                raise Problem('cpa_busy', 'Stop the current operation first', 409)
            url, digest = _release()
            if proxy is not None:
                self.data['proxy_url'] = proxy
                self._save()
            self.install_id = ident = object()
            self.progress = {'done': 0, 'total': 0}
            self.state, self.error = 'installing', None
            _background(self._install, ident, archive, url, digest, self.data['proxy_url'])
            return self.status()

    def _install(self, ident, archive, url, digest, proxy):
        try:
            self.binary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _temporary(self.binary.parent) as bundle, _temporary(self.binary.parent) as exe:
                if archive:
                    source = Path(archive)
                    if not source.is_file():
                        raise Problem('cpa_archive', 'Not a regular archive file')
                    total = source.stat().st_size
                    stream = source.open('rb')
                else:
                    # Only the loopback unit-test fixture uses cleartext HTTP.
                    opener = local_opener() if url.startswith('http:') else _download_opener(proxy)
                    stream = opener.open(url, timeout=30)
                    total = int(stream.headers.get('Content-Length') or 0)
                hasher = hashlib.sha256()
                done = 0
                with stream, bundle.open('wb') as dst:
                    for block in iter(lambda: stream.read(128 * 1024), b''):
                        done += len(block)
                        if max(total, done) > LIMIT:
                            raise Problem('cpa_archive', 'Archive is too large')
                        with self.lock:
                            if self.install_id is not ident:
                                return
                            self.progress = {'done': done, 'total': total}
                        hasher.update(block)
                        dst.write(block)
                if hasher.hexdigest() != digest:
                    raise Problem('cpa_checksum', 'Archive SHA-256 mismatch')
                _extract(bundle, exe)
                exe.chmod(0o755)
                with self.lock:
                    if self.install_id is ident:
                        self._prepare()
                        self._config()
                        os.replace(exe, self.binary)
                        self.invalid_metadata = False
                        self.state, self.progress, self.install_id = 'stopped', None, None
        except Exception as exc:
            with self.lock:
                if self.install_id is ident:
                    self.install_id = self.progress = None
                    if isinstance(exc, Problem):
                        self._fail(exc.code, exc.detail)
                    else:
                        self._fail('cpa_install', 'Cannot download, verify or install archive')

    def start(self):
        with self.lock:
            if self.disabled:
                raise Problem('cpa_disabled', 'Engine is disabled by the environment', 409)
            if self.invalid_metadata:
                raise Problem('cpa_metadata', 'Reinstall to repair engine metadata', 409)
            if self.state == 'installing' or not self.binary.is_file():
                raise Problem('cpa_not_ready', 'Install the engine first', 409)
            if self.state not in ('ready', 'starting'):
                self.start_id = ident = object()
                self.state, self.error = 'starting', None
                _background(self._run, ident)
            return self.status()

    def _run(self, ident):
        proc = None
        try:
            with self.lock:
                if self.start_id is not ident:
                    return
                self._prepare()
                self._config(starting=True)  # CPA hashes management.secret-key in place.
                port = self.data['port']
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(('127.0.0.1', port))
                env = {k: v for k, v in os.environ.items() if k in (
                    'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
                if self.data['proxy_url']:
                    env.update(HTTP_PROXY=self.data['proxy_url'], HTTPS_PROXY=self.data['proxy_url'],
                               NO_PROXY='127.0.0.1,localhost,::1')
                self.proc = proc = subprocess.Popen([str(self.binary), '-config', str(self.config)], cwd=self.root,
                                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT, start_new_session=True, umask=0o077)
                self.started = time.monotonic()
                logger = _background(self._drain, proc.stdout)
            ready = False
            deadline = time.monotonic() + 20
            opener = local_opener()
            while proc.poll() is None and time.monotonic() < deadline:
                try:
                    with opener.open(f'http://127.0.0.1:{port}/healthz', timeout=0.5) as response:
                        ready = response.status == 200
                except error.HTTPError as exc:
                    exc.close()
                except (OSError, http.client.HTTPException):
                    pass
                if ready:
                    break
                time.sleep(0.1)
            with self.lock:
                if self.proc is not proc:
                    return
                if not ready:
                    self._terminate(proc)
                    self._config()
                else:
                    self.state = 'ready'
            code = proc.wait()
            logger.join(timeout=1)
            with self.lock:
                if self.proc is not proc:
                    return
                self._terminate(proc)  # Reap leftover members after an unexpected leader exit.
                self.proc = None
                self._fail('cpa_exit' if ready else 'cpa_start',
                           f'Engine exited ({code})' if ready else 'Health check failed or engine exited at startup')
                if ready and self.data['autostart'] and not self.disabled and time.monotonic() - self.last_retry >= 60:
                    self.last_retry = time.monotonic()
                    self.start()
        except Exception:
            with self.lock:
                if self.start_id is ident:
                    if proc is not None:
                        self._terminate(proc)
                    self.proc = None
                    self._fail('cpa_start', 'Could not start engine; check binary, permissions and listen port')
                    if self.config.is_file() and 'management_key' in self.data:
                        with contextlib.suppress(OSError):
                            self._config()

    def _drain(self, stream):
        try:
            with stream:
                while raw := stream.readline(8192):
                    record = logging.LogRecord('embedded-cpa', logging.INFO, '', 0, _log_message(raw), (), None)
                    self.log_handler.handle(record)
        finally:
            self.log_handler.close()

    def logs(self, count):
        count = max(0, min(1000, count))
        lines = []
        if count:
            with self.log_handler.lock:
                for path in (self.log, self.log.with_suffix('.log.1')):
                    try:
                        lines = path.read_text(encoding='utf-8').splitlines()[-(count - len(lines)):] + lines
                    except FileNotFoundError:
                        pass
                    if len(lines) >= count:
                        break
        return lines

    @staticmethod
    def _terminate(proc):
        deadline = time.monotonic() + 5
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
            # A helper can ignore SIGTERM after its parent has already exited.
            while time.monotonic() < deadline:
                os.killpg(proc.pid, 0)
                time.sleep(0.05)
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def stop(self):
        with self.lock:
            self.start_id = self.install_id = self.progress = None
            if self.proc:
                self._terminate(self.proc)
                self.proc = None
                self._config()
            self.state = 'stopped' if self.binary.is_file() else 'missing'
            self.error = None
            return self.status()

    def restart(self):
        with self.lock:
            if self.state == 'installing':
                raise Problem('cpa_busy', 'Installation in progress', 409)
            self.stop()
            return self.start()

    def settings(self, changes):
        if 'proxy_url' in changes:
            value = changes['proxy_url']
            with self.lock:
                current = self.data['proxy_url']
            # the masked view of the stored proxy ([redacted]@host) sent back unchanged keeps the stored credentials
            if isinstance(value, str) and current and MASK in value and value.strip() == masked_proxy(current):
                value = current
            changes = {**changes, 'proxy_url': proxy_url(value)}
        if 'autostart' in changes and not isinstance(changes['autostart'], bool):
            raise Problem('cpa_invalid', 'Expected boolean autostart')
        with self.lock:
            if self.invalid_metadata:
                raise Problem('cpa_metadata', 'Reinstall to repair engine metadata', 409)
            changed = 'proxy_url' in changes and changes['proxy_url'] != self.data['proxy_url']
            self.data.update({k: changes[k] for k in ('autostart', 'proxy_url') if k in changes})
            self._save()
            if changed and self.state in ('ready', 'starting'):
                return self.restart()
            if changed and self.binary.is_file() and 'management_key' in self.data:
                self._config()
            return self.status()

    def connection(self):
        with self.lock:
            if self.state != 'ready' or not self.proc or self.proc.poll() is not None:
                raise Problem('cpa_not_ready', 'Engine is not ready', 409)
            return (f'http://127.0.0.1:{self.data["port"]}', self.data['management_key'], self.data['api_key'])

    def is_managed(self, pid):
        with self.lock:
            return bool(pid) and self.data.get('cpa_profile_id') == pid

    def managed_connection(self, pid):
        """For the profile managed by the embedded CPA engine, verify the engine is live and return (base_url, api_key)."""
        with self.lock:
            if not pid or self.data.get('cpa_profile_id') != pid:
                return None
            base, _, key = self.connection()
            return base + '/v1', key

    def remember_profile(self, pid):
        with self.lock:
            self.data['cpa_profile_id'] = pid
            self._save()

    def forget_profile(self, pid):
        with self.lock:
            if pid and self.data.get('cpa_profile_id') == pid:
                self.data.pop('cpa_profile_id', None)
                self._save()
