"""Occupancy grid from recorded scans and optimized poses (evidence per cell, not a union of points).

A cell is occupied only if obstacle-height points were seen there in enough scans, from a long enough
stretch of the path, and few beams of other scans passed through it. Height is measured from the floor
under the sensor of each scan, and points far from the sensor are not used.

usage: fastlio_loop_grid.py <recording> <result_dir> [--traj traj_loop.tum] [--prefix grid_loop]
"""
import argparse
import json
import os
import time

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial.transform import Rotation as R

OCC, FREE, UNKNOWN = 0, 254, 205
MAX_CELLS = 40_000_000   # cells of the area covered by the trajectory plus the range, before cropping

D = dict(
    res=0.05,            # m per cell
    map_range=20.0,      # m, points farther from the sensor are not used (angular error grows with range)
    obs_min=0.10,        # m above the local floor
    obs_max=0.70,
    ground_tol=0.05,     # m, |height| below this is floor
    scan_stride=2,
    ray_deg=0.5,         # azimuth bin of the free-space rays
    min_hits=4,          # scans that must see an obstacle point in the cell
    min_span=1.0,        # m of path between the first and last scan that hit the cell
    occ_ratio=0.10,      # hits / (hits + beams that passed through); low, so glass that is seen through stays
    robot_radius=0.3,    # m, cells the robot drove over are free; 0 disables
    closing=2,           # iterations of the 3x3 closing of free space
)


