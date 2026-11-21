"""Dynamic object removal plugin backend (plugin interface v1).

Routes under /api/ext/dynamic_removal/:
  GET  maps                       folder maps with a cloud; has_scans tells which can be cleaned
  POST run {map, params}          start a DUFOMap job (worker.py in the plugin's venv)
  POST jobs/{id}/save {name, grid}  save the static cloud as a new version of the map (console route
                                  POST /api/maps/{map}/derive; grid 'regrid' projects it again, 'keep' keeps
                                  the map's grid)
"""
import json
import math
import os
import re

from fastapi import Body

NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
LIMITS = {'resolution': (0.1, 0.05, 0.5), 'd_s': (0.2, 0.0, 1.0), 'min_range': (0.3, 0.0, 5.0),
          'max_range': (0.0, 0.0, 200.0)}
INT_LIMITS = {'d_p': (1, 0, 5), 'scan_stride': (1, 1, 20), 'threads': (0, 0, 64)}
TOOL = 'DUFOMap dynamic point removal (dynamic_removal plugin)'


def setup(ctx):
    @ctx.router.get('/maps')
    async def maps():
        out = []
        for m in await ctx.api.get('/api/maps'):
            if m.get('kind') != 'folder' or not m.get('has_cloud'):
                continue
            if 'has_scans' in m:
                has_scans = bool(m['has_scans'])
            else:
                try:
                    d = await ctx.api.get(f"/api/maps/{m['name']}")
                except ctx.ApiCallError:
                    continue
                has_scans = bool(d.get('scans'))
            out.append({'name': m['name'], 'created': m.get('created'), 'algorithm': m.get('algorithm'),
                        'points': m.get('points'), 'bag': m.get('bag'), 'has_grid': bool(m.get('has_grid')),
                        'has_scans': has_scans})
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
        return p

    @ctx.router.post('/run')
    async def run(body: dict = Body(...)):
        name = body.get('map')
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ctx.error(400, 'map_unavailable', name=str(name))
        try:
            d = await ctx.api.get(f'/api/maps/{name}')
        except ctx.ApiCallError as e:
            raise ctx.error(e.status, 'map_unavailable', name=name)
        cloud = os.path.join(d['location'], 'map.pcd')
        if not os.path.isfile(cloud):
            raise ctx.error(400, 'no_cloud', name=name)
        if not d.get('scans'):
            raise ctx.error(400, 'no_scans', name=name)
        params = parameters(body.get('params') or {})
        params.update(map=name, cloud=cloud, recording=d['scans']['recording'],
                      trajectory=d['scans']['trajectory'])
        return ctx.jobs.submit('clean', params, title=name, target=name)

    @ctx.router.post('/jobs/{jid}/save')
    async def save(jid: str, body: dict = Body(...)):
        job = ctx.jobs.get(jid)
        if job['state'] != 'succeeded':
            raise ctx.error(409, 'not_finished')
        new_name = body.get('name')
        if not isinstance(new_name, str) or not NAME_RE.match(new_name):
            raise ctx.error(400, 'bad_name')
        grid = body.get('grid', 'regrid')
        if grid not in ('keep', 'regrid'):
            raise ctx.error(400, 'bad_params', reason='grid')
        folder = ctx.jobs.dir_of(jid)
        try:
            with open(os.path.join(folder, 'result.json'), encoding='utf-8') as f:
                result = json.load(f)
        except (OSError, ValueError) as e:
            raise ctx.error(500, 'run_failed', reason=f'result.json: {e}')
        cloud_path = os.path.join(folder, 'static.pcd')
        if not os.path.isfile(cloud_path):
            raise ctx.error(500, 'run_failed', reason='static.pcd missing')
        note = {k: result[k] for k in ('scans', 'points', 'dynamic', 'dynamic_ratio', 'params')}
        note['job'] = jid
        try:
            return await ctx.api.post(f"/api/maps/{job['params']['map']}/derive", data={
                'new_name': new_name, 'tool': TOOL, 'grid_mode': grid, 'note': json.dumps(note)},
                files={'cloud': ('static.pcd', cloud_path)})
        except ctx.ApiCallError as e:
            detail = e.detail if isinstance(e.detail, dict) else {'code': 'map_derive_failed',
                                                                  'params': {'reason': str(e.detail)}}
            raise ctx.console_error(e.status, detail.get('code', 'map_derive_failed'), **(detail.get('params') or {}))
