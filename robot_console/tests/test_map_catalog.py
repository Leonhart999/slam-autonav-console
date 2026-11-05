"""map_catalog.py: which maps are listed and which completeness issues are reported, on a temporary library.

Pure Python (numpy, PIL, PyYAML), no ROS needed.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.map_catalog import CatalogError, MapCatalog


def write_grid(folder, stem='map', w=40, h=20, res=0.05, image=None):
    folder.mkdir(parents=True, exist_ok=True)
    image = image or f'{stem}.pgm'
    Image.new('L', (w, h), 205).save(folder / image)
    (folder / f'{stem}.yaml').write_text(
        f'image: {image}\nresolution: {res}\norigin: [-1.0, -2.0, 0.0]\n'
        'negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n', encoding='utf-8')


def write_cloud(folder, name='map_voxel.pcd'):
    (folder / name).write_text(
        '# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n'
        'WIDTH 2\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS 2\nDATA ascii\n0 0 0\n1 1 1\n', encoding='utf-8')


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-catalog-test-')
        self.root = Path(self.tmp.name)
        self.catalog = MapCatalog(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, name):
        entry = {m['name']: m for m in self.catalog.list()}[name]
        return {i['code'] for i in entry['issues']}, entry

    def test_complete_map_has_no_issues(self):
        folder = self.root / 'full'
        write_grid(folder)
        write_cloud(folder)
        (folder / 'meta.json').write_text(json.dumps({'preset': 'office', 'algorithm': 'fast_lio2'}))
        codes, m = self.codes('full')
        self.assertEqual(codes, set())
        self.assertTrue(m['navigable'] and m['has_grid'] and m['has_cloud'])
        self.assertEqual(m['grid']['width'], 40)
        self.assertEqual(m['grid']['size_m'], [2.0, 1.0])
        self.assertEqual(m['algorithm'], 'fast_lio2')

    def test_only_2d_without_meta(self):
        write_grid(self.root / 'flat2d')
        codes, m = self.codes('flat2d')
        self.assertEqual(codes, {'only_2d', 'no_scene', 'no_meta'})
        self.assertTrue(m['navigable'])
        self.assertFalse(m['has_cloud'])

    def test_cloud_without_grid_is_not_navigable(self):
        folder = self.root / 'cloudonly'
        folder.mkdir()
        write_cloud(folder, 'map.pcd')
        (folder / 'meta.json').write_text(json.dumps({'preset': 'office'}))
        codes, m = self.codes('cloudonly')
        self.assertEqual(codes, {'no_grid'})
        self.assertFalse(m['navigable'])
        self.assertTrue(m['has_cloud'])
        self.assertEqual(m['points'], 2)

    def test_broken_grid_and_meta(self):
        folder = self.root / 'broken'
        write_grid(folder, image='map.pgm')
        (folder / 'map.pgm').unlink()                       # yaml points to a missing image
        (folder / 'meta.json').write_text('{not json')
        codes, m = self.codes('broken')
        self.assertIn('grid_invalid', codes)
        self.assertIn('meta_invalid', codes)
        self.assertFalse(m['navigable'])

    def test_image_outside_library_is_rejected(self):
        outside = tempfile.TemporaryDirectory(prefix='console-catalog-outside-')
        self.addCleanup(outside.cleanup)
        Image.new('L', (4, 4), 205).save(Path(outside.name) / 'x.pgm')
        folder = self.root / 'escape'
        folder.mkdir()
        (folder / 'map.yaml').write_text(f'image: {outside.name}/x.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n')
        codes, _ = self.codes('escape')
        self.assertIn('grid_invalid', codes)

    def test_legacy_flat_map_and_ignored_entries(self):
        write_grid(self.root, stem='old_office')
        (self.root / '.trash').mkdir()
        (self.root / 'not a valid name').mkdir()
        (self.root / 'empty_folder').mkdir()
        names = [m['name'] for m in self.catalog.list()]
        self.assertEqual(names, ['old_office'])
        codes, m = self.codes('old_office')
        self.assertIn('legacy_flat', codes)
        self.assertEqual(m['kind'], 'flat')

    def test_detail_and_lookup_errors(self):
        folder = self.root / 'full'
        write_grid(folder)
        write_cloud(folder)
        d = self.catalog.detail('full')
        self.assertEqual({f['name'] for f in d['files']}, {'map.pgm', 'map.yaml', 'map_voxel.pcd'})
        self.assertEqual(d['yaml_path'], str(folder.resolve() / 'map.yaml'))
        with self.assertRaises(CatalogError) as e:
            self.catalog.detail('missing')
        self.assertEqual(e.exception.code, 'map_not_found')
        with self.assertRaises(CatalogError) as e:
            self.catalog.detail('../etc')
        self.assertEqual(e.exception.code, 'invalid_map_name')

    def test_missing_root_is_empty(self):
        self.assertEqual(MapCatalog(self.root / 'nope').list(), [])


if __name__ == '__main__':
    unittest.main()
