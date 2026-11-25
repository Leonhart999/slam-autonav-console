"""Narrow, authenticated console bridge to the embedded CPA v8 engine."""
from functools import wraps
from http import HTTPStatus
import http.client
import json
from pathlib import PurePosixPath
import re
import secrets
from urllib import error, request
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Body
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from . import cpa, profiles

MANAGEMENT = '/v8/management'
WEBUI = {'codex', 'claude', 'antigravity', 'xai', 'devin'}
PROVIDERS = WEBUI | {'kimi', 'kimi-ai', 'copilot', 'meta'}
FILENAME = re.compile(r'[A-Za-z0-9._@+\-]{1,200}')
STATE = re.compile(r'[A-Za-z0-9._-]{1,128}')
SENSITIVE = re.compile('token|secret|refresh|access|password|key', re.I)
# Scalar fields from buildAuthFileEntryLocked. Do not copy path, private
# claims, quota payloads or arbitrary nested upstream/provider metadata.
ENTRY = set('''name id auth_index provider type label email account account_type project_id
    status status_message disabled unavailable runtime_only source size success failed
    supports_quota quota_provider quota_plugin_id created_at updated_at modtime next_retry_after
    priority weight websockets request_retry oauth_provider requires_sign_in renewal_state recovery_stage
    quota_blocked adopted adopted_at inference_verified last_inference_verified_at renewal_verified
    last_renewal_verified_at next_action siwc_bind_supported use_siwc siwc_status siwc_bound siwc_expires_at'''.split())
# Only this specific token-related metadata value can cross the boundary, and
# only if it is actually a timestamp rather than credential material.
EXPIRY = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)')
# Query parameters of an authorization URL that would carry a credential. Matched as whole names: Codex's URL has
# id_token_add_organizations (a flag) and SIWC sends id_token_hint (a hint the provider asked for), neither a secret.
URL_SECRET = re.compile(r'(?i)(access|refresh)_token|client_secret|password|api[_-]?key|token')
SAFE_REASONS = {'auth file not found', 'unknown or expired state', 'unsupported provider',
                'invalid state', 'provider does not match state', 'Authentication failed',
                'Authenticated account does not match target credential', 'invalid service account',
                'project_id missing', 'callback server unavailable', 'failed to generate authorization url'}


def _invalid(field):
    raise cpa.Problem('cpa_invalid', 'Invalid ' + field)


def _name(value):
    if not isinstance(value, str) or not FILENAME.fullmatch(value) or value in ('.', '..'):
        _invalid('credential filename')
    return value


def _state(value):
    if not isinstance(value, str) or not STATE.fullmatch(value) or '..' in value:
        _invalid('OAuth state')
    return value


def _provider(value):
    if value == 'anthropic':
        value = 'claude'
    if not isinstance(value, str) or value not in PROVIDERS:
        _invalid('provider')
    return value


