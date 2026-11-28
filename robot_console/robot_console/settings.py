"""Edit the raw console configuration. Running services keep their startup configuration."""
import base64
import contextlib
import http.client
import io
import json
import os
import platform
import re
import shutil
import stat
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from . import open_folder
from .config import bilingual, merge
from .plugin_market import DEFAULT_INDEX_URLS
from .plugins import shown
from .version import REPO, VERSION

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError as RoundTripError
    from ruamel.yaml.resolver import VersionedResolver
except ImportError:
    YAML = None
    RoundTripError = yaml.YAMLError
else:
    class ConsoleResolver(VersionedResolver):
        @property
        def versioned_resolver(self):
            return yaml.SafeLoader.yaml_implicit_resolvers


HEADER = '# Written by the console settings page. Merged onto config/console.yaml.\n'
MISSING = object()
IMPORT_LIMIT = 256 * 1024
# Folders: checked at save time so a restart cannot fail on them (exists as a file, or not creatable)
DIR_KEYS = ('paths.data_root', 'mapping.maps_dir', 'mapping.runs_dir', 'evaluation.results_dir', 'server.log_dir',
            'playback.bag_dirs')
# Machine-specific folders are not taken from an imported file: data_root also decides which plugin folders and
# plugin state the console trusts, so a shared settings file must not be able to point it elsewhere
IMPORT_SKIP = DIR_KEYS
TOPIC = re.compile(r'(/[A-Za-z_][A-Za-z0-9_]*)+')
GITHUB = 'https://api.github.com'
PYPI = 'https://pypi.org/simple/'


def error(status, code, **params):
    return HTTPException(status, detail={'code': code, 'params': params})


def invalid(key, reason, **limits):
    return error(422, 'setting_invalid', key=key, reason=reason, **limits)


def number(low, high, integer=False):
    def check(key, value):
        if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
            raise invalid(key, 'type')
        if not low <= value <= high:
            raise invalid(key, 'range', min=low, max=high)
    return check


def string(key, value):
    if not isinstance(value, str):
        raise invalid(key, 'type')


def path_value(key, value):
    string(key, value)
    if (not value.startswith(('/', '~', '${data_root}')) or len(value) > 512
            or any(c in value for c in '\0\r\n')
            or (key == 'paths.data_root' and '${data_root}' in value)
            or (key == 'navigation.default_map' and not value.endswith('.yaml'))):
        raise invalid(key, 'format')


def topic(key, value):
    string(key, value)
    if not TOPIC.fullmatch(value) and not (key == 'mapping.gt_topic' and value == ''):
        raise invalid(key, 'format')


def location(key, value):
    string(key, value)
    market = key in ('plugins.market.index_urls', 'market_index_url')
    if value == '' and not market:
        return
    if any(c in value for c in '\0\r\n'):
        raise invalid(key, 'format')
    if key in ('pip_index_url', 'plugins.pip.index_url'):
        if not value.startswith(('http://', 'https://')):
            raise invalid(key, 'format')
        return
    if market and os.path.isabs(value):
        return
    schemes = ('https://',) if market else ('http://', 'https://')
    if key in ('plugins.pip.proxy', 'pip_proxy'):   # pip (with PySocks) also takes SOCKS; the market does not
        schemes += ('socks5://', 'socks5h://')
    try:
        parts = urlsplit(value)
        if market and value.startswith('file://') and parts.path:
            return
        if (not value.startswith(schemes) or not parts.hostname or parts.port == 0
                or any(c.isspace() for c in value)):
            raise ValueError
    except ValueError:
        raise invalid(key, 'format') from None


def sequence(check_item, low, high, *, unique=True, ordered=False):
    def check(key, value):
        if not isinstance(value, list):
            raise invalid(key, 'type')
        if not low <= len(value) <= high:
            raise invalid(key, 'count', min=low, max=high)
        for item in value:
            check_item(key, item)
        if ordered and value[0] >= value[1]:
            raise invalid(key, 'order')
        if unique and len(set(value)) != len(value):
            raise invalid(key, 'duplicate')
    return check


