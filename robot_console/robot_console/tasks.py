"""Durable, bounded background jobs shared by projection and evaluation.

Browser cancellation only stops observing a job. On a backend restart unfinished jobs become
interrupted; retry explicitly creates a new job, preserving the previous attempt and its error.
"""
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException

ACTIVE = {'queued', 'running'}
LOG = logging.getLogger(__name__)


def task_error(reason, status=400):
    return HTTPException(status, detail={'code': 'task_failed', 'params': {'reason': str(reason)}})


class Tasks:
    def __init__(self, root, workers=2, limit=32):
        self.root = Path(root).expanduser()
        self.workers, self.limit = workers, limit
        self._lock = threading.RLock()
        self._jobs, self._handlers, self._resources = {}, {}, {}
        self._queue = queue.Queue()
        self._threads = []
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

    def register(self, kind, handler, resource=None):
        self._handlers[kind] = (handler, resource or kind)
        self._resources.setdefault(resource or kind, threading.Lock())

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
            job = self._jobs[ident]
            try:
                handler, resource = self._handlers[job['kind']]
                with self._resources[resource]:
                    self._update(job, state='running', phase='reading')
                    result = handler(job['target'], copy.deepcopy(job['params']),
                                     lambda phase: self._update(job, phase=phase))
                    path = self.root / (ident + '.result.json')
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                    os.replace(tmp, path)
                    self._update(job, state='succeeded', phase='done', finished=time.time())
            except Exception as exc:
                detail = getattr(exc, 'detail', None)
                if not isinstance(detail, dict) or not isinstance(detail.get('code'), str):
                    code = getattr(exc, 'code', 'task_failed')
                    detail = {'code': code, 'params': getattr(exc, 'params', {'reason': str(exc)})}
                try:
                    self._update(job, state='failed', phase='failed', error=detail, finished=time.time())
                except OSError:
                    # Keep the worker available even if persisting the failure also fails.
                    # The durable running record will become interrupted on the next start.
                    LOG.exception('Cannot persist task failure %s', ident)

    def mark_saved_preview(self, token, name):
        """Keep the recovery entry useful after saving consumes the disposable preview."""
        with self._lock:
            for job in self._jobs.values():
                if job['kind'] != 'regrid' or job['state'] != 'succeeded':
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

    def close(self, wait=False):
        with self._lock:
            self._closed = True
            for _ in self._threads:
                self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join()


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
