"""Batch navigation experiments (nav_batch_*): N repetitions of a waypoint mission with auto-reset.

Between repetitions:
  - fast reset: zero /cmd_vel -> Gazebo /world/<world>/set_pose back to spawn -> clear local & global
    costmaps -> reset /initialpose (AMCL) or verify /ground_truth (gt_localizer);
  - fallback restart: if fast reset fails (robot wedged/tilted, Nav2 exited, or AMCL fails to reset),
    cleanly restart simulation + Nav2.

Each repetition creates a standard ~/runs/nav_<time>/ run record via NavMission (tagged with 'batch').
The batch summary lives in ~/runs/nav_batch_<time>/batch.json.
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

from .nav_eval import nav_metrics
from .navigation import NavigationError
from .process_manager import StackClosing, StackRunning, _group_alive

NAV_BATCH_RE = re.compile(r'^nav_batch_[0-9_]{8,40}$')
SET_POSE_RE = re.compile(r'^/world/[^/]+/set_pose$')
POLL = 0.4


class NavBatchError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


class _Aborted(Exception):
    pass


class _RunFailed(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def _mean_std(vals):
    vals = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
    return {'mean': round(m, 6), 'std': round(sd, 6), 'min': round(min(vals), 6), 'max': round(max(vals), 6),
            'n': len(vals)}


def _euler_to_quat(roll, pitch, yaw):
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def spawn_to_map(spawn_xy_yaw, origin):
    """Map-frame pose (x, y, yaw) of the world spawn pose given map origin (mx, my, myaw) in world."""
    if origin is None:
        return 0.0, 0.0, 0.0
    sx, sy, syaw = spawn_xy_yaw
    mx, my, myaw = origin
    dx, dy = sx - mx, sy - my
    c, s = math.cos(-myaw), math.sin(-myaw)
    return round(c * dx - s * dy, 4), round(s * dx + c * dy, 4), round(_wrap(syaw - myaw), 5)


def summarize_nav_runs(runs):
    """Aggregate completed repetitions (mean ± std per metric + pooled goal success rate)."""
    done = [r for r in runs if r.get('ok')]
    total_goals = sum(r.get('finished') or 0 for r in done)
    total_succeeded = sum(r.get('succeeded') or 0 for r in done)
    return {
        'runs': len(runs),
        'completed': len(done),
        'all_goals_runs': sum(1 for r in done if r.get('all_succeeded')),
        'total_goals': total_goals,
        'total_succeeded': total_succeeded,
        'pooled_success_rate': round(total_succeeded / total_goals, 4) if total_goals else None,
        'success_rate': _mean_std([r.get('success_rate') for r in done]),
        'total_time_s': _mean_std([r.get('total_time_s') for r in done]),
        'mean_time_s': _mean_std([r.get('mean_time_s') for r in done]),
        'total_path_m': _mean_std([r.get('total_path_m') for r in done]),
        'mean_path_m': _mean_std([r.get('mean_path_m') for r in done]),
        'recoveries': _mean_std([r.get('recoveries') for r in done]),
        'total_recoveries': sum(r.get('recoveries') or 0 for r in done) if done else 0,
        'goal_error_m': _mean_std([r.get('mean_goal_error_m') for r in done]),
        'loc_error_m': _mean_std([r.get('mean_loc_error_m') for r in done]),
        'amcl_rmse_m': _mean_std([r.get('amcl_rmse_m') for r in done]),
        'min_clearance_m': _mean_std([r.get('min_clearance_m') for r in done]),
        'wall_s': _mean_std([r.get('wall_s') for r in done]),
    }


class NavBatchRunner:
    def __init__(self, cfg, node, procs, nav_maps, navdata, mission, helpers):
        nb = cfg.get('nav_batch') or {}
        b = cfg.get('batch') or {}
        self.cfg = cfg
        self.node, self.procs = node, procs
        self.nav_maps, self.navdata, self.mission = nav_maps, navdata, mission
        self.h = helpers
        self.presets = cfg['presets']
        self.sim = helpers['sim_name']
        self.nav = helpers['nav_name']
        self.runs_dir = Path(os.path.expanduser(cfg['mapping'].get('runs_dir', '~/runs')))
        self.gt_topic = cfg['mapping'].get('gt_topic') or '/ground_truth'
        self.max_runs = int(nb.get('max_runs', b.get('max_runs', 20)))
        self.ready_timeout = float(nb.get('ready_timeout', b.get('ready_timeout', 120)))
        self.lock = threading.Lock()
        self.abort_evt = threading.Event()
        self.thread = None
        self.batch = None
        self.live = {}

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def options(self):
        lo, hi = self.h['scale_range']
        return {
            'variants': sorted(self.h['variants']()),
            'default_variant': self.h['default_variant'],
            'localizers': ['amcl', 'gt'],
            'max_runs': self.max_runs,
            'goal_timeout': self.h['goal_timeout'],
            'speed_scale_range': [lo, hi],
        }

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
            for d in sorted(self.runs_dir.glob('nav_batch_*'), reverse=True):
                try:
                    b = json.loads((d / 'batch.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                out.append({
                    'id': b.get('id'), 'created': b.get('created'), 'map': b.get('map'),
                    'preset': b.get('preset'), 'variant': b.get('variant'),
                    'localizer': b.get('localizer') or 'amcl',
                    'speed_scale': b.get('speed_scale', 1.0), 'n': b.get('n'),
                    'goals_count': len(b.get('goals') or []), 'state': b.get('state'),
                    'summary': b.get('summary'),
                    'run_ids': [r.get('run_id') for r in (b.get('runs') or []) if r.get('run_id')],
                })
        return out

    def load(self, batch_id):
        if not NAV_BATCH_RE.fullmatch(batch_id or ''):
            raise NavBatchError('batch_not_found', 404, id=batch_id)
        if self.batch and self.batch.get('id') == batch_id:
            return self.status()
        try:
            return json.loads((self.runs_dir / batch_id / 'batch.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise NavBatchError('batch_not_found', 404, id=batch_id)

    def start(self, map_name, variant, localizer, goals_raw, n, speed_scale=1.0):
        try:
            asset = self.nav_maps.assets(map_name)
        except NavigationError as e:
            raise NavBatchError(e.code, 404 if e.code == 'map_not_found' else 400, **e.params)
        preset = asset.get('preset')
        if not preset or preset not in self.presets:
            raise NavBatchError('navigation_sim_no_scene', 409, name=map_name)

        variants = self.h['variants']()
        variant = variant or self.h['default_variant']
        if variant not in variants:
            raise NavBatchError('navigation_variant_unknown', 400, name=str(variant))
        localizer = (localizer or 'amcl').strip().lower()
        if localizer not in ('amcl', 'gt'):
            raise NavBatchError('navigation_localizer_unknown', 400, name=str(localizer))
        goals = self.h['parse_goals']({'goals': goals_raw}, asset['grid'])
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= self.max_runs:
            raise NavBatchError('batch_invalid', 400, field='n', value=str(n))
        lo, hi = self.h['scale_range']
        if not isinstance(speed_scale, (int, float)) or isinstance(speed_scale, bool) or not (lo <= float(speed_scale) <= hi):
            raise NavBatchError('navigation_speed_invalid', 400, lo=lo, hi=hi)
        speed_scale = round(float(speed_scale), 2)

        t_old = self.thread
        if t_old is not None and t_old.is_alive() and (self.batch or {}).get('state') != 'running':
            t_old.join(timeout=2.0)
        with self.lock:
            if self.running() or self.h['mapping_batch_running']():
                raise NavBatchError('batch_running', 409, id=(self.batch or {}).get('id', 'batch'))
            busy = self.h['other_busy']()
            if busy:
                raise NavBatchError('batch_busy', 409, names=', '.join(busy))

            stamp = time.strftime('%Y%m%d_%H%M%S')
            batch_id, k = f'nav_batch_{stamp}', 2
            while (self.runs_dir / batch_id).exists():
                batch_id, k = f'nav_batch_{stamp}_{k}', k + 1
            self.abort_evt.clear()
            self.batch = {
                'id': batch_id, 'type': 'batch_navigation',
                'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'ended': None, 'state': 'running',
                'map': map_name, 'preset': preset, 'variant': variant, 'localizer': localizer,
                'speed_scale': speed_scale, 'n': n,
                'goals': [{'x': x, 'y': y, 'yaw': yaw} for x, y, yaw in goals],
                'runs': [], 'summary': None, 'reason': None,
                'started_wall': time.time(), 'ended_wall': None,
            }
            self.live = {'index': 0, 'phase': 'starting', 'mission': None, 'run_started': None}
            try:
                (self.runs_dir / batch_id).mkdir(parents=True)
            except OSError as e:
                self.batch = None
                raise NavBatchError('batch_failed', 500, reason=str(e))
            self._write()
            self._asset = asset
            self._params_src = variants[variant]
            self._goals = goals
            self.thread = threading.Thread(target=self._main, daemon=True)
            self.thread.start()
        return self.status()

    def abort(self, reason='canceled'):
        if self.running():
            with self.lock:
                self.batch['reason'] = reason
            self.abort_evt.set()
            return True
        return False

    # ---------------- internal helpers ----------------

    def _phase(self, phase, **extra):
        with self.lock:
            self.live.update(phase=phase, **extra)

    def _write(self):
        b = {k: v for k, v in self.batch.items() if k not in ('started_wall', 'ended_wall')}
        path = self.runs_dir / self.batch['id'] / 'batch.json'
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)

    def _wait(self, cond, timeout, fail_code):
        end = time.monotonic() + timeout
        while True:
            if self.abort_evt.is_set():
                raise _Aborted()
            if cond():
                return
            if time.monotonic() > end:
                raise _RunFailed(fail_code, seconds=int(timeout))
            time.sleep(POLL)

    def _topic_live(self, name):
        snap = self.node.snapshot()
        t = next((x for x in snap.get('topics', []) if x['name'] == name), None)
        clock = snap.get('clock')
        return t is not None and t['hz'] > 0 and clock is not None and not clock['stale']

    def _alive(self, name):
        s = self.procs.get(name)
        if s is None or s.proc is None:
            return False
        return s.proc.poll() is None or _group_alive(s.proc.pid)

    def _stop_stack(self, name):
        s = self.procs.get(name)
        if s is not None and s.state() in ('running', 'stopping'):
            self.procs.stop(name)

    def _cleanup(self):
        self._phase('stopping')
        self.mission.stop()
        self._stop_stack(self.nav)
        self._stop_stack(self.sim)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            left = [n for n in (self.nav, self.sim) if self._alive(n)]
            if not left:
                return []
            time.sleep(POLL)
        return left

    def _start_sim_and_nav(self, preset, asset, variant, localizer, speed_scale, origin):
        self._phase('start_sim')
        sim_args = list(self.presets[preset]['args'].get(self.sim, []))
        try:
            self.procs.start(self.sim, sim_args, preset, 'navigation')
        except StackClosing:
            if self.abort_evt.is_set():
                raise _Aborted()
            raise _RunFailed('start_failed', name=self.sim, reason='console is restarting')
        except StackRunning as e:
            raise _RunFailed('stack_running', name=e.name)
        except OSError as e:
            raise _RunFailed('start_failed', name=self.sim, reason=str(e))

        self._phase('wait_sim')
        self._wait(lambda: self._topic_live(self.gt_topic), self.ready_timeout, 'sim_not_ready')

        self._phase('start_nav')
        nav_args = [f"map:={asset['_yaml']}", *self.h['prepare_params'](self._params_src, speed_scale),
                    f'localizer:={localizer}']
        if localizer == 'gt' and origin:
            nav_args += [f'start_x:={origin[0]}', f'start_y:={origin[1]}', f'start_yaw:={origin[2]}']
        try:
            self.procs.start(self.nav, nav_args, preset, 'navigation')
        except StackClosing:
            if self.abort_evt.is_set():
                raise _Aborted()
            raise _RunFailed('start_failed', name=self.nav, reason='console is restarting')
        except StackRunning as e:
            raise _RunFailed('stack_running', name=e.name)
        except OSError as e:
            raise _RunFailed('start_failed', name=self.nav, reason=str(e))
        self.h['set_active_run'](variant, self._params_src, localizer)
        self.navdata.reset(enabled=True)

        self._phase('wait_nav')
        def nav_ready():
            snap = self.navdata.snapshot()
            return (snap['tf_ok'] and (snap['age'] is None or snap['age'] <= 0.5)
                    and self._topic_live('/scan') and self.navdata.nav2_active())
        self._wait(nav_ready, self.ready_timeout, 'nav_not_ready')
        time.sleep(1.0)

    def _restart_all(self, preset, asset, variant, localizer, speed_scale, origin):
        left = self._cleanup()
        if left:
            raise _RunFailed('leftover_processes', leftover=', '.join(left))
        time.sleep(1.5)
        self._start_sim_and_nav(preset, asset, variant, localizer, speed_scale, origin)

    def _fast_reset(self, spawn, origin, localizer):
        """Reset robot to spawn via Gazebo /world/.../set_pose, clear costmaps, and re-seed localization."""
        if spawn is None or not spawn.get('world'):
            return False
        s_proc = self.procs.get(self.sim)
        n_proc = self.procs.get(self.nav)
        if s_proc is None or s_proc.state() != 'running' or n_proc is None or n_proc.state() != 'running':
            return False

        self.node.stop_motion(0.4)
        time.sleep(0.4)

        sx, sy, sz, sr, sp, syaw = spawn['pose']
        qx, qy, qz, qw = _euler_to_quat(sr, sp, syaw)
        svc = f"/world/{spawn['world']}/set_pose"
        req = (f'name: "{spawn["model"]}", '
               f'position: {{x: {sx}, y: {sy}, z: {sz}}}, '
               f'orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}')
        t_reset = time.monotonic()
        try:
            p = subprocess.run(
                ['ign', 'service', '-s', svc, '--reqtype', 'ignition.msgs.Pose',
                 '--reptype', 'ignition.msgs.Boolean', '--timeout', '5000', '--req', req],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8,
            )
            if 'data: true' not in p.stdout:
                return False
        except (OSError, subprocess.SubprocessError):
            return False

        # Wait until /ground_truth confirms robot is at spawn and upright
        end = time.monotonic() + 4.0
        gt_ok = False
        while time.monotonic() < end:
            if self.abort_evt.is_set():
                raise _Aborted()
            gt = self.mission.latest_gt_world()
            if gt and gt[0] > t_reset and math.hypot(gt[1] - sx, gt[2] - sy) < 0.25 and abs(gt[3] - sz) < 0.35:
                gt_ok = True
                break
            time.sleep(0.1)
        if not gt_ok:
            return False

        self.navdata.clear_costmaps(timeout=5.0)
        rx, ry, ryaw = spawn_to_map((sx, sy, syaw), origin)

        if localizer == 'amcl':
            prev_amcl = self.navdata.amcl_stamp()
            self.navdata.set_initial_pose(rx, ry, ryaw)
            end = time.monotonic() + 10.0
            while time.monotonic() < end:
                if self.abort_evt.is_set():
                    raise _Aborted()
                pose = self.navdata.robot_pose()
                if (self.navdata.amcl_stamp() != prev_amcl and pose is not None
                        and math.hypot(pose[0] - rx, pose[1] - ry) < 0.35):
                    time.sleep(0.5)
                    return self.navdata.nav2_active(timeout=2.0)
                time.sleep(0.2)
            return False

        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            if self.abort_evt.is_set():
                raise _Aborted()
            pose = self.navdata.robot_pose()
            if pose is not None and math.hypot(pose[0] - rx, pose[1] - ry) < 0.20:
                time.sleep(0.3)
                return self.navdata.nav2_active(timeout=2.0)
            time.sleep(0.1)
        return False

    def _main(self):
        from .nav_mission import map_origin, world_file, world_spawn_info

        b = self.batch
        asset = self._asset
        preset, variant, localizer = b['preset'], b['variant'], b['localizer']
        speed_scale = b['speed_scale']
        goals = self._goals
        sim_cmd = list(self.presets[preset]['args'].get(self.sim, []))
        try:
            spawn = world_spawn_info(world_file(sim_cmd))
        except Exception:
            spawn = None
        meta = self.h['map_meta'](asset)
        origin, origin_source = map_origin(meta, self.runs_dir, sim_cmd)

        try:
            # Ensure a clean initial state (even if sim/navigation were open on the Navigation page)
            self._restart_all(preset, asset, variant, localizer, speed_scale, origin)

            for i in range(1, b['n'] + 1):
                wall0 = time.monotonic()
                rec = {
                    'index': i, 'run_id': None, 'ok': False, 'all_succeeded': False,
                    'code': None, 'params': {}, 'reset_kind': 'initial' if i == 1 else 'fast',
                    'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'ended': None,
                }
                with self.lock:
                    b['runs'].append(rec)
                    self.live.update(index=i, mission=None, run_started=time.time())

                try:
                    if i > 1:
                        self._phase('resetting')
                        if not self._fast_reset(spawn, origin, localizer):
                            rec['reset_kind'] = 'restart'
                            self._restart_all(preset, asset, variant, localizer, speed_scale, origin)

                    self._phase('drive')
                    n_proc = self.procs.get(self.nav)
                    ctx = {
                        'map': b['map'], 'map_yaml': str(asset['_yaml']), 'preset': preset,
                        'variant': variant, 'localizer': localizer, 'batch': b['id'],
                        'params_file': self._params_src,
                        'nav_cmd': ' '.join(n_proc.run_cmd or n_proc.cmd),
                        'sim_cmd': ' '.join(sim_cmd),
                        'origin': origin, 'origin_source': origin_source,
                    }
                    mst = self.mission.start(goals, ctx, self.h['speed_fn'])
                    rec['run_id'] = mst['id']

                    while True:
                        if self.abort_evt.is_set():
                            self.mission.cancel()
                            # Wait briefly for mission record to close
                            t_end = time.monotonic() + 5.0
                            while time.monotonic() < t_end and (self.mission.status() or {}).get('state') == 'running':
                                time.sleep(0.2)
                            raise _Aborted()
                        st = self.mission.status()
                        with self.lock:
                            self.live['mission'] = st
                        if st is None or st['state'] != 'running':
                            break
                        if n_proc.state() != 'running':
                            self.mission.stop('stopped')
                            raise _RunFailed('nav_exited', code=n_proc.exit_code)
                        time.sleep(POLL)

                    if st['state'] != 'finished':
                        raise _RunFailed('mission_failed', state=st['state'])

                    info = json.loads((self.runs_dir / st['id'] / 'run.json').read_text(encoding='utf-8'))
                    m = nav_metrics(info)
                    rec.update({
                        'ok': True,
                        'all_succeeded': m['finished'] > 0 and m['succeeded'] == m['goals'],
                        'goals': m['goals'], 'finished': m['finished'], 'succeeded': m['succeeded'],
                        'success_rate': m['success_rate'],
                        'total_time_s': m['total_time_s'], 'mean_time_s': m['mean_time_s'],
                        'total_path_m': m['total_path_m'], 'mean_path_m': m['mean_path_m'],
                        'mean_goal_error_m': m['mean_goal_error_m'],
                        'mean_loc_error_m': m['mean_loc_error_m'],
                        'amcl_rmse_m': m['amcl_rmse_m'],
                        'recoveries': m['recoveries'],
                        'min_clearance_m': m['min_clearance_m'],
                    })
                except _RunFailed as e:
                    rec['code'], rec['params'] = e.code, {**rec['params'], **e.params}
                finally:
                    rec['ended'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    rec['wall_s'] = round(time.monotonic() - wall0, 1)
                    with self.lock:
                        b['summary'] = summarize_nav_runs(b['runs'])
                        self._write()

            self._cleanup()
            with self.lock:
                b['state'] = 'finished'
        except _Aborted:
            self._cleanup()
            with self.lock:
                last = b['runs'][-1] if b['runs'] else None
                if last and not last['ok'] and last['code'] is None:
                    last['code'] = b.get('reason') or 'canceled'
                b['summary'] = summarize_nav_runs(b['runs'])
                b['state'] = 'canceled'
        except Exception as e:
            self._cleanup()
            with self.lock:
                b['reason'] = f'{type(e).__name__}: {e}'
                b['summary'] = summarize_nav_runs(b['runs'])
                b['state'] = 'failed'
        finally:
            with self.lock:
                if b['state'] == 'running':
                    b['state'] = 'failed'
                b['summary'] = summarize_nav_runs(b['runs'])
                b['ended'] = time.strftime('%Y-%m-%d %H:%M:%S')
                b['ended_wall'] = time.time()
                self.live.update(phase='done', mission=None)
                self._write()


def register_nav_batch_routes(app, cfg, node, procs, nav_maps, navdata, mission, helpers):
    runner = NavBatchRunner(cfg, node, procs, nav_maps, navdata, mission, helpers)

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/nav-batch/options')
    def nav_batch_options():
        return runner.options()

    @app.get('/api/nav-batch/status')
    def nav_batch_status():
        return runner.status()

    @app.get('/api/nav-batch/history')
    def nav_batch_history():
        return runner.history()

    @app.get('/api/nav-batch/{batch_id}')
    def nav_batch_get(batch_id: str):
        try:
            return runner.load(batch_id)
        except NavBatchError as e:
            raise fail(e)

    @app.post('/api/nav-batch/start')
    def nav_batch_start(body: dict = Body(...)):
        try:
            n = body.get('n')
            speed = body.get('speed_scale', 1.0)
            return runner.start(
                body.get('map'),
                body.get('variant'),
                body.get('localizer', 'amcl'),
                body.get('goals'),
                int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else n,
                float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else speed,
            )
        except NavBatchError as e:
            raise fail(e)

    @app.post('/api/nav-batch/cancel')
    def nav_batch_cancel():
        if not runner.abort('canceled'):
            raise fail(NavBatchError('batch_not_running', 409))
        return runner.status()

    return runner
