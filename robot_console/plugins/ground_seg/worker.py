"""Ground segmentation job (runs in the plugin's venv with pypatchworkpp): Patchwork++ labels map points as
ground / not ground, and a new 2D grid is built from the height of each point above the local ground.

Sources
  scans  every recorded body-frame scan (the scan recording of the map) is segmented on its own, as Patchwork++
         is meant to be used; labels are voted per voxel in the map frame and given to the map cloud's points
         (so a cloud cleaned of dynamic objects stays clean).
  map    the map cloud is cut into discs of `radius` around poses of the map's trajectory (every `step` m),
         each disc is turned into that pose's frame and segmented; labels are voted per point.

Grid (same size, resolution and origin as the map's current grid, so Nav2 goals and the scene stay valid)
  ground height per cell = mean z of its ground points, spread to cells without ground up to `fill` m away
  occupied  >= min_points non-ground points with obs_min <= z - ground height <= obs_max
  free      cells with ground points that are not occupied (small gaps closed with `closing` iterations)
  unknown   everything else

Outputs in the job folder: grid.bin (raw cells, map.pgm row order), grid.png, diff.png, labels.png,
ground.pcd, nonground.pcd, result.json.
"""
import json
import os
import sys
import time

import numpy as np

from mapio import (FREE, OCCUPIED, UNKNOWN, ScanRecording, cell_index, emit, fail, read_grid, read_pcd_xyz,
                   read_tum, rotations, save_png, write_pcd_xyz)

PLUGIN = 'ground_seg'
BIAS = 1 << 20          # voxel keys: three 21-bit fields


def keys_of(xyz, voxel):
    k = np.floor(xyz / voxel).astype(np.int64) + BIAS
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


class Votes:
    """Ground / non-ground votes per voxel key, merged in batches to bound memory."""

    def __init__(self):
        self.keys = np.zeros(0, np.int64)
        self.g = np.zeros(0, np.int32)
        self.n = np.zeros(0, np.int32)
        self.pending = []
        self.size = 0

    def add(self, keys, ground):
        self.pending.append((keys, ground))
        self.size += len(keys)
        if self.size > 20_000_000:
            self.merge()

    def merge(self):
        if not self.pending:
            return
        keys = np.concatenate([self.keys] + [k for k, _ in self.pending])
        g = np.concatenate([self.g] + [m.astype(np.int32) for _, m in self.pending])
        n = np.concatenate([self.n] + [(~m).astype(np.int32) for _, m in self.pending])
        uniq, inv = np.unique(keys, return_inverse=True)
        self.keys, self.g, self.n = uniq, np.bincount(inv, g, len(uniq)).astype(np.int32), \
            np.bincount(inv, n, len(uniq)).astype(np.int32)
        self.pending, self.size = [], 0


def make_patchwork(height, min_range, max_range):
    import pypatchworkpp
    p = pypatchworkpp.Parameters()
    p.sensor_height = float(height)
    p.min_range = float(min_range)
    p.max_range = float(max_range)
    p.enable_RNR = False          # the reflected-noise filter needs intensities scaled like the original sensor
    p.verbose = False
    return pypatchworkpp.patchworkpp(p)


def segment(pw, pts):
    """Boolean ground mask of (n, 3) points in the sensor frame."""
    cloud = np.zeros((len(pts), 4), np.float32)
    cloud[:, :3] = pts
    pw.estimateGround(cloud)
    mask = np.zeros(len(pts), bool)
    idx = np.asarray(pw.getGroundIndices()).reshape(-1)
    mask[idx[(idx >= 0) & (idx < len(pts))]] = True
    return mask


def ground_peak(z):
    """Height of the densest 2 cm layer below the sensor (the floor seen around it), or None."""
    z = z[(z < -0.05) & (z > -5)]
    if len(z) < 50:
        return None
    hist, edges = np.histogram(z, bins=np.arange(z.min(), z.max() + 0.02, 0.02))
    if not len(hist):
        return None
    k = int(np.argmax(hist))
    return float(-(edges[k] + edges[k + 1]) / 2)


