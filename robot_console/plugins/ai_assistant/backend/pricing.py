"""Model prices for the usage estimate: one table for the plugin, keyed by model id, kept in pricing.json.

An entry is added by the user ('manual'), by the models.dev sync of the chosen providers ('sync'), or by a one-off
import of single models.dev models ('import'). Prices are per 1M tokens (input, output, cache_read, cache_write; a
missing field is not counted) in USD or CNY; models.dev prices are USD.

Sync rules: manual entries are never overwritten; an entry the user deletes while it is synced is remembered in
`hidden` so the next sync does not bring it back; editing a synced or imported entry turns it into a manual one;
when two providers in the sync list have the same model id, the first one in the list wins. Imported entries are
refreshed by every sync from their own provider, whether or not it is in the list.

A usage record is priced when it is written (the price is a snapshot, later edits do not change it). Records that had
no price get one as soon as a matching entry appears (see backfill). Lookup order for a call: exact id, then case
and a vendor prefix ("anthropic/...") and a date suffix ("-20250514") ignored, then the longest entry id found in the
model id between separators (relay names such as "relay--claude-opus-5-5-kiro"). The model the reply names comes
before the requested one at every step.

Routes (under /api/ext/ai_assistant):
  GET  /pricing                  entries, sync state, catalog state; starts a background sync when it is due
  POST /pricing/entries          {entry, old_id?} -> add or change a manual entry
  POST /pricing/entries/delete   {ids}
  PATCH /pricing/sync            {enabled?, providers?, unhide?}
  POST /pricing/sync             sync now (waits for it) -> listing
  GET  /pricing/catalog          ?q=&provider=&limit= -> models.dev providers and matching models (fetched if needed)
  POST /pricing/import           {models: [{provider, id}]}
"""
import json
import math
import os
import re
import threading
import time
import urllib.request

from fastapi import Body

CATALOG_URL = 'https://models.dev/api.json'
FIELDS = ('input', 'output', 'cache_read', 'cache_write')
CURRENCIES = ('USD', 'CNY')
SOURCES = ('manual', 'sync', 'import')
# official vendors whose prices a relay usually passes on; the user can change the list
DEFAULT_PROVIDERS = ['anthropic', 'openai', 'google', 'deepseek', 'xai', 'moonshotai', 'zhipuai', 'alibaba',
                     'mistral', 'minimax']
SYNC_EVERY = 86400              # an enabled sync runs again after a day
RETRY_AFTER = 600               # a failed automatic sync waits this long before the next try
FETCH_TIMEOUT = 40
MAX_CATALOG = 64 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_PRICE = 1e6                 # per 1M tokens
PROVIDER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
DATE_SUFFIX = re.compile(r'(?:[-_@]\d{8}|[-_@]\d{4}-\d{2}-\d{2})$')
SEPARATORS = '/:._-@ '


