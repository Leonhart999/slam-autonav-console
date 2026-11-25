"""Plugins: folders with a plugin.yaml that add backend routes, pages and settings to the console.

Developer guide: docs/plugins.md and docs/plugin-guidelines.md.

A plugin is a folder <plugins dir>/<id>/ with
  plugin.yaml     manifest (id, api, version, name, description, backend, frontend, settings)
  <module>.py     optional backend, or a package <module>/; must define setup(ctx)
  <dir>/...       optional frontend: a pre-built script that calls window.RobotConsole.register()

Trust model: a plugin's Python code runs inside the console process with the console's rights. Therefore
  - scanning only reads plugin.yaml, it never imports code;
  - a newly found plugin is disabled until the user enables it on the Plugins page, and enabling takes effect
    at the next console start (no hot loading);
  - ROBOT_CONSOLE_NO_PLUGINS=1 starts the console without loading any plugin;
  - a plugin that fails to import or whose setup() raises is reported on the page; the console keeps running.

Enabled flags and settings live in one JSON state file (mode 0600). Secret settings are never sent to the
browser: the settings route only says whether they are set.

Python packages (manifest `python.requirements`) go into a venv of their own per plugin (PythonEnvs), installed
when the user presses Install on the Plugins page; the console's own environment is never changed. Plugin code
uses those packages in worker processes (ctx.jobs / ctx.run_python), never by importing them into the console.
"""
import asyncio
import collections
import copy
import hashlib
import importlib.util
import json
import math
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from urllib.parse import unquote, urlencode, urlsplit

import yaml
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

PLUGIN_API = 1                     # version of the plugin interface this console implements
SUPPORTED_APIS = (1,)
MANIFEST = 'plugin.yaml'
ID_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
MODULE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
SETTING_TYPES = ('string', 'text', 'number', 'integer', 'boolean', 'select', 'secret')
MAX_LENGTH = {'string': 1000, 'text': 20000, 'secret': 4000}
NO_PLUGINS_ENV = 'ROBOT_CONSOLE_NO_PLUGINS'
MODULE_PREFIX = 'robot_console_ext'
# One pip requirement: a name with optional extras and version specifiers. No URLs, paths, markers or pip options,
# so a manifest cannot point pip at another index or run an option such as --index-url.
_SPEC = r'(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+'
REQ_RE = re.compile(rf'^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*\])?'
                    rf'(?:\s*{_SPEC}(?:\s*,\s*{_SPEC})*)?$')
MAX_REQUIREMENTS = 40


def shown(path):
    """Path for display on the page: the home directory written as ~."""
    home = os.path.expanduser('~')
    return '~' + path[len(home):] if path == home or path.startswith(home + '/') else path


def _origin(folder):
    """'market' / 'file' for a plugin folder the plugin market installed (its .market.json), else None."""
    try:
        with open(os.path.join(folder, '.market.json'), encoding='utf-8') as f:
            source = json.load(f).get('source')
        return source if source in ('market', 'file') else None
    except (OSError, ValueError, AttributeError):
        return None


class PluginError(Exception):
    """A problem with one plugin, reported to the page as {code, params}."""

    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


# ---------------- manifest ----------------

def _bilingual(value, field, required=False):
    if value is None or value == '':
        if required:
            raise PluginError('plugin_manifest_field', field=field)
        return {'zh': '', 'en': ''}
    if isinstance(value, str):
        return {'zh': value, 'en': value}
    if isinstance(value, dict) and all(isinstance(value.get(k, ''), str) for k in ('zh', 'en')):
        zh, en = value.get('zh') or '', value.get('en') or ''
        if required and not (zh or en):
            raise PluginError('plugin_manifest_field', field=field)
        return {'zh': zh or en, 'en': en or zh}
    raise PluginError('plugin_manifest_field', field=field)


def _optional_str(m, key):
    value = m.get(key)
    if value is None:
        return ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise PluginError('plugin_manifest_field', field=key)
    return str(value)