FIELDS = {
    'paths.data_root': path_value,
    'mapping.maps_dir': path_value,
    'mapping.runs_dir': path_value,
    'evaluation.results_dir': path_value,
    'server.log_dir': path_value,
    'playback.bag_dirs': sequence(path_value, 1, 8),
    'evaluation.rpe_delta': number(0.01, 100),
    'evaluation.max_diff': number(0.0001, 1),
    'mapping.gt_topic': topic,
    'batch.slam_stacks': sequence(string, 1, 16),
    'batch.run_timeout': number(30, 86400, integer=True),
    'batch.ready_timeout': number(5, 3600, integer=True),
    'batch.max_runs': number(1, 1000, integer=True),
    'batch.speeds': sequence(number(0.1, 100), 1, 8),
    'navigation.default_map': path_value,
    'navigation.goal_timeout': number(1, 86400),
    'navigation.speed_scale_range': sequence(number(0.1, 20), 2, 2, ordered=True),
    'navigation.max_w_cap': number(0.1, 10),
    'playback.rates': sequence(number(0.05, 64), 1, 10),
    'teleop.max_v': number(0.05, 20),
    'teleop.recommended_v': sequence(number(0, 20), 2, 2, ordered=True),
    'teleop.max_w': number(0.05, 10),
    'teleop.timeout': number(0.1, 10),
    'teleop.topic': topic,
    'estop.zero_duration': number(0.2, 10),     # > 0: an E-stop always holds zero velocity for a while
    'server.port': number(1024, 65535, integer=True),
    'server.status_rate': number(0.2, 20),
    'plugins.pip.proxy': location,
    'plugins.pip.index_url': location,
    'plugins.market.proxy': location,
    'plugins.market.index_urls': sequence(location, 1, 8, unique=False),
}


def lookup(tree, key, default=None):
    for part in key.split('.'):
        if not isinstance(tree, dict) or part not in tree:
            return default
        tree = tree[part]
    return tree


def assign(tree, key, value):
    """Copy each edited branch so a YAML alias elsewhere keeps its original contents."""
    head, dot, tail = key.partition('.')
    if dot:
        child = tree.get(head)
        if not isinstance(child, dict):
            if value is MISSING:
                return
            if head in tree and child is not None:
                raise error(500, 'settings_file_error', reason='write')
            child = {}
        # CommentedMap.copy() carries YAML comments and formatting onto the detached branch.
        branch = child.copy()
        assign(branch, tail, value)
        value = branch if branch else MISSING
    if value is MISSING:
        # A YAML << merge would reintroduce a deleted key on the next load.
        # Materialize the siblings in this branch and leave the anchor's source intact.
        if getattr(tree, '_yaml_merge', None):
            siblings = list(tree.items())
            del tree._yaml_merge
            tree.update(siblings)
        tree.pop(head, None)
    else:
        tree[head] = value