def load_recording(rec):
    """Index rows (sorted, one per stamp) and points of a recording; the recorder may still be writing."""
    rows = []
    with open(os.path.join(rec, 'index.csv'), encoding='ascii') as f:
        for line in f:
            parts = line.split()
            if line.endswith('\n') and len(parts) == 10:
                rows.append([float(v) for v in parts])
    if not rows:
        raise ValueError('The scan recording is empty')
    idx = np.array(rows)
    idx = idx[np.argsort(idx[:, 0], kind='stable')]
    _, first = np.unique(idx[:, 0], return_index=True)
    idx = idx[np.sort(first)]
    path = os.path.join(rec, 'scans.bin')
    pts = np.memmap(path, dtype=np.float32, mode='r', shape=(os.path.getsize(path) // 16, 4))
    idx = idx[idx[:, 1] + idx[:, 2] <= len(pts)]
    return idx, pts


def load_poses(tum, idx):
    """Poses of the trajectory and the index rows they belong to. A trajectory written while the recording
    was still growing covers only its first scans: rows are matched by stamp."""
    t = np.loadtxt(tum, ndmin=2)
    if len(t) != len(idx):
        micro = lambda s: np.round(s * 1e6).astype(np.int64)   # noqa: E731
        idx = idx[np.isin(micro(idx[:, 0]), micro(t[:, 0]))]
    if len(t) < 2 or len(t) != len(idx) or np.abs(t[:, 0] - idx[:, 0]).max() > 1e-3:
        raise ValueError('Trajectory does not match the recording scan by scan')
    return idx, R.from_quat(t[:, 4:8]).as_matrix(), t[:, 1:4]


def sensor_height(idx, pts, rot):
    """Height of the body origin above the floor: lowest strong peak of nearby point heights."""
    z = []
    for k in np.linspace(0, len(idx) - 1, 200).astype(int):
        o, n = int(idx[k, 1]), int(idx[k, 2])
        w = np.asarray(pts[o:o + n:4, :3], dtype=np.float64) @ rot[k].T
        z.append(w[np.hypot(w[:, 0], w[:, 1]) < 4.0, 2])
    hist, edges = np.histogram(np.concatenate(z), bins=np.arange(-3.0, 0.0, 0.02))
    k = np.where(hist >= 0.5 * hist.max())[0][0]
    k = k + int(np.argmax(hist[k:k + 5]))
    return float(-0.5 * (edges[k] + edges[k + 1]))


def accumulate(idx, pts, rot, pos, p, log=print):
    """Per-cell evidence: scans with an obstacle hit, beams passing through, floor hits, path span of the hits."""
    res, rng = p['res'], p['map_range']
    hs = p.get('sensor_height') or sensor_height(idx, pts, rot)
    xmin, ymin = pos[:, :2].min(axis=0) - rng - 0.5
    xmax, ymax = pos[:, :2].max(axis=0) + rng + 0.5
    w, h = int(np.ceil((xmax - xmin) / res)), int(np.ceil((ymax - ymin) / res))
    if w * h > MAX_CELLS:
        raise ValueError(f'Grid of {w} x {h} cells is too large; use a coarser resolution')
    hit = np.zeros(w * h, np.uint16)
    miss = np.zeros(w * h, np.uint16)
    gnd = np.zeros(w * h, np.uint16)
    s_lo = np.full(w * h, np.inf, np.float32)
    s_hi = np.full(w * h, -np.inf, np.float32)
    path = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pos[:, :2], axis=0), axis=1))]
    nbin = int(round(360 / p['ray_deg']))
    steps = np.arange(0.0, rng, res)
    ang = (np.arange(nbin) + 0.5) * (2 * np.pi / nbin) - np.pi
    ray_dx, ray_dy = np.outer(np.cos(ang), steps), np.outer(np.sin(ang), steps)

    def cells(x, y):
        ix, iy = ((x - xmin) / res).astype(np.int64), ((y - ymin) / res).astype(np.int64)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        return np.unique(iy[ok] * w + ix[ok])

    t0 = time.time()
    scans = range(0, len(idx), p['scan_stride'])
    for done, k in enumerate(scans):
        o, n = int(idx[k, 1]), int(idx[k, 2])
        b = np.asarray(pts[o:o + n, :3], dtype=np.float64)
        b = b[np.einsum('ij,ij->i', b, b) < rng * rng]
        d = b @ rot[k].T                       # offset from the sensor in world axes
        height = d[:, 2] + hs                  # above the floor under this scan's sensor
        x, y = d[:, 0] + pos[k, 0], d[:, 1] + pos[k, 1]
        band = (height >= p['obs_min']) & (height <= p['obs_max'])
        floor = np.abs(height) <= p['ground_tol']
        c = cells(x[band], y[band])
        hit[c] += 1
        s_lo[c] = np.minimum(s_lo[c], path[k])
        s_hi[c] = np.maximum(s_hi[c], path[k])
        gnd[cells(x[floor], y[floor])] += 1
        # Free-space rays: per azimuth bin, up to one cell before the nearest obstacle-height return.
        rho = np.hypot(d[band, 0], d[band, 1])
        ab = ((np.arctan2(d[band, 1], d[band, 0]) + np.pi) / (2 * np.pi) * nbin).astype(int) % nbin
        near = np.full(nbin, np.inf)
        np.minimum.at(near, ab, rho)
        seen = np.isfinite(near)
        through = steps[None, :] < (near[seen, None] - res)
        cm = cells((pos[k, 0] + ray_dx[seen])[through], (pos[k, 1] + ray_dy[seen])[through])
        miss[cm[~np.isin(cm, c, assume_unique=True)]] += 1
        if done % 500 == 0:
            log(f'  grid evidence: scan {done}/{len(scans)} ({time.time() - t0:.0f} s)')
    shape = (h, w)
    span = np.where(hit > 0, s_hi - s_lo, 0).astype(np.float32)
    return dict(hit=hit.reshape(shape), miss=miss.reshape(shape), gnd=gnd.reshape(shape),
                span=span.reshape(shape), origin=np.array([xmin, ymin]), sensor_height=hs)


def driven_mask(shape, origin, pos, p):
    driven = np.zeros(shape, bool)
    if p['robot_radius'] <= 0:
        return driven
    ix = ((pos[:, 0] - origin[0]) / p['res']).astype(int)
    iy = ((pos[:, 1] - origin[1]) / p['res']).astype(int)
    driven[iy, ix] = True
    r = int(round(p['robot_radius'] / p['res']))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return ndimage.binary_dilation(driven, structure=xx * xx + yy * yy <= r * r)


def classify(ev, pos, p):
    hit, miss = ev['hit'].astype(np.float32), ev['miss'].astype(np.float32)
    driven = driven_mask(hit.shape, ev['origin'], pos, p)
    occupied = ((hit >= p['min_hits']) & (ev['span'] >= p['min_span'])
                & (hit >= p['occ_ratio'] * (hit + miss)) & ~driven)
    free = ((ev['gnd'] > 0) | (ev['miss'] >= 2) | driven) & ~occupied
    if p['closing']:
        free = ndimage.binary_closing(free, structure=np.ones((3, 3)), iterations=p['closing']) & ~occupied
    grid = np.full(hit.shape, UNKNOWN, np.uint8)
    grid[free] = FREE
    grid[occupied] = OCC
    return grid


