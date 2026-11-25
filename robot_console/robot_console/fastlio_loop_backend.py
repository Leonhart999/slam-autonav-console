"""Offline loop-closure back-end for FAST-LIO2 output (keyframes + GICP loop edges + GTSAM pose graph).

Input : <rec>/scans.bin + <rec>/index.csv written by fastlio_loop_record.py (body-frame scans and odometry poses).
Output: <out>/traj_odom.tum, traj_loop.tum, map_odom_voxel.pcd, map_loop_voxel.pcd, loop_report.json, figures.
loop_report.json is written even when no loop is accepted (then without the trajectories after optimization).
Lines "PHASE <name>" on stdout mark the stages (reading, matching, optimizing, rebuilding) for the console.

usage: fastlio_loop_backend.py <rec_dir> <out_dir> [--no-map] [--poses scan_poses.tum] [--params JSON]
"""
import argparse
import json
import os
import sys
import time

import gtsam
import matplotlib
import numpy as np
import small_gicp
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation as R, Slerp
from scipy.optimize import least_squares

try:
    from robot_console import fastlio_loop_grid as loop_grid
except ImportError:
    # Run as a file by the console job (python -I, the package is not installed in the loop venv): load the
    # sibling module directly.
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        'fastlio_loop_grid', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fastlio_loop_grid.py'))
    loop_grid = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(loop_grid)

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

P = dict(
    kf_dist=1.0,            # m travelled between keyframes
    kf_angle=15.0,          # deg rotated between keyframes
    max_range=50.0,         # m, points farther from the sensor are not used for loop registration
    map_range=20.0,         # m, same limit for the rebuilt map and grid (angular error grows with range)
    loop_radius=10.0,       # m, a keyframe this close to a much earlier one is a loop candidate
    loop_min_gap=100.0,     # m of path between the two keyframes of a loop
    src_window=10.0,        # m of path around the new keyframe merged into the source submap
    tgt_window=15.0,        # m of path around the old keyframe merged into the target submap
    submap_res=0.1,         # m voxel of the submaps
    stages=((0.5, 3.0), (0.25, 1.0), (0.15, 0.5)),  # GICP: (downsampling m, max correspondence m)
    accept_overlap=0.6,     # share of source points within 0.3 m of the target after alignment
    accept_rmse=0.15,       # m, rms distance of those points
    # The graph adjusts only x/y/yaw. A world-z yaw correction leaves z and gravity tilt unchanged.
    odo_xy_per_sqrt_m=0.01, odo_xy_min=0.005,
    odo_yaw_deg_per_sqrt_m=0.1, odo_yaw_per_rad=0.03,
    loop_rot_deg=0.5, loop_trans=0.05,
    min_loop_spacing=4.0,   # m between successive loop edges (overlapping submaps are correlated)
    max_loop_shift=5.0, max_loop_yaw_deg=10.0,
    max_loop_tilt_deg=1.5, max_loop_z=0.25,
    min_trans_eig_ratio=0.002,
    map_res=0.1, threads=4,
)


# ---------- data ----------
def load(rec, poses=None):
    """Index rows, points and odometry poses of a recording. poses: TUM file listing the scans to use (by stamp),
    e.g. data/scan_poses.tum of a map saved while the recorder was still running."""
    idx = np.atleast_2d(np.loadtxt(os.path.join(rec, 'index.csv')))
    if idx.shape[1] != 10 or len(idx) < 2 or not np.isfinite(idx).all():
        raise ValueError('Invalid recording index: expected >= 2 finite rows with 10 columns')
    delta = np.diff(idx[:, 0])
    if np.any(delta < -0.001):
        raise ValueError('Recording timestamps move backwards by more than 1 ms')
    # Paired lidar scans can finish a few microseconds out of order; retain their scan offsets.
    idx = idx[np.argsort(idx[:, 0], kind='stable')]
    _, first = np.unique(idx[:, 0], return_index=True)
    idx = idx[np.sort(first)]
    if poses:
        micro = lambda t: np.round(t * 1e6).astype(np.int64)   # noqa: E731
        listed = micro(np.loadtxt(poses, ndmin=2)[:, 0])
        idx = idx[np.isin(micro(idx[:, 0]), listed)]
        if len(idx) < 2:
            raise ValueError('The pose file lists fewer than 2 scans of the recording')
    pts = np.memmap(os.path.join(rec, 'scans.bin'), dtype=np.float32, mode='r').reshape(-1, 4)
    if np.any(idx[:, 1:3] < 0) or np.any(idx[:, 1] + idx[:, 2] > len(pts)):
        raise ValueError('Recording index points outside scans.bin')
    T = np.tile(np.eye(4), (len(idx), 1, 1))
    T[:, :3, :3] = R.from_quat(idx[:, 6:10]).as_matrix()
    T[:, :3, 3] = idx[:, 3:6]
    return idx, pts, T


