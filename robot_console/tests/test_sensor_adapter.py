"""Sensor adapter transform (no ROS needed) and sensor-profile playback."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robot_console.playback import Playback, PlaybackError, play_args, sensor_profile_of  # noqa: E402
from robot_console.sensor_adapter import OUT_DTYPE, Transform  # noqa: E402

CLOUD, IMU = 'sensor_msgs/msg/PointCloud2', 'sensor_msgs/msg/Imu'
FLIP = [[0, -1, 0, 0.01], [-1, 0, 0, 0.02], [0, 0, -1, 0.03], [0, 0, 0, 1]]     # lidar -> imu
IMU_BASE = [[0, 0, -1, 0.3], [-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]]         # imu -> base (imu y is up)
PROFILE = {'label': {'zh': '实车', 'en': 'robot'}, 'match': ['/front_lidar', '/front_lidar/imu'],
           'points': '/front_lidar', 'imu': '/front_lidar/imu',
           'point_time': {'field': 'timestamp', 'absolute': True}, 'imu_acc_unit': 'g',
           'lidar_to_imu': FLIP, 'imu_to_base': IMU_BASE,
           'slam_args': {'mapping_fastlio': ['config_file:=fastlio_bag.yaml']}}


def rs_cloud(points, stamp):
    """Bytes of a RoboSense-like cloud: x y z intensity (f4) ring (u2) timestamp (f8), point_step 26."""
    dt = np.dtype({'names': ['x', 'y', 'z', 'intensity', 'ring', 'timestamp'],
                   'formats': ['<f4', '<f4', '<f4', '<f4', '<u2', '<f8'],
                   'offsets': [0, 4, 8, 12, 16, 18], 'itemsize': 26})
    a = np.zeros(len(points), dtype=dt)
    for i, (x, y, z, ring, dt_s) in enumerate(points):
        a[i] = (x, y, z, 5.0, ring, stamp + dt_s)
    fields = [('x', 0, 7), ('y', 4, 7), ('z', 8, 7), ('intensity', 12, 7), ('ring', 16, 4), ('timestamp', 18, 8)]
    return fields, 26, a.tobytes()


class TransformTests(unittest.TestCase):
    def test_points_go_to_body_frame_with_relative_time(self):
        tf = Transform(PROFILE)
        stamp = 1793427531.2
        fields, step, data = rs_cloud([(1.0, 2.0, 3.0, 95, 0.05), (np.nan, np.nan, np.nan, 3, 0.0)], stamp)
        out = tf.points(fields, step, data, stamp)
        self.assertEqual(out.dtype, OUT_DTYPE)
        self.assertEqual(len(out), 1)                       # the NaN point is dropped
        p_imu = np.array(FLIP)[:3, :3] @ [1.0, 2.0, 3.0] + [0.01, 0.02, 0.03]
        p_body = np.array(IMU_BASE)[:3, :3] @ p_imu         # no lever arm: origin stays at the IMU
        np.testing.assert_allclose([out['x'][0], out['y'][0], out['z'][0]], p_body, atol=1e-5)
        self.assertAlmostEqual(float(out['time'][0]), 0.05, places=5)
        self.assertEqual(int(out['ring'][0]), 95)
        self.assertEqual(float(out['intensity'][0]), 5.0)

    def test_imu_rotated_and_scaled_from_g(self):
        tf = Transform(PROFILE)
        gyro, acc = tf.imu((0.0, 0.1, 0.0), (0.0, 1.0, 0.0))  # standing still: gravity reaction along imu +y
        np.testing.assert_allclose(acc, [0, 0, 9.80665], atol=1e-9)   # -> base +z in m/s^2
        np.testing.assert_allclose(gyro, [0, 0, 0.1], atol=1e-12)

    def test_slightly_non_orthonormal_rotation_is_projected(self):
        T = np.array(IMU_BASE, float)
        T[:3, :3] *= 1.00006
        tf = Transform({**PROFILE, 'imu_to_base': T.tolist()})
        np.testing.assert_allclose(tf.R_bi @ tf.R_bi.T, np.eye(3), atol=1e-12)
        with self.assertRaises(ValueError):
            Transform({**PROFILE, 'imu_acc_unit': 'mg'})


def make_bag(root, name, topics):
    d = Path(root) / name
    d.mkdir(parents=True)
    (d / 'bag_0.mcap').write_bytes(b'x')
    info = {'version': 5, 'storage_identifier': 'sqlite3', 'relative_file_paths': ['bag_0.mcap'],
            'duration': {'nanoseconds': 60_000_000_000}, 'starting_time': {'nanoseconds_since_epoch': 1793427531200000000},
            'message_count': 10,
            'topics_with_message_count': [{'topic_metadata': {'name': n, 'type': t, 'serialization_format': 'cdr',
                                                              'offered_qos_profiles': ''}, 'message_count': 5}
                                          for n, t in topics]}
    (d / 'metadata.yaml').write_text(yaml.safe_dump({'rosbag2_bagfile_information': info}))
    return str(d)


class FakeStack:
    def __init__(self, state='idle'):
        self._state, self.started_at, self.exit_code, self.preset = state, 100.0, None, None

    def state(self):
        return self._state


class ProfilePlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-sensor-test-')
        self.bag = make_bag(self.tmp.name, 'heu61', [('/front_lidar', CLOUD), ('/front_lidar/imu', IMU),
                                                     ('/rear_lidar', CLOUD)])
        self.stacks = {n: FakeStack() for n in ('playback', 'sim', 'sensor_adapter', 'mapping_fastlio', 'mapping_glim')}
        self.started = []

        def start(name, args, preset, origin):
            self.started.append((name, args))
            self.stacks[name]._state = 'running'
        procs = SimpleNamespace(get=self.stacks.get, start=start, stop=lambda n: None)
        node = SimpleNamespace(count_subscribers=lambda topic: 1, sim_now=lambda: None)
        profiles = {'mapping_fastlio': {'inputs': {'points': '/points_fastlio', 'imu': '/imu'}, 'sources': ['sim', 'bag']},
                    'mapping_glim': {'inputs': {'points': '/points_fastlio', 'imu': '/imu'}, 'sources': ['sim', 'bag']}}
        mapping = SimpleNamespace(profiles=profiles, stacks={'sim': 'sim'}, last_stamp=lambda: None)
        cfg = {'playback': {'bag_dirs': [self.tmp.name], 'sensor_profiles': {'heu61': PROFILE},
                            'sensor_profiles_file': '/x/sensor_profiles.yaml'},
               'mapping': {}}
        self.pb = Playback(cfg, node, procs, mapping)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bag_list_shows_profile_and_unsupported_slams(self):
        b = self.pb.bags()[0]
        self.assertEqual(b['sensor']['name'], 'heu61')
        self.assertTrue(b['slams']['mapping_fastlio']['ok'])
        self.assertEqual(b['slams']['mapping_fastlio']['plan']['points'],
                         {'from': '/front_lidar', 'to': '/points_fastlio', 'via': '/playback/raw/points'})
        self.assertEqual(b['slams']['mapping_glim']['missing'], ['sensor'])
        self.assertIsNone(sensor_profile_of({'topics': [{'name': '/front_lidar', 'type': CLOUD}]}, {'heu61': PROFILE}))

    def test_slam_start_args_and_playback_through_the_adapter(self):
        self.assertEqual(self.pb.slam_start_args('mapping_fastlio', self.bag),
                         ('sensor:heu61', ['config_file:=fastlio_bag.yaml']))
        with self.assertRaises(PlaybackError) as cm:
            self.pb.slam_start_args('mapping_glim', self.bag)
        self.assertEqual(cm.exception.code, 'playback_sensor_unsupported')
        # a SLAM started with the simulation configuration is refused
        self.stacks['mapping_fastlio']._state = 'running'
        with self.assertRaises(PlaybackError) as cm:
            self.pb.start(self.bag)
        self.assertEqual(cm.exception.code, 'playback_sensor_restart')
        self.stacks['mapping_fastlio'].preset = 'sensor:heu61'
        self.pb.start(self.bag)
        (a_name, a_args), (p_name, p_args) = self.started
        self.assertEqual((a_name, p_name), ('sensor_adapter', 'playback'))
        self.assertIn('profile:=heu61', a_args)
        self.assertIn('points_in:=/playback/raw/points', a_args)
        self.assertIn('points_out:=/points_fastlio', a_args)
        self.assertIn('/front_lidar:=/playback/raw/points', p_args)
        self.assertIn('/front_lidar/imu:=/playback/raw/imu', p_args)

    def test_play_args_without_profile_unchanged(self):
        bag = {'path': '/b', 'topics': [{'name': '/points', 'type': CLOUD, 'count': 1}]}
        args, _ = play_args(bag, {'points': {'from': '/points', 'to': '/points_fastlio'}}, 1.0)
        self.assertIn('/points:=/points_fastlio', args)


from robot_console.sensor_adapter import RING2_OFFSET, Pairer, merge  # noqa: E402

FLIP_Z = [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, -0.7], [0, 0, 0, 1]]     # rear lidar -> front lidar


def pts(n, t=0.0):
    a = np.zeros(n, dtype=OUT_DTYPE)
    a['time'] = t
    return a


class DualLidarTests(unittest.TestCase):
    def test_second_lidar_goes_through_its_extrinsic(self):
        tf = Transform({**PROFILE, 'points2': '/rear_lidar', 'lidar2_to_lidar': FLIP_Z})
        stamp = 100.0
        fields, step, data = rs_cloud([(1.0, 2.0, 3.0, 5, 0.03)], stamp)
        rear = tf.points(fields, step, data, stamp, lidar=1)
        p_front = np.array([1.0, -2.0, -3.7])
        p_body = np.array(IMU_BASE)[:3, :3] @ (np.array(FLIP)[:3, :3] @ p_front + [0.01, 0.02, 0.03])
        np.testing.assert_allclose([rear['x'][0], rear['y'][0], rear['z'][0]], p_body, atol=1e-5)
        self.assertEqual(int(rear['ring'][0]), 5 + RING2_OFFSET)

    def test_merge_shifts_times_to_the_earlier_stamp(self):
        t0, m = merge([(10.05, pts(2, 0.01)), (10.0, pts(3, 0.02))])
        self.assertEqual(t0, 10.0)
        self.assertEqual(len(m), 5)
        np.testing.assert_allclose(sorted(m['time']), [0.02, 0.02, 0.02, 0.06, 0.06], atol=1e-6)

    def test_pairer_waits_for_the_partner_and_keeps_stamp_order(self):
        p = Pairer(2)
        self.assertEqual(p.add(0, 1.0, pts(1)), [])                 # waits for the rear scan
        out = p.add(1, 1.0, pts(2))
        self.assertEqual([(t, len(a)) for t, a in out], [(1.0, 3)])
        self.assertEqual(p.add(0, 1.1, pts(1)), [])                 # rear 1.1 was dropped by the bag
        out = p.add(0, 1.4, pts(1))                                 # 0.3 s later: 1.1 goes out alone
        self.assertEqual([(t, len(a)) for t, a in out], [(1.1, 1)])
        self.assertEqual(p.add(1, 1.1, pts(1)), [])                 # too late, already published
        self.assertEqual(Pairer(1).add(0, 5.0, pts(4))[0][0], 5.0)

    def test_profile_plan_adds_the_second_lidar(self):
        from robot_console.playback import profile_plan
        plan, missing = profile_plan({**PROFILE, 'points2': '/rear_lidar'}, {'points': '/points_fastlio', 'imu': '/imu'})
        self.assertEqual(missing, [])
        self.assertEqual(plan['points2'], {'from': '/rear_lidar', 'to': '/points_fastlio', 'via': '/playback/raw/points2'})
        args, _ = play_args({'path': '/b', 'topics': []}, plan, 1.0)
        self.assertIn('/rear_lidar:=/playback/raw/points2', args)


if __name__ == '__main__':
    unittest.main()