def estimate_height(local_clouds):
    peaks = []
    for pts in local_clouds:
        r = np.hypot(pts[:, 0], pts[:, 1])
        h = ground_peak(pts[(r > 1.0) & (r < 6.0), 2])
        if h is not None:
            peaks.append(h)
    return float(np.median(peaks)) if peaks else None


def from_scans(p, votes):
    rec = ScanRecording(p['recording'], p['trajectory'])
    if not len(rec):
        raise ValueError('no scan of the recording matches a pose of the trajectory')
    stride = max(1, int(p['scan_stride']))
    order = range(0, len(rec), stride)
    height = p['sensor_height']
    if not height:
        sample = list(range(0, len(rec), max(1, len(rec) // 40)))
        height = estimate_height([rec.scan(i)[0][:, :3] for i in sample])
        if height is None:
            raise ValueError('could not find the floor below the sensor; set the sensor height')
    pw = make_patchwork(height, p['min_range'], p['max_range'])
    rot = rotations(rec.poses[:, 3:7])
    used = 0
    for n, i in enumerate(order):
        pts, pose = rec.scan(i)
        pts = pts[:, :3]
        r = np.linalg.norm(pts, axis=1)
        pts = pts[(r > 0.05) & (r < p['max_range'])]
        if len(pts) < 100:
            continue
        mask = segment(pw, pts)
        world = pts @ rot[i].T + pose[:3]
        votes.add(keys_of(world, p['voxel']), mask)
        used += 1
        if n % 20 == 0:
            emit(phase='segmenting', progress=round(0.05 + 0.75 * n / len(order), 4))
    votes.merge()
    # The scans only vote; the points are the map's own cloud (one per voxel). A cloud cleaned by another tool
    # (e.g. dynamic objects removed) thus keeps its cleaning, and the grid shows what the map contains.
    emit(phase='reading', progress=0.8)
    xyz = read_pcd_xyz(p['cloud'])
    keys = keys_of(xyz, p['voxel'])
    keys, first = np.unique(keys, return_index=True)
    pts = xyz[first]
    pos = np.clip(np.searchsorted(votes.keys, keys), 0, max(len(votes.keys) - 1, 0))
    found = (votes.keys[pos] == keys) if len(votes.keys) else np.zeros(len(keys), bool)
    ground = found & (votes.g[pos] > votes.n[pos])
    return pts, ground, {'scans': used, 'scans_total': len(rec), 'unmatched': rec.unmatched,
                         'sensor_height': round(height, 3), 'unvoted': int((~found).sum())}


def path_samples(poses, step):
    """Indices of poses every `step` m along the trajectory (first and last included)."""
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(poses[:, :3], axis=0), axis=1))]
    marks = np.arange(0, d[-1] + 1e-9, step)
    idx = np.unique(np.r_[np.searchsorted(d, marks), len(poses) - 1])
    return idx[idx < len(poses)]


