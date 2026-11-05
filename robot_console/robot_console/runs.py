"""运行记录：每次建图自动在 ~/runs/<编号>/ 下记录估计轨迹、真值轨迹和概况，给实验评估页（第 5 步）用。

目录内容：
  est.tum   SLAM 的估计位姿（FAST-LIO：/Odometry，camera_init 坐标系；GLIM：/glim_ros/odom，glim_odom 坐标系，
            GLIM 退出后换成它全局优化后的轨迹，在线结果另存为 est_online.tum），TUM 格式：t x y z qx qy qz qw
  gt.tum    仿真真值（/ground_truth，world 坐标系，原样记录，评估时用 evo 对齐）
  run.json  类型、场景、开始结束时间、轨迹长度、体素数、保存过的地图等
用 evo 评估：evo_ape tum gt.tum est.tum -a

A run is kept in memory until the estimated pose has moved MIN_TRAVEL from its first pose (or a map is
saved); only then is the folder created. Restarts that never move no longer leave empty runs behind.
"""
import json
import math
import os
import time
from collections import deque

MIN_TRAVEL = 0.5          # m, straight-line distance from the first estimated pose
PENDING_MAX_LINES = 100000  # per file; a long stationary wait only drops its oldest lines


class RunRecorder:

    def __init__(self, runs_dir, log):
        self.runs_dir = os.path.expanduser(runs_dir)
        self._log = log
        self.run_id = None
        self._dir = None
        self._est = self._gt = None
        self._info = {}
        self._last_write = 0.0
        self._pending = None      # (stamp, info) of a run that has not been written yet
        self._origin = None       # first estimated position of the pending run
        self._buf_est = self._buf_gt = None

    def begin(self, info):
        """开始一条新记录（第一条里程计到达时调用）。The folder is created later, see commit()."""
        self.end()
        frame = info.get('frame') or 'camera_init'
        self._info = {'type': 'mapping', 'started': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'ended': None, 'maps': [], **info,
                      'files': {'est': f'est.tum ({frame})', 'gt': 'gt.tum (world)'}}
        self._pending = time.strftime('%Y%m%d_%H%M%S')
        self._origin = None
        self._buf_est = deque(maxlen=PENDING_MAX_LINES)
        self._buf_gt = deque(maxlen=PENDING_MAX_LINES)

    @property
    def pending(self):
        return self._pending is not None

    def commit(self):
        """Create the run folder and write the buffered poses. Returns the run id or None."""
        if self._pending is None:
            return self.run_id
        stamp, est, gt = self._pending, self._buf_est, self._buf_gt
        self._pending = self._buf_est = self._buf_gt = None
        run_id = f'map_{stamp}'
        n = 2
        while os.path.exists(os.path.join(self.runs_dir, run_id)):
            run_id = f'map_{stamp}_{n}'
            n += 1
        try:
            self._dir = os.path.join(self.runs_dir, run_id)
            os.makedirs(self._dir)
            self._est = open(os.path.join(self._dir, 'est.tum'), 'w')
            self._gt = open(os.path.join(self._dir, 'gt.tum'), 'w')
        except OSError as e:
            self._log.warn(f'无法创建运行记录 {run_id}：{e}')
            self._dir = self._est = self._gt = None
            return None
        self.run_id = run_id
        self._info = {'id': run_id, **self._info}
        self._est.writelines(est)
        self._gt.writelines(gt)
        self._write()
        return run_id

    @staticmethod
    def _line(t, p, q):
        return f'{t:.9f} {p.x:.6f} {p.y:.6f} {p.z:.6f} {q.x:.6f} {q.y:.6f} {q.z:.6f} {q.w:.6f}\n'

    def est(self, t, pose):
        line = self._line(t, pose.position, pose.orientation)
        if self._est:
            self._est.write(line)
        elif self._pending is not None:
            self._buf_est.append(line)
            p = pose.position
            if self._origin is None:
                self._origin = (p.x, p.y, p.z)
            elif math.dist(self._origin, (p.x, p.y, p.z)) >= MIN_TRAVEL:
                self.commit()

    def gt(self, t, pose):
        line = self._line(t, pose.position, pose.orientation)
        if self._gt:
            self._gt.write(line)
        elif self._pending is not None:
            self._buf_gt.append(line)

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
            self._pending = self._buf_est = self._buf_gt = None
            return
        if self.run_id is None:
            return
        self._info.update(fields, ended=time.strftime('%Y-%m-%d %H:%M:%S'))
        self._write()
        for f in (self._est, self._gt):
            if f:
                f.close()
        self._est = self._gt = None
        self.run_id = None

    def replace_est(self, run_id, lines, source, extra=None):
        """Replace est.tum of a run with a final trajectory (e.g. GLIM's globally optimized one after it exits).

        The online estimate is kept as est_online.tum. For the run being recorded right now, est.tum is closed,
        so poses that still arrive are no longer appended. extra: fields merged into run.json.
        Returns False when the run folder does not exist.
        """
        d = os.path.join(self.runs_dir, run_id)
        if not os.path.isdir(d):
            return False
        current = run_id == self.run_id
        if current and self._est:
            self._est.close()
            self._est = None
        est = os.path.join(d, 'est.tum')
        online = os.path.join(d, 'est_online.tum')
        if os.path.exists(est) and not os.path.exists(online):
            os.replace(est, online)
        tmp = est + '.tmp'
        with open(tmp, 'w') as f:
            f.writelines(lines)
        os.replace(tmp, est)
        fields = {'est_source': source, **(extra or {})}
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
            for f in (self._est, self._gt):
                if f:
                    f.flush()
            tmp = os.path.join(self._dir, 'run.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._info, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(self._dir, 'run.json'))
        except OSError as e:
            self._log.warn(f'写运行记录失败：{e}')