def same_override(left, right):
    """Preserve the supplied numeric type, including inside lists."""
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_override(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def raw_fields(tree):
    return {key: lookup(tree, key) for key in FIELDS}


def read_text(path):
    if path is None:
        return ''
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''
    except (OSError, UnicodeError):
        raise error(500, 'settings_file_error', reason='read') from None


def mapping_text(text, lenient=False):
    """Parse a YAML mapping. Empty and `null` documents are an empty mapping. lenient: like config.load
    (`safe_load(...) or {}`), any falsy document ([], '', 0, false) is empty too - used for the override file
    itself, so a file the console already starts with never stops it from starting; imports stay strict."""
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        if node is None or node.tag == 'tag:yaml.org,2002:null':
            return {}
        if not lenient and not isinstance(node, yaml.MappingNode):
            raise ValueError('mapping')
        result = loader.construct_document(node)
        if lenient and not result:
            return {}
        if not isinstance(result, dict):
            raise ValueError('mapping')
        return result
    finally:
        loader.dispose()


def read_mapping(text):
    try:
        return mapping_text(text, lenient=True)
    except (ValueError, yaml.YAMLError, RecursionError, TypeError, OverflowError):
        raise error(500, 'settings_file_error', reason='read') from None


def yaml_text(document):
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def render(original, changes):
    """Match config.load's YAML 1.1 scalar rules when round-tripping comments."""
    if YAML is None:
        document = read_mapping(original)
        for key, value in changes.items():
            assign(document, key, value)
        return HEADER + yaml_text(document) if document else HEADER
    codec = YAML(typ='rt')
    codec.Resolver = ConsoleResolver
    codec.version = (1, 1)
    codec.preserve_quotes = True
    document = codec.load(original)
    preamble = ''
    if document is None:
        document = {}
        # keep a comment-only file's comments; a `null` document becomes an empty mapping
        comments = ''.join(line for line in original.splitlines(True) if line.lstrip().startswith('#'))
        preamble = comments.rstrip() + '\n' if comments.strip() else HEADER
    for key, value in changes.items():
        assign(document, key, value)
    if not document:
        return HEADER
    stream = io.StringIO()
    codec.dump(document, stream)
    result = stream.getvalue()
    if not original.lstrip().startswith('%YAML'):
        result = result.removeprefix('%YAML 1.1\n---\n')
    return preamble + result


def backup(path):
    pattern = re.compile(re.escape(path.name) + r'\.bak_(\d{8}_\d{6})$')
    backups = sorted(p for p in path.parent.iterdir() if pattern.fullmatch(p.name) and p.is_file())
    stamp = datetime.now().replace(microsecond=0)
    if backups:
        # Multiple saves can land in one second. Keep each revision under a distinct, sortable name.
        latest = datetime.strptime(backups[-1].name[-15:], '%Y%m%d_%H%M%S')
        stamp = max(stamp, latest + timedelta(seconds=1))
    dest = path.with_name(path.name + stamp.strftime('.bak_%Y%m%d_%H%M%S'))
    # owner-only from the first byte: backups may hold proxy credentials
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as out, open(path, 'rb') as src:
        shutil.copyfileobj(src, out)
    shutil.copystat(path, dest)
    os.chmod(dest, 0o600)
    for old in backups[:-4]:
        old.unlink()


def write_config(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    mode = stat.S_IMODE(path.stat().st_mode) if exists else 0o600   # new file: may hold proxy credentials
    temp = tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', newline='\n',
                                       delete=False)
    try:
        with temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temp.name, mode)
        if exists:
            backup(path)
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


def checked_render(original, changes, expected):
    """Render, then read the result back the way config.load does: a round trip that changes any value (YAML
    version directives, ruamel quirks) falls back to a plain PyYAML rewrite, which drops comments but keeps values."""
    try:
        text = render(original, changes)
        if read_mapping(text) == expected:
            return text
    except HTTPException:          # the round trip produced something the console could not read back
        pass
    text = HEADER + yaml_text(expected) if expected else HEADER
    if read_mapping(text) != expected:
        raise error(500, 'settings_file_error', reason='write')
    return text


@contextlib.contextmanager
def file_lock(path):
    """Serialize read-modify-write of the override file between console instances sharing it (Linux): an
    exclusive flock on the folder that holds it, so no lock file is left next to the user's configuration."""
    try:
        import fcntl
    except ImportError:            # not on Linux: the per-instance lock still applies
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)               # closing the descriptor releases the lock


def expand_raw(value, data_root):
    value = str(value).replace('${data_root}', os.path.expanduser(str(data_root or '~/robot_console_data')))
    return os.path.expanduser(value)


def check_dir(key, raw, data_root):
    """A folder the console creates at start: it must not exist as a file and must be creatable."""
    path = expand_raw(raw, data_root)
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise invalid(key, 'not_dir')
        if not os.access(path, os.W_OK | os.X_OK):
            raise invalid(key, 'not_writable')
        return
    parent = os.path.dirname(os.path.abspath(path))
    while parent and not os.path.exists(parent):
        parent = os.path.dirname(parent)
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK | os.X_OK):
        raise invalid(key, 'not_writable')


def port_busy(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host or '127.0.0.1', port))
        except OSError:
            return True
    return False


