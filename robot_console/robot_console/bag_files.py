"""Upload, delete and trash rosbag2 bags for the Playback page (step ① "Choose a bag").

Upload: a bag is a folder (metadata.yaml + .db3 / .mcap files, often several GB), so it is not sent as one
multipart form. The page picks the folder in the browser and sends each file as a raw request body:
  POST   /api/playback/uploads?name=&size=&files_count=   begin: checks the name and the free disk space, returns a token
  PUT    /api/playback/uploads/{token}?path=        one file, streamed to <upload_dir>/.uploads/<token>/<path>
  POST   /api/playback/uploads/{token}/finish       metadata.yaml must parse and list only files that arrived;
                                                    then the folder is renamed to <upload_dir>/<name>
  DELETE /api/playback/uploads/{token}              cancel (the page calls it when a file fails)
Only one upload at a time; unfinished upload folders are removed when the console starts.

Free space: inside WSL the Linux disk reports the free space of its virtual disk (about 1 TB), not of the
Windows drive that holds the disk image. playback.space_check lists extra paths whose free space must also
suffice (e.g. /mnt/d when the WSL disk image is on D:).

Delete: the bag folder moves to <root>/.trash/<name>_<time> (hidden folders are not scanned, so it leaves the
list), with <entry>.json beside it recording the original path below <root> so it can be restored.
The trash keeps the disk space until it is emptied:
  GET    /api/playback/trash                     entries of every bag folder's trash + the trash folders
  POST   /api/playback/trash/restore?id=         move one entry back (refused if its old place is taken)
  DELETE /api/playback/trash/item?id=            delete one entry permanently
  DELETE /api/playback/trash                     empty every trash (permanent)
id is the entry's path; only folders directly inside a configured <root>/.trash are accepted. Entries deleted
before the .json records existed cannot be restored from the page (their original path is not known).
"""
import datetime
import json
import os
import re
import secrets
import shutil
import threading
import time

from fastapi import HTTPException, Request

from .playback import find_bags, read_bag

NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
FILE_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')
SUFFIXES = ('.yaml', '.db3', '.mcap')
MAX_FILES = 64
SPACE_MARGIN = 2 * 1024 ** 3         # bytes that must stay free on every checked disk after the upload
CHUNK = 1024 * 1024


class BagFileError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def folder_size(path):
    total = 0
    for d, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