def _inside(base, rel, field):
    """rel resolved inside base; '..' or absolute paths are refused."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or '\\' in rel:
        raise PluginError('plugin_manifest_field', field=field)
    path = os.path.realpath(os.path.join(base, rel))
    if path != os.path.realpath(base) and not path.startswith(os.path.realpath(base) + os.sep):
        raise PluginError('plugin_manifest_field', field=field)
    return path


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _setting(raw, index):
    field = f'settings[{index}]'
    if not isinstance(raw, dict):
        raise PluginError('plugin_manifest_field', field=field)
    key = raw.get('key')
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise PluginError('plugin_manifest_field', field=f'{field}.key')
    kind = raw.get('type')
    if kind not in SETTING_TYPES:
        raise PluginError('plugin_manifest_field', field=f'settings.{key}.type')
    s = {'key': key, 'type': kind, 'label': _bilingual(raw.get('label') or key, f'settings.{key}.label'),
         'help': _bilingual(raw.get('help'), f'settings.{key}.help')}
    if kind in ('number', 'integer'):
        for bound in ('min', 'max', 'step'):
            if raw.get(bound) is not None:
                if not _number(raw[bound]) or (kind == 'integer' and bound != 'step' and int(raw[bound]) != raw[bound]):
                    raise PluginError('plugin_manifest_field', field=f'settings.{key}.{bound}')
                s[bound] = raw[bound]
        if 'min' in s and 'max' in s and s['min'] > s['max']:
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.min')
    if kind in MAX_LENGTH:
        limit = raw.get('max_length', MAX_LENGTH[kind])
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LENGTH[kind]:
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.max_length')
        s['max_length'] = limit
    if kind == 'select':
        options = raw.get('options')
        if not isinstance(options, list) or not options:
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.options')
        s['options'] = []
        for o in options:
            value, label = (o.get('value'), o.get('label')) if isinstance(o, dict) else (o, None)
            if not isinstance(value, str) and not _number(value):
                raise PluginError('plugin_manifest_field', field=f'settings.{key}.options')
            s['options'].append({'value': value, 'label': _bilingual(label if label is not None else str(value),
                                                                     f'settings.{key}.options')})
        if len({json.dumps(o['value']) for o in s['options']}) != len(s['options']):
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.options')
    if kind == 'secret':
        if raw.get('default') not in (None, ''):
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.default')   # no secrets in manifests
        s['default'] = ''
    else:
        default = raw.get('default', _EMPTY[kind](s))
        try:
            s['default'] = check_value(s, default)
        except PluginError:
            raise PluginError('plugin_manifest_field', field=f'settings.{key}.default')
    return s


_EMPTY = {
    'string': lambda s: '', 'text': lambda s: '', 'boolean': lambda s: False, 'secret': lambda s: '',
    'number': lambda s: s.get('min', 0), 'integer': lambda s: int(s.get('min', 0)),
    'select': lambda s: s['options'][0]['value'],
}


def check_value(spec, value):
    """The setting value, checked against its specification; PluginError when it does not fit."""
    key, kind = spec['key'], spec['type']
    if kind in ('string', 'text', 'secret'):
        if not isinstance(value, str):
            raise PluginError('plugin_setting_type', key=key, type=kind)
        if len(value) > spec['max_length']:
            raise PluginError('plugin_setting_length', key=key, max=spec['max_length'])
        return value
    if kind == 'boolean':
        if not isinstance(value, bool):
            raise PluginError('plugin_setting_type', key=key, type=kind)
        return value
    if kind in ('number', 'integer'):
        if not _number(value) or (kind == 'integer' and int(value) != value):
            raise PluginError('plugin_setting_type', key=key, type=kind)
        if kind == 'integer':
            value = int(value)
        if value < spec.get('min', -math.inf) or value > spec.get('max', math.inf):
            raise PluginError('plugin_setting_range', key=key, min=spec.get('min', '-∞'), max=spec.get('max', '∞'))
        return value
    if kind == 'select':
        for o in spec['options']:
            if o['value'] == value and type(o['value']) is type(value):
                return value
        raise PluginError('plugin_setting_option', key=key)
    raise PluginError('plugin_setting_type', key=key, type=kind)


def read_manifest(folder):
    """The checked manifest of the plugin in `folder` (no plugin code is run). PluginError when it is invalid."""
    path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(path):
        raise PluginError('plugin_manifest_missing', file=MANIFEST)
    try:
        with open(path, encoding='utf-8') as f:
            m = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise PluginError('plugin_manifest_unreadable', reason=str(e).splitlines()[0][:200])
    if not isinstance(m, dict):
        raise PluginError('plugin_manifest_unreadable', reason='not a mapping')
    pid = m.get('id')
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise PluginError('plugin_manifest_field', field='id')
    if pid != os.path.basename(os.path.normpath(folder)):
        raise PluginError('plugin_id_mismatch', id=pid, folder=os.path.basename(os.path.normpath(folder)))
    api = m.get('api')
    if not isinstance(api, int) or isinstance(api, bool):
        raise PluginError('plugin_manifest_field', field='api')
    version = _optional_str(m, 'version')
    if not version:
        raise PluginError('plugin_manifest_field', field='version')
    out = {
        'id': pid, 'api': api, 'version': version,
        'name': _bilingual(m.get('name'), 'name', required=True),
        'description': _bilingual(m.get('description'), 'description'),
        'author': _optional_str(m, 'author'), 'license': _optional_str(m, 'license'),
        'homepage': _optional_str(m, 'homepage'), 'upstream': _optional_str(m, 'upstream'),
        'backend': None, 'frontend': None, 'settings': [], 'python': None,
    }
    for key in ('homepage', 'upstream'):
        if out[key] and not re.match(r'^https?://', out[key]):
            raise PluginError('plugin_manifest_field', field=key)
    backend = m.get('backend')
    if backend is not None:
        module = backend.get('module') if isinstance(backend, dict) else None
        if not isinstance(module, str) or not MODULE_RE.match(module):
            raise PluginError('plugin_manifest_field', field='backend.module')
        file = os.path.join(folder, module + '.py')
        package = os.path.join(folder, module, '__init__.py')
        if not os.path.isfile(file) and not os.path.isfile(package):
            raise PluginError('plugin_file_missing', path=module + '.py')
        out['backend'] = {'module': module, 'path': file if os.path.isfile(file) else package,
                          'package': not os.path.isfile(file)}
    frontend = m.get('frontend')
    if frontend is not None:
        if not isinstance(frontend, dict):
            raise PluginError('plugin_manifest_field', field='frontend')
        fdir = _inside(folder, frontend.get('dir', 'ui'), 'frontend.dir')
        fe = {'dir': fdir}
        for key, required in (('script', True), ('style', False)):
            rel = frontend.get(key)
            if rel is None and not required:
                continue
            full = _inside(fdir, rel, f'frontend.{key}')
            if not os.path.isfile(full):
                raise PluginError('plugin_file_missing', path=os.path.relpath(full, folder))
            fe[key] = os.path.relpath(full, fdir).replace(os.sep, '/')
        out['frontend'] = fe
    settings = m.get('settings') or []
    if not isinstance(settings, list):
        raise PluginError('plugin_manifest_field', field='settings')
    out['settings'] = [_setting(s, i) for i, s in enumerate(settings)]
    if len({s['key'] for s in out['settings']}) != len(out['settings']):
        raise PluginError('plugin_manifest_field', field='settings.key')
    python = m.get('python')
    if python is not None:
        reqs = python.get('requirements') if isinstance(python, dict) else None
        if (not isinstance(reqs, list) or not 1 <= len(reqs) <= MAX_REQUIREMENTS
                or not all(isinstance(r, str) and len(r) <= 200 and REQ_RE.match(r.strip()) for r in reqs)):
            raise PluginError('plugin_manifest_field', field='python.requirements')
        out['python'] = {'requirements': [r.strip() for r in reqs]}
    return out


# ---------------- state file ----------------

class StateFile:
    """Enabled flags and settings of every plugin: {"version": 1, "plugins": {id: {"enabled", "settings"}}}."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._read()

    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            return {'version': 1, 'plugins': {}}
        except (OSError, ValueError) as e:
            # Keep the broken file for the user, start with everything disabled
            print(f'[robot_console] plugin state {self.path} unreadable ({e}); plugins start disabled',
                  file=sys.stderr)
            try:
                os.replace(self.path, self.path + '.broken')
            except OSError:
                pass
            return {'version': 1, 'plugins': {}}
        plugins = data.get('plugins') if isinstance(data, dict) else None
        return {'version': 1, 'plugins': plugins if isinstance(plugins, dict) else {}}

    def entry(self, pid):
        e = self.data['plugins'].get(pid)
        return e if isinstance(e, dict) else {}

    def enabled(self, pid):
        return self.entry(pid).get('enabled') is True

    def settings(self, pid):
        s = self.entry(pid).get('settings')
        return dict(s) if isinstance(s, dict) else {}

    def update(self, pid, **fields):
        with self.lock:
            entry = dict(self.entry(pid))
            entry.update(fields)
            self.data['plugins'][pid] = entry
            self._write()

    def forget(self, pid):
        """Drop the enabled flag and the settings of a plugin (uninstall with purge)."""
        with self.lock:
            if self.data['plugins'].pop(pid, None) is not None:
                self._write()

    def _write(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# ---------------- in-process API client ----------------

class ApiCallError(Exception):
    """A console REST call made by a plugin failed. status = HTTP status, detail = the {code, params} body."""

    def __init__(self, status, detail):
        super().__init__(f'{status}: {detail}')
        self.status = status
        self.detail = detail


async def asgi_request(app, method, path, json_body=None, params=None, content=None, content_type=None):
    """Call an ASGI app in this process like an HTTP client would. Returns (status, parsed JSON or text).
    content/content_type send a raw body (bytes) instead of JSON."""
    parts = urlsplit(path)
    query = parts.query
    if params:
        query = (query + '&' if query else '') + urlencode(params, doseq=True)
    if content is not None:
        body = bytes(content)
    else:
        body = b'' if json_body is None else json.dumps(json_body).encode()
    headers = [(b'host', b'console'), (b'accept', b'application/json')]
    if content is not None:
        headers += [(b'content-type', (content_type or 'application/octet-stream').encode()),
                    (b'content-length', str(len(body)).encode())]
    elif json_body is not None:
        headers += [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method.upper(),
             'scheme': 'http', 'path': unquote(parts.path), 'raw_path': parts.path.encode(), 'root_path': '',
             'query_string': query.encode(), 'headers': headers, 'client': ('127.0.0.1', 0),
             'server': ('console', 80), 'state': {}}
    done = asyncio.Event()
    sent = False
    response = {'status': 500, 'body': []}

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await done.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        if message['type'] == 'http.response.start':
            response['status'] = message['status']
        elif message['type'] == 'http.response.body':
            response['body'].append(message.get('body', b''))
            if not message.get('more_body'):
                done.set()

    try:
        await app(scope, receive, send)
    finally:
        done.set()
    raw = b''.join(response['body'])
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        value = raw.decode('utf-8', 'replace')
    return response['status'], value


def multipart_body(fields=None, files=None):
    """(body, content type) of a multipart/form-data request. fields: {name: text}; files: {name: (filename,
    bytes or a file path)}."""
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in (fields or {}).items():
        chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                   str(value).encode(), b'\r\n']
    for name, (filename, payload) in (files or {}).items():
        if isinstance(payload, (str, os.PathLike)):
            with open(payload, 'rb') as f:
                payload = f.read()
        safe = str(filename).replace('"', '').replace('\r', '').replace('\n', '')
        chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                   'Content-Type: application/octet-stream\r\n\r\n'.encode(), bytes(payload), b'\r\n']
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks), f'multipart/form-data; boundary={boundary}'


