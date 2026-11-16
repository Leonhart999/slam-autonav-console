"""Bounded pcd2grid previews and immutable new map versions."""
import asyncio
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from .map_catalog import scan_source
from .map_library import NAME_RE


DEFAULT_PARAMS = {'res': 0.05, 'obs_min': 0.1, 'obs_max': 0.7, 'ground_tol': 0.05,
                  'ground_mode': 'plane', 'min_points': 2, 'tile': 5.0, 'voxel': 0.0,
                  'inflate': 0.0, 'closing_iters': 2, 'max_cells': 4000000}
AUTO_RESOLUTIONS = (0.05, 0.1, 0.2, 0.5)   # m, an imported cloud gets the finest one that fits max_cells
WORKER_TIMEOUT = 120                        # s
SCAN_TIMEOUT = 600                          # s, the scan method reads the whole recording
# Method 'scans': per-cell evidence from the recorded scans (fastlio_loop_grid.py); key -> (default, min, max)
SCAN_LIMITS = {'res': (0.05, 0.02, 1), 'obs_min': (0.1, -5, 5), 'obs_max': (0.7, -5, 5),
               'ground_tol': (0.05, 0.001, 1), 'map_range': (20.0, 2, 100), 'min_span': (1.0, 0, 20),
               'occ_ratio': (0.1, 0, 1), 'robot_radius': (0.3, 0, 2)}
SCAN_INT_LIMITS = {'min_hits': (4, 1, 100), 'closing_iters': (2, 0, 5)}


def fail(reason, status=400):
    return HTTPException(status, detail={'code': 'map_operation_failed', 'params': {'reason': str(reason)}})


def run_worker(python, cloud, out_dir, params, timeout=WORKER_TIMEOUT, **job):
    """Run regrid_worker.py (pcd2grid, or the scan method when job has method='scans') in its own process;
    writes map.pgm / map.yaml / result.json into out_dir and returns result.json. Raises RuntimeError with
    the worker's last error line."""
    out_dir = Path(out_dir)
    (out_dir / 'job.json').write_text(json.dumps({'cloud': str(cloud), 'out_dir': str(out_dir), 'params': params,
                                                  **job}), encoding='utf-8')
    worker = Path(__file__).with_name('regrid_worker.py')
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    try:
        process = subprocess.run([str(python), '-I', str(worker), str(out_dir / 'job.json')],
                                 cwd='/tmp', env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'Projection took longer than {timeout} s')
    if process.returncode:
        raise RuntimeError((process.stderr.strip().splitlines() or ['Projection failed'])[-1])
    return json.loads((out_dir / 'result.json').read_text())


def auto_params(extent_xy):
    """Default projection parameters for a cloud spanning extent_xy = (dx, dy) m: the finest resolution of
    AUTO_RESOLUTIONS whose grid (plus pcd2grid's margin) stays under max_cells."""
    p = dict(DEFAULT_PARAMS)
    dx, dy = (max(float(v), 0.0) + 2.0 for v in extent_xy)
    p['res'] = next((r for r in AUTO_RESOLUTIONS if (dx / r) * (dy / r) <= p['max_cells']), AUTO_RESOLUTIONS[-1])
    return p


