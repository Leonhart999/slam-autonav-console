"""Plugins shipped with the console: manifests and built frontends, and the numpy parts of their workers that do not
need the plugins' own packages (Livox CustomMsg parsing, PCD/TUM/scan-recording I/O, grid building)."""
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from robot_console import plugins  # noqa: E402

SHIPPED = ROOT / 'plugins'


def load(plugin, module):
    """Import plugins/<plugin>/<module>.py under a unique name (the plugin folder first on sys.path)."""
    folder = str(SHIPPED / plugin)
    sys.path.insert(0, folder)
    try:
        for name in ('mapio', 'livox'):
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(f'shipped_{plugin}_{module}', SHIPPED / plugin / f'{module}.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(folder)


class ManifestTests(unittest.TestCase):
    def test_every_shipped_plugin_is_valid_and_built(self):
        ids = sorted(p.name for p in SHIPPED.iterdir() if p.is_dir() and not p.name.startswith(('_', '.')))
        self.assertEqual(ids, ['ai_assistant', 'bag_convert', 'dynamic_removal', 'ground_seg'])
        for pid in ids:
            m = plugins.read_manifest(str(SHIPPED / pid))
            self.assertIn(m['api'], plugins.SUPPORTED_APIS)
            self.assertTrue((SHIPPED / pid / 'ui' / 'plugin.js').is_file(), pid)
            if pid != 'ai_assistant':
                self.assertTrue(m['python']['requirements'], pid)
                self.assertTrue(all('==' in r for r in m['python']['requirements'][:1]), pid)   # main package pinned
                self.assertTrue((SHIPPED / pid / 'worker.py').is_file(), pid)


def custom_msg(sec, nsec, frame, timebase, points):
    """ROS1 serialization of a livox_ros_driver2/CustomMsg."""
    head = struct.pack('<IIII', 7, sec, nsec, len(frame)) + frame.encode()
    body = struct.pack('<QIB', timebase, len(points), 0) + b'\x00\x00\x00' + struct.pack('<I', len(points))
    for t, x, y, z, r, tag, line in points:
        body += struct.pack('<IfffBBB', t, x, y, z, r, tag, line)
    return head + body


class LivoxTests(unittest.TestCase):
    def test_parse_and_convert(self):
        livox = load('bag_convert', 'livox')
        pts = [(0, 1.0, 2.0, 3.0, 10, 0x10, 0), (50_000_000, 0.0, 0.0, 0.0, 0, 0, 1), (99_000_000, -1.5, 0.5, 0.25, 200, 0, 3)]
        raw = custom_msg(100, 500, 'livox_frame', 100 * 10**9 + 400, pts)
        sec, nsec, frame, timebase, arr = livox.parse_ros1(raw)
        self.assertEqual((sec, nsec, frame, timebase, len(arr)), (100, 500, 'livox_frame', 100 * 10**9 + 400, 3))
        cloud = livox.to_cloud(sec, nsec, timebase, arr)
        self.assertEqual(len(cloud), 2)                                   # the zero point is dropped
        self.assertEqual(cloud.itemsize, 24)
        np.testing.assert_allclose(cloud['x'], [1.0, -1.5])
        np.testing.assert_allclose(cloud['time'], [-100e-9, 99_000_000e-9 - 100e-9], atol=1e-9)
        self.assertEqual(list(cloud['ring']), [0, 3])
        self.assertEqual(list(cloud['intensity']), [10.0, 200.0])
        names = [f[0] for f in livox.FIELDS]
        offsets = [f[1] for f in livox.FIELDS]
        self.assertEqual(offsets, [cloud.dtype.fields[n][1] for n in names])
        with self.assertRaises(ValueError):
            livox.parse_ros1(raw[:-5])


class MapIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.io = load('ground_seg', 'mapio')

    def tearDown(self):
        self.tmp.cleanup()

    def test_pcd_roundtrip_and_extra_fields(self):
        xyz = np.random.default_rng(0).normal(size=(100, 3)).astype(np.float32)
        self.io.write_pcd_xyz(self.dir / 'a.pcd', xyz)
        np.testing.assert_array_equal(self.io.read_pcd_xyz(self.dir / 'a.pcd'), xyz)
        # FAST-LIO style: x y z intensity normal_x normal_y normal_z curvature
        data = np.zeros(5, dtype=[(n, '<f4') for n in ('x', 'y', 'z', 'intensity', 'normal_x', 'normal_y',
                                                       'normal_z', 'curvature')])
        data['x'] = np.arange(5)
        header = ('VERSION 0.7\nFIELDS x y z intensity normal_x normal_y normal_z curvature\nSIZE 4 4 4 4 4 4 4 4\n'
                  'TYPE F F F F F F F F\nCOUNT 1 1 1 1 1 1 1 1\nWIDTH 5\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\n'
                  'POINTS 5\nDATA binary\n')
        (self.dir / 'b.pcd').write_bytes(header.encode() + data.tobytes())
        np.testing.assert_array_equal(self.io.read_pcd_xyz(self.dir / 'b.pcd')[:, 0], np.arange(5))

    def test_scan_recording_matches_poses(self):
        rec = self.dir / 'rec'
        rec.mkdir()
        scans = [np.full((3, 4), i, np.float32) for i in range(4)]
        (rec / 'scans.bin').write_bytes(b''.join(s.tobytes() for s in scans))
        rows, off = [], 0
        for i, s in enumerate(scans):
            rows.append(f'{10 + i * 0.1:.6f} {off} {len(s)} 0 0 0 0 0 0 1')
            off += len(s)
        (rec / 'index.csv').write_text('\n'.join(rows) + '\n')
        # trajectory: poses for scans 0, 1, 3 only (scan 2 has none within 5 ms)
        (self.dir / 'traj.tum').write_text('10.0 1 0 0 0 0 0 1\n10.1005 2 0 0 0 0 0 1\n10.3 4 0 0 0 0 0 1\n')
        r = self.io.ScanRecording(str(rec), str(self.dir / 'traj.tum'))
        self.assertEqual((len(r), r.unmatched), (3, 1))
        pts, pose = r.scan(2)
        self.assertEqual((pts[0, 0], pose[0]), (3.0, 4.0))

    def test_rotations(self):
        q = np.array([[0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)]])          # 90 deg about z
        np.testing.assert_allclose(self.io.rotations(q)[0] @ [1, 0, 0], [0, 1, 0], atol=1e-12)

    def test_mapio_copies_stay_identical_and_handle_empty_inputs(self):
        self.assertEqual((SHIPPED / 'dynamic_removal' / 'mapio.py').read_text(encoding='utf-8'),
                         (SHIPPED / 'ground_seg' / 'mapio.py').read_text(encoding='utf-8'))
        empty_pcd = self.dir / 'empty.pcd'
        empty_pcd.write_text('VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nWIDTH 0\nHEIGHT 1\nPOINTS 0\nDATA ascii\n')
        self.assertEqual(self.io.read_pcd_xyz(empty_pcd).shape, (0, 3))
        bad_pcd = self.dir / 'bad.pcd'
        bad_pcd.write_text('VERSION 0.7\nDATA ascii\n')
        with self.assertRaises(ValueError):
            self.io.read_pcd_xyz(bad_pcd)
        rec = self.dir / 'empty_rec'
        rec.mkdir()
        (rec / 'scans.bin').write_bytes(b'')
        (rec / 'index.csv').write_text('10.0 0 0 0 0 0 0 0 0 1\n')
        (self.dir / 'traj.tum').write_text('10.0 0 0 0 0 0 0 1\n')
        with self.assertRaises(ValueError):
            self.io.ScanRecording(str(rec), str(self.dir / 'traj.tum'))


