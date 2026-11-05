"""RunRecorder (runs.py): a run folder only appears after the robot moves, and run.json stays consistent.

Pure Python, no ROS needed. Run with:
    ~/venvs/console/bin/python -m pytest -p no:anyio tests -q
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.runs import MIN_TRAVEL, RunRecorder


def pose(x, y=0.0, z=0.0):
    return SimpleNamespace(position=SimpleNamespace(x=x, y=y, z=z),
                           orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))


class Log:
    def __init__(self):
        self.warnings = []

    def warn(self, msg):
        self.warnings.append(msg)


class RunRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-runs-test-')
        self.root = Path(self.tmp.name)
        self.log = Log()
        self.rec = RunRecorder(str(self.root), self.log)

    def tearDown(self):
        self.rec.end()
        self.tmp.cleanup()

    def runs(self):
        return sorted(p.name for p in self.root.iterdir())

    def test_stationary_run_leaves_nothing(self):
        self.rec.begin({'preset': 'office'})
        for i in range(50):
            self.rec.est(i * 0.1, pose(0.01 * (i % 3)))
            self.rec.gt(i * 0.1, pose(0.0))
        self.assertTrue(self.rec.pending)
        self.rec.end()
        self.assertEqual(self.runs(), [])
        self.assertIsNone(self.rec.run_id)

    def test_folder_created_after_min_travel_with_buffered_lines(self):
        self.rec.begin({'preset': 'office'})
        self.rec.est(0.0, pose(0.0))
        self.rec.gt(0.0, pose(10.0))
        self.rec.est(0.1, pose(MIN_TRAVEL * 0.5))
        self.assertEqual(self.runs(), [])
        self.rec.est(0.2, pose(MIN_TRAVEL + 0.01))      # crosses the threshold -> commit
        self.assertFalse(self.rec.pending)
        run_id = self.rec.run_id
        self.assertRegex(run_id, r'^map_\d{8}_\d{6}$')
        self.rec.est(0.3, pose(1.0))                    # written directly now
        self.rec.end(length=1.0)
        folder = self.root / run_id
        est = (folder / 'est.tum').read_text().splitlines()
        gt = (folder / 'gt.tum').read_text().splitlines()
        self.assertEqual(len(est), 4)
        self.assertEqual(len(gt), 1)
        self.assertEqual(len(est[0].split()), 8)        # TUM: t x y z qx qy qz qw
        info = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(info['id'], run_id)
        self.assertEqual(info['type'], 'mapping')
        self.assertEqual(info['preset'], 'office')
        self.assertEqual(info['length'], 1.0)
        self.assertIsNotNone(info['ended'])
        self.assertFalse((folder / 'run.json.tmp').exists())

    def test_add_map_keeps_a_stationary_run(self):
        self.rec.begin({})
        self.rec.est(0.0, pose(0.0))
        self.rec.add_map('office_a')
        run_id = self.rec.run_id
        self.assertIsNotNone(run_id)
        self.rec.end()
        info = json.loads((self.root / run_id / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(info['maps'], ['office_a'])

    def test_update_while_pending_is_kept_for_commit(self):
        self.rec.begin({})
        self.rec.update(voxels=123)
        self.rec.est(0.0, pose(0.0))
        self.rec.est(1.0, pose(2.0))
        run_id = self.rec.run_id
        self.rec.end()
        info = json.loads((self.root / run_id / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(info['voxels'], 123)

    def test_same_second_gets_a_suffix(self):
        self.rec.begin({})
        stamp = self.rec._pending
        os.makedirs(self.root / f'map_{stamp}')
        self.rec.add_map('x')
        self.assertEqual(self.rec.run_id, f'map_{stamp}_2')

    def test_unwritable_runs_dir_warns_instead_of_raising(self):
        blocker = self.root / 'file'
        blocker.write_text('')
        rec = RunRecorder(str(blocker / 'runs'), self.log)
        rec.begin({})
        rec.add_map('x')
        self.assertIsNone(rec.run_id)
        self.assertTrue(self.log.warnings)
        rec.end()


if __name__ == '__main__':
    unittest.main()