class BagFiles:

    def __init__(self, cfg, playback):
        p = cfg.get('playback') or {}
        self.pb = playback
        roots = playback.roots
        self.upload_dir = os.path.realpath(os.path.expanduser(
            p.get('upload_dir') or os.path.join(roots[0] if roots else '~/robot_console_data/bags', 'uploads')))
        if not any(self.upload_dir == r or self.upload_dir.startswith(r.rstrip('/') + '/') for r in roots):
            roots.append(self.upload_dir)          # uploaded bags must show up in the list
        self.space_check = [os.path.expanduser(x) for x in (p.get('space_check') or [])]
        self.staging = os.path.join(self.upload_dir, '.uploads')
        self._lock = threading.Lock()
        self._session = None                       # {'token', 'name', 'size', 'files', 'received', 'dir', 'started'}
        shutil.rmtree(self.staging, ignore_errors=True)

    # ---------------- upload ----------------

    def free_space(self):
        """[(path, free bytes)] of every disk an upload has to fit on."""
        out = []
        for path in [self.upload_dir, *self.space_check]:
            probe = path
            while probe and not os.path.exists(probe):
                probe = os.path.dirname(probe)
            try:
                out.append((path, shutil.disk_usage(probe or '/').free))
            except OSError:
                pass
        return out

    def begin(self, name, size, files):
        if not NAME_RE.match(name or '') or name.startswith('.'):
            raise BagFileError('bag_bad_name', name=name)
        if not 0 < int(files) <= MAX_FILES:
            raise BagFileError('bag_upload_invalid', reason=f'1 to {MAX_FILES} files')
        size = int(size)
        if size <= 0:
            raise BagFileError('bag_upload_invalid', reason='empty upload')
        target = os.path.join(self.upload_dir, name)
        if os.path.exists(target):
            raise BagFileError('bag_exists', 409, name=name)
        for path, free in self.free_space():
            if free < size + SPACE_MARGIN:
                raise BagFileError('bag_no_space', 507, path=path, free=round(free / 1024 ** 3, 1),
                                   need=round((size + SPACE_MARGIN) / 1024 ** 3, 1))
        with self._lock:
            s = self._session
            if s is not None and (s['active'] or time.monotonic() - s['touched'] < 600):
                raise BagFileError('bag_upload_busy', 409, name=s['name'])
            if s is not None:                      # abandoned for 10 min: drop it
                shutil.rmtree(s['dir'], ignore_errors=True)
            token = secrets.token_hex(8)
            d = os.path.join(self.staging, token)
            os.makedirs(d)
            self._session = {'token': token, 'name': name, 'size': size, 'files': int(files), 'received': {},
                             'dir': d, 'touched': time.monotonic(), 'active': set(), 'cancelled': False}
        return {'token': token, 'target': target}

    def _get(self, token):
        s = self._session
        if s is None or s['token'] != token:
            raise BagFileError('bag_upload_unknown', 404)
        return s

    async def receive(self, token, path, request: Request):
        """Stream one file of the bag to the staging folder."""
        import asyncio
        with self._lock:
            s = self._get(token)
            if not FILE_RE.match(path or '') or path.startswith('.') or not path.lower().endswith(SUFFIXES):
                raise BagFileError('bag_upload_invalid', reason=f'file name {path!r}')
            if path in s['received'] or len(s['received']) >= s['files']:
                raise BagFileError('bag_upload_invalid', reason=f'unexpected file {path!r}')
            s['received'][path] = 0
            s['active'].add(path)
        dest = os.path.join(s['dir'], path)
        count = 0
        try:
            with open(dest, 'xb') as f:
                async for chunk in request.stream():
                    with self._lock:
                        if s['cancelled'] or self._session is not s:
                            raise BagFileError('bag_upload_unknown', 404)
                        if sum(s['received'].values()) + len(chunk) > s['size']:
                            raise BagFileError('bag_upload_invalid', reason='more data than announced')
                        count += len(chunk)
                        s['received'][path] = count
                        s['touched'] = time.monotonic()
                    write = asyncio.create_task(asyncio.to_thread(f.write, chunk))
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        # Let the disk write finish before closing/removing its file.
                        await write
                        raise
            with self._lock:
                if s['cancelled'] or self._session is not s:
                    raise BagFileError('bag_upload_unknown', 404)
                s['active'].discard(path)
                s['touched'] = time.monotonic()
        except BaseException:
            with self._lock:
                s['received'].pop(path, None)
            try:
                os.remove(dest)
            except OSError:
                pass
            with self._lock:
                s['active'].discard(path)
                if s['cancelled'] and not s['active']:
                    shutil.rmtree(s['dir'], ignore_errors=True)
            raise
        return {'path': path, 'bytes': count}

    def finish(self, token):
        with self._lock:
            s = self._get(token)
            if s['active']:
                raise BagFileError('bag_upload_invalid', 409, reason='files are still uploading')
            meta = os.path.join(s['dir'], 'metadata.yaml')
            if not os.path.isfile(meta):
                raise BagFileError('bag_upload_invalid', reason='metadata.yaml is missing')
            bag = read_bag(s['dir'])
            if bag is None:
                raise BagFileError('bag_upload_invalid', reason='metadata.yaml is not a rosbag2 metadata file')
            import yaml
            with open(meta, encoding='utf-8') as f:
                listed = (yaml.safe_load(f) or {})['rosbag2_bagfile_information'].get('relative_file_paths') or []
            if (not isinstance(listed, list) or not listed or
                    any(not isinstance(p, str) or not FILE_RE.fullmatch(p) or p.startswith('.') or
                        not p.lower().endswith(('.db3', '.mcap')) for p in listed)):
                raise BagFileError('bag_upload_invalid', reason='invalid relative_file_paths')
            missing = [p for p in listed if p not in s['received'] or
                       not os.path.isfile(os.path.join(s['dir'], p))]
            if missing:
                raise BagFileError('bag_upload_invalid', reason='missing ' + ', '.join(missing[:3]))
            if len(s['received']) != s['files'] or sum(s['received'].values()) != s['size']:
                raise BagFileError('bag_upload_invalid', reason='file count or total size does not match')
            target = os.path.join(self.upload_dir, s['name'])
            if os.path.exists(target):
                raise BagFileError('bag_exists', 409, name=s['name'])
            os.replace(s['dir'], target)
            self._session = None
        return {'name': s['name'], 'path': target, 'size': folder_size(target)}

    def cancel(self, token):
        with self._lock:
            s = self._get(token)
            s['cancelled'] = True
            if not s['active']:
                shutil.rmtree(s['dir'], ignore_errors=True)
            self._session = None
        return {'cancelled': token}

    # ---------------- delete / trash ----------------

    def _root_of(self, path):
        for r in self.pb.roots:
            r = os.path.realpath(r)
            if path.startswith(r.rstrip('/') + '/'):
                return r
        return None

    def delete(self, path):
        real = os.path.realpath(path or '')
        bag = next((b for b in find_bags(self.pb.roots) if b['path'] == real), None)
        if bag is None:
            raise BagFileError('bag_not_found', 404, path=path)
        root = self._root_of(real)
        if root is None:                           # the bag is a configured folder itself
            raise BagFileError('bag_is_root', 409, path=real)
        st = self.pb.status()
        if st['state'] in ('running', 'stopping') and st.get('bag') == real:
            raise BagFileError('bag_in_use', 409, name=bag['name'])
        trash = os.path.join(root, '.trash')
        os.makedirs(trash, exist_ok=True)
        origin = os.path.relpath(real, root)
        base = os.path.join(trash, origin.replace('/', '_') + time.strftime('_%Y%m%d_%H%M%S'))
        dest, n = base, 1
        while os.path.lexists(dest) or os.path.lexists(dest + '.json'):   # same bag deleted twice in a second
            dest, n = f'{base}_{n}', n + 1
        with open(dest + '.json', 'w', encoding='utf-8') as f:
            json.dump({'origin': origin, 'deleted': datetime.datetime.now(datetime.timezone.utc).isoformat()}, f)
        try:
            os.replace(real, dest)
        except OSError:
            os.remove(dest + '.json')
            raise
        return {'name': bag['name'], 'trash': dest}

    def trash_dirs(self):
        return [os.path.join(os.path.realpath(r), '.trash') for r in self.pb.roots]

    def trash(self):
        """[{root, path, size, items}] of every bag folder's trash that holds something."""
        out = []
        for r, t in zip(self.pb.roots, self.trash_dirs()):
            n = len(self._entries_in(t))
            if n:
                out.append({'root': r, 'path': t, 'size': folder_size(t), 'items': n})
        return out

    @staticmethod
    def _entries_in(t):
        if not os.path.isdir(t):
            return []
        return sorted(e for e in os.listdir(t)
                      if os.path.isdir(os.path.join(t, e)) and not os.path.islink(os.path.join(t, e)))

    @staticmethod
    def _record(path):
        try:
            with open(path + '.json', encoding='utf-8') as f:
                rec = json.load(f)
            return rec if isinstance(rec, dict) else {}
        except (OSError, ValueError):
            return {}

    def trash_entries(self):
        """Every entry of every bag folder's trash, newest first, and the trash folders."""
        out = []
        for t in self.trash_dirs():
            root = os.path.dirname(t)
            for e in self._entries_in(t):
                p = os.path.join(t, e)
                rec = self._record(p)
                origin = rec.get('origin')
                ok = isinstance(origin, str) and self._origin_path(root, origin) is not None
                deleted = rec.get('deleted') if isinstance(rec.get('deleted'), str) else \
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.lstat(p).st_mtime))
                out.append({'id': p, 'name': origin if ok else e, 'folder': t, 'deleted': deleted,
                            'size': folder_size(p), 'restorable': ok})
        out.sort(key=lambda x: x['deleted'], reverse=True)
        return {'entries': out, 'dirs': self.trash_dirs()}

    @staticmethod
    def _origin_path(root, origin):
        """<root>/<origin> if origin is a plain relative path that stays inside root, else None."""
        if not origin or origin.startswith('/') or any(part in ('', '.', '..', '.trash') for part in origin.split('/')):
            return None
        target = os.path.normpath(os.path.join(root, origin))
        return target if target.startswith(root.rstrip('/') + '/') else None

    def _entry(self, entry_id):
        """Validated (trash folder, entry path) for an id from trash_entries()."""
        p = os.path.abspath(entry_id or '')
        t = os.path.dirname(p)
        name = os.path.basename(p)
        if t not in self.trash_dirs() or name.startswith('.') or not os.path.isdir(p) or os.path.islink(p):
            raise BagFileError('bag_trash_not_found', 404, entry=entry_id)
        return t, p

    def restore(self, entry_id):
        t, p = self._entry(entry_id)
        root = os.path.dirname(t)
        origin = self._record(p).get('origin')
        target = self._origin_path(root, origin) if isinstance(origin, str) else None
        if target is None:
            raise BagFileError('bag_trash_not_restorable', 409, entry=os.path.basename(p))
        if os.path.lexists(target):
            raise BagFileError('bag_exists', 409, name=origin)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(p, target)
        try:
            os.remove(p + '.json')
        except OSError:
            pass
        return {'restored': target, 'name': origin}

    def purge(self, entry_id):
        _, p = self._entry(entry_id)
        size = folder_size(p)
        shutil.rmtree(p)
        try:
            os.remove(p + '.json')
        except OSError:
            pass
        return {'freed': size}

    def empty_trash(self):
        freed = 0
        for t in self.trash_dirs():
            if not os.path.isdir(t):
                continue
            freed += folder_size(t)
            for item in os.listdir(t):
                p = os.path.join(t, item)
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
        return {'freed': freed}


