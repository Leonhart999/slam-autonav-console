"""Folder-map trash routes; all filesystem operations stay below pinned directory FDs."""
import ctypes
import errno
import json
import os
import re
import shlex
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from .map_library import NAME_RE

_ID_RE = re.compile(r'[0-9a-f]{32}')
_MANIFEST_LIMIT = 4096
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class ApiError(HTTPException):
    """Use the application's translated generic map-operation error."""

    def __init__(self, reason, status=400):
        self.code = 'map_operation_failed'
        self.params = {'reason': reason}
        super().__init__(status, detail={'code': self.code, 'params': self.params})


def _fail(reason, status=400):
    raise ApiError(reason, status)


def _stat(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


@contextmanager
def _directory(parent, name):
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _scan(fd, depth=0):
    """Reject links and special files without opening them; return regular-file bytes."""
    if depth > 128:
        _fail('Map directory nesting exceeds the safety limit')
    size = 0
    for name in os.listdir(fd):
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            with _directory(fd, name) as child:
                size += _scan(child, depth + 1)
        elif stat.S_ISREG(entry.st_mode):
            size += entry.st_size
        else:
            _fail('Symbolic links and special files are not allowed in map trash')
    return size


def _remove(fd):
    """Unlink through directory FDs, never following a substituted symbolic link."""
    for name in os.listdir(fd):
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            with _directory(fd, name) as child:
                _remove(child)
            os.rmdir(name, dir_fd=fd)
        elif stat.S_ISREG(entry.st_mode):
            os.unlink(name, dir_fd=fd)
        else:
            _fail('Symbolic links and special files are not allowed in map trash')


def _rename(source_fd, source, target_fd, target):
    """Linux atomic, no-replace rename: never overwrite even an empty destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        _fail('Atomic no-replace rename is unavailable on this system')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            _fail('The destination already exists; nothing was overwritten', 409)
        raise OSError(code, os.strerror(code))


class _MapTrash:
    def __init__(self, catalog, library, procs, cfg):
        # Catalog roots are already expanded/resolved. Do not resolve untrusted children.
        self.root = Path(os.path.abspath(os.path.expanduser(str(catalog.root))))
        self.library = library
        self.procs = procs
        self.nav_cfg = cfg.get('navigation') or {}
        self.nav_name = self.nav_cfg.get('stack', 'navigation')
        self.lock = threading.RLock()

    @contextmanager
    def root_fd(self):
        # Walk every component with O_NOFOLLOW, including parents of the configured root.
        fd = os.open('/', _DIR_FLAGS)
        try:
            for part in self.root.parts[1:]:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def trash_fd(self, root, create=False):
        if create:
            try:
                os.mkdir('.trash', mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
        with _directory(root, '.trash') as fd:
            yield fd

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            _fail('Invalid map name')

    @staticmethod
    def validate_id(entry_id):
        if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
            _fail('Invalid trash identifier')

    def check_busy(self, name):
        job = self.library.status()
        if job.get('state') == 'running' and job.get('name') == name:
            _fail('This map is currently being saved', 409)
        stack = self.procs.get(self.nav_name)
        if stack is None or stack.state() not in ('running', 'stopping'):
            return
        cmd = stack.run_cmd or stack.cmd
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        values = [arg.split(':=', 1)[1] for arg in cmd
                  if isinstance(arg, str) and arg.startswith(('map:=', 'map_yaml:=',
                                                            'yaml_filename:='))]
        # Match the launch file's default even when started from the Monitor page.
        if not values:
            values = [self.nav_cfg['default_map']] if self.nav_cfg.get('default_map') else []
        for value in values:
            path = Path(os.path.expanduser(value.strip('\"\'')))
            active = path.parent.name if path.name == 'map.yaml' else path.stem
            if active == name:
                _fail('This map is used by running or stopping navigation', 409)

    def read_entry(self, trash, entry_id):
        self.validate_id(entry_id)
        if _stat(trash, entry_id) is None:
            _fail('Trash entry was not found', 404)
        with _directory(trash, entry_id) as entry:
            if set(os.listdir(entry)) != {'manifest.json', 'map'}:
                _fail('Trash entry is incomplete or contains unexpected files')
            fd = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=entry)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > _MANIFEST_LIMIT:
                    _fail('Trash manifest is not a bounded regular file')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    raw = stream.read(_MANIFEST_LIMIT + 1)
                if len(raw) > _MANIFEST_LIMIT:
                    _fail('Trash manifest exceeds the safety limit')
                data = json.loads(raw)
            finally:
                os.close(fd)
            if not isinstance(data, dict) or data.get('version') != 1 or data.get('id') != entry_id:
                _fail('Invalid trash manifest')
            self.validate_name(data.get('name'))
            deleted = data.get('deleted')
            if not isinstance(deleted, str) or len(deleted) > 64:
                _fail('Invalid deletion timestamp in trash manifest')
            datetime.fromisoformat(deleted)
            size = data.get('size')
            if type(size) is not int or size < 0:
                _fail('Invalid map size in trash manifest')
            with _directory(entry, 'map') as payload:
                actual_size = _scan(payload)
            return {'id': entry_id, 'name': data['name'], 'deleted': deleted,
                    'size': actual_size}

    def entries(self, trash):
        result = [self.read_entry(trash, entry_id) for entry_id in os.listdir(trash)]
        return sorted(result, key=lambda row: row['deleted'], reverse=True)

    def list(self):
        with self.lock:
            if not self.root.exists():
                return []
            with self.root_fd() as root:
                if _stat(root, '.trash') is None:
                    return []
                with self.trash_fd(root) as trash:
                    return self.entries(trash)

    def move(self, name):
        self.validate_name(name)
        with self.lock, self.root_fd() as root:
            self.check_busy(name)
            source = _stat(root, name)
            if source is None:
                if _stat(root, name + '.yaml') is not None:
                    _fail('Legacy flat maps cannot be deleted because image ownership may be shared')
                _fail('Map was not found', 404)
            if not stat.S_ISDIR(source.st_mode):
                _fail('Only real folder maps can be moved to trash; symbolic links are forbidden')
            with _directory(root, name) as folder:
                size = _scan(folder)
                if not any(_stat(folder, marker) is not None for marker in
                           ('map.yaml', 'meta.json', 'map.pcd', 'map_voxel.pcd')):
                    _fail('The directory is not a catalog folder map')
            entry_id = uuid.uuid4().hex
            record = {'id': entry_id, 'name': name,
                      'deleted': datetime.now(timezone.utc).isoformat(), 'size': size}
            with self.trash_fd(root, create=True) as trash:
                os.mkdir(entry_id, mode=0o700, dir_fd=trash)
                try:
                    with _directory(trash, entry_id) as entry:
                        fd = os.open('manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     os.O_NOFOLLOW, 0o600, dir_fd=entry)
                        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                            json.dump({'version': 1, **record}, stream, ensure_ascii=True)
                            stream.flush()
                            os.fsync(stream.fileno())
                        self.check_busy(name)
                        _rename(root, name, entry, 'map')
                except Exception:
                    # Never clean up a payload if the move has already succeeded.
                    with _directory(trash, entry_id) as entry:
                        if _stat(entry, 'map') is None:
                            _remove(entry)
                            os.rmdir(entry_id, dir_fd=trash)
                    raise
            return {'ok': True, **record}

    def restore(self, entry_id):
        self.validate_id(entry_id)
        with self.lock, self.root_fd() as root, self.trash_fd(root) as trash:
            record = self.read_entry(trash, entry_id)
            name = record['name']
            self.check_busy(name)
            if any(_stat(root, candidate) is not None for candidate in (name, name + '.yaml')):
                _fail('A folder or legacy flat map with this name already exists', 409)
            with _directory(trash, entry_id) as entry:
                _rename(entry, 'map', root, name)
                os.unlink('manifest.json', dir_fd=entry)
            os.rmdir(entry_id, dir_fd=trash)
            return {'ok': True, **record}

    def delete(self, entry_id=None):
        if entry_id is not None:
            self.validate_id(entry_id)
        with self.lock, self.root_fd() as root:
            if _stat(root, '.trash') is None:
                if entry_id is not None:
                    _fail('Trash entry was not found', 404)
                return {'ok': True, 'deleted': 0}
            with self.trash_fd(root) as trash:
                records = ([self.read_entry(trash, entry_id)] if entry_id is not None
                           else self.entries(trash))
                # Validate all entries before beginning a bulk permanent deletion.
                for record in records:
                    self.check_busy(record['name'])
                for record in records:
                    with _directory(trash, record['id']) as entry:
                        _remove(entry)
                    os.rmdir(record['id'], dir_fd=trash)
                return {'ok': True, 'deleted': len(records)}


def register_trash_routes(app, catalog, library, procs, cfg):
    """Trash lives under /api/map-trash so no map name (e.g. 'trash') can collide with it."""
    backend = _MapTrash(catalog, library, procs, cfg)

    def action(method, *args):
        try:
            return method(*args)
        except ApiError:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ApiError(f'Map operation could not be completed: {exc}') from exc

    @app.get('/api/map-trash')
    def list_trash():
        return action(backend.list)

    @app.delete('/api/map-trash')
    def empty_trash():
        return action(backend.delete)

    @app.post('/api/map-trash/{id}/restore')
    def restore_map(id: str):
        return action(backend.restore, id)

    @app.delete('/api/map-trash/{id}')
    def delete_trashed_map(id: str):
        return action(backend.delete, id)

    @app.delete('/api/maps/{name}')
    def trash_map(name: str):
        return action(backend.move, name)

    return backend