class Settings:
    def __init__(self, cfg):
        self.cfg = cfg
        self.target = Path(cfg['user_config_target']) if cfg.get('user_config_target') else None
        self.shipped = Path(cfg['config_files'][0])
        self.lock = threading.Lock()
        self.running = raw_fields(merge(self.defaults(), read_mapping(read_text(self.target))))

    def defaults(self):
        try:
            return read_mapping(self.shipped.read_text(encoding='utf-8'))
        except (OSError, UnicodeError):
            raise error(500, 'settings_file_error', reason='read') from None

    def payload(self, defaults, user):
        effective = merge(defaults, user)
        values = raw_fields(effective)
        profiles = lookup(effective, 'mapping.slam_profiles') or {}
        stacks = self.cfg.get('stacks', {})
        options = [{'name': name, 'label': bilingual(stacks.get(name, {}).get('label'), name)}
                   for name in profiles]
        return {
            'user_file': shown(str(self.target)) if self.target else None,
            'writable': self.target is not None,
            'reason': None if self.target else 'no_user_config',
            'fields': {key: {'value': value, 'default': lookup(defaults, key),
                             'overridden': lookup(user, key, MISSING) is not MISSING}
                       for key, value in values.items()},
            'options': {'batch.slam_stacks': options},
            'read_only': {'server.host': lookup(effective, 'server.host', '127.0.0.1')},
            'pending_restart': values != self.running,
        }

    def require_target(self):
        if self.target is None:
            raise error(409, 'settings_read_only')

    def apply(self, updates, resets):
        self.require_target()
        if not isinstance(updates, dict):
            raise invalid('set', 'type')
        if not isinstance(resets, list) or not all(isinstance(key, str) for key in resets):
            raise invalid('reset', 'type')
        for key in [*updates, *resets]:
            if key not in FIELDS:
                raise error(422, 'setting_unknown', key=key)
        with self.lock, file_lock(self.target):
            defaults = self.defaults()
            original = read_text(self.target)
            user = read_mapping(original)
            profiles = lookup(merge(defaults, user), 'mapping.slam_profiles') or {}
            desired = {}
            for key, value in updates.items():
                FIELDS[key](key, value)
                if key == 'batch.slam_stacks' and any(name not in profiles for name in value):
                    raise invalid(key, 'option')
                desired[key] = MISSING if value == lookup(defaults, key, MISSING) else value
            desired.update(dict.fromkeys(resets, MISSING))
            changes = {k: v for k, v in desired.items() if not same_override(lookup(user, k, MISSING), v)}
            for key, value in changes.items():
                assign(user, key, value)
            effective = merge(defaults, user)
            # Folders and port as the next start will see them (explicit values, resets and folders derived from
            # ${data_root} alike): whatever differs from the running console must work, or the restart fails
            data_root = lookup(effective, 'paths.data_root')
            root_moved = data_root != self.running.get('paths.data_root')
            for key in DIR_KEYS:
                raw = lookup(effective, key)
                if raw is None or (raw == self.running.get(key) and not root_moved):
                    continue
                for item in (raw if isinstance(raw, list) else [raw]):
                    check_dir(key, item, data_root)
            port = lookup(effective, 'server.port')
            running_port = self.cfg['server'].get('port')
            if isinstance(port, int) and port != running_port and port_busy(self.cfg['server'].get('host'), port):
                raise invalid('server.port', 'busy')
            if {'teleop.recommended_v', 'teleop.max_v'} & desired.keys():
                pair = lookup(effective, 'teleop.recommended_v', MISSING)
                limit = lookup(effective, 'teleop.max_v', 0.6)
                if pair is not MISSING:
                    FIELDS['teleop.max_v']('teleop.max_v', limit)
                    FIELDS['teleop.recommended_v']('teleop.recommended_v', pair)
                    if pair[1] > limit:
                        raise invalid('teleop.recommended_v', 'range', min=0, max=limit)
            if changes or (not user and original != HEADER):
                try:
                    write_config(self.target, checked_render(original, changes, user))
                except (OSError, ValueError, TypeError, OverflowError, RoundTripError, yaml.YAMLError):
                    raise error(500, 'settings_file_error', reason='write') from None
            result = self.payload(defaults, user)
            result.update(changed=list(changes), restart_required=result['pending_restart'])
            return result


