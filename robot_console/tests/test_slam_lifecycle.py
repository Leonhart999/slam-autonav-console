"""A finished SLAM run stays exportable but cannot absorb later simulation/playback data."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from nav_msgs.msg import Odometry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.mapping import MappingData


def odom(t, x=0.0):
    msg = Odometry()
    msg.header.stamp.sec = t
    msg.pose.pose.position.x = float(x)
    msg.pose.pose.orientation.w = 1.0
    return msg


class SlamLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        node = SimpleNamespace(get_logger=lambda: SimpleNamespace(warn=lambda *a: None),
                               create_subscription=lambda *a, **kw: None,
                               create_timer=lambda *a: None)
        self.data = MappingData(node, {'runs_dir': self.tmp.name})
        self.running = True  # actual child-process liveness, including a still-running shutdown
        self.data.is_running = lambda name: self.running

    def tearDown(self):
        self.data.close()
        self.tmp.cleanup()

    def begin(self, playback=False, stationary=False):
        if playback:
            self.data.run_info = lambda: {'type': 'playback', 'gt_source': 'bag', 'ref_source': 'wheel'}
        self.data._on_odom(odom(1))
        self.data._on_gt(odom(1))
        self.data._on_odom(odom(2, 0 if stationary else 1))
        self.data._on_gt(odom(2, 0 if stationary else 1))
        self.data._on_ref(odom(2, 1))
        self.data.record_lag(3)
        return self.data.recorder.run_id

    def files(self, rid):
        self.data.recorder.flush()
        return {p.name: p.read_bytes() for p in (Path(self.tmp.name) / rid).iterdir()}

    def test_stop_callback_closes_run_before_accepting_any_late_data(self):
        # Each callback can be the first event after a normal stop or an unexpected exit.
        callbacks = [lambda: self.data._on_gt(odom(3, 3)),
                     lambda: self.data._on_odom(odom(3, 3)),
                     lambda: self.data._on_cloud(object()),
                     lambda: self.data._on_ref(odom(3, 3)),
                     lambda: self.data.record_lag(4)]
        for callback in callbacks:
            with self.subTest(callback=callback):
                self.data.reset()
                self.running = True
                rid = self.begin(playback=True)
                before = self.files(rid)
                self.running = False
                callback()
                after = self.files(rid)
                self.assertIsNone(self.data.recorder.run_id)
                self.assertEqual(self.data.status()['run'], rid)
                self.assertIsNotNone(json.loads(after['run.json'])['ended'])
                for name in ('est.tum', 'gt.tum', 'ref.tum', 'lag.csv'):
                    self.assertEqual(before[name], after[name])
                for late in callbacks:
                    late()
                self.assertEqual(after, self.files(rid))

    def test_timer_finishes_without_incoming_messages_and_is_idempotent(self):
        rid = self.begin()
        self.running = False
        self.data.finish_if_stopped()
        closed = self.files(rid)
        self.data.finish_if_stopped()
        self.assertEqual(closed, self.files(rid))
        self.assertTrue(json.loads(closed['run.json'])['ended'])

    def test_inflight_cloud_cannot_change_a_stopped_map(self):
        rid = self.begin()
        self.running = False
        with patch('robot_console.mapping.cloud_xyz', return_value=np.array([[1., 2., 3.]])):
            self.data._process(object(), self.data.epoch, self.data._keys)
        self.assertEqual(self.data.status()['voxels'], 0)
        self.assertIsNotNone(json.loads(self.files(rid)['run.json'])['ended'])

    def test_final_glim_trajectory_survives_finish_export_and_map_registration(self):
        rid = self.begin()
        online = self.files(rid)['est.tum']
        final = '1.000000000 9 0 0 0 0 0 1\n'
        # A final trajectory may arrive before the lifecycle timer observes process exit.
        self.data.finish_if_stopped()
        self.assertEqual(self.data.recorder.run_id, rid)
        self.assertTrue(self.data.replace_estimate(rid, [final], 'glim_dump'))
        self.running = False
        self.data.finish_if_stopped()
        self.data.add_map_to_run('final-map')
        target = Path(self.tmp.name) / 'export'
        self.assertEqual(self.data.export_run(str(target)), rid)
        saved = self.files(rid)
        info = json.loads(saved['run.json'])
        self.assertEqual(saved['est_online.tum'], online)
        self.assertEqual(saved['est.tum'].decode(), final)
        self.assertEqual(info['est_source'], 'glim_dump')
        self.assertEqual(info['maps'], ['final-map'])
        self.assertTrue(info['ended'])
        self.assertEqual((target / 'est.tum').read_text(), final)
        self.assertEqual(json.loads((target / 'run.json').read_text()), info)

    def test_final_trajectory_can_arrive_after_the_run_has_already_closed(self):
        rid = self.begin()
        self.running = False
        self.data._on_gt(odom(3, 3))
        closed = self.files(rid)
        final = '1.000000000 8 0 0 0 0 0 1\n'
        self.assertTrue(self.data.replace_estimate(rid, [final], 'glim_dump'))
        self.data.add_map_to_run('late-final-map')
        target = Path(self.tmp.name) / 'export'
        self.assertEqual(self.data.export_run(str(target)), rid)
        saved = self.files(rid)
        info = json.loads(saved['run.json'])
        self.assertEqual(info['ended'], json.loads(closed['run.json'])['ended'])
        self.assertEqual(info['est_source'], 'glim_dump')
        self.assertEqual(info['maps'], ['late-final-map'])
        self.assertEqual(saved['gt.tum'], closed['gt.tum'])
        self.assertEqual((target / 'est.tum').read_text(), final)
        self.assertEqual(json.loads((target / 'run.json').read_text()), info)

    def test_export_immediately_after_exit_closes_without_waiting_for_timer(self):
        rid = self.begin()
        self.running = False
        target = Path(self.tmp.name) / 'export'
        self.assertEqual(self.data.export_run(str(target)), rid)
        self.assertTrue(json.loads((target / 'run.json').read_text())['ended'])

    def test_stationary_save_commits_before_stop_and_remains_exportable(self):
        self.assertIsNone(self.begin(stationary=True))
        rid = self.data.keep_run()
        self.assertIsNotNone(rid)
        self.running = False
        self.data.finish_if_stopped()
        self.assertEqual(self.data.export_run(str(Path(self.tmp.name) / 'export')), rid)

    def test_unsaved_stationary_run_is_discarded_at_stop(self):
        self.begin(stationary=True)
        self.running = False
        self.data.finish_if_stopped()
        self.assertFalse(self.data.recorder.pending)
        self.assertIsNone(self.data.status()['run'])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_new_run_does_not_reopen_or_append_to_previous_run(self):
        old = self.begin()
        self.running = False
        self.data.finish_if_stopped()
        closed = self.files(old)
        epoch = self.data.epoch
        self.running = True
        self.data._on_gt(odom(10, 10))  # before the new SLAM's first odometry
        self.assertEqual(closed, self.files(old))
        new = self.begin()
        self.assertNotEqual(new, old)
        self.assertGreater(self.data.epoch, epoch)
        self.assertEqual(closed, self.files(old))
        self.assertEqual(self.data.status()['length'], 1.0)