class PricingError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def fetch_raw(url=CATALOG_URL, timeout=FETCH_TIMEOUT):
    """The models.dev catalog as JSON (proxies come from the usual environment variables)."""
    req = urllib.request.Request(url, headers={'User-Agent': 'robot-console-ai-assistant', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:          # noqa: S310  fixed https URL
        data = resp.read(MAX_CATALOG + 1)
    if len(data) > MAX_CATALOG:
        raise ValueError('catalog too large')
    return json.loads(data)


def _rate(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= MAX_PRICE:
        return None
    return float(value)


def compact(raw):
    """Only what pricing needs from the models.dev catalog: providers and their models that have a price."""
    if not isinstance(raw, dict):
        raise ValueError('catalog is not an object')
    providers = []
    for pid, p in raw.items():
        if not isinstance(p, dict) or not isinstance(pid, str) or not PROVIDER_ID.match(pid):
            continue
        models = []
        for mid, m in (p.get('models') or {}).items():
            if not isinstance(m, dict) or not isinstance(mid, str) or not 0 < len(mid) <= 200:
                continue
            cost = m.get('cost') if isinstance(m.get('cost'), dict) else {}
            rates = {f: _rate(cost.get(f)) for f in FIELDS}
            if rates['input'] is None and rates['output'] is None:
                continue
            name = m.get('name') if isinstance(m.get('name'), str) else ''
            models.append({'id': mid, 'name': name[:200] or mid, **rates})
        if models:
            models.sort(key=lambda x: x['id'])
            name = p.get('name') if isinstance(p.get('name'), str) else ''
            providers.append({'id': pid, 'name': name[:200] or pid, 'models': models})
    if not providers:
        raise ValueError('catalog has no priced models')
    providers.sort(key=lambda x: x['id'])
    return {'providers': providers}


def _norm(mid):
    return mid.strip().lower()


def _variants(mid):
    """Spellings of a model id to compare: as is, without a vendor prefix, each also without a date suffix."""
    m = _norm(mid)
    out = []
    for v in (m, m.rsplit('/', 1)[-1]):
        for w in (v, DATE_SUFFIX.sub('', v)):
            if w and w not in out:
                out.append(w)
    return out


def _contains(text, part):
    """`part` occurs in `text` with a separator (or the end) on both sides."""
    start = text.find(part)
    while start >= 0:
        end = start + len(part)
        if (start == 0 or text[start - 1] in SEPARATORS) and (end == len(text) or text[end] in SEPARATORS):
            return True
        start = text.find(part, start + 1)
    return False


def entry_cost(entry, row):
    rates = {f: entry[f] for f in FIELDS if entry.get(f) is not None}
    if not rates:
        return None
    return sum((row.get(f) or 0) / 1e6 * rate for f, rate in rates.items())


class Pricing:
    def __init__(self, path, catalog_path):
        self.path = path
        self.catalog_path = catalog_path
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.data = self._empty()
        self.exists = False
        self.catalog = None
        self.catalog_fetched = None
        self.syncing = False
        self.listeners = []                    # called after the entries changed (backfill of the usage ledger)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('entries'), dict):
                self.data = {**self._empty(), **data, 'sync': {**self._empty()['sync'], **(data.get('sync') or {})}}
                self.exists = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            os.replace(path, path + '.broken')
        try:
            with open(catalog_path, encoding='utf-8') as f:
                cat = json.load(f)
            self.catalog = {'providers': cat['providers']}
            self.catalog_fetched = float(cat.get('fetched') or 0) or None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self._reindex()

    @staticmethod
    def _empty():
        return {'version': 1, 'entries': {}, 'hidden': [],
                'sync': {'enabled': True, 'providers': list(DEFAULT_PROVIDERS), 'last': None, 'attempt': None,
                         'error': None}}

    def _write(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self.exists = True

    def _reindex(self):
        exact, loose = {}, {}
        for mid in sorted(self.data['entries']):
            vs = _variants(mid)
            exact.setdefault(vs[0], mid)
            for v in vs[1:]:
                loose.setdefault(v, mid)
        self._exact, self._loose = exact, loose
        # longest first, so "claude-opus-5-5" wins over "claude-opus-5"
        self._parts = sorted(((v, mid) for mid in self.data['entries'] for v in _variants(mid) if len(v) >= 4),
                             key=lambda x: (-len(x[0]), x[1]))
        self._cache = {}

    def _changed(self):
        self._reindex()
        self._write()
        for fn in list(self.listeners):
            fn()

    # ---- reading ----
    def listing(self):
        with self.lock:
            entries = [{'id': mid, **e} for mid, e in self.data['entries'].items()]
            entries.sort(key=lambda e: (e.get('name') or e['id']).lower())
            sync = dict(self.data['sync'])
            names = {p['id']: p['name'] for p in (self.catalog or {}).get('providers', [])}
            return {'entries': entries, 'hidden': len(self.data['hidden']),
                    'sync': {**sync, 'running': self.syncing,
                             'provider_names': {p: names.get(p, p) for p in sync['providers']}},
                    'catalog': {'fetched': self.catalog_fetched,
                                'providers': len((self.catalog or {}).get('providers', []))}}

    def lookup(self, *candidates):
        """The entry for a call (first matching candidate at the strictest step), or None."""
        ids = [c for c in candidates if isinstance(c, str) and c.strip()]
        if not ids:
            return None
        key = tuple(ids)
        with self.lock:
            if key in self._cache:
                mid = self._cache[key]
            else:
                mid = self._find(ids)
                self._cache[key] = mid
            e = self.data['entries'].get(mid) if mid else None
            return {'id': mid, **e} if e else None

    def _find(self, ids):
        entries = self.data['entries']
        for c in ids:
            if c in entries:
                return c
        for c in ids:
            if _norm(c) in self._exact:
                return self._exact[_norm(c)]
        for c in ids:
            for v in _variants(c):
                if v in self._exact:
                    return self._exact[v]
                if v in self._loose:
                    return self._loose[v]
        for c in ids:
            text = _norm(c)
            for part, mid in self._parts:
                if len(part) < len(text) and _contains(text, part):
                    return mid
        return None

    # ---- manual changes ----
    def migrate(self, profiles):
        """Prices that version 0.5 kept per model of a service become manual entries, once."""
        if self.exists:
            return 0
        n = 0
        with self.lock:
            for p in profiles:
                for m in p.get('models') or []:
                    price = m.get('price')
                    if not isinstance(price, dict) or not isinstance(m.get('id'), str) or m['id'] in self.data['entries']:
                        continue
                    rates = {f: _rate(price.get(f)) for f in FIELDS}
                    if all(v is None for v in rates.values()):
                        continue
                    currency = price.get('currency') if price.get('currency') in CURRENCIES else 'USD'
                    self.data['entries'][m['id']] = {'name': m.get('label') or m['id'], 'currency': currency, **rates,
                                                     'source': 'manual', 'updated': time.time()}
                    n += 1
            self._changed()
        return n

    def save_entry(self, body, old_id=None):
        if not isinstance(body, dict):
            raise PricingError('pricing_invalid', field='entry')
        mid = body.get('id')
        if not isinstance(mid, str) or not mid.strip() or len(mid.strip()) > 200 or '\n' in mid:
            raise PricingError('pricing_invalid', field='id')
        mid = mid.strip()
        name = body.get('name') if isinstance(body.get('name'), str) else ''
        if len(name) > 200 or '\n' in name:
            raise PricingError('pricing_invalid', field='name')
        currency = body.get('currency', 'USD')
        if currency not in CURRENCIES:
            raise PricingError('pricing_invalid', field='currency')
        rates = {}
        for f in FIELDS:
            v = body.get(f)
            if v is None:
                rates[f] = None
                continue
            rates[f] = _rate(v)
            if rates[f] is None:
                raise PricingError('pricing_invalid', field=f)
        if all(v is None for v in rates.values()):
            raise PricingError('pricing_invalid', field='input')
        with self.lock:
            entries = self.data['entries']
            if old_id is not None and old_id not in entries:
                raise PricingError('pricing_not_found', id=old_id)
            if mid != old_id and mid in entries:
                raise PricingError('pricing_exists', id=mid)
            if mid not in entries and len(entries) >= MAX_ENTRIES:
                raise PricingError('pricing_invalid', field='entries')
            if old_id is not None and old_id != mid:
                del entries[old_id]
            entries[mid] = {'name': name.strip() or mid, 'currency': currency, **rates, 'source': 'manual',
                            'updated': time.time()}
            if mid in self.data['hidden']:
                self.data['hidden'].remove(mid)
            self._changed()
            return {'id': mid, **entries[mid]}

    def delete(self, ids):
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > MAX_ENTRIES:
            raise PricingError('pricing_invalid', field='ids')
        with self.lock:
            n = 0
            for mid in ids:
                e = self.data['entries'].pop(mid, None)
                if e is None:
                    continue
                n += 1
                if e.get('source') == 'sync' and mid not in self.data['hidden']:
                    self.data['hidden'].append(mid)
            if n:
                self._changed()
            return n

    def set_sync(self, body):
        if not isinstance(body, dict):
            raise PricingError('pricing_invalid', field='sync')
        with self.lock:
            sync = self.data['sync']
            if 'enabled' in body:
                if not isinstance(body['enabled'], bool):
                    raise PricingError('pricing_invalid', field='enabled')
                sync['enabled'] = body['enabled']
            if 'providers' in body:
                ps = body['providers']
                if not isinstance(ps, list) or len(ps) > 400 or \
                        not all(isinstance(p, str) and PROVIDER_ID.match(p) for p in ps):
                    raise PricingError('pricing_invalid', field='providers')
                ps = list(dict.fromkeys(ps))
                # entries that only came from a provider taken off the list go with it
                for mid, e in list(self.data['entries'].items()):
                    if e.get('source') == 'sync' and e.get('provider') not in ps:
                        del self.data['entries'][mid]
                sync['providers'] = ps
            if body.get('unhide'):
                self.data['hidden'] = []
            self._changed()
        return self.listing()

    # ---- models.dev ----
    def due(self, now=None):
        now = now or time.time()
        with self.lock:
            s = self.data['sync']
            if not s['enabled'] or self.syncing:
                return False
            if s.get('attempt') and s.get('error') and now - s['attempt'] < RETRY_AFTER:
                return False
            return not s.get('last') or now - s['last'] >= SYNC_EVERY

    def _fetch_catalog(self):
        cat = compact(fetch_raw())
        now = time.time()
        os.makedirs(os.path.dirname(self.catalog_path) or '.', exist_ok=True)
        tmp = self.catalog_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'fetched': now, **cat}, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, self.catalog_path)
        with self.lock:
            self.catalog, self.catalog_fetched = cat, now
        return cat

    def ensure_catalog(self):
        with self.lock:
            if self.catalog is not None:
                return self.catalog
        with self.sync_lock:
            if self.catalog is not None:
                return self.catalog
            try:
                return self._fetch_catalog()
            except Exception as e:  # noqa: BLE001  network, TLS, JSON
                raise PricingError('pricing_fetch_failed', reason=str(e)[:200])

    def sync(self):
        """Fetch models.dev and apply it; raises PricingError when the catalog can't be had."""
        with self.sync_lock:
            with self.lock:
                self.syncing = True
                self.data['sync']['attempt'] = time.time()
            try:
                try:
                    cat = self._fetch_catalog()
                except Exception as e:  # noqa: BLE001
                    with self.lock:
                        self.data['sync']['error'] = str(e)[:200] or type(e).__name__
                        self._write()
                    raise PricingError('pricing_fetch_failed', reason=self.data['sync']['error'])
                with self.lock:
                    self._apply(cat)
                    self.data['sync'].update(last=time.time(), error=None)
                    self._changed()
            finally:
                with self.lock:
                    self.syncing = False
        return self.listing()

    def _apply(self, cat):
        entries, hidden = self.data['entries'], set(self.data['hidden'])
        by_id = {p['id']: p for p in cat['providers']}
        now = time.time()
        taken = set()
        for pid in self.data['sync']['providers']:
            p = by_id.get(pid)
            if not p:
                continue
            for m in p['models']:
                mid = m['id']
                if mid in hidden or mid in taken:
                    continue
                old = entries.get(mid)
                if old and old.get('source') in ('manual', 'import'):
                    continue
                taken.add(mid)
                entries[mid] = {'name': m['name'], 'currency': 'USD', **{f: m[f] for f in FIELDS},
                                'source': 'sync', 'provider': pid, 'updated': now}
        for mid, e in entries.items():
            if e.get('source') == 'import':
                m = next((x for x in by_id.get(e.get('provider'), {}).get('models', []) if x['id'] == mid), None)
                if m:
                    e.update({f: m[f] for f in FIELDS}, name=m['name'], currency='USD', updated=now)

    def search_catalog(self, q='', provider='', limit=200):
        cat = self.ensure_catalog()
        q = (q or '').strip().lower()
        # the providers in the sync list first (official prices), in their order, then the rest by id
        with self.lock:
            rank = {pid: i for i, pid in enumerate(self.data['sync']['providers'])}
        ordered = sorted(cat['providers'], key=lambda p: (rank.get(p['id'], len(rank)), p['id']))
        providers = [{'id': p['id'], 'name': p['name'], 'count': len(p['models'])} for p in ordered]
        models = []
        for p in ordered:
            if provider and p['id'] != provider:
                continue
            for m in p['models']:
                if q and q not in m['id'].lower() and q not in m['name'].lower() and q not in p['id']:
                    continue
                models.append({'provider': p['id'], 'provider_name': p['name'], **m})
                if len(models) >= limit:
                    break
            if len(models) >= limit:
                break
        with self.lock:
            entries = self.data['entries']
            for m in models:
                m['added'] = m['id'] in entries
        return {'fetched': self.catalog_fetched, 'providers': providers, 'models': models}

    def import_models(self, refs):
        if not isinstance(refs, list) or not refs or len(refs) > 500:
            raise PricingError('pricing_invalid', field='models')
        cat = self.ensure_catalog()
        by_id = {p['id']: {m['id']: m for m in p['models']} for p in cat['providers']}
        now = time.time()
        with self.lock:
            n = 0
            for r in refs:
                if not isinstance(r, dict) or not isinstance(r.get('provider'), str) or not isinstance(r.get('id'), str):
                    raise PricingError('pricing_invalid', field='models')
                m = by_id.get(r['provider'], {}).get(r['id'])
                if not m:
                    raise PricingError('pricing_not_found', id=f"{r['provider']}/{r['id']}")
                if m['id'] not in self.data['entries'] and len(self.data['entries']) >= MAX_ENTRIES:
                    raise PricingError('pricing_invalid', field='entries')
                self.data['entries'][m['id']] = {'name': m['name'], 'currency': 'USD', **{f: m[f] for f in FIELDS},
                                                 'source': 'import', 'provider': r['provider'], 'updated': now}
                if m['id'] in self.data['hidden']:
                    self.data['hidden'].remove(m['id'])
                n += 1
            self._changed()
        return n


def backfill(ledger, table):
    """Price usage records that have no price yet, now that the table may have one for their model."""
    with ledger.write_lock, ledger.connection() as db:
        models = [r[0] for r in db.execute('SELECT DISTINCT model FROM usage WHERE cost IS NULL AND model IS NOT NULL')]
        for model in models:
            e = table.lookup(model)
            if not e:
                continue
            rates = {f: e[f] for f in FIELDS if e.get(f) is not None}
            if not rates:
                continue
            expr = ' + '.join(f'coalesce({f}, 0) * :{f}' for f in rates)
            db.execute(f'UPDATE usage SET cost = ({expr}) / 1e6, currency = :currency WHERE cost IS NULL AND model = :model',
                       {**rates, 'currency': e['currency'], 'model': model})


def register(ctx, table, ledger):
    def bad(e, status=400):
        return ctx.error(status, e.code, **e.params)

    def refill():
        try:
            backfill(ledger, table)
        except Exception as e:  # noqa: BLE001  the table change itself is saved
            ctx.log.warn(f'usage backfill failed: {e}')
    table.listeners.append(refill)

    def background_sync():
        try:
            table.sync()
        except PricingError as e:
            ctx.log.warn(f'models.dev price sync failed: {e.params.get("reason")}')
        except Exception as e:  # noqa: BLE001
            ctx.log.warn(f'models.dev price sync failed: {e}')

    @ctx.router.get('/pricing')
    def listing():
        if table.due():
            threading.Thread(target=background_sync, daemon=True, name='ai_assistant_pricing').start()
            time.sleep(0.05)                   # usually enough for `running` to show in this answer
        return table.listing()

    @ctx.router.post('/pricing/entries')
    def save_entry(body: dict = Body(...)):
        try:
            return table.save_entry(body.get('entry'), body.get('old_id'))
        except PricingError as e:
            raise bad(e, 404 if e.code == 'pricing_not_found' else 409 if e.code == 'pricing_exists' else 400)

    @ctx.router.post('/pricing/entries/delete')
    def delete_entries(body: dict = Body(...)):
        try:
            return {'deleted': table.delete(body.get('ids'))}
        except PricingError as e:
            raise bad(e)

    @ctx.router.patch('/pricing/sync')
    def set_sync(body: dict = Body(...)):
        try:
            return table.set_sync(body)
        except PricingError as e:
            raise bad(e)

    @ctx.router.post('/pricing/sync')
    def sync_now():
        try:
            return table.sync()
        except PricingError as e:
            raise bad(e, 502)

    @ctx.router.get('/pricing/catalog')
    def catalog(q: str = '', provider: str = '', limit: str = '200'):
        try:
            n = max(1, min(500, int(limit)))
        except ValueError:
            raise ctx.error(400, 'pricing_invalid', field='limit')
        try:
            return table.search_catalog(q[:200], provider[:100], n)
        except PricingError as e:
            raise bad(e, 502)

    @ctx.router.post('/pricing/import')
    def import_models(body: dict = Body(...)):
        try:
            return {'imported': table.import_models(body.get('models')), **table.listing()}
        except PricingError as e:
            raise bad(e, 502 if e.code == 'pricing_fetch_failed' else 404 if e.code == 'pricing_not_found' else 400)

