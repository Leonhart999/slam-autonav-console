"""Plugin market: a JSON index of plugins on GitHub, and installing plugins from it or from a zip file.

Developer guide: docs/plugins.md ("Plugin market") and docs/plugin-guidelines.md.

The index (format 1) is a file like github.com/Leonhart999/slam-console-plugins/index.json:

    {"format": 1, "name": "...", "updated": "2026-11-20",
     "plugins": [{"id": "map_eval", "version": "0.1.0", "api": 1, "name": {"zh": ..., "en": ...},
                  "description": {...}, "author": ..., "license": ..., "homepage": ..., "source": ...,
                  "upstream": ..., "requirements": ["small_gicp==1.0.1", ...],
                  "url": "https://github.com/.../map_eval-0.1.0.zip", "sha256": "...", "size": 123456,
                  "published": "2026-11-20"}]}

The console tries the URLs of plugins.market.index_urls in order and keeps the last index it read in
${data_root}/plugin_market.json, so the list still shows when GitHub cannot be reached.

Installing never runs plugin code and keeps the trust model of plugins.py:
  - the zip is downloaded to ${data_root}/plugin_downloads, its size and SHA-256 must equal the index entry;
  - the archive must hold exactly one folder named like the plugin id, no absolute paths, no "..", no links,
    at most MAX_FILES files and MAX_UNPACKED bytes; its plugin.yaml must be valid and have the entry's id,
    version and a supported api;
  - it is unpacked next to the target folder (first entry of plugins.dirs) and swapped in with renames;
    a folder of the same id that was not installed by the market (no .market.json) is never replaced;
  - a new plugin is disabled like every new plugin; an update keeps the enabled flag and the settings,
    and applies at the next console start.
A zip file uploaded from the page goes through the same checks; it counts as verified when its SHA-256 equals
an entry of the last index read.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlsplit
from urllib.request import pathname2url

from .plugins import (ID_RE, REQ_RE, SUPPORTED_APIS, PluginError, _bilingual, read_manifest, shown)

INDEX_FORMAT = 1
DEFAULT_INDEX_URLS = [
    'https://raw.githubusercontent.com/Leonhart999/slam-console-plugins/main/index.json',
    # raw.githubusercontent.com is blocked on some networks where the API still works
    'https://api.github.com/repos/Leonhart999/slam-console-plugins/contents/index.json?ref=main',
]
PROVENANCE = '.market.json'      # written into every plugin folder the market installs
MAX_INDEX = 2 * 1024 * 1024      # bytes
MAX_FILES = 5000
MAX_UNPACKED = 512 * 1024 * 1024
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
USER_AGENT = 'robot-console-plugin-market'
CHUNK = 1 << 16


def version_key(v):
    """Sort key of a version string: numeric parts compare as numbers ('0.10.0' > '0.9.1'), a pre-release
    suffix ('1.0.0rc1', '1.0.0-beta') sorts before the release."""
    parts = []
    for piece in re.split(r'[.+]', str(v)):
        m = re.match(r'^(\d+)(.*)$', piece)
        if m:
            parts.append((int(m.group(1)), 0 if m.group(2) else 1, m.group(2)))
        else:
            parts.append((-1, 0, piece))
    return parts


def read_provenance(folder):
    try:
        with open(os.path.join(folder, PROVENANCE), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def check_entry(raw):
    """A checked index entry, or PluginError('plugin_market_entry', ...)."""
    def bad(field):
        return PluginError('plugin_market_entry', id=str(raw.get('id') if isinstance(raw, dict) else '?')[:40],
                           field=field)

    if not isinstance(raw, dict):
        raise bad('entry')
    pid = raw.get('id')
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise bad('id')
    version = raw.get('version')
    if not isinstance(version, str) or not 0 < len(version) <= 40:
        raise bad('version')
    api = raw.get('api')
    if not isinstance(api, int) or isinstance(api, bool):
        raise bad('api')
    url = raw.get('url')
    if not isinstance(url, str) or urlsplit(url).scheme not in ('https', 'http', 'file'):
        raise bad('url')
    sha = raw.get('sha256')
    if not isinstance(sha, str) or not SHA_RE.match(sha.lower()):
        raise bad('sha256')
    size = raw.get('size')
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise bad('size')
    reqs = raw.get('requirements') or []
    if not isinstance(reqs, list) or not all(isinstance(r, str) and REQ_RE.match(r.strip()) for r in reqs):
        raise bad('requirements')
    try:
        name = _bilingual(raw.get('name'), 'name', required=True)
        description = _bilingual(raw.get('description'), 'description')
    except PluginError as e:
        raise bad(e.params.get('field', 'name'))
    out = {'id': pid, 'version': version, 'api': api, 'name': name, 'description': description,
           'url': url, 'sha256': sha.lower(), 'size': size, 'requirements': [r.strip() for r in reqs]}
    for key in ('author', 'license', 'homepage', 'source', 'upstream', 'published'):
        value = raw.get(key, '')
        if not isinstance(value, str):
            raise bad(key)
        if key in ('homepage', 'source', 'upstream') and value and not re.match(r'^https?://', value):
            raise bad(key)
        out[key] = value[:300]
    return out


def check_index(data):
    """(entries, skipped) of an index document; PluginError when the document itself is unusable."""
    if not isinstance(data, dict) or data.get('format') != INDEX_FORMAT or not isinstance(data.get('plugins'), list):
        raise PluginError('plugin_market_index_failed', reason=f'not a plugin index of format {INDEX_FORMAT}')
    entries, skipped, seen = [], [], set()
    for raw in data['plugins']:
        try:
            e = check_entry(raw)
        except PluginError as err:
            skipped.append({'code': err.code, 'params': err.params})
            continue
        if e['id'] in seen:
            skipped.append({'code': 'plugin_market_entry', 'params': {'id': e['id'], 'field': 'id (duplicate)'}})
            continue
        seen.add(e['id'])
        entries.append(e)
    return entries, skipped


def _url(location):
    """file:// URL for a local path, other URLs unchanged."""
    if re.match(r'^[a-z][a-z0-9+.-]*://', location, re.I):
        return location
    return 'file://' + pathname2url(os.path.abspath(os.path.expanduser(location)))