def import_values(text):
    if not isinstance(text, str):
        raise error(422, 'settings_import_invalid', reason='type')
    try:
        if len(text.encode('utf-8')) > IMPORT_LIMIT:
            raise error(422, 'settings_import_invalid', reason='size')
        document = mapping_text(text)
    except (yaml.YAMLError, UnicodeError, RecursionError, OverflowError, TypeError):
        raise error(422, 'settings_import_invalid', reason='yaml') from None
    except ValueError as exc:
        reason = 'mapping' if exc.args == ('mapping',) else 'yaml'
        raise error(422, 'settings_import_invalid', reason=reason) from None

    # Report an unknown subtree once. Traversal is bounded by the depth of the whitelist,
    # even if the input uses recursive aliases or has a very large unrelated subtree.
    updates, ignored = {}, []

    def visit(tree, prefix):
        for name, value in tree.items():
            key = prefix + str(name)
            if not isinstance(name, str) or '.' in name:
                ignored.append(key)
            elif key in IMPORT_SKIP:
                ignored.append(key)
            elif key in FIELDS:
                updates[key] = value
            elif isinstance(value, dict) and any(k.startswith(key + '.') for k in FIELDS):
                visit(value, key + '.')
            else:
                ignored.append(key)

    visit(document, '')
    return updates, ignored


def folder_info(key, path):
    base = {'key': key, 'path': shown(path), 'exists': False, 'bytes': 0, 'items': 0, 'partial': False}
    deadline = time.monotonic() + 2
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return base
    except OSError:
        return dict(base, partial=True)
    if not stat.S_ISDIR(mode):
        return base
    base['exists'] = True
    pending = [path]
    while pending:
        if time.monotonic() >= deadline:
            return dict(base, partial=True)
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        return dict(base, partial=True)
                    if directory == path and not entry.name.startswith('.'):
                        base['items'] += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            base['bytes'] += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        base['partial'] = True
        except OSError:
            base['partial'] = True
    return base


def disk_info(paths):
    disks, devices = [], set()
    for path in paths:
        try:
            device = os.stat(path).st_dev
            if device not in devices:
                usage = shutil.disk_usage(path)
                disks.append(dict(path=shown(path), total=usage.total, used=usage.used, free=usage.free))
                devices.add(device)
        except OSError:
            continue
    return disks


def storage(cfg, pool):
    root = cfg['paths']['data_root']
    folders = [('data_root', root), ('maps', cfg['mapping']['maps_dir']), ('runs', cfg['mapping']['runs_dir']),
               *(('bags', path) for path in cfg['playback']['bag_dirs']),
               ('results', cfg['evaluation']['results_dir']), ('logs', cfg['server']['log_dir'])]
    disks = pool.submit(disk_info, [root, *cfg['playback'].get('space_check', [])])
    walks = [pool.submit(folder_info, key, path) for key, path in folders]
    done, pending = wait([disks, *walks], timeout=5.5)
    for job in pending:
        job.cancel()
    # A blocked filesystem call can outlive the scan budget. Do not join its worker here.
    results = [job.result() if job in done else dict(key=key, path=shown(path), exists=False,
                                                     items=0, bytes=0, partial=True)
               for job, (key, path) in zip(walks, folders)]
    return {'folders': results, 'disks': disks.result() if disks in done else []}


def about(cfg, plugins):
    try:
        entries = plugins.list()
        if isinstance(entries, dict):
            entries = entries['plugins']
        enabled, total = sum(p.get('enabled') is True for p in entries), len(entries)
    except Exception:
        enabled = total = None
    try:
        os_name = platform.freedesktop_os_release()['PRETTY_NAME']
    except (OSError, KeyError):
        os_name = platform.system()
    evo = cfg['evaluation']['evo_python']
    target = cfg.get('user_config_target')
    return {
        'version': VERSION, 'repo': REPO, 'ros_distro': os.getenv('ROS_DISTRO', ''),
        'rmw': os.getenv('RMW_IMPLEMENTATION') or 'rmw_fastrtps_cpp',
        'domain_id': os.getenv('ROS_DOMAIN_ID') or '0', 'python': platform.python_version(),
        'os': os_name, 'wsl': open_folder.is_wsl(), 'kernel': platform.release(),
        'venv': shown(cfg['server']['venv']), 'evo_python': shown(evo),
        'evo_ok': os.path.isfile(evo) and os.access(evo, os.X_OK),
        'plugins_enabled': enabled, 'plugins_total': total,
        'config_files': [shown(str(p)) for p in cfg['config_files']],
        'user_file': shown(str(target)) if target else None, 'host': cfg['server']['host'],
        'port': cfg['server']['port'], 'data_root': shown(cfg['paths']['data_root']),
    }