def register_bag_file_routes(app, cfg, pb):
    files = BagFiles(cfg, pb)

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    def guard(fn, *args):
        try:
            return fn(*args)
        except BagFileError as e:
            raise fail(e)
        except OSError as e:
            raise HTTPException(500, detail={'code': 'bag_file_failed', 'params': {'reason': str(e)}})

    @app.get('/api/playback/storage')
    def playback_storage():
        return {'upload_dir': files.upload_dir, 'trash': files.trash(),
                'free': [{'path': p, 'free': f} for p, f in files.free_space()]}

    @app.post('/api/playback/uploads')
    def upload_begin(name: str, size: int, files_count: int = 1):
        return guard(files.begin, name, size, files_count)

    @app.put('/api/playback/uploads/{token}')
    async def upload_file(token: str, path: str, request: Request):
        try:
            return await files.receive(token, path, request)
        except BagFileError as e:
            raise fail(e)
        except OSError as e:
            raise HTTPException(500, detail={'code': 'bag_file_failed', 'params': {'reason': str(e)}})

    @app.post('/api/playback/uploads/{token}/finish')
    def upload_finish(token: str):
        return guard(files.finish, token)

    @app.delete('/api/playback/uploads/{token}')
    def upload_cancel(token: str):
        return guard(files.cancel, token)

    @app.delete('/api/playback/bags')
    def delete_bag(path: str):
        return guard(files.delete, path)

    @app.get('/api/playback/trash')
    def bag_trash():
        return guard(files.trash_entries)

    @app.post('/api/playback/trash/restore')
    def bag_trash_restore(id: str):
        return guard(files.restore, id)

    @app.delete('/api/playback/trash/item')
    def bag_trash_purge(id: str):
        return guard(files.purge, id)

    @app.delete('/api/playback/trash')
    def empty_trash():
        return guard(files.empty_trash)

    return files