def scan_xyz(idx, pts, k, max_range=None):
    o, n = int(idx[k, 1]), int(idx[k, 2])
    p = np.asarray(pts[o:o + n, :3], dtype=np.float64)
    return p[np.einsum('ij,ij->i', p, p) < (max_range or P['max_range']) ** 2]


def voxel_down(p, res):
    if len(p) == 0:
        return p
    # Exact row keys: a linear combination of signed coordinates can alias distinct voxels.
    key = np.ascontiguousarray(np.floor(p / res).astype(np.int64))
    packed = key.view(np.dtype((np.void, key.dtype.itemsize * 3))).reshape(-1)
    _, i = np.unique(packed, return_index=True)
    return p[i]


def apply(T, p):
    return p @ T[:3, :3].T + T[:3, 3]


def select_keyframes(T):
    kf, last = [0], T[0]
    for k in range(1, len(T)):
        d = np.linalg.norm(T[k, :3, 3] - last[:3, 3])
        a = np.degrees(np.linalg.norm(R.from_matrix(last[:3, :3].T @ T[k, :3, :3]).as_rotvec()))
        if d >= P['kf_dist'] or a >= P['kf_angle']:
            kf.append(k)
            last = T[k]
    if kf[-1] != len(T) - 1:
        kf.append(len(T) - 1)
    return np.array(kf)


def submap(idx, pts, T, kfs, ref):
    """Merge the scans of keyframes kfs (scan indices) into the frame of scan `ref`."""
    inv = np.linalg.inv(T[ref])
    cloud = [apply(inv @ T[k], scan_xyz(idx, pts, k)) for k in kfs]
    return voxel_down(np.vstack(cloud), P['submap_res'])


# ---------- loop detection and registration ----------
def find_candidates(Tk, s):
    xy = Tk[:, :3, 3]
    out = []
    for i in range(len(Tk)):
        early = np.where(s < s[i] - P['loop_min_gap'])[0]
        if len(early) == 0:
            continue
        d = np.linalg.norm(xy[early] - xy[i], axis=1)
        j = early[np.argmin(d)]
        if d.min() < P['loop_radius']:
            out.append((i, int(j), float(d.min())))
    return out


def register(src, tgt, init):
    T = init.copy()
    res = None
    for down, corr in P['stages']:
        t_pc, t_tree = small_gicp.preprocess_points(tgt, downsampling_resolution=down, num_neighbors=20, num_threads=P['threads'])
        s_pc, _ = small_gicp.preprocess_points(src, downsampling_resolution=down, num_neighbors=20, num_threads=P['threads'])
        res = small_gicp.align(t_pc, s_pc, t_tree, T, registration_type='GICP', max_correspondence_distance=corr,
                               num_threads=P['threads'], max_iterations=50)
        T = np.array(res.T_target_source)
    return T, res


def refine_planar(src, tgt, init, seed, level_R=None):
    """Point-to-plane ICP in x/y/yaw, preserving the odometry scan's z and tilt.

    GICP supplies a basin of attraction, not a 6-DOF correction to apply blindly.
    Covariance eigenvectors give target normals; robust residuals limit moving objects.
    """
    level_R = np.eye(3) if level_R is None else level_R
    target, _ = small_gicp.preprocess_points(tgt, 0.15, num_neighbors=20, num_threads=P['threads'])
    tx = np.asarray(target.points())[:, :3] @ level_R.T
    cov = np.asarray(target.covs())[:, :3, :3]
    values, vecs = np.linalg.eigh(cov)
    normals = vecs[:, :, 0] @ level_R.T
    flat = values[:, 0] / np.maximum(values[:, 1], 1e-12) < 0.35
    tx, normals = tx[flat], normals[flat]
    tree = cKDTree(tx)
    sx = voxel_down(src, 0.15)
    base = apply(init, sx) @ level_R.T
    drift = seed @ np.linalg.inv(init)
    drift_rotation = level_R @ drift[:3, :3] @ level_R.T
    drift_translation = level_R @ drift[:3, 3]
    v = np.array([drift_translation[0], drift_translation[1], np.arctan2(drift_rotation[1, 0], drift_rotation[0, 0])])
    used = np.empty(0, dtype=int)
    for _ in range(30):
        rot = R.from_euler('z', v[2]).as_matrix()
        moved = base @ rot.T + np.array([v[0], v[1], 0.0])
        dist, ids = tree.query(moved, workers=P['threads'])
        keep = dist < 0.6
        if keep.sum() < 100:
            raise ValueError('Too few planar correspondences')
        bp, tp, n = base[keep], tx[ids[keep]], normals[ids[keep]]
        def residual(x):
            c, s = np.cos(x[2]), np.sin(x[2])
            p = bp.copy()
            p[:, 0] = c * bp[:, 0] - s * bp[:, 1] + x[0]
            p[:, 1] = s * bp[:, 0] + c * bp[:, 1] + x[1]
            return np.einsum('ij,ij->i', p - tp, n)
        opt = least_squares(residual, v, loss='huber', f_scale=0.08, max_nfev=20)
        change = np.linalg.norm(opt.x - v)
        v = opt.x
        used = np.where(keep)[0]
        if change < 1e-5:
            break
    correction = np.eye(4)
    correction[:3, :3] = level_R.T @ R.from_euler('z', v[2]).as_matrix() @ level_R
    correction[:3, 3] = level_R.T @ np.array([v[0], v[1], 0.0])
    return correction @ init, int(len(used))


