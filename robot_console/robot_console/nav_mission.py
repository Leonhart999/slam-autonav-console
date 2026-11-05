"""Navigation missions (step 3b): goals sent one after another with NavigateToPose, each with its own result
and metrics (same definitions as lidar_nav/nav_goals.py), plus a run record for the evaluation page.

Per goal:
  result       succeeded / aborted / canceled / rejected / timeout
  time         seconds (simulated when /clock is fresh) from sending the goal to Nav2's result
  path         distance driven, accumulated from ground truth (from map -> base_link without ground truth)
  goal_error   ground-truth position SETTLE s after the result vs the goal (m), and the yaw error (deg)
  loc_error    localization estimate (map -> base_link, AMCL + odometry) vs ground truth at the same moment
  recoveries   Nav2's number_of_recoveries in the last feedback
  clearance    smallest distance from base_link to a laser point (/scan) while driving to this goal

Run folder ~/runs/nav_<time>/:
  run.json        map, scene, Nav2 parameter variant, speed, ground-truth origin, goals with results, summary
  goals.csv       one row per goal
  amcl.csv        every /amcl_pose with the ground truth nearest in time and the error (AMCL error curve)
  track.csv       10 Hz: ground truth, map -> base_link estimate, clearance
  path.png, amcl_error.png  written by nav_figures.py when the mission ends

Ground truth (/ground_truth, Gazebo world frame) is moved into the map frame with the world pose of the map
origin, see map_origin(). Without it the ground-truth columns stay empty and the errors are null.
"""
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

SETTLE = 0.5            # s to wait after a result before measuring the arrival error (robot comes to rest)
TICK = 0.1              # s, sampling period of track.csv / clearance / timeout check
MAX_GOALS = 200
GOAL_COLUMNS = ['index', 'x', 'y', 'yaw', 'result', 'time_s', 'path_m', 'goal_error_m', 'goal_yaw_error_deg',
                'loc_error_m', 'recoveries', 'min_clearance_m', 'speed_scale']
RUN_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,80}')


def _wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def _quat_yaw(qx, qy, qz, qw):
    return math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _round(v, n=3):
    return None if v is None or not math.isfinite(v) else round(v, n)


# ---------------- Where the map frame is in the Gazebo world ----------------

def _tum_rows(path, limit=None):
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                return
            parts = line.split()
            if len(parts) == 8:
                yield [float(v) for v in parts]


def origin_from_run(run_dir):
    """World pose of the map frame from the map's own mapping run: T_world_map = T_world_base * inv(T_map_base)
    at the first SLAM pose (est.tum is in camera_init = map frame, gt.tum in the world frame)."""
    est = next(_tum_rows(run_dir / 'est.tum', 1), None)
    if est is None:
        return None
    t0 = est[0]
    best = None
    for row in _tum_rows(run_dir / 'gt.tum'):
        if best is None or abs(row[0] - t0) < abs(best[0] - t0):
            best = row
        elif row[0] > t0 + 1.0:
            break
    if best is None or abs(best[0] - t0) > 0.5:
        return None
    ey, gy = _quat_yaw(*est[4:8]), _quat_yaw(*best[4:8])
    yaw = _wrap(gy - ey)
    c, s = math.cos(yaw), math.sin(yaw)
    x = best[1] - (c * est[1] - s * est[2])
    y = best[2] - (s * est[1] + c * est[2])
    return x, y, yaw


def origin_from_world(world_file):
    """Spawn pose of the robot (<include> of model://sim_robot) in a world SDF."""
    root = ET.parse(world_file).getroot()
    for inc in root.iter('include'):
        uri = (inc.findtext('uri') or '').strip()
        if uri.rstrip('/').endswith('sim_robot'):
            v = [float(t) for t in (inc.findtext('pose') or '0 0 0 0 0 0').split()]
            v += [0.0] * (6 - len(v))
            return v[0], v[1], v[5]
    return None


def world_file(sim_cmd):
    """World SDF used by the running simulation (world:= argument, same resolution as sim.launch.py)."""
    arg = next((a for a in sim_cmd or [] if isinstance(a, str) and a.startswith('world:=')), None)
    name = arg.split(':=', 1)[1] if arg else 'indoor_office.sdf'
    if os.path.isabs(name):
        return Path(name)
    from ament_index_python.packages import get_package_share_directory
    return Path(get_package_share_directory('sim_robot')) / 'worlds' / name


