"""Offline loop-closure back-end for FAST-LIO2 output (keyframes + GICP loop edges + GTSAM pose graph).

Input : <rec>/scans.bin + <rec>/index.csv written by rec_scans.py (body-frame scans and odometry poses).
Output: <out>/traj_odom.tum, traj_loop.tum, map_odom_voxel.pcd, map_loop_voxel.pcd, loop_report.json, figures.

usage: loop_backend.py <rec_dir> <out_dir> [--no-map]
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

from robot_console import fastlio_loop_grid as loop_grid

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
def load(rec):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording')
    parser.add_argument('output')
    parser.add_argument('--no-map', action='store_true')
    parser.add_argument('--map-res', type=float, default=P['map_res'])
    parser.add_argument('--scan-stride', type=int, default=2)
    parser.add_argument('--map-range', type=float, default=P['map_range'])
    args = parser.parse_args()
    P['map_range'] = args.map_range
    if args.map_res <= 0 or args.scan_stride < 1:
        parser.error('map-res must be positive and scan-stride must be >= 1')
    P['map_res'] = args.map_res
    rec, out = args.recording, args.output
    if os.path.isdir(out) and os.listdir(out):
        raise FileExistsError(f'Refusing to overwrite nonempty output directory: {out}')
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    idx, pts, T = load(rec)
    stamps = idx[:, 0]
    kf = select_keyframes(T)
    Tk = T[kf]
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(Tk[:, :3, 3], axis=0), axis=1))])
    print(f'scans {len(T)}, keyframes {len(kf)}, path {s[-1]:.1f} m, {stamps[-1] - stamps[0]:.0f} s')

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
    good = [lp for lp in loops if lp['ok']]
    print(f'accepted loops: {len(good)} / {len(loops)}  ({time.time() - t0:.0f} s)')
    if not good:
        print('no loop accepted - nothing to optimise')
        return

    Tk_new, e0, e1 = optimise(Tk, s, good)
    T_new = all_scan_poses(T, kf, Tk_new)
    dxy = np.linalg.norm(Tk_new[:, :2, 3] - Tk[:, :2, 3], axis=1)
    dz = Tk_new[:, 2, 3] - Tk[:, 2, 3]
    yaw_old = np.unwrap(R.from_matrix(Tk[:, :3, :3]).as_euler('zyx')[:, 0])
    yaw_new = np.unwrap(R.from_matrix(Tk_new[:, :3, :3]).as_euler('zyx')[:, 0])
    dyaw = np.degrees(yaw_new - yaw_old)
    print(f'graph error {e0:.1f} -> {e1:.3f}; correction at the end: xy {dxy[-1]:.3f} m, z {dz[-1]:+.3f} m, yaw {dyaw[-1]:+.3f} deg; '
          f'z range after {T_new[:, 2, 3].min():+.3f}..{T_new[:, 2, 3].max():+.3f}')
    write_tum(os.path.join(out, 'traj_odom.tum'), stamps, T)
    write_tum(os.path.join(out, 'traj_loop.tum'), stamps, T_new)

    # junction consistency: scans of the last 60 s against the map of the first 60 s
    first = np.where(stamps < stamps[0] + 60)[0][::5]
    last = np.where(stamps > stamps[-1] - 60)[0][::5]
    report = dict(params={k: v for k, v in P.items()}, scans=len(T), keyframes=len(kf), path_m=float(s[-1]),
                  input_recording=os.path.realpath(rec), backend_mode='offline_planar',
                  map_scan_stride=args.scan_stride, map_range_m=P['map_range'],
                  loops=[{k: v for k, v in lp.items() if k != 'T_ji'} for lp in loops], accepted=len(good),
                  graph_error=[e0, e1], end_correction=dict(xy_m=float(dxy[-1]), z_m=float(dz[-1]), yaw_deg=float(dyaw[-1])),
                  z_range_odom=[float(T[:, 2, 3].min()), float(T[:, 2, 3].max())],
                  z_range_loop=[float(T_new[:, 2, 3].min()), float(T_new[:, 2, 3].max())],
                  end_minus_start_odom=[float(v) for v in T[-1, :3, 3] - T[0, :3, 3]],
                  end_minus_start_loop=[float(v) for v in T_new[-1, :3, 3] - T_new[0, :3, 3]])
    junction = {}
    a = voxel_down(np.vstack([apply(T[k], scan_xyz(idx, pts, k)) for k in first]), 0.1)
    b0 = voxel_down(np.vstack([apply(T[k], scan_xyz(idx, pts, k)) for k in last]), 0.1)
    b1 = voxel_down(np.vstack([apply(T_new[k], scan_xyz(idx, pts, k)) for k in last]), 0.1)
    # Both evaluations use the same fixed spatial region; do not silently discard each method's bad matches.
    lo, hi = np.percentile(a, [1, 99], axis=0)
    lo -= 0.5
    hi += 0.5
    tree = cKDTree(a)
    for name, b in (('odom', b0), ('loop', b1)):
        support = np.all((b >= lo) & (b <= hi), axis=1)
        d, _ = tree.query(b[support], k=1, workers=P['threads'])
        junction[name] = dict(points=int(len(d)), mean_m=float(d.mean()), median_m=float(np.median(d)),
                              p90_m=float(np.percentile(d, 90)), within_0p1=float((d < 0.1).mean()))
        junction[name + '_clouds'] = (a, b)
        print(f'junction {name}: mean {junction[name]["mean_m"]:.3f} median {junction[name]["median_m"]:.3f} '
              f'p90 {junction[name]["p90_m"]:.3f} m, within 0.1 m {junction[name]["within_0p1"]:.2f}')
    report['junction_region_xyz'] = [lo.tolist(), hi.tolist()]
    report['junction_metric_note'] = '3D nearest-point map consistency in a fixed start-map region; not ground-truth accuracy; includes floor.'
    # Fixed point identities: select geometric overlap once with the accepted planar loop,
    # then evaluate the very same scan points before/after (not independent ground-truth accuracy).
    common_errors = {}
    a = voxel_down(np.vstack([apply(T[k], scan_xyz(idx, pts, k)) for k in first]), 0.1)
    tree = cKDTree(a)
    fixed_before, fixed_after = [], []
    for k in last:
        local = voxel_down(scan_xyz(idx, pts, k), 0.1)
        before, after = apply(T[k], local), apply(T_new[k], local)
        da, _ = tree.query(after, workers=P['threads'])
        wall = (after[:, 2] > -0.19) & (after[:, 2] < 1.31)
        match = (da < 0.3) & wall
        db, _ = tree.query(before[match], workers=P['threads'])
        fixed_before.extend(db.tolist())
        fixed_after.extend(da[match].tolist())
    for name, values in (('odom', fixed_before), ('loop', fixed_after)):
        values = np.asarray(values)
        common_errors[name] = dict(points=len(values), median_m=float(np.median(values)),
                                   p90_m=float(np.percentile(values, 90)), rmse_m=float(np.sqrt(np.mean(values**2))))
    report['junction_fixed_wall_support'] = common_errors
    report['junction_fixed_wall_support_note'] = 'Same source wall points in both evaluations; overlap selected using the optimized map (distance < 0.3 m); in-sample consistency, not independent accuracy.'
    report['junction'] = {k: v for k, v in junction.items() if not k.endswith('_clouds')}
    json.dump(report, open(os.path.join(out, 'loop_report.json'), 'w'), indent=1)

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

    # ---- figure: junction before / after (start part blue, end part red) ----
    floor_z = -0.49
    c = T[0, :2, 3]
    fig, ax = plt.subplots(1, 2, figsize=(15, 6.5), dpi=150)
    for a_, name, title in ((ax[0], 'odom', 'before: FAST-LIO2 only'), (ax[1], 'loop', 'after: with loop closure')):
        a, b = junction[name + '_clouds']
        a, b = wall_slice(a, floor_z), wall_slice(b, floor_z)
        a_.plot(a[:, 0], a[:, 1], '.', ms=0.6, color='#2a78d6', label='first 60 s')
        a_.plot(b[:, 0], b[:, 1], '.', ms=0.6, color='#eb6834', label='last 60 s')
        a_.set_xlim(c[0] - 32, c[0] + 22); a_.set_ylim(c[1] - 16, c[1] + 14); a_.set_aspect('equal'); a_.grid(alpha=0.3)
        j = junction[name]
        a_.set_title(f'{title}\nend part vs start part: median {j["median_m"] * 100:.1f} cm, 90% within {j["p90_m"] * 100:.1f} cm')
        a_.set_xlabel('x [m]'); a_.set_ylabel('y [m]'); a_.legend(markerscale=15, fontsize=8, loc='upper right')
    fig.tight_layout(); fig.savefig(os.path.join(out, 'junction_before_after.png')); plt.close(fig)

    if args.no_map:
        return
    # ---- voxel maps and top views ----
    scans = np.arange(0, len(T), args.scan_stride)
    fig, ax = plt.subplots(1, 2, figsize=(14, 8.5), dpi=150)
    for a_, name, poses, title in ((ax[0], 'odom', T, 'before: FAST-LIO2 only'), (ax[1], 'loop', T_new, 'after: with loop closure')):
        # Grid from per-cell evidence first; the map then drops what the grid judged transient.
        grid_stats, clean = loop_grid.build(rec, out, f'traj_{name}.tum', f'grid_{name}',
                                            dict(map_range=P['map_range'], scan_stride=args.scan_stride))
        print(f'grid {name}: {grid_stats["width"]} x {grid_stats["height"]}, occupied {grid_stats["occupied"]:.2%}, '
              f'free {grid_stats["free"]:.2%} ({time.time() - t0:.0f} s)')
        m = build_map(idx, pts, poses, scans, P['map_res'], clean)
        write_pcd(os.path.join(out, f'map_{name}_voxel.pcd'), m)
        w = wall_slice(m, floor_z)
        a_.plot(w[:, 0], w[:, 1], ',', color='k', alpha=0.25)
        a_.set_aspect('equal'); a_.set_title(f'{title} ({len(m) / 1e6:.2f} M voxels of {P["map_res"]} m)')
        a_.set_xlabel('x [m]'); a_.set_ylabel('y [m]')
        print(f'map {name}: {len(m)} voxels ({time.time() - t0:.0f} s)')
    fig.tight_layout(); fig.savefig(os.path.join(out, 'map_top_view_before_after.png')); plt.close(fig)


if __name__ == '__main__':
    main()