def fit_quality(src, tgt_tree, T):
    d, _ = tgt_tree.query(apply(T, src), k=1, workers=P['threads'])
    near = d < 0.3
    return float(near.mean()), float(np.sqrt(np.mean(d[near] ** 2))) if near.any() else 9.9


def pose_delta(T):
    """x y z [m], roll pitch yaw [deg] of a transform."""
    return [float(v) for v in T[:3, 3]] + [float(v) for v in R.from_matrix(T[:3, :3]).as_euler('xyz', degrees=True)]


# ---------- pose graph ----------
def optimise(Tk, s, loops):
    """Planar pose graph; keep the front-end's height and gravity alignment exactly."""
    graph, init = gtsam.NonlinearFactorGraph(), gtsam.Values()
    planar = [gtsam.Pose2(float(t[0, 3]), float(t[1, 3]), float(np.arctan2(t[1, 0], t[0, 0]))) for t in Tk]
    graph.add(gtsam.PriorFactorPose2(0, planar[0], gtsam.noiseModel.Diagonal.Sigmas(np.full(3, 1e-6))))
    for k, pose in enumerate(planar):
        init.insert(k, pose)
    for k in range(len(Tk) - 1):
        rel = planar[k].between(planar[k + 1])
        d = max(s[k + 1] - s[k], 1e-3)
        syaw = np.radians(P['odo_yaw_deg_per_sqrt_m']) * np.sqrt(d) + P['odo_yaw_per_rad'] * abs(rel.theta())
        sxy = P['odo_xy_per_sqrt_m'] * np.sqrt(d) + P['odo_xy_min']
        sig = np.array([sxy, sxy, max(syaw, 1e-5)])
        graph.add(gtsam.BetweenFactorPose2(k, k + 1, rel, gtsam.noiseModel.Diagonal.Sigmas(sig)))
    lsig = np.array([P['loop_trans'], P['loop_trans'], np.radians(P['loop_rot_deg'])])
    for lp in loops:
        # Registration is 6-DOF; project the inferred world pose into the same planar chart.
        inferred = Tk[lp['j']] @ lp['T_ji']
        pi = gtsam.Pose2(float(inferred[0, 3]), float(inferred[1, 3]), float(np.arctan2(inferred[1, 0], inferred[0, 0])))
        noise = gtsam.noiseModel.Robust.Create(gtsam.noiseModel.mEstimator.Huber.Create(1.345),
                                               gtsam.noiseModel.Diagonal.Sigmas(lsig))
        graph.add(gtsam.BetweenFactorPose2(lp['j'], lp['i'], planar[lp['j']].between(pi), noise))
    params = gtsam.LevenbergMarquardtParams()
    params.setMaxIterations(200)
    result = gtsam.LevenbergMarquardtOptimizer(graph, init, params).optimize()
    out = Tk.copy()
    for k in range(len(Tk)):
        pose = result.atPose2(k)
        dyaw = pose.theta() - planar[k].theta()
        out[k, :3, :3] = R.from_euler('z', dyaw).as_matrix() @ Tk[k, :3, :3]
        out[k, :2, 3] = (pose.x(), pose.y())
    return out, float(graph.error(init)), float(graph.error(result))