def transient_points(world, sensor_pos, clean):
    """True for obstacle-height points whose cell the grid did not accept (moving objects, stray returns)."""
    p = clean['params']
    height = world[:, 2] - sensor_pos[2] + p['sensor_height']
    band = (height >= p['obs_min']) & (height <= p['obs_max'])
    occupied = clean['occupied']
    ix = ((world[:, 0] - clean['origin'][0]) / p['res']).astype(np.int64)
    iy = ((world[:, 1] - clean['origin'][1]) / p['res']).astype(np.int64)
    inside = (ix >= 0) & (ix < occupied.shape[1]) & (iy >= 0) & (iy < occupied.shape[0])
    keep = np.zeros(len(world), bool)
    keep[inside] = occupied[iy[inside], ix[inside]]
    return band & ~keep


def crop_to_content(grid, origin, res, margin=0.5):
    ys, xs = np.where(grid != UNKNOWN)
    m = int(round(margin / res))
    y0, y1 = max(ys.min() - m, 0), min(ys.max() + m + 1, grid.shape[0])
    x0, x1 = max(xs.min() - m, 0), min(xs.max() + m + 1, grid.shape[1])
    return grid[y0:y1, x0:x1], origin + np.array([x0, y0]) * res


def write_grid(prefix, grid, origin, res):
    image = Image.fromarray(np.flipud(grid))
    image.save(prefix + '.pgm')
    image.save(prefix + '_preview.png')
    with open(prefix + '.yaml', 'w') as f:
        f.write(f'image: {os.path.basename(prefix)}.pgm\nmode: trinary\nresolution: {res}\n'
                f'origin: [{origin[0]:.3f}, {origin[1]:.3f}, 0.0]\nnegate: 0\n'
                f'occupied_thresh: 0.65\nfree_thresh: 0.15\n')


def build_grid(recording, trajectory, out_prefix, params=None, save_evidence=False, log=print):
    """Grid from <recording>/scans.bin + index.csv and a TUM trajectory with one pose per scan.

    Writes <out_prefix>.pgm / .yaml / _preview.png / .json; returns (stats, data for transient_points)."""
    p = dict(D, **(params or {}))
    idx, pts = load_recording(recording)
    idx, rot, pos = load_poses(trajectory, idx)
    ev = accumulate(idx, pts, rot, pos, p, log)
    if save_evidence:
        np.savez(out_prefix + '_evidence.npz', **ev)
    full = classify(ev, pos, p)
    if not (full != UNKNOWN).any():
        raise ValueError('No scan point within the range and height limits')
    grid, origin = crop_to_content(full, ev['origin'], p['res'])
    write_grid(out_prefix, grid, origin, p['res'])
    p['sensor_height'] = float(ev['sensor_height'])
    stats = dict(params=p, trajectory=os.path.basename(trajectory), scans=len(range(0, len(idx), p['scan_stride'])),
                 width=int(grid.shape[1]), height=int(grid.shape[0]),
                 origin=[round(float(origin[0]), 3), round(float(origin[1]), 3), 0.0],
                 occupied=float((grid == OCC).mean()), free=float((grid == FREE).mean()))
    with open(out_prefix + '.json', 'w') as f:
        json.dump(stats, f, indent=1)
    return stats, dict(occupied=full == OCC, origin=ev['origin'], params=p)


def build(recording, result_dir, traj='traj_loop.tum', prefix='grid_loop', params=None, save_evidence=False, log=print):
    return build_grid(recording, os.path.join(result_dir, traj), os.path.join(result_dir, prefix),
                      params, save_evidence, log)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('recording')
    ap.add_argument('result_dir')
    ap.add_argument('--traj', default='traj_loop.tum')
    ap.add_argument('--prefix', default='grid_loop')
    ap.add_argument('--save-evidence', action='store_true')
    ap.add_argument('--sensor-height', type=float, default=0.0, help='m above the floor; 0 = estimate from the scans')
    for key, value in D.items():
        ap.add_argument('--' + key.replace('_', '-'), type=type(value), default=value)
    args = ap.parse_args()
    params = {k: getattr(args, k) for k in D}
    params['sensor_height'] = args.sensor_height
    print(json.dumps(build(args.recording, args.result_dir, args.traj, args.prefix, params, args.save_evidence)[0]))


if __name__ == '__main__':
    main()
