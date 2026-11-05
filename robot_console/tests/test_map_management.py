"""Map management regression tests; use temporary libraries and a local HTTP server.
Run with ~/venvs/console/bin/python -m unittest discover -s tests -v.
"""
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image
import uvicorn
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.map_catalog import MapCatalog, register_map_routes
from robot_console.map_import import import_navigation
from robot_console.map_library import write_xyz_pcd
from robot_console.upload_routes import register_import_routes


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-map-test-')
        self.root = Path(self.tmp.name) / 'maps'
        self.root.mkdir()
        self.library = SimpleNamespace(maps_dir=str(self.root), status=lambda: self.job)
        self.job = {'state': 'idle'}
        self.stack = SimpleNamespace(state=lambda: self.nav_state,
            cmd=['ros2', 'launch', 'lidar_nav', 'navigation.launch.py'], run_cmd=None)
        self.nav_state = 'idle'
        procs = SimpleNamespace(get=lambda name: self.stack)
        cfg = {'mapping': {'maps_dir': str(self.root)},
               'server': {'venv': str(Path(sys.executable).resolve().parent.parent)},
               'navigation': {'stack': 'navigation'}}
        # sys.executable may resolve to /usr/bin/python: use the installed console venv.
        cfg['server']['venv'] = str(Path.home() / 'venvs/console')
        self.app = FastAPI()
        register_import_routes(self.app, cfg, self.library,
            SimpleNamespace(root=Path(self.tmp.name) / 'scenes'), {}, procs)
        self.catalog = register_map_routes(self.app, cfg, library=self.library, procs=procs)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error'))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.01)
        self.assertTrue(self.server.started)
        x, y = np.meshgrid(np.arange(0, 2, .05), np.arange(0, 2, .05))
        floor = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
        wall = np.column_stack([np.ones(100), np.linspace(0, 2, 100), np.full(100, .4)])
        self.cloud = Path(self.tmp.name) / 'source.pcd'
        write_xyz_pcd(self.cloud, np.vstack([floor, wall]).astype(np.float32))

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.tmp.cleanup()

    def request(self, method, path, body=None, files=None):
        headers = {}
        if files is not None:
            boundary = 'console-test-boundary'
            parts = []
            for key, value in (body or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            for key, (name, content) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + content + b'\r\n')
            data = b''.join(parts) + f'--{boundary}--\r\n'.encode()
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        else:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None:
                headers['Content-Type'] = 'application/json'
        try:
            response = urlopen(Request(self.base + path, data=data, headers=headers, method=method), timeout=30)
        except HTTPError as e:
            response = e
        raw = response.read()
        return response.status, (json.loads(raw) if 'json' in response.headers.get('Content-Type', '') else raw)

    def upload_cloud(self, name='cloud'):
        code, body = self.request('POST', '/api/navigation/import', {'name': name},
            {'cloud_file': ('cloud.pcd', self.cloud.read_bytes())})
        self.assertEqual(code, 200, body)
        return body

    def project(self, name='cloud', **params):
        code, body = self.request('POST', f'/api/maps/{name}/regrid', params)
        self.assertEqual(code, 200, body)
        return body

    def test_cloud_import_preview_save_and_recycle(self):
        result = self.upload_cloud()
        self.assertIsNone(result['grid'])
        original = self.root / 'cloud' / 'map.pcd'
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        code, detail = self.request('GET', '/api/maps/cloud')
        self.assertEqual(code, 200)
        self.assertFalse(detail['navigable'])
        self.assertIsNone(detail['voxels'])
        preview = self.project(res=.1)
        code, png = self.request('GET', f"/api/maps/regrid/{preview['token']}/grid.png")
        self.assertEqual(code, 200)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (preview['grid']['width'], preview['grid']['height']))
        code, saved = self.request('POST', f"/api/maps/regrid/{preview['token']}/save", {'name': 'version2'})
        self.assertEqual(code, 200, saved)
        self.assertTrue(saved['navigable'])
        self.assertFalse((self.root / 'cloud' / 'map.yaml').exists())
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), before)
        self.assertEqual(original.stat().st_ino, (self.root / 'version2/map.pcd').stat().st_ino)
        import yaml
        metadata = yaml.safe_load((self.root / 'version2/map.yaml').read_text())
        self.assertLess(metadata['free_thresh'], 1 - 205/255)
        self.assertEqual(self.request('GET', f"/api/maps/regrid/{preview['token']}/grid.png")[0], 404)
        code, trashed = self.request('DELETE', '/api/maps/cloud')
        self.assertEqual(code, 200, trashed)
        self.assertTrue((self.root / 'version2/map.pcd').is_file())
        code, restored = self.request('POST', f"/api/map-trash/{trashed['id']}/restore")
        self.assertEqual(code, 200, restored)
        self.assertTrue(original.is_file())
        self.request('DELETE', '/api/maps/cloud')
        self.assertEqual(self.request('DELETE', '/api/map-trash')[0], 200)
        self.assertEqual(self.request('GET', '/api/map-trash')[1], [])
        self.assertEqual(hashlib.sha256((self.root / 'version2/map.pcd').read_bytes()).hexdigest(), before)

    def test_parameters_and_experimental_gate(self):
        self.upload_cloud()
        for payload in [{'res': 0}, {'obs_min': .9, 'obs_max': .5}, {'res': True},
                        {'experimental': False, 'ground_mode': 'heightmap'},
                        {'ground_cell': .5}, {'min_obstacle_area': .01}, {'level': True}]:
            code, body = self.request('POST', '/api/maps/cloud/regrid', payload)
            self.assertEqual(code, 400, (payload, body))
        preview = self.project(experimental=True, ground_mode='heightmap', ground_cell=.5,
                               ground_smooth=.5, min_obstacle_area=.01, min_free_area=0)
        self.assertTrue(preview['experimental'])
        self.assertEqual(self.request('DELETE', f"/api/maps/regrid/{preview['token']}")[0], 200)

    def test_save_refuses_existing_name_or_changed_source(self):
        self.upload_cloud()
        preview = self.project()
        self.assertEqual(self.request('POST', f"/api/maps/regrid/{preview['token']}/save", {'name': 'cloud'})[0], 409)
        cloud = self.root / 'cloud/map.pcd'
        cloud.write_bytes(cloud.read_bytes() + b' ')
        self.assertEqual(self.request('POST', f"/api/maps/regrid/{preview['token']}/save", {'name': 'new'})[0], 400)
        self.assertFalse((self.root / 'new').exists())

    def test_busy_maps_cannot_be_deleted(self):
        self.upload_cloud()
        self.job = {'state': 'running', 'name': 'cloud'}
        self.assertEqual(self.request('DELETE', '/api/maps/cloud')[0], 409)
        self.job = {'state': 'idle'}
        self.nav_state = 'running'
        self.stack.run_cmd = ['ros2', 'launch', 'lidar_nav', 'navigation.launch.py', f'map:={self.root}/cloud/map.yaml']
        self.assertEqual(self.request('DELETE', '/api/maps/cloud')[0], 409)
        self.nav_state = 'stopping'
        self.assertEqual(self.request('DELETE', '/api/maps/cloud')[0], 409)
        self.assertTrue((self.root / 'cloud').is_dir())

    def test_restore_collision_does_not_overwrite(self):
        self.upload_cloud()
        code, record = self.request('DELETE', '/api/maps/cloud')
        self.assertEqual(code, 200)
        self.upload_cloud()
        self.assertEqual(self.request('POST', f"/api/map-trash/{record['id']}/restore")[0], 409)
        self.assertEqual(len(self.request('GET', '/api/map-trash')[1]), 1)

    def test_import_pairing_and_existing_name(self):
        code, result = self.request('POST', '/api/navigation/import', {'name': 'bad'},
                                  {'yaml_file': ('map.yaml', b'image: map.pgm')})
        self.assertEqual(code, 400, result)
        self.assertFalse((self.root / 'bad').exists())
        self.upload_cloud()
        code, result = self.request('POST', '/api/navigation/import', {'name': 'cloud'},
                                  {'cloud_file': ('map.pcd', self.cloud.read_bytes())})
        self.assertEqual(code, 409, result)

    def test_2d_only_and_combined_import_still_work(self):
        image = io.BytesIO()
        Image.new('L', (8, 6), 254).save(image, format='PPM')
        yaml = b'image: floor.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n'
        for name, with_cloud in [('grid', False), ('both', True)]:
            files = {'yaml_file': ('floor.yaml', yaml), 'image_file': ('floor.pgm', image.getvalue())}
            if with_cloud:
                files['cloud_file'] = ('map.pcd', self.cloud.read_bytes())
            code, body = self.request('POST', '/api/navigation/import', {'name': name}, files)
            self.assertEqual(code, 200, body)
            self.assertIsNotNone(body['grid'])
            self.assertEqual(body['has_cloud'], with_cloud)

    def test_trash_refuses_links_and_preserves_outside_files(self):
        self.upload_cloud()
        outside = Path(self.tmp.name) / 'outside'
        outside.write_text('keep')
        (self.root / 'cloud/link').symlink_to(outside)
        self.assertEqual(self.request('DELETE', '/api/maps/cloud')[0], 400)
        self.assertEqual(outside.read_text(), 'keep')
        self.assertTrue((self.root / 'cloud').is_dir())

    def test_map_named_trash_does_not_touch_recycle_bin(self):
        # Regression: trash routes once lived at /api/maps/trash and shadowed a map called 'trash'
        self.upload_cloud('other')
        code, other = self.request('DELETE', '/api/maps/other')
        self.assertEqual(code, 200, other)
        self.upload_cloud('trash')
        code, detail = self.request('GET', '/api/maps/trash')
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['name'], 'trash')
        code, moved = self.request('DELETE', '/api/maps/trash')
        self.assertEqual(code, 200, moved)
        self.assertEqual(moved['name'], 'trash')
        self.assertFalse((self.root / 'trash').exists())
        names = sorted(row['name'] for row in self.request('GET', '/api/map-trash')[1])
        self.assertEqual(names, ['other', 'trash'])


if __name__ == '__main__':
    unittest.main()
