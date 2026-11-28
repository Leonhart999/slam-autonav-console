"""Model service profiles of the AI assistant (like the provider list of cc-switch): several services, one active.

A profile: name, note, website, colour, API format (openai = Chat Completions, anthropic = Messages API), base URL
plus extra candidate URLs to compare by latency, an optional "full URL" switch (the URL is the complete endpoint),
API key, a model list (request name, menu label, reasoning effort) with one selected model, proxy, User-Agent and
header / body overrides.

Stored in <plugin data>/providers.json with mode 0600. Keys never leave the backend: views carry `key_set` and a
short hint (`sk-…abcd`) only.
"""
import copy
import json
import os
import re
import threading
import uuid

from urllib.parse import unquote, urlsplit, urlunsplit

FORMATS = ('openai', 'anthropic')
EFFORT_VALUES = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
EFFORTS = ('',) + EFFORT_VALUES
COLORS = ('', 'cyan', 'blue', 'indigo', 'violet', 'pink', 'red', 'orange', 'amber', 'green', 'teal', 'slate')
MAX_PROFILES = 50
MAX_MODELS = 300
MAX_ENDPOINTS = 10
# body keys the assistant itself owns: an override could break the conversation or the tool loop
RESERVED_BODY = ('model', 'messages', 'tools', 'tool_choice', 'system', 'stream')
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")


class ProfileError(Exception):
    def __init__(self, code, **params):
        super().__init__(f'{code} {params}')
        self.code = code
        self.params = params


