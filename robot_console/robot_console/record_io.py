"""Ordered disk I/O for run recording. ROS callbacks only enqueue immutable data.

Pending trajectory text is limited to 32 MiB. A stalled disk never blocks ROS: rejected
samples are reported in the log and run.json, so an incomplete run is identifiable.
Lifecycle operations stay ordered with the samples and are drained on shutdown.
"""
import copy
import json
import os
import queue
import shutil
import threading
from concurrent.futures import Future


def replace_est_files(directory, lines, fields):
    if not os.path.isdir(directory):
        return False
    est = os.path.join(directory, 'est.tum')
    online = os.path.join(directory, 'est_online.tum')
    if os.path.exists(est) and not os.path.exists(online):
        os.replace(est, online)
    with open(est + '.tmp', 'w') as f:
        f.writelines(lines)
    os.replace(est + '.tmp', est)
    path = os.path.join(directory, 'run.json')
    with open(path, encoding='utf-8') as f:
        info = json.load(f)
    info.update(fields)
    info.setdefault('files', {})['est_online'] = 'est_online.tum (online odometry)'
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    os.replace(path + '.tmp', path)
    return True


class RecordIO:
    def __init__(self, log, max_bytes=32 * 1024 * 1024):
        self.log = log
        self.max_bytes = max_bytes
        self._pending_bytes = 0
        self._pending_samples = 0
        self._lock = threading.Lock()
        self.errors = {}
        self._queue = queue.SimpleQueue()
        self._files = set()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='run-writer', daemon=True)
        self._thread.start()

    def _error(self, directory, reason):
        with self._lock:
            first = directory not in self.errors
            self.errors[directory] = reason
        if first:
            self.log.warn(f'运行记录不完整 {directory}：{reason}')

    def submit(self, directory, fn, *args, size=0, recording=True):
        future = Future()
        with self._lock:
            if self._closed:
                future.set_exception(OSError('run writer is closed'))
                return future
            overflow = size and (self._pending_bytes + size > self.max_bytes or
                                 self._pending_samples >= 8192)
            if not overflow:
                self._pending_bytes += size
                self._pending_samples += bool(size)
                self._queue.put((directory, fn, args, size, recording, future))
        if overflow:
            reason = 'disk writer backlog exceeded its memory limit; trajectory samples were dropped'
            self._error(directory, reason)
            future.set_exception(OSError(reason))
        return future

    def _run(self):
        while True:
            job = self._queue.get()
            if job is None:
                break
            directory, fn, args, size, recording, future = job
            try:
                future.set_result(fn(*args))
            except Exception as exc:
                if recording:
                    self._error(directory, str(exc))
                future.set_exception(exc)
            finally:
                with self._lock:
                    self._pending_bytes -= size
                    self._pending_samples -= bool(size)
        for f in list(self._files):
            try:
                f.close()
            except OSError as exc:
                self._error(os.path.dirname(f.name), str(exc))
        self._files.clear()

    def mkdir(self, directory):
        self.submit(directory, os.makedirs, directory)

    def open(self, path):
        return QueuedFile(self, path)

    def metadata(self, directory, info):
        self.submit(directory, self._metadata, directory, copy.deepcopy(info))

    def _metadata(self, directory, info):
        with self._lock:
            error = self.errors.get(directory)
        if error:
            info['recording_error'] = error
        path = os.path.join(directory, 'run.json')
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)

    def _flush(self):
        for f in list(self._files):
            try:
                f.flush()
            except OSError as exc:
                self._error(os.path.dirname(f.name), str(exc))
                raise

    def flush(self):
        self.submit('', self._flush, recording=False).result()

    def export(self, directory, run_dir):
        def copy_run():
            self._flush()
            with self._lock:
                error = self.errors.get(run_dir)
            if error:
                raise OSError(error)
            os.makedirs(directory, exist_ok=True)
            # gt.tum / ref.tum / lag.csv exist only for some runs (a playback without ground truth has no gt.tum)
            for name in ('est.tum', 'gt.tum', 'ref.tum', 'lag.csv', 'run.json'):
                src = os.path.join(run_dir, name)
                if name in ('est.tum', 'run.json') or os.path.exists(src):
                    shutil.copy2(src, os.path.join(directory, name))
        return self.submit(run_dir, copy_run, recording=False)

    def shutdown(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        self._thread.join()


class QueuedFile:
    def __init__(self, writer, path):
        self.writer = writer
        self.directory = os.path.dirname(path)
        self.file = None
        writer.submit(self.directory, self._open, path)

    def _open(self, path):
        self.file = open(path, 'w', encoding='utf-8')
        self.writer._files.add(self.file)

    def _write(self, text):
        if self.file is None:
            raise OSError('trajectory file could not be opened')
        self.file.write(text)

    def write(self, text):
        self.writer.submit(self.directory, self._write, text, size=len(text))

    def writelines(self, lines):
        self.write(''.join(lines))

    def flush(self):
        self.writer.submit(self.directory, self._flush)

    def _flush(self):
        if self.file is not None:
            self.file.flush()

    def close(self):
        self.writer.submit(self.directory, self._close)

    def _close(self):
        if self.file is not None:
            try:
                self.file.close()
            finally:
                self.writer._files.discard(self.file)
                self.file = None
