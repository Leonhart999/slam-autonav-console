"""Dynamic point removal job (runs in the plugin's venv with the dufomap package).

Every recorded body-frame scan is inserted into DUFOMap with its pose from the map's trajectory (rays from the
pose origin, i.e. the FAST-LIO body frame; the LiDAR is a few cm to dm away from it, well below DUFOMap's
inflation distances). Afterwards every point of map.pcd is labelled static or dynamic: a point is dynamic when it
lies in space that scans saw as free.

Outputs in the job folder: static.pcd, dynamic.pcd, preview.png (top view: grey static, red dynamic),
result.json.
"""
import json
import os
import sys
import time

import numpy as np

from mapio import ScanRecording, emit, fail, read_pcd_xyz, save_png, write_pcd_xyz

PLUGIN = 'dynamic_removal'
PREVIEW = 1600          # px, longest side of preview.png


def preview(static, dynamic, path):
    both = np.vstack([static[:, :2], dynamic[:, :2]]) if len(dynamic) else static[:, :2]
    if not len(both):
        save_png(path, np.full((64, 64, 3), 255, np.uint8))
        return {'resolution': 0.05, 'origin': [0.0, 0.0], 'width': 64, 'height': 64}
    lo, hi = both.min(axis=0), both.max(axis=0)
    res = max(float((hi - lo).max()) / PREVIEW, 0.05)
    w, h = (np.floor((hi - lo) / res).astype(int) + 1)

    def density(pts):
        c = np.floor((pts[:, :2] - lo) / res).astype(int)
        img = np.zeros((h, w), np.float32)
        np.add.at(img, (h - 1 - c[:, 1], c[:, 0]), 1)
        return img

    s = density(static)
    img = np.full((h, w, 3), 255, np.uint8)
    shade = np.clip(255 - 40 * np.log1p(s), 70, 255).astype(np.uint8)
    img[s > 0] = np.repeat(shade[s > 0][:, None], 3, axis=1)
    if len(dynamic):
        d = density(dynamic)
        mask = d > 0
        # thicken red pixels a little so single dynamic points stay visible
        grown = mask.copy()
        grown[1:, :] |= mask[:-1, :]
        grown[:-1, :] |= mask[1:, :]
        grown[:, 1:] |= mask[:, :-1]
        grown[:, :-1] |= mask[:, 1:]
        img[grown] = (220, 40, 40)
    save_png(path, img)
    return {'resolution': round(res, 4), 'origin': [float(lo[0]), float(lo[1])], 'width': int(w), 'height': int(h)}


def main():
    job = sys.argv[1]
    with open(os.path.join(job, 'input.json'), encoding='utf-8') as f:
        p = json.load(f)['params']
    t0 = time.time()
    try:
        from dufomap import dufomap
    except ImportError as e:
        fail(job, PLUGIN, 'run_failed', reason=f'dufomap: {e}')
    try:
        rec = ScanRecording(p['recording'], p['trajectory'])
    except (OSError, ValueError) as e:
        fail(job, PLUGIN, 'scans_unreadable', reason=str(e))
    if not len(rec):
        fail(job, PLUGIN, 'scans_unreadable', reason='no scan matches a pose of the trajectory')
    mapper = dufomap(p['resolution'], p['d_s'], p['d_p'], num_threads=p['threads'], hit_extension=True,
                     ray_passthrough_hits=False)
    order = range(0, len(rec), max(1, int(p['scan_stride'])))
    used = 0
    for n, i in enumerate(order):
        pts, pose = rec.scan(i)
        pts = np.ascontiguousarray(pts[:, :3], dtype=np.float32)
        r = np.linalg.norm(pts, axis=1)
        keep = r > p['min_range']
        if p['max_range'] > 0:
            keep &= r < p['max_range']
        pts = pts[keep]
        if len(pts) < 10:
            continue
        x, y, z, qx, qy, qz, qw = (float(v) for v in pose)
        mapper.run(pts, [x, y, z, qw, qx, qy, qz], cloud_transform=True)
        used += 1
        if n % 10 == 0:
            emit(phase='integrating', progress=round(0.02 + 0.8 * n / len(order), 4))
    emit(phase='propagating', progress=0.83)
    mapper.oncePropagateCluster(if_propagate=True, if_cluster=False)
    emit(phase='labelling', progress=0.87)
    try:
        cloud = read_pcd_xyz(p['cloud'])
    except (OSError, ValueError, KeyError) as e:
        fail(job, PLUGIN, 'cloud_unreadable', reason=str(e))
    labels = mapper.segment(cloud, np.eye(4), cloud_transform=False).astype(bool)
    static, dynamic = cloud[~labels], cloud[labels]
    emit(phase='writing', progress=0.93)
    write_pcd_xyz(os.path.join(job, 'static.pcd'), static)
    write_pcd_xyz(os.path.join(job, 'dynamic.pcd'), dynamic)
    view = preview(static, dynamic, os.path.join(job, 'preview.png'))
    result = {
        'map': p['map'], 'scans': used, 'scans_total': len(rec), 'unmatched': rec.unmatched,
        'points': int(len(cloud)), 'static': int(len(static)), 'dynamic': int(len(dynamic)),
        'dynamic_ratio': round(float(len(dynamic)) / max(1, len(cloud)), 6), 'preview': view,
        'seconds': round(time.time() - t0, 1),
        'params': {k: p[k] for k in ('resolution', 'd_s', 'd_p', 'scan_stride', 'min_range', 'max_range')},
    }
    result_path = os.path.join(job, 'result.json')
    result_tmp = result_path + '.tmp'
    with open(result_tmp, 'w', encoding='utf-8') as f:
        json.dump(result, f)
    os.replace(result_tmp, result_path)


if __name__ == '__main__':
    main()