class Regrid:
    def __init__(self, catalog, cfg):
        self.catalog = catalog
        self.root = catalog.root
        self.stage = self.root / '.regrid'
        self.python = Path(cfg['server']['venv']).expanduser() / 'bin/python'
        self.lock = threading.Lock()

    def scan_parameters(self, body):
        """Parameters of the scan method, named as fastlio_loop_grid.py expects them."""
        if set(body) - set(SCAN_LIMITS) - set(SCAN_INT_LIMITS) - {'method'}:
            raise fail('Unsupported projection parameter')
        p = {}
        for key, (default, lo, hi) in SCAN_LIMITS.items():
            v = body.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise fail(f'{key} must be between {lo} and {hi}')
            p[key] = float(v)
        for key, (default, lo, hi) in SCAN_INT_LIMITS.items():
            v = body.get(key, default)
            if type(v) is not int or not lo <= v <= hi:
                raise fail(f'{key} must be an integer between {lo} and {hi}')
            p[key] = v
        if p['obs_min'] >= p['obs_max']:
            raise fail('Obstacle minimum height must be less than maximum height')
        p['closing'] = p.pop('closing_iters')
        return p

    def parameters(self, body):
        if not isinstance(body, dict):
            raise fail('Projection parameters must be an object')
        if body.get('method', 'cloud') not in ('cloud', 'scans'):
            raise fail('Unsupported projection method')
        body = {k: v for k, v in body.items() if k != 'method'}
        limits = {'res': (0.01, 1), 'obs_min': (-100, 100), 'obs_max': (-100, 100),
                  'ground_tol': (0.001, 1), 'tile': (0.5, 50), 'voxel': (0, 0.5),
                  'inflate': (0, 2), 'ground_cell': (0.1, 5), 'ground_smooth': (0, 5),
                  'min_obstacle_area': (0, 10), 'min_free_area': (0, 10)}
        if set(body) - set(limits) - {'experimental', 'ground_mode', 'min_points', 'closing_iters'}:
            raise fail('Unsupported projection parameter')
        experimental = body.get('experimental', False)
        if not isinstance(experimental, bool):
            raise fail('experimental must be a boolean')
        p = dict(DEFAULT_PARAMS)
        for key, (lo, hi) in limits.items():
            if key in body:
                v = body[key]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                    raise fail(f'{key} must be between {lo} and {hi}')
                p[key] = float(v)
        for key, lo, hi in [('min_points', 1, 100), ('closing_iters', 0, 5)]:
            if key in body:
                v = body[key]
                if type(v) is not int or not lo <= v <= hi:
                    raise fail(f'{key} must be an integer between {lo} and {hi}')
                p[key] = v
        mode = body.get('ground_mode', 'plane')
        if mode not in ('plane', 'grid', 'none', 'heightmap'):
            raise fail('Unsupported ground model')
        p['ground_mode'] = mode
        exp_keys = ('ground_cell', 'ground_smooth', 'min_obstacle_area', 'min_free_area')
        if not experimental and (mode == 'heightmap' or any(k in body for k in exp_keys)):
            raise fail('Enable experimental features before using heightmap or component filtering')
        if p['obs_min'] >= p['obs_max']:
            raise fail('Obstacle minimum height must be less than maximum height')
        # Never rotate the cloud here: all assets must retain their navigation frame.
        return p, experimental

    def preview(self, name, body, progress=lambda phase: None):
        scans = isinstance(body, dict) and body.get('method') == 'scans'
        if scans:
            p, experimental = self.scan_parameters(body), False
        else:
            p, experimental = self.parameters(body)
        if not self.lock.acquire(blocking=False):
            raise fail('Another projection/save is in progress', 409)
        out = None
        try:
            progress('reading')
            kind, path = self.catalog._locate(name)
            if kind != 'folder':
                raise fail('A folder map with a 3D point cloud is required')
            cloud = path / 'map.pcd'
            if not cloud.is_file() or cloud.is_symlink() or path.is_symlink():
                raise fail('The original map.pcd is missing or is a symbolic link')
            source = None
            if scans:
                meta_file = path / 'meta.json'
                source = scan_source(path, json.loads(meta_file.read_text()) if meta_file.is_file() else {})
                if source is None:
                    raise fail('This map has no scan recording; use the point-cloud method')
            self.stage.mkdir(exist_ok=True)
            # Preview files are disposable, separate from saved maps.
            for child in self.stage.iterdir():
                if child.is_dir() and not child.is_symlink() and time.time() - child.stat().st_mtime > 3600:
                    shutil.rmtree(child)
            if len(list(self.stage.iterdir())) >= 8:
                raise fail('Too many pending previews; wait for old previews to expire', 409)
            out = self.stage / uuid.uuid4().hex
            out.mkdir()
            signature = [cloud.stat().st_ino, cloud.stat().st_size, cloud.stat().st_mtime_ns]
            try:
                progress('projecting')
                if source:
                    result = run_worker(self.python, cloud, out, p, SCAN_TIMEOUT, method='scans',
                                        recording=str(source[0]), trajectory=str(source[1]))
                else:
                    result = run_worker(self.python, cloud, out, p)
            except RuntimeError as exc:
                raise fail(exc)
            record = {**result, 'token': out.name, 'parent': name, 'params': p,
                      'experimental': experimental, 'signature': signature}
            progress('writing')
            (out / 'record.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            with Image.open(out / 'map.pgm') as im:
                im.save(out / 'grid.png')
            return {k: v for k, v in record.items() if k != 'signature'}
        except HTTPException:
            if out:
                shutil.rmtree(out, ignore_errors=True)
            raise
        except Exception as exc:
            if out:
                shutil.rmtree(out, ignore_errors=True)
            raise fail(exc) from exc
        finally:
            self.lock.release()

    def resolve(self, token):
        if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise fail('Invalid preview identifier')
        out = self.stage / token
        if not (out / 'record.json').is_file():
            raise fail('Preview expired; generate a new preview', 404)
        return out

    def save(self, token, name):
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise fail('Use 1–64 letters, digits, underscores or hyphens for the map name')
        with self.lock:
            out = self.resolve(token)
            record = json.loads((out / 'record.json').read_text())
            kind, source = self.catalog._locate(record['parent'])
            cloud = source / 'map.pcd'
            if kind != 'folder' or cloud.is_symlink() or source.is_symlink():
                raise fail('Source map is not available')
            stat = cloud.stat()
            if [stat.st_ino, stat.st_size, stat.st_mtime_ns] != record['signature']:
                raise fail('Source cloud changed; generate a new preview')
            dest = self.root / name
            if dest.exists() or (self.root / (name + '.yaml')).exists():
                raise fail('Map name already exists; choose a new version name', 409)
            try:
                dest.mkdir()
            except FileExistsError:
                raise fail('Map name already exists', 409)
            try:
                for filename in ('map.pcd', 'map_voxel.pcd'):
                    src = source / filename
                    if src.is_file() and not src.is_symlink():
                        try:
                            os.link(src, dest / filename)
                        except OSError:
                            shutil.copy2(src, dest / filename)
                for filename in ('map.pgm',):
                    shutil.copy2(out / filename, dest / filename)
                # Waypoints stay valid: the new grid keeps the cloud's (= the map's) frame
                if (source / 'goals.yaml').is_file() and not (source / 'goals.yaml').is_symlink():
                    shutil.copy2(source / 'goals.yaml', dest / 'goals.yaml')
                shutil.copy2(out / 'grid.png', dest / 'map_preview.png')
                original = source / 'meta.json'
                meta = json.loads(original.read_text()) if original.is_file() else {}
                # The per-scan trajectory travels with the version, so the scan method stays available.
                scans = scan_source(source, meta)
                if scans:
                    (dest / 'data').mkdir()
                    shutil.copy2(scans[1], dest / 'data' / scans[1].name)
                meta.update(name=name, created=time.strftime('%Y-%m-%d %H:%M:%S'), source='regrid',
                            parent=record['parent'], grid=record['grid'], project_error=None,
                            figures_error=None, regrid=record,
                            files={'map.pcd': 'Original cloud', 'map_voxel.pcd': 'Cloud preview',
                                   'map.pgm': 'Reprojected occupancy grid', 'map.yaml': 'Nav2 map metadata'})
                (dest / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
                # map.yaml is the publication marker; no existing version is modified.
                shutil.copy2(out / 'map.yaml', dest / 'map.yaml')
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
                raise
            detail = self.catalog.detail(name)
            shutil.rmtree(out)
            return detail


def register_regrid_routes(app, catalog, cfg):
    from .tasks import tasks_for
    service = Regrid(catalog, cfg)
    tasks = tasks_for(app, cfg)
    tasks.register('regrid', lambda target, params, progress: service.preview(target, params, progress))

    @app.post('/api/maps/{name}/regrid')
    async def preview(name: str, body: dict, background: bool = False):
        try:
            if background:
                # Reject malformed inputs immediately, before acknowledging a durable job.
                if body.get('method') == 'scans':
                    service.scan_parameters(body)
                else:
                    service.parameters(body)
                catalog._locate(name)
                return tasks.submit('regrid', name, body)
            return await asyncio.to_thread(service.preview, name, body)
        except HTTPException:
            raise
        except Exception as exc:
            raise fail(exc) from exc

    @app.delete('/api/maps/regrid/{token}')
    def discard(token: str):
        with service.lock:
            out = service.resolve(token)
            shutil.rmtree(out)
        return {'ok': True}

    @app.get('/api/maps/regrid/{token}/grid.png')
    def image(token: str):
        return FileResponse(service.resolve(token) / 'grid.png', media_type='image/png',
                            headers={'Cache-Control': 'no-store'})

    @app.post('/api/maps/regrid/{token}/save')
    def save(token: str, body: dict):
        try:
            detail = service.save(token, body.get('name'))
            try:
                tasks.mark_saved_preview(token, detail['name'])
            except OSError:
                # Publishing the map already succeeded; do not report it as a failed save.
                import logging
                logging.getLogger(__name__).exception('Cannot update saved projection task')
            return detail
        except HTTPException:
            raise
        except Exception as exc:
            raise fail(exc) from exc

    return service
