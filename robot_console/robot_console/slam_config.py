"""Layered SLAM configuration: the parameter file a SLAM stack really runs with is built by the console.

    base  ->  input  ->  derived  ->  options  ->  console  ->  user        (later layers win)

A specification file (config/slam/<algorithm>.yaml, named by console.yaml slam_profiles.<stack>.config) holds
    format   ros_params (default): one ROS 2 parameter file, written as <slam_config_dir>/<stack>.yaml and given
             to the launch file as config_path:= / config_file:= (FAST-LIO);
             glim_json: a folder of JSON files, written as <slam_config_dir>/<stack>/ and given to the node as
             config_path:= (GLIM). A parameter is then named <file without .json>.<section>.<key>
    base     the validated parameters, nested like the SLAM's own file; or
    base_dir (glim_json) the folder of JSON files that is the base (comments in them are allowed)
    console  {points, imu, map_file: dotted.key}   which parameters take the input topics and the map path that
             console.yaml slam_profiles already names
    inputs   {kind: {dotted.key: value}}   what depends on the data source: 'sim' (the simulated robot, also a bag
             recorded from it) or 'adapter' (a real-robot bag played through the sensor adapter)
    derive   {kind: {dotted.key: {rule, enabled, ...}}}   values computed from the measured facts of a sensor
             (SensorFacts: number of rings, scan rate, points per scan, measured once per sensor profile from a
             bag). An enabled rule sets the value; a rule that is not enabled only shows its value on the page as
             a suggestion: a rule is enabled after it was checked on recorded data.
    options  {name: {dotted.key: value}}   the option switches of the stack (slam_profiles.<stack>.options)
    tunable  {dotted.key: {min, max, label, help}}   the parameters a user may change on the page, with the
             range accepted for each (numbers) and bilingual texts
The console layer sets what console.yaml already knows (input topics, map path); the user layer is what the
user changed on the page ("Parameters" of the SLAM step): stored in mapping.slam_overrides_file per stack and
scope, the scope being the data source ('sim', or 'sensor:<profile>' for a real robot), so a value tuned for one
robot never reaches another.

Every key of a layer must exist in the base with the same type: FAST-LIO declares typed parameters, so an
integer where it expects a float aborts the node, and a misspelt key would be ignored silently.

SlamConfigs.start_args is registered as the start hook of each configured stack (ProcessManager.on_start):
it writes <mapping.slam_config_dir>/<stack>.yaml and points the stack's launch at it. info() is the record of
the last start (every value and the layer it came from), stored as run.json 'slam_config'.

Routes (register_slam_config_routes): GET /api/slam_config/{stack}?bag= (every parameter with its base value,
the value before the user's change, its layer and, for tunable ones, the range), PUT (body {values: {key: value}},
replaces the user's values of that stack and scope; a value equal to the automatic one is dropped), DELETE.
Changes apply at the next start of the stack.

No ROS imports: unit tested (tests/test_slam_config.py).
"""
import glob
import json
import os
import sys
import threading

import yaml

LAYERS = ('base', 'input', 'option', 'console', 'user')
CONFIG_ARGS = ('config_path:=', 'config_file:=')


class SlamConfigError(ValueError):
    pass


def flatten(tree, prefix=''):
    """{'a': {'b': 1}} -> {'a.b': 1}; lists are values."""
    out = {}
    for key, value in (tree or {}).items():
        name = f'{prefix}{key}'
        if isinstance(value, dict):
            out.update(flatten(value, name + '.'))
        else:
            out[name] = value
    return out


def unflatten(flat):
    out = {}
    for name, value in flat.items():
        node = out
        *parents, leaf = name.split('.')
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return out