class ConsoleApi:
    """Calls the console's own REST routes inside the process (ASGI), the way the web page would.

    The REST routes are the stable interface between plugins and the console. Plugin management routes
    (/api/plugins...) are refused, so a plugin cannot change other plugins or their settings."""

    def __init__(self, get_app):
        self._get_app = get_app

    async def request(self, method, path, json_body=None, params=None, files=None, data=None, content=None,
                      content_type=None):
        """files/data: a multipart form ({name: (filename, bytes or path)} / {name: text}); content: a raw body
        (bytes, with content_type). Without them the body is json_body as JSON."""
        parts = urlsplit(path)
        clean = posixpath.normpath(unquote(parts.path))
        if parts.scheme or parts.netloc or not clean.startswith('/api/'):
            raise ValueError(f'not a console API path: {path}')
        if clean == '/api/plugins' or clean.startswith('/api/plugins/'):
            raise PermissionError('plugins cannot call the plugin management API')
        if files is not None or data is not None:
            content, content_type = multipart_body(data, files)
        status, value = await asgi_request(self._get_app(), method, path, json_body, params, content, content_type)
        if status >= 400:
            detail = value.get('detail') if isinstance(value, dict) else value
            raise ApiCallError(status, detail)
        return value

    async def get(self, path, params=None):
        return await self.request('GET', path, params=params)

    async def post(self, path, json_body=None, params=None, **kwargs):
        return await self.request('POST', path, json_body, params, **kwargs)

    async def put(self, path, json_body=None, params=None, **kwargs):
        return await self.request('PUT', path, json_body, params, **kwargs)

    async def delete(self, path, params=None):
        return await self.request('DELETE', path, params=params)


# ---------------- Python environments of plugins ----------------

def requirements_digest(reqs):
    return hashlib.sha256('\n'.join(reqs).encode()).hexdigest()[:16]


def base_python():
    """The interpreter behind the console's own venv (e.g. /usr/bin/python3.10), so the plugin venvs do not
    depend on the console venv."""
    if sys.prefix != sys.base_prefix:
        candidate = os.path.join(sys.base_prefix, 'bin', f'python{sys.version_info[0]}.{sys.version_info[1]}')
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def clean_env():
    """Environment for venv/pip: without the console's PYTHONPATH (ROS packages), which would make pip treat
    packages as installed that the venv does not have."""
    return {k: v for k, v in os.environ.items()
            if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'VIRTUAL_ENV', 'PYTHONUSERBASE')}


class PythonEnvs:
    """One venv per plugin (envs_dir/<id>) holding the packages of its manifest's python.requirements.

    Installing runs `python -m venv` and `pip install` in a background thread, one plugin at a time; no plugin
    code runs. The record of the last install is envs_dir/<id>.json (state ready/failed, the requirements it was
    built for, pip freeze), pip's output envs_dir/<id>.log. An env built for other requirements (the plugin was
    updated) is 'outdated' and not used until it is reinstalled. A failed install leaves no env behind."""

    TIMEOUT = 1800        # s, the whole install
    LOG_TAIL = 65536      # bytes of pip output returned to the page

    def __init__(self, root, pip=None, python=None):
        self.root = root
        self.pip = pip or {}
        self.python = python or base_python()
        self.lock = threading.Lock()
        self.installing = {}                     # id -> start time
        self.threads = []

    def env_dir(self, pid):
        return os.path.join(self.root, pid)

    def python_of(self, pid):
        return os.path.join(self.env_dir(pid), 'bin', 'python')

    def _record(self, pid):
        try:
            with open(os.path.join(self.root, pid + '.json'), encoding='utf-8') as f:
                record = json.load(f)
            return record if isinstance(record, dict) else None
        except (OSError, ValueError):
            return None

    def status(self, pid, reqs):
        out = {'requirements': list(reqs), 'env': shown(self.env_dir(pid)), 'state': 'missing', 'error': None,
               'finished': None, 'packages': [], 'python': None}
        with self.lock:
            if pid in self.installing:
                return {**out, 'state': 'installing', 'started': self.installing[pid]}
        record = self._record(pid)
        if record is None:
            return out
        out.update(finished=record.get('finished'), error=record.get('error'))
        if record.get('state') == 'failed':
            return {**out, 'state': 'failed'}
        if record.get('state') != 'ready' or not os.path.isfile(self.python_of(pid)):
            return out
        out.update(packages=list(record.get('packages') or []), python=record.get('python'))
        out['state'] = 'ready' if record.get('digest') == requirements_digest(reqs) else 'outdated'
        return out

    def ready_python(self, pid, reqs):
        return self.python_of(pid) if self.status(pid, reqs)['state'] == 'ready' else None

    def install(self, pid, reqs):
        with self.lock:
            if self.installing:
                raise PluginError('plugin_python_busy', status=409, id=next(iter(self.installing)))
            self.installing[pid] = time.time()
        thread = threading.Thread(target=self._install, args=(pid, list(reqs)), daemon=True,
                                  name=f'plugin-env-{pid}')
        self.threads = [t for t in self.threads if t.is_alive()] + [thread]
        thread.start()

    def _pip_options(self):
        opts = []
        for key, flag in (('index_url', '--index-url'), ('extra_index_url', '--extra-index-url'),
                          ('proxy', '--proxy'), ('timeout', '--timeout'), ('retries', '--retries')):
            value = self.pip.get(key)
            if value not in (None, ''):
                opts += [flag, str(value)]
        return opts

    def _install(self, pid, reqs):
        env = self.env_dir(pid)
        log_path = os.path.join(self.root, pid + '.log')
        record = {'state': 'failed', 'requirements': reqs, 'digest': requirements_digest(reqs),
                  'started': time.time(), 'error': None}
        deadline = time.monotonic() + self.TIMEOUT
        try:
            os.makedirs(self.root, exist_ok=True)
            with open(log_path, 'w', encoding='utf-8') as log:
                def run(cmd):
                    log.write('$ ' + ' '.join(cmd) + '\n')
                    log.flush()
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise RuntimeError(f'install took longer than {self.TIMEOUT} s')
                    p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       cwd=self.root, timeout=left, env=clean_env())
                    log.flush()
                    if p.returncode:
                        raise RuntimeError(f'{os.path.basename(cmd[0])} {cmd[1]} {cmd[2]} exited with code '
                                           f'{p.returncode}')

                shutil.rmtree(env, ignore_errors=True)
                run([self.python, '-m', 'venv', env])
                req_file = os.path.join(env, 'requirements.txt')
                with open(req_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(reqs) + '\n')
                python = self.python_of(pid)
                run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--prefer-binary',
                     *self._pip_options(), '-r', req_file])
                freeze = subprocess.run([python, '-m', 'pip', 'freeze', '--all', '--disable-pip-version-check'],
                                        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                                        env=clean_env())
                version = subprocess.run([python, '-c', 'import platform; print(platform.python_version())'],
                                         capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                                         env=clean_env())
                record.update(state='ready', packages=freeze.stdout.split(), python=version.stdout.strip())
                log.write('\nInstalled.\n')
        except Exception as e:  # noqa: BLE001  reported on the page
            reason = str(e) if isinstance(e, RuntimeError) else f'{type(e).__name__}: {e}'
            # pip's own last ERROR line says more than its exit code
            errors = [line.strip() for line in self.log(pid).splitlines() if line.startswith('ERROR:')]
            record['error'] = (f'{reason}. {errors[-1]}' if errors else reason)[:600]
            shutil.rmtree(env, ignore_errors=True)
        finally:
            record['finished'] = time.time()
            try:
                os.makedirs(self.root, exist_ok=True)
                tmp = os.path.join(self.root, pid + '.json.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
                os.replace(tmp, os.path.join(self.root, pid + '.json'))
            except OSError:
                pass
            with self.lock:
                self.installing.pop(pid, None)

    def log(self, pid):
        try:
            with open(os.path.join(self.root, pid + '.log'), 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - self.LOG_TAIL))
                return f.read().decode('utf-8', 'replace')
        except OSError:
            return ''

    def remove(self, pid):
        with self.lock:
            if pid in self.installing:
                raise PluginError('plugin_python_busy', status=409, id=pid)
        shutil.rmtree(self.env_dir(pid), ignore_errors=True)
        for suffix in ('.json', '.log'):
            try:
                os.remove(os.path.join(self.root, pid + suffix))
            except OSError:
                pass

    def wait(self, timeout=None):
        """Wait for running installs (tests)."""
        for t in list(self.threads):
            t.join(timeout)