def all_scan_poses(T, kf, Tk_new):
    """Interpolate map-to-odometry corrections between keyframes without scan-boundary jumps."""
    corr = Tk_new @ np.linalg.inv(T[kf])
    grid = np.arange(len(T), dtype=float)
    out_corr = np.tile(np.eye(4), (len(T), 1, 1))
    out_corr[:, :3, :3] = Slerp(kf.astype(float), R.from_matrix(corr[:, :3, :3]))(grid).as_matrix()
    for axis in range(3):
        out_corr[:, axis, 3] = np.interp(grid, kf, corr[:, axis, 3])
    return out_corr @ T


# ---------- map and figures ----------
def map_scan(idx, pts, T, k, clean):
    """World points of one scan for the map; drops obstacle-height points the grid judged transient."""
    world = apply(T[k], scan_xyz(idx, pts, k, P['map_range']))
    if clean is None:
        return world
    return world[~loop_grid.transient_points(world, T[k, :3, 3], clean)]


def build_map(idx, pts, T, scans, res, clean=None):
    keys = np.empty(0, dtype=np.int64)
    off = 20000  # voxel index offset keeps keys positive
    for c in range(0, len(scans), 200):
        chunk = np.vstack([map_scan(idx, pts, T, k, clean) for k in scans[c:c + 200]])
        v = np.floor(chunk / res).astype(np.int64) + off
        keys = np.union1d(keys, (v[:, 0] << 42) | (v[:, 1] << 21) | v[:, 2])
    v = np.stack([keys >> 42, (keys >> 21) & 0x1FFFFF, keys & 0x1FFFFF], axis=1) - off
    return (v + 0.5) * res


def write_pcd(path, p):
    p = p.astype(np.float32)
    with open(path, 'wb') as f:
        f.write((f'# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n'
                 f'COUNT 1 1 1\nWIDTH {len(p)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(p)}\nDATA binary\n').encode())
        f.write(p.tobytes())


def write_tum(path, stamps, T):
    q = R.from_matrix(T[:, :3, :3]).as_quat()
    np.savetxt(path, np.hstack([stamps[:, None], T[:, :3, 3], q]), fmt=['%.6f'] + ['%.5f'] * 3 + ['%.6f'] * 4)


def wall_slice(p, floor_z):
    return p[(p[:, 2] > floor_z + 0.3) & (p[:, 2] < floor_z + 1.8)]


# Parameters the console's loop dialog may override (--params); the others stay fixed.
TUNABLE = ('loop_radius', 'loop_min_gap', 'accept_overlap', 'accept_rmse', 'max_loop_shift', 'max_loop_yaw_deg')
REVISIT_GAP = 30.0     # m of path: accepted loops closer than this at both ends show the same revisit
MAX_JUNCTIONS = 4      # revisits shown in the junction figure and report


def phase(name):
    """Stage marker; the console job (map_loop.py) shows it as the task's progress."""
    print(f'PHASE {name}', flush=True)


def write_report(out, report):
    with open(os.path.join(out, 'loop_report.json'), 'w') as f:
        json.dump(report, f, indent=1)


