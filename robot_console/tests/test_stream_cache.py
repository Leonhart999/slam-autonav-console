"""One slow status sample can serve many clients, including reconnects."""
import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.stream_cache import SharedSnapshot


class SharedSnapshotTests(unittest.TestCase):
    def test_coalesces_clients_and_refreshes_after_expiry(self):
        async def scenario():
            calls = []
            now = [0.0]
            def sample():
                calls.append(1)
                return {'sample': len(calls)}
            cache = SharedSnapshot(sample, .2)
            with patch('robot_console.stream_cache.time', SimpleNamespace(monotonic=lambda: now[0])):
                results = await asyncio.gather(*(cache.get() for _ in range(10)))
                self.assertEqual(len(calls), 1)
                self.assertTrue(all(r is results[0] for r in results))
                self.assertIs(await cache.get(), results[0])
                now[0] = .3
                self.assertEqual(await cache.get(), {'sample': 2})
        asyncio.run(scenario())

    def test_disconnected_client_does_not_cancel_shared_work(self):
        async def scenario():
            entered, resume = threading.Event(), threading.Event()
            calls = []
            def sample():
                calls.append(1)
                entered.set()
                resume.wait(3)
                return {'ok': True}
            cache = SharedSnapshot(sample, 10)
            first = asyncio.create_task(cache.get())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                second = asyncio.create_task(cache.get())
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
            finally:
                resume.set()
            self.assertEqual(await second, {'ok': True})
            self.assertEqual(len(calls), 1)
        asyncio.run(scenario())

    def test_failed_sample_is_retried(self):
        async def scenario():
            calls = []
            def sample():
                calls.append(1)
                if len(calls) == 1:
                    raise OSError('sample failed')
                return 2
            cache = SharedSnapshot(sample, 10)
            with self.assertRaises(OSError):
                await cache.get()
            self.assertEqual(await cache.get(), 2)
        asyncio.run(scenario())