class DynamicRemovalWorkerTests(unittest.TestCase):
    def test_preview_empty_cloud(self):
        w = load('dynamic_removal', 'worker')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'preview.png'
            meta = w.preview(np.empty((0, 3), np.float32), np.empty((0, 3), np.float32), str(out))
            self.assertTrue(out.is_file())
            self.assertEqual((meta['width'], meta['height']), (64, 64))


class ShippedBackendTests(unittest.TestCase):
    def test_maps_routes_avoid_per_map_detail_lookups(self):
        import asyncio
        from fastapi import FastAPI
        app = FastAPI()
        detail_calls = []

        @app.get('/api/maps')
        def api_maps():
            return [
                {'name': 'm1', 'kind': 'folder', 'has_cloud': True, 'has_grid': True,
                 'has_scans': True, 'has_trajectory': True, 'created': '2026-11-21', 'points': 100},
                {'name': 'm2', 'kind': 'folder', 'has_cloud': True, 'has_grid': True,
                 'has_scans': False, 'has_trajectory': False, 'created': '2026-11-20', 'points': 50},
            ]

        @app.get('/api/maps/{name}')
        def api_map_detail(name: str):
            detail_calls.append(name)
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'paths': {'data_root': tmp},
                   'plugins': {'dirs': [str(SHIPPED)], 'state_file': str(Path(tmp) / 'state.json'),
                               'data_dir': str(Path(tmp) / 'data'), 'envs_dir': str(Path(tmp) / 'envs')}}
            mgr = plugins.PluginManager(cfg)
            mgr.set_enabled('dynamic_removal', True)
            mgr.set_enabled('ground_seg', True)
            mgr = plugins.PluginManager(cfg)
            plugins.register_plugin_routes(app, mgr)
            try:
                s1, dr = asyncio.run(plugins.asgi_request(app, 'GET', '/api/ext/dynamic_removal/maps'))
                s2, gs = asyncio.run(plugins.asgi_request(app, 'GET', '/api/ext/ground_seg/maps'))
                self.assertEqual((s1, s2), (200, 200))
                self.assertEqual([m['has_scans'] for m in dr], [True, False])
                self.assertEqual([(m['has_scans'], m['has_trajectory']) for m in gs], [(True, True), (False, False)])
                self.assertEqual(detail_calls, [])
            finally:
                mgr.shutdown()

    def test_bag_convert_sources_handles_nonexistent_nested_upload_dir(self):
        import asyncio
        from fastapi import FastAPI
        app = FastAPI()
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = str(Path(tmp) / 'a' / 'b' / 'uploads')

            @app.get('/api/playback/storage')
            def storage():
                return {'upload_dir': upload_dir}

            @app.get('/api/playback/bags')
            def bags():
                return {'roots': [], 'bags': []}

            cfg = {'paths': {'data_root': tmp},
                   'plugins': {'dirs': [str(SHIPPED)], 'state_file': str(Path(tmp) / 'state.json'),
                               'data_dir': str(Path(tmp) / 'data'), 'envs_dir': str(Path(tmp) / 'envs')}}
            mgr = plugins.PluginManager(cfg)
            mgr.set_enabled('bag_convert', True)
            mgr = plugins.PluginManager(cfg)
            plugins.register_plugin_routes(app, mgr)
            try:
                status, body = asyncio.run(plugins.asgi_request(app, 'GET', '/api/ext/bag_convert/sources'))
                self.assertEqual(status, 200, body)
                self.assertGreater(body['free'], 0)
            finally:
                mgr.shutdown()


