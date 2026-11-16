"""Regenerate a 2D grid from a 3D cloud with pcd2grid (runs as a separate process, see map_regrid.py).

Usage: python regrid_worker.py <job.json>
job.json: {"cloud": path, "out_dir": dir, "params": {GridParams fields}}
      or  {"method": "scans", "recording": dir, "trajectory": tum, "out_dir": dir, "params": {...}}
Writes <out_dir>/map.pgm, map.yaml and result.json (stats, warnings, info, grid geometry).
A separate process keeps the console's memory flat and isolates crashes in experimental features.
"""
import hashlib
import json
import os
import sys
import time

import numpy as np


def scan_grid(job):
    """Grid from recorded scans and their poses (per-cell evidence), see fastlio_loop_grid.py."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'fastlio_loop_grid', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fastlio_loop_grid.py'))
    grid = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grid)

    t0 = time.time()
    stats, _ = grid.build_grid(job['recording'], job['trajectory'], os.path.join(job['out_dir'], 'map'),
                               job['params'], log=lambda *_: None)
    res, w, h = stats['params']['res'], stats['width'], stats['height']
    out = {
        'method': 'scans',
        'input_scans': stats['scans'],
        'elapsed': round(time.time() - t0, 2),
        'grid': {'resolution': res, 'width': w, 'height': h, 'origin': stats['origin'],
                 'size_m': [round(w * res, 2), round(h * res, 2)]},
        'stats': {'width': w, 'height': h, 'occupied': stats['occupied'], 'free': stats['free']},
        'warnings': [],
        'info': f"{stats['scans']} scans, sensor {stats['params']['sensor_height']:.2f} m above the floor",
    }
    with open(os.path.join(job['out_dir'], 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)


def main():
    with open(sys.argv[1], encoding='utf-8') as f:
        job = json.load(f)
    if job.get('method') == 'scans':
        return scan_grid(job)
    import pcd2grid
    from pcd2grid.core import GridParams, convert
    from pcd2grid.io import read_points
    from pcd2grid.output import write_map

    t0 = time.time()
    xyz = read_points(job['cloud'])
    p = dict(job['params'])
    for key in ('z_slab',):
        if p.get(key) is not None:
            p[key] = tuple(p[key])
    result = convert(xyz, GridParams(**p))
    write_map(result, os.path.join(job['out_dir'], 'map'), preview=False)
    h, w = result.grid.shape
    # Version numbers also cover local unreleased snapshots; fingerprint their source.
    digest = hashlib.sha256()
    package_dir = os.path.dirname(pcd2grid.__file__)
    for filename in ('core.py', 'io.py', 'output.py', 'processing.py'):
        with open(os.path.join(package_dir, filename), 'rb') as source:
            digest.update(filename.encode('ascii') + b'\0' + source.read())
    out = {
        'pcd2grid': pcd2grid.__version__,
        'pcd2grid_build': digest.hexdigest(),
        'input_points': int(len(xyz)),
        'elapsed': round(time.time() - t0, 2),
        'grid': {'resolution': result.res, 'width': w, 'height': h,
                 'origin': [round(result.origin[0], 3), round(result.origin[1], 3), 0.0],
                 'size_m': [round(w * result.res, 2), round(h * result.res, 2)]},
        'stats': result.stats, 'warnings': list(result.warnings), 'info': result.info,
        'level_rotation': None if result.level_rotation is None else np.round(result.level_rotation, 6).tolist(),
    }
    with open(os.path.join(job['out_dir'], 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:      # One line on stderr; the console shows it to the user
        print(f'{type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