def strip_json_comments(text):
    """JSON text without // and /* */ comments (GLIM's configuration files carry them); strings are kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def load_json_dir(directory):
    """{file name without .json: content} of a folder of JSON configuration files."""
    out = {}
    for path in sorted(glob.glob(os.path.join(directory, '*.json'))):
        with open(path, encoding='utf-8') as f:
            out[os.path.splitext(os.path.basename(path))[0]] = json.loads(strip_json_comments(f.read()))
    return out


def coerce(key, base, value):
    """`value` as the type of the base value of `key`, or SlamConfigError. Integers are accepted for floats
    (and converted); nothing else is converted."""
    def bad():
        return SlamConfigError(f'{key}: {value!r} does not fit the base value {base!r}')

    if isinstance(base, bool):
        if not isinstance(value, bool):
            raise bad()
        return value
    if isinstance(base, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad()
        return value
    if isinstance(base, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise bad()
        return float(value)
    if isinstance(base, str):
        if not isinstance(value, str):
            raise bad()
        return value
    if isinstance(base, list):
        if not isinstance(value, list) or len(value) != len(base):
            raise bad()
        return [coerce(key, b, v) for b, v in zip(base, value)]
    raise SlamConfigError(f'{key}: unsupported base value {base!r}')


def _layer(base, values, where):
    out = {}
    for key, value in flatten(values).items():
        if key not in base:
            raise SlamConfigError(f'{where}: {key} is not a parameter of the base configuration')
        out[key] = coerce(f'{where}: {key}', base[key], value)
    return out


def _texts(value, default=''):
    """{zh, en} from a string or a {zh, en} mapping (a missing language falls back to the other)."""
    if isinstance(value, dict):
        zh, en = value.get('zh'), value.get('en')
        return {'zh': str(zh or en or default), 'en': str(en or zh or default)}
    return {'zh': str(value or default), 'en': str(value or default)}


def _kind(value):
    return 'bool' if isinstance(value, bool) else 'int' if isinstance(value, int) else \
        'float' if isinstance(value, float) else None


def _tunable(base, raw):
    out = {}
    for key, t in (raw or {}).items():
        t = t or {}
        kind = _kind(base.get(key))
        if key not in base or kind is None:
            raise SlamConfigError(f'tunable: {key} is not a bool, integer or float parameter of the base')
        item = {'type': kind, 'label': _texts(t.get('label'), key), 'help': _texts(t.get('help'))}
        if kind != 'bool':
            for bound in ('min', 'max'):
                if t.get(bound) is None:
                    raise SlamConfigError(f'tunable: {key} needs {bound}')
                item[bound] = coerce(f'tunable: {key}.{bound}', base[key], t[bound])
            if not item['min'] <= base[key] <= item['max']:
                raise SlamConfigError(f'tunable: the base value of {key} is outside its own range')
        out[key] = item
    return out


def check_user(spec, values):
    """The user's values, typed and inside the tunable ranges, or SlamConfigError."""
    if not isinstance(values, dict):
        raise SlamConfigError('values must be a mapping of parameter to value')
    out = {}
    for key, value in values.items():
        t = spec['tunable'].get(key)
        if t is None:
            raise SlamConfigError(f'{key} cannot be changed')
        value = coerce(key, spec['base'][key], value)
        if t['type'] != 'bool' and not t['min'] <= value <= t['max']:
            raise SlamConfigError(f'{key}: {value:g} is outside {t["min"]:g} .. {t["max"]:g}')
        out[key] = value
    return out


# ---------------- values derived from measured sensor facts ----------------
# A rule is fn(facts, params) -> value or None (None: the facts do not say, keep the input layer's value).
# facts (sensor_profiles.measure_facts): rings, scan_rate (Hz), points_per_scan, imu_rate (Hz).

def _rule_rings(facts, p):
    """One entry per ring number of the cloud the SLAM gets (both lidars when two are merged)."""
    n = facts.get('rings')
    return int(n) if n and n >= 1 else None


def _rule_scan_rate(facts, p):
    hz = facts.get('scan_rate')
    return int(round(hz)) if hz and hz >= 0.5 else None


def _rule_decimate(facts, p):
    """Keep every N-th point so that about `target` points of a scan are left (N within min .. max)."""
    n = facts.get('points_per_scan')
    if not n:
        return None
    return int(min(max(round(n / float(p['target'])), p.get('min', 1)), p.get('max', 20)))


def _rule_ring_neighbors(facts, p):
    """Covariance neighbours (k_correspondences) from the lidar ring count: sparse scans need more
    neighbours so they do not all lie on the same scan ring."""
    n = facts.get('rings')
    if not n or n < 1:
        return None
    return int(p.get('sparse', 30) if n <= int(p.get('sparse_max_rings', 24)) else p.get('dense', 20))


RULES = {'rings': _rule_rings, 'scan_rate': _rule_scan_rate, 'decimate': _rule_decimate,
         'ring_neighbors': _rule_ring_neighbors}