def map_origin(meta, runs_dir, sim_cmd):
    """((x, y, yaw) of the map origin in the world, source text) or (None, None)."""
    run = meta.get('run') if isinstance(meta, dict) else None
    if isinstance(run, str) and RUN_ID_RE.fullmatch(run):
        try:
            o = origin_from_run(Path(runs_dir).expanduser() / run)
            if o:
                return o, f'mapping run {run}'
        except (OSError, ValueError):
            pass
    try:
        path = world_file(sim_cmd)
        o = origin_from_world(path)
        if o:
            return o, f'spawn pose in {path.name}'
    except (OSError, ValueError, ET.ParseError, LookupError):
        pass
    return None, None


# ---------------- Missions ----------------

class NavMission:

    def __init__(self, node, navdata, cfg):
        """cfg: navigation section of console.yaml plus runs_dir and gt_topic from the mapping section."""
        self.node, self.nav = node, navdata
        self.runs_dir = Path(cfg.get('runs_dir') or '~/runs').expanduser()
        self.timeout = float(cfg.get('goal_timeout', 240.0))
        self.gt_topic = cfg.get('gt_topic') or None
        self.lock = threading.RLock()
        self.m = None                  # current or last mission
        self.token = 0
        self.origin = None             # world pose of the map frame for the running mission
        self.gt = deque(maxlen=400)    # (t, x, y, yaw) in the map frame, ~4 s at 100 Hz
        self.files = {}
        self.ticks = 0
        gt_qos = QoSProfile(depth=50, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
        amcl_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL)
        if self.gt_topic:
            node.create_subscription(Odometry, self.gt_topic, self._on_gt, gt_qos)
        node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self._on_amcl, amcl_qos)
        node.create_timer(TICK, self._tick)

    def _now(self):
        t = self.node.sim_now()
        return time.time() if t is None else t

    def _running(self):
        return self.m is not None and self.m['state'] == 'running'

    # ---------------- Start / cancel / stop ----------------

    def start(self, goals, ctx, speed):
        """goals: [(x, y, yaw)] in the map frame (already validated). ctx: run.json fields from the server,
        including 'origin' / 'origin_source'. speed(): current speed multiplier."""
        with self.lock:
            if self._running():
                self._finish('preempted')
            self.token += 1
            token = self.token
            stamp = time.strftime('%Y%m%d_%H%M%S')
            run_id, n = f'nav_{stamp}', 2
            while (self.runs_dir / run_id).exists():
                run_id, n = f'nav_{stamp}_{n}', n + 1
            folder = self.runs_dir / run_id
            folder.mkdir(parents=True)
            self.origin = ctx.get('origin')
            self.gt.clear()
            self.m = {
                'token': token, 'id': run_id, 'dir': folder, 'state': 'running', 'index': 0, 'cancel': False,
                't0': self._now(), 'speed': speed,
                'goals': [{'index': i + 1, 'x': x, 'y': y, 'yaw': yaw, 'result': None, 'state': 'waiting'}
                          for i, (x, y, yaw) in enumerate(goals)],
                'amcl': {'n': 0, 'sum': 0.0, 'sq': 0.0, 'max': 0.0},
                'info': {'id': run_id, 'type': 'navigation', 'started': time.strftime('%Y-%m-%d %H:%M:%S'),
                         'ended': None, 'state': 'running',
                         'clock': 'sim' if self.node.sim_now() is not None else 'wall',
                         'goal_timeout': self.timeout, 'settle': SETTLE,
                         **{k: v for k, v in ctx.items() if k not in ('origin', 'origin_source')},
                         'ground_truth': ({'topic': self.gt_topic, 'origin': [round(v, 4) for v in self.origin],
                                           'source': ctx.get('origin_source')} if self.origin else None),
                         'files': {'goals.csv': 'per-goal results', 'amcl.csv': 'AMCL pose vs ground truth',
                                   'track.csv': '10 Hz ground truth, estimate and clearance',
                                   'path.png': 'path over the map', 'amcl_error.png': 'AMCL error over time'}},
            }
            self.files = {}
            for key, header in (('amcl', ['t', 'goal', 'amcl_x', 'amcl_y', 'amcl_yaw', 'gt_x', 'gt_y', 'gt_yaw',
                                          'error_xy', 'error_yaw_deg']),
                                ('track', ['t', 'goal', 'gt_x', 'gt_y', 'gt_yaw', 'est_x', 'est_y', 'est_yaw',
                                           'clearance'])):
                f = open(folder / f'{key}.csv', 'w', newline='', encoding='utf-8')
                w = csv.writer(f)
                w.writerow(header)
                self.files[key] = (f, w)
            self._write()
        self._send(token, 0)
        return self.status()

    def cancel(self):
        """Stop the mission after cancelling the current goal. False when no mission runs."""
        with self.lock:
            if not self._running():
                return False
            self.m['cancel'] = True
        self.nav.cancel_goal()
        return True

    def stop(self, reason='stopped'):
        """Navigation stopped or restarted: close the record (pending callbacks are ignored)."""
        with self.lock:
            if self._running():
                self._finish(reason)

    # ---------------- Goal sequence ----------------

    def _current(self, token, index):
        return self._running() and self.m['token'] == token and self.m['index'] == index

    def _send(self, token, index):
        with self.lock:
            if not self._running() or self.m['token'] != token:
                return
            self.m['index'] = index
            g = self.m['goals'][index]
            g.update(state='active', _t0=self._now(), _path=0.0, _last=None, _clear=math.inf, _timed_out=False,
                     speed_scale=self.m['speed']())
            x, y, yaw = g['x'], g['y'], g['yaw']
        self.nav.send_goal(x, y, yaw, on_done=lambda state, info: self._done(token, index, state, info))

    def _done(self, token, index, state, info):
        with self.lock:
            if not self._current(token, index):
                return
            g = self.m['goals'][index]
            g['time_s'] = _round(self._now() - g['_t0'], 2)
            g['recoveries'] = (info.get('feedback') or {}).get('recoveries', 0)
            g['result'] = 'timeout' if state == 'canceled' and g['_timed_out'] else state
            g['state'] = 'settling'
        threading.Timer(SETTLE, self._settled, (token, index)).start()

    def _settled(self, token, index):
        with self.lock:
            if not self._current(token, index):
                return
            g = self.m['goals'][index]
            gt = self.gt[-1] if self.gt else None
            est = self.nav.robot_pose()
            if gt:
                g['goal_error_m'] = _round(math.hypot(gt[1] - g['x'], gt[2] - g['y']))
                g['goal_yaw_error_deg'] = _round(math.degrees(abs(_wrap(gt[3] - g['yaw']))), 1)
            if gt and est:
                g['loc_error_m'] = _round(math.hypot(gt[1] - est[0], gt[2] - est[1]))
            g['path_m'] = _round(g['_path'], 2)
            g['min_clearance_m'] = _round(g['_clear'])
            g['state'] = 'done'
            if self.m['cancel'] or g['result'] == 'canceled':
                self._finish('canceled')
                return
            if index + 1 >= len(self.m['goals']):
                self._finish('finished')
                return
            self._write()
        self._send(token, index + 1)

    def _finish(self, state):
        """Close the running mission (lock held)."""
        m = self.m
        m['state'] = state
        for g in m['goals']:
            if g['state'] in ('active', 'settling'):
                g['state'] = 'done'
                g['result'] = g['result'] or ('canceled' if state == 'canceled' else state)
                g['path_m'] = _round(g.get('_path'), 2)
                g['min_clearance_m'] = _round(g.get('_clear'))
        m['info'].update(state=state, ended=time.strftime('%Y-%m-%d %H:%M:%S'))
        for f, _ in self.files.values():
            f.close()
        self.files = {}
        self._write()
        folder = str(m['dir'])
        threading.Thread(target=self._figures, args=(folder,), daemon=True).start()

    def _figures(self, folder):
        try:
            p = subprocess.run([sys.executable, '-m', 'robot_console.nav_figures', folder],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            if p.returncode:
                self.node.get_logger().warn(f'导航记录作图失败：{p.stderr.strip()[-300:]}')
        except (OSError, subprocess.SubprocessError) as e:
            self.node.get_logger().warn(f'导航记录作图失败：{e!r}')

    # ---------------- Samples ----------------

    def _on_gt(self, msg):
        with self.lock:
            if not self._running() or self.origin is None:
                return
            sx, sy, syaw = self.origin
            p, q = msg.pose.pose.position, msg.pose.pose.orientation
            c, s = math.cos(-syaw), math.sin(-syaw)
            dx, dy = p.x - sx, p.y - sy
            x, y = c * dx - s * dy, s * dx + c * dy
            t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            self.gt.append((t, x, y, _wrap(_quat_yaw(q.x, q.y, q.z, q.w) - syaw)))
            g = self.m['goals'][self.m['index']]
            if g['state'] == 'active':
                if g['_last'] is not None:
                    g['_path'] += math.hypot(x - g['_last'][0], y - g['_last'][1])
                g['_last'] = (x, y)

    def _on_amcl(self, msg):
        with self.lock:
            if not self._running() or 'amcl' not in self.files:
                return
            p, q = msg.pose.pose.position, msg.pose.pose.orientation
            t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            yaw = _quat_yaw(q.x, q.y, q.z, q.w)
            gt = min(self.gt, key=lambda r: abs(r[0] - t)) if self.gt else None
            row = [f'{t - self.m["t0"]:.3f}', self.m['index'] + 1, f'{p.x:.4f}', f'{p.y:.4f}', f'{yaw:.4f}']
            if gt and abs(gt[0] - t) < 0.2:
                err = math.hypot(p.x - gt[1], p.y - gt[2])
                a = self.m['amcl']
                a['n'] += 1
                a['sum'] += err
                a['sq'] += err * err
                a['max'] = max(a['max'], err)
                row += [f'{gt[1]:.4f}', f'{gt[2]:.4f}', f'{gt[3]:.4f}', f'{err:.4f}',
                        f'{math.degrees(abs(_wrap(yaw - gt[3]))):.2f}']
            else:
                row += [''] * 5
            self.files['amcl'][1].writerow(row)

    def _tick(self):
        with self.lock:
            if not self._running() or 'track' not in self.files:
                return
            g = self.m['goals'][self.m['index']]
            if g['state'] != 'active':
                return
            now = self._now()
            gt = self.gt[-1] if self.gt else None
        est = self.nav.robot_pose()           # TF lookups outside the lock
        clear = self.nav.clearance()
        timed_out = False
        with self.lock:
            if not self._running() or self.m['goals'][self.m['index']] is not g or g['state'] != 'active':
                return
            if clear is not None:
                g['_clear'] = min(g['_clear'], clear)
            if gt is None and est is not None:          # no ground truth: path from the estimate
                if g['_last'] is not None:
                    g['_path'] += math.hypot(est[0] - g['_last'][0], est[1] - g['_last'][1])
                g['_last'] = (est[0], est[1])
            row = [f'{now - self.m["t0"]:.3f}', g['index']]
            row += [f'{v:.4f}' for v in gt[1:]] if gt else [''] * 3
            row += [f'{v:.4f}' for v in est] if est else [''] * 3
            row.append('' if clear is None else f'{clear:.3f}')
            f, w = self.files['track']
            w.writerow(row)
            if now - g['_t0'] > self.timeout and not g['_timed_out']:
                g['_timed_out'] = timed_out = True
            self.ticks += 1
            if self.ticks % 20 == 0:                    # flush about every 2 s
                f.flush()
                self.files['amcl'][0].flush()
        if timed_out:
            self.nav.cancel_goal()

    # ---------------- Record ----------------

    @staticmethod
    def _public(g):
        return {k: v for k, v in g.items() if not k.startswith('_')}

    def _summary(self):
        goals = [g for g in self.m['goals'] if g['result']]
        ok = [g for g in goals if g['result'] == 'succeeded']
        a = self.m['amcl']

        def stat(key, rows, fn):
            vals = [g[key] for g in rows if g.get(key) is not None]
            return _round(fn(vals)) if vals else None
        return {
            'goals': len(self.m['goals']), 'finished': len(goals), 'succeeded': len(ok),
            'success_rate': _round(len(ok) / len(goals), 3) if goals else None,
            'total_time_s': _round(sum(g.get('time_s') or 0 for g in goals), 1),
            'total_path_m': _round(sum(g.get('path_m') or 0 for g in goals), 2),
            'mean_goal_error_m': stat('goal_error_m', ok, lambda v: sum(v) / len(v)),
            'max_goal_error_m': stat('goal_error_m', ok, max),
            'min_clearance_m': stat('min_clearance_m', goals, min),
            'recoveries': sum(g.get('recoveries') or 0 for g in goals),
            'amcl_error': ({'samples': a['n'], 'mean_m': _round(a['sum'] / a['n']),
                            'rmse_m': _round(math.sqrt(a['sq'] / a['n'])), 'max_m': _round(a['max'])}
                           if a['n'] else None),
        }

    def _write(self):
        m = self.m
        info = {**m['info'], 'summary': self._summary(), 'goals': [self._public(g) for g in m['goals']]}
        try:
            tmp = m['dir'] / 'run.json.tmp'
            tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, m['dir'] / 'run.json')
            with open(m['dir'] / 'goals.csv', 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(GOAL_COLUMNS)
                for g in m['goals']:
                    w.writerow(['' if g.get(k) is None else g.get(k) for k in GOAL_COLUMNS])
        except OSError as e:
            self.node.get_logger().warn(f'写导航记录失败：{e}')

    def status(self):
        with self.lock:
            m = self.m
            if m is None:
                return None
            out = {'id': m['id'], 'state': m['state'], 'index': m['index'], 'total': len(m['goals']),
                   'has_gt': bool(m['info']['ground_truth']),
                   'goals': [self._public(g) for g in m['goals']], 'summary': self._summary()}
            g = m['goals'][m['index']]
            if m['state'] == 'running' and g['state'] == 'active':
                out['live'] = {'path_m': _round(g['_path'], 2), 'clearance_m': _round(g['_clear']),
                               'elapsed_s': _round(self._now() - g['_t0'], 1)}
            return out
