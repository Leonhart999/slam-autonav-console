"""POST /api/maps/{name}/derive: new map versions with a replacement grid and/or cloud (map_derive.py), and the
scan paths in the map detail."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml
from fastapi import FastAPI
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import plugins  # noqa: E402
from robot_console.map_catalog import MapCatalog  # noqa: E402
from robot_console.map_derive import register_derive_routes  # noqa: E402
from robot_console.map_library import write_xyz_pcd  # noqa: E402


def make_map(root, name, w=6, h=4):
    d = root / name
    d.mkdir()
    grid = np.full((h, w), 254, dtype=np.uint8)
    grid[0, :] = 0
    Image.fromarray(grid, mode='L').save(d / 'map.pgm')
    (d / 'map.yaml').write_text(yaml.safe_dump({'image': 'map.pgm', 'mode': 'trinary', 'resolution': 0.05,
                                                'origin': [-1.0, -2.0, 0.0], 'negate': 0,
                                                'occupied_thresh': 0.65, 'free_thresh': 0.25}))
    write_xyz_pcd(d / 'map.pcd', np.random.default_rng(0).uniform(-1, 1, (500, 3)).astype(np.float32))
    (d / 'meta.json').write_text(json.dumps({'name': name, 'preset': 'office', 'algorithm': 'FAST-LIO2',
                                             'input_recording': '/nowhere'}))
    (d / 'goals.yaml').write_text('goals: []\n')
    (d / 'data').mkdir()
    (d / 'data' / 'est.tum').write_text('0 0 0 0 0 0 0 1\n')
    (d / 'figures').mkdir()
    return d, grid


class DeriveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src, self.grid = make_map(self.root, 'office')
        self.app = FastAPI()
        cfg = {'server': {'venv': str(Path.home() / 'venvs/console')}}
        self.catalog = MapCatalog(self.root)
        register_derive_routes(self.app, self.catalog, cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, name, fields, files=None):
        body, ctype = plugins.multipart_body(fields, files)
        return asyncio.run(plugins.asgi_request(self.app, 'POST', f'/api/maps/{name}/derive', content=body,
                                                content_type=ctype))

    def test_replace_grid_keeps_cloud_data_and_lineage(self):
        new = self.grid.copy()
        new[1, 1] = 0
        new[3, 5] = 205
        status, body = self.post('office', {'new_name': 'office_ground', 'tool': 'Ground tool', 'grid_mode': 'replace',
                                            'note': json.dumps({'sensor_height': 0.4})},
                                 {'grid': ('grid.bin', new.tobytes())})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['cells'], {'occupied': 7, 'unknown': 1, 'free': 16})
        d = self.root / 'office_ground'
        np.testing.assert_array_equal(np.asarray(Image.open(d / 'map.pgm')), new)
        info = yaml.safe_load((d / 'map.yaml').read_text())
        self.assertEqual((info['resolution'], info['origin'], info['free_thresh']), (0.05, [-1.0, -2.0, 0.0], 0.15))
        self.assertEqual((d / 'map.pcd').read_bytes(), (self.src / 'map.pcd').read_bytes())
        self.assertTrue((d / 'data' / 'est.tum').is_file())
        self.assertTrue((d / 'goals.yaml').is_file())
        self.assertFalse((d / 'figures').exists())
        meta = json.loads((d / 'meta.json').read_text())
        self.assertEqual((meta['source'], meta['parent'], meta['preset'], meta['input_recording']),
                         ('derived', 'office', 'office', '/nowhere'))
        self.assertEqual(meta['derived'][-1]['tool'], 'Ground tool')
        self.assertEqual(meta['derived'][-1]['note'], {'sensor_height': 0.4})
        self.assertEqual(meta['derived'][-1]['grid'], 'replace')
        self.assertFalse(list(self.root.glob('.derive*')))
        # the source never changes
        np.testing.assert_array_equal(np.asarray(Image.open(self.src / 'map.pgm')), self.grid)

    def test_replace_cloud_keep_grid(self):
        pts = np.random.default_rng(1).uniform(0, 3, (2000, 3)).astype(np.float32)
        with tempfile.NamedTemporaryFile(suffix='.pcd') as f:
            write_xyz_pcd(f.name, pts)
            status, body = self.post('office', {'new_name': 'office_static', 'tool': 'DUFOMap'},
                                     {'cloud': ('static.pcd', f.name)})
        self.assertEqual(status, 200, body)
        d = self.root / 'office_static'
        meta = json.loads((d / 'meta.json').read_text())
        self.assertEqual(meta['points'], 2000)
        self.assertLessEqual(meta['voxels'], 2000)
        self.assertEqual(meta['derived'][-1]['cloud'], 'replaced')
        self.assertEqual((d / 'map.pgm').read_bytes(), (self.src / 'map.pgm').read_bytes())
        detail = self.catalog.detail('office_static')
        self.assertEqual(detail['points'], 2000)
        self.assertTrue(detail['has_grid'])

    @unittest.skipUnless((Path.home() / 'venvs/console/bin/python').exists(), 'console venv with pcd2grid')
    def test_regrid_with_a_new_cloud(self):
        rng = np.random.default_rng(2)
        floor = np.c_[rng.uniform(-2, 2, (20000, 2)), np.zeros(20000)]
        wall = np.c_[np.full(3000, 1.5), rng.uniform(-2, 2, 3000), rng.uniform(0, 1, 3000)]
        with tempfile.NamedTemporaryFile(suffix='.pcd') as f:
            write_xyz_pcd(f.name, np.vstack([floor, wall]).astype(np.float32))
            status, body = self.post('office', {'new_name': 'office_regrid', 'tool': 'x', 'grid_mode': 'regrid'},
                                     {'cloud': ('c.pcd', f.name)})
        self.assertEqual(status, 200, body)
        d = self.root / 'office_regrid'
        meta = json.loads((d / 'meta.json').read_text())
        self.assertEqual(meta['derived'][-1]['grid'], 'regrid')
        self.assertTrue(meta['regrid']['derived'])
        grid = np.asarray(Image.open(d / 'map.pgm'))
        self.assertTrue((grid == 0).any() and (grid == 254).any())

    def test_errors(self):
        cases = [
            ({'new_name': 'office', 'tool': 'x', 'grid_mode': 'regrid'}, None, 409, 'map_exists'),
            ({'new_name': 'bad name', 'tool': 'x', 'grid_mode': 'regrid'}, None, 400, 'invalid_map_name'),
            ({'new_name': 'v2', 'tool': 'x'}, None, 400, 'map_derive_failed'),                  # nothing to change
            ({'new_name': 'v2', 'tool': '', 'grid_mode': 'regrid'}, None, 400, 'map_derive_failed'),
            ({'new_name': 'v2', 'tool': 'x', 'grid_mode': 'replace'}, {'grid': ('g', b'\x00' * 5)}, 400,
             'map_derive_failed'),                                                               # wrong size
            ({'new_name': 'v2', 'tool': 'x', 'grid_mode': 'replace'}, {'grid': ('g', b'\x07' * 24)}, 400,
             'map_derive_failed'),                                                               # bad values
            ({'new_name': 'v2', 'tool': 'x', 'grid_mode': 'keep', 'note': '[1]'}, {'cloud': ('c', b'x')}, 400,
             'map_derive_failed'),
            ({'new_name': 'v2', 'tool': 'x', 'extra': '1', 'grid_mode': 'regrid'}, None, 400, 'map_derive_failed'),
        ]
        for fields, files, status, code in cases:
            got_status, body = self.post('office', fields, files)
            self.assertEqual((got_status, body['detail']['code']), (status, code), (fields, body))
        self.assertEqual(self.post('missing', {'new_name': 'v3', 'tool': 'x', 'grid_mode': 'regrid'})[0], 404)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['office'])

    def test_detail_names_the_scan_recording(self):
        rec = self.root / 'rec'
        rec.mkdir()
        (rec / 'scans.bin').write_bytes(b'')
        (rec / 'index.csv').write_text('')
        meta = json.loads((self.src / 'meta.json').read_text())
        meta['input_recording'] = str(rec)
        (self.src / 'meta.json').write_text(json.dumps(meta))
        self.assertIsNone(self.catalog.detail('office')['scans'])          # no scan trajectory yet
        (self.src / 'data' / 'scan_poses.tum').write_text('')
        self.assertEqual(self.catalog.detail('office')['scans'],
                         {'recording': str(rec), 'trajectory': str(self.src / 'data' / 'scan_poses.tum')})


if __name__ == '__main__':
    unittest.main()