def _text(value, field, limit, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ProfileError('profile_invalid', field=field)
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ProfileError('profile_invalid', field=field)
    return value


def _url(value, field, required=False):
    value = _text(value, field, 500, required)
    if value and not re.match(r'^https?://[^\s/]+', value):
        raise ProfileError('base_url_invalid', url=value)
    return value.rstrip('/')


def _efforts(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileError('profile_invalid', field='models')
    picked = set()
    for e in value:
        if not isinstance(e, str) or e not in EFFORT_VALUES:
            raise ProfileError('profile_invalid', field='models')
        picked.add(e)
    return [e for e in EFFORT_VALUES if e in picked]


def _models(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_MODELS:
        raise ProfileError('profile_invalid', field='models')
    out, seen = [], set()
    for m in value:
        if not isinstance(m, dict):
            raise ProfileError('profile_invalid', field='models')
        mid = _text(m.get('id'), 'models', 200, True)
        if mid in seen:
            continue
        seen.add(mid)
        effort = m.get('effort') or ''
        if effort not in EFFORTS:
            raise ProfileError('profile_invalid', field='models')
        efforts = _efforts(m.get('efforts'))
        if efforts and effort and effort not in efforts:
            effort = ''
        entry = {'id': mid, 'label': _text(m.get('label'), 'models', 200) or mid,
                 'effort': effort, 'efforts': efforts}
        out.append(entry)          # a 'price' from version 0.5 is dropped: prices live in pricing.json now
    return out


def _headers(value):
    if value in (None, ''):
        return {}
    if not isinstance(value, dict) or len(value) > 50:
        raise ProfileError('profile_invalid', field='headers')
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or not HEADER_NAME.match(k) or not isinstance(v, (str, int, float)) \
                or '\n' in str(v) or '\r' in str(v) or len(str(v)) > 2000:
            raise ProfileError('profile_invalid', field='headers')
        out[k] = str(v)
    return out


def _body(value):
    if value in (None, ''):
        return {}
    if not isinstance(value, dict) or len(json.dumps(value)) > 20000:
        raise ProfileError('profile_invalid', field='body')
    bad = [k for k in value if k in RESERVED_BODY]
    if bad:
        raise ProfileError('body_reserved', keys=', '.join(bad))
    return copy.deepcopy(value)


MASK = '[redacted]'
_NON_SECRET = {'tokentype', 'tokencount', 'maxtokens', 'maxoutputtokens', 'maxcompletiontokens', 'finishreason', 'stopreason'}


def _secret_field(name):
    norm = re.sub(r'[^a-z0-9]', '', name.lower())
    if not norm or norm in _NON_SECRET:
        return False
    if re.search(r'(secret|password|passwd|apikey|authorization|privatekey|sessionkey|cookie|credential|'
                 r'accesstoken|refreshtoken|idtoken|authtoken|bearertoken|oauthtoken|signature)', norm):
        return True
    # short gateway header names such as X-Auth, X-Token, X-Key, X-Pass
    return norm in ('token', 'key', 'auth', 'pass', 'session') or \
        norm.endswith(('token', 'secret', 'apikey', 'key', 'auth', 'pass'))


def mask_url(value):
    """Hide user:password inside a URL while keeping scheme, host and port visible."""
    if not value:
        return ''
    try:
        u = urlsplit(value)
    except ValueError:
        return value
    if '@' not in u.netloc:
        return value
    host = u.netloc.rsplit('@', 1)[1]
    return urlunsplit((u.scheme, f'{MASK}@{host}', u.path, u.query, u.fragment))


def _restore_url(value, old, field):
    """A URL from the editor: the masked view of the stored URL ([redacted]@host) means "keep the stored one"."""
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ProfileError('profile_invalid', field=field)
    trimmed = value.strip()
    if MASK in trimmed:
        if old and trimmed.rstrip('/') == mask_url(old).rstrip('/'):
            return old
        raise ProfileError('profile_invalid', field=field)
    return trimmed


def _url_secrets(url, out):
    """The userinfo of a URL in every spelling an upstream may echo (raw and percent-decoded)."""
    if not url:
        return
    try:
        u = urlsplit(url)
    except ValueError:
        return
    if '@' not in u.netloc:
        return
    creds = u.netloc.rsplit('@', 1)[0]
    parts = [creds, *creds.split(':', 1)]
    for p in parts:
        if p:
            out.append(p)
            if unquote(p) != p:
                out.append(unquote(p))


def _credentials(value, old=None, mask=False):
    """Mask credential overrides in views; preserve masked values submitted by the editor."""
    if isinstance(value, list):
        prev = old if isinstance(old, list) else []
        return [_credentials(v, prev[i] if i < len(prev) else None, mask) for i, v in enumerate(value)]
    if not isinstance(value, dict):
        return value
    prev = old if isinstance(old, dict) else {}
    out = {}
    for k, v in value.items():
        if _secret_field(k):
            if mask:
                out[k] = MASK
            else:
                out[k] = prev.get(k, '') if v == MASK else v
        else:
            out[k] = _credentials(v, prev.get(k), mask)
    return out


def _collect_secrets(value, out, force=False):
    if isinstance(value, list):
        for item in value:
            _collect_secrets(item, out, force)
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect_secrets(v, out, force or _secret_field(str(k)))
    elif force and isinstance(value, str) and value and value != MASK:
        out.append(value)
        if ' ' in value:
            out.append(value.rsplit(' ', 1)[-1])


def safe_error(params, settings):
    """Relays and proxy errors sometimes quote the submitted credential or proxy password."""
    secrets = [settings.get('api_key')]
    px = settings.get('proxy') or ''
    if px:
        secrets.append(px)
    for url in (px, settings.get('base_url') or ''):
        _url_secrets(url, secrets)
    _collect_secrets(settings.get('headers'), secrets)
    _collect_secrets(settings.get('body'), secrets)
    out = dict(params)
    reason = out.get('reason')
    if isinstance(reason, str):
        for secret in sorted({s for s in secrets if s and len(s) >= 2}, key=len, reverse=True):
            reason = reason.replace(secret, MASK)
        reason = re.sub(r'(https?://|socks5h?://)[^\s/@]+@', rf'\1{MASK}@', reason)
        reason = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}', rf'\1 {MASK}', reason)
        reason = re.sub(r'(?i)((?:access_token|refresh_token|client_secret|api_key|password|secret)\s*[:=]\s*["\']?)[^\s"\',;&}]+',
                        rf'\1{MASK}', reason)
        out['reason'] = reason
    return out


def clean(raw, old=None):
    """A checked profile from browser input. `api_key`: None or '' keeps the old key, `clear_key` removes it."""
    if not isinstance(raw, dict):
        raise ProfileError('profile_invalid', field='profile')
    fmt = raw.get('format') or 'openai'
    if fmt not in FORMATS:
        raise ProfileError('provider_unknown', provider=fmt)
    color = raw.get('color') or ''
    if color not in COLORS:
        color = ''
    endpoints = raw.get('endpoints') or []
    if not isinstance(endpoints, list) or len(endpoints) > MAX_ENDPOINTS:
        raise ProfileError('profile_invalid', field='endpoints')
    prev = old or {}
    p = {
        'id': prev.get('id') or uuid.uuid4().hex[:12],
        'name': _text(raw.get('name'), 'name', 60, True),
        'note': _text(raw.get('note'), 'note', 200),
        'website': _url(_restore_url(raw.get('website'), prev.get('website', ''), 'website'), 'website'),
        'color': color,
        'format': fmt,
        'base_url': _url(_restore_url(raw.get('base_url'), prev.get('base_url', ''), 'base_url'), 'base_url'),
        'endpoints': [],
        'full_url': bool(raw.get('full_url')) and fmt == 'openai',
        'models': _models(raw.get('models')),
        'model': _text(raw.get('model'), 'model', 200),
        'proxy': _url(_restore_url(raw.get('proxy'), prev.get('proxy', ''), 'proxy'), 'proxy'),
        'user_agent': _text(raw.get('user_agent'), 'user_agent', 500),
        'headers': _credentials(_headers(raw.get('headers')), prev.get('headers')),
        'body': _credentials(_body(raw.get('body')), prev.get('body')),
        'api_key': prev.get('api_key', ''),
    }
    if prev.get('managed_by'):
        p['managed_by'] = prev['managed_by']          # set by the backend only, never by the editor
    old_endpoints = prev.get('endpoints') or []
    for e in endpoints:
        if isinstance(e, str) and MASK in e:
            match = next((o for o in old_endpoints if mask_url(o).rstrip('/') == e.strip().rstrip('/')), None)
            if match is None:
                raise ProfileError('profile_invalid', field='endpoints')
            e = match
        e = _url(e, 'endpoints', True)
        if e not in p['endpoints']:
            p['endpoints'].append(e)
    key = raw.get('api_key')
    if isinstance(key, str) and key.strip():
        if len(key.strip()) > 1000:
            raise ProfileError('profile_invalid', field='api_key')
        p['api_key'] = key.strip()
    if raw.get('clear_key'):
        p['api_key'] = ''
    ids = [m['id'] for m in p['models']]
    if p['model'] and p['model'] not in ids:
        p['models'].insert(0, {'id': p['model'], 'label': p['model'], 'effort': '', 'efforts': []})
    if not p['model'] and ids:
        p['model'] = ids[0]
    return p


def key_hint(key):
    if not key:
        return ''
    if len(key) <= 2:
        return MASK
    return f'{key[:3]}…{key[-4:]}' if len(key) > 10 else '…' + key[-2:]


def view(p, managed=False):
    """What the browser may see of a profile."""
    out = {k: copy.deepcopy(v) for k, v in p.items() if k not in ('api_key', 'managed_by')}
    for field in ('headers', 'body'):
        out[field] = _credentials(out[field], mask=True)
    out['proxy'] = mask_url(out.get('proxy', ''))
    out['base_url'] = mask_url(out.get('base_url', ''))
    out['endpoints'] = [mask_url(e) for e in out.get('endpoints') or []]
    out['website'] = mask_url(out.get('website', ''))
    out['key_set'] = bool(p.get('api_key'))
    out['key_hint'] = key_hint(p.get('api_key'))
    if managed or p.get('managed_by'):
        out['managed'] = True
    return out


def model_entry(p, mid=None):
    mid = mid or p.get('model') or ''
    return next((m for m in p.get('models') or [] if m['id'] == mid),
                {'id': mid, 'label': mid, 'effort': '', 'efforts': []})


def settings_for(p, general, model=None):
    """The flat settings dict providers.make_provider() takes, for profile `p` (and one of its models)."""
    m = model_entry(p, model)
    return {
        'provider': p['format'], 'base_url': p.get('base_url', ''), 'full_url': p.get('full_url', False),
        'api_key': p.get('api_key', ''), 'model': m['id'], 'effort': m.get('effort', ''),
        'proxy': p.get('proxy', ''), 'user_agent': p.get('user_agent', ''), 'headers': p.get('headers') or {},
        'body': p.get('body') or {},
        'timeout': general.get('timeout', 180), 'max_tool_rounds': general.get('max_tool_rounds', 12),
        'instructions': general.get('instructions', ''),
    }


class ProfileStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {'version': 1, 'active': '', 'profiles': []}
        self.exists = False
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('profiles'), list):
                self.data = data
                self.exists = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            os.replace(path, path + '.broken')       # keep the bad file for the user, start empty

    def _write(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self.exists = True

    def all(self):
        with self.lock:
            return copy.deepcopy(self.data['profiles'])

    def get(self, pid):
        with self.lock:
            p = next((x for x in self.data['profiles'] if x['id'] == pid), None)
            return copy.deepcopy(p) if p else None

    def active(self):
        with self.lock:
            profiles = self.data['profiles']
            p = next((x for x in profiles if x['id'] == self.data.get('active')), None)
            return copy.deepcopy(p) if p else None

    def listing(self):
        with self.lock:
            return {'active': self.data.get('active') or '', 'profiles': [view(p) for p in self.data['profiles']]}

    def create(self, raw):
        with self.lock:
            if len(self.data['profiles']) >= MAX_PROFILES:
                raise ProfileError('too_many_profiles', max=MAX_PROFILES)
            p = clean(raw)
            self.data['profiles'].append(p)
            if not self.data.get('active'):
                self.data['active'] = p['id']
            self._write()
            return p

    def update(self, pid, raw):
        with self.lock:
            for i, old in enumerate(self.data['profiles']):
                if old['id'] == pid:
                    p = clean(raw, old)
                    self.data['profiles'][i] = p
                    self._write()
                    return p
            raise ProfileError('profile_missing', id=pid)

    def delete(self, pid):
        with self.lock:
            before = len(self.data['profiles'])
            self.data['profiles'] = [p for p in self.data['profiles'] if p['id'] != pid]
            if len(self.data['profiles']) == before:
                raise ProfileError('profile_missing', id=pid)
            if self.data.get('active') == pid:
                self.data['active'] = self.data['profiles'][0]['id'] if self.data['profiles'] else ''
            self._write()

    def activate(self, pid, model=None, effort=None):
        """Make a profile (and one of its models) the active one; effort sets that model's reasoning effort."""
        with self.lock:
            p = next((x for x in self.data['profiles'] if x['id'] == pid), None)
            if p is None:
                raise ProfileError('profile_missing', id=pid)
            if model is not None:
                if model not in [m['id'] for m in p['models']]:
                    raise ProfileError('model_unknown', model=model)
                p['model'] = model
            if effort is not None:
                if effort not in EFFORTS:
                    raise ProfileError('profile_invalid', field='effort')
                entry = next((m for m in p['models'] if m['id'] == p.get('model')), None)
                if entry is None:
                    if not p.get('model'):
                        raise ProfileError('model_missing')
                    entry = {'id': p['model'], 'label': p['model'], 'effort': '', 'efforts': []}
                    p['models'].insert(0, entry)
                entry['effort'] = effort
            self.data['active'] = pid
            self._write()
            return copy.deepcopy(p)

    def mark_managed(self, pid, by='cpa'):
        """Record in providers.json itself that the embedded engine owns this profile, so it stays bound to the engine
        even if the engine's own metadata is lost."""
        with self.lock:
            for p in self.data['profiles']:
                if p['id'] == pid:
                    if p.get('managed_by') != by:
                        p['managed_by'] = by
                        self._write()
                    return copy.deepcopy(p)
            raise ProfileError('profile_missing', id=pid)

    def managed(self, by='cpa'):
        with self.lock:
            p = next((x for x in self.data['profiles'] if x.get('managed_by') == by), None)
            return copy.deepcopy(p) if p else None

    def reorder(self, ids):
        with self.lock:
            by_id = {p['id']: p for p in self.data['profiles']}
            if not isinstance(ids, list) or sorted(ids) != sorted(by_id):
                raise ProfileError('profile_invalid', field='order')
            self.data['profiles'] = [by_id[i] for i in ids]
            self._write()

    def migrate(self, legacy):
        """First start after the update: turn the old single-service plugin settings into a profile."""
        with self.lock:
            if self.exists or not legacy or not any(legacy.get(k) for k in ('base_url', 'api_key', 'model')):
                return None
            fmt = legacy.get('provider') if legacy.get('provider') in FORMATS else 'openai'
            raw = {'name': 'Claude' if fmt == 'anthropic' else 'Default', 'format': fmt,
                   'base_url': legacy.get('base_url') or '', 'model': legacy.get('model') or '',
                   'proxy': legacy.get('proxy') or '', 'api_key': legacy.get('api_key') or ''}
            try:
                p = clean(raw)
            except ProfileError:
                return None
            self.data['profiles'].append(p)
            self.data['active'] = p['id']
            self._write()
            return p


def draft_settings(store, general, pid, raw):
    """Settings for a not-yet-saved profile in the editor (model fetch, latency, test): the stored key is used when
    the editor's key field is empty."""
    old = store.get(pid) if pid else None
    raw = dict(raw or {})
    raw['name'] = (raw.get('name') or '').strip() or 'draft'
    p = clean(raw, old)
    return p, settings_for(p, general)
