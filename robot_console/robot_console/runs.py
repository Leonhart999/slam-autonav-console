"""运行记录：每次建图自动在 ~/runs/<编号>/ 下记录估计轨迹、真值轨迹和概况，给实验评估页（第 5 步）用。

目录内容：
  est.tum   SLAM 的估计位姿（FAST-LIO：/Odometry，camera_init 坐标系；GLIM：/glim_ros/odom，glim_odom 坐标系，
            GLIM 退出后换成它全局优化后的轨迹，在线结果另存为 est_online.tum），TUM 格式：t x y z qx qy qz qw
  gt.tum    仿真真值（/ground_truth，world 坐标系，原样记录，评估时用 evo 对齐）
  run.json  类型、场景、开始结束时间、轨迹长度、体素数、保存过的地图等
用 evo 评估：evo_ape tum gt.tum est.tum -a

Run types: 'mapping' (simulation) and 'playback' (the SLAM was fed from a rosbag, see playback.py
session_info). Playback runs are named play_<time>_..., record ground truth only when the bag itself carries it
(gt_source 'bag'; a simulation started during the playback must not leak its /ground_truth into the run), and
may add
  ref.tum   reference odometry played from the bag (e.g. wheel odometry, sensor profile `reference`); not ground truth
  lag.csv   bag time (s), how far the SLAM odometry was behind the playback clock (s), about 1 Hz

A run is kept in memory until the estimated pose has moved MIN_TRAVEL from its first pose (or a map is
saved); only then is the folder created. Restarts that never move no longer leave empty runs behind.
"""
import json
import math
import os
import time
import uuid
from collections import deque

MIN_TRAVEL = 0.5          # m, straight-line distance from the first estimated pose
PENDING_MAX_LINES = 100000  # per file; a long stationary wait only drops its oldest lines
FILES = {'est': 'est.tum', 'gt': 'gt.tum', 'ref': 'ref.tum', 'lag': 'lag.csv'}


