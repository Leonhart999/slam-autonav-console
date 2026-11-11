"""Coalesce identical WebSocket snapshots; all callers use the same asyncio loop."""
import asyncio
import time


class SharedSnapshot:
    def __init__(self, factory, period):
        self.factory = factory
        self.period = period
        self._task = None
        self._expires = 0.0

    async def get(self):
        now = time.monotonic()
        if self._task is None or (self._task.done() and now >= self._expires):
            self._task = asyncio.create_task(asyncio.to_thread(self.factory))
            self._expires = now + self.period
            # A disconnected client can stop waiting while the shared sample finishes.
            self._task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        task = self._task
        try:
            return await asyncio.shield(task)
        except Exception:
            if self._task is task:
                self._task = None
            raise
