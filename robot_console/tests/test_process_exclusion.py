"""Mutual exclusion is enforced at the process boundary, for web and direct/batch starts alike."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.process_manager import ProcessManager, StackRunning


class ProcessExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.procs = ProcessManager({n: {'cmd': ['sleep', '30']} for n in ('fastlio', 'glim', 'sim')},
                                    self.tmp.name, SimpleNamespace())
        self.procs.set_exclusive(['fastlio', 'glim', 'unknown'])

    def tearDown(self):
        self.procs.stop_all()
        self.tmp.cleanup()

    def test_simultaneous_starts_have_exactly_one_winner(self):
        barrier = threading.Barrier(2)
        def start(name):
            barrier.wait(timeout=5)
            try:
                return ('started', self.procs.start(name)['name'])
            except StackRunning as exc:
                return ('blocked', exc.name)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(start, ['fastlio', 'glim']))
        winner = next(name for state, name in results if state == 'started')
        self.assertEqual(sorted(state for state, name in results), ['blocked', 'started'])
        self.assertEqual(next(name for state, name in results if state == 'blocked'), winner)
        self.assertEqual(sum(s['state'] == 'running' for s in self.procs.status()), 1)

    def test_conflict_is_checked_before_start_hooks(self):
        self.procs.start('fastlio')
        called = []
        self.procs.on_start('glim', lambda *a: called.append(True))
        with self.assertRaises(StackRunning) as caught:
            self.procs.start('glim')
        self.assertEqual(caught.exception.name, 'fastlio')
        self.assertEqual(called, [])

    def test_other_slam_waits_until_stop_hook_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def stopped(stack):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError('test did not release stop hook')
        self.procs.on_stopped('fastlio', stopped)
        self.procs.start('fastlio')
        with ThreadPoolExecutor(1) as pool:
            stopping = pool.submit(self.procs.stop, 'fastlio')
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(self.procs.get('fastlio').state(), 'stopping')
                with self.assertRaises(StackRunning):
                    self.procs.start('glim')
            finally:
                release.set()
                stopping.result(timeout=5)
        self.assertEqual(self.procs.start('glim')['state'], 'running')

    def test_unrelated_stack_can_run_together(self):
        self.procs.start('fastlio')
        self.assertEqual(self.procs.start('sim')['state'], 'running')

    def test_failed_spawn_does_not_occupy_the_group(self):
        with patch('robot_console.process_manager.subprocess.Popen', side_effect=OSError('spawn failed')):
            with self.assertRaises(OSError):
                self.procs.start('fastlio')
        self.assertEqual(self.procs.start('glim')['state'], 'running')
