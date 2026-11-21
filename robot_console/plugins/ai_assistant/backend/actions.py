"""Run tools of the assistant: they start or stop experiments and compute evaluations, so they change console state.

They are only offered when the user allows it in the chat panel (access "ask": every call is shown to the user to
approve first; "auto": they run directly). Each one is a single POST to a console route the web page also uses, so
the console's own checks apply (a busy simulation, a batch already running, unknown presets, E-STOP).
"""
from . import tools
from .tools import ToolError, _pick, _round, _stats, q

BATCH_KEYS = tools.BATCH_STATE_KEYS + ('created', 'save_maps', 'goals')


async def evaluate_run(api, a):
    params = {'force': 'true'} if a.get('force') else None
    d = await api.post(f'/api/evaluation/runs/{q(a["run_id"])}/evaluate', params=params)
    out = {'run_id': a['run_id'], 'evaluated': True}
    if isinstance(d, dict):
        if isinstance(d.get('eval'), dict):
            out['eval'] = _stats(d['eval'])
        if isinstance(d.get('ref_eval'), dict):
            out['reference_deviation'] = _stats(d['ref_eval'])
    return out


async def start_mapping_batch(api, a):
    body = {'preset': a['preset'], 'slam': a['slam'], 'n': a['n'], 'speed': a.get('speed', 1),
            'save_maps': bool(a.get('save_maps', False))}
    d = await api.post('/api/batch/start', body)
    return {'started': True, 'batch': _round(_pick(d, BATCH_KEYS)),
            'note': 'The batch runs in the background; follow it on the Evaluation page (batch panel) or read it '
                    'later with get_batch.'}


async def start_nav_batch(api, a):
    goals = a.get('goals')
    if not goals:
        saved = await api.get(f'/api/navigation/maps/{q(a["map"])}/goals')
        goals = saved.get('goals') or []
        if not goals:
            raise ToolError(f'map {a["map"]} has no saved goals: give goals, or save them on the Navigation page')
    body = {'map': a['map'], 'variant': a.get('variant') or None, 'localizer': a.get('localizer', 'amcl'),
            'goals': goals, 'n': a['n'], 'speed_scale': a.get('speed_scale', 1.0)}
    d = await api.post('/api/nav-batch/start', body)
    out = _pick(d, BATCH_KEYS)
    out['goals'] = len(out.get('goals') or goals)
    return {'started': True, 'batch': _round(out),
            'note': 'The navigation batch runs in the background; follow it on the Evaluation page or read it later '
                    'with get_batch (kind navigation).'}


async def cancel_batch(api, a):
    path = '/api/batch/cancel' if (a.get('kind') or 'mapping') == 'mapping' else '/api/nav-batch/cancel'
    d = await api.post(path)
    return {'cancelled': True, 'batch': _round(_pick(d, tools.BATCH_STATE_KEYS))}


GOAL = {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'yaw': {'type': 'number'}},
        'required': ['x', 'y', 'yaw'], 'additionalProperties': False}

ACTIONS = [
    {'name': 'evaluate_run', 'handler': evaluate_run,
     'description': 'RUN TOOL. Compute the evo evaluation of a run that has ground truth or reference odometry but '
                    'no evaluation yet (ape_rmse empty in list_runs), or recompute it with force=true. Returns ATE/RPE.',
     'schema': {'type': 'object', 'properties': {'run_id': tools.RUN_ID, 'force': {'type': 'boolean'}},
                'required': ['run_id'], 'additionalProperties': False}},
    {'name': 'start_mapping_batch', 'handler': start_mapping_batch,
     'description': 'RUN TOOL. Start a mapping batch experiment: the simulation drives the scene\'s route n times '
                    'with one SLAM stack and evaluates every run (takes minutes per run). Preset, slam, n and speed '
                    'must come from get_experiment_options. Only one batch runs at a time and the simulation must be '
                    'free.',
     'schema': {'type': 'object', 'properties': {
         'preset': {'type': 'string', 'description': 'Scene preset name, e.g. office'},
         'slam': {'type': 'string', 'description': 'SLAM stack name, e.g. mapping_fastlio'},
         'n': {'type': 'integer', 'minimum': 1, 'maximum': 50},
         'speed': {'type': 'number', 'description': 'Simulation speed target, one of the offered speeds (default 1)'},
         'save_maps': {'type': 'boolean', 'description': 'Also save the map of every run (default false)'}},
         'required': ['preset', 'slam', 'n'], 'additionalProperties': False}},
    {'name': 'start_nav_batch', 'handler': start_nav_batch,
     'description': 'RUN TOOL. Start a navigation batch: Nav2 drives through the goals on a map n times in '
                    'simulation with one planner/controller variant and localizer. Without goals the map\'s saved '
                    'goals are used. Values must come from get_experiment_options.',
     'schema': {'type': 'object', 'properties': {
         'map': {'type': 'string'},
         'variant': {'type': 'string', 'description': 'Nav2 variant, e.g. navfn_dwb (default: the console default)'},
         'localizer': {'type': 'string', 'enum': ['amcl', 'gt']},
         'n': {'type': 'integer', 'minimum': 1, 'maximum': 50},
         'speed_scale': {'type': 'number'},
         'goals': {'type': 'array', 'items': GOAL, 'description': 'Goals in map coordinates (m, rad)'}},
         'required': ['map', 'n'], 'additionalProperties': False}},
    {'name': 'cancel_batch', 'handler': cancel_batch,
     'description': 'RUN TOOL. Cancel the running mapping or navigation batch (the current run is stopped).',
     'schema': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['mapping', 'navigation']}},
                'required': ['kind'], 'additionalProperties': False}},
]
for _a in ACTIONS:
    _a['kind'] = 'run'

ALL = tools.TOOLS + ACTIONS
BY_NAME = {t['name']: t for t in ALL}


def kind(name):
    t = BY_NAME.get(name)
    return t['kind'] if t else None


def definitions():
    return tools.definitions(ALL)


async def run(api, name, args):
    return await tools.run(api, name, args, BY_NAME)
