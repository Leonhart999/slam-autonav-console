"""Read-only tools of the assistant. Every tool reads console data through the console's own REST API (ctx.api, GET
only) and returns compact JSON for the model: big series, help texts and file lists are left out or summarized.
"""
import asyncio
import json
import math
from urllib.parse import quote

MAX_RESULT_CHARS = 14000


class ToolError(Exception):
    pass


def q(value):
    return quote(str(value), safe='')


def _round(v, n=4):
    if isinstance(v, float):
        return round(v, n) if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _round(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_round(x, n) for x in v]
    return v


def _pick(d, keys):
    return {k: d.get(k) for k in keys if d.get(k) not in (None, [], {}, '')}


def _text(v):
    """Bilingual {zh, en} -> one string (English first, the model answers in the user's language anyway)."""
    if isinstance(v, dict) and ('en' in v or 'zh' in v):
        en, zh = v.get('en') or '', v.get('zh') or ''
        return en if en == zh or not zh else f'{en} / {zh}' if en else zh
    return v


# ---------------- trajectory summary ----------------

def trajectory_summary(tr):
    """Distance, start/end, height (z) profile and the steepest height change of an estimated trajectory."""
    t, x, y, z = tr.get('t') or [], tr.get('x') or [], tr.get('y') or [], tr.get('z') or []
    n = min(len(t), len(x), len(y), len(z))
    if n < 2:
        return {'poses': n}
    d = [0.0]
    for i in range(1, n):
        d.append(d[-1] + math.dist((x[i - 1], y[i - 1], z[i - 1]), (x[i], y[i], z[i])))
    total = d[-1]
    zmin, zmax = min(z[:n]), max(z[:n])
    checkpoints = {}
    marks = [m for m in (1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 1000, 2000) if m < total] + [total]
    j = 0
    for m in marks:
        while j < n - 1 and d[j] < m:
            j += 1
        checkpoints[f'{m:g}m' if m != total else 'end'] = {'z': round(z[j], 3), 't': round(t[j], 1)}
    # steepest height change over any window of about 2 m driven
    best, k = None, 0
    for i in range(n):
        while k < n - 1 and d[k] - d[i] < 2.0:
            k += 1
        if d[k] - d[i] < 1.0:
            break
        dz = z[k] - z[i]
        if best is None or abs(dz) > abs(best[0]):
            best = (dz, i, k)
    out = {
        'poses': n, 'duration_s': round(t[n - 1] - t[0], 1), 'distance_m': round(total, 2),
        'start': [round(x[0], 3), round(y[0], 3), round(z[0], 3)],
        'end': [round(x[n - 1], 3), round(y[n - 1], 3), round(z[n - 1], 3)],
        'start_end_distance_m': round(math.dist((x[0], y[0], z[0]), (x[n - 1], y[n - 1], z[n - 1])), 3),
        'z': {'start': round(z[0], 3), 'end': round(z[n - 1], 3), 'min': round(zmin, 3), 'max': round(zmax, 3),
              'end_minus_start': round(z[n - 1] - z[0], 3),
              'per_100m': round((z[n - 1] - z[0]) / total * 100, 3) if total > 1 else None,
              'at_distance': checkpoints},
    }
    if best:
        dz, i, k = best
        out['z']['steepest_2m_window'] = {'dz': round(dz, 3), 'from_m': round(d[i], 1), 'to_m': round(d[k], 1),
                                          't': [round(t[i], 1), round(t[k], 1)]}
    return out


def sampled_trajectory(tr, samples):
    t, x, y, z = tr.get('t') or [], tr.get('x') or [], tr.get('y') or [], tr.get('z') or []
    n = min(len(t), len(x), len(y), len(z))
    if n == 0:
        return []
    d = [0.0]
    for i in range(1, n):
        d.append(d[-1] + math.dist((x[i - 1], y[i - 1], z[i - 1]), (x[i], y[i], z[i])))
    idx = sorted({round(i * (n - 1) / max(1, samples - 1)) for i in range(samples)})
    return [[round(t[i], 2), round(d[i], 2), round(x[i], 3), round(y[i], 3), round(z[i], 3)] for i in idx]


