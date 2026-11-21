"""nav_speed.py: DWB and MPPI parameter scaling for /speed_limit."""
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.nav_speed import write_console_params


class NavSpeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-navspeed-test-')
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dwb_scaling(self):
        src = self.dir / 'dwb.yaml'
        dst = self.dir / 'out' / 'dwb.yaml'
        src.write_text(yaml.safe_dump({
            'controller_server': {'ros__parameters': {'FollowPath': {
                'plugin': 'dwb_core::DWBLocalPlanner',
                'max_vel_x': 0.5, 'max_speed_xy': 0.5, 'max_vel_theta': 1.0,
            }}},
            'velocity_smoother': {'ros__parameters': {
                'max_velocity': [0.5, 0.0, 1.0], 'min_velocity': [-0.5, 0.0, -1.0],
            }},
        }), encoding='utf-8')
        bv, bw = write_console_params(str(src), str(dst), 4.0, 2.5)
        self.assertEqual((bv, bw), (0.5, 1.0))
        out = yaml.safe_load(dst.read_text(encoding='utf-8'))
        f = out['controller_server']['ros__parameters']['FollowPath']
        s = out['velocity_smoother']['ros__parameters']
        self.assertEqual((f['max_vel_x'], f['max_speed_xy'], f['max_vel_theta']), (2.0, 2.0, 4.0))
        self.assertEqual(s['max_velocity'], [2.0, 0.0, 2.5])
        self.assertEqual(s['min_velocity'], [-2.0, 0.0, -2.5])

    def test_mppi_scaling(self):
        src = self.dir / 'mppi.yaml'
        dst = self.dir / 'out' / 'mppi.yaml'
        src.write_text(yaml.safe_dump({
            'controller_server': {'ros__parameters': {'FollowPath': {
                'plugin': 'nav2_mppi_controller::MPPIController',
                'vx_max': 0.5, 'vx_min': -0.25, 'wz_max': 1.0,
            }}},
            'velocity_smoother': {'ros__parameters': {
                'max_velocity': [0.5, 0.0, 1.0], 'min_velocity': [-0.5, 0.0, -1.0],
            }},
        }), encoding='utf-8')
        bv, bw = write_console_params(str(src), str(dst), 4.0, 2.5)
        self.assertEqual((bv, bw), (0.5, 1.0))
        out = yaml.safe_load(dst.read_text(encoding='utf-8'))
        f = out['controller_server']['ros__parameters']['FollowPath']
        s = out['velocity_smoother']['ros__parameters']
        self.assertEqual((f['vx_max'], f['vx_min'], f['wz_max']), (2.0, -1.0, 4.0))
        self.assertEqual(s['max_velocity'], [2.0, 0.0, 2.5])
        self.assertEqual(s['min_velocity'], [-2.0, 0.0, -2.5])


if __name__ == '__main__':
    unittest.main()