def _url(value):
    if not isinstance(value, str) or len(value) > 16384 or re.search(r'[\s\x00-\x1f\\]', value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in ('https', 'http') and url.hostname and url.username is None and url.port != 0:
            return url
    except ValueError:
        pass
    return None


class Upstream(Exception):
    def __init__(self, status=0, reason='Invalid engine response'):
        self.status, self.reason = status, reason


def _call(engine, path, method='GET', *, query=None, body=None, multipart=None):
    base, management_key, api_key = engine.connection()
    models = path == '/v1/models'
    url = base + ('' if models else MANAGEMENT) + path
    if query:
        url += '?' + urlencode(query)
    headers = {'Authorization': 'Bearer ' + (api_key if models else management_key), 'Accept': 'application/json'}
    data = None
    if multipart is not None:
        headers['Content-Type'], data = multipart
    elif body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = request.Request(url, method=method, data=data, headers=headers)
    try:
        with cpa.local_opener().open(req, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024 or any(k.encode() in raw for k in (api_key, management_key)):
            raise Upstream()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise Upstream()
        return result
    except error.HTTPError as exc:
        # Error strings may contain proxy credentials, callback URLs or an
        # entire provider response. Only echo known, literal CPA diagnostics.
        reason = HTTPStatus(exc.code).phrase if exc.code in HTTPStatus._value2member_map_ else 'Engine request failed'
        with exc:
            try:
                value = json.loads(exc.read(4096)).get('error')
                if isinstance(value, str) and value in SAFE_REASONS:
                    reason = value
            except (ValueError, AttributeError, OSError, http.client.HTTPException):
                pass
        raise Upstream(exc.code, reason) from None
    except (OSError, http.client.HTTPException):
        raise Upstream(0, 'Engine connection failed') from None
    except (ValueError, RecursionError):
        raise Upstream() from None


def _fields(data, allowed):
    return {k: v for k, v in data.items() if k in allowed and isinstance(v, (str, bool, int, float))}


def _credential(item):
    if not isinstance(item, dict):
        raise Upstream()
    out = _fields(item, ENTRY)
    # sdk/cliproxy/auth/types.go:986 returns the literal API key as
    # AccountInfo's second value for API-key (not OAuth) accounts.
    hidden = [v for k, v in item.items() if SENSITIVE.search(k) and k != 'access_token_expires_at'
              and isinstance(v, str) and v]
    if item.get('account_type') == 'api_key':
        account = out.pop('account', None)
        if isinstance(account, str) and account:
            hidden.append(account)
    for k, v in list(out.items()):
        if isinstance(v, str) and (any(s in v for s in hidden) or
                                   re.search(r'(?i)Bearer\s+\S|-----BEGIN .*KEY-----|(?:token|password|secret|api[_-]?key)["\s]*[:=]', v)):
            out[k] = '[redacted]'
    value = item.get('access_token_expires_at')
    if isinstance(value, str) and EXPIRY.fullmatch(value):
        out['access_token_expires_at'] = value
    return out


def _login(result):
    url = _url(result.get('url'))
    state = result.get('state')
    if (not url or any(URL_SECRET.fullmatch(k)
                       for part in (url.query, url.fragment) for k in parse_qs(part))
            or not isinstance(state, str) or not STATE.fullmatch(state) or '..' in state):
        raise Upstream()
    out = {'url': result['url'], 'state': state}
    if type(result.get('expires_in')) is int:
        out['expires_in'] = result['expires_in']
    return out


def _models(engine):
    result = _call(engine, '/v1/models').get('data')
    if not isinstance(result, list):
        raise Upstream()
    return [_fields(m, {'id', 'owned_by'}) for m in result if isinstance(m, dict) and isinstance(m.get('id'), str)]


def register(ctx, engine, store):
    class SafeRoute(APIRoute):
        def get_route_handler(self):
            endpoint = super().get_route_handler()

            async def handle(req):
                try:
                    return await endpoint(req)
                except RequestValidationError:
                    # FastAPI otherwise includes the rejected input in the 422 body.
                    raise ctx.error(422, 'cpa_invalid', reason='Invalid request') from None
            return handle

    router = APIRouter(prefix='/cpa', route_class=SafeRoute)

    def guarded(fn):
        @wraps(fn)
        def handler(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except cpa.Problem as exc:
                raise ctx.error(exc.status, exc.code, reason=exc.detail) from None
            except Upstream as exc:
                # ctx.error's first parameter is also named status.
                err = ctx.error(502, 'cpa_error', reason=exc.reason)
                err.detail['params']['status'] = exc.status
                raise err from None
            except profiles.ProfileError as exc:
                raise ctx.error(400, exc.code, **exc.params) from None
            except OSError:
                raise ctx.error(500, 'cpa_io_error') from None
        return handler

    router.get('/status')(engine.status)
    for route in ('start', 'stop', 'restart'):
        router.post('/' + route)(guarded(getattr(engine, route)))

    @router.post('/install')
    @guarded
    def install(body: dict = Body(default={})):
        if set(body) - {'archive', 'proxy_url'}:
            _invalid('install option')
        return engine.install(body.get('archive'), body.get('proxy_url'))

    @router.get('/logs')
    def logs(lines: int = 200):
        return {'lines': engine.logs(lines)}

    @router.put('/settings')
    @guarded
    def settings(body: dict):
        if set(body) - {'autostart', 'proxy_url'}:
            _invalid('setting')
        return engine.settings(body)

    @router.post('/oauth/start')
    @guarded
    def oauth_start(body: dict):
        provider = _provider(body.get('provider'))
        query = {'provider': provider}
        if provider in WEBUI:
            query['is_webui'] = 'true'
        options = {}
        if 'proxy_url' in body:
            options['proxy_url'] = cpa.proxy_url(body['proxy_url'])
        if 'name' in body:
            options['name'] = _name(body['name'])
        result = _call(engine, '/oauth/auth-url', 'POST' if options else 'GET', query=query, body=options or None)
        out = _login(result)
        # xAI accepts is_webui but actually returns a device flow in v8.1.2.
        out['flow'] = result.get('flow', 'browser' if provider in WEBUI else 'device')
        if out['flow'] not in ('device', 'browser'):
            raise Upstream()
        if isinstance(result.get('user_code'), str):
            out['user_code'] = result['user_code']
        return out

    @router.get('/oauth/status')
    @guarded
    def oauth_status(state: str):
        result = _call(engine, '/oauth/status', query={'state': _state(state)})
        status = result.get('status')
        if status not in ('ok', 'wait', 'error'):
            raise Upstream()
        if status == 'error':
            msg = result.get('error')
            return {'status': status, 'error': msg if isinstance(msg, str) and msg in SAFE_REASONS else 'OAuth failed'}
        return {'status': status}

    @router.delete('/oauth/session')
    @guarded
    def cancel(state: str):
        result = _call(engine, '/oauth/session', 'DELETE', query={'state': _state(state)})
        if not isinstance(result.get('cancelled'), bool):
            raise Upstream()
        return {'cancelled': result['cancelled']}

    @router.post('/oauth/callback')
    @guarded
    def callback(body: dict):
        provider = _provider(body.get('provider'))
        redirect = body.get('redirect_url')
        url = _url(redirect)
        states = parse_qs(url.query, keep_blank_values=True).get('state', []) if url else []
        if len(states) != 1:
            _invalid('redirect_url')
        _state(states[0])
        _call(engine, '/oauth/callback', 'POST', body={'provider': provider, 'redirect_url': redirect})
        return {'ok': True}

    @router.post('/oauth/siwc')
    @guarded
    def siwc(body: dict):
        out = _login(_call(engine, '/oauth/siwc/bind', 'POST', body={'name': _name(body.get('name'))}))
        if 'expires_in' not in out:
            raise Upstream()
        return out

    @router.post('/vertex')
    @guarded
    def vertex(body: dict):
        filename = _name(body.get('filename'))
        text = body.get('content')
        try:
            if not isinstance(text, str) or len(text) >= 65536 or len(text.encode()) >= 65536:
                _invalid('content')
            obj = json.loads(text)
            if not isinstance(obj, dict) or obj.get('type') != 'service_account':
                _invalid('service account')
        except (ValueError, RecursionError):
            _invalid('service account')
        prefix = body.get('prefix', '')
        if not isinstance(prefix, str) or (prefix and not FILENAME.fullmatch(prefix)):
            _invalid('prefix')
        boundary = secrets.token_hex(24)
        payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                   f'Content-Type: application/json\r\n\r\n{text}\r\n--{boundary}--\r\n').encode()
        result = _call(engine, '/oauth/import', 'POST', query={'provider': 'vertex'},
                       multipart=('multipart/form-data; boundary=' + boundary, payload))
        out = {'ok': True}
        saved = result.get('auth-file')
        if isinstance(saved, str) and saved:
            out['name'] = PurePosixPath(saved).name
        if prefix:
            # The v8.1.2 import handler ignores prefix. Set it on the actual
            # saved credential rather than guessing the name from the upload.
            if not out.get('name'):
                raise Upstream()
            _call(engine, '/credentials/fields', 'PATCH', body={'name': _name(out['name']), 'prefix': prefix})
        return out

    @router.get('/credentials')
    @guarded
    def credentials():
        files = _call(engine, '/credentials').get('files')
        if not isinstance(files, list):
            raise Upstream()
        return {'items': [_credential(f) for f in files]}

    @router.patch('/credentials/status')
    @guarded
    def credential_status(body: dict):
        if not isinstance(body.get('disabled'), bool):
            _invalid('disabled flag')
        _call(engine, '/credentials/status', 'PATCH', body={'name': _name(body.get('name')), 'disabled': body['disabled']})
        return {'ok': True}

    @router.post('/credentials/refresh')
    @guarded
    def refresh(body: dict):
        _call(engine, '/credentials/refresh', 'POST', body={'name': _name(body.get('name'))})
        return {'ok': True}

    @router.post('/credentials/verify')
    @guarded
    def verify(body: dict):
        data = {'name': _name(body.get('name'))}
        if 'model' in body:
            if not isinstance(body['model'], str) or len(body['model']) > 200:
                _invalid('model')
            data['model'] = body['model']
        result = _call(engine, '/credentials/verify', 'POST', body=data)
        out = _fields(result, {'status', 'verified', 'recovery_stage', 'renewal_state', 'inference_verified'})
        if 'credential' in result:
            out['credential'] = _credential(result['credential'])
        return out

    @router.delete('/credentials')
    @guarded
    def delete(name: str):
        _call(engine, '/credentials', 'DELETE', query={'name': _name(name)})
        return {'ok': True}

    @router.get('/models')
    @guarded
    def models():
        return {'models': _models(engine)}

    @router.post('/use')
    @guarded
    def use(body: dict = Body(default={})):
        if not isinstance(body, dict) or (set(body) - {'activate'}):
            _invalid('use option')
        activate = body.get('activate', True)
        if not isinstance(activate, bool):
            _invalid('activate')
        models = _models(engine)
        with engine.lock, store.lock:
            base, _, key = engine.connection()
            old = store.get(engine.data.get('cpa_profile_id')) or store.managed()
            if not activate and old is None:
                return {'profile': None}
            entries = {m['id']: m for m in (old or {}).get('models', [])}
            ids = [m['id'] for m in models]
            raw = {'name': (old or {}).get('name') or 'CPA (OAuth)',
                   'note': (old or {}).get('note') if old else 'Managed by the embedded CPA engine',
                   'website': (old or {}).get('website', ''),
                   'color': (old or {}).get('color', ''),
                   'format': 'openai', 'base_url': base + '/v1', 'api_key': key,
                   'user_agent': (old or {}).get('user_agent', ''),
                   'headers': (old or {}).get('headers', {}),
                   'body': (old or {}).get('body', {}),
                   'model': (old or {}).get('model') if (old or {}).get('model') in ids else '',
                   'models': [entries.get(mid, {'id': mid}) for mid in ids]}
            profile = store.update(old['id'], raw) if old else store.create(raw)
            profile = store.mark_managed(profile['id'])
            engine.remember_profile(profile['id'])
            if activate:
                profile = store.activate(profile['id'])
            return {'profile': profiles.view(profile, managed=True)}

    ctx.router.include_router(router)
