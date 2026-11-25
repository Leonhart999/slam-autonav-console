"""Loop optimization on the Maps page (map_loop.py): parameters, availability, task, figures, saving.

The back-end interpreter is replaced by a small shell script that writes a prepared result, so these tests need
neither GTSAM nor small_gicp. One end-to-end test runs the real back-end on a synthetic recording when the loop
venv (~/venvs/loop) is installed.
Run with: ~/venvs/console/bin/python -m pytest -p no:anyio tests/test_map_loop.py
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from fastapi import FastAPI, HTTPException
from PIL import Image
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from robot_console import map_loop  # noqa: E402
from robot_console.fastlio_loop_export import write_pcd  # noqa: E402
from robot_console.map_catalog import MapCatalog, loop_state  # noqa: E402
from robot_console.map_loop import LoopService, backend_command, parameters, register_loop_routes  # noqa: E402

LOOP_PYTHON = Path.home() / 'venvs/loop/bin/python'

# Stands in for the loop venv's python: "-I -c ..." is the environment check, "-I <script> <rec> <out> ..." a run.
FAKE_PYTHON = r'''#!/bin/bash
if [ "$2" = "-c" ]; then exit 0; fi
out="$4"
echo "PHASE reading"
echo "scans 3, keyframes 3"
[ -n "$FAKE_LOOP_SLEEP" ] && sleep "$FAKE_LOOP_SLEEP"
if [ -n "$FAKE_LOOP_FAIL" ]; then echo "ValueError: $FAKE_LOOP_FAIL"; exit 1; fi
echo "PHASE matching"
echo "PHASE optimizing"
echo "PHASE rebuilding"
mkdir -p "$out"
cp -r "$FAKE_LOOP_RESULT"/. "$out"/
echo "$@" > "$out/argv.txt"
'''


def tum(path, stamps):
    rows = np.column_stack([stamps, np.arange(len(stamps)) * 0.5, np.zeros((len(stamps), 2)),
                            np.zeros((len(stamps), 3)), np.ones(len(stamps))])
    np.savetxt(path, rows, fmt='%.6f')


def write_result(d, recording, accepted=1):
    """A back-end result directory as fastlio_loop_backend.py leaves it."""
    d.mkdir(parents=True)
    stamps = np.array([100.0, 100.1, 100.2])
    tum(d / 'traj_odom.tum', stamps)
    tum(d / 'traj_loop.tum', stamps)
    write_pcd(d / 'map_loop_voxel.pcd', np.random.default_rng(0).random((500, 3)).astype(np.float32) * 4)
    (d / 'keyframe_corrections.csv').write_text('path_m,horizontal_shift_m,height_shift_m,yaw_change_deg\n0,0,0,0\n')
    grid = np.full((40, 30), 254, np.uint8)
    grid[0] = 0
    Image.fromarray(grid).save(d / 'grid_loop.pgm')
    Image.fromarray(grid).save(d / 'grid_loop_preview.png')
    Image.fromarray(grid).save(d / 'trajectory_correction.png')
    (d / 'grid_loop.yaml').write_text('image: grid_loop.pgm\nmode: trinary\nresolution: 0.05\n'
                                      'origin: [-1.0, -1.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.15\n')
    (d / 'grid_loop.json').write_text('{}')
    loop = dict(i=2, j=0, s_i=30.0, s_j=0.0, dist=1.2, ok=accepted > 0, overlap_before=0.4, overlap_after=0.8,
                rmse_before=0.15, rmse_after=0.05, shift_xyz=[0.3, 0.4, 0.0], drift_rpy_deg=[0, 0, -1.0],
                rejection_reasons=[] if accepted else ['poor_fit'])
    report = dict(scans=3, keyframes=3, path_m=31.0, duration_s=0.2, input_recording=str(recording),
                  loops=[loop], accepted=accepted, revisits=accepted, map_scan_stride=2, map_range_m=20.0)
    if accepted:
        report.update(end_correction=dict(xy_m=0.5, z_m=0.0, yaw_deg=-1.0), max_correction=dict(xy_m=0.5, yaw_deg=1.0),
                      z_range_loop=[0.0, 0.0],
                      junctions=[dict(i=2, j=0, s_i=30.0, s_j=0.0,
                                      wall_odom=dict(points=10, median_m=0.3, p90_m=0.9, rmse_m=0.5),
                                      wall_loop=dict(points=10, median_m=0.05, p90_m=0.1, rmse_m=0.06))])
    (d / 'loop_report.json').write_text(json.dumps(report))


class LoopTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-loop-test-')
        base = Path(self.tmp.name)
        self.maps, self.runs, self.rec = base / 'maps', base / 'runs', base / 'scans' / 'rec1'
        self.maps.mkdir()
        self.rec.mkdir(parents=True)
        (self.rec / 'scans.bin').write_bytes(b'\0' * 48)
        (self.rec / 'index.csv').write_text(''.join(f'{100 + k / 10:.6f} {k} 1 0 0 0 0 0 0 1\n' for k in range(3)))
        self.make_map('src', {'slam': 'mapping_fastlio_ground', 'run': 'play_src', 'input_recording': str(self.rec)})
        run = self.runs / 'play_src'
        run.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'id': 'play_src', 'type': 'playback', 'source': 'bag',
                                                  'bag': '/bags/b1', 'bag_name': 'b1'}))
        tum(run / 'gt.tum', np.array([100.0, 100.1, 100.2]))
        self.result = base / 'fake_result'
        write_result(self.result, self.rec)
        self.python = base / 'fake_python'
        self.python.write_text(FAKE_PYTHON)
        self.python.chmod(0o755)
        os.environ['FAKE_LOOP_RESULT'] = str(self.result)
        for key in ('FAKE_LOOP_FAIL', 'FAKE_LOOP_SLEEP'):
            os.environ.pop(key, None)
        self.cfg = {'mapping': {'maps_dir': str(self.maps), 'runs_dir': str(self.runs)},
                    'loop_backend': {'python': str(self.python), 'timeout': 60}}
        self.catalog = MapCatalog(self.maps)
        self.service = LoopService(self.catalog, self.cfg)

    def tearDown(self):
        for key in ('FAKE_LOOP_RESULT', 'FAKE_LOOP_FAIL', 'FAKE_LOOP_SLEEP'):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def make_map(self, name, meta, scan_poses=True):
        d = self.maps / name
        (d / 'data').mkdir(parents=True)
        Image.fromarray(np.full((10, 10), 254, np.uint8)).save(d / 'map.pgm')
        (d / 'map.yaml').write_text('image: map.pgm\nresolution: 0.05\norigin: [0.0, 0.0, 0.0]\nnegate: 0\n'
                                    'occupied_thresh: 0.65\nfree_thresh: 0.15\n')
        write_pcd(d / 'map.pcd', np.zeros((5, 3), np.float32))
        (d / 'meta.json').write_text(json.dumps({'name': name, **meta}))
        if scan_poses:
            tum(d / 'data' / 'scan_poses.tum', np.array([100.0, 100.1, 100.2]))
        return d


class ParameterTests(LoopTestBase):
    def test_defaults_and_limits(self):
        p = parameters({})
        self.assertEqual(p['loop_min_gap'], 100.0)
        self.assertEqual(p['scan_stride'], 2)
        self.assertEqual(parameters({'loop_min_gap': 30})['loop_min_gap'], 30.0)
        for bad in ({'loop_radius': 0.5}, {'loop_min_gap': True}, {'scan_stride': 2.5}, {'nope': 1},
                    {'map_res': float('nan')}):
            with self.assertRaises(HTTPException, msg=bad):
                parameters(bad)

    def test_command_passes_poses_and_backend_parameters_only(self):
        p = parameters({'loop_min_gap': 40})
        cmd = backend_command('/py', '/rec', '/rec/poses.tum', '/out', p)
        self.assertEqual(cmd[:2], ['/py', '-I'])
        self.assertTrue(cmd[2].endswith('fastlio_loop_backend.py'))
        self.assertEqual(cmd[cmd.index('--poses') + 1], '/rec/poses.tum')
        extra = json.loads(cmd[cmd.index('--params') + 1])
        self.assertEqual(set(extra), set(map_loop.BACKEND_KEYS))
        self.assertEqual(extra['loop_min_gap'], 40.0)
        self.assertEqual(cmd[cmd.index('--scan-stride') + 1], '2')

    def test_availability(self):
        self.assertEqual(loop_state(self.maps / 'src', json.loads((self.maps / 'src/meta.json').read_text())),
                         {'reason': None, 'ground': True})
        self.make_map('plain', {'slam': 'mapping_fastlio'}, scan_poses=False)
        self.make_map('lidar', {'slam': 'mapping_fastlio_lidar', 'input_recording': str(self.rec)})
        self.make_map('done', {'postprocess': 'fastlio_planar_loop', 'input_recording': str(self.rec)})
        self.assertEqual(self.catalog.detail('plain')['loop'], {'reason': 'no_scans', 'ground': False})
        self.assertEqual(self.catalog.detail('lidar')['loop'], {'reason': None, 'ground': False})
        self.assertEqual(self.catalog.detail('done')['loop']['reason'], 'already_optimized')
        with self.assertRaises(HTTPException):
            self.service.source('plain')
        with self.assertRaises(HTTPException):
            self.service.source('done')


class ServiceTests(LoopTestBase):
    def run_loop(self, body=None):
        phases = []
        record = self.service.run('src', body or {}, phases.append)
        return record, phases

    def test_run_and_save(self):
        record, phases = self.run_loop({'loop_min_gap': 30})
        self.assertEqual(phases, ['reading', 'matching', 'optimizing', 'rebuilding', 'writing'])
        self.assertEqual(record['accepted'], 1)
        self.assertEqual(record['parent'], 'src')
        self.assertTrue(record['ground'])
        self.assertEqual(record['params']['loop_min_gap'], 30.0)
        self.assertEqual(record['figures'], ['trajectory_correction', 'grid_loop_preview'])
        self.assertEqual(record['loops'][0]['shift_xy'], 0.5)
        self.assertEqual(record['junctions'][0]['wall_after']['median_m'], 0.05)
        out = self.service.resolve(record['token'])
        argv = (out / 'result' / 'argv.txt').read_text()
        self.assertIn(str(self.maps / 'src' / 'data' / 'scan_poses.tum'), argv)
        self.assertTrue(self.service.figure(record['token'], 'trajectory_correction').is_file())

        detail = self.service.save(record['token'], 'src_loop')
        self.assertEqual(detail['name'], 'src_loop')
        self.assertEqual(detail['loop_run'], 'play_loop_src_loop')
        self.assertEqual(detail['loop']['reason'], 'already_optimized')
        self.assertTrue(detail['has_scans'])            # regrid by scan evidence stays available
        meta = json.loads((self.maps / 'src_loop' / 'meta.json').read_text())
        self.assertEqual(meta['source'], 'loop')
        self.assertEqual(meta['parent_map'], 'src')
        self.assertEqual(meta['source_run'], 'play_src')
        self.assertEqual(meta['loop_params']['loop_min_gap'], 30.0)
        self.assertIn('exact baseline', meta['provenance_note'])
        self.assertEqual(meta['bag_name'], 'b1')
        self.assertFalse((self.maps / 'src_loop' / 'goals.yaml').exists())
        run = self.runs / 'play_loop_src_loop'
        for f in ('est.tum', 'est_online.tum', 'gt.tum', 'run.json'):
            self.assertTrue((run / f).is_file(), f)
        self.assertIn('src_loop', (self.rec / 'used_by_maps.txt').read_text().split())
        self.assertFalse(out.exists())
        with self.assertRaises(HTTPException) as ctx:
            self.service.resolve(record['token'])
        self.assertEqual(ctx.exception.status_code, 404)
        # the source map is untouched
        self.assertEqual(json.loads((self.maps / 'src' / 'meta.json').read_text())['run'], 'play_src')

    def test_save_refusals(self):
        record, _ = self.run_loop()
        token = record['token']
        for name in ('', 'a' * 49, 'bad name', '../x'):
            with self.assertRaises(HTTPException, msg=name):
                self.service.save(token, name)
        with self.assertRaises(HTTPException) as ctx:
            self.service.save(token, 'src')
        self.assertEqual(ctx.exception.status_code, 409)
        (self.runs / 'play_loop_taken').mkdir()
        with self.assertRaises(HTTPException) as ctx:
            self.service.save(token, 'taken')
        self.assertEqual(ctx.exception.status_code, 409)
        # the scans the result was computed from changed
        tum(self.maps / 'src' / 'data' / 'scan_poses.tum', np.array([100.0, 100.1]))
        with self.assertRaises(HTTPException) as ctx:
            self.service.save(token, 'fresh')
        self.assertIn('changed', ctx.exception.detail['params']['reason'])
        self.assertFalse((self.maps / 'fresh').exists())

    def test_no_accepted_loop_cannot_be_saved(self):
        empty = Path(self.tmp.name) / 'empty_result'
        write_result(empty, self.rec, accepted=0)
        os.environ['FAKE_LOOP_RESULT'] = str(empty)
        record, _ = self.run_loop()
        self.assertEqual(record['accepted'], 0)
        self.assertEqual(record['loops'][0]['reasons'], ['poor_fit'])
        with self.assertRaises(HTTPException):
            self.service.save(record['token'], 'nothing')
        self.service.discard(record['token'])
        self.assertEqual(list((self.maps / '.loop').iterdir()), [])

    def test_backend_failure_and_timeout(self):
        os.environ['FAKE_LOOP_FAIL'] = 'The pose file lists fewer than 2 scans'
        with self.assertRaises(HTTPException) as ctx:
            self.run_loop()
        self.assertIn('fewer than 2 scans', ctx.exception.detail['params']['reason'])
        os.environ.pop('FAKE_LOOP_FAIL')
        os.environ['FAKE_LOOP_SLEEP'] = '5'
        self.service.timeout = 0.5
        t0 = time.time()
        with self.assertRaises(HTTPException) as ctx:
            self.run_loop()
        self.assertLess(time.time() - t0, 4)
        self.assertIn('longer than', ctx.exception.detail['params']['reason'])
        self.assertEqual(list((self.maps / '.loop').iterdir()), [])   # failed runs leave nothing behind

    def test_pending_limit_and_expiry(self):
        stage = self.maps / '.loop'
        stage.mkdir()
        for k in range(map_loop.KEEP_PENDING):
            (stage / f'{k:032x}').mkdir()
        with self.assertRaises(HTTPException) as ctx:
            self.run_loop()
        self.assertEqual(ctx.exception.status_code, 409)
        old = time.time() - map_loop.EXPIRE - 10
        os.utime(stage / f'{0:032x}', (old, old))
        record, _ = self.run_loop()
        self.assertFalse((stage / f'{0:032x}').exists())
        self.assertTrue((stage / record['token']).is_dir())

    def test_missing_environment(self):
        self.service.env.python = Path(self.tmp.name) / 'no_such_python'
        with self.assertRaises(HTTPException) as ctx:
            self.service.check('src', {})
        self.assertIn('requirements-loop.txt', ctx.exception.detail['params']['reason'])
        env = self.service.env.check()
        self.assertFalse(env['installed'])


class RouteTests(LoopTestBase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI()
        register_loop_routes(self.app, self.catalog, self.cfg)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error'))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.01)

    def tearDown(self):
        self.app.state.console_tasks.close()
        self.server.should_exit = True
        self.thread.join(5)
        super().tearDown()

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, method=method, headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get('content-type', '').startswith('application/json') else raw)
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_background_task_figure_and_save(self):
        status, env = self.call('GET', '/api/maps/loop/env')
        self.assertEqual((status, env['installed']), (200, True))
        status, body = self.call('POST', '/api/maps/src/loop?background=true', {'loop_radius': 0.1})
        self.assertEqual(status, 400)                       # refused before a task is created
        status, body = self.call('POST', '/api/maps/missing/loop?background=true', {})
        self.assertEqual(status, 404)
        status, job = self.call('POST', '/api/maps/src/loop?background=true', {})
        self.assertEqual((status, job['kind'], job['target']), (200, 'loop', 'src'))
        for _ in range(300):
            status, job = self.call('GET', f'/api/tasks/{job["id"]}')
            if job['state'] not in ('queued', 'running'):
                break
            time.sleep(0.05)
        self.assertEqual(job['state'], 'succeeded', job)
        token = job['result']['token']
        status, png = self.call('GET', f'/api/maps/loop/{token}/figures/trajectory_correction.png')
        self.assertEqual(status, 200)
        self.assertTrue(png.startswith(b'\x89PNG'))
        status, _ = self.call('GET', f'/api/maps/loop/{token}/figures/secret.png')
        self.assertEqual(status, 404)
        status, detail = self.call('POST', f'/api/maps/loop/{token}/save', {'name': 'src_loop'})
        self.assertEqual((status, detail['name'], detail['loop_run']), (200, 'src_loop', 'play_loop_src_loop'))
        status, job = self.call('GET', f'/api/tasks/{job["id"]}')
        self.assertEqual(job['saved_map'], 'src_loop')


@unittest.skipUnless(LOOP_PYTHON.is_file(), 'loop venv (~/venvs/loop) not installed')
class EndToEndTests(LoopTestBase):
    """Real back-end on a synthetic recording: a circle driven 2.5 times with drifting odometry."""

    def test_synthetic_loop_reduces_drift(self):
        import loop_fixture
        rec = Path(self.tmp.name) / 'scans' / 'synthetic'
        true, odom, _ = loop_fixture.make_recording(str(rec), yaw_drift_deg=3.0, xy_drift=0.4)
        d = self.make_map('synth', {'slam': 'mapping_fastlio_ground', 'input_recording': str(rec)})
        loop_fixture.write_scan_poses(str(rec), str(d / 'data' / 'scan_poses.tum'))
        self.service.env.python = LOOP_PYTHON
        self.service.env._state = None
        record = self.service.run('synth', {'loop_min_gap': 20, 'loop_radius': 5, 'map_res': 0.1, 'scan_stride': 3})
        self.assertGreater(record['accepted'], 0)
        self.assertIn('junction_before_after', record['figures'])
        result = self.service.resolve(record['token']) / 'result'
        errors = {}
        for name in ('traj_odom', 'traj_loop'):
            t = np.loadtxt(result / f'{name}.tum')
            errors[name] = np.linalg.norm(t[:, 1:3] - true[:, :2, 3], axis=1).mean()
        self.assertLess(errors['traj_loop'], 0.5 * errors['traj_odom'], errors)
        detail = self.service.save(record['token'], 'synth_loop')
        self.assertTrue(detail['has_grid'])
        self.assertTrue(detail['has_cloud'])


if __name__ == '__main__':
    unittest.main()
