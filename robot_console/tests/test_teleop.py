"""Teleop commands must not overtake estop or disconnect stops."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.teleop import Teleop


class TeleopTests(unittest.TestCase):
    def make(self, busy=lambda: False, publish=None):
        self.sent = []
        def record(msg):
            self.sent.append((msg.linear.x, msg.angular.z))
            if publish:
                publish(msg)
        node = SimpleNamespace(create_publisher=lambda *a: SimpleNamespace(publish=record),
                               create_timer=lambda *a: None)
        return Teleop(node, {}, busy)

    def test_estop_rejects_command_already_in_busy_check(self):
        entered, resume = threading.Event(), threading.Event()
        def busy():
            entered.set()
            resume.wait(3)
            return False
        tele = self.make(busy)
        result = []
        command = threading.Thread(target=lambda: result.append(tele.command(.5, 0, 1)))
        command.start()
        try:
            self.assertTrue(entered.wait(2))
            tele.lock()
        finally:
            resume.set()
            command.join(3)
        self.assertEqual(result, ['teleop_locked'])
        self.assertEqual(self.sent, [(0.0, 0.0)])
        self.assertFalse(tele._active)

    def test_lock_serializes_with_publication_and_finishes_at_zero(self):
        publishing, resume, stopped = threading.Event(), threading.Event(), threading.Event()
        def publish(msg):
            if msg.linear.x:
                publishing.set()
                resume.wait(3)
        tele = self.make(publish=publish)
        command = threading.Thread(target=lambda: tele.command(.5, 0, 1))
        stop = threading.Thread(target=lambda: (tele.lock(), stopped.set()))
        command.start()
        try:
            self.assertTrue(publishing.wait(2))
            stop.start()
            self.assertFalse(stopped.wait(.05))
        finally:
            resume.set()
            command.join(3)
            if stop.ident:
                stop.join(3)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.sent, [(.5, 0.0), (0.0, 0.0)])

    def test_unlock_does_not_revive_a_command_from_before_estop(self):
        entered, resume = threading.Event(), threading.Event()
        def busy():
            entered.set()
            resume.wait(3)
            return False
        tele = self.make(busy)
        result = []
        command = threading.Thread(target=lambda: result.append(tele.command(.5, 0, 1)))
        command.start()
        try:
            self.assertTrue(entered.wait(2))
            tele.lock()
            tele.unlock()
        finally:
            resume.set()
            command.join(3)
        self.assertEqual(result, ['teleop_locked'])
        self.assertEqual(self.sent, [(0.0, 0.0)])

    def test_disconnect_only_stops_the_owner(self):
        tele = self.make()
        tele.command(.4, 0, 1)
        tele.release(2)
        self.assertEqual(self.sent, [(.4, 0.0)])
        tele.release(1)
        self.assertEqual(self.sent[-1], (0.0, 0.0))

    def test_unlock_and_watchdog_still_work(self):
        tele = self.make()
        tele.lock()
        self.assertEqual(tele.command(.4, 0, 1), 'teleop_locked')
        tele.unlock()
        self.assertIsNone(tele.command(.4, 0, 1))
        tele._last = 0
        tele._watchdog()
        self.assertEqual(self.sent[-1], (0.0, 0.0))
        self.assertFalse(tele._active)
