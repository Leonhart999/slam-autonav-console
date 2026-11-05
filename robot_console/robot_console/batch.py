"""Batch mapping experiments (evaluation page, design doc section 3.5).

One batch = N repetitions of: start sim(preset) -> SLAM -> waypoints -> wait for the route -> end the run record
-> (optionally save the map) -> stop waypoints, SLAM, sim -> wait until every process group is gone.
A run that does not finish within run_timeout is recorded as failed and the batch goes on. With speed > 1 the
sim's physics real_time_factor is raised right after it starts (sim time, sensor rates and the route stay the same,
only the wall clock runs shorter; the reached RTF is recorded per run). Cancel and E-STOP
abort the batch at once and clean up. After the last run every run record is evaluated with evo.

The batch record is ~/runs/batch_<time>/batch.json (the run records themselves stay ~/runs/map_<time>/).
Waypoint progress is parsed from waypoint_driver's log the same way as the mapping page (server.py).
"""
import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from fastapi import Body, HTTPException

from .map_library import SaveError
from .process_manager import StackRunning, _group_alive

WAYPOINT_RE = re.compile(r'到达航点 (\d+)/(\d+)')    # waypoint_driver's Chinese log, same as server.py
TOTAL_RE = re.compile(r'共 (\d+) 个航点')
BATCH_RE = re.compile(r'^batch_[0-9_]{8,40}$')
PHYSICS_RE = re.compile(r'^/world/[^/]+/set_physics$')
STEP_RE = re.compile(r'<max_step_size>\s*([0-9.eE+-]+)\s*</max_step_size>')
POLL = 0.5


class BatchError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


class Aborted(Exception):
    pass


