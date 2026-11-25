"""Loop optimization of a saved FAST-LIO2 map (Maps page, "Loop optimization").

Runs the offline planar loop back-end (fastlio_loop_backend.py) on the raw scans the map was built from, as a
background task, and keeps the result in a disposable staging folder. The page shows the loop table and the
figures; saving publishes a new map and a new run (fastlio_loop_export.py). The source map, its run and the
scan recording are never changed.

  GET    /api/maps/loop/env                    is the back-end interpreter usable (GTSAM, small_gicp)?
  POST   /api/maps/{name}/loop?background=true submit task 'loop'; its result is the record below (with token)
  GET    /api/maps/loop/{token}/figures/{figure}.png
  POST   /api/maps/loop/{token}/save {name}    -> map detail of the new map (+ run id)
  DELETE /api/maps/loop/{token}

The back-end runs in its own interpreter (loop_backend.python: GTSAM 4.2 wheel + small_gicp), never inside the
console process, so the system GTSAM used by GLIM is untouched.
"""
import asyncio
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .map_catalog import loop_state, scan_source

# Dialog parameters: key -> (default, min, max). Keys of BACKEND_KEYS go to the back-end's --params.
LIMITS = {'loop_radius': (10.0, 2, 30), 'loop_min_gap': (100.0, 10, 1000), 'map_range': (20.0, 2, 50),
          'map_res': (0.05, 0.02, 0.5), 'accept_overlap': (0.6, 0.3, 0.95), 'accept_rmse': (0.15, 0.03, 0.5),
          'max_loop_shift': (5.0, 0.5, 20), 'max_loop_yaw_deg': (10.0, 1, 45)}
INT_LIMITS = {'scan_stride': (2, 1, 10)}
BACKEND_KEYS = ('loop_radius', 'loop_min_gap', 'accept_overlap', 'accept_rmse', 'max_loop_shift', 'max_loop_yaw_deg')
FIGURES = ('trajectory_correction', 'junction_before_after', 'map_top_view_before_after',
           'grid_odom_preview', 'grid_loop_preview')
SAVE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,48}$')   # the run of the new map is play_loop_<name>
KEEP_PENDING = 3          # unsaved results; each holds two full voxel maps (HEU61: ~130 MB each)
EXPIRE = 24 * 3600        # s, unsaved results are deleted after this
MIN_FREE = 2 << 30        # bytes free in the map library before a task starts
ENV_RECHECK = 60          # s, a failed environment check is repeated after this
INSTALL_HINT = ('python3 -m venv ~/venvs/loop && '
                '~/venvs/loop/bin/pip install -r robot_console/install/requirements-loop.txt')


def fail(reason, status=400):
    return HTTPException(status, detail={'code': 'map_operation_failed', 'params': {'reason': str(reason)}})


def parameters(body):
    """Validated dialog parameters (all keys, defaults filled in)."""
    if not isinstance(body, dict):
        raise fail('Loop parameters must be an object')
    unknown = set(body) - set(LIMITS) - set(INT_LIMITS)
    if unknown:
        raise fail('Unsupported loop parameter: ' + ', '.join(sorted(unknown)))
    p = {}
    for key, (default, lo, hi) in LIMITS.items():
        v = body.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise fail(f'{key} must be between {lo} and {hi}')
        p[key] = float(v)
    for key, (default, lo, hi) in INT_LIMITS.items():
        v = body.get(key, default)
        if type(v) is not int or not lo <= v <= hi:
            raise fail(f'{key} must be an integer between {lo} and {hi}')
        p[key] = v
    return p


def backend_command(python, recording, poses, out, p):
    script = Path(__file__).with_name('fastlio_loop_backend.py')
    return [str(python), '-I', str(script), str(recording), str(out), '--poses', str(poses),
            '--map-res', str(p['map_res']), '--scan-stride', str(p['scan_stride']), '--map-range', str(p['map_range']),
            '--params', json.dumps({k: p[k] for k in BACKEND_KEYS})]


