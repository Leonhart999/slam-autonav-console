"""Ground segmentation plugin backend (plugin interface v1).

Routes under /api/ext/ground_seg/:
  GET  maps                 folder maps with a cloud and a 2D grid, and which sources they offer
  POST run {map, source, params}   start a segmentation job (worker.py in the plugin's venv)
  POST jobs/{id}/save {name}       save the job's grid as a new version of the map (console route
                                   POST /api/maps/{map}/derive, grid_mode replace)
Job progress, results, images and clouds come from the generic job routes (/jobs, /jobs/{id}/files/...).
"""
import json
import math
import os
import re

from fastapi import Body

NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
# key: (default, min, max); sensor_height 0 = estimate from the data
LIMITS = {
    'sensor_height': (0.0, 0.0, 5.0), 'min_range': (0.3, 0.0, 10.0), 'max_range': (30.0, 5.0, 120.0),
    'radius': (20.0, 5.0, 60.0), 'step': (1.0, 0.2, 10.0), 'voxel': (0.05, 0.02, 0.5),
    'obs_min': (0.1, -1.0, 3.0), 'obs_max': (0.7, 0.0, 5.0), 'fill': (2.0, 0.0, 20.0),
}
INT_LIMITS = {'min_points': (2, 1, 100), 'closing': (2, 0, 5), 'scan_stride': (1, 1, 50)}
TOOL = 'Patchwork++ ground segmentation (ground_seg plugin)'


def setup(ctx):
    def trajectory_of(detail):
        data = os.path.join(detail['location'], 'data')
        for name in ('scan_poses.tum', 'est.tum'):
            if os.path.isfile(os.path.join(data, name)):
                return os.path.join(data, name)
        return None

    async def detail_of(name):
        try:
            return await ctx.api.get(f'/api/maps/{name}')
        except ctx.ApiCallError as e:
            raise ctx.error(e.status, 'map_unavailable', name=name)

    @ctx.router.get('/maps')
    async def maps():
        out = []
        for m in await ctx.api.get('/api/maps'):
            if m.get('kind') != 'folder' or not m.get('has_cloud') or not m.get('has_grid'):
                continue
            if 'has_scans' in m and 'has_trajectory' in m:
                has_scans, has_traj = bool(m['has_scans']), bool(m['has_trajectory'])
            else:
                try:
                    d = await ctx.api.get(f"/api/maps/{m['name']}")
                except ctx.ApiCallError:
                    continue
                has_scans, has_traj = bool(d.get('scans')), trajectory_of(d) is not None
            out.append({'name': m['name'], 'created': m.get('created'), 'algorithm': m.get('algorithm'),
                        'points': m.get('points'), 'grid': m.get('grid'), 'bag': m.get('bag'),
                        'has_scans': has_scans, 'has_trajectory': has_traj})
        return out

    def parameters(body):
        if not isinstance(body, dict):
            raise ctx.error(400, 'bad_params', reason='not an object')
        p = {}
        for key, (default, lo, hi) in LIMITS.items():
            v = body.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ctx.error(400, 'bad_params', reason=f'{key} must be between {lo} and {hi}')
            p[key] = float(v)
        for key, (default, lo, hi) in INT_LIMITS.items():
            v = body.get(key, default)
            if type(v) is not int or not lo <= v <= hi:
                raise ctx.error(400, 'bad_params', reason=f'{key} must be an integer between {lo} and {hi}')
            p[key] = v
        if p['obs_min'] >= p['obs_max']:
            raise ctx.error(400, 'bad_params', reason='obs_min must be below obs_max')
        return p

    @ctx.router.post('/run')
    async def run(body: dict = Body(...)):
        name = body.get('map')
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ctx.error(400, 'map_unavailable', name=str(name))
        d = await detail_of(name)
        if not d.get('has_grid') or not d.get('yaml_path') or not os.path.isfile(os.path.join(d['location'], 'map.pcd')):
            raise ctx.error(400, 'map_incomplete', name=name)
        source = body.get('source', 'auto')
        if source not in ('auto', 'scans', 'map'):
            raise ctx.error(400, 'bad_params', reason='source')
        if source == 'auto':
            source = 'scans' if d.get('scans') else 'map'
        params = parameters(body.get('params') or {})
        params.update(map=name, source=source, map_yaml=d['yaml_path'],
                      cloud=os.path.join(d['location'], 'map.pcd'))
        if source == 'scans':
            if not d.get('scans'):
                raise ctx.error(400, 'no_scans', name=name)
            params.update(recording=d['scans']['recording'], trajectory=d['scans']['trajectory'])
        else:
            traj = trajectory_of(d)
            if traj is None:
                raise ctx.error(400, 'no_trajectory', name=name)
            params['trajectory'] = traj
        return ctx.jobs.submit('segment', params, title=name, target=name)

    @ctx.router.post('/jobs/{jid}/save')
    async def save(jid: str, body: dict = Body(...)):
        job = ctx.jobs.get(jid)
        if job['state'] != 'succeeded':
            raise ctx.error(409, 'not_finished')
        new_name = body.get('name')
        if not isinstance(new_name, str) or not NAME_RE.match(new_name):
            raise ctx.error(400, 'bad_name')
        folder = ctx.jobs.dir_of(jid)
        try:
            with open(os.path.join(folder, 'result.json'), encoding='utf-8') as f:
                result = json.load(f)
        except (OSError, ValueError) as e:
            raise ctx.error(500, 'segment_failed', reason=f'result.json: {e}')
        grid_path = os.path.join(folder, 'grid.bin')
        if not os.path.isfile(grid_path):
            raise ctx.error(500, 'segment_failed', reason='grid.bin missing')
        note = {'source': result['source'], 'sensor_height': result.get('sensor_height'),
                'params': result['params'], 'cells': result['cells'], 'changes': result['changes'], 'job': jid}
        try:
            return await ctx.api.post(f"/api/maps/{job['params']['map']}/derive", data={
                'new_name': new_name, 'tool': TOOL, 'grid_mode': 'replace', 'note': json.dumps(note)},
                files={'grid': ('grid.bin', grid_path)})
        except ctx.ApiCallError as e:
            detail = e.detail if isinstance(e.detail, dict) else {'code': 'map_derive_failed',
                                                                  'params': {'reason': str(e.detail)}}
            raise ctx.console_error(e.status, detail.get('code', 'map_derive_failed'), **(detail.get('params') or {}))