# ---------------- background jobs of plugins ----------------

JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')
JOB_ACTIVE = ('queued', 'running')
_OMITTED = object()


class PluginJobs:
    """Background jobs of one plugin: each job runs a worker script of the plugin in its Python environment, one
    job at a time, in its own process group (cancel kills it).

    Files: <data_dir>/jobs/<job id>/ with job.json (the record), input.json (what the worker reads), log.txt, and
    whatever the worker writes there; result.json when it succeeded, error.json ({code, params}) when it failed
    with a coded error. Worker protocol: `python -E -s <script> <job dir>`, cwd = job dir; stdout lines that are
    JSON objects with "progress" (0..1) and/or "phase" update the job, every other line goes to log.txt.
    Records survive restarts: a job that was queued or running when the console stopped becomes interrupted."""

    KEEP = 50
    LOG_LINES = 400

    def __init__(self, ctx):
        self._ctx = ctx
        self.root = os.path.join(ctx._data_dir, 'jobs')
        self.lock = threading.RLock()
        self.jobs = {}
        self._result_cache = {}
        self.queue = collections.deque()
        self.wake = threading.Condition(self.lock)
        self.thread = None
        self.proc = None
        self.closed = False
        if os.path.isdir(self.root):
            for jid in os.listdir(self.root):
                job = self._read(jid)
                if job is None:
                    continue
                if job['state'] in JOB_ACTIVE:
                    job.update(state='interrupted', finished=job.get('finished') or time.time(),
                               error={'code': 'plugin_job_interrupted', 'params': {}})
                    self._write(job)
                self.jobs[jid] = job
            self._prune()

    def _dir(self, jid):
        return os.path.join(self.root, jid)

    def _read(self, jid):
        if not JOB_ID_RE.match(jid):
            return None
        try:
            with open(os.path.join(self._dir(jid), 'job.json'), encoding='utf-8') as f:
                job = json.load(f)
        except (OSError, ValueError):
            return None
        return job if isinstance(job, dict) and job.get('id') == jid and job.get('state') else None

    def _write(self, job):
        path = os.path.join(self._dir(job['id']), 'job.json')
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(job, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _update(self, job, **fields):
        with self.lock:
            job.update(fields)
            try:
                self._write(job)
            except OSError:
                pass

    RESULT_IN_LIST = 65536     # bytes: results up to this size are part of the job list too

    def view(self, job, result=False):
        out = {k: v for k, v in job.items() if k not in ('script', 'cancel')}
        if result and job['state'] == 'succeeded':
            jid = job['id']
            if result == 'small':
                with self.lock:
                    if jid in self._result_cache:
                        cached = self._result_cache[jid]
                        if cached is not _OMITTED:
                            out['result'] = copy.deepcopy(cached)
                        return out
            path = os.path.join(self._dir(jid), 'result.json')
            try:
                if result == 'small' and os.path.getsize(path) > self.RESULT_IN_LIST:
                    with self.lock:
                        self._result_cache[jid] = _OMITTED
                    return out
                with open(path, encoding='utf-8') as f:
                    val = json.load(f)
                if result == 'small':
                    with self.lock:
                        self._result_cache[jid] = val
                    out['result'] = copy.deepcopy(val)
                else:
                    out['result'] = val
            except (OSError, ValueError):
                out['result'] = None
        return out

    def submit(self, kind, params, title='', target='', script='worker.py'):
        """Queue a job; returns its record. Raises HTTPException 409 when the plugin's Python environment is not
        ready. params must be JSON-serialisable; the worker finds them in input.json."""
        python = self._ctx.python
        if python is None:
            raise self._ctx._core_error(409, 'plugin_python_missing', id=self._ctx.id)
        full = _inside(self._ctx.dir, script, 'script')
        if not os.path.isfile(full):
            raise self._ctx._core_error(500, 'plugin_job_failed', reason=f'worker script {script} is missing')
        with self.lock:
            if self.closed:
                raise self._ctx._core_error(503, 'plugin_job_failed', reason='the console is stopping')
            jid = uuid.uuid4().hex
            os.makedirs(self._dir(jid))
            job = {'id': jid, 'kind': str(kind), 'title': str(title), 'target': str(target), 'state': 'queued',
                   'phase': '', 'progress': 0.0, 'created': time.time(), 'started': None, 'finished': None,
                   'error': None, 'params': params, 'script': script}
            with open(os.path.join(self._dir(jid), 'input.json'), 'w', encoding='utf-8') as f:
                json.dump({'id': jid, 'kind': job['kind'], 'target': job['target'], 'params': params,
                           'plugin_dir': self._ctx.dir, 'data_dir': self._ctx.data_dir, 'job_dir': self._dir(jid)},
                          f, ensure_ascii=False, indent=2)
            self._write(job)
            self.jobs[jid] = job
            self.queue.append(jid)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True, name=f'plugin-jobs-{self._ctx.id}')
                self.thread.start()
            self.wake.notify_all()
            return self.view(job)

    def _loop(self):
        while True:
            with self.lock:
                while not self.queue and not self.closed:
                    self.wake.wait()
                if self.closed:
                    return
                job = self.jobs.get(self.queue.popleft())
                if job is None or job['state'] != 'queued':
                    continue
            try:
                self._run(job)
            except Exception as e:  # noqa: BLE001  keep the loop alive
                self._update(job, state='failed', finished=time.time(),
                             error={'code': 'plugin_job_failed', 'params': {'reason': f'{type(e).__name__}: {e}'}})
            with self.lock:
                self._prune()

    def _run(self, job):
        jdir = self._dir(job['id'])
        python = self._ctx.python
        if python is None:
            self._update(job, state='failed', finished=time.time(),
                         error={'code': 'plugin_python_missing', 'params': {'id': self._ctx.id}})
            return
        script = os.path.join(self._ctx.dir, job['script'])
        tail = collections.deque(maxlen=self.LOG_LINES)
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        with self.lock:
            if self.closed or job.get('cancel'):
                self._update(job, state='cancelled', finished=time.time(), error=None)
                return
            self._update(job, state='running', started=time.time(), phase='')
        with open(os.path.join(jdir, 'log.txt'), 'w', encoding='utf-8') as log:
            proc = subprocess.Popen([python, '-E', '-s', script, jdir], cwd=jdir, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                    errors='replace', start_new_session=True)
            with self.lock:
                self.proc = proc
                cancelled = bool(job.get('cancel'))
            if cancelled:
                # cancel() or close() came while the process did not exist yet (proc was still None), so nothing
                # was killed then; without this the worker would run to the end before the job is marked cancelled
                self._kill(proc)
            last = 0.0
            for line in proc.stdout:
                msg = None
                if line.startswith('{'):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        msg = None
                if isinstance(msg, dict) and ('progress' in msg or 'phase' in msg):
                    fields = {}
                    p = msg.get('progress')
                    if isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p):
                        fields['progress'] = round(min(max(float(p), 0.0), 1.0), 4)
                    if isinstance(msg.get('phase'), str):
                        fields['phase'] = msg['phase'][:80]
                    if isinstance(msg.get('detail'), dict):
                        fields['detail'] = msg['detail']
                    now = time.monotonic()
                    with self.lock:
                        if fields.get('phase', job['phase']) != job['phase'] or now - last > 0.5:
                            last = now
                            self._update(job, **fields)
                        else:
                            job.update(fields)
                    continue
                tail.append(line)
                log.write(line)
                log.flush()
            code = proc.wait()
        with self.lock:
            self.proc = None
            if job.get('cancel'):
                self._update(job, state='cancelled', finished=time.time(), error=None)
            elif code == 0 and os.path.isfile(os.path.join(jdir, 'result.json')):
                self._update(job, state='succeeded', progress=1.0, finished=time.time())
            else:
                error = None
                try:
                    with open(os.path.join(jdir, 'error.json'), encoding='utf-8') as f:
                        error = json.load(f)
                except (OSError, ValueError):
                    pass
                if not (isinstance(error, dict) and isinstance(error.get('code'), str)):
                    lines = [s.strip() for s in tail if s.strip()]
                    error = {'code': 'plugin_job_failed',
                             'params': {'reason': (lines[-1] if lines else f'exit code {code}')[:400]}}
                else:
                    error = {'code': error['code'], 'params': error.get('params') if isinstance(error.get('params'), dict)
                             else {}}
                self._update(job, state='failed', finished=time.time(), error=error)

    def _prune(self):
        done = sorted((j for j in self.jobs.values() if j['state'] not in JOB_ACTIVE),
                      key=lambda j: j.get('finished') or j['created'], reverse=True)
        for job in done[self.KEEP:]:
            shutil.rmtree(self._dir(job['id']), ignore_errors=True)
            self.jobs.pop(job['id'], None)
            self._result_cache.pop(job['id'], None)

    def get(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if job is None:
                raise self._ctx._core_error(404, 'plugin_job_not_found', id=jid)
            return job

    def list(self, kind=None):
        with self.lock:
            jobs = [j for j in self.jobs.values() if kind is None or j['kind'] == kind]
            return [self.view(j, result='small') for j in sorted(jobs, key=lambda j: j['created'], reverse=True)]

    def dir_of(self, jid):
        """Folder of a job (its outputs), for the plugin's own routes."""
        return self._dir(self.get(jid)['id'])

    def cancel(self, jid):
        with self.lock:
            job = self.get(jid)
            if job['state'] == 'queued':
                self._update(job, state='cancelled', finished=time.time())
                return self.view(job)
            if job['state'] != 'running':
                return self.view(job)
            job['cancel'] = True
            proc = self.proc
        if proc is not None:
            self._kill(proc)
        return self.view(job)

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def remove(self, jid):
        with self.lock:
            job = self.get(jid)
            if job['state'] in JOB_ACTIVE:
                raise self._ctx._core_error(409, 'plugin_job_active', id=jid)
            shutil.rmtree(self._dir(jid), ignore_errors=True)
            self.jobs.pop(jid, None)
            self._result_cache.pop(jid, None)

    def file(self, jid, name):
        """A file the job wrote (inside its folder), or None."""
        base = os.path.realpath(self.dir_of(jid))
        full = os.path.realpath(os.path.join(base, name))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None
        return full

    def cancel_all(self):
        """Cancel all queued/running plugin jobs without closing the worker loop."""
        cancelled = []
        with self.lock:
            for job in self.jobs.values():
                if job['state'] == 'queued':
                    self._update(job, state='cancelled', finished=time.time(), error=None)
                    cancelled.append(job['id'])
                elif job['state'] == 'running':
                    job['cancel'] = True
                    cancelled.append(job['id'])
            proc = self.proc
        if proc is not None:
            threading.Thread(target=self._kill, args=(proc,), daemon=True).start()
        return cancelled

    def close(self):
        with self.lock:
            self.closed = True
            proc = self.proc
            for job in self.jobs.values():
                if job['state'] in JOB_ACTIVE:
                    job['cancel'] = True
            self.wake.notify_all()
            thread = self.thread
        if proc is not None:
            self._kill(proc)
        if thread is not None and thread is not threading.current_thread():
            thread.join(10)

    def mount(self, router):
        """Generic routes of the jobs on the plugin's router (/api/ext/<id>/jobs...)."""
        ctx = self._ctx

        @router.get('/jobs')
        def jobs_list(kind: str | None = None):
            return {'python': ctx.python_status(), 'jobs': self.list(kind)}

        @router.get('/jobs/{jid}')
        def jobs_get(jid: str):
            return self.view(self.get(jid), result=True)

        @router.post('/jobs/{jid}/cancel')
        def jobs_cancel(jid: str):
            return self.cancel(jid)

        @router.delete('/jobs/{jid}')
        def jobs_remove(jid: str):
            self.remove(jid)
            return {'ok': True}

        @router.get('/jobs/{jid}/log')
        def jobs_log(jid: str):
            path = os.path.join(self.dir_of(jid), 'log.txt')
            try:
                with open(path, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 65536))
                    return {'text': f.read().decode('utf-8', 'replace')}
            except OSError:
                return {'text': ''}

        @router.get('/jobs/{jid}/files/{name:path}')
        def jobs_file(jid: str, name: str):
            full = self.file(jid, name)
            if full is None:
                raise ctx._core_error(404, 'plugin_job_file_missing', name=name)
            return FileResponse(full, headers={'Cache-Control': 'no-store'})


