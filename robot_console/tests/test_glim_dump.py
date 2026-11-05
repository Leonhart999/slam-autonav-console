"""glim_dump.py (reading GLIM's dump) and RunRecorder.replace_est (swapping in the optimized trajectory).

Pure Python, no ROS or GLIM needed: the dump is a small synthetic one in GLIM 1.2's layout. Run with:
    ~/venvs/console/bin/python -m pytest -p no:anyio tests -q
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import glim_dump
from robot_console.runs import RunRecorder


def write_dump(root, submaps, traj):
    """submaps: list of (4x4 T_world_origin, (N, 3) points in the submap frame)."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, 'graph.txt'), 'w') as f:
        f.write(f'num_submaps: {len(submaps)}\nnum_all_frames: 3\nnum_matching_cost_factors: 0\n')
    for i, (T, pts) in enumerate(submaps):
        d = os.path.join(root, f'{i:06d}')
        os.makedirs(d)
        with open(os.path.join(d, 'data.txt'), 'w') as f:
            f.write(f'id: {i}\nT_world_origin: \n')
            for row in T:
                f.write('   ' + ' '.join(f'{v:g}' for v in row) + '\n')   # Eigen pads rows with spaces
            f.write('T_origin_endpoint_L: \n1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n')
        np.asarray(pts, '<f4').tofile(os.path.join(d, 'points_compact.bin'))
    with open(os.path.join(root, 'traj_imu.txt'), 'w') as f:
        for t, x in traj:
            f.write(f'{t:.9f} {x:.6f} 0.000000 0.000000 0.000000 0.000000 0.000000 1.000000\n')


class Log:
    def warn(self, msg):
        pass


def pose(x):
    return SimpleNamespace(position=SimpleNamespace(x=x, y=0.0, z=0.0),
                           orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))


class GlimDumpTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dump = os.path.join(self.tmp.name, 'dump')

    def tearDown(self):
        self.tmp.cleanup()

    def test_map_points_applies_submap_poses_and_ignores_stale_folders(self):
        T1 = np.eye(4)
        T1[:3, 3] = [10, 0, 0]
        T1[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]      # 90 deg yaw
        write_dump(self.dump, [(np.eye(4), [[1, 2, 3]]), (T1, [[1, 0, 0], [0, 1, 0]])], [])
        # a leftover folder from a longer earlier run in the same dump_path must not be read
        stale = os.path.join(self.dump, '000002')
        os.makedirs(stale)
        pts = glim_dump.map_points(self.dump)
        np.testing.assert_allclose(pts, [[1, 2, 3], [10, 1, 0], [9, 0, 0]], atol=1e-5)

    def test_missing_and_empty_dump(self):
        with self.assertRaises(glim_dump.DumpError) as e:
            glim_dump.map_points(self.dump)
        self.assertEqual(e.exception.code, 'glim_dump_missing')
        write_dump(self.dump, [], [])
        with self.assertRaises(glim_dump.DumpError) as e:
            glim_dump.map_points(self.dump)
        self.assertEqual(e.exception.code, 'glim_dump_empty')

    def test_trajectory_window(self):
        write_dump(self.dump, [], [(1.0, 0.1), (2.0, 0.2), (3.0, 0.3)])
        lines = glim_dump.trajectory(self.dump, 1.5, 3.0)
        self.assertEqual([float(l.split()[0]) for l in lines], [2.0, 3.0])

    def test_replace_est_keeps_online_estimate(self):
        runs = os.path.join(self.tmp.name, 'runs')
        rec = RunRecorder(runs, Log())
        rec.begin({'frame': 'glim_odom'})
        for i in range(10):
            rec.est(float(i), pose(i * 0.2))          # moves 1.8 m, so the folder is created
        run_id = rec.run_id
        self.assertIsNotNone(run_id)
        self.assertTrue(rec.replace_est(run_id, ['5.0 1 0 0 0 0 0 1\n'], 'test'))
        rec.est(11.0, pose(3.0))                      # dropped: est.tum is closed after the swap
        rec.end()
        d = os.path.join(runs, run_id)
        self.assertEqual(open(os.path.join(d, 'est.tum')).read(), '5.0 1 0 0 0 0 0 1\n')
        self.assertEqual(len(open(os.path.join(d, 'est_online.tum')).readlines()), 10)
        info = json.load(open(os.path.join(d, 'run.json'), encoding='utf-8'))
        self.assertEqual(info['est_source'], 'test')
        self.assertIn('est_online', info['files'])
        self.assertEqual(info['files']['est'], 'est.tum (glim_odom)')


if __name__ == '__main__':
    unittest.main()
