"""Publish a separate loop-optimized map and comparison run without altering source results."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import numpy as np


def read_pcd(path):
    with open(path, 'rb') as stream:
        fields = {}
        while True:
            line = stream.readline()
            if not line:
                raise ValueError('Incomplete PCD header')
            parts = line.decode('ascii').split()
            if not parts or parts[0].startswith('#'):
                continue
            fields[parts[0]] = parts[1:]
            if parts[0] == 'DATA':
                break
        if fields['FIELDS'] != ['x', 'y', 'z'] or fields['DATA'] != ['binary']:
            raise ValueError('Expected loop back-end binary XYZ PCD')
        return np.fromfile(stream, dtype='<f4', count=int(fields['POINTS'][0]) * 3).reshape(-1, 3)


def write_pcd(path, xyz):
    with open(path, 'wb') as stream:
        stream.write((f'# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n'
                      f'COUNT 1 1 1\nWIDTH {len(xyz)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\n'
                      f'POINTS {len(xyz)}\nDATA binary\n').encode('ascii'))
        stream.write(np.asarray(xyz, dtype='<f4').tobytes())


PROVENANCE = {
    # CLI: the scans were recorded during a new replay of the bag, so the source run is lineage only
    'new_replay': 'New FAST-LIO replay on the same bag/settings; source_run provides bag/reference metadata only, '
                  'not the exact odometry baseline.',
    # Console (Maps page): the scans were recorded during the playback that produced the parent map
    'same_replay': "Scans were recorded during the replay of source_run: its est.tum and this run's est_online.tum "
                   'come from the same FAST-LIO odometry, so source_run is the exact baseline.',
}


def mark_recording_used(recording, name):
    """Name the new map in the recording's used_by_maps.txt, so the playback page does not delete the recording."""
    if not recording:
        return
    try:
        with open(os.path.join(recording, 'used_by_maps.txt'), 'a', encoding='utf-8') as f:
            f.write(name + '\n')
    except OSError:
        pass