# ---------------- tools ----------------

def _stats(ev):
    if not isinstance(ev, dict):
        return ev
    out = _pick(ev, ('matched_poses', 'duration', 'length', 'ref_length', 'params', 'created'))
    for k in ('ape', 'rpe'):
        if isinstance(ev.get(k), dict):
            out[k] = ev[k]
    return _round(out)


def _run_row(r):
    row = _pick(r, ('id', 'type', 'started', 'algorithm', 'preset', 'variant', 'duration', 'length', 'ape_rmse',
                    'rpe_rmse', 'evaluated', 'eval_error', 'maps', 'params_changed', 'recording'))
    pb = r.get('playback')
    if isinstance(pb, dict):
        row['playback'] = _pick(pb, ('bag_name', 'range', 'rate', 'sensor', 'gt_kind', 'gt_suspect', 'ref_source',
                                     'ref_ape_rmse', 'loop', 'lag', 'stats'))
    if r.get('nav'):
        row['nav'] = r['nav']
    return _round(row)


async def list_maps(api, a):
    maps = await api.get('/api/maps')
    rows = []
    for m in maps:
        row = _pick(m, ('name', 'created', 'source', 'bag', 'preset', 'algorithm', 'points', 'navigable', 'has_cloud',
                        'has_grid'))
        if isinstance(m.get('grid'), dict):
            row['grid'] = _pick(m['grid'], ('resolution', 'size_m'))
        row['issues'] = [i.get('code') for i in m.get('issues') or []]
        rows.append(row)
    return {'count': len(rows), 'maps': rows}


async def get_map(api, a):
    m = await api.get(f'/api/maps/{q(a["name"])}')
    m.pop('location_win', None)
    m['files'] = [f'{f["name"]} ({f.get("size")} B)' for f in m.get('files') or []]
    return _round(m)


async def list_runs(api, a):
    data = await api.get('/api/evaluation/runs')
    runs = data.get('runs') or []
    kind = a.get('type')
    if kind:
        runs = [r for r in runs if r.get('type') == kind]
    text = (a.get('contains') or '').lower()
    if text:
        def hay(r):
            pb = r.get('playback') or {}
            return ' '.join(str(v) for v in (r.get('id'), r.get('algorithm'), r.get('preset'), pb.get('bag_name'),
                                               ' '.join(r.get('maps') or []))).lower()
        runs = [r for r in runs if text in hay(r)]
    runs.sort(key=lambda r: r.get('started') or '', reverse=True)
    limit = max(1, min(int(a.get('limit') or 20), 60))
    return {'total_matching': len(runs), 'shown': min(limit, len(runs)), 'eval_params': data.get('params'),
            'runs': [_run_row(r) for r in runs[:limit]]}


def _slam_config_values(sc):
    """run.json slam_config -> {key: value} and the user / changed values."""
    if not isinstance(sc, dict):
        return None
    params = sc.get('params') or sc.get('values') or {}
    if isinstance(params, list):
        return {p.get('key'): {'value': p.get('value'), 'source': p.get('source')} for p in params if isinstance(p, dict)}
    return params


async def get_run(api, a):
    rid = a['run_id']
    d = await api.get(f'/api/evaluation/runs/{q(rid)}')
    out = {'id': d.get('id'), 'type': d.get('type'), 'row': _run_row(d)}
    info = dict(d.get('info') or {})
    sc = info.pop('slam_config', None)
    out['info'] = _round(info)
    if sc is not None:
        out['slam_config'] = _round(_pick(sc, ('stack', 'name', 'algorithm', 'input', 'sensor', 'scope', 'file'))
                                    | {'params': _slam_config_values(sc)})
    out['eval'] = _stats(d.get('eval'))
    if d.get('ref_eval'):
        out['ref_eval_vs_reference_odometry'] = _stats(d['ref_eval'])
    for k in ('amcl', 'nav', 'figures'):
        if d.get(k):
            out[k] = _round(d[k])
    try:
        out['trajectory'] = trajectory_summary(await api.get(f'/api/evaluation/runs/{q(rid)}/trajectory'))
    except Exception as e:  # noqa: BLE001  runs without an estimate (navigation) have no trajectory
        out['trajectory'] = {'unavailable': str(getattr(e, 'detail', e))[:200]}
    return out