def file_signature(path):
    st = Path(path).stat()
    return [st.st_ino, st.st_size, st.st_mtime_ns]


def compact(report, token, parent, params, ground, elapsed):
    """What the dialog shows: summary, one row per checked loop, revisit metrics, available figures."""
    loops = [{'s_i': round(lp['s_i'], 1), 's_j': round(lp['s_j'], 1), 'dist': round(lp['dist'], 2),
              'overlap_before': round(lp['overlap_before'], 3), 'overlap_after': round(lp['overlap_after'], 3),
              'rmse_before': round(lp['rmse_before'], 3), 'rmse_after': round(lp['rmse_after'], 3),
              'shift_xy': round(math.hypot(*lp['shift_xyz'][:2]), 3), 'yaw_deg': round(lp['drift_rpy_deg'][2], 3),
              'ok': bool(lp['ok']), 'reasons': list(lp['rejection_reasons'])} for lp in report.get('loops', [])]
    junctions = [{'s_i': round(j['s_i'], 1), 's_j': round(j['s_j'], 1),
                  'wall_before': j['wall_odom'], 'wall_after': j['wall_loop']} for j in report.get('junctions', [])]
    return {'token': token, 'parent': parent, 'params': params, 'ground': ground, 'elapsed': round(elapsed, 1),
            'scans': report['scans'], 'keyframes': report['keyframes'], 'path_m': round(report['path_m'], 1),
            'duration_s': round(report.get('duration_s', 0.0), 1), 'accepted': report['accepted'],
            'revisits': report.get('revisits', 0), 'end_correction': report.get('end_correction'),
            'max_correction': report.get('max_correction'), 'loops': loops, 'junctions': junctions,
            'maps': report.get('maps'), 'figures': []}


class LoopEnv:
    """Is the back-end interpreter there and does it import the numerical libraries? Cached."""

    def __init__(self, python):
        self.python = Path(python)
        self._lock = threading.Lock()
        self._state, self._checked = None, 0.0

    def check(self):
        with self._lock:
            if self._state and (self._state['installed'] or time.time() - self._checked < ENV_RECHECK):
                return dict(self._state)
            reason = None
            if not self.python.is_file():
                reason = f'{self.python} not found'
            else:
                try:
                    p = subprocess.run([str(self.python), '-I', '-c', 'import gtsam, small_gicp, scipy, matplotlib'],
                                       cwd='/tmp', stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                       timeout=60)
                    if p.returncode:
                        reason = (p.stderr.strip().splitlines() or ['import failed'])[-1]
                except (OSError, subprocess.TimeoutExpired) as e:
                    reason = str(e)
            self._state = {'installed': reason is None, 'python': str(self.python), 'reason': reason,
                           'install': INSTALL_HINT}
            self._checked = time.time()
            return dict(self._state)