# ---------------- plugin context ----------------

def emit(logger, level, msg):
    """Log through the console's ROS logger (stderr without one). rclpy remembers the severity of each call site
    and raises when one site logs with another severity, so every severity has its own line here."""
    try:
        if logger is None:
            print(msg, file=sys.stderr)
        elif level == 'info':
            logger.info(msg)
        elif level == 'warn':
            logger.warn(msg)
        else:
            logger.error(msg)
    except Exception:  # noqa: BLE001  logging must never break the console
        print(msg, file=sys.stderr)


class PluginLog:
    def __init__(self, pid, logger):
        self._prefix = f'[plugin {pid}] '
        self._logger = logger

    def _emit(self, level, msg):
        emit(self._logger, level, self._prefix + str(msg))

    def info(self, msg):
        self._emit('info', msg)

    def warn(self, msg):
        self._emit('warn', msg)

    warning = warn

    def error(self, msg):
        self._emit('error', msg)


class PluginContext:
    """Everything a plugin backend may use (plugin interface v1). See docs/plugins.md."""

    api_version = PLUGIN_API

    def __init__(self, manager, record):
        self.id = record.id
        self.dir = record.dir
        self.router = APIRouter()
        self.log = PluginLog(record.id, manager.logger)
        self.api = ConsoleApi(lambda: manager.app)
        self._manager = manager
        self._record = record
        self._data_dir = os.path.join(manager.data_dir, record.id)
        self._shutdown = []
        self._jobs = None

    @property
    def data_dir(self):
        os.makedirs(self._data_dir, exist_ok=True)
        return self._data_dir

    def settings(self):
        """Current values (defaults filled in, secrets included). Read again on every call: changes made on the
        Plugins page apply without a restart."""
        return self._manager.values(self.id, secrets=True)

    def error(self, status, code, **params):
        """An HTTPException whose code the plugin's own language pack translates (errors.<code>)."""
        return HTTPException(status, detail={'code': f'plugin.{self.id}.{code}', 'params': params})

    ApiCallError = ApiCallError       # what ctx.api raises, so plugins need not import console modules

    @staticmethod
    def console_error(status, code, **params):
        """An HTTPException with one of the console's own error codes (translated by the console), e.g. to pass
        on the {code, params} of a failed ctx.api call."""
        return HTTPException(status, detail={'code': code, 'params': params})

    _core_error = console_error

    def on_shutdown(self, fn):
        self._shutdown.append(fn)

    # -- Python packages of the manifest (python.requirements) --

    def _requirements(self):
        python = (self._record.manifest or {}).get('python')
        return python['requirements'] if python else None

    @property
    def python(self):
        """Interpreter of the plugin's own venv when it is installed and up to date, else None. A plugin without
        python.requirements gets the console's interpreter."""
        reqs = self._requirements()
        if reqs is None:
            return sys.executable
        return self._manager.envs.ready_python(self.id, reqs)

    def python_status(self):
        """{state: missing|installing|ready|outdated|failed, requirements, packages, error, ...}, or None when
        the manifest lists no packages."""
        reqs = self._requirements()
        return None if reqs is None else self._manager.envs.status(self.id, reqs)

    async def run_python(self, args, input_data=None, timeout=60, cwd=None):
        """Run the plugin's Python (its venv) with args, e.g. [script path, ...], without blocking the console.
        Returns (exit code, stdout, stderr). Raises HTTPException 409 when the environment is not ready."""
        python = self.python
        if python is None:
            raise self._core_error(409, 'plugin_python_missing', id=self.id)
        proc = await asyncio.create_subprocess_exec(
            python, '-E', '-s', *[str(a) for a in args], cwd=cwd or self.data_dir,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), start_new_session=True)
        data = input_data.encode() if isinstance(input_data, str) else input_data
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await proc.wait()
            raise self._core_error(504, 'plugin_job_failed', reason=f'took longer than {timeout} s')
        return proc.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')

    @property
    def jobs(self):
        """Background jobs of this plugin (PluginJobs). Every plugin with a backend also gets the routes
        GET /jobs, GET /jobs/{id}, POST /jobs/{id}/cancel, DELETE /jobs/{id}, GET /jobs/{id}/log and
        GET /jobs/{id}/files/{name} under /api/ext/<id>/ (mounted after setup(), so its own routes win)."""
        if self._jobs is None:
            self._jobs = PluginJobs(self)
        return self._jobs


