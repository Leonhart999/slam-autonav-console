#!/usr/bin/env python3
"""Build the data of the static web demo (robot_console/web/demo) from one saved map and its mapping run.

Usage:
  python3 -I tools/make_web_demo_data.py --map ~/maps/<map> --run ~/runs/<run> --out web/demo/public/data

Inputs (as written by the console):
  <map>/map_voxel.pcd, <map>/meta.json             voxel map (camera_init frame) and its metadata
  <run>/est.tum, <run>/gt.tum, <run>/eval.json      estimate, ground truth, evo evaluation of the run

Outputs:
  run.json   run metadata, evo statistics, the estimate and the ground truth in the map frame, APE per pose,
             and a top view of the aligned trajectories (world frame), all numbers rounded for size
  map.bin    the voxel map: voxel indices packed into sorted uint32 keys, stored as deltas, gzip-compressed
  <scene>-grid.png + <scene>-console.json (--console or --summary-only): saved grid and original evo series.
  map.json (--summary-only): map metadata and compressed map, without the replay poses in run.json.
  Both console modes require Pillow, PyYAML, map.pgm, map.yaml and the run's run.json.

The ground truth is moved into the map frame with the inverse of evo's SE(3) alignment (est -> gt) from
eval.json, so the 3D view can draw it over the map. The APE per pose is recomputed here with the same
alignment and association tolerance; its RMSE is checked against eval.json.
"""
import argparse
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np

DATA_VERSION = 1


def read_pcd_xyz(path: Path) -> np.ndarray:
    with open(path, 'rb') as f:
        header = {}
        while True:
            line = f.readline().decode('ascii').strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition(' ')
            header[key] = value.split()
            if key == 'DATA':
                break
        body = f.read()
    fields = header['FIELDS']
    if header['DATA'][0] != 'binary':
        sys.exit(f'{path}: only binary PCD files are supported')
    kinds = {'F': 'f', 'U': 'u', 'I': 'i'}
    dtype = np.dtype([(n, '<' + kinds[t] + s) for n, s, t in zip(fields, header['SIZE'], header['TYPE'])])
    points = np.frombuffer(body, dtype=dtype, count=int(header['POINTS'][0]))
    return np.stack([points['x'], points['y'], points['z']], 1).astype(np.float64)


def read_tum(path: Path) -> np.ndarray:
    data = np.loadtxt(path, comments='#')
    return data[np.argsort(data[:, 0], kind='stable')]