class LoopService:
    def __init__(self, catalog, cfg):
        self.catalog = catalog
        self.root = catalog.root
        self.stage = self.root / '.loop'
        lb = cfg.get('loop_backend') or {}
        self.env = LoopEnv(Path(lb.get('python') or '~/venvs/loop/bin/python').expanduser())
        self.timeout = float(lb.get('timeout') or 1800)
        self.runs_dir = Path(cfg['mapping'].get('runs_dir', '~/runs')).expanduser()
        self.lock = threading.Lock()       # staging and saving; the back-end itself runs outside it
        self.tasks = None                  # set by register_loop_routes so worker subprocesses can be tracked

    # ---------------- source map ----------------

    def source(self, name):
        """(folder, meta, recording, poses, ground) of a map that can be optimized; HTTP 400 with the reason."""
        kind, path = self.catalog._locate(name)
        meta = {}
        if kind == 'folder' and (path / 'meta.json').is_file():
            try:
                meta = json.loads((path / 'meta.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                meta = {}
        folder = path if kind == 'folder' else None
        state = loop_state(folder, meta)
        if state['reason'] == 'no_scans':
            raise fail('This map has no recorded scans; switch Save raw scans on before a playback')
        if state['reason'] == 'already_optimized':
            raise fail('This map is already loop-optimized; optimize its source map instead')
        recording, poses = scan_source(folder, meta)
        return folder, meta, recording, poses, state['ground']

    # ---------------- staging ----------------

    def _prepare_stage(self):
        self.stage.mkdir(exist_ok=True)
        now = time.time()
        for child in self.stage.iterdir():
            if child.is_dir() and not child.is_symlink() and now - child.stat().st_mtime > EXPIRE:
                shutil.rmtree(child, ignore_errors=True)
        pending = [c for c in self.stage.iterdir() if c.is_dir()]
        if len(pending) >= KEEP_PENDING:
            raise fail(f'{len(pending)} unsaved loop results are waiting; save or discard one first', 409)
        if shutil.disk_usage(self.root).free < MIN_FREE:
            raise fail('Less than 2 GB free next to the map library', 507)

    def resolve(self, token):
        if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise fail('Invalid result identifier')
        out = self.stage / token
        if not (out / 'record.json').is_file():
            raise fail('Result expired or already saved; run the optimization again', 404)
        return out

    def discard(self, token):
        with self.lock:
            shutil.rmtree(self.resolve(token))

    # ---------------- task ----------------

    def check(self, name, body):
        """Everything that can be refused before a task is acknowledged."""
        p = parameters(body)
        self.source(name)
        env = self.env.check()
        if not env['installed']:
            raise fail(f'Loop back-end environment is not installed ({env["reason"]}); run: {INSTALL_HINT}')
        return p

    def run(self, name, body, progress=lambda phase: None):
        p = self.check(name, body)
        folder, meta, recording, poses, ground = self.source(name)
        with self.lock:
            self._prepare_stage()
            out = self.stage / uuid.uuid4().hex
            out.mkdir()
        try:
            signature = {'poses': file_signature(poses), 'index': file_signature(recording / 'index.csv')}
            (out / 'job.json').write_text(json.dumps({'parent': name, 'params': p, 'recording': str(recording),
                                                      'poses': str(poses)}), encoding='utf-8')
            t0 = time.time()
            self._backend(backend_command(self.env.python, recording, poses, out / 'result', p), out, progress)
            progress('writing')
            report = json.loads((out / 'result' / 'loop_report.json').read_text())
            record = compact(report, out.name, name, p, ground, time.time() - t0)
            record['figures'] = [f for f in FIGURES if (out / 'result' / f'{f}.png').is_file()]
            (out / 'record.json').write_text(json.dumps({**record, 'signature': signature}, ensure_ascii=False),
                                             encoding='utf-8')
            return record
        except HTTPException:
            shutil.rmtree(out, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(out, ignore_errors=True)
            raise fail(exc) from exc

    def _backend(self, cmd, out, progress):
        """Run the back-end; stage lines become task phases, the full output goes to backend.log."""
        env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MPLBACKEND='Agg')
        tail = []
        with open(out / 'backend.log', 'w', encoding='utf-8') as log:
            proc = subprocess.Popen(cmd, cwd='/tmp', env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True)
            timed_out = threading.Event()

            def kill():
                timed_out.set()
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            timer = threading.Timer(self.timeout, kill)
            timer.start()
            ctx = self.tasks.track_process(proc) if self.tasks is not None else None
            try:
                if ctx is not None:
                    ctx.__enter__()
                for line in proc.stdout:
                    log.write(line)
                    line = line.strip()
                    if line.startswith('PHASE '):
                        progress(line[6:])
                    elif line:
                        tail = (tail + [line])[-20:]
                proc.wait()
            except BaseException as exc:
                if ctx is not None:
                    ctx.__exit__(type(exc), exc, exc.__traceback__)
                raise
            else:
                if ctx is not None:
                    ctx.__exit__(None, None, None)
            finally:
                timer.cancel()
        if timed_out.is_set():
            raise fail(f'Loop optimization took longer than {self.timeout:.0f} s')
        if proc.returncode:
            raise fail(tail[-1] if tail else f'Loop back-end exited with code {proc.returncode}')

    # ---------------- results ----------------

    def figure(self, token, name):
        if name not in FIGURES:
            raise fail('Unknown figure', 404)
        path = self.resolve(token) / 'result' / f'{name}.png'
        if not path.is_file():
            raise fail('Figure not available', 404)
        return path

    def save(self, token, name):
        from .fastlio_loop_export import publish
        if not isinstance(name, str) or not SAVE_NAME_RE.fullmatch(name):
            raise fail('Use 1–48 letters, digits, underscores or hyphens for the map name')
        with self.lock:
            out = self.resolve(token)
            record = json.loads((out / 'record.json').read_text())
            if record['accepted'] < 1:
                raise fail('No loop was accepted; there is nothing to save')
            folder, meta, recording, poses, _ = self.source(record['parent'])
            sig = record['signature']
            if file_signature(poses) != sig['poses'] or file_signature(recording / 'index.csv') != sig['index']:
                raise fail('The source map or its scans changed; run the optimization again')
            if (self.root / name).exists() or (self.root / (name + '.yaml')).exists():
                raise fail('Map name already exists; choose a new name', 409)
            run_id = 'play_loop_' + name
            if (self.runs_dir / run_id).exists():
                raise fail(f'Run {run_id} already exists; choose a new name', 409)
            source_run = self.runs_dir / str(meta.get('run') or '')
            source_run = source_run if meta.get('run') and (source_run / 'run.json').is_file() else None
            try:
                publish(out / 'result', self.root, name, source_run=source_run, runs_dir=self.runs_dir,
                        parent_map=record['parent'], provenance='same_replay', loop_params=record['params'],
                        quiet=True)
            except FileExistsError as exc:
                raise fail(f'Already exists: {exc}', 409)
            detail = self.catalog.detail(name)
            shutil.rmtree(out, ignore_errors=True)
            return {**detail, 'loop_run': run_id}


def register_loop_routes(app, catalog, cfg):
    from .tasks import tasks_for
    service = LoopService(catalog, cfg)
    tasks = tasks_for(app, cfg)
    service.tasks = tasks
    tasks.register('loop', lambda target, params, progress: service.run(target, params, progress))

    @app.get('/api/maps/loop/env')
    async def loop_env():
        return await asyncio.to_thread(service.env.check)

    @app.post('/api/maps/{name}/loop')
    async def loop_submit(name: str, body: dict, background: bool = True):
        try:
            # Reject bad input, unusable maps and a missing environment before acknowledging a durable job.
            await asyncio.to_thread(service.check, name, body)
            if background:
                return tasks.submit('loop', name, body)
            return await asyncio.to_thread(service.run, name, body)
        except HTTPException:
            raise
        except Exception as exc:
            code = getattr(exc, 'code', None)
            if code in ('map_not_found', 'invalid_map_name'):
                raise HTTPException(404 if code == 'map_not_found' else 400,
                                    detail={'code': code, 'params': getattr(exc, 'params', {})})
            raise fail(exc) from exc

    @app.get('/api/maps/loop/{token}/figures/{figure}.png')
    def loop_figure(token: str, figure: str):
        return FileResponse(service.figure(token, figure), media_type='image/png',
                            headers={'Cache-Control': 'no-store'})

    @app.delete('/api/maps/loop/{token}')
    def loop_discard(token: str):
        service.discard(token)
        return {'ok': True}

    @app.post('/api/maps/loop/{token}/save')
    def loop_save(token: str, body: dict):
        try:
            detail = service.save(token, body.get('name'))
        except HTTPException:
            raise
        except Exception as exc:
            raise fail(exc) from exc
        try:
            tasks.mark_saved_preview(token, detail['name'], kind='loop')
        except OSError:
            import logging
            logging.getLogger(__name__).exception('Cannot update saved loop task')
        return detail

    return service