def _derive_spec(base, raw):
    out = {}
    for kind, entries in (raw or {}).items():
        out[kind] = {}
        for key, entry in (entries or {}).items():
            entry = dict(entry or {})
            rule = entry.pop('rule', None)
            if key not in base or rule not in RULES:
                raise SlamConfigError(f'derive.{kind}: {key} needs a base parameter and a rule of {sorted(RULES)}')
            out[kind][key] = {'rule': rule, 'enabled': bool(entry.pop('enabled', False)), 'params': entry}
    return out


def derive(spec, input_kind, facts):
    """{key: {'value', 'rule', 'enabled'}} for the rules of this input kind that give a value from `facts`."""
    out = {}
    if not facts:
        return out
    for key, d in (spec['derive'].get(input_kind) or {}).items():
        try:
            value = RULES[d['rule']](facts, d['params'])
            if value is not None:
                out[key] = {'value': coerce(key, spec['base'][key], value), 'rule': d['rule'],
                            'enabled': d['enabled']}
        except (KeyError, TypeError, ValueError):      # incomplete facts or rule parameters: no value
            continue
    return out


class SensorFacts:
    """Measured facts per sensor profile: a YAML file {profile: {rings, scan_rate, points_per_scan, ...}}."""

    def __init__(self, path):
        self.path = path or ''
        self._lock = threading.Lock()

    def _read(self):
        if not self.path or not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, name):
        with self._lock:
            facts = self._read().get(name)
        return dict(facts) if isinstance(facts, dict) else None

    def set(self, name, facts):
        if not self.path:
            return
        with self._lock:
            data = self._read()
            data[name] = dict(facts)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f'{self.path}.tmp{os.getpid()}'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write('# Sensor facts measured by robot_console from a bag of each sensor profile (slam_config.py);\n'
                        '# delete an entry to have it measured again.\n')
                yaml.safe_dump(data, f, sort_keys=True, default_flow_style=False, allow_unicode=True)
            os.replace(tmp, self.path)


def load_spec(path, expand=None):
    """The specification file, checked: {'path', 'name', 'algorithm', 'format', 'base' (flat), 'console',
    'inputs', 'derive', 'options', 'tunable', 'validated'}. expand: config.Expander for ${pkg:NAME} in base_dir
    (a relative base_dir is relative to the specification file)."""
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    fmt = raw.get('format') or 'ros_params'
    if fmt not in ('ros_params', 'glim_json'):
        raise SlamConfigError(f'{path}: unknown format {fmt!r}')
    if raw.get('base_dir'):
        base_dir = expand(raw['base_dir']) if expand else str(raw['base_dir'])
        base_dir = os.path.normpath(os.path.join(os.path.dirname(path), base_dir))
        try:
            base = flatten(load_json_dir(base_dir))
        except ValueError as e:
            raise SlamConfigError(f'{base_dir}: {e}')
        if not base:
            raise SlamConfigError(f'{path}: no JSON files in {base_dir}')
    else:
        base = flatten(raw.get('base'))
    if not base:
        raise SlamConfigError(f'{path}: no base parameters')
    console = dict(raw.get('console') or {})
    for role, key in console.items():
        if role not in ('points', 'imu', 'map_file') or key not in base or not isinstance(base[key], str):
            raise SlamConfigError(f'{path}: console.{role} must name a string parameter of the base')
    name = os.path.splitext(os.path.basename(path))[0]
    return {
        'path': path, 'name': name, 'algorithm': raw.get('algorithm') or name, 'format': fmt, 'base': base,
        'console': console,
        'inputs': {k: _layer(base, v, f'inputs.{k}') for k, v in (raw.get('inputs') or {}).items()},
        'derive': _derive_spec(base, raw.get('derive')),
        'options': {k: _layer(base, v, f'options.{k}') for k, v in (raw.get('options') or {}).items()},
        'tunable': _tunable(base, raw.get('tunable')),
        # where each input kind + option set was checked; data / result are shown on the page ({zh, en})
        'validated': [{**v, 'options': sorted(v.get('options') or []), 'data': _texts(v.get('data')),
                       'result': _texts(v.get('result'))} for v in (raw.get('validated') or [])],
    }


