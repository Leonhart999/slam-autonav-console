"""MapEditor: save-as-new-version, validation, staging (pure Python, temporary map library)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.map_edit import FREE, OCCUPIED, UNKNOWN, EditError, MapEditor, classify


def make_map(root, name, grid, extra=True):
    d = root / name
    d.mkdir()
    Image.fromarray(grid, mode='L').save(d / 'map.pgm')
    (d / 'map.yaml').write_text(yaml.safe_dump({'image': 'map.pgm', 'mode': 'trinary', 'resolution': 0.05,
                                                'origin': [-1.0, -2.0, 0.0], 'negate': 0,
                                                'occupied_thresh': 0.65, 'free_thresh': 0.25}))
    if extra:
        (d / 'meta.json').write_text(json.dumps({'name': name, 'preset': 'office', 'edits': []}))
        (d / 'map.pcd').write_bytes(b'cloud')
        (d / 'goals.yaml').write_text('goals: []\n')
    return d


class MapEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.grid = np.full((4, 6), FREE, dtype=np.uint8)
        self.grid[0, :] = OCCUPIED
        self.grid[3, 5] = UNKNOWN
        make_map(self.root, 'office', self.grid)
        self.ed = MapEditor(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_grid_roundtrip_and_save_new_version(self):
        data, w, h, _, _ = self.ed.grid('office')
        self.assertEqual((w, h), (6, 4))
        g = np.frombuffer(data, dtype=np.uint8).reshape(h, w).copy()
        g[0, 0] = FREE       # erase one obstacle cell
        g[2, 2] = OCCUPIED   # draw one
        out = self.ed.save('office', 'office_edit', g.tobytes())
        self.assertEqual(out['changed'], {'occupied': 1, 'unknown': 0, 'free': 1})
        d = self.root / 'office_edit'
        saved = np.asarray(Image.open(d / 'map.pgm'))
        np.testing.assert_array_equal(saved, g)
        info = yaml.safe_load((d / 'map.yaml').read_text())
        self.assertEqual(info['resolution'], 0.05)
        self.assertEqual(info['origin'], [-1.0, -2.0, 0.0])
        meta = json.loads((d / 'meta.json').read_text())
        self.assertEqual(meta['parent'], 'office')
        self.assertEqual(meta['preset'], 'office')
        self.assertEqual(meta['edits'][-1]['cells']['free'], 1)
        for f in ('map.pcd', 'goals.yaml', 'map_preview.png'):
            self.assertTrue((d / f).is_file(), f)
        # Source untouched, no staging folder left
        np.testing.assert_array_equal(np.asarray(Image.open(self.root / 'office' / 'map.pgm')), self.grid)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith('.')], [])

    def test_rejects_bad_input(self):
        data, *_ = self.ed.grid('office')
        cases = [('office', 'x', b'\x00' * 3, 'map_edit_size'),
                 ('office', 'x', bytes([7]) * len(data), 'map_edit_values'),
                 ('office', 'office', data, 'map_exists'),
                 ('office', '../evil', data, 'invalid_map_name'),
                 ('nope', 'x', data, 'map_not_found'),
                 ('office', 'x', data, 'map_edit_unchanged')]
        for src, new, body, code in cases:
            with self.assertRaises(EditError) as cm:
                self.ed.save(src, new, body)
            self.assertEqual(cm.exception.code, code)
        self.assertFalse((self.root / 'x').exists())

    def test_classify(self):
        info = {'negate': 0, 'occupied_thresh': 0.65, 'free_thresh': 0.25}
        raw = np.array([[0, 100, 180, 205, 230, 254]], dtype=np.uint8)
        # map_server: p = (255 - v) / 255; 205 -> 0.196 < 0.25 -> free
        self.assertEqual(classify(raw, info).tolist(), [[OCCUPIED, UNKNOWN, UNKNOWN, FREE, FREE, FREE]])

    def test_legacy_free_thresh_025_keeps_nav2_meaning(self):
        """Source with free_thresh 0.25: every unedited cell must classify the same in the new version."""
        d = make_map(self.root, 'old', self.grid)
        info = yaml.safe_load((d / 'map.yaml').read_text())
        info['free_thresh'] = 0.25
        (d / 'map.yaml').write_text(yaml.safe_dump(info))
        data, w, h, _, legacy = self.ed.grid('old')
        self.assertEqual(legacy, 1)                       # the single 205 pixel is read as free
        g = np.frombuffer(data, dtype=np.uint8).reshape(h, w).copy()
        before = g.copy()
        g[2, 1] = OCCUPIED
        self.ed.save('old', 'old_edit', g.tobytes())
        new = yaml.safe_load((self.root / 'old_edit' / 'map.yaml').read_text())
        new_cls = classify(np.asarray(Image.open(self.root / 'old_edit' / 'map.pgm')), new)
        src_cls = classify(self.grid, info)
        mask = np.ones_like(self.grid, dtype=bool)
        mask[2, 1] = False
        np.testing.assert_array_equal(new_cls[mask], src_cls[mask])
        np.testing.assert_array_equal(src_cls, before)
        edit = json.loads((self.root / 'old_edit' / 'meta.json').read_text())['edits'][-1]
        self.assertFalse(edit['legacy_unknown'])
        self.assertEqual((edit['legacy_free_cells'], edit['restored_unknown']), (1, 0))

        # Option on: the legacy cell goes back to unknown, even without other edits
        out = self.ed.save('old', 'old_fix', before.tobytes(), legacy_unknown=True)
        self.assertEqual(out['restored_unknown'], 1)
        fixed = np.asarray(Image.open(self.root / 'old_fix' / 'map.pgm'))
        self.assertEqual(int(fixed[3, 5]), UNKNOWN)
        self.assertTrue(json.loads((self.root / 'old_fix' / 'meta.json').read_text())['edits'][-1]['legacy_unknown'])


if __name__ == '__main__':
    unittest.main()
