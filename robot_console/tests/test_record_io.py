"""Slow disks must not block ROS; epochs, export and GLIM replacements stay ordered."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.runs import RunRecorder
from test_runs import Log, pose


class BackgroundRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = Log()
        self.rec = RunRecorder(str(self.root), self.log, background=True)

    def tearDown(self):
        self.rec.end()
        self.rec.shutdown()
        self.tmp.cleanup()

    def start(self):
        self.rec.begin({'frame': 'camera_init'})
        self.rec.est(0, pose(0))
        self.rec.gt(0, pose(0))
        self.rec.est(1, pose(1))
        return self.rec.run_id

    def test_stalled_disk_does_not_block_pose_recording_and_shutdown_drains(self):
        entered, resume, recorded = threading.Event(), threading.Event(), threading.Event()
        def stall():
            entered.set()
            resume.wait(3)
        self.rec._io.submit('', stall)
        self.assertTrue(entered.wait(2))
        def record():
            self.start()
            for i in range(2, 102):
                self.rec.est(i, pose(i))
            self.rec.end()
            recorded.set()
        producer = threading.Thread(target=record)
        producer.start()
        try:
            self.assertTrue(recorded.wait(1), 'recording waited for the stalled disk')
        finally:
            resume.set()
            producer.join(3)
        self.rec.shutdown()
        folders = list(self.root.glob('map_*'))
        self.assertEqual(len(folders), 1)
        self.assertEqual(len((folders[0] / 'est.tum').read_text().splitlines()), 102)
        self.assertIsNotNone(json.loads((folders[0] / 'run.json').read_text())['ended'])

    def test_export_captures_only_preceding_samples(self):
        run = self.start()
        self.rec.update(force=True, length=1)
        export = self.rec.export(str(self.root / 'snapshot'))
        self.rec.est(2, pose(2))
        export.result(timeout=3)
        self.rec.end()
        self.rec.flush()
        self.assertEqual(len((self.root / 'snapshot' / 'est.tum').read_text().splitlines()), 2)
        self.assertEqual(len((self.root / run / 'est.tum').read_text().splitlines()), 3)

    def test_reset_and_glim_replacement_do_not_mix_runs(self):
        first = self.start()
        self.rec.end()
        second = self.start()
        self.assertNotEqual(first, second)
        changed = self.rec.replace_est(second, ['5 8 0 0 0 0 0 1\n'], 'GLIM')
        self.assertTrue(changed.result(timeout=3))
        self.rec.est(3, pose(3))
        self.rec.end()
        self.rec.flush()
        self.assertEqual((self.root / second / 'est.tum').read_text(), '5 8 0 0 0 0 0 1\n')
        self.assertEqual(len((self.root / second / 'est_online.tum').read_text().splitlines()), 2)
        self.assertEqual(len((self.root / first / 'est.tum').read_text().splitlines()), 2)
        self.assertEqual(json.loads((self.root / second / 'run.json').read_text())['est_source'], 'GLIM')

    def test_backlog_limit_marks_incomplete_recording_and_refuses_export(self):
        self.rec._io.max_bytes = 1
        run = self.start()
        self.rec.update(force=True)
        export = self.rec.export(str(self.root / 'snapshot'))
        with self.assertRaises(OSError):
            export.result(timeout=3)
        self.rec.end()
        self.rec.flush()
        self.assertIn('recording_error', json.loads((self.root / run / 'run.json').read_text()))
        self.assertTrue(self.log.warnings)