def build(spec, input_kind, options=(), console=None, user=None, derived=None):
    """(params, sources): the flat parameters and, per key, the layer that set it ('base', 'input:sim',
    'derived:rings', 'option:lidar', 'console', 'user'). derived: the result of derive(); only its enabled
    entries are applied. An input kind or option the specification does not know is an error."""
    base = spec['base']
    params = dict(base)
    sources = {k: 'base' for k in base}

    def apply(values, source):
        for key, value in values.items():
            params[key] = value
            sources[key] = source

    if input_kind not in spec['inputs']:
        raise SlamConfigError(f'{spec["name"]}: no input kind {input_kind!r}')
    apply(spec['inputs'][input_kind], f'input:{input_kind}')
    for key, d in (derived or {}).items():
        if d['enabled']:
            apply({key: d['value']}, f'derived:{d["rule"]}')
    for opt in sorted(options or ()):
        if opt not in spec['options']:
            raise SlamConfigError(f'{spec["name"]}: no option {opt!r}')
        apply(spec['options'][opt], f'option:{opt}')
    apply(_layer(base, console or {}, 'console'), 'console')
    apply(_layer(base, user or {}, 'user'), 'user')
    return params, sources


def validated_entry(spec, input_kind, options):
    """The `validated` record of this input kind and option set, or None."""
    want = sorted(options or ())
    return next((v for v in spec['validated']
                 if v.get('input') == input_kind and sorted(v.get('options') or []) == want), None)


def write_ros_params(path, params, header=''):
    """Write flat parameters as a ROS 2 parameter file for any node name."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        if header:
            f.write(''.join(f'# {line}\n' for line in header.splitlines()))
        yaml.safe_dump({'/**': {'ros__parameters': unflatten(params)}}, f, sort_keys=False,
                       default_flow_style=None, allow_unicode=True, width=120)
    os.replace(tmp, path)


def write_glim_json(directory, params, header=''):
    """Write flat parameters (<file>.<section>.<key>) as a folder of JSON files for GLIM; JSON files left
    there by an earlier specification are removed."""
    os.makedirs(directory, exist_ok=True)
    files = unflatten(params)
    for old in glob.glob(os.path.join(directory, '*.json')):
        if os.path.splitext(os.path.basename(old))[0] not in files:
            os.remove(old)
    for stem, tree in files.items():
        tmp = os.path.join(directory, f'.{stem}.tmp{os.getpid()}')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(tree, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, os.path.join(directory, f'{stem}.json'))
    with open(os.path.join(directory, 'GENERATED.txt'), 'w', encoding='utf-8') as f:
        f.write(header + '\n')


class Overrides:
    """The user's values: a YAML file {stack: {scope: {key: value}}}, rewritten atomically."""

    def __init__(self, path):
        self.path = path or ''
        self._lock = threading.Lock()

    def _read(self):
        if not self.path or not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, stack, scope):
        with self._lock:
            values = (self._read().get(stack) or {}).get(scope) or {}
        return dict(values) if isinstance(values, dict) else {}

    def set(self, stack, scope, values):
        """Replace the values of one stack and scope (an empty mapping removes the entry)."""
        if not self.path:
            raise SlamConfigError('no file for user values (mapping.slam_overrides_file)')
        with self._lock:
            data = self._read()
            scopes = data.get(stack) if isinstance(data.get(stack), dict) else {}
            if values:
                scopes[scope] = dict(values)
            else:
                scopes.pop(scope, None)
            if scopes:
                data[stack] = scopes
            else:
                data.pop(stack, None)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f'{self.path}.tmp{os.getpid()}'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write('# SLAM parameters changed on the pages of robot_console (slam_config.py): stack, then the\n'
                        '# data source (sim or sensor:<profile>), then parameter: value.\n')
                yaml.safe_dump(data, f, sort_keys=True, default_flow_style=False, allow_unicode=True)
            os.replace(tmp, self.path)