async def get_trajectory(api, a):
    rid = a['run_id']
    samples = max(10, min(int(a.get('samples') or 60), 300))
    tr = await api.get(f'/api/evaluation/runs/{q(rid)}/trajectory')
    return {'id': rid, 'columns': ['t_s', 'distance_m', 'x', 'y', 'z'], 'frame_note': 'estimated trajectory, SLAM frame',
            'samples': sampled_trajectory(tr, samples)}


async def compare_runs(api, a):
    ids = list(dict.fromkeys(a.get('run_ids') or []))[:8]
    if len(ids) < 2:
        raise ToolError('give at least two run ids')
    fetched = await asyncio.gather(*(get_run(api, {'run_id': rid}) for rid in ids), return_exceptions=True)
    rows, configs = [], {}
    for rid, r in zip(ids, fetched):
        if isinstance(r, BaseException):
            rows.append({'id': rid, 'error': str(getattr(r, 'detail', r))[:200]})
            continue
        tr = r.get('trajectory') or {}
        rows.append({'id': rid, 'run': r['row'], 'ape': (r.get('eval') or {}).get('ape'),
                     'rpe': (r.get('eval') or {}).get('rpe'),
                     'trajectory': {k: tr.get(k) for k in ('distance_m', 'start_end_distance_m')} |
                     {'z': {k: (tr.get('z') or {}).get(k) for k in ('end_minus_start', 'min', 'max', 'per_100m',
                                                                     'steepest_2m_window')}}})
        if r.get('slam_config'):
            configs[rid] = r['slam_config'].get('params') or {}
    diff = {}
    if len(configs) >= 2:
        keys = set().union(*configs.values())
        for k in sorted(keys):
            vals = {rid: (c.get(k) or {}).get('value') if isinstance(c.get(k), dict) else c.get(k)
                    for rid, c in configs.items()}
            if len({json.dumps(v, sort_keys=True) for v in vals.values()}) > 1:
                diff[k] = vals
    return {'runs': rows, 'slam_parameter_differences': diff,
            'note': None if len(configs) >= 2 else 'parameter comparison needs runs that recorded slam_config'}


async def list_batches(api, a):
    kind = a.get('kind') or 'mapping'
    path = '/api/batch/history' if kind == 'mapping' else '/api/nav-batch/history'
    hist = await api.get(path)
    limit = max(1, min(int(a.get('limit') or 15), 40))
    return {'kind': kind, 'total': len(hist), 'batches': _round(hist[:limit])}


async def get_batch(api, a):
    kind = a.get('kind') or 'mapping'
    base = '/api/batch/' if kind == 'mapping' else '/api/nav-batch/'
    return _round(await api.get(base + q(a['batch_id'])))


async def get_slam_config(api, a):
    path = f'/api/slam_config/{q(a["stack"])}'
    params = {'bag': a['bag']} if a.get('bag') else None
    d = await api.get(path, params=params)
    rows = []
    for p in d.get('params') or []:
        row = _pick(p, ('key', 'value', 'source', 'base', 'auto', 'user', 'suggest'))
        tun = p.get('tunable') or {}
        if tun:
            row['label'] = _text(tun.get('label'))
            row['range'] = [tun.get('min'), tun.get('max')]
        rows.append(row)
    head = {k: _text(v) if k != 'validated' else _round(v) for k, v in d.items() if k != 'params'}
    return {**head, 'params': rows,
            'sources': 'base = shipped file, auto = derived from sensor facts, user = changed by the user'}


