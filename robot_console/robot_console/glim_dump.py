"""Read a GLIM dump directory (written by glim_rosnode when it exits, ROS parameter dump_path).

Layout used here (GLIM 1.2, glim/src/glim/mapping/global_mapping.cpp and sub_map.cpp):
  graph.txt              first line "num_submaps: N"
  traj_imu.txt           TUM poses of the IMU after global optimization (with loop closure), world frame
  odom_imu.txt           the same frames from odometry only (no loop closure), odom frame
  000000/ .. 0000NN/     one folder per submap:
    data.txt             "T_world_origin:" followed by a 4x4 matrix (optimized submap pose)
    points_compact.bin   float32 x y z per point, in the submap origin frame

The submap points are what GLIM itself exports as the map (GlobalMapping::export_points): each submap
transformed by its optimized T_world_origin. Only the first N folders listed in graph.txt are read, so stale
folders left over from an earlier, longer run in the same dump_path are ignored.
"""
import os

import numpy as np


class DumpError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def num_submaps(dump_dir):
    path = os.path.join(dump_dir, 'graph.txt')
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('num_submaps:'):
                    return int(line.split(':', 1)[1])
    except OSError:
        raise DumpError('glim_dump_missing', path=dump_dir)
    raise DumpError('glim_dump_invalid', path=path)


def read_matrix(lines, key):
    """The 4x4 matrix printed by Eigen on the 4 lines after 'key:'."""
    for i, line in enumerate(lines):
        if line.strip() == key + ':':
            rows = [list(map(float, lines[i + 1 + r].split())) for r in range(4)]
            m = np.array(rows)
            if m.shape == (4, 4):
                return m
            break
    return None


def submap_points(submap_dir):
    """(N, 3) float32 points of one submap in world coordinates."""
    with open(os.path.join(submap_dir, 'data.txt'), encoding='utf-8') as f:
        lines = f.read().splitlines()
    T = read_matrix(lines, 'T_world_origin')
    if T is None:
        raise DumpError('glim_dump_invalid', path=os.path.join(submap_dir, 'data.txt'))
    path = os.path.join(submap_dir, 'points_compact.bin')
    raw = np.fromfile(path, dtype='<f4')
    if raw.size % 3:
        raise DumpError('glim_dump_invalid', path=path)
    pts = raw.reshape(-1, 3).astype(np.float64)
    return (pts @ T[:3, :3].T + T[:3, 3]).astype(np.float32)


def map_points(dump_dir):
    """All submap points of the dump in the world (glim_map) frame."""
    n = num_submaps(dump_dir)
    if n <= 0:
        raise DumpError('glim_dump_empty', path=dump_dir)
    parts = [submap_points(os.path.join(dump_dir, f'{i:06d}')) for i in range(n)]
    return np.concatenate(parts) if parts else np.empty((0, 3), np.float32)


def fresh(dump_dir, since):
    """True when the dump was (re)written at or after the wall-clock time `since`."""
    try:
        return os.path.getmtime(os.path.join(dump_dir, 'graph.txt')) >= since - 1.0
    except OSError:
        return False


def tum_range(path):
    """(first, last) timestamp of a TUM file, or None."""
    first = last = None
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                parts = line.split()
                if len(parts) == 8:
                    t = float(parts[0])
                    first = t if first is None else first
                    last = t
    except OSError:
        return None
    return None if first is None else (first, last)


def apply_to_runs(mapping, stack, prof):
    """Stop hook (ProcessManager.on_stopped): replace est.tum of every run recorded from this GLIM process with
    GLIM's globally optimized trajectory, cut to the run's own time span. Returns a coded message or None."""
    if stack.started_at is None:
        return None
    dump = prof['dump_dir']
    if not fresh(dump, stack.started_at):
        return {'code': 'glim_dump_missing', 'params': {'path': dump}}
    done = 0
    for run_id in mapping.runs_of(stack.name, stack.started_at):
        d = os.path.join(mapping.recorder.runs_dir, run_id)
        online = os.path.join(d, 'est_online.tum')
        span = tum_range(online if os.path.exists(online) else os.path.join(d, 'est.tum'))
        if span is None:
            continue
        lines = trajectory(dump, span[0] - 0.05, span[1] + 0.05)
        if lines and mapping.replace_estimate(
                run_id, lines, 'GLIM traj_imu.txt (global optimization with loop closure)',
                {'glim_dump': dump, 'glim_submaps': num_submaps(dump)}):
            done += 1
    return {'code': 'glim_traj_applied', 'params': {'runs': done}}


def trajectory(dump_dir, t_from=None, t_to=None, name='traj_imu.txt'):
    """TUM lines of the optimized trajectory, optionally limited to [t_from, t_to] (s)."""
    out = []
    with open(os.path.join(dump_dir, name), encoding='utf-8') as f:
        for line in f:
            parts = line.split()
            if len(parts) != 8:
                continue
            t = float(parts[0])
            if (t_from is not None and t < t_from) or (t_to is not None and t > t_to):
                continue
            out.append(line if line.endswith('\n') else line + '\n')
    return out
