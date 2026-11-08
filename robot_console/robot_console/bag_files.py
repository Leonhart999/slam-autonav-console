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
list). The trash keeps the disk space until it is emptied (DELETE /api/playback/trash, permanent).
"""
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
            if s is not None and time.time() - s['touched'] < 600:
                raise BagFileError('bag_upload_busy', 409, name=s['name'])
            if s is not None:                      # abandoned for 10 min: drop it
                shutil.rmtree(s['dir'], ignore_errors=True)
            token = secrets.token_hex(8)
            d = os.path.join(self.staging, token)
            os.makedirs(d)
            self._session = {'token': token, 'name': name, 'size': size, 'files': int(files), 'received': {},
                             'dir': d, 'touched': time.time()}
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
        dest = os.path.join(s['dir'], path)
        count = 0
        limit = s['size'] - sum(s['received'].values())
        try:
            with open(dest, 'xb') as f:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > limit:
                        raise BagFileError('bag_upload_invalid', reason='more data than announced')
                    await asyncio.to_thread(f.write, chunk)
        except BaseException:
            with self._lock:
                s['received'].pop(path, None)
            try:
                os.remove(dest)
            except OSError:
                pass
            raise
        with self._lock:
            s['received'][path] = count
            s['touched'] = time.time()
        return {'path': path, 'bytes': count}

    def finish(self, token):
        with self._lock:
            s = self._get(token)
            meta = os.path.join(s['dir'], 'metadata.yaml')
            if not os.path.isfile(meta):
                raise BagFileError('bag_upload_invalid', reason='metadata.yaml is missing')
            bag = read_bag(s['dir'])
            if bag is None:
                raise BagFileError('bag_upload_invalid', reason='metadata.yaml is not a rosbag2 metadata file')
            import yaml
            with open(meta, encoding='utf-8') as f:
                listed = (yaml.safe_load(f) or {})['rosbag2_bagfile_information'].get('relative_file_paths') or []
            missing = [p for p in listed if not os.path.isfile(os.path.join(s['dir'], p))]
            if missing:
                raise BagFileError('bag_upload_invalid', reason='missing ' + ', '.join(missing[:3]))
            target = os.path.join(self.upload_dir, s['name'])
            if os.path.exists(target):
                raise BagFileError('bag_exists', 409, name=s['name'])
            os.replace(s['dir'], target)
            self._session = None
        return {'name': s['name'], 'path': target, 'size': folder_size(target)}

    def cancel(self, token):
        with self._lock:
            s = self._get(token)
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
        dest = os.path.join(trash, os.path.relpath(real, root).replace('/', '_') + time.strftime('_%Y%m%d_%H%M%S'))
        os.replace(real, dest)
        return {'name': bag['name'], 'trash': dest}

    def trash(self):
        """[{root, path, size, items}] of every bag folder's trash that holds something."""
        out = []
        for r in self.pb.roots:
            t = os.path.join(os.path.realpath(r), '.trash')
            if os.path.isdir(t) and os.listdir(t):
                out.append({'root': r, 'path': t, 'size': folder_size(t), 'items': len(os.listdir(t))})
        return out

    def empty_trash(self):
        freed = 0
        for t in self.trash():
            freed += t['size']
            for item in os.listdir(t['path']):
                p = os.path.join(t['path'], item)
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

    @app.delete('/api/playback/trash')
    def empty_trash():
        return guard(files.empty_trash)

    return files
