"""Load console.yaml and stacks.yaml.

The configuration has two layers:
  1. config/console.yaml shipped with the package (generic defaults that work on any machine)
  2. an optional user override file: the file named by the environment variable ROBOT_CONSOLE_USER_CONFIG,
     or ~/.config/robot_console/console.yaml (only when the console is started without --config). It holds
     only the keys to change and is merged onto layer 1 key by key (dicts are merged recursively, lists and
     other values are replaced).

Strings in console.yaml, stacks.yaml and mapping_presets.yaml may use:
  ~/xxx or name:=~/xxx   the home directory
  ${data_root}           paths.data_root (maps, run records, logs, ... default to folders below it)
  ${pkg:NAME}            the install directory share/NAME of a ROS package; left as is, with a warning,
                         when the package is not installed
"""
import os
import re
import sys
from pathlib import Path

import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory

HOME = os.path.expanduser('~')
USER_CONFIG = '~/.config/robot_console/console.yaml'
DEFAULT_DATA_ROOT = '~/robot_console_data'
_PLACEHOLDER = re.compile(r'\$\{(data_root|pkg:[A-Za-z0-9_]+)\}')


def default_config_path():
    return os.path.join(get_package_share_directory('robot_console'), 'config', 'console.yaml')


def user_config_path(explicit=False):
    """Path of the user override file, or None when there is none.

    explicit: the console was started with --config. Then ~/.config/robot_console/console.yaml is NOT applied
    (test instances pass their own complete console.yaml and must not pick up this machine's data folders);
    an override file named by ROBOT_CONSOLE_USER_CONFIG still is."""
    env = os.environ.get('ROBOT_CONSOLE_USER_CONFIG')
    if env:
        path = os.path.expanduser(env)
    elif explicit:
        return None
    else:
        path = os.path.expanduser(USER_CONFIG)
    return path if os.path.isfile(path) else None


def merge(base, override):
    """Return base with override merged in: dicts key by key, every other value (lists too) replaced."""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