# ---------------- manager ----------------

class PluginRecord:
    def __init__(self, pid, folder):
        self.id = pid
        self.dir = folder
        self.manifest = None
        self.issue = None          # PluginError of the manifest (invalid / duplicate / incompatible)
        self.active = False        # loaded at this console start (backend set up, frontend served)
        self.load_error = None     # PluginError when loading at this start failed
        self.context = None
        self.disk_version = None   # of an active plugin: the version now on disk when it differs (updated)


class PluginManager:
    def __init__(self, cfg, logger=None):
        pc = cfg.get('plugins') or {}
        self.cfg = cfg
        self.dirs = list(pc.get('dirs') or [])
        shipped_dir = pc.get('shipped_dir')
        self.shipped_dir = os.path.normpath(shipped_dir) if shipped_dir else None
        self.data_dir = pc.get('data_dir') or os.path.join(cfg['paths']['data_root'], 'plugin_data')
        self.state = StateFile(pc.get('state_file') or os.path.join(cfg['paths']['data_root'], 'plugin_state.json'))
        self.envs = PythonEnvs(pc.get('envs_dir') or os.path.join(cfg['paths']['data_root'], 'plugin_envs'),
                               pip=pc.get('pip') or {}, python=pc.get('python') or None)
        self.logger = logger
        self.disabled_by_env = os.environ.get(NO_PLUGINS_ENV, '') not in ('', '0')
        self.app = None
        self.lock = threading.RLock()
        self.records = {}
        if self.dirs:
            try:
                os.makedirs(self.dirs[0], exist_ok=True)     # so "Open folder" works before the first plugin
            except OSError:
                pass
        self.scan()

    # -- discovery (reads manifests only) --

    def scan(self):
        with self.lock:
            old = self.records
            records = {}
            for base in self.dirs:
                is_shipped = bool(self.shipped_dir) and os.path.normpath(base) == self.shipped_dir
                try:
                    names = sorted(os.listdir(base))
                except OSError:
                    continue
                for name in names:
                    folder = os.path.join(base, name)
                    if name.startswith(('.', '_')) or not os.path.isdir(folder):
                        continue
                    prev = next((r for r in old.values() if r.dir == folder), None)
                    if prev is not None and prev.active:
                        rec = prev           # loaded code stays as it was until the restart
                        key = rec.id
                        try:                 # replaced on disk (market update): applies at the next start
                            version = read_manifest(folder)['version']
                        except PluginError:
                            version = None
                        rec.disk_version = version if version != rec.manifest['version'] else None
                    else:
                        rec = PluginRecord(name, folder)
                        try:
                            rec.manifest = read_manifest(folder)
                            if rec.manifest['api'] not in SUPPORTED_APIS:
                                rec.issue = PluginError('plugin_api_unsupported', api=rec.manifest['api'],
                                                        supported=', '.join(map(str, SUPPORTED_APIS)))
                        except PluginError as e:
                            rec.issue = e
                        if prev is not None:
                            rec.load_error = prev.load_error
                        key = rec.manifest['id'] if rec.manifest else name
                    if key in records:
                        # A user/market install in an earlier dir shadows the bundled copy instead of
                        # showing a duplicate-plugin error card. If the bundled copy is still loaded from
                        # startup, keep the active record until restart and mark its pending version.
                        if is_shipped:
                            if rec.active and records[key].manifest is not None:
                                rec.disk_version = records[key].manifest['version']
                                records[key] = rec
                            continue
                        dup = PluginRecord(name, folder)
                        dup.manifest = rec.manifest
                        dup.issue = PluginError('plugin_duplicate', id=key, other=records[key].dir)
                        records[f'{key}@{folder}'] = dup
                        continue
                    records[key] = rec
            # Plugins loaded at start stay listed even if their folder was removed meanwhile
            for pid, rec in old.items():
                if rec.active and pid not in records:
                    records[pid] = rec
                elif rec.active and pid in records and records[pid] is not rec and not os.path.isdir(rec.dir):
                    # Active market override was uninstalled while a bundled copy exists: keep the loaded
                    # instance listed until restart and show the bundled version as pending.
                    rec.disk_version = records[pid].manifest['version'] if records[pid].manifest else ''
                    records[pid] = rec
            self.records = records
        return self.list()

    def usable(self, rec):
        return rec.manifest is not None and rec.issue is None

    # -- loading at start --

    def load(self, app):
        """Import and set up every enabled plugin. Called once while the app is built, before the routes
        of the plugins are mounted."""
        self.app = app
        if self.disabled_by_env:
            self._log('warn', f'{NO_PLUGINS_ENV} is set: no plugin is loaded')
            return
        for rec in list(self.records.values()):
            if not self.usable(rec) or not self.state.enabled(rec.id):
                continue
            ctx = PluginContext(self, rec)
            backend = rec.manifest['backend']
            try:
                if backend:
                    module = self._import(rec, backend)
                    setup = getattr(module, 'setup', None)
                    if not callable(setup):
                        raise PluginError('plugin_no_setup', module=backend['module'])
                    setup(ctx)
            except PluginError as e:
                rec.load_error = e
            except (Exception, SystemExit) as e:  # noqa: BLE001  a plugin must never stop the console
                rec.load_error = PluginError('plugin_load_failed', reason=f'{type(e).__name__}: {e}'[:300])
                self._log('error', f'plugin {rec.id} failed to load:\n{traceback.format_exc()}')
            if rec.load_error is not None:
                self._log('error', f'plugin {rec.id} not loaded: {rec.load_error.code} {rec.load_error.params}')
                sys.modules.pop(f'{MODULE_PREFIX}.{rec.id}', None)
                continue
            rec.active = True
            rec.context = ctx
            if backend:
                ctx.jobs.mount(ctx.router)      # the generic job routes; their own routes were added first
            app.include_router(ctx.router, prefix=f'/api/ext/{rec.id}')
            self._log('info', f'plugin {rec.id} {rec.manifest["version"]} loaded')

    def _import(self, rec, backend):
        if MODULE_PREFIX not in sys.modules:
            pkg = importlib.util.module_from_spec(importlib.util.spec_from_loader(MODULE_PREFIX, loader=None))
            pkg.__path__ = []
            sys.modules[MODULE_PREFIX] = pkg
        name = f'{MODULE_PREFIX}.{rec.id}'
        kwargs = {'submodule_search_locations': [os.path.dirname(backend['path'])]} if backend['package'] else {}
        spec = importlib.util.spec_from_file_location(name, backend['path'], **kwargs)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def cancel_jobs(self):
        """Cancel active jobs across all loaded plugins without shutting the plugins down."""
        cancelled = []
        for rec in self.records.values():
            if rec.context is not None and rec.context._jobs is not None:
                try:
                    cancelled.extend(rec.context._jobs.cancel_all())
                except Exception as e:  # noqa: BLE001
                    self._log('warn', f'plugin {rec.id} jobs could not be cancelled: {e}')
        return cancelled

    def shutdown(self):
        for rec in self.records.values():
            for fn in reversed(rec.context._shutdown if rec.context else []):
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self._log('warn', f'plugin {rec.id} shutdown hook failed: {e}')
            if rec.context is not None and rec.context._jobs is not None:
                try:
                    rec.context._jobs.close()
                except Exception as e:  # noqa: BLE001
                    self._log('warn', f'plugin {rec.id} jobs did not stop: {e}')

    def _log(self, level, msg):
        emit(self.logger, level, msg if self.logger is not None else f'[robot_console] {msg}')

    # -- queries and changes from the page --

    def get(self, pid):
        rec = self.records.get(pid)
        if rec is None:
            raise PluginError('plugin_not_found', status=404, id=pid)
        return rec

    def status_of(self, rec):
        if rec.issue is not None:
            return {'plugin_duplicate': 'duplicate', 'plugin_api_unsupported': 'incompatible'}.get(
                rec.issue.code, 'invalid')
        if rec.active:
            return 'active'
        if rec.load_error is not None and self.state.enabled(rec.id):
            return 'failed'
        return 'disabled'

    def info(self, rec):
        m = rec.manifest
        enabled = self.state.enabled(rec.id) if self.usable(rec) else False
        state = self.status_of(rec)
        issue = rec.issue or (rec.load_error if state == 'failed' else None)
        fe = m['frontend'] if m and rec.active else None
        return {
            'id': rec.id, 'dir': shown(rec.dir), 'path': rec.dir, 'state': state, 'enabled': enabled,
            # the switch differs from what runs now: the change applies at the next start (a plugin that failed
            # at this start is not a pending change; a restart only retries it)
            'restart_needed': not self.disabled_by_env and (
                (enabled and not rec.active and rec.load_error is None) or (not enabled and rec.active)
                or (rec.active and (rec.disk_version is not None or not os.path.isdir(rec.dir)))),
            # version on disk of a loaded plugin that was updated or removed since the start ('' = removed)
            'pending_version': (rec.disk_version if rec.disk_version is not None else ('' if rec.active and not os.path.isdir(rec.dir) else None)),
            # how the folder got there: 'market' / 'file' (plugin market, .market.json), None (put by hand/shipped)
            'origin': _origin(rec.dir),
            'issue': {'code': issue.code, 'params': issue.params} if issue else None,
            'manifest': None if m is None else {
                k: m.get(k, '') for k in ('api', 'version', 'name', 'description', 'author', 'license', 'homepage',
                                          'upstream')},
            'has_backend': bool(m and m['backend']), 'has_frontend': bool(m and m['frontend']),
            'frontend': None if fe is None else {
                'script': f'/ext/{rec.id}/{fe["script"]}',
                'style': f'/ext/{rec.id}/{fe["style"]}' if fe.get('style') else None},
            'settings': [{k: v for k, v in s.items() if not (s['type'] == 'secret' and k == 'default')}
                         for s in (m['settings'] if m else [])],
            'python': self.envs.status(rec.id, m['python']['requirements']) if m and m.get('python') else None,
        }

    def python_of(self, pid):
        rec = self.get(pid)
        if rec.manifest is None or not rec.manifest.get('python') or rec.issue is not None:
            raise PluginError('plugin_python_none', status=409, id=pid)
        return rec

    def install_python(self, pid):
        """Install (or rebuild) the plugin's venv in the background; the page polls GET /api/plugins."""
        with self.lock:
            rec = self.python_of(pid)
            self.envs.install(pid, rec.manifest['python']['requirements'])
            return self.info(rec)

    def remove_python(self, pid):
        with self.lock:
            rec = self.python_of(pid)
            self.envs.remove(pid)
            return self.info(rec)

    def list(self):
        with self.lock:
            records = sorted(self.records.values(), key=lambda r: (r.issue is not None, r.id))
            return {'api_version': PLUGIN_API, 'dirs': [shown(d) for d in self.dirs], 'paths': self.dirs,
                    'disabled_by_env': self.disabled_by_env,
                    'plugins': [self.info(r) for r in records]}

    def set_enabled(self, pid, enabled):
        with self.lock:
            rec = self.get(pid)
            if enabled and not self.usable(rec):
                raise PluginError('plugin_unusable', status=409, id=pid)
            self.state.update(pid, enabled=bool(enabled))
            return self.info(rec)

    def values(self, pid, secrets=False):
        """Setting values with defaults filled in. Stored values that no longer fit the manifest (the plugin was
        updated) fall back to the default."""
        rec = self.get(pid)
        stored = self.state.settings(pid)
        out = {}
        for spec in (rec.manifest['settings'] if rec.manifest else []):
            value = stored.get(spec['key'], spec['default'])
            try:
                value = check_value(spec, value)
            except PluginError:
                value = spec['default']
            if spec['type'] == 'secret' and not secrets:
                continue
            out[spec['key']] = value
        return out

    def settings_view(self, pid):
        rec = self.get(pid)
        if rec.manifest is None:
            raise PluginError('plugin_unusable', status=409, id=pid)
        stored = self.state.settings(pid)
        return {'values': self.values(pid),
                'secrets': {s['key']: bool(stored.get(s['key'])) for s in rec.manifest['settings']
                            if s['type'] == 'secret'}}

    def save_settings(self, pid, values, clear=()):
        """values: new values (only the keys to change; a secret given as '' stays unchanged).
        clear: secrets to remove. Everything is checked before anything is written."""
        with self.lock:
            rec = self.get(pid)
            if rec.manifest is None:
                raise PluginError('plugin_unusable', status=409, id=pid)
            specs = {s['key']: s for s in rec.manifest['settings']}
            if not isinstance(values, dict):
                raise PluginError('plugin_setting_type', key='values', type='object')
            for key in list(values) + list(clear):
                if key not in specs:
                    raise PluginError('plugin_setting_unknown', key=key)
            stored = self.state.settings(pid)
            new = copy.deepcopy(stored)
            for key, value in values.items():
                spec = specs[key]
                if spec['type'] == 'secret' and value == '':
                    continue
                new[key] = check_value(spec, value)
            for key in clear:
                if specs[key]['type'] != 'secret':
                    raise PluginError('plugin_setting_type', key=key, type='secret')
                new.pop(key, None)
            # drop keys the manifest no longer has
            new = {k: v for k, v in new.items() if k in specs}
            self.state.update(pid, settings=new)
            return self.settings_view(pid)

    def asset(self, pid, path):
        """File of an active plugin's frontend folder, or None."""
        rec = self.records.get(pid)
        if rec is None or not rec.active or not rec.manifest['frontend']:
            return None
        base = os.path.realpath(rec.manifest['frontend']['dir'])
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None
        return full


