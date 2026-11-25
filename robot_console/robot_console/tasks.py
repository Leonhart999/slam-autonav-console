"""Durable, bounded background jobs shared by projection and evaluation.

Browser cancellation only stops observing a job. On a backend restart unfinished jobs become
interrupted; retry explicitly creates a new job, preserving the previous attempt and its error.
Finished records can be removed one by one or all at once; only the newest KEEP_FINISHED are kept.
"""
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import signal
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

from fastapi import HTTPException

ACTIVE = {'queued', 'running'}
# Finished records kept on disk; equal to the list size, so every stored record is visible in the UI.
KEEP_FINISHED = 100
LOG = logging.getLogger(__name__)


def task_error(reason, status=400):
    return HTTPException(status, detail={'code': 'task_failed', 'params': {'reason': str(reason)}})


class Tasks:
    def __init__(self, root, workers=2, limit=32, keep=KEEP_FINISHED):
        self.root = Path(root).expanduser()
        self.workers, self.limit, self.keep = workers, limit, keep
        self._lock = threading.RLock()
        self._jobs, self._handlers, self._resources = {}, {}, {}
        self._queue = queue.Queue()
        self._threads = []
        self._procs = set()
        self._interrupted = set()
        self._closed = False
        if self.root.is_dir():
            for path in self.root.glob('*.json'):
                if path.is_symlink() or path.name.endswith('.result.json'):
                    continue
                try:
                    job = json.loads(path.read_text(encoding='utf-8'))
                    if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
                            or path.stem != job['id'] or len(job['id']) != 32
                            or any(c not in '0123456789abcdef' for c in job['id'])
                            or not all(isinstance(job.get(k), str) for k in ('kind', 'target', 'phase', 'fingerprint'))
                            or not isinstance(job.get('params'), dict)
                            or job.get('state') not in ACTIVE | {'failed', 'succeeded', 'interrupted'}
                            or not all(isinstance(job.get(k), (int, float)) and math.isfinite(job[k])
                                       for k in ('created', 'updated'))):
                        continue
                    if job['state'] in ACTIVE:
                        job.update(state='interrupted', phase='interrupted', finished=time.time(),
                                   error={'code': 'task_interrupted', 'params': {}})
                        self._write(job)
                    self._jobs[job['id']] = job
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            # Leftovers of an earlier crash: half-written files and results whose record is gone.
            for path in self.root.glob('*.tmp'):
                path.unlink(missing_ok=True)
            for path in self.root.glob('*.result.json'):
                if not (self.root / (path.name[:-len('.result.json')] + '.json')).exists():
                    path.unlink(missing_ok=True)
            self._prune()

    def register(self, kind, handler, resource=None):
        self._handlers[kind] = (handler, resource or kind)
        self._resources.setdefault(resource or kind, threading.Lock())

    @contextmanager
    def track_process(self, proc):
        """Register an active worker subprocess so close() can terminate it before exec/restart."""
        with self._lock:
            self._procs.add(proc)
        try:
            yield proc
        finally:
            with self._lock:
                self._procs.discard(proc)

    def _terminate_procs(self):
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            try:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        for proc in procs:
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

    def _write(self, job):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (job['id'] + '.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(tmp, path)

    def _view(self, job, result=False):
        view = {k: copy.deepcopy(v) for k, v in job.items() if k not in ('params', 'fingerprint')}
        view['input'] = copy.deepcopy(job['params'])
        view['result'] = None
        if result and job['state'] == 'succeeded':
            try:
                view['result'] = json.loads((self.root / (job['id'] + '.result.json')).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise task_error('Task result is unavailable', 410)
        return view

    def submit(self, kind, target, params, retry_of=None):
        if kind not in self._handlers:
            raise task_error('Unsupported task kind')
        params = copy.deepcopy(params)
        fingerprint = hashlib.sha256(json.dumps([kind, target, params], sort_keys=True,
                                               allow_nan=False).encode()).hexdigest()
        with self._lock:
            if self._closed:
                raise task_error('Task service is stopping', 503)
            for job in self._jobs.values():
                if job['state'] in ACTIVE and (job['fingerprint'] == fingerprint or
                        (kind == 'evaluate' and job['kind'] == kind and job['target'] == target)):
                    return self._view(job)
            if sum(j['state'] in ACTIVE for j in self._jobs.values()) >= self.limit:
                raise task_error('Task queue is full; wait for an active task to finish', 409)
            now = time.time()
            job = {'id': uuid.uuid4().hex, 'kind': kind, 'target': target, 'params': params,
                   'state': 'queued', 'phase': 'queued', 'created': now, 'updated': now,
                   'finished': None, 'error': None, 'retry_of': retry_of, 'fingerprint': fingerprint}
            self._write(job)  # acknowledge only after the recovery record is durable
            self._jobs[job['id']] = job
            if not self._threads:
                for i in range(self.workers):
                    thread = threading.Thread(target=self._worker, name=f'console-task-{i}', daemon=True)
                    self._threads.append(thread)
                    thread.start()
            self._queue.put(job['id'])
            return self._view(job)

    def _update(self, job, **fields):
        with self._lock:
            job.update(fields, updated=time.time())
            self._write(job)

    def _worker(self):
        while True:
            ident = self._queue.get()
            if ident is None:
                return
            with self._lock:
                job = self._jobs.get(ident)
                if job is None or job['state'] != 'queued':
                    continue
            try:
                handler, resource = self._handlers[job['kind']]
                with self._resources[resource]:
                    with self._lock:
                        if job['state'] != 'queued' or ident in self._interrupted:
                            self._interrupted.discard(ident)
                            continue
                        self._update(job, state='running', phase='reading')
                    result = handler(job['target'], copy.deepcopy(job['params']),
                                     lambda phase: self._update(job, phase=phase))
                    with self._lock:
                        if ident in self._interrupted:
                            self._interrupted.discard(ident)
                            self._update(job, state='interrupted', phase='interrupted', finished=time.time(),
                                         error={'code': 'task_interrupted', 'params': {}})
                            continue
                    path = self.root / (ident + '.result.json')
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                    os.replace(tmp, path)
                    self._update(job, state='succeeded', phase='done', finished=time.time())
            except Exception as exc:
                with self._lock:
                    was_interrupted = ident in self._interrupted
                    self._interrupted.discard(ident)
                try:
                    if was_interrupted:
                        self._update(job, state='interrupted', phase='interrupted', finished=time.time(),
                                     error={'code': 'task_interrupted', 'params': {}})
                    else:
                        detail = getattr(exc, 'detail', None)
                        if not isinstance(detail, dict) or not isinstance(detail.get('code'), str):
                            code = getattr(exc, 'code', 'task_failed')
                            detail = {'code': code, 'params': getattr(exc, 'params', {'reason': str(exc)})}
                        self._update(job, state='failed', phase='failed', error=detail, finished=time.time())
                except OSError:
                    # Keep the worker available even if persisting the failure also fails.
                    # The durable running record will become interrupted on the next start.
                    LOG.exception('Cannot persist task failure %s', ident)
            with self._lock:
                self._prune()

    def _discard(self, ident):
        """Forget a finished job. The record goes first, so a crash in between leaves only an orphan result."""
        for suffix in ('.json', '.result.json'):
            (self.root / (ident + suffix)).unlink(missing_ok=True)
        self._jobs.pop(ident, None)

    def _prune(self):
        """Keep the newest `keep` finished records; older ones are deleted with their results."""
        done = sorted((j for j in self._jobs.values() if j['state'] not in ACTIVE),
                      key=lambda j: j.get('finished') or j['updated'], reverse=True)
        for job in done[self.keep:]:
            try:
                self._discard(job['id'])
            except OSError:
                LOG.warning('Cannot delete old task record %s', job['id'], exc_info=True)

    def mark_saved_preview(self, token, name, kind='regrid'):
        """Keep the recovery entry useful after saving consumes the disposable preview (regrid or loop result)."""
        with self._lock:
            for job in self._jobs.values():
                if job['kind'] != kind or job['state'] != 'succeeded':
                    continue
                try:
                    if self._view(job, result=True)['result'].get('token') == token:
                        self._update(job, saved_map=name)
                        return
                except HTTPException:
                    continue

    def get(self, ident):
        with self._lock:
            job = self._jobs.get(ident)
            if job is None:
                raise task_error('Task not found', 404)
            return self._view(job, result=True)

    def list(self, kind=None, target=None):
        with self._lock:
            jobs = [j for j in self._jobs.values() if (not kind or j['kind'] == kind)
                    and (target is None or j['target'] == target)]
            return [self._view(j) for j in sorted(jobs, key=lambda j: j['created'], reverse=True)[:100]]

    def retry(self, ident):
        with self._lock:
            job = self._jobs.get(ident)
            if job is None:
                raise task_error('Task not found', 404)
            if job['state'] not in ('failed', 'interrupted'):
                raise task_error('Only failed or interrupted tasks can be retried', 409)
            return self.submit(job['kind'], job['target'], job['params'], retry_of=ident)

    def remove(self, ident):
        """Delete one finished record and its result. Queued or running jobs cannot be removed."""
        with self._lock:
            job = self._jobs.get(ident)
            if job is None:
                raise task_error('Task not found', 404)
            if job['state'] in ACTIVE:
                raise HTTPException(409, detail={'code': 'task_active', 'params': {}})
            try:
                self._discard(ident)
            except OSError as exc:
                raise task_error(f'Cannot delete the task record: {exc}', 500)

    def clear(self):
        """Delete every finished record; queued and running jobs stay. Returns (removed, failed)."""
        removed = failed = 0
        with self._lock:
            for job in [j for j in self._jobs.values() if j['state'] not in ACTIVE]:
                try:
                    self._discard(job['id'])
                    removed += 1
                except OSError:
                    failed += 1
                    LOG.warning('Cannot delete task record %s', job['id'], exc_info=True)
        return removed, failed

    def interrupt_all(self):
        """Interrupt queued and running background tasks without closing the worker pool."""
        interrupted = []
        with self._lock:
            for job in self._jobs.values():
                if job['state'] == 'queued':
                    job.update(state='interrupted', phase='interrupted', finished=time.time(),
                               error={'code': 'task_interrupted', 'params': {}})
                    try:
                        self._write(job)
                    except OSError:
                        LOG.warning('Cannot persist interrupted task %s', job['id'], exc_info=True)
                    interrupted.append(job['id'])
                elif job['state'] == 'running':
                    self._interrupted.add(job['id'])
                    interrupted.append(job['id'])
            has_procs = bool(self._procs)
        if has_procs:
            threading.Thread(target=self._terminate_procs, daemon=True).start()
        return interrupted

    def close(self, wait=False, terminate=False):
        with self._lock:
            self._closed = True
            for _ in self._threads:
                self._queue.put(None)
        if terminate:
            self._terminate_procs()
        if wait:
            for thread in self._threads:
                thread.join(timeout=5 if terminate else None)
            # An interrupted in-flight job on disk must not stay 'running' across a clean restart
            with self._lock:
                for job in self._jobs.values():
                    if job['state'] in ACTIVE:
                        job.update(state='interrupted', phase='interrupted', finished=time.time(),
                                   error={'code': 'task_interrupted', 'params': {}})
                        try:
                            self._write(job)
                        except OSError:
                            LOG.warning('Cannot persist interrupted task %s', job['id'], exc_info=True)


def tasks_for(app, cfg):
    tasks = getattr(app.state, 'console_tasks', None)
    if tasks is not None:
        return tasks
    runs = Path(cfg['mapping'].get('runs_dir', '~/runs')).expanduser()
    tasks = app.state.console_tasks = Tasks(runs / '.tasks')

    @app.get('/api/tasks')
    def task_list(kind: str | None = None, target: str | None = None):
        return {'tasks': tasks.list(kind, target)}

    @app.get('/api/tasks/{ident}')
    def task_get(ident: str):
        return tasks.get(ident)

    @app.post('/api/tasks/{ident}/retry')
    def task_retry(ident: str):
        return tasks.retry(ident)

    # Both deletions answer with the remaining list, so the task center redraws from one response.
    @app.delete('/api/tasks')
    def task_clear():
        removed, failed = tasks.clear()
        return {'removed': removed, 'failed': failed, 'tasks': tasks.list()}

    @app.delete('/api/tasks/{ident}')
    def task_remove(ident: str):
        tasks.remove(ident)
        return {'removed': 1, 'failed': 0, 'tasks': tasks.list()}

    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application) as state:
            try:
                yield state
            finally:
                tasks.close()
    app.router.lifespan_context = lifespan
    return tasks