class Expander:
    """Expands ~, ${data_root} and ${pkg:NAME} in configuration strings."""

    def __init__(self, data_root):
        self.data_root = os.path.expanduser(data_root)
        self.missing = set()          # ROS packages that were not found (warned once each)

    def _sub(self, m):
        key = m.group(1)
        if key == 'data_root':
            return self.data_root
        pkg = key[4:]
        try:
            return get_package_share_directory(pkg)
        except PackageNotFoundError:
            if pkg not in self.missing:
                self.missing.add(pkg)
                print(f'[robot_console] ROS package {pkg} used in the configuration is not installed; '
                      'stacks that need it cannot start', file=sys.stderr)
            return m.group(0)

    def __call__(self, value):
        """Command arguments and paths. Commands do not go through a shell, so ~ is expanded here,
        both as '~/xxx' and in ROS launch arguments 'name:=~/xxx'."""
        s = _PLACEHOLDER.sub(self._sub, str(value))
        if s.startswith('~'):
            return os.path.expanduser(s)
        return s.replace(':=~', ':=' + HOME)

    def tree(self, value):
        """Expand every string inside nested dicts and lists."""
        if isinstance(value, dict):
            return {k: self.tree(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.tree(v) for v in value]
        if isinstance(value, str):
            return self(value)
        return value


def _expand_arg(arg):
    """Expand ~ and placeholders with the default data root (kept for older callers)."""
    return Expander(DEFAULT_DATA_ROOT)(arg)


def bilingual(value, default=''):
    """Text shown on the web page, as {'zh': Chinese, 'en': English}.

    The configuration may give one string (used for both languages) or {zh: ..., en: ...};
    a missing language falls back to the other one.
    """
    if value is None:
        value = default
    if isinstance(value, dict):
        zh, en = value.get('zh'), value.get('en')
        return {'zh': zh or en or '', 'en': en or zh or ''}
    return {'zh': str(value), 'en': str(value)}


def resolve_web_dist(value, config_path):
    """Folder of the built frontend. With 'auto' (or nothing) it looks, in order, for web/dist
      - next to the robot_console Python package (develop install),
      - in the source tree the config file really lives in (with --symlink-install the config under share/
        links to the source),
      - in <workspace>/src/robot_console, the workspace being two levels above the package's install prefix
        (a plain colcon build copies the package into <workspace>/install/robot_console).
    If none exists the first candidate is returned and the server reports that the frontend is not built."""
    if value and value != 'auto':
        return os.path.expanduser(value)
    candidates = [Path(__file__).resolve().parents[1] / 'web' / 'dist',
                  Path(os.path.realpath(config_path)).parents[1] / 'web' / 'dist']
    try:
        from ament_index_python.packages import get_package_prefix
        ws = Path(get_package_prefix('robot_console')).parents[1]
        # robot_console directly in src/ or one level down (the public repository holds all three packages)
        candidates += [ws / 'src' / 'robot_console' / 'web' / 'dist'] + \
            sorted(p / 'dist' for p in ws.glob('src/*/robot_console/web'))
    except Exception:              # not installed through colcon (e.g. tests run from the source tree)
        pass
    for c in candidates:
        if (c / 'index.html').is_file():
            return str(c)
    return str(candidates[0])


def resolve_packaged(name, config_path):
    """A file shipped in the package's config folder, named relative to console.yaml: next to the console.yaml
    in use, else in the package's own config folder (source tree or share/). The first candidate is returned
    when none exists, so the caller reports that path."""
    if os.path.isabs(name):
        return name
    candidates = [os.path.join(os.path.dirname(config_path), name),
                  str(Path(__file__).resolve().parents[1] / 'config' / name)]
    try:
        candidates.append(os.path.join(get_package_share_directory('robot_console'), 'config', name))
    except PackageNotFoundError:
        pass
    return next((c for c in candidates if os.path.isfile(c)), candidates[0])


def shipped_plugins_dir():
    """Folder of the plugins shipped with the console: share/robot_console/plugins when installed, else plugins/ of
    the source tree (tests). None when neither exists."""
    candidates = []
    try:
        candidates.append(os.path.join(get_package_share_directory('robot_console'), 'plugins'))
    except PackageNotFoundError:
        pass
    candidates.append(str(Path(__file__).resolve().parents[1] / 'plugins'))
    return next((c for c in candidates if os.path.isdir(c)), None)


def load(path=None):
    explicit = path is not None
    path = path or default_config_path()
    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    user = user_config_path(explicit)
    if user:
        with open(user, encoding='utf-8') as f:
            cfg = merge(cfg, yaml.safe_load(f) or {})
    cfg['config_files'] = [path] + ([user] if user else [])

    cfg['paths'] = cfg.get('paths') or {}
    expand = Expander(cfg['paths'].get('data_root') or DEFAULT_DATA_ROOT)
    cfg['paths']['data_root'] = expand.data_root

    srv = cfg['server'] = cfg.get('server') or {}
    srv.setdefault('venv', '~/venvs/console')
    srv.setdefault('log_dir', '${data_root}/logs')
    for key in ('venv', 'log_dir'):
        srv[key] = expand(srv[key])
    web_dist = srv.get('web_dist') or 'auto'
    srv['web_dist'] = resolve_web_dist('auto' if web_dist == 'auto' else expand(web_dist), path)

    stacks_path = os.path.join(os.path.dirname(path), cfg.get('stacks_file', 'stacks.yaml'))
    with open(stacks_path, encoding='utf-8') as f:
        stacks = yaml.safe_load(f) or {}
    for name, spec in stacks.items():
        spec['cmd'] = [expand(a) for a in spec['cmd']]
        spec['label'] = bilingual(spec.get('label'), name)
        spec['description'] = bilingual(spec.get('description'))
    cfg['stacks'] = stacks

    cfg.setdefault('topics', [])
    cfg.setdefault('tf_edges', [])
    cfg.setdefault('estop', {})
    cfg['teleop'] = cfg.get('teleop') or {}

    # Data folders default to data_root; every path is made absolute here so the modules can use it as is
    mapping = cfg['mapping'] = cfg.get('mapping') or {}
    for key, sub in (('maps_dir', 'maps'), ('runs_dir', 'runs'), ('scenes_dir', 'scenes')):
        mapping.setdefault(key, '${data_root}/' + sub)
    for key in ('maps_dir', 'runs_dir', 'scenes_dir', 'pcd_path'):
        if mapping.get(key):
            mapping[key] = expand(mapping[key])
    if mapping.get('slam_profiles'):
        mapping['slam_profiles'] = expand.tree(mapping['slam_profiles'])
        # Layered SLAM configuration (slam_config.py): the specification file is named relative to this file;
        # a console.yaml copied elsewhere on its own (test instances) falls back to the installed package's copy
        for prof in mapping['slam_profiles'].values():
            if isinstance(prof, dict) and prof.get('config'):
                prof['config'] = resolve_packaged(prof['config'], path)
    mapping['slam_config_dir'] = expand(mapping.get('slam_config_dir') or '${data_root}/slam/effective')
    mapping['slam_overrides_file'] = expand(mapping.get('slam_overrides_file')
                                            or '${data_root}/slam/overrides.yaml')
    mapping['slam_facts_file'] = expand(mapping.get('slam_facts_file') or '${data_root}/slam/sensor_facts.yaml')
    playback = cfg['playback'] = cfg.get('playback') or {}
    playback['bag_dirs'] = [expand(d) for d in (playback.get('bag_dirs') or ['${data_root}/bags'])]
    if playback.get('upload_dir'):
        playback['upload_dir'] = expand(playback['upload_dir'])
    playback['space_check'] = [expand(d) for d in (playback.get('space_check') or [])]
    if (playback.get('scan_recording') or {}).get('dir'):
        playback['scan_recording']['dir'] = expand(playback['scan_recording']['dir'])
    # Sensor profiles of real robots (bags played through robot_console/sensor_adapter.py)
    profiles = {}
    sfile = playback.get('sensor_profiles_file')
    if sfile:
        playback['sensor_profiles_file'] = os.path.join(os.path.dirname(path), sfile)
        with open(playback['sensor_profiles_file'], encoding='utf-8') as f:
            profiles = yaml.safe_load(f) or {}
    for name, p in profiles.items():
        p['label'] = bilingual(p.get('label'), name)
        p['slam_args'] = {stack: [expand(a) for a in (args or [])]
                          for stack, args in (p.get('slam_args') or {}).items()}
    playback['sensor_profiles'] = profiles
    # Profiles made on the Playback page (robot_console/sensor_profiles.py) live in a writable file of their own
    # and all get the SLAM configurations of sensor_slam_args
    playback['user_sensor_profiles_file'] = expand(playback.get('user_sensor_profiles_file')
                                                   or '${data_root}/sensor_profiles.yaml')
    playback['sensor_slam_args'] = {stack: [expand(a) for a in (args or [])]
                                    for stack, args in (playback.get('sensor_slam_args') or {}).items()}
    ev = cfg['evaluation'] = cfg.get('evaluation') or {}
    ev.setdefault('results_dir', '${data_root}/results')
    ev.setdefault('evo_python', '~/venvs/evo/bin/python')
    for key in ('results_dir', 'evo_python'):
        ev[key] = expand(ev[key])
    # Offline planar loop back-end (Maps page, map_loop.py): its own interpreter with GTSAM + small_gicp
    lb = cfg['loop_backend'] = cfg.get('loop_backend') or {}
    lb['python'] = expand(lb.get('python') or '~/venvs/loop/bin/python')
    lb.setdefault('timeout', 1800)
    plugins = cfg['plugins'] = cfg.get('plugins') or {}
    plugins['dirs'] = [expand(d) for d in (plugins.get('dirs') or ['${data_root}/plugins'])]
    shipped = shipped_plugins_dir()
    if plugins.get('shipped', True) and shipped:
        plugins['shipped_dir'] = shipped
        if shipped not in plugins['dirs']:
            plugins['dirs'].append(shipped)
    else:
        plugins['shipped_dir'] = None
    plugins['state_file'] = expand(plugins.get('state_file') or '${data_root}/plugin_state.json')
    plugins['data_dir'] = expand(plugins.get('data_dir') or '${data_root}/plugin_data')
    plugins['envs_dir'] = expand(plugins.get('envs_dir') or '${data_root}/plugin_envs')
    if plugins.get('python'):
        plugins['python'] = expand(plugins['python'])
    nav = cfg['navigation'] = cfg.get('navigation') or {}
    for key in ('params_file', 'default_map'):
        if nav.get(key):
            nav[key] = expand(nav[key])

    # Mapping scene presets: bilingual texts, expanded arguments. A preset may list files it needs
    # (`requires`, e.g. a building model that each user generates from a dataset); presets with a missing
    # file are left out with a warning, so the page never offers a world Gazebo cannot load.
    presets = {}
    pfile = mapping.get('presets_file')
    if pfile:
        with open(os.path.join(os.path.dirname(path), pfile), encoding='utf-8') as f:
            presets = yaml.safe_load(f) or {}
    unavailable = {}
    for name, p in list(presets.items()):
        missing = [f for f in (expand(r) for r in (p.get('requires') or [])) if not os.path.exists(f)]
        if missing:
            unavailable[name] = missing
            del presets[name]
            print(f'[robot_console] scene preset {name} hidden, missing: {", ".join(missing)}'
                  + (f' ({p["requires_hint"]})' if p.get('requires_hint') else ''), file=sys.stderr)
            continue
        p['label'] = bilingual(p.get('label'), name)
        p['description'] = bilingual(p.get('description'))
        p['args'] = {stack: [expand(a) for a in (args or [])]
                     for stack, args in (p.get('args') or {}).items()}
    if unavailable and mapping.get('default_preset') in unavailable:
        mapping['default_preset'] = next(iter(presets), None)
    cfg['presets'] = presets
    cfg['unavailable_presets'] = unavailable
    cfg['missing_packages'] = sorted(expand.missing)
    return cfg