def detect(idx, pts, T, kf, Tk, s, t0):
    """Verify every loop candidate by submap registration; returns all checked loops (accepted or not)."""
    cands = find_candidates(Tk, s)
    print(f'loop candidates: {len(cands)}')
    loops, tgt_cache = [], {}
    last_accepted_s = -np.inf
    for i, j, d in cands:
        if s[i] - last_accepted_s < P['min_loop_spacing'] and i != len(kf) - 1:
            continue
        tgt_sel = np.where((np.abs(s - s[j]) <= P['tgt_window']) & (s < s[i] - P['loop_min_gap']))[0]
        src_sel = np.where((np.abs(s - s[i]) <= P['src_window']) & (s > s[j] + P['loop_min_gap']))[0]
        src = submap(idx, pts, T, kf[src_sel], kf[i])
        if j not in tgt_cache:
            tgt = submap(idx, pts, T, kf[tgt_sel], kf[j])
            tgt_cache[j] = (tgt, cKDTree(tgt))
        tgt, tree = tgt_cache[j]
        init = np.linalg.inv(Tk[j]) @ Tk[i]
        ov0, rm0 = fit_quality(src, tree, init)
        T_full, res = register(src, tgt, init)
        T_ji, planar_matches = refine_planar(src, tgt, init, T_full, Tk[j, :3, :3])
        ov1, rm1 = fit_quality(src, tree, T_ji)
        full_drift = Tk[j] @ T_full @ np.linalg.inv(Tk[i])
        full_shift = (Tk[j] @ T_full)[:3, 3] - Tk[i, :3, 3]
        # drift = transform that moves the odometry pose of keyframe i onto where the loop says it is
        drift = Tk[j] @ T_ji @ np.linalg.inv(Tk[i])
        moved = Tk[j] @ T_ji
        shift = moved[:3, 3] - Tk[i, :3, 3]
        ev = np.linalg.eigvalsh(np.array(res.H)[3:, 3:])
        drift_rpy = pose_delta(drift)[3:]
        eig_ratio = float(ev[0] / max(ev[-1], 1e-12))
        reasons = []
        if not res.converged:
            reasons.append('not_converged')
        if ov1 < P['accept_overlap'] or rm1 > P['accept_rmse']:
            reasons.append('poor_fit')
        if np.linalg.norm(shift[:2]) > P['max_loop_shift'] or abs(drift_rpy[2]) > P['max_loop_yaw_deg']:
            reasons.append('excessive_correction')
        full_rpy = pose_delta(full_drift)[3:]
        if abs(full_shift[2]) > P['max_loop_z'] or np.max(np.abs(full_rpy[:2])) > P['max_loop_tilt_deg']:
            reasons.append('ground_inconsistent')
        if eig_ratio < P['min_trans_eig_ratio']:
            reasons.append('degenerate_geometry')
        ok = not reasons
        if ok:
            last_accepted_s = s[i]
        lp = dict(i=int(i), j=int(j), s_i=float(s[i]), s_j=float(s[j]), dist=d, ok=ok, converged=bool(res.converged),
                  overlap_before=ov0, rmse_before=rm0, overlap_after=ov1, rmse_after=rm1,
                  shift_xyz=[float(v) for v in shift], drift_rpy_deg=drift_rpy,
                  trans_eig_ratio=eig_ratio, rejection_reasons=reasons, T_ji=T_ji,
                  full_registration_rpy_deg=full_rpy, planar_matches=planar_matches)
        loops.append(lp)
        print(f'  kf {i:4d} (s {s[i]:6.1f}) -> kf {j:3d} (s {s[j]:5.1f}) d {d:5.2f} | overlap {ov0:.2f}->{ov1:.2f} rmse {rm0:.3f}->{rm1:.3f}'
              f' | shift {shift[0]:+.3f} {shift[1]:+.3f} {shift[2]:+.3f} m  rpy {lp["drift_rpy_deg"][0]:+.2f} {lp["drift_rpy_deg"][1]:+.2f} {lp["drift_rpy_deg"][2]:+.2f} deg'
              f' | eig {lp["trans_eig_ratio"]:.3f} {"OK" if ok else "REJECTED"}')
    print(f'accepted loops: {sum(lp["ok"] for lp in loops)} / {len(loops)}  ({time.time() - t0:.0f} s)')
    return loops


