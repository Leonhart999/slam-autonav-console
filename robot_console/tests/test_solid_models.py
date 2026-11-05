"""solid_models.asset_path: only known file types inside the model folder, also with symlinked files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import solid_models


class AssetPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-solid-test-')
        root = Path(self.tmp.name)
        self.models = root / 'share' / 'models'
        model = self.models / 'shelf'
        (model / 'meshes').mkdir(parents=True)
        (model / 'model.sdf').write_text('<sdf/>')
        # colcon --symlink-install: installed files are symlinks into another tree
        src = root / 'src' / 'shelf.dae'
        src.parent.mkdir()
        src.write_text('<COLLADA/>')
        os.symlink(src, model / 'meshes' / 'shelf.dae')
        (root / 'secret.png').write_text('x')
        patcher = mock.patch.object(solid_models, 'model_dirs', return_value=[self.models])
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_symlinked_mesh_is_served(self):
        p = solid_models.asset_path('shelf', 'meshes/shelf.dae')
        self.assertEqual(p, self.models / 'shelf' / 'meshes' / 'shelf.dae')

    def test_outside_or_unknown_files_are_refused(self):
        self.assertIsNone(solid_models.asset_path('shelf', '../../../secret.png'))
        self.assertIsNone(solid_models.asset_path('shelf', str(Path(self.tmp.name) / 'secret.png')))
        self.assertIsNone(solid_models.asset_path('shelf', 'model.sdf'))           # not a served file type
        self.assertIsNone(solid_models.asset_path('shelf', 'meshes/missing.dae'))
        self.assertIsNone(solid_models.asset_path('../shelf', 'meshes/shelf.dae'))  # invalid model name


if __name__ == '__main__':
    unittest.main()
