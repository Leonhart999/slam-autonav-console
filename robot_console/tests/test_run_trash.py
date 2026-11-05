"""run_trash.py: move run folders to <runs_dir>/.trash, refusals, restore, permanent delete."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.run_trash import RunTrash, RunTrashError


class RunTrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-runtrash-test-')
        root = Path(self.tmp.name)
        self.runs, self.maps = root / 'runs', root / 'maps'
        for run, info in (('map_1', {'type': 'mapping'}), ('map_2', {'type': 'mapping'}),
                          ('map_3', {'type': 'mapping'}), ('nav_1', {'type': 'navigation', 'state': 'running'})):
            (self.runs / run).mkdir(parents=True)
            (self.runs / run / 'run.json').write_text(json.dumps(info))
            (self.runs / run / 'est.tum').write_text('0 0 0 0 0 0 0 1\n')
        (self.maps / 'office').mkdir(parents=True)
        (self.maps / 'office' / 'meta.json').write_text(json.dumps({'run': 'map_2'}))
        # An older bulk clean-up: a folder of runs without a run.json of its own
        (self.runs / '.trash' / 'p0_old' / 'map_0').mkdir(parents=True)
        (self.runs / '.trash' / 'p0_old' / 'map_0' / 'run.json').write_text('{}')
        self.recording = None
        self.batch = False
        self.trash = RunTrash(self.runs, self.maps, recording=lambda: self.recording, batch_running=lambda: self.batch)

    def tearDown(self):
        self.tmp.cleanup()

    def test_move_and_refusals(self):
        self.recording = 'map_3'
        r = self.trash.move(['map_1', 'map_2', 'map_3', 'nav_1', '../maps', 'nope', 'map_1'])
        self.assertEqual(r['moved'], ['map_1'])
        reasons = {x['id']: x['reason'] for x in r['refused']}
        self.assertEqual(reasons, {'map_2': 'used_by_map', 'map_3': 'recording', 'nav_1': 'recording',
                                   '../maps': 'not_found', 'nope': 'not_found'})
        self.assertEqual(next(x for x in r['refused'] if x['id'] == 'map_2')['maps'], ['office'])
        self.assertFalse((self.runs / 'map_1').exists())
        self.assertTrue((self.maps / 'office').is_dir())        # nothing outside runs_dir touched

    def test_batch_running_refuses_everything(self):
        self.batch = True
        with self.assertRaises(RunTrashError) as c:
            self.trash.move(['map_1'])
        self.assertEqual(c.exception.code, 'run_batch_running')
        self.assertTrue((self.runs / 'map_1').is_dir())

    def test_list_restore_purge_empty(self):
        self.trash.move(['map_1'])
        entries = {e['type']: e for e in self.trash.list()['entries']}
        self.assertEqual(entries['mapping']['run'], 'map_1')
        self.assertTrue(entries['mapping']['restorable'])
        self.assertEqual(entries['group']['runs'], 1)
        self.assertFalse(entries['group']['restorable'])
        with self.assertRaises(RunTrashError) as c:
            self.trash.restore(entries['group']['entry'])
        self.assertEqual(c.exception.code, 'run_trash_not_restorable')

        # Restore refuses to overwrite a run of the same name
        (self.runs / 'map_1').mkdir()
        with self.assertRaises(RunTrashError) as c:
            self.trash.restore(entries['mapping']['entry'])
        self.assertEqual(c.exception.code, 'run_trash_exists')
        (self.runs / 'map_1').rmdir()
        self.assertEqual(self.trash.restore(entries['mapping']['entry']), {'restored': 'map_1'})
        self.assertTrue((self.runs / 'map_1' / 'est.tum').is_file())

        self.assertEqual(self.trash.purge('p0_old'), {'deleted': 'p0_old'})
        self.trash.move(['map_1'])
        self.assertEqual(self.trash.empty(), {'deleted': 1})
        self.assertEqual(self.trash.list()['entries'], [])
        for bad in ('..', '../runs', 'a/b', ''):
            with self.assertRaises(RunTrashError):
                self.trash.purge(bad)
        self.assertTrue(self.runs.is_dir() and self.maps.is_dir())


if __name__ == '__main__':
    unittest.main()
