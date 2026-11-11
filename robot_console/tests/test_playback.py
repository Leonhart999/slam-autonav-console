"""playback.py: finding bags, matching a SLAM's inputs to bag topics, ros2 bag play arguments, start checks.

Pure Python: bags are fake folders with a metadata.yaml, processes and the ROS node are stubs.
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.playback import Playback, PlaybackError, find_bags, match_inputs, play_args  # noqa: E402

META = """rosbag2_bagfile_information:
  version: 5
  storage_identifier: {storage}
  duration:
    nanoseconds: 120000000000
  starting_time:
    nanoseconds_since_epoch: 1793427531200016975
  message_count: {count}
  topics_with_message_count:
{topics}
  relative_file_paths:
    - x_0.db3
"""
TOPIC = """    - topic_metadata:
        name: {name}
        type: {type}
        serialization_format: cdr
        offered_qos_profiles: ""
      message_count: {count}
"""
CLOUD, IMU, ODOM, CLOCK = ('sensor_msgs/msg/PointCloud2', 'sensor_msgs/msg/Imu', 'nav_msgs/msg/Odometry',
                           'rosgraph_msgs/msg/Clock')


def make_bag(root, rel, topics, storage='sqlite3'):
    d = Path(root) / rel
    d.mkdir(parents=True)
    text = ''.join(TOPIC.format(name=n, type=t, count=c) for n, t, c in topics)
    (d / 'metadata.yaml').write_text(META.format(storage=storage, count=sum(c for *_, c in topics), topics=text))
    (d / 'x_0.db3').write_bytes(b'0' * 1000)
    return str(d)


class FindAndMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-playback-test-')
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_bags_reads_metadata_and_skips_hidden(self):
        make_bag(self.root, 'heu61/s120', [('/points', CLOUD, 1200)])
        make_bag(self.root, '.trash/old', [('/points', CLOUD, 10)])
        (Path(self.root) / 'not_a_bag').mkdir()
        bags = find_bags([self.root, '/does/not/exist'])
        self.assertEqual([b['name'] for b in bags], ['heu61/s120'])
        b = bags[0]
        self.assertAlmostEqual(b['duration'], 120.0)
        self.assertAlmostEqual(b['start'], 1793427531.2, places=3)
        self.assertEqual(b['size'], 1000)
        self.assertEqual(b['topics'], [{'name': '/points', 'type': CLOUD, 'count': 1200}])

    def test_match_inputs_same_name_remap_and_missing(self):
        topics = [{'name': '/points_fastlio', 'type': CLOUD, 'count': 10},
                  {'name': '/lidar/points', 'type': CLOUD, 'count': 20},
                  {'name': '/imu', 'type': IMU, 'count': 2000}]
        plan, missing = match_inputs(topics, {'points': '/points_fastlio', 'imu': '/imu'})
        self.assertEqual(missing, [])
        self.assertEqual(plan['points'], {'from': '/points_fastlio', 'to': '/points_fastlio'})
        # no topic with the input's name: the busiest topic of the right type is remapped
        plan, _ = match_inputs(topics, {'points': '/playback/points'})
        self.assertEqual(plan['points'], {'from': '/lidar/points', 'to': '/playback/points'})
        _, missing = match_inputs(topics[:2], {'points': '/x', 'imu': '/imu'})
        self.assertEqual(missing, ['imu'])

    def test_play_args_clock_topics_and_remaps(self):
        bag = {'path': '/b/heu', 'topics': [{'name': '/points', 'type': CLOUD, 'count': 1}]}
        plan = {'points': {'from': '/points', 'to': '/playback/points'}}
        args, own_clock = play_args(bag, plan, 2.0, '/ground_truth')
        self.assertTrue(own_clock)
        self.assertEqual(args, ['/b/heu', '--rate', '2', '--disable-keyboard-controls', '--topics', '/points',
                                '--remap', '/points:=/playback/points', '--clock', '100'])
        # a simulation bag: recorded /clock and ground truth are played, nothing is remapped
        sim = {'path': '/b/sim', 'topics': [{'name': n, 'type': t, 'count': 1} for n, t in (
            ('/points_fastlio', CLOUD), ('/imu', IMU), ('/ground_truth', ODOM), ('/clock', CLOCK))]}
        plan = {'points': {'from': '/points_fastlio', 'to': '/points_fastlio'}, 'imu': {'from': '/imu', 'to': '/imu'}}
        args, own_clock = play_args(sim, plan, 1.0, '/ground_truth')
        self.assertFalse(own_clock)
        self.assertEqual(args, ['/b/sim', '--rate', '1', '--disable-keyboard-controls', '--topics', '/imu',
                                '/points_fastlio', '/ground_truth', '/clock'])

    def test_play_args_qos_override_for_raw_dds_names(self):
        # bags from other recorders may offer avoid_ros_namespace_conventions: true -> ROS nodes never see the
        # topic; the recorded profile is passed back with the flag off, only for the played topics
        import yaml
        from robot_console.playback import raw_name_qos
        raw = {'offered_qos_profiles': "- history: 1\n  depth: 100\n  reliability: 1\n  durability: 2\n"
                                       "  avoid_ros_namespace_conventions: true"}
        q = raw_name_qos(raw)
        self.assertEqual(q, {'history': 'keep_last', 'depth': 100, 'reliability': 'reliable', 'durability': 'volatile',
                             'avoid_ros_namespace_conventions': False})
        self.assertIsNone(raw_name_qos({'offered_qos_profiles': "- history: 1\n  depth: 10"}))
        self.assertIsNone(raw_name_qos({'offered_qos_profiles': ''}))
        bag = {'path': '/b/heu', 'topics': [{'name': '/front_lidar', 'type': CLOUD, 'count': 1}],
               'qos_fix': {'/front_lidar': q, '/rear_lidar': q}}
        plan = {'points': {'from': '/front_lidar', 'to': '/points_fastlio'}}
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "qos.yaml")
            args, _ = play_args(bag, plan, 1.0, None, path)
            self.assertEqual(args[-2:], ['--qos-profile-overrides-path', path])
            with open(path, encoding='utf-8') as f:
                self.assertEqual(list(yaml.safe_load(f)), ['/front_lidar'])
        args, _ = play_args({**bag, 'qos_fix': {}}, plan, 1.0, None, '/nowhere.yaml')
        self.assertNotIn('--qos-profile-overrides-path', args)


class FakeStack:
    def __init__(self, state='idle'):
        self._state, self.started_at, self.exit_code = state, 100.0, None

    def state(self):
        return self._state


class StartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-playback-test-')
        self.bag = make_bag(self.tmp.name, 'heu', [('/points', CLOUD, 1200)])
        self.stacks = {'playback': FakeStack(), 'sim': FakeStack(), 'mapping_glim_lidar': FakeStack(),
                       'mapping_fastlio': FakeStack()}
        self.started, self.stopped = [], []

        def stop(name):                     # the player exits on SIGINT with a non-zero code
            self.stopped.append(name)
            self.stacks[name]._state, self.stacks[name].exit_code = 'exited', -2
        procs = SimpleNamespace(get=self.stacks.get, stop=stop,
                                start=lambda name, args, preset, origin: self.started.append((name, args)))
        self.subscribers = 1
        self.clock, self.odom_stamp = None, None
        node = SimpleNamespace(count_subscribers=lambda topic: self.subscribers, sim_now=lambda: self.clock)
        profiles = {'mapping_fastlio': {'inputs': {'points': '/points_fastlio', 'imu': '/imu'}, 'sources': ['sim', 'bag']},
                    'mapping_glim_lidar': {'inputs': {'points': '/playback/points'}, 'sources': ['bag']}}
        mapping = SimpleNamespace(profiles=profiles, stacks={'sim': 'sim', 'slam': 'mapping_glim_lidar'},
                                  last_stamp=lambda: self.odom_stamp)
        cfg = {'playback': {'bag_dirs': [self.tmp.name], 'rates': [0.5, 1, 2, 4, 8]}, 'mapping': {'gt_topic': '/ground_truth'}}
        self.pb = Playback(cfg, node, procs, mapping)

    def tearDown(self):
        self.tmp.cleanup()

    def code(self, *args):
        with self.assertRaises(PlaybackError) as cm:
            self.pb.start(*args)
        return cm.exception.code

    def test_needs_a_running_slam_with_the_inputs(self):
        self.assertEqual(self.code(self.bag), 'playback_need_slam')

    def test_part_of_the_bag(self):
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.assertEqual(self.code(self.bag, 1.0, 50.0, 50.5), 'playback_bad_range')   # shorter than 1 s
        self.assertEqual(self.code(self.bag, 1.0, -1.0, 30.0), 'playback_bad_range')
        self.pb.start(self.bag, 1.0, 10.0, 30.0)
        args = self.started[0][1]
        self.assertEqual(args[args.index('--start-offset') + 1], '10')
        self.assertEqual(self.pb.status()['range'], [10.0, 30.0])
        # the monitor stops the player at the end of the part; that counts as finished despite exit code -2
        self.stacks['playback']._state = 'running'
        bag_start = 1793427531.2000170
        self.clock = bag_start + 30.2
        self.pb._monitor(self.pb._cur)
        self.assertEqual(self.stopped, ['playback'])
        st = self.pb.status()
        self.assertTrue(st['finished'])
        self.assertEqual(st['position'], 30.0)
        self.assertEqual(self.pb.session_info()['bag_range'], [10.0, 30.0])

    def test_user_stop_is_not_finished(self):
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.pb.start(self.bag, 1.0)
        self.stacks['playback']._state, self.stacks['playback'].exit_code = 'exited', -2
        self.assertFalse(self.pb.status()['finished'])
        self.stacks['mapping_fastlio']._state = 'running'
        self.assertEqual(self.code(self.bag), 'bag_missing_inputs')     # the bag has no IMU

    def test_refuses_sim_unknown_bag_bad_rate_and_slam_not_listening(self):
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.stacks['sim']._state = 'running'
        self.assertEqual(self.code(self.bag), 'playback_sim_running')
        self.stacks['sim']._state = 'idle'
        self.assertEqual(self.code('/etc'), 'bag_not_found')
        self.assertEqual(self.code(self.bag, 3.0), 'playback_bad_rate')
        self.subscribers = 0
        self.assertEqual(self.code(self.bag), 'playback_slam_not_ready')

    def test_starts_once_per_slam_run(self):
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.pb.start(self.bag, 1.0)
        name, args = self.started[0]
        self.assertEqual(name, 'playback')
        self.assertIn('/points:=/playback/points', args)
        self.assertEqual(self.pb.session_info()['bag'], self.bag)
        self.stacks['playback']._state = 'exited'
        self.stacks['mapping_glim_lidar']._state = 'exited'          # saving a GLIM map stops GLIM first
        self.assertEqual(self.pb.session_info()['bag'], self.bag)
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.assertEqual(self.code(self.bag), 'playback_restart_slam')
        self.assertTrue(self.pb.status()['needs_slam_restart'])
        self.stacks['mapping_glim_lidar'].started_at = 200.0      # SLAM restarted: a new playback is fine
        self.pb.start(self.bag, 0.5)
        self.assertEqual(len(self.started), 2)

    def test_slam_lag_after_the_bag_ends(self):
        self.stacks['mapping_glim_lidar']._state = 'running'
        self.pb.start(self.bag, 2.0)
        self.stacks['playback']._state = 'running'
        self.clock, self.odom_stamp = 1793427650.0, 1793427620.0   # SLAM 30 s behind the playback clock
        self.assertEqual(self.pb.status()['slam_lag'], 30.0)
        self.assertFalse(self.pb.status()['catching_up'])           # still playing
        self.stacks['playback']._state, self.stacks['playback'].exit_code = 'exited', 0
        self.clock = None                                            # /clock stops with the player
        st = self.pb.status()
        self.assertTrue(st['finished'] and st['catching_up'])
        self.odom_stamp = 1793427649.8                               # caught up
        self.assertFalse(self.pb.status()['catching_up'])

    def test_recorded_clock_position_follows_clock(self):
        """A simulation bag plays its own /clock (unknown offset): the position follows /clock after the first
        sample, so a player slower than the requested rate is not shown ahead."""
        sim = make_bag(self.tmp.name, 'sim', [('/points_fastlio', CLOUD, 100), ('/imu', IMU, 2000),
                                              ('/clock', CLOCK, 100000)])
        self.stacks['mapping_fastlio']._state = 'running'
        self.pb.start(sim, 8.0)
        self.stacks['playback']._state = 'running'
        self.clock = 3.4                                   # first /clock: anchored to the wall estimate (~0 s)
        self.assertLess(self.pb.status()['position'], 0.5)
        self.pb._cur['wall0'] -= 2.0                       # 2 s of wall time: estimate 16 s at 8x
        self.clock = 3.4 + 12.0                            # but the player only got 12 s further
        self.assertAlmostEqual(self.pb.status()['position'], 12.0, delta=0.5)


if __name__ == '__main__':
    unittest.main()
