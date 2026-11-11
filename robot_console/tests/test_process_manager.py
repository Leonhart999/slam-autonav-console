"""process_manager.py: a console stop records duration, the signal stage it ended at and the exit code.

Pure Python, starts only `sleep` / `bash` child processes.
"""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.process_manager import ProcessManager

LOG = SimpleNamespace(warn=lambda *a: None, info=lambda *a: None)


class StopRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-procs-test-')
        stacks = {
            'sleeper': {'cmd': ['sleep', '30']},
            # bash itself exits on SIGINT only after its foreground child; the child (sleep) gets SIGINT too
            'shell': {'cmd': ['bash', '-c', 'sleep 30']},
        }
        self.procs = ProcessManager(stacks, self.tmp.name, SimpleNamespace(get_logger=lambda: LOG))

    def tearDown(self):
        for s in self.procs.status():
            if s['state'] == 'running':
                self.procs.stop(s['name'])
        self.tmp.cleanup()

    def test_sigint_stop_is_recorded(self):
        self.procs.start('sleeper')
        info = self.procs.get('sleeper').info()
        self.assertIsNone(info['last_stop'])
        time.sleep(0.2)
        info = self.procs.stop('sleeper')
        self.assertEqual(info['state'], 'exited')
        ls = info['last_stop']
        self.assertEqual(ls['stage'], 'sigint')
        self.assertEqual(ls['exit_code'], -2)
        self.assertLess(ls['duration'], 5)
        self.assertEqual(info['messages'], [])

    def test_restart_clears_the_record(self):
        self.procs.start('shell')
        time.sleep(0.2)
        self.assertEqual(self.procs.stop('shell')['last_stop']['stage'], 'sigint')
        self.procs.start('shell')
        self.assertIsNone(self.procs.get('shell').info()['last_stop'])

    def test_status_lists_last_stop_for_every_stack(self):
        self.assertTrue(all('last_stop' in s for s in self.procs.status()))

    def test_log_tail_reads_only_the_end_of_a_large_log(self):
        path = Path(self.tmp.name) / 'large.log'
        path.write_bytes(b'old log line\n' * 100000 + '末尾一\n末尾二\n未结束'.encode())
        self.procs.get('sleeper').log_path = str(path)
        reads = []
        real_open = open
        class Reader:
            def __enter__(self):
                self.file = real_open(path, 'rb')
                return self
            def __exit__(self, *args):
                self.file.close()
            def seek(self, *args):
                return self.file.seek(*args)
            def read(self, size):
                data = self.file.read(size)
                reads.append(len(data))
                return data
        with patch('robot_console.process_manager.open', return_value=Reader()):
            self.assertEqual(self.procs.log_tail('sleeper', 3), '末尾一\n末尾二\n未结束')
        self.assertLess(sum(reads), 32768)

    def test_log_tail_handles_empty_short_and_terminated_logs(self):
        path = Path(self.tmp.name) / 'small.log'
        self.procs.get('sleeper').log_path = str(path)
        for raw, expected in [(b'', ''), (b'one', 'one'), (b'one\ntwo\n', 'two\n')]:
            path.write_bytes(raw)
            self.assertEqual(self.procs.log_tail('sleeper', 1), expected)


if __name__ == '__main__':
    unittest.main()