class GroundGridTests(unittest.TestCase):
    def test_grid_from_labelled_points_on_a_slope(self):
        w = load('ground_seg', 'worker')
        rng = np.random.default_rng(1)
        # a ramp rising 1 m over 10 m, a 0.4 m box on it at x 6..6.5, and a 2 m high beam (above obs_max)
        xy = rng.uniform([0, 0], [10, 4], (40000, 2))
        ground = np.c_[xy, 0.1 * xy[:, 0]]
        box = np.c_[rng.uniform([6, 1], [6.5, 1.5], (2000, 2)), np.zeros(2000)]
        box[:, 2] = 0.1 * box[:, 0] + rng.uniform(0.05, 0.4, 2000)
        beam = np.c_[rng.uniform([2, 0], [2.2, 4], (500, 2)), 0.1 * 2.1 + 2.0 + np.zeros(500)]
        pts = np.vstack([ground, box, beam]).astype(np.float32)
        labels = np.r_[np.ones(len(ground), bool), np.zeros(len(box) + len(beam), bool)]
        grid = {'res': 0.1, 'origin': [0.0, 0.0], 'width': 100, 'height': 40}
        params = {'obs_min': 0.1, 'obs_max': 0.7, 'min_points': 2, 'closing': 1, 'fill': 1.0}
        cells, known, above = w.build_grid(pts, labels, grid, params)
        rows = np.flipud(cells)                     # row 0 = bottom again
        self.assertEqual(rows[12, 62], w.OCCUPIED)  # the box
        self.assertEqual(rows[30, 90], w.FREE)      # high end of the ramp: a flat height rule would block it
        self.assertEqual(rows[20, 21], w.FREE)      # under the beam
        self.assertTrue(np.flipud(above)[20, 21])
        self.assertLess((rows == w.OCCUPIED).sum(), 120)

    def test_fill_heights_and_samples(self):
        w = load('ground_seg', 'worker')
        h = np.zeros((5, 5))
        known = np.zeros((5, 5), bool)
        h[2, 2], known[2, 2] = 1.5, True
        filled, reach = w.fill_heights(h, known, 1)
        self.assertEqual(int(reach.sum()), 5)
        self.assertTrue(np.allclose(filled[reach], 1.5))
        poses = np.c_[np.linspace(0, 10, 101), np.zeros((101, 6))]
        self.assertEqual(list(w.path_samples(poses, 2.5)), [0, 25, 50, 75, 100])


if __name__ == '__main__':
    unittest.main()