class RunFailed(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def _mean_std(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
    return {'mean': round(m, 6), 'std': round(sd, 6), 'min': round(min(vals), 6), 'max': round(max(vals), 6),
            'n': len(vals)}


class BatchRunner:
    def __init__(self, cfg, node, procs, mapping, library, evaluation):
        b = cfg.get('batch') or {}
        self.cfg = cfg
        self.node, self.procs, self.mapping, self.library, self.evaluation = node, procs, mapping, library, evaluation
        self.presets = cfg['presets']
        st = mapping.stacks
        self.sim, self.waypoints = st.get('sim'), st.get('waypoints')
        self.slam_stacks = [s for s in (b.get('slam_stacks') or [st.get('slam')]) if procs.get(s)]
        self.run_timeout = float(b.get('run_timeout', 900))
        self.ready_timeout = float(b.get('ready_timeout', 120))
        self.max_runs = int(b.get('max_runs', 20))
        # Offered sim speed-ups (physics real_time_factor targets); 1 = real time, the default
        self.speeds = sorted({1.0, *(float(x) for x in (b.get('speeds') or [1]) if float(x) > 0)})
        self.gt_topic = cfg['mapping'].get('gt_topic') or '/ground_truth'
        self.runs_dir = Path(os.path.expanduser(cfg['mapping'].get('runs_dir', '~/runs')))
        # Everything that must be idle before a batch starts (a running sim/SLAM/Nav2 would be clobbered)
        self.guard = sorted({n for n in (self.sim, 'sim_gui', self.waypoints, 'navigation', *self.slam_stacks)
                             if n and procs.get(n)})
        self.lock = threading.Lock()
        self.abort_evt = threading.Event()
        self.thread = None
        self.batch = None          # dict written to batch.json
        self.live = {}             # progress not stored in batch.json

    # ---------------- options / status ----------------

    def options(self):
        presets = [{'name': n, 'label': p['label'], 'description': p['description']}
                   for n, p in self.presets.items() if p.get('waypoints') is not False and not p.get('custom')]
        slams = [{'name': n, 'label': self.procs.get(n).label} for n in self.slam_stacks]
        return {'presets': presets, 'slam': slams, 'max_runs': self.max_runs, 'run_timeout': self.run_timeout,
                'speeds': self.speeds}

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.lock:
            if self.batch is None:
                return {'state': 'idle'}
            out = json.loads(json.dumps(self.batch))
            out.update(self.live)
            if self.batch.get('started_wall'):
                out['elapsed'] = round((self.batch.get('ended_wall') or time.time()) - self.batch['started_wall'], 1)
            return out

    def history(self):
        out = []
        if self.runs_dir.is_dir():
            for d in sorted(self.runs_dir.glob('batch_*'), reverse=True):
                try:
                    b = json.loads((d / 'batch.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                out.append({k: b.get(k) for k in ('id', 'created', 'preset', 'slam', 'n', 'state', 'summary')}
                           | {'speed': b.get('speed', 1.0)}
                           | {'run_ids': [r.get('run_id') for r in b.get('runs', []) if r.get('run_id')]})
        return out

    def load(self, batch_id):
        if not BATCH_RE.fullmatch(batch_id or ''):
            raise BatchError('batch_not_found', 404, id=batch_id)
        if self.batch and self.batch.get('id') == batch_id:
            return self.status()
        try:
            return json.loads((self.runs_dir / batch_id / 'batch.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise BatchError('batch_not_found', 404, id=batch_id)

    # ---------------- start / abort ----------------

    def start(self, preset, slam, n, save_maps=False, speed=1.0):
        if preset not in self.presets or self.presets[preset].get('waypoints') is False:
            raise BatchError('batch_invalid', field='preset', value=str(preset))
        if slam not in self.slam_stacks:
            raise BatchError('batch_invalid', field='slam', value=str(slam))
        if not isinstance(n, int) or not 1 <= n <= self.max_runs:
            raise BatchError('batch_invalid', field='n', value=str(n))
        if not isinstance(speed, (int, float)) or float(speed) not in self.speeds:
            raise BatchError('batch_invalid', field='speed', value=str(speed))
        with self.lock:
            if self.running():
                raise BatchError('batch_running', 409, id=self.batch['id'])
            busy = [s for s in self.guard if self.procs.get(s).state() in ('running', 'stopping')]
            if busy:
                raise BatchError('batch_busy', 409, names=', '.join(busy))
            stamp = time.strftime('%Y%m%d_%H%M%S')
            batch_id, k = f'batch_{stamp}', 2
            while (self.runs_dir / batch_id).exists():
                batch_id, k = f'batch_{stamp}_{k}', k + 1
            self.abort_evt.clear()
            self.batch = {'id': batch_id, 'type': 'batch_mapping', 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                          'ended': None, 'state': 'running', 'preset': preset, 'slam': slam, 'n': n,
                          'save_maps': bool(save_maps), 'speed': float(speed), 'run_timeout': self.run_timeout,
                          'runs': [],
                          'summary': None, 'reason': None, 'started_wall': time.time(), 'ended_wall': None}
            self.live = {'index': 0, 'phase': 'starting', 'progress': None, 'run_started': None}
            try:
                (self.runs_dir / batch_id).mkdir(parents=True)
            except OSError as e:
                self.batch = None
                raise BatchError('batch_failed', 500, reason=str(e))
            self._write()
            self.thread = threading.Thread(target=self._main, daemon=True)
            self.thread.start()
        return self.status()

    def abort(self, reason='canceled'):
        """Cancel button and E-STOP. Returns at once; the batch thread cleans up."""
        if self.running():
            with self.lock:
                self.batch['reason'] = reason
            self.abort_evt.set()
            return True
        return False

    # ---------------- batch thread ----------------

    def _phase(self, phase, **extra):
        with self.lock:
            self.live.update(phase=phase, **extra)

    def _write(self):
        b = {k: v for k, v in self.batch.items() if k not in ('started_wall', 'ended_wall')}
        path = self.runs_dir / self.batch['id'] / 'batch.json'
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)

    def _wait(self, cond, timeout, fail_code, deadline=None):
        """Poll cond() until true. Raises Aborted, or RunFailed(fail_code) / RunFailed('run_timeout')."""
        end = time.monotonic() + timeout
        while True:
            if self.abort_evt.is_set():
                raise Aborted()
            if cond():
                return
            now = time.monotonic()
            if deadline is not None and now > deadline:
                raise RunFailed('run_timeout', seconds=int(self.run_timeout))
            if now > end:
                raise RunFailed(fail_code, seconds=int(timeout))
            time.sleep(POLL)

    def _topic_live(self, name):
        snap = self.node.snapshot()
        t = next((x for x in snap.get('topics', []) if x['name'] == name), None)
        clock = snap.get('clock')
        return t is not None and t['hz'] > 0 and clock is not None and not clock['stale']

    def _start(self, name, preset):
        args = self.presets[preset]['args'].get(name, [])
        try:
            self.procs.start(name, args, preset, 'batch')
        except StackRunning:
            raise RunFailed('stack_running', name=name)
        except OSError as e:
            raise RunFailed('start_failed', name=name, reason=str(e))

    def _step_size(self, preset):
        """max_step_size of the preset's world file (set_physics needs it with the new factor); 1 ms if unknown."""
        world = next((a.split(':=', 1)[1] for a in self.presets[preset]['args'].get(self.sim, [])
                      if a.startswith('world:=')), None)
        try:
            from ament_index_python.packages import get_package_share_directory
            text = (Path(get_package_share_directory('sim_robot')) / 'worlds' / world).read_text(encoding='utf-8')
            m = STEP_RE.search(text)
            return float(m.group(1)) if m else 0.001
        except Exception:
            return 0.001

    def _set_speed(self, factor, preset):
        """Raise the running sim's real_time_factor. The ign CLI inherits the console's IGN_PARTITION, the same
        partition the sim stack was started in, so it reaches only that Gazebo."""
        def ign(*args):
            return subprocess.run(['ign', *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        try:
            svc = next((s.strip() for s in ign('service', '-l').stdout.splitlines() if PHYSICS_RE.match(s.strip())), None)
            if svc is None:
                raise RunFailed('speed_failed', reason='no /world/*/set_physics service')
            req = f'max_step_size: {self._step_size(preset)}, real_time_factor: {factor}'
            p = ign('service', '-s', svc, '--reqtype', 'ignition.msgs.Physics', '--reptype', 'ignition.msgs.Boolean',
                    '--timeout', '5000', '--req', req)
            if 'data: true' not in p.stdout:
                raise RunFailed('speed_failed', reason=(p.stdout + p.stderr).strip()[:200] or 'no reply')
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RunFailed('speed_failed', reason=str(e))

    def _progress(self):
        text = self.procs.log_end(self.waypoints)
        hits = WAYPOINT_RE.findall(text)
        total = TOTAL_RE.findall(text)
        done, n = (int(hits[-1][0]), int(hits[-1][1])) if hits else (0, int(total[-1]) if total else None)
        return {'done': done, 'total': n}

    def _cleanup(self):
        """Stop waypoints -> SLAM -> sim (SLAM saves its map on stop), then wait for the process groups."""
        self._phase('stopping')
        slam = self.batch['slam']
        for name in (self.waypoints, slam, self.sim):
            s = self.procs.get(name)
            if s is not None and s.state() == 'running':
                self.procs.stop(name)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            left = [n for n in (self.waypoints, slam, self.sim) if self._alive(n)]
            if not left:
                return []
            time.sleep(POLL)
        return left

    def _alive(self, name):
        s = self.procs.get(name)
        if s is None or s.proc is None:
            return False
        return s.proc.poll() is None or _group_alive(s.proc.pid)

    def _sim_time(self):
        clock = self.node.snapshot().get('clock')
        return None if clock is None or clock['stale'] else float(clock['sim_time'])

    def _rtf(self, rec, start):
        """Real-time factor while driving: simulated seconds per wall-clock second."""
        sim0, wall0 = start
        sim1, wall1 = self._sim_time(), time.monotonic()
        rec['drive_wall_s'] = round(wall1 - wall0, 1)
        if sim0 is not None and sim1 is not None and wall1 > wall0:
            rec['drive_sim_s'] = round(sim1 - sim0, 1)
            rec['rtf'] = round((sim1 - sim0) / (wall1 - wall0), 3)

    def _one(self, i):
        b = self.batch
        preset, slam = b['preset'], b['slam']
        deadline = time.monotonic() + self.run_timeout
        wall0 = time.monotonic()
        rec = {'index': i, 'run_id': None, 'ok': False, 'code': None, 'params': {}, 'map': None,
               'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'ended': None}
        with self.lock:
            b['runs'].append(rec)
            self.live.update(index=i, progress=None, run_started=time.time())
        try:
            self._phase('start_sim')
            self._start(self.sim, preset)
            self._phase('wait_sim')
            self._wait(lambda: self._topic_live(self.gt_topic), self.ready_timeout, 'sim_not_ready', deadline)
            if b.get('speed', 1.0) != 1.0:
                self._set_speed(b['speed'], preset)
            self._phase('start_slam')
            self._start(slam, preset)
            self.mapping.select(slam)           # the mapping data follows this SLAM's topics
            self.mapping.reset()                # same as the mapping page: every SLAM start begins a new map
            self._phase('wait_slam')
            self._wait(lambda: self.mapping.status()['initialized'], self.ready_timeout, 'slam_not_ready', deadline)
            time.sleep(2.0)                     # let FAST-LIO settle after IMU init before the robot moves
            self._phase('drive')
            drive0 = (self._sim_time(), time.monotonic())
            self._start(self.waypoints, preset)
            wp = self.procs.get(self.waypoints)

            def route_done():
                with self.lock:
                    self.live['progress'] = self._progress()
                return wp.state() == 'exited'
            self._wait(route_done, self.run_timeout, 'run_timeout', deadline)
            self._rtf(rec, drive0)
            if wp.exit_code != 0:
                raise RunFailed('waypoints_failed', code=wp.exit_code)
            if self.procs.get(slam).state() != 'running':
                raise RunFailed('slam_exited', code=self.procs.get(slam).exit_code)
            rec['run_id'] = self.mapping.status()['run']
            if rec['run_id'] is None:
                raise RunFailed('no_run_record')
            if b['save_maps']:
                self._phase('save_map')
                name = f'{b["id"].replace("batch_", "b")}_{i}'
                try:
                    self.library.start_save(name)
                    self._wait(lambda: self.library.status().get('state') != 'running', 120, 'save_timeout')
                    st = self.library.status()
                    if st.get('state') == 'done':
                        rec['map'] = name
                    else:
                        rec['params']['save_error'] = st.get('code')
                except SaveError as e:
                    rec['params']['save_error'] = e.code
            self.mapping.reset()                # ends the run record (runs.py writes run.json)
            rec['ok'] = True
        except RunFailed as e:
            rec['code'], rec['params'] = e.code, {**rec['params'], **e.params}
            rec['run_id'] = rec['run_id'] or self.mapping.status()['run']
            self.mapping.reset()
        finally:
            rec['ended'] = time.strftime('%Y-%m-%d %H:%M:%S')
            rec['wall_s'] = round(time.monotonic() - wall0, 1)   # whole repetition incl. start-up and stop
            left = self._cleanup()
            if left:
                rec['ok'] = False
                rec['code'] = rec['code'] or 'leftover_processes'
                rec['params']['leftover'] = ', '.join(left)
            with self.lock:
                self._write()
        return not left

    def _main(self):
        b = self.batch
        try:
            for i in range(1, b['n'] + 1):
                if not self._one(i):
                    with self.lock:
                        b['reason'] = 'leftover_processes'
                    break
                time.sleep(3.0)                 # let Gazebo's transport and DDS forget the old processes
            else:
                self._evaluate()
                with self.lock:
                    b['state'] = 'finished'
        except Aborted:
            with self.lock:
                last = b['runs'][-1] if b['runs'] else None
                if last and not last['ok'] and last['code'] is None:
                    last['code'] = b.get('reason') or 'canceled'
                    last['run_id'] = last['run_id'] or self.mapping.status()['run']
            self.mapping.reset()
            self._cleanup()
            self._evaluate()
            with self.lock:
                b['state'] = 'canceled'
        except Exception as e:                  # Unexpected: record it and leave nothing running
            with self.lock:
                b['reason'] = f'{type(e).__name__}: {e}'
            self._cleanup()
            with self.lock:
                b['state'] = 'failed'
        finally:
            with self.lock:
                if b['state'] == 'running':
                    b['state'] = 'failed'
                b['ended'] = time.strftime('%Y-%m-%d %H:%M:%S')
                b['ended_wall'] = time.time()
                self.live.update(phase='done', progress=None)
                self._write()

    def _evaluate(self):
        self._phase('evaluate')
        for rec in self.batch['runs']:
            if not rec.get('run_id'):
                continue
            try:
                ev = self.evaluation.evaluate(rec['run_id'])['eval']
                rec['ape_rmse'] = ev['ape']['rmse']
                rec['rpe_rmse'] = (ev.get('rpe') or {}).get('rmse')
                rec['length'] = ev['length']
                rec['duration'] = ev['duration']
            except Exception as e:              # EvalError or a run that never got written
                rec['eval_error'] = getattr(e, 'params', {}).get('reason') or getattr(e, 'code', str(e))
        ok = [r for r in self.batch['runs'] if r['ok']]
        with self.lock:
            self.batch['summary'] = {
                'runs': len(self.batch['runs']), 'succeeded': len(ok),
                'ape_rmse': _mean_std([r.get('ape_rmse') for r in ok]),
                'rpe_rmse': _mean_std([r.get('rpe_rmse') for r in ok]),
                'length': _mean_std([r.get('length') for r in ok]),
                'duration': _mean_std([r.get('duration') for r in ok]),
                'rtf': _mean_std([r.get('rtf') for r in ok]),
                'wall_s': _mean_std([r.get('wall_s') for r in ok]),
            }


def register_batch_routes(app, cfg, node, procs, mapping, library, evaluation):
    runner = BatchRunner(cfg, node, procs, mapping, library, evaluation)

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/batch/options')
    def batch_options():
        return runner.options()

    @app.get('/api/batch/status')
    def batch_status():
        return runner.status()

    @app.get('/api/batch/history')
    def batch_history():
        return runner.history()

    @app.get('/api/batch/{batch_id}')
    def batch_get(batch_id: str):
        try:
            return runner.load(batch_id)
        except BatchError as e:
            raise fail(e)

    @app.post('/api/batch/start')
    def batch_start(body: dict = Body(...)):
        try:
            n = body.get('n')
            speed = body.get('speed', 1.0)
            return runner.start(body.get('preset'), body.get('slam'), int(n) if isinstance(n, (int, float)) else n,
                                bool(body.get('save_maps', False)),
                                float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else speed)
        except BatchError as e:
            raise fail(e)

    @app.post('/api/batch/cancel')
    def batch_cancel():
        if not runner.abort('canceled'):
            raise fail(BatchError('batch_not_running', 409))
        return runner.status()

    return runner