def socks_socket(proxy, host, port, timeout):
    """Open a SOCKS5 stream. With socks5h the proxy resolves the destination name."""
    sock = socket.create_connection((proxy.hostname, proxy.port or 1080), timeout)
    try:
        with sock.makefile('rb', buffering=0) as stream:
            def read(n):
                result = bytearray()
                while len(result) < n:
                    chunk = stream.read(n - len(result))
                    if not chunk:
                        raise OSError('Proxy disconnected')
                    result.extend(chunk)
                return bytes(result)

            method = b'\x02' if proxy.username is not None else b'\x00'
            sock.sendall(b'\x05\x01' + method)
            if read(2) != b'\x05' + method:
                raise OSError('Proxy negotiation failed')
            if method == b'\x02':
                user, password = (unquote(s or '').encode() for s in (proxy.username, proxy.password))
                sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
                if read(2) != b'\x01\x00':
                    raise OSError('Proxy authentication failed')
            if proxy.scheme == 'socks5h':
                name = host.encode('idna')
                address = b'\x03' + bytes([len(name)]) + name
            else:
                family, _, _, _, addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
                address = (b'\x01' if family == socket.AF_INET else b'\x04') + socket.inet_pton(family, addr[0])
            sock.sendall(b'\x05\x01\x00' + address + port.to_bytes(2, 'big'))
            reply = read(4)
            if reply[:3] != b'\x05\x00\x00':
                raise OSError('Proxy connection failed')
            size = read(1)[0] if reply[3] == 3 else {1: 4, 4: 16}.get(reply[3])
            if size is None:
                raise OSError('Invalid proxy reply')
            read(size + 2)
        return sock
    except BaseException:
        sock.close()
        raise


class TunnelTLS(io.RawIOBase):
    """HTTP client's socket interface for TLS to an origin inside TLS to an HTTPS proxy."""
    def __init__(self, transport, host):
        self.transport = transport
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = ssl.create_default_context().wrap_bio(self.incoming, self.outgoing, server_hostname=host)
        self.transferred = False
        self.exchange(self.tls.do_handshake)

    def exchange(self, operation, *args):
        while True:
            try:
                return operation(*args)
            except ssl.SSLWantReadError:
                pass
            finally:
                if self.outgoing.pending:
                    self.transport.sendall(self.outgoing.read())
            packet = self.transport.recv(65536)
            if packet:
                self.incoming.write(packet)
            else:
                self.incoming.write_eof()

    def sendall(self, data):
        remaining = memoryview(data)
        while remaining:
            remaining = remaining[self.exchange(self.tls.write, remaining):]

    def readable(self):
        return True

    def readinto(self, buffer):
        try:
            return self.exchange(self.tls.read, len(buffer), buffer)
        except ssl.SSLZeroReturnError:
            return 0

    def makefile(self, mode):
        # urllib closes the connection after receiving headers. Its response owns the stream.
        self.transferred = True
        return io.BufferedReader(self)

    def close(self):
        if self.transferred:
            self.transferred = False
        else:
            self.transport.close()
            super().close()