def revisits(good):
    """One representative loop (the middle one) per revisit, largest correction first, and the number of revisits.
    Consecutive accepted loops whose old and new ends both lie within REVISIT_GAP of path show the same place."""
    groups = []
    for lp in sorted(good, key=lambda lp: lp['s_i']):
        last = groups[-1][-1] if groups else None
        if last and abs(lp['s_i'] - last['s_i']) < REVISIT_GAP and abs(lp['s_j'] - last['s_j']) < REVISIT_GAP:
            groups[-1].append(lp)
        else:
            groups.append([lp])
    reps = sorted((g[len(g) // 2] for g in groups), key=lambda lp: -float(np.linalg.norm(lp['shift_xyz'][:2])))
    return reps[:MAX_JUNCTIONS], len(groups)


def nearest_stats(d):
    return dict(points=int(len(d)), mean_m=float(d.mean()), median_m=float(np.median(d)),
                p90_m=float(np.percentile(d, 90)), within_0p1=float((d < 0.1).mean()))


def junction(idx, pts, T, T_new, kf, s, lp, floor_z):
    """Map consistency where loop lp closes: the scans around its new end against the map around its old end,
    with odometry poses (before) and loop poses (after). In-sample consistency, not ground-truth accuracy."""
    tgt = kf[(np.abs(s - lp['s_j']) <= P['tgt_window']) & (s < lp['s_i'] - P['loop_min_gap'])]
    src = kf[(np.abs(s - lp['s_i']) <= P['src_window']) & (s > lp['s_j'] + P['loop_min_gap'])]
    out = dict(i=lp['i'], j=lp['j'], s_i=lp['s_i'], s_j=lp['s_j'])
    clouds, trees = {}, {}
    for name, poses in (('odom', T), ('loop', T_new)):
        a = voxel_down(np.vstack([apply(poses[k], scan_xyz(idx, pts, k)) for k in tgt]), 0.1)
        b = voxel_down(np.vstack([apply(poses[k], scan_xyz(idx, pts, k)) for k in src]), 0.1)
        # Region metric: points of the new visit inside the bounds of the old map (includes the floor).
        lo, hi = np.percentile(a, [1, 99], axis=0)
        support = np.all((b >= lo - 0.5) & (b <= hi + 0.5), axis=1)
        trees[name] = cKDTree(a)
        out[name] = nearest_stats(trees[name].query(b[support], k=1, workers=P['threads'])[0])
        clouds[name] = (a, b)
    # Wall metric: the same wall points of the new visit before and after, selected once where the optimized
    # map overlaps (distance < 0.3 m).
    before, after = [], []
    for k in src:
        local = voxel_down(scan_xyz(idx, pts, k), 0.1)
        moved = apply(T_new[k], local)
        da, _ = trees['loop'].query(moved, workers=P['threads'])
        match = (da < 0.3) & (moved[:, 2] > floor_z + 0.3) & (moved[:, 2] < floor_z + 1.8)
        db, _ = trees['odom'].query(apply(T[k], local[match]), workers=P['threads'])
        before.extend(db.tolist())
        after.extend(da[match].tolist())
    for name, values in (('wall_odom', before), ('wall_loop', after)):
        values = np.asarray(values)
        out[name] = (dict(points=len(values), median_m=float(np.median(values)), p90_m=float(np.percentile(values, 90)),
                          rmse_m=float(np.sqrt(np.mean(values ** 2)))) if len(values) else None)
    return out, clouds


def junction_figure(path, items, floor_z):
    """One row per revisit: wall slice of the old map (blue) and of the new visit (red), before and after."""
    bounds = []
    for j, clouds in items:
        both = np.vstack([wall_slice(c, floor_z) for c in clouds['loop']])
        if len(both) < 10:
            both = np.vstack(clouds['loop'])
        lo, hi = np.percentile(both[:, :2], [1, 99], axis=0)
        bounds.append((lo - 2.0, hi + 2.0))
    # Row height follows the aspect ratio of the area, so long corridors do not leave empty space.
    heights = [float(np.clip(7.0 * (hi[1] - lo[1]) / max(hi[0] - lo[0], 1e-3), 2.5, 9.0)) + 1.3 for lo, hi in bounds]
    fig, ax = plt.subplots(len(items), 2, figsize=(15, sum(heights)), dpi=120, squeeze=False,
                           gridspec_kw={'height_ratios': heights})
    for row, (j, clouds), (lo, hi) in zip(ax, items, bounds):
        walls = {name: tuple(wall_slice(c, floor_z) for c in clouds[name]) for name in ('odom', 'loop')}
        for a_, name, title in ((row[0], 'odom', 'before: FAST-LIO2 only'), (row[1], 'loop', 'after: with loop closure')):
            a, b = walls[name]
            a_.plot(a[:, 0], a[:, 1], '.', ms=0.6, color='#2a78d6', label=f'first visit (path {j["s_j"]:.0f} m)')
            a_.plot(b[:, 0], b[:, 1], '.', ms=0.6, color='#eb6834', label=f'revisit (path {j["s_i"]:.0f} m)')
            a_.set_xlim(lo[0], hi[0]); a_.set_ylim(lo[1], hi[1]); a_.set_aspect('equal'); a_.grid(alpha=0.3)
            w = j['wall_' + name]
            detail = (f'wall points: median {w["median_m"] * 100:.1f} cm, 90% within {w["p90_m"] * 100:.1f} cm'
                      if w else 'no matching wall points')
            a_.set_title(f'{title}\n{detail}')
            a_.set_xlabel('x [m]'); a_.set_ylabel('y [m]'); a_.legend(markerscale=15, fontsize=8, loc='upper right')
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording')
    parser.add_argument('output')
    parser.add_argument('--no-map', action='store_true')
    parser.add_argument('--map-res', type=float, default=P['map_res'])
    parser.add_argument('--scan-stride', type=int, default=2)
    parser.add_argument('--map-range', type=float, default=P['map_range'])
    parser.add_argument('--poses', help='TUM file with one pose per scan; only the scans it lists are used '
                                        '(a map saved before the recording ended)')
    parser.add_argument('--params', help='JSON object overriding ' + ', '.join(TUNABLE))
    args = parser.parse_args()
    P['map_range'] = args.map_range
    if args.map_res <= 0 or args.scan_stride < 1:
        parser.error('map-res must be positive and scan-stride must be >= 1')
    P['map_res'] = args.map_res
    if args.params:
        extra = json.loads(args.params)
        if not isinstance(extra, dict) or set(extra) - set(TUNABLE):
            parser.error('params must be a JSON object with keys from: ' + ', '.join(TUNABLE))
        for key, value in extra.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                parser.error(f'{key} must be a positive number')
            P[key] = float(value)
    rec, out = args.recording, args.output
    if os.path.isdir(out) and os.listdir(out):
        raise FileExistsError(f'Refusing to overwrite nonempty output directory: {out}')
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    phase('reading')
    idx, pts, T = load(rec, args.poses)
    stamps = idx[:, 0]
    kf = select_keyframes(T)
    Tk = T[kf]
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(Tk[:, :3, 3], axis=0), axis=1))])
    print(f'scans {len(T)}, keyframes {len(kf)}, path {s[-1]:.1f} m, {stamps[-1] - stamps[0]:.0f} s')

    phase('matching')
    loops = detect(idx, pts, T, kf, Tk, s, t0)
    good = [lp for lp in loops if lp['ok']]
    write_tum(os.path.join(out, 'traj_odom.tum'), stamps, T)
    report = dict(params={k: v for k, v in P.items()}, scans=len(T), keyframes=len(kf), path_m=float(s[-1]),
                  duration_s=float(stamps[-1] - stamps[0]),
                  input_recording=os.path.realpath(rec),
                  input_poses=os.path.realpath(args.poses) if args.poses else None, backend_mode='offline_planar',
                  map_scan_stride=args.scan_stride, map_range_m=P['map_range'], map_res_m=P['map_res'],
                  loops=[{k: v for k, v in lp.items() if k != 'T_ji'} for lp in loops], accepted=len(good))
    if not good:
        print('no loop accepted - nothing to optimise')
        write_report(out, report)
        return

    phase('optimizing')
    Tk_new, e0, e1 = optimise(Tk, s, good)
    T_new = all_scan_poses(T, kf, Tk_new)
    dxy = np.linalg.norm(Tk_new[:, :2, 3] - Tk[:, :2, 3], axis=1)
    dz = Tk_new[:, 2, 3] - Tk[:, 2, 3]
    yaw_old = np.unwrap(R.from_matrix(Tk[:, :3, :3]).as_euler('zyx')[:, 0])
    yaw_new = np.unwrap(R.from_matrix(Tk_new[:, :3, :3]).as_euler('zyx')[:, 0])
    dyaw = np.degrees(yaw_new - yaw_old)
    print(f'graph error {e0:.1f} -> {e1:.3f}; correction at the end: xy {dxy[-1]:.3f} m, z {dz[-1]:+.3f} m, yaw {dyaw[-1]:+.3f} deg; '
          f'z range after {T_new[:, 2, 3].min():+.3f}..{T_new[:, 2, 3].max():+.3f}')
    write_tum(os.path.join(out, 'traj_loop.tum'), stamps, T_new)
    # Floor height in the map frame, for the wall slices: sensor height estimated from the scans themselves.
    hs = loop_grid.sensor_height(idx, pts, T[:, :3, :3])
    floor_z = float(np.median(T[:, 2, 3])) - hs
    report.update(graph_error=[e0, e1],
                  end_correction=dict(xy_m=float(dxy[-1]), z_m=float(dz[-1]), yaw_deg=float(dyaw[-1])),
                  max_correction=dict(xy_m=float(dxy.max()), yaw_deg=float(np.abs(dyaw).max())),
                  z_range_odom=[float(T[:, 2, 3].min()), float(T[:, 2, 3].max())],
                  z_range_loop=[float(T_new[:, 2, 3].min()), float(T_new[:, 2, 3].max())],
                  end_minus_start_odom=[float(v) for v in T[-1, :3, 3] - T[0, :3, 3]],
                  end_minus_start_loop=[float(v) for v in T_new[-1, :3, 3] - T_new[0, :3, 3]],
                  sensor_height_m=hs, floor_z_m=floor_z)

    reps, n_revisits = revisits(good)
    items = []
    for lp in reps:
        j, clouds = junction(idx, pts, T, T_new, kf, s, lp, floor_z)
        items.append((j, clouds))
        w0, w1 = j['wall_odom'], j['wall_loop']
        if w0 and w1:
            print(f'junction kf {lp["i"]} -> {lp["j"]}: wall median {w0["median_m"]:.3f} -> {w1["median_m"]:.3f} m, '
                  f'p90 {w0["p90_m"]:.3f} -> {w1["p90_m"]:.3f} m')
    report['revisits'] = n_revisits
    report['junctions'] = [j for j, _ in items]
    report['junction_metric_note'] = ('Per revisit: scans around the new end of a loop against the map around its old '
                                      'end, both with odometry (before) and with loop poses (after). Region metric '
                                      'includes the floor; wall metric uses the same wall points before and after, '
                                      'selected where the optimized map overlaps (< 0.3 m). In-sample map consistency, '
                                      'not ground-truth accuracy.')

    # ---- figure: correction along the path ----
    fig, axs = plt.subplots(1, 3, figsize=(15, 5), dpi=150)
    ax = axs[:2]
    ax[0].plot(T[:, 0, 3], T[:, 1, 3], color='#2a78d6', lw=1.5, ls='--', label='FAST-LIO2 odometry (before)')
    ax[0].plot(T_new[:, 0, 3], T_new[:, 1, 3], color='#eb6834', lw=1.5, label='after loop closure')
    for lp in good:
        ax[0].plot([Tk[lp['i'], 0, 3], Tk[lp['j'], 0, 3]], [Tk[lp['i'], 1, 3], Tk[lp['j'], 1, 3]], color='#1baf7a', lw=0.8)
    ax[0].plot(*T[0, :2, 3], 'k*', ms=10, label='start')
    ax[0].set_aspect('equal'); ax[0].grid(alpha=0.3); ax[0].legend(fontsize=8)
    ax[0].set_xlabel('x [m]'); ax[0].set_ylabel('y [m]'); ax[0].set_title('trajectory (green: loop edges)')
    ax[1].plot(s, dxy, color='#2a78d6', label='horizontal shift')
    ax[1].plot(s, dz, color='#eb6834', ls='--', label='height shift')
    ax[1].grid(alpha=0.3); ax[1].legend(fontsize=8); ax[1].set_xlabel('path length [m]')
    ax[1].set_ylabel('translation correction [m]')
    ax[1].set_title('position correction')
    axs[2].plot(s, dyaw, color='#2a78d6')
    axs[2].set_xlabel('path length [m]'); axs[2].set_ylabel('yaw correction [deg]')
    axs[2].set_title('heading correction'); axs[2].grid(alpha=0.3)
    np.savetxt(os.path.join(out, 'keyframe_corrections.csv'), np.column_stack((s, dxy, dz, dyaw)),
               delimiter=',', header='path_m,horizontal_shift_m,height_shift_m,yaw_change_deg', comments='')
    fig.tight_layout(); fig.savefig(os.path.join(out, 'trajectory_correction.png')); plt.close(fig)
    if items:
        junction_figure(os.path.join(out, 'junction_before_after.png'), items, floor_z)
    write_report(out, report)

    if args.no_map:
        return
    # ---- voxel maps, grids and top views ----
    phase('rebuilding')
    scans = np.arange(0, len(T), args.scan_stride)
    maps = {}
    fig, ax = plt.subplots(1, 2, figsize=(14, 8.5), dpi=150)
    for a_, name, poses, title in ((ax[0], 'odom', T, 'before: FAST-LIO2 only'), (ax[1], 'loop', T_new, 'after: with loop closure')):
        # Grid from per-cell evidence first; the map then drops what the grid judged transient.
        # The trajectory files list exactly the scans used here, so the grid follows --poses as well.
        grid_stats, clean = loop_grid.build(rec, out, f'traj_{name}.tum', f'grid_{name}',
                                            dict(map_range=P['map_range'], scan_stride=args.scan_stride))
        print(f'grid {name}: {grid_stats["width"]} x {grid_stats["height"]}, occupied {grid_stats["occupied"]:.2%}, '
              f'free {grid_stats["free"]:.2%} ({time.time() - t0:.0f} s)')
        m = build_map(idx, pts, poses, scans, P['map_res'], clean)
        write_pcd(os.path.join(out, f'map_{name}_voxel.pcd'), m)
        maps[name] = dict(voxels=int(len(m)), grid=dict(width=grid_stats['width'], height=grid_stats['height'],
                                                        occupied=grid_stats['occupied'], free=grid_stats['free']))
        w = wall_slice(m, floor_z)
        a_.plot(w[:, 0], w[:, 1], ',', color='k', alpha=0.25)
        a_.set_aspect('equal'); a_.set_title(f'{title} ({len(m) / 1e6:.2f} M voxels of {P["map_res"]} m)')
        a_.set_xlabel('x [m]'); a_.set_ylabel('y [m]')
        print(f'map {name}: {len(m)} voxels ({time.time() - t0:.0f} s)')
    fig.tight_layout(); fig.savefig(os.path.join(out, 'map_top_view_before_after.png')); plt.close(fig)
    report['maps'] = maps
    report['elapsed_s'] = round(time.time() - t0, 1)
    write_report(out, report)


if __name__ == '__main__':
    main()