class SlamConfigs:
    """The configured stacks of one console: builds and records their parameter files."""

    def __init__(self, mapping_cfg, profiles, sensor_prefix='sensor:', log=None, expand=None):
        """mapping_cfg: console.yaml mapping (slam_config_dir, slam_profiles.<stack>.config as resolved by
        config.load). profiles: mapping.slam_profiles() result. expand: config.Expander, see load_spec."""
        self.dir = mapping_cfg.get('slam_config_dir') or ''
        self.overrides = Overrides(mapping_cfg.get('slam_overrides_file'))
        self.facts = SensorFacts(mapping_cfg.get('slam_facts_file'))
        self.sensor_prefix = sensor_prefix
        self._log = log
        self._profiles = profiles
        self._lock = threading.Lock()
        self._auto_lock = threading.Lock()
        self._derived, self._facts = {}, None
        self._last = {}             # stack -> record of its last start
        self.specs = {}             # stack -> specification
        loaded = {}
        for stack, prof in profiles.items():
            path = prof.get('config')
            if not path:
                continue
            try:
                if path not in loaded:
                    loaded[path] = load_spec(path, expand)
                spec = loaded[path]
                for kind in spec['inputs'] or ['sim']:        # fails early
                    build(spec, kind, prof.get('options') or [], self._console_layer(prof, spec))
                self.specs[stack] = spec
            except (OSError, yaml.YAMLError, SlamConfigError) as e:
                msg = f'[robot_console] SLAM configuration of {stack} not used ({e}); it starts with its own file'
                log.warn(msg) if log else print(msg, file=sys.stderr)

    @staticmethod
    def _console_layer(prof, spec):
        """What console.yaml already says about the stack: the topics it listens on and where it saves, put
        into the parameters the specification names for them (console: {points, imu, map_file})."""
        inputs = prof.get('inputs') or {}
        values = {'points': inputs.get('points'), 'imu': inputs.get('imu'), 'map_file': prof.get('pcd_path')}
        return {key: values[role] for role, key in spec['console'].items() if values.get(role)}

    def has(self, stack):
        return stack in self.specs

    def input_of(self, preset):
        """(input kind, sensor profile name) for a stack preset: 'sensor:<profile>' is a real-robot bag played
        through the sensor adapter, everything else the simulated robot."""
        if preset and str(preset).startswith(self.sensor_prefix):
            return 'adapter', str(preset)[len(self.sensor_prefix):]
        return 'sim', None

    def scope_of(self, preset):
        """Key the user's values are stored under: the data source ('sim' or 'sensor:<profile>')."""
        kind, sensor = self.input_of(preset)
        return f'{self.sensor_prefix}{sensor}' if kind == 'adapter' else 'sim'

    def _auto(self, stack, preset):
        """The configuration before the user's values, with the sensor facts and derived values behind it."""
        spec, prof = self.specs[stack], self._profiles[stack]
        kind, sensor = self.input_of(preset)
        options = sorted(prof.get('options') or [])
        facts = self.facts.get(sensor) if sensor else None
        derived = derive(spec, kind, facts)
        params, sources = build(spec, kind, options, self._console_layer(prof, spec), derived=derived)
        self._derived, self._facts = derived, facts      # of the last call, read by plan() / describe()
        return spec, kind, sensor, options, params, sources

    def user_of(self, stack, preset, auto=None):
        """The stored user values of the stack for this data source that still apply: entries the
        specification no longer accepts (key removed, range narrowed) are left out, and so are values equal
        to the automatic one."""
        spec = self.specs[stack]
        out = {}
        for key, value in self.overrides.get(stack, self.scope_of(preset)).items():
            try:
                value = check_user(spec, {key: value})[key]
            except SlamConfigError as e:
                msg = f'[robot_console] stored value of {stack} ignored: {e}'
                self._log.warn(msg) if self._log else print(msg, file=sys.stderr)
                continue
            if auto is None or auto.get(key) != value:
                out[key] = value
        return out

    def plan(self, stack, preset=None):
        """The record of a start of `stack` with `preset`, without writing anything."""
        with self._auto_lock:
            spec, kind, sensor, options, params, sources = self._auto(stack, preset)
            derived, facts = self._derived, self._facts
        user = self.user_of(stack, preset, params)
        for key, value in user.items():
            params[key], sources[key] = value, 'user'
        changed = {k: {'value': params[k], 'from': sources[k]} for k in params if sources[k] != 'base'}
        return {
            'name': spec['name'], 'algorithm': spec['algorithm'], 'input': kind, 'sensor': sensor,
            'options': options, 'modified': bool(user), 'user': user,
            # measured sensor facts and what the rules made of them (enabled: applied, else only suggested)
            'facts': facts, 'derived': derived,
            'validated': validated_entry(spec, kind, options),
            # the parameter file, or for GLIM the folder of JSON files
            'file': os.path.join(self.dir, stack if spec['format'] == 'glim_json' else f'{stack}.yaml'),
            'changed': changed, 'params': params,
        }

    def describe(self, stack, preset=None):
        """Everything the page shows for `stack` on this data source: per parameter the base value, the value
        before the user's change (auto) with its layer, the user's value and, when tunable, the range."""
        with self._auto_lock:
            spec, kind, sensor, options, auto, sources = self._auto(stack, preset)
            derived, facts = self._derived, self._facts
        user = self.user_of(stack, preset, auto)
        # A rule's value that is not the automatic one (the rule is not enabled, or an option switch set the
        # parameter after it): shown as a suggestion
        suggest = {k: {'value': d['value'], 'rule': d['rule']} for k, d in derived.items()
                   if d['value'] != auto[k]}
        # tunable parameters first, in the order of the specification; then the read-only rest
        keys = [*spec['tunable'], *(k for k in spec['base'] if k not in spec['tunable'])]
        rows = [{'key': key, 'base': spec['base'][key], 'auto': auto[key], 'source': sources[key],
                 'user': user.get(key), 'value': user.get(key, auto[key]), 'suggest': suggest.get(key),
                 'tunable': spec['tunable'].get(key)} for key in keys]
        return {'stack': stack, 'name': spec['name'], 'algorithm': spec['algorithm'], 'input': kind,
                'sensor': sensor, 'scope': self.scope_of(preset), 'options': options, 'facts': facts,
                'validated': validated_entry(spec, kind, options), 'modified': bool(user),
                'user_count': len(user), 'params': rows}

    def set_user(self, stack, preset, values):
        """Store the user's values of `stack` for this data source (replacing the old ones) and describe the
        result. Values equal to the automatic ones are not stored."""
        with self._auto_lock:
            spec, _, _, _, auto, _ = self._auto(stack, preset)
        values = {k: v for k, v in check_user(spec, values or {}).items() if auto[k] != v}
        self.overrides.set(stack, self.scope_of(preset), values)
        return self.describe(stack, preset)

    def start_args(self, stack, extra, preset):
        """Start hook: write the stack's parameter file and return `extra` pointing at it."""
        rec = self.plan(stack, preset)
        header = (f'Written by robot_console at every start of {stack}; edits here are overwritten.\n'
                  f'Layers: {os.path.basename(self.specs[stack]["path"])} base, input {rec["input"]}'
                  + (f' ({rec["sensor"]})' if rec['sensor'] else '')
                  + ''.join(f', {k} from the sensor ({d["rule"]})' for k, d in rec['derived'].items()
                            if rec['changed'].get(k, {}).get('from') == f'derived:{d["rule"]}')
                  + f', options [{", ".join(rec["options"])}]'
                  + (f', changed by the user: {", ".join(rec["user"])}' if rec['modified'] else ''))
        kept = [a for a in (extra or []) if not str(a).startswith(CONFIG_ARGS)]
        if self.specs[stack]['format'] == 'glim_json':
            write_glim_json(rec['file'], rec['params'], header)
            args = [*kept, f'config_path:={rec["file"]}']
        else:
            write_ros_params(rec['file'], rec['params'], header)
            args = [*kept, f'config_path:={os.path.dirname(rec["file"])}',
                    f'config_file:={os.path.basename(rec["file"])}']
        with self._lock:
            self._last[stack] = rec
        return args

    def info(self, stack):
        """Record of the last start of `stack` (None when it has no layered configuration or never started)."""
        with self._lock:
            return self._last.get(stack)


def register_slam_config_routes(app, configs, preset_of):
    """preset_of(stack, bag) -> the preset a start of `stack` would get on the Playback page for that bag
    (None for the simulation); it may raise an HTTPException itself."""
    from fastapi import HTTPException

    def call(stack, fn, *args):
        if not configs.has(stack):
            raise HTTPException(404, detail={'code': 'slam_config_none', 'params': {'name': stack}})
        try:
            return fn(stack, *args)
        except SlamConfigError as e:
            raise HTTPException(400, detail={'code': 'slam_config_invalid', 'params': {'reason': str(e)}})

    @app.get('/api/slam_config/{stack}')
    def get_slam_config(stack: str, bag: str | None = None):
        return call(stack, configs.describe, preset_of(stack, bag))

    @app.put('/api/slam_config/{stack}')
    def put_slam_config(stack: str, body: dict, bag: str | None = None):
        return call(stack, configs.set_user, preset_of(stack, bag), body.get('values'))

    @app.delete('/api/slam_config/{stack}')
    def delete_slam_config(stack: str, bag: str | None = None):
        return call(stack, configs.set_user, preset_of(stack, bag), {})