class ProxyTransport(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Handle SOCKS and TLS-to-proxy without changing the process-wide urllib opener."""
    def __init__(self, proxy, environment):
        super().__init__()
        self.proxy, self.environment = proxy, environment

    def http_open(self, req):
        secure = req.type == 'https'
        connection = http.client.HTTPSConnection if secure else http.client.HTTPConnection
        address = self.proxy
        if not address and not urllib.request.proxy_bypass(req.host):
            address = self.environment.get(req.type) or self.environment.get('all')
        if not address:
            return self.do_open(connection, req)
        proxy = urlsplit(address if '://' in address else 'http://' + address)
        if proxy.scheme in ('socks5', 'socks5h'):
            def via_socks(*args, **kwargs):
                conn = connection(*args, **kwargs)
                conn._create_connection = lambda dest, timeout, source: socks_socket(proxy, *dest, timeout)
                return conn
            return self.do_open(via_socks, req)
        if proxy.scheme not in ('http', 'https'):
            raise OSError('Unsupported proxy scheme')
        tls_proxy = proxy.scheme == 'https'
        proxy_port = proxy.port or (443 if tls_proxy else 80)
        auth = {}
        if proxy.username is not None:
            credentials = (unquote(proxy.username) + ':' + unquote(proxy.password or '')).encode()
            auth['Proxy-Authorization'] = 'Basic ' + base64.b64encode(credentials).decode('ascii')
        if not secure:
            req.set_proxy(proxy.netloc.rsplit('@', 1)[-1], 'http')
            for key, value in auth.items():
                req.add_unredirected_header(key, value)
            forward = http.client.HTTPSConnection if tls_proxy else http.client.HTTPConnection
            return self.do_open(lambda host, **kw: forward(proxy.hostname, proxy_port, **kw), req)

        def via_tunnel(host, **kwargs):
            origin = http.client.HTTPSConnection(host, **kwargs)
            if not tls_proxy:
                conn = http.client.HTTPSConnection(proxy.hostname, proxy_port, **kwargs)
                conn.set_tunnel(origin.host, origin.port, headers=auth)
                return conn

            def connect():
                outer = http.client.HTTPSConnection(proxy.hostname, proxy_port, **kwargs)
                outer.connect()
                try:
                    tunnel = http.client.HTTPConnection(proxy.hostname, proxy_port, **kwargs)
                    tunnel.set_tunnel(origin.host, origin.port, headers=auth)
                    tunnel.sock = outer.sock
                    tunnel._tunnel()
                    origin.sock = TunnelTLS(outer.sock, origin.host)
                except BaseException:
                    outer.close()
                    raise
            origin.connect = connect
            return origin
        return self.do_open(via_tunnel, req)

    https_open = http_open


class WebRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme not in ('http', 'https'):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(url, proxy, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': 'robot_console'})
    environment = {} if proxy else urllib.request.getproxies()
    opener = urllib.request.build_opener(WebRedirect(), urllib.request.ProxyHandler({}),
                                         ProxyTransport(proxy, environment))
    return opener.open(req, timeout=timeout)


def version_key(version):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?', version)
    if not match:
        raise ValueError('Invalid release version')
    pre = match[4]
    parts = [(0, int(s)) if s.isdigit() else (1, s) for s in pre.split('.')] if pre else []
    return tuple(map(int, match.groups()[:3])), pre is None, parts


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class UpdateCheck:
    def __init__(self, proxy):
        self.proxy = proxy
        self.lock = threading.Lock()
        self.cached = None
        self.checked = 0

    def get(self, force):
        with self.lock:
            if self.cached and time.monotonic() - self.checked < (30 if force else 6 * 3600):
                return self.cached
            result = dict(current=VERSION, latest=None, newer=False, url=None, published_at=None,
                          checked_at=now_iso(), error=None)
            try:
                with urlopen(f'{GITHUB}/repos/{REPO}/releases/latest', self.proxy, 10) as response:
                    release = json.load(response)
                latest = release['tag_name'].removeprefix('v')
                newer = version_key(latest) > version_key(VERSION)
                result.update(latest=latest, newer=newer, url=release.get('html_url'),
                              published_at=release.get('published_at'))
            except urllib.error.HTTPError as exc:
                result['error'] = f'http_{exc.code}'
                exc.close()
            except (OSError, http.client.HTTPException):
                result['error'] = 'network'
            except (ValueError, KeyError, AttributeError, TypeError, RecursionError):
                result['error'] = 'parse'
            self.cached, self.checked = result, time.monotonic()
            return result


def network_probe(target, url, proxy):
    result = dict(target=target, url=url, ok=False, ms=0, status=None, error=None)
    if target == 'market' and not url.startswith(('https://', 'http://')):
        path = url
        if url.startswith('file://'):
            parts = urlsplit(url)
            authority = '//' + parts.netloc if parts.netloc and parts.netloc != 'localhost' else ''
            path = urllib.request.url2pathname(authority + parts.path)
        result['ok'] = os.path.isfile(path)
        if not result['ok']:
            result['error'] = 'missing'
        return result
    start = time.monotonic()
    try:
        with urlopen(url, proxy, 8) as response:
            result.update(ok=True, status=response.status)
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, error=f'http_{exc.code}')
        exc.close()
    except (OSError, http.client.HTTPException, ValueError):
        result['error'] = 'network'
    result['ms'] = round(1000 * (time.monotonic() - start))
    return result


def require_json(request: Request):
    """Browsers send `application/json` cross-site only after a CORS preflight, which this server never grants, so
    a page on another site cannot submit a form to these routes."""
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise error(415, 'settings_json_required')


class SettingsRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def route(request):
            try:
                return await handle(request)
            except RequestValidationError as exc:
                if self.path.endswith('/import'):
                    raise error(422, 'settings_import_invalid', reason='type') from None
                key = 'force' if self.path.endswith('/update') else 'body'
                raise invalid(key, 'type') from exc
        return route


def register_settings_routes(app, cfg, plugins=None):
    settings = Settings(cfg)
    market_proxy = lookup(cfg, 'plugins.market.proxy') or lookup(cfg, 'plugins.pip.proxy') or ''
    updates = UpdateCheck(market_proxy)
    scans = ThreadPoolExecutor(max_workers=16, thread_name_prefix='console-storage')
    router = APIRouter(prefix='/api/settings', route_class=SettingsRoute)

    @router.get('')
    def get_settings():
        return settings.payload(settings.defaults(), read_mapping(read_text(settings.target)))

    json_only = [Depends(require_json)]

    @router.put('', dependencies=json_only)
    def put_settings(body: dict = Body(...)):
        settings.require_target()
        for key in body:
            if key not in ('set', 'reset'):
                raise error(422, 'setting_unknown', key=key)
        return settings.apply(body.get('set', {}), body.get('reset', []))

    @router.post('/reset_all', dependencies=json_only)
    def reset_all():
        return settings.apply({}, list(FIELDS))

    @router.get('/export')
    def export_settings():
        user = read_mapping(read_text(settings.target))
        exported = {}
        for key in FIELDS:
            value = lookup(user, key, MISSING)
            if value is not MISSING:
                assign(exported, key, value)
        return dict(format='robot_console.settings', version=1, exported_at=now_iso(),
                    console_yaml=yaml_text(exported) if exported else '')

    @router.post('/import', dependencies=json_only)
    def import_settings(body: dict = Body(...)):
        settings.require_target()
        values, ignored = import_values(body.get('console_yaml'))
        resets = [k for k in FIELDS if k not in values and k not in IMPORT_SKIP]
        return dict(settings.apply(values, resets), ignored=ignored)

    @router.get('/storage')
    def get_storage():
        return storage(cfg, scans)

    @router.get('/about')
    def get_about():
        return about(cfg, plugins)

    @router.get('/update')
    def get_update(force: bool = False):
        return updates.get(force)

    @router.post('/network_test', dependencies=json_only)
    def network_test(body: dict = Body(...)):
        for key in ('proxy', 'pip_proxy', 'pip_index_url', 'market_index_url'):
            if key in body:
                location(key, body[key])
        indexes = lookup(cfg, 'plugins.market.index_urls') or DEFAULT_INDEX_URLS
        pip_proxy = lookup(cfg, 'plugins.pip.proxy') or ''
        targets = [('market', body.get('market_index_url', indexes[0]), body.get('proxy', market_proxy)),
                   ('pip', body.get('pip_index_url', lookup(cfg, 'plugins.pip.index_url')) or PYPI,
                    body.get('pip_proxy', body.get('proxy', pip_proxy))),
                   ('github', GITHUB, body.get('proxy', market_proxy))]
        with ThreadPoolExecutor(max_workers=3) as pool:
            probes = [pool.submit(network_probe, *target) for target in targets]
            return {'results': [probe.result() for probe in probes]}

    app.include_router(router)