def yaw_of(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def associate(t_est: np.ndarray, t_ref: np.ndarray, max_diff: float) -> np.ndarray:
    """Index into t_ref of the closest stamp for every estimate stamp, -1 when farther than max_diff."""
    j = np.clip(np.searchsorted(t_ref, t_est), 1, len(t_ref) - 1)
    left = t_ref[j - 1]
    right = t_ref[j]
    j = np.where(np.abs(t_est - left) <= np.abs(right - t_est), j - 1, j)
    return np.where(np.abs(t_ref[j] - t_est) <= max_diff, j, -1)


def encode_map(xyz: np.ndarray, voxel: float) -> tuple[bytes, dict]:
    idx = np.floor(xyz / voxel + 1e-6).astype(np.int64)
    lo = idx.min(0)
    idx -= lo
    bits = [max(1, int(v).bit_length()) for v in idx.max(0)]
    if sum(bits) > 32:
        sys.exit(f'map too large for 32-bit voxel keys: bits {bits}')
    keys = (idx[:, 0] << (bits[1] + bits[2])) | (idx[:, 1] << bits[2]) | idx[:, 2]
    keys = np.unique(keys).astype(np.uint32)
    deltas = np.diff(keys, prepend=np.uint32(0)).astype('<u4')
    raw = gzip.compress(deltas.tobytes(), compresslevel=9, mtime=0)
    info = {
        'file': 'map.bin',
        'encoding': 'voxel-key-delta-u32-gzip',
        'voxel': voxel,
        'min_index': lo.tolist(),
        'bits': bits,
        'count': int(len(keys)),
        'bytes': len(raw),
    }
    return raw, info


def rounded(a: np.ndarray, digits: int) -> list:
    return np.round(a, digits).tolist()


def write_console_assets(map_dir: Path, run_dir: Path, out: Path, evaluation: dict, scene: str) -> None:
    from PIL import Image
    import yaml

    info = yaml.safe_load((map_dir / 'map.yaml').read_text(encoding='utf-8'))
    run = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
    with Image.open(map_dir / 'map.pgm') as image:
        image.save(out / f'{scene}-grid.png', optimize=True)
        grid = dict(width=image.width, height=image.height, resolution=info['resolution'], origin=info['origin'])
    keys = ['evo', 'created', 'params', 'matched_poses', 'duration', 'length', 'ref_length', 'ape', 'rpe', 'series']
    public = dict(grid=grid, started=run['started'], ended=run['ended'], evaluation={k: evaluation[k] for k in keys})
    (out / f'{scene}-console.json').write_text(json.dumps(public, separators=(',', ':')) + '\n', encoding='utf-8')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--map', required=True, type=Path, help='saved map folder (map_voxel.pcd, meta.json)')
    ap.add_argument('--run', required=True, type=Path, help='mapping run folder (est.tum, gt.tum, eval.json)')
    ap.add_argument('--out', required=True, type=Path, help='output folder (run.json, map.bin)')
    ap.add_argument('--scene', default='warehouse', help='scene key the demo page has captions for')
    ap.add_argument('--console', action='store_true', help='also export the grid and full evaluation')
    ap.add_argument('--summary-only', action='store_true', help='export the console grid, map and evaluation without replay poses')
    ap.add_argument('--top-view-points', type=int, default=1500)
    args = ap.parse_args()

    meta = json.loads((args.map / 'meta.json').read_text(encoding='utf-8'))
    ev = json.loads((args.run / 'eval.json').read_text(encoding='utf-8'))
    if meta.get('run') and meta['run'] != args.run.name:
        sys.exit(f"map {args.map.name} was saved from run {meta['run']}, not {args.run.name}")

    voxel = float(meta.get('voxel_size') or 0.1)
    xyz = read_pcd_xyz(args.map / 'map_voxel.pcd')
    map_bytes, map_info = encode_map(xyz, voxel)
    if args.summary_only:
        args.out.mkdir(parents=True, exist_ok=True)
        write_console_assets(args.map, args.run, args.out, ev, args.scene)
        summary = {'version': DATA_VERSION, 'algorithm': meta['algorithm'], 'frame': meta['frame'],
                   'duration': meta['duration'], 'length': meta['length'], 'extent': meta['extent'], 'map': map_info}
        (args.out / 'map.json').write_text(json.dumps(summary, separators=(',', ':')) + '\n', encoding='utf-8')
        (args.out / 'map.bin').write_bytes(map_bytes)
        return

    est = read_tum(args.run / 'est.tum')
    gt = read_tum(args.run / 'gt.tum')
    rot = np.array(ev['alignment']['rotation'], dtype=np.float64)
    trans = np.array(ev['alignment']['translation'], dtype=np.float64)
    max_diff = float(ev['params'].get('max_diff', 0.01))

    t0 = est[0, 0]
    est_xyz = est[:, 1:4]
    est_world = est_xyz @ rot.T + trans                       # est aligned onto the ground truth
    gt_map = (gt[:, 1:4] - trans) @ rot                       # ground truth in the map frame: R^T (p - t)

    j = associate(est[:, 0], gt[:, 0], max_diff)
    ok = j >= 0
    err = np.full(len(est), np.nan)
    err[ok] = np.linalg.norm(est_world[ok] - gt[j[ok], 1:4], axis=1)
    rmse = math.sqrt(float(np.nanmean(err ** 2)))
    if abs(rmse - ev['ape']['rmse']) > 0.002:
        sys.exit(f"APE RMSE recomputed {rmse:.4f} m differs from eval.json {ev['ape']['rmse']:.4f} m")

    # Ground truth at the estimate stamps (closest within 0.05 s), for drawing it up to the replay time
    jg = associate(est[:, 0], gt[:, 0], 0.05)
    if (jg < 0).any():
        print(f'note: {(jg < 0).sum()} estimate poses have no ground truth within 0.05 s', file=sys.stderr)
    jg = np.where(jg < 0, np.maximum.accumulate(np.where(jg < 0, 0, jg)), jg)

    step = max(1, len(est) // args.top_view_points)
    top = slice(0, None, step)

    out = {
        'version': DATA_VERSION,
        'scene': args.scene,
        'algorithm': meta.get('algorithm', 'FAST-LIO2'),
        'frame': meta.get('frame', 'camera_init'),
        'duration': round(float(est[-1, 0] - t0), 2),
        'length': ev['length'],
        'ref_length': ev['ref_length'],
        'extent': meta['extent'],
        'voxels': map_info['count'],
        'map': map_info,
        'eval': {
            'tool': f"evo {ev['evo']}",
            'align': ev['params']['align'],
            'pose_relation': ev['params']['pose_relation'],
            'max_diff': max_diff,
            'rpe_delta_m': ev['params'].get('rpe_delta'),
            'matched_poses': ev['matched_poses'],
            'ape': {k: round(v, 4) for k, v in ev['ape'].items()},
            'rpe': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in (ev.get('rpe') or {}).items()},
        },
        'est': {
            't': rounded(est[:, 0] - t0, 2),
            'x': rounded(est_xyz[:, 0], 3), 'y': rounded(est_xyz[:, 1], 3), 'z': rounded(est_xyz[:, 2], 3),
            'yaw': rounded(yaw_of(est[:, 4:8]), 4),
            'ape': [None if math.isnan(v) else round(v, 4) for v in err],
        },
        'gt': {
            'x': rounded(gt_map[jg, 0], 3), 'y': rounded(gt_map[jg, 1], 3), 'z': rounded(gt_map[jg, 2], 3),
        },
        'top_view': {
            't': rounded(est[top, 0] - t0, 2),
            'est': rounded(est_world[top, :2], 3),
            'gt': rounded(gt[jg[top], 1:3], 3),
        },
    }

    args.out.mkdir(parents=True, exist_ok=True)
    if args.console:
        write_console_assets(args.map, args.run, args.out, ev, args.scene)
    (args.out / 'map.bin').write_bytes(map_bytes)
    (args.out / 'run.json').write_text(json.dumps(out, separators=(',', ':')), encoding='utf-8')
    print(f"map: {map_info['count']} voxels, {len(map_bytes) / 1e6:.2f} MB; run.json "
          f"{(args.out / 'run.json').stat().st_size / 1e6:.2f} MB; APE RMSE {rmse:.4f} m "
          f"(eval.json {ev['ape']['rmse']:.4f} m)")


if __name__ == '__main__':
    main()