async def get_stack_log(api, a):
    name = a['stack']
    lines = max(10, min(int(a.get('lines') or 120), 400))
    stacks = await api.get('/api/stacks')
    state = next((_pick(s, ('name', 'state', 'cmd', 'preset', 'uptime', 'exit_code', 'last_stop', 'messages'))
                  for s in stacks if s.get('name') == name), None)
    log = await api.get(f'/api/stacks/{q(name)}/log', params={'lines': lines})
    return {'stack': state, 'log_tail': log.get('text') or '(empty: not started since the console started)'}


async def get_console_status(api, a):
    s = await api.get('/api/status')
    out = {'stacks': [_round(_pick(x, ('name', 'state', 'preset', 'uptime', 'exit_code', 'last_stop', 'messages')))
                      for x in s.get('stacks') or []]}
    for k in ('clock', 'resources', 'paths'):
        if k in s:
            out[k] = _round(s[k])
    if isinstance(s.get('topics'), list):
        out['topics'] = [_round(_pick(t, ('name', 'type', 'publishers', 'hz', 'expected_hz', 'age'))) for t in s['topics']]
    if isinstance(s.get('tf'), list):
        out['tf'] = _round(s['tf'])
    elif isinstance(s.get('tf_edges'), list):
        out['tf'] = _round(s['tf_edges'])
    return out


async def list_sensor_profiles(api, a):
    return _round(await api.get('/api/playback/sensor_profiles'), 6)


async def list_bags(api, a):
    d = await api.get('/api/playback/bags')
    return _round(d)