def publish(result_dir, maps_dir, name, grid_python=None, grid_script=None, source_run=None, runs_dir=None,
            parent_map=None, provenance='new_replay', loop_params=None, quiet=False):
    """Publish <maps_dir>/<name> (and <runs_dir>/play_loop_<name> when runs_dir is given) from a back-end result.
    Returns (map dir, run id or None)."""
    if provenance not in PROVENANCE:
        raise ValueError('Unknown provenance')
    result_dir, maps_dir = Path(result_dir).resolve(), Path(maps_dir).expanduser().resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', name):
        raise ValueError('Map name must contain 1-48 ASCII letters, digits, underscores or hyphens')
    dest = maps_dir / name
    if dest.exists():
        raise FileExistsError(f'Refusing to overwrite map: {dest}')
    report = json.loads((result_dir / 'loop_report.json').read_text())
    if report['accepted'] < 1:
        raise ValueError('No accepted loop in result')
    source = Path(source_run).expanduser().resolve() if source_run else None
    metadata = json.loads((source / 'run.json').read_text()) if source else {}
    run_id = 'play_loop_' + name
    run_dest = Path(runs_dir).expanduser().resolve() / run_id if runs_dir else None
    if run_dest and run_dest.exists():
        raise FileExistsError(f'Refusing to overwrite run: {run_dest}')
    maps_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.loop_staging_', dir=maps_dir))
    published = False
    try:
        shutil.copy2(result_dir / 'map_loop_voxel.pcd', staging / 'map.pcd')
        xyz = read_pcd(staging / 'map.pcd')
        # Always rebuild preview from the corrected map, never from the online accumulation.
        vox = np.floor(xyz / 0.1).astype(np.int64)
        _, keep = np.unique(np.ascontiguousarray(vox).view('V24').reshape(-1), return_index=True)
        preview = (vox[keep] + 0.5) * 0.1
        write_pcd(staging / 'map_voxel.pcd', preview)
        data = staging / 'data'
        data.mkdir()
        shutil.copy2(result_dir / 'traj_loop.tum', data / 'est.tum')
        shutil.copy2(result_dir / 'traj_odom.tum', data / 'est_online.tum')
        for item in ('loop_report.json', 'keyframe_corrections.csv'):
            shutil.copy2(result_dir / item, data / item)
        if source:
            for item in ('ref.tum', 'gt.tum'):
                if (source / item).is_file():
                    shutil.copy2(source / item, data / item)
        figures = staging / 'figures' / 'comparison'
        figures.mkdir(parents=True)
        for item in result_dir.glob('*.png'):
            shutil.copy2(item, figures / item.name)
        from PIL import Image
        import yaml
        if (result_dir / 'grid_loop.pgm').is_file():
            # Grid built by the back-end from per-cell scan evidence (range limit, hit count, free-space rays).
            shutil.copy2(result_dir / 'grid_loop.pgm', staging / 'map.pgm')
            shutil.copy2(result_dir / 'grid_loop_preview.png', staging / 'map_preview.png')
            shutil.copy2(result_dir / 'grid_loop.json', data / 'grid.json')
            grid_yaml = yaml.safe_load((result_dir / 'grid_loop.yaml').read_text())
            grid_yaml['image'] = 'map.pgm'
            (staging / 'map.yaml').write_text(yaml.safe_dump(grid_yaml, default_flow_style=None, sort_keys=False))
        else:
            # Older result without a grid: project the voxel map; one 5 cm voxel contributes one cell.
            if not grid_python or not grid_script:
                raise ValueError('Result has no grid_loop.pgm; pass --grid-python and --grid-script')
            proc = subprocess.run([grid_python, grid_script, str(staging / 'map.pcd'), str(staging / 'map'),
                                   '--res', '0.05', '--min-points', '1'], capture_output=True, text=True, timeout=300)
            (data / 'projection.log').write_text(proc.stdout + proc.stderr)
            if proc.returncode:
                raise RuntimeError('Grid projection failed: ' + proc.stderr[-1500:])
        grid_yaml = yaml.safe_load((staging / 'map.yaml').read_text())
        with Image.open(staging / 'map.pgm') as image:
            w, h = image.size
        trajectory = np.loadtxt(data / 'est.tum', ndmin=2)
        length = float(np.linalg.norm(np.diff(trajectory[:, 1:4], axis=0), axis=1).sum())
        info = {k: v for k, v in metadata.items() if k not in ('id', 'maps', 'extent', 'length', 'voxels', 'last_error', 'lag')}
        info.update(name=name, created=time.strftime('%Y-%m-%d %H:%M:%S'), algorithm='FAST-LIO2 (ground + planar loop)',
                    source='loop', frame='fastlio_loop_map', run=run_id if run_dest else None,
                    source_run=metadata.get('id'), input_recording=report.get('input_recording'),
                    provenance_note=PROVENANCE[provenance],
                    parent_map=parent_map, postprocess='fastlio_planar_loop', loop_params=loop_params,
                    points=len(xyz), voxels=len(preview), voxel_size=0.1,
                    duration=float(trajectory[-1, 0] - trajectory[0, 0]), length=round(length, 2),
                    extent=[float(v) for v in np.r_[xyz.min(axis=0), xyz.max(axis=0)]],
                    grid=dict(width=w, height=h, resolution=grid_yaml['resolution'],
                              size_m=[round(w * grid_yaml['resolution'], 2), round(h * grid_yaml['resolution'], 2)]),
                    project_error=None, figures_error=None,
                    loop_summary=dict(accepted=report['accepted'], end_correction=report['end_correction'],
                                      z_range=report['z_range_loop']),
                    files={'map.pcd': f'Loop-optimized voxel map (scan stride {report.get("map_scan_stride")}, '
                                      f'range <= {report.get("map_range_m")} m)',
                           'map_voxel.pcd': '10 cm preview rebuilt from the optimized map',
                           'data/': 'Optimized and source trajectories, registration report, projection log',
                           'figures/': 'Before/after comparison figures', 'map.pgm / map.yaml': 'Nav2 occupancy grid'})
        (staging / 'meta.json').write_text(json.dumps(info, ensure_ascii=False, indent=2))
        if dest.exists():
            raise FileExistsError(dest)
        staging.rename(dest)
        published = True
        if run_dest:
            run_dest.mkdir()
            for item in ('est.tum', 'est_online.tum', 'ref.tum', 'gt.tum', 'lag.csv', 'loop_report.json'):
                if (dest / 'data' / item).is_file():
                    shutil.copy2(dest / 'data' / item, run_dest / item)
            run_meta = {k: v for k, v in metadata.items() if k not in ('lag', 'slam_cmd', 'last_error')}
            run_meta.update(id=run_id, maps=[name], algorithm=info['algorithm'], frame=info['frame'],
                            source_run=metadata.get('id'), est_source='fastlio_planar_loop',
                            provenance_note=PROVENANCE[provenance],
                            slam='fastlio_planar_loop_offline', parent_slam_cmd=metadata.get('slam_cmd'),
                            input_recording=report.get('input_recording'),
                            started=time.strftime('%Y-%m-%d %H:%M:%S'), ended=time.strftime('%Y-%m-%d %H:%M:%S'),
                            duration=info['duration'], length=info['length'], voxels=len(preview), extent=info['extent'])
            (run_dest / 'run.json').write_text(json.dumps(run_meta, ensure_ascii=False, indent=2))
        mark_recording_used(report.get('input_recording'), name)
        if not quiet:
            print(json.dumps({'map': str(dest), 'run': str(run_dest) if run_dest else None,
                              'points': len(xyz), 'voxels': len(preview), 'grid': info['grid']}, indent=2))
        return dest, (run_id if run_dest else None)
    finally:
        if not published and staging.exists():
            # Only this call's fresh hidden staging directory can be removed.
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results')
    parser.add_argument('--maps-dir', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--grid-python', help='only for results without grid_loop.pgm')
    parser.add_argument('--grid-script', help='only for results without grid_loop.pgm')
    parser.add_argument('--source-run')
    parser.add_argument('--runs-dir')
    parser.add_argument('--parent-map')
    args = parser.parse_args()
    publish(args.results, args.maps_dir, args.name, args.grid_python, args.grid_script,
            args.source_run, args.runs_dir, args.parent_map)


if __name__ == '__main__':
    main()