class RunRecorder:

    def __init__(self, runs_dir, log, background=False):
        self.runs_dir = os.path.expanduser(runs_dir)
        self._log = log
        self.run_id = None
        self._dir = None
        self._files = {}          # stream ('est', 'gt', 'ref', 'lag') -> open file of the committed run
        self._streams = ()        # streams the current run records
        self._info = {}
        self._last_write = 0.0
        self._pending = None      # (stamp, info) of a run that has not been written yet
        self._origin = None       # first estimated position of the pending run
        self._bufs = {}           # stream -> lines buffered while the run is pending
        self._lag = None          # [n, sum, max] of the lag samples
        from .record_io import RecordIO
        self._io = RecordIO(log) if background else None

    def begin(self, info):
        """开始一条新记录（第一条里程计到达时调用）。The folder is created later, see commit()."""
        self.end()
        frame = info.get('frame') or 'camera_init'
        kind = info.get('type') or 'mapping'
        streams = ['est']
        files = {'est': f'est.tum ({frame})'}
        if kind != 'playback' or info.get('gt_source') == 'bag':
            streams.append('gt')
            files['gt'] = 'gt.tum (world)'
        if kind == 'playback':
            if info.get('ref_source'):
                streams.append('ref')
                files['ref'] = f'ref.tum ({info["ref_source"]}, reference odometry, not ground truth)'
            streams.append('lag')
            files['lag'] = 'lag.csv (bag time s, SLAM lag s)'
        self._streams = tuple(streams)
        self._info = {'type': kind, 'started': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'ended': None, 'maps': [], **info, 'files': files}
        self._info['type'] = kind
        self._pending = time.strftime('%Y%m%d_%H%M%S')
        self._origin = None
        self._bufs = {s: deque(maxlen=PENDING_MAX_LINES) for s in streams}
        self._lag = None

    @property
    def pending(self):
        return self._pending is not None

    @property
    def accepts_gt(self):
        """Does the current run record ground truth? (No for a playback whose bag has none.)"""
        return 'gt' in self._streams

    def commit(self):
        """Create the run folder and write the buffered poses. Returns the run id or None."""
        if self._pending is None:
            return self.run_id
        stamp, bufs = self._pending, self._bufs
        self._pending, self._bufs = None, {}
        prefix = 'play' if self._info.get('type') == 'playback' else 'map'
        run_id = f'{prefix}_{stamp}'
        n = 2
        if self._io:
            # Reserve a unique id without querying a slow filesystem in the ROS callback.
            run_id += '_' + uuid.uuid4().hex[:12]
        while not self._io and os.path.exists(os.path.join(self.runs_dir, run_id)):
            run_id = f'{prefix}_{stamp}_{n}'
            n += 1
        try:
            self._dir = os.path.join(self.runs_dir, run_id)
            if self._io:
                self._io.mkdir(self._dir)
                self._files = {s: self._io.open(os.path.join(self._dir, FILES[s])) for s in self._streams}
            else:
                os.makedirs(self._dir)
                self._files = {s: open(os.path.join(self._dir, FILES[s]), 'w') for s in self._streams}
        except OSError as e:
            self._log.warn(f'无法创建运行记录 {run_id}：{e}')
            self._dir = None
            self._files = {}
            return None
        self.run_id = run_id
        self._info = {'id': run_id, **self._info}
        for s, f in self._files.items():
            f.writelines(bufs.get(s) or ())
        self._write()
        return run_id

    @staticmethod
    def _line(t, p, q):
        return f'{t:.9f} {p.x:.6f} {p.y:.6f} {p.z:.6f} {q.x:.6f} {q.y:.6f} {q.z:.6f} {q.w:.6f}\n'

    def _put(self, stream, line):
        f = self._files.get(stream)
        if f:
            f.write(line)
        elif self._pending is not None and stream in self._bufs:
            self._bufs[stream].append(line)

    def est(self, t, pose):
        line = self._line(t, pose.position, pose.orientation)
        if self._files.get('est'):
            self._files['est'].write(line)
        elif self._pending is not None:
            self._bufs['est'].append(line)
            p = pose.position
            if self._origin is None:
                self._origin = (p.x, p.y, p.z)
            elif math.dist(self._origin, (p.x, p.y, p.z)) >= MIN_TRAVEL:
                self.commit()

    def gt(self, t, pose):
        if 'gt' in self._streams:
            self._put('gt', self._line(t, pose.position, pose.orientation))

    def ref(self, t, pose):
        """Reference odometry of a playback run (bag's wheel odometry etc.)."""
        if 'ref' in self._streams:
            self._put('ref', self._line(t, pose.position, pose.orientation))

    def lag(self, t, lag):
        """SLAM lag behind the playback clock at bag time t; the summary goes into run.json 'lag'."""
        if 'lag' not in self._streams:
            return
        self._put('lag', f'{t:.3f},{lag:.3f}\n')
        n, total, peak = self._lag or (0, 0.0, 0.0)
        self._lag = (n + 1, total + lag, max(peak, lag))
        self._info['lag'] = {'mean': round(self._lag[1] / self._lag[0], 2), 'max': round(self._lag[2], 2),
                             'samples': self._lag[0]}

    def update(self, force=False, **fields):
        """更新概况；最多 5 s 写一次盘（force=True 时立即写），控制台崩溃也只丢最后几秒。"""
        if self._pending is not None:
            self._info.update(fields)   # kept for commit()
            return
        if self.run_id is None:
            return
        self._info.update(fields)
        if force or time.monotonic() - self._last_write > 5.0:
            self._write()

    def add_map(self, name):
        self.commit()             # saving a map always keeps the run
        if self.run_id is not None:
            self._info['maps'].append(name)
            self._write()

    def end(self, **fields):
        if self._pending is not None:
            # Never moved far enough: drop the run without touching the disk
            self._pending = None
            self._bufs = {}
            return
        if self.run_id is None:
            return
        self._info.update(fields, ended=time.strftime('%Y-%m-%d %H:%M:%S'))
        self._write()
        for f in self._files.values():
            f.close()
        self._files = {}
        self.run_id = None

    def replace_est(self, run_id, lines, source, extra=None):
        """Replace est.tum of a run with a final trajectory (e.g. GLIM's globally optimized one after it exits).

        The online estimate is kept as est_online.tum. For the run being recorded right now, est.tum is closed,
        so poses that still arrive are no longer appended. extra: fields merged into run.json.
        Returns False when the run folder does not exist.
        """
        d = os.path.join(self.runs_dir, run_id)
        if not self._io and not os.path.isdir(d):
            return False
        current = run_id == self.run_id
        if current and self._files.get('est'):
            self._files.pop('est').close()
        fields = {'est_source': source, **(extra or {})}
        if self._io:
            from .record_io import replace_est_files
            result = self._io.submit(d, replace_est_files, d, list(lines), fields)
            if current:
                self._info.update(fields)
                self._info.setdefault('files', {})['est_online'] = 'est_online.tum (online odometry)'
                self._write()
            return result
        est = os.path.join(d, 'est.tum')
        online = os.path.join(d, 'est_online.tum')
        if os.path.exists(est) and not os.path.exists(online):
            os.replace(est, online)
        tmp = est + '.tmp'
        with open(tmp, 'w') as f:
            f.writelines(lines)
        os.replace(tmp, est)
        if current:
            self._info.update(fields)
            self._info.setdefault('files', {})['est_online'] = 'est_online.tum (online odometry)'
            self._write()
        else:
            path = os.path.join(d, 'run.json')
            try:
                with open(path, encoding='utf-8') as f:
                    info = json.load(f)
            except (OSError, ValueError):
                info = {'id': run_id}
            info.update(fields)
            info.setdefault('files', {})['est_online'] = 'est_online.tum (online odometry)'
            with open(path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            os.replace(path + '.tmp', path)
        return True

    def _write(self):
        self._last_write = time.monotonic()
        try:
            for f in self._files.values():
                f.flush()
            if self._io:
                self._io.metadata(self._dir, self._info)
                return
            tmp = os.path.join(self._dir, 'run.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._info, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(self._dir, 'run.json'))
        except OSError as e:
            self._log.warn(f'写运行记录失败：{e}')

    def flush(self):
        """Wait for earlier disk operations without holding the mapping lock."""
        if self._io:
            self._io.flush()
        else:
            for f in self._files.values():
                f.flush()

    def export(self, directory):
        """Queue a consistent copy before any subsequently queued trajectory writes."""
        return self._io.export(directory, self._dir)

    def shutdown(self):
        self.end()
        if self._io:
            self._io.shutdown()
