"""open_folder.py: only existing folders inside the console's data folders are opened; the opener is stubbed.
The route function is called directly (the venv has no HTTP test client)."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException  # noqa: E402

from robot_console import open_folder  # noqa: E402


class OpenFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-open-test-')
        base = Path(self.tmp.name).resolve()
        self.maps = base / 'maps'
        (self.maps / 'office' / 'figures').mkdir(parents=True)
        (base / 'other').mkdir()
        self.base = base
        cfg = {'mapping': {'maps_dir': str(self.maps), 'runs_dir': str(base / 'runs')},
               'evaluation': {'results_dir': str(base / 'results')}, 'playback': {'bag_dirs': []}}
        app = FastAPI()
        open_folder.register_open_folder_routes(app, cfg)
        self.route = next(r.endpoint for r in app.routes if getattr(r, 'path', '') == '/api/open-folder')
        self.started = []
        self.popen = mock.patch.object(open_folder.subprocess, 'Popen',
                                       side_effect=lambda cmd, **kw: self.started.append(cmd) or SimpleNamespace(wait=lambda timeout=None: 0))
        self.popen.start()

    def tearDown(self):
        self.popen.stop()
        self.tmp.cleanup()

    def status(self, path):
        try:
            self.route(path=path)
            return 200
        except HTTPException as e:
            return e.status_code

    def test_opens_a_map_folder_with_the_opener(self):
        with mock.patch.object(open_folder, 'opener', return_value=(['explorer.exe', 'X:\\maps\\office'], 'X:\\maps\\office')):
            r = self.route(path=str(self.maps / 'office' / 'figures'))
        self.assertEqual(self.started, [['explorer.exe', 'X:\\maps\\office']])
        self.assertEqual(r['opened'], 'X:\\maps\\office')

    def test_refuses_outside_missing_and_traversal(self):
        with mock.patch.object(open_folder, 'opener', return_value=(['true'], 'x')):
            self.assertEqual(self.status(str(self.base / 'other')), 403)
            self.assertEqual(self.status(str(self.maps / 'office' / '..' / '..' / 'other')), 403)
            self.assertEqual(self.status('/etc'), 403)
            self.assertEqual(self.status(str(self.maps / 'missing')), 404)
        self.assertEqual(self.started, [])

    def test_creates_a_missing_trash_folder_of_a_data_folder_only(self):
        with mock.patch.object(open_folder, 'opener', return_value=(['explorer.exe', 'X'], 'X')):
            self.assertEqual(self.status(str(self.maps / '.trash')), 200)
            self.assertTrue((self.maps / '.trash').is_dir())
            self.assertEqual(self.status(str(self.maps / 'office' / '.trash')), 404)   # not a data folder's trash
            self.assertFalse((self.maps / 'office' / '.trash').exists())
            self.assertEqual(self.status(str(self.base / 'other' / '.trash')), 403)
            self.assertFalse((self.base / 'other' / '.trash').exists())

    def test_no_desktop(self):
        with mock.patch.object(open_folder, 'opener', return_value=(None, None)):
            with self.assertRaises(HTTPException) as cm:
                self.route(path=str(self.maps))
        self.assertEqual(cm.exception.status_code, 501)
        self.assertEqual(cm.exception.detail['code'], 'open_folder_unsupported')


if __name__ == '__main__':
    unittest.main()