def _texts(v):
    """_text() all the way down (option lists carry bilingual labels)."""
    if isinstance(v, dict):
        t = _text(v)
        return t if t is not v else {k: _texts(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_texts(x) for x in v]
    return v


BATCH_STATE_KEYS = ('id', 'state', 'phase', 'index', 'n', 'preset', 'slam', 'speed', 'map', 'variant', 'localizer',
                    'speed_scale', 'elapsed', 'reason', 'summary')


async def _safe(coro):
    try:
        return await coro
    except Exception as e:  # noqa: BLE001  one missing part must not hide the others
        detail = getattr(e, 'detail', None)
        return {'error': str(detail if detail is not None else e)[:300]}


async def get_experiment_options(api, a):
    out = {}
    for key, base in (('mapping_batch', '/api/batch'), ('nav_batch', '/api/nav-batch')):
        opts = await _safe(api.get(base + '/options'))
        status = await _safe(api.get(base + '/status'))
        if isinstance(status, dict) and 'error' not in status:
            status = _pick(status, BATCH_STATE_KEYS) or {'state': 'idle'}
        out[key] = {'options': _texts(opts), 'current': _round(status)}
    maps = await _safe(api.get('/api/navigation/maps'))
    if isinstance(maps, list):
        rows = []
        for m in maps[:40]:
            row = {'name': m.get('name'), 'scene': m.get('preset') or None}
            if row['scene']:
                goals = await _safe(api.get(f'/api/navigation/maps/{q(m.get("name"))}/goals'))
                row['saved_goals'] = len(goals.get('goals') or []) if 'error' not in goals else 0
            rows.append(row)
        out['navigation_maps'] = rows
        out['note'] = ('A navigation batch needs a map with a scene (simulation world) and goals: the saved goals of '
                       'the map are used when none are given.')
    else:
        out['navigation_maps'] = maps
    return out


RUN_ID = {'type': 'string', 'description': 'Run id from list_runs, e.g. map_20261114_085817_c56de97664e4'}
TOOLS = [
    {'name': 'list_maps', 'handler': list_maps,
     'description': 'List saved maps: name, creation time, source (simulation or bag), algorithm, point count, '
                    '2D grid size, navigability and problems.',
     'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'get_map', 'handler': get_map,
     'description': 'Details of one saved map: metadata (meta.json), the run that built it, the SLAM command, grid, '
                    'extent, files.',
     'schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                'additionalProperties': False}},
    {'name': 'list_runs', 'handler': list_runs,
     'description': 'List run records, newest first. Types: mapping (simulation SLAM runs), playback (SLAM fed '
                    'from a bag), navigation. Rows give algorithm, scene/bag, duration, length, ATE (ape_rmse) and '
                    'RPE (rpe_rmse) when evaluated, playback statistics (start_end, z_end, z_max_abs height drift, '
                    'ref_ape_rmse against wheel odometry) and changed parameters.',
     'schema': {'type': 'object', 'properties': {
         'type': {'type': 'string', 'enum': ['mapping', 'playback', 'navigation']},
         'contains': {'type': 'string', 'description': 'Substring of id, algorithm, scene or bag name'},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 60}}, 'additionalProperties': False}},
    {'name': 'get_run', 'handler': get_run,
     'description': 'Everything about one run: run.json info, the SLAM parameters it used (slam_config, if recorded), '
                    'evo ATE/RPE statistics (eval.json), deviation from reference odometry, navigation results, and a '
                    'trajectory summary (distance, start-end distance, height z at distance checkpoints, z drift per '
                    '100 m, steepest z change over 2 m).',
     'schema': {'type': 'object', 'properties': {'run_id': RUN_ID}, 'required': ['run_id'],
                'additionalProperties': False}},
    {'name': 'get_trajectory', 'handler': get_trajectory,
     'description': 'Evenly spaced samples of a run\'s estimated trajectory (t, driven distance, x, y, z) to see where '
                    'drift happens.',
     'schema': {'type': 'object', 'properties': {'run_id': RUN_ID,
                                                 'samples': {'type': 'integer', 'minimum': 10, 'maximum': 300}},
                'required': ['run_id'], 'additionalProperties': False}},
    {'name': 'compare_runs', 'handler': compare_runs,
     'description': 'Compare 2-8 runs side by side: ATE/RPE, distance, loop closure, height drift, and SLAM '
                    'parameters that differ between them.',
     'schema': {'type': 'object', 'properties': {'run_ids': {'type': 'array', 'items': {'type': 'string'},
                                                             'minItems': 2, 'maxItems': 8}},
                'required': ['run_ids'], 'additionalProperties': False}},
    {'name': 'list_batches', 'handler': list_batches,
     'description': 'Batch experiments (the same scene and SLAM repeated N times) with mean/std/min/max of ATE, RPE, '
                    'length, duration, real-time factor. kind: mapping (default) or navigation (planner/controller '
                    'variants, success rate, time, path length, goal error).',
     'schema': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['mapping', 'navigation']},
                                                 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 40}},
                'additionalProperties': False}},
    {'name': 'get_batch', 'handler': get_batch,
     'description': 'One batch experiment with all its repetitions.',
     'schema': {'type': 'object', 'properties': {'batch_id': {'type': 'string'},
                                                 'kind': {'type': 'string', 'enum': ['mapping', 'navigation']}},
                'required': ['batch_id'], 'additionalProperties': False}},
    {'name': 'get_slam_config', 'handler': get_slam_config,
     'description': 'Current effective SLAM parameters of a SLAM stack (layers: base file, values derived from '
                    'sensor facts, user changes), with labels and allowed ranges. stack: a SLAM stack name such as '
                    'mapping_fastlio, mapping_fastlio_ground, mapping_fastlio_lidar, mapping_fastlio_lidar_ground, '
                    'mapping_glim, mapping_glim_lidar. bag: optional bag path (as in list_bags) to get the parameters '
                    'for that bag\'s sensor profile.',
     'schema': {'type': 'object', 'properties': {'stack': {'type': 'string'}, 'bag': {'type': 'string'}},
                'required': ['stack'], 'additionalProperties': False}},
    {'name': 'get_stack_log', 'handler': get_stack_log,
     'description': 'State and the last lines of the output log of a process the console started (stacks: sim, '
                    'mapping_fastlio, mapping_glim, playback, navigation, waypoints, ...). Only processes started since '
                    'the console started have a log.',
     'schema': {'type': 'object', 'properties': {'stack': {'type': 'string'},
                                                 'lines': {'type': 'integer', 'minimum': 10, 'maximum': 400}},
                'required': ['stack'], 'additionalProperties': False}},
    {'name': 'get_console_status', 'handler': get_console_status,
     'description': 'Live state: processes (running or not, exit codes, how they stopped), topic rates against '
                    'expected rates, TF transforms, CPU and memory, simulation clock.',
     'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'list_sensor_profiles', 'handler': list_sensor_profiles,
     'description': 'Sensor profiles of real robots used for bag playback: topics, point time field and unit, ring and '
                    'intensity fields, IMU acceleration unit, extrinsics (lidar_to_imu, imu_to_base, second lidar), '
                    'reference odometry topic.',
     'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'list_bags', 'handler': list_bags,
     'description': 'Recorded rosbag2 bags available for playback, with their topics and durations.',
     'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
]
TOOLS.append(
    {'name': 'get_experiment_options', 'handler': get_experiment_options,
     'description': 'What batch experiments can be started and what is running now: mapping batch options (scene '
                    'presets, SLAM stacks, max repetitions, simulation speeds), navigation batch options (Nav2 '
                    'variants, localizers, speed scale range), maps usable for navigation batches with their number '
                    'of saved goals, and the state of the current mapping and navigation batch. Read this before '
                    'start_mapping_batch or start_nav_batch.',
     'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}})
for _t in TOOLS:
    _t['kind'] = 'read'
BY_NAME = {t['name']: t for t in TOOLS}


def definitions(table=None):
    return [{k: t[k] for k in ('name', 'description', 'schema')} for t in (table or TOOLS)]


def _check_args(tool, args):
    schema = tool['schema']
    if not isinstance(args, dict) or '_invalid_arguments' in args:
        raise ToolError('arguments are not a JSON object')
    props = schema.get('properties') or {}
    for k in schema.get('required') or []:
        if k not in args:
            raise ToolError(f'missing argument: {k}')
    unknown = [k for k in args if k not in props]
    if unknown:
        raise ToolError(f'unknown arguments: {", ".join(unknown)}')
    for k, v in args.items():
        kind = props[k].get('type')
        ok = {'string': isinstance(v, str), 'integer': isinstance(v, int) and not isinstance(v, bool),
              'number': isinstance(v, (int, float)) and not isinstance(v, bool),
              'boolean': isinstance(v, bool), 'array': isinstance(v, list)}.get(kind, True)
        if not ok:
            raise ToolError(f'argument {k} must be {kind}')
        if 'enum' in props[k] and v not in props[k]['enum']:
            raise ToolError(f'argument {k} must be one of {props[k]["enum"]}')
        if kind in ('integer', 'number') and ('minimum' in props[k] and v < props[k]['minimum']
                                             or 'maximum' in props[k] and v > props[k]['maximum']):
            raise ToolError(f'argument {k} out of range')


async def run(api, name, args, table=None):
    """(content text, is_error). Never raises: errors go back to the model as tool errors.
    table: {name: tool} to look the tool up in (default: the read tools of this module)."""
    tool = (table or BY_NAME).get(name)
    if tool is None:
        return f'Error: unknown tool {name}', True
    try:
        _check_args(tool, args)
        result = await tool['handler'](api, args)
        text = json.dumps(result, ensure_ascii=False, separators=(',', ':'), default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f'… [truncated, {len(text)} characters in total; ask for less, e.g. a ' \
                                              'smaller limit or one item]'
        return text, False
    except ToolError as e:
        return f'Error: {e}', True
    except Exception as e:  # noqa: BLE001  ApiCallError and anything else: tell the model what went wrong
        detail = getattr(e, 'detail', None)
        return f'Error: {json.dumps(detail, ensure_ascii=False) if detail is not None else e}'[:600], True