def from_map(p, votes_unused):
    emit(phase='reading', progress=0.02)
    xyz = read_pcd_xyz(p['cloud'])
    keys = keys_of(xyz, p['voxel'])
    _, first = np.unique(keys, return_index=True)
    pts = xyz[np.sort(first)]
    _, poses = read_tum(p['trajectory'])
    if not len(poses):
        raise ValueError('the trajectory is empty')
    samples = path_samples(poses, p['step'])
    rot = rotations(poses[:, 3:7])
    # bucket the points in radius-sized cells for fast disc queries
    cell = p['radius']
    cxy = np.floor(pts[:, :2] / cell).astype(np.int64)
    cid = (cxy[:, 0] + BIAS) * (2 * BIAS) + (cxy[:, 1] + BIAS)
    order = np.argsort(cid)
    sorted_id = cid[order]

    def near(x, y):
        cx, cy = int(np.floor(x / cell)), int(np.floor(y / cell))
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                c = (cx + dx + BIAS) * (2 * BIAS) + (cy + dy + BIAS)
                lo, hi = np.searchsorted(sorted_id, [c, c + 1])
                out.append(order[lo:hi])
        return np.concatenate(out)

    def local(i):
        idx = near(*poses[i, :2])
        rel = pts[idx] - poses[i, :3]
        keep = np.hypot(rel[:, 0], rel[:, 1]) <= p['radius']
        return idx[keep], (rel[keep] @ rot[i]).astype(np.float32)        # world -> pose frame: R^T (p - t)

    height = p['sensor_height']
    if not height:
        pick = samples[:: max(1, len(samples) // 40)]
        height = estimate_height([local(i)[1] for i in pick])
        if height is None:
            raise ValueError('could not find the floor below the trajectory; set the sensor height')
    pw = make_patchwork(height, p['min_range'], p['radius'])
    g = np.zeros(len(pts), np.int32)
    n = np.zeros(len(pts), np.int32)
    for k, i in enumerate(samples):
        idx, loc = local(i)
        if len(idx) < 100:
            continue
        mask = segment(pw, loc)
        g[idx[mask]] += 1
        n[idx[~mask]] += 1
        if k % 5 == 0:
            emit(phase='segmenting', progress=round(0.05 + 0.75 * k / len(samples), 4))
    ground = g > n
    return pts, ground, {'samples': int(len(samples)), 'sensor_height': round(height, 3),
                         'unvoted': int(((g + n) == 0).sum())}


def fill_heights(height, known, iters):
    """Spread known cell heights into unknown neighbours (4-neighbourhood mean), `iters` rings."""
    h = np.where(known, height, 0.0)
    k = known.copy()
    for _ in range(iters):
        if k.all():
            break
        s = np.zeros_like(h)
        c = np.zeros_like(h)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            s += np.roll(np.where(k, h, 0.0), shift, axis)
            c += np.roll(k, shift, axis)
        # np.roll wraps around: drop what came over the border
        c[0, :] -= k[-1, :]; s[0, :] -= np.where(k[-1, :], h[-1, :], 0)        # noqa: E702
        c[-1, :] -= k[0, :]; s[-1, :] -= np.where(k[0, :], h[0, :], 0)         # noqa: E702
        c[:, 0] -= k[:, -1]; s[:, 0] -= np.where(k[:, -1], h[:, -1], 0)        # noqa: E702
        c[:, -1] -= k[:, 0]; s[:, -1] -= np.where(k[:, 0], h[:, 0], 0)         # noqa: E702
        new = (~k) & (c > 0)
        if not new.any():
            break
        h[new] = s[new] / c[new]
        k |= new
    return h, k


def dilate(mask, iters):
    m = mask.copy()
    for _ in range(iters):
        m = m | np.pad(m[1:, :], ((0, 1), (0, 0))) | np.pad(m[:-1, :], ((1, 0), (0, 0))) \
            | np.pad(m[:, 1:], ((0, 0), (0, 1))) | np.pad(m[:, :-1], ((0, 0), (1, 0)))
    return m


def erode(mask, iters):
    return ~dilate(~mask, iters)


def build_grid(pts, ground, grid, p):
    w, h = grid['width'], grid['height']
    flat, inside = cell_index(pts[:, :2], grid)
    gsel = ground & inside
    cnt = np.bincount(flat[gsel], minlength=w * h).reshape(h, w)
    zsum = np.bincount(flat[gsel], weights=pts[gsel, 2], minlength=w * h).reshape(h, w)
    known = cnt > 0
    height = np.where(known, zsum / np.maximum(cnt, 1), 0.0)
    height, reach = fill_heights(height, known, int(np.ceil(p['fill'] / grid['res'])))
    nsel = ~ground & inside
    nf = flat[nsel]
    ok = reach.reshape(-1)[nf]
    dz = pts[nsel, 2][ok] - height.reshape(-1)[nf[ok]]
    hit = (dz >= p['obs_min']) & (dz <= p['obs_max'])
    occ_cnt = np.bincount(nf[ok][hit], minlength=w * h).reshape(h, w)
    occupied = occ_cnt >= p['min_points']
    free = known & ~occupied
    if p['closing'] > 0:
        closed = erode(dilate(free | occupied, p['closing']), p['closing'])
        free = free | (closed & ~occupied & reach)
    cells = np.full((h, w), UNKNOWN, np.uint8)
    cells[free] = FREE
    cells[occupied] = OCCUPIED
    above = np.zeros((h, w), bool)
    np.logical_or.at(above.reshape(-1), nf[ok][dz > p['obs_max']], True)
    return np.flipud(cells), np.flipud(known), np.flipud(above)


def main():
    job = sys.argv[1]
    with open(os.path.join(job, 'input.json'), encoding='utf-8') as f:
        p = json.load(f)['params']
    t0 = time.time()
    try:
        grid, original = read_grid(p['map_yaml'])
    except Exception as e:  # noqa: BLE001
        fail(job, PLUGIN, 'grid_unreadable', reason=str(e))
    if grid['width'] * grid['height'] > 25_000_000:
        fail(job, PLUGIN, 'grid_too_large')
    try:
        if p['source'] == 'scans':
            pts, ground, info = from_scans(p, Votes())
        else:
            pts, ground, info = from_map(p, None)
    except (ValueError, OSError, KeyError) as e:
        fail(job, PLUGIN, 'segment_failed', reason=str(e))
    if not ground.any():
        fail(job, PLUGIN, 'no_ground')
    emit(phase='grid', progress=0.85)
    cells, ground_cells, above = build_grid(pts, ground, grid, p)
    grid_path = os.path.join(job, 'grid.bin')
    grid_tmp = grid_path + '.tmp'
    cells.tofile(grid_tmp)
    os.replace(grid_tmp, grid_path)
    save_png(os.path.join(job, 'grid.png'), cells)
    # what changed against the map's current grid
    changes = {f'{a}_to_{b}': int(((original == va) & (cells == vb)).sum())
               for a, va in (('free', FREE), ('occupied', OCCUPIED), ('unknown', UNKNOWN))
               for b, vb in (('free', FREE), ('occupied', OCCUPIED), ('unknown', UNKNOWN)) if a != b}
    base = np.where(cells == FREE, 250, np.where(cells == OCCUPIED, 60, 205)).astype(np.uint8)
    diff = np.repeat(base[:, :, None], 3, axis=2)
    diff[(cells == OCCUPIED) & (original != OCCUPIED)] = (220, 40, 40)      # new obstacles
    diff[(cells == FREE) & (original == OCCUPIED)] = (40, 170, 70)          # obstacles removed
    # free <-> unknown is not marked: maps saved with free_thresh 0.25 read every unknown cell as free
    save_png(os.path.join(job, 'diff.png'), diff)
    labels = np.full(cells.shape + (3,), 255, np.uint8)
    labels[above] = (190, 190, 190)                                         # only points above obs_max
    labels[ground_cells] = (120, 200, 130)                                  # ground
    labels[cells == OCCUPIED] = (210, 60, 60)                               # obstacles
    save_png(os.path.join(job, 'labels.png'), labels)
    emit(phase='writing', progress=0.95)
    write_pcd_xyz(os.path.join(job, 'ground.pcd'), pts[ground])
    write_pcd_xyz(os.path.join(job, 'nonground.pcd'), pts[~ground])
    result = {
        'map': p['map'], 'source': p['source'], **info,
        'points': int(len(pts)), 'ground_points': int(ground.sum()), 'nonground_points': int((~ground).sum()),
        'grid': {'width': grid['width'], 'height': grid['height'], 'resolution': grid['res'],
                 'origin': grid['origin']},
        'cells': {'free': int((cells == FREE).sum()), 'occupied': int((cells == OCCUPIED).sum()),
                  'unknown': int((cells == UNKNOWN).sum())},
        'original': {'free': int((original == FREE).sum()), 'occupied': int((original == OCCUPIED).sum()),
                     'unknown': int((original == UNKNOWN).sum())},
        'changes': changes, 'seconds': round(time.time() - t0, 1),
        'params': {k: p[k] for k in ('sensor_height', 'min_range', 'max_range', 'radius', 'step', 'voxel',
                                     'obs_min', 'obs_max', 'min_points', 'closing', 'fill', 'scan_stride')},
    }
    result_path = os.path.join(job, 'result.json')
    result_tmp = result_path + '.tmp'
    with open(result_tmp, 'w', encoding='utf-8') as f:
        json.dump(result, f)
    os.replace(result_tmp, result_path)


if __name__ == '__main__':
    main()