def register_plugin_routes(app, plugins):
    """Plugin management routes, the plugins' own routes (/api/ext/<id>/...) and their frontend files
    (/ext/<id>/...). Call after every core route and before the web page is mounted at '/'."""

    from fastapi import Request
    from .plugin_market import PluginMarket

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    market = PluginMarket(plugins, plugins.cfg)
    plugins.market = market

    @app.get('/api/plugins')
    def list_plugins():
        return plugins.list()

    # -- plugin market (robot_console/plugin_market.py) --

    @app.get('/api/plugins/market')
    def plugin_market(refresh: bool = False):
        return market.view(refresh=refresh)

    @app.post('/api/plugins/market/upload')
    async def plugin_market_upload(request: Request, name: str = ''):
        try:
            return await market.install_upload(request.stream(), name)
        except PluginError as e:
            raise fail(e)

    @app.post('/api/plugins/market/{pid}/install')
    def plugin_market_install(pid: str):
        try:
            return market.install(pid)
        except PluginError as e:
            raise fail(e)

    @app.delete('/api/plugins/{pid}')
    def plugin_uninstall(pid: str, purge: bool = False):
        try:
            return market.uninstall(pid, purge=purge)
        except PluginError as e:
            raise fail(e)

    @app.post('/api/plugins/rescan')
    def rescan_plugins():
        return plugins.scan()

    @app.put('/api/plugins/{pid}/enabled')
    def set_plugin_enabled(pid: str, body: dict = Body(...)):
        if not isinstance(body.get('enabled'), bool):
            raise fail(PluginError('plugin_setting_type', key='enabled', type='boolean'))
        try:
            return plugins.set_enabled(pid, body['enabled'])
        except PluginError as e:
            raise fail(e)

    @app.get('/api/plugins/{pid}/settings')
    def get_plugin_settings(pid: str):
        try:
            return plugins.settings_view(pid)
        except PluginError as e:
            raise fail(e)

    @app.put('/api/plugins/{pid}/settings')
    def put_plugin_settings(pid: str, body: dict = Body(...)):
        clear = body.get('clear') or []
        if not isinstance(clear, list) or not all(isinstance(k, str) for k in clear):
            raise fail(PluginError('plugin_setting_type', key='clear', type='list'))
        try:
            return plugins.save_settings(pid, body.get('values') or {}, clear)
        except PluginError as e:
            raise fail(e)

    @app.post('/api/plugins/{pid}/python/install')
    def install_plugin_python(pid: str):
        try:
            return plugins.install_python(pid)
        except PluginError as e:
            raise fail(e)

    @app.delete('/api/plugins/{pid}/python')
    def remove_plugin_python(pid: str):
        try:
            return plugins.remove_python(pid)
        except PluginError as e:
            raise fail(e)

    @app.get('/api/plugins/{pid}/python/log')
    def plugin_python_log(pid: str):
        try:
            plugins.python_of(pid)
        except PluginError as e:
            raise fail(e)
        return {'text': plugins.envs.log(pid)}

    @app.get('/ext/{pid}/{path:path}')
    def plugin_asset(pid: str, path: str):
        full = plugins.asset(pid, path)
        if full is None:
            raise fail(PluginError('plugin_asset_missing', status=404, id=pid, path=path))
        # Plugin files keep their names across versions: always revalidate
        return FileResponse(full, headers={'Cache-Control': 'no-cache'})

    plugins.load(app)