class PluginMarket:
    def __init__(self, manager, cfg):
        pc = cfg.get('plugins') or {}
        mc = pc.get('market') or {}
        self.manager = manager
        self.index_urls = [str(u) for u in (mc.get('index_urls') or DEFAULT_INDEX_URLS) if str(u).strip()]
        self.proxy = mc.get('proxy') or (pc.get('pip') or {}).get('proxy') or ''
        self.timeout = float(mc.get('timeout') or 30)
        self.max_size = int(float(mc.get('max_size_mb') or 64) * 1024 * 1024)
        root = cfg['paths']['data_root']
        self.cache_path = mc.get('cache_file') or os.path.join(root, 'plugin_market.json')
        self.downloads = mc.get('downloads_dir') or os.path.join(root, 'plugin_downloads')
        self.lock = threading.Lock()           # one install / uninstall at a time
        self.cache = self._read_cache()

    # -- network --

    def _opener(self):
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({'http': self.proxy, 'https': self.proxy}))
        return urllib.request.build_opener(*handlers)

    def _open(self, url):
        headers = {'User-Agent': USER_AGENT}
        if urlsplit(url).hostname == 'api.github.com':
            headers['Accept'] = 'application/vnd.github.raw+json'
        return self._opener().open(urllib.request.Request(url, headers=headers), timeout=self.timeout)

    @staticmethod
    def _reason(e):
        if isinstance(e, urllib.error.HTTPError):
            return f'HTTP {e.code} {e.reason}'
        if isinstance(e, urllib.error.URLError):
            return str(e.reason)[:200]
        return f'{type(e).__name__}: {e}'[:200]

    # -- index --

    def _read_cache(self):
        try:
            with open(self.cache_path, encoding='utf-8') as f:
                data = json.load(f)
            entries, skipped = check_index(data['index'])
            return {'source': data.get('source'), 'fetched': data.get('fetched'), 'index': data['index'],
                    'entries': entries, 'skipped': skipped}
        except (OSError, ValueError, KeyError, TypeError, PluginError):
            return None

    def fetch(self):
        """Read the index from the first URL that answers with a valid index; cache it. PluginError when none
        does (the reasons of every URL in `reason`)."""
        reasons = []
        for location in self.index_urls:
            url = _url(location)
            try:
                with self._open(url) as r:
                    raw = r.read(MAX_INDEX + 1)
                if len(raw) > MAX_INDEX:
                    raise ValueError('index larger than 2 MB')
                data = json.loads(raw.decode('utf-8'))
                entries, skipped = check_index(data)
            except PluginError as e:
                reasons.append(f'{location}: {e.params.get("reason", e.code)}')
                continue
            except Exception as e:  # noqa: BLE001  any failure of one URL: try the next one
                reasons.append(f'{location}: {self._reason(e)}')
                continue
            self.cache = {'source': location, 'fetched': time.time(), 'index': data, 'entries': entries,
                          'skipped': skipped}
            try:
                os.makedirs(os.path.dirname(self.cache_path) or '.', exist_ok=True)
                tmp = self.cache_path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({k: self.cache[k] for k in ('source', 'fetched', 'index')}, f, ensure_ascii=False)
                os.replace(tmp, self.cache_path)
            except OSError:
                pass
            return self.cache
        raise PluginError('plugin_market_index_failed', status=502, reason=' | '.join(reasons)[:1200] or 'no URL')

    def view(self, refresh=False):
        error = None
        if refresh or self.cache is None:
            try:
                self.fetch()
            except PluginError as e:
                error = {'code': e.code, 'params': e.params}
        c = self.cache or {}
        index = c.get('index') or {}
        return {
            'index_urls': self.index_urls, 'source': c.get('source'), 'fetched': c.get('fetched'),
            'name': str(index.get('name') or ''), 'updated': str(index.get('updated') or ''),
            'stale': error is not None and bool(c), 'error': error, 'skipped': c.get('skipped') or [],
            'install_dir': shown(self.target_dir()) if self.target_dir() else None,
            'api_version': max(SUPPORTED_APIS), 'supported_apis': list(SUPPORTED_APIS),
            'busy': self.lock.locked(),
            'plugins': [self._entry_view(e) for e in c.get('entries') or []],
        }

    def _entry_view(self, e):
        installed = self.installed(e['id'])
        if e['api'] not in SUPPORTED_APIS:
            status = 'incompatible'
        elif installed is None:
            status = 'available'
        else:
            a, b = version_key(installed['version']), version_key(e['version'])
            status = 'installed' if a == b else ('update' if a < b else 'newer')
        return {**e, 'status': status, 'installed': installed}

    # -- installed plugins --

    def target_dir(self):
        return self.manager.dirs[0] if self.manager.dirs else None

    def installed(self, pid):
        """What is installed under this id: version on disk, where, and how it came there; None if nothing."""
        rec = self.manager.records.get(pid)
        if rec is None or not os.path.isdir(rec.dir):
            return None            # never installed, or removed while it is still loaded until the restart
        try:
            version = read_manifest(rec.dir)['version']
        except PluginError:
            version = rec.manifest['version'] if rec.manifest else '?'
        prov = read_provenance(rec.dir)
        target = self.target_dir()
        in_target = bool(target) and os.path.dirname(os.path.normpath(rec.dir)) == os.path.normpath(target)
        return {'version': version, 'dir': shown(rec.dir), 'origin': (prov or {}).get('source') or
                ('manual' if in_target else 'bundled'), 'state': self.manager.status_of(rec),
                'enabled': self.manager.state.enabled(pid), 'active': rec.active}

    # -- download and unpack --

    def _download(self, entry):
        os.makedirs(self.downloads, exist_ok=True)
        part = os.path.join(self.downloads, f'{entry["id"]}-{entry["version"]}.zip.part')
        limit = min(entry['size'], self.max_size)
        if entry['size'] > self.max_size:
            raise PluginError('plugin_market_too_large', status=413, size=entry['size'], max=self.max_size)
        digest = hashlib.sha256()
        size = 0
        try:
            with self._open(_url(entry['url'])) as r, open(part, 'wb') as f:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise PluginError('plugin_market_too_large', status=413, size=size, max=limit)
                    digest.update(chunk)
                    f.write(chunk)
        except PluginError:
            _remove(part)
            raise
        except Exception as e:  # noqa: BLE001
            _remove(part)
            raise PluginError('plugin_market_download_failed', status=502, url=entry['url'], reason=self._reason(e))
        actual = digest.hexdigest()
        if size != entry['size'] or actual != entry['sha256']:
            _remove(part)
            raise PluginError('plugin_market_checksum', expected=entry['sha256'], actual=actual,
                              size=size, expected_size=entry['size'])
        return part

    def _unpack(self, zpath, expected_id=None):
        """Unpack a plugin zip next to the target folder. Returns (temporary folder, plugin folder in it,
        manifest). The caller removes the temporary folder."""
        target = self.target_dir()
        if not target:
            raise PluginError('plugin_market_no_dir', status=409)
        os.makedirs(target, exist_ok=True)

        def bad(reason):
            return PluginError('plugin_market_bad_archive', reason=reason)

        try:
            z = zipfile.ZipFile(zpath)
        except (OSError, zipfile.BadZipFile) as e:
            raise bad(f'not a zip file ({e})')
        with z:
            infos = z.infolist()
            if not infos or len(infos) > MAX_FILES:
                raise bad(f'{len(infos)} entries (1 to {MAX_FILES} allowed)')
            if sum(i.file_size for i in infos) > MAX_UNPACKED:
                raise bad(f'more than {MAX_UNPACKED // (1024 * 1024)} MB unpacked')
            tops, items = set(), []
            for info in infos:
                name = info.filename
                parts = [p for p in name.split('/') if p not in ('', '.')]
                if (not parts or name.startswith('/') or '\\' in name or ':' in name or '..' in parts
                        or any(p.startswith('\x00') for p in parts)):
                    raise bad(f'unsafe path {name[:120]!r}')
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise bad(f'symbolic link {name[:120]!r}')
                tops.add(parts[0])
                items.append((info, parts))
            if len(tops) != 1:
                raise bad('the archive must hold exactly one folder (the plugin id); it holds ' +
                          ', '.join(sorted(tops))[:200])
            top = tops.pop()
            if not ID_RE.match(top):
                raise bad(f'folder name {top[:60]!r} is not a plugin id')
            if expected_id and top != expected_id:
                raise PluginError('plugin_market_mismatch', expected=expected_id, got=top)
            tmp = tempfile.mkdtemp(prefix=f'.{top}.installing-', dir=target)
            try:
                for info, parts in items:
                    dest = os.path.join(tmp, *parts)
                    if info.is_dir():
                        os.makedirs(dest, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(info) as src, open(dest, 'wb') as out:
                        shutil.copyfileobj(src, out, CHUNK)
                    os.chmod(dest, 0o644)
                folder = os.path.join(tmp, top)
                if not os.path.isdir(folder):
                    raise bad('no plugin folder in the archive')
                manifest = read_manifest(folder)
                if manifest['api'] not in SUPPORTED_APIS:
                    raise PluginError('plugin_market_incompatible', id=top, api=manifest['api'],
                                      supported=', '.join(map(str, SUPPORTED_APIS)))
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
        return tmp, folder, manifest

    def _place(self, tmp, folder, pid, provenance):
        """Swap the unpacked folder in as <target>/<pid>. Never replaces a folder the market did not install."""
        target = self.target_dir()
        dest = os.path.join(target, pid)
        with open(os.path.join(folder, PROVENANCE), 'w', encoding='utf-8') as f:
            json.dump(provenance, f, ensure_ascii=False, indent=2)
        old = None
        try:
            if os.path.lexists(dest):
                if os.path.islink(dest) or read_provenance(dest) is None:
                    raise PluginError('plugin_market_conflict', status=409, id=pid, dir=shown(dest))
                old = os.path.join(tmp, '.previous')
                os.rename(dest, old)
            os.rename(folder, dest)
        except BaseException:
            if old is not None and not os.path.lexists(dest):
                os.rename(old, dest)          # put the previous version back
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return dest

    def _finish(self, pid):
        self.manager.scan()
        rec = self.manager.records.get(pid)
        return {'plugin': self.manager.info(rec) if rec is not None else None, 'market': self.view()}

    # -- actions --

    def install(self, pid):
        """Install or update `pid` from the index (read again first, so the checksum is the current one)."""
        if not self.lock.acquire(blocking=False):
            raise PluginError('plugin_market_busy', status=409)
        try:
            if not self.target_dir():
                raise PluginError('plugin_market_no_dir', status=409)
            try:
                self.fetch()
            except PluginError:
                if self.cache is None:
                    raise
            entry = next((e for e in self.cache['entries'] if e['id'] == pid), None)
            if entry is None:
                raise PluginError('plugin_market_not_listed', status=404, id=pid)
            if entry['api'] not in SUPPORTED_APIS:
                raise PluginError('plugin_market_incompatible', id=pid, api=entry['api'],
                                  supported=', '.join(map(str, SUPPORTED_APIS)))
            self._check_replaceable(pid)
            part = self._download(entry)
            try:
                tmp, folder, manifest = self._unpack(part, expected_id=pid)
                if manifest['version'] != entry['version']:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise PluginError('plugin_market_mismatch', expected=f'{pid} {entry["version"]}',
                                      got=f'{manifest["id"]} {manifest["version"]}')
                self._place(tmp, folder, pid, {
                    'source': 'market', 'verified': True, 'version': entry['version'], 'url': entry['url'],
                    'sha256': entry['sha256'], 'index': self.cache.get('source'), 'installed': time.time()})
            finally:
                _remove(part)
            return self._finish(pid)
        finally:
            self.lock.release()

    async def install_upload(self, stream, filename):
        """Install a plugin zip sent as the raw request body (async iterator of bytes)."""
        if not self.lock.acquire(blocking=False):
            raise PluginError('plugin_market_busy', status=409)
        part = None
        try:
            if not self.target_dir():
                raise PluginError('plugin_market_no_dir', status=409)
            os.makedirs(self.downloads, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix='upload-', suffix='.zip.part', dir=self.downloads)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, 'wb') as f:
                async for chunk in stream:
                    size += len(chunk)
                    if size > self.max_size:
                        raise PluginError('plugin_market_too_large', status=413, size=size, max=self.max_size)
                    digest.update(chunk)
                    f.write(chunk)
            if not size:
                raise PluginError('plugin_market_bad_archive', reason='empty file')
            return await asyncio.to_thread(self._install_file, part, filename, digest.hexdigest())
        finally:
            if part:
                _remove(part)
            self.lock.release()

    def _install_file(self, part, filename, sha):
        tmp, folder, manifest = self._unpack(part)
        pid = manifest['id']
        try:
            self._check_replaceable(pid)
        except PluginError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        listed = next((e for e in (self.cache or {}).get('entries') or [] if e['sha256'] == sha), None)
        verified = listed is not None and listed['id'] == pid and listed['version'] == manifest['version']
        self._place(tmp, folder, pid, {
            'source': 'file', 'verified': verified, 'version': manifest['version'], 'sha256': sha,
            'file': os.path.basename(filename or '')[:200], 'installed': time.time()})
        out = self._finish(pid)
        out.update(sha256=sha, verified=verified)
        return out

    def _check_replaceable(self, pid):
        """Refuse to replace a plugin folder the user put there, or one whose jobs or install still run."""
        dest = os.path.join(self.target_dir(), pid)
        if os.path.lexists(dest) and (os.path.islink(dest) or read_provenance(dest) is None):
            raise PluginError('plugin_market_conflict', status=409, id=pid, dir=shown(dest))
        self._check_idle(pid)

    def _check_idle(self, pid):
        if pid in self.manager.envs.installing:
            raise PluginError('plugin_market_in_use', status=409, id=pid)
        rec = self.manager.records.get(pid)
        ctx = rec.context if rec is not None else None
        if ctx is not None and ctx._jobs is not None and any(
                j['state'] in ('queued', 'running') for j in ctx._jobs.list()):
            raise PluginError('plugin_market_in_use', status=409, id=pid)

    def uninstall(self, pid, purge=False):
        """Remove a plugin the market (or a zip upload) installed. The enabled flag is cleared; with purge also
        its settings, data folder and Python environment."""
        if not self.lock.acquire(blocking=False):
            raise PluginError('plugin_market_busy', status=409)
        try:
            target = self.target_dir()
            dest = os.path.join(target, pid) if target and ID_RE.match(pid) else None
            if dest is None or not os.path.isdir(dest) or os.path.islink(dest) or read_provenance(dest) is None:
                raise PluginError('plugin_market_not_installed', status=409, id=pid)
            self._check_idle(pid)
            trash = tempfile.mkdtemp(prefix=f'.{pid}.removing-', dir=target)
            os.rename(dest, os.path.join(trash, pid))
            shutil.rmtree(trash, ignore_errors=True)
            if purge:
                self.manager.envs.remove(pid)
                shutil.rmtree(os.path.join(self.manager.data_dir, pid), ignore_errors=True)
                self.manager.state.forget(pid)
            else:
                self.manager.state.update(pid, enabled=False)
            return self._finish(pid)
        finally:
            self.lock.release()


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
