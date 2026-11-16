"""Durable background jobs: deduplication, bounded work, recovery and HTTP projection/evaluation."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uvicorn

from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.tasks import Tasks, tasks_for
from robot_console.evaluation import register_evaluation_routes
from robot_console.map_regrid import register_regrid_routes


def finished(tasks, ident):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = tasks.get(ident)
        if job['state'] not in ('queued', 'running'):
            return job
        time.sleep(0.005)
    raise AssertionError('task did not finish')


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = Tasks(self.root / 'tasks')

    def tearDown(self):
        self.tasks.close(wait=True)
        self.tmp.cleanup()

    def test_result_and_input_survive_a_new_service_instance(self):
        def work(target, params, progress):
            progress('writing')
            return {'target': target, 'value': params['value']}
        self.tasks.register('evaluate', work)
        job = self.tasks.submit('evaluate', 'run-1', {'value': 12})
        result = finished(self.tasks, job['id'])
        recovered = Tasks(self.root / 'tasks')
        self.assertEqual(recovered.get(job['id']), result)
        self.assertEqual(result['input'], {'value': 12})
        self.assertEqual(result['result'], {'target': 'run-1', 'value': 12})
        self.assertEqual(recovered.list()[0]['result'], None)

    def test_concurrent_identical_submissions_share_one_job(self):
        release = threading.Event()
        calls = []
        def work(target, params, progress):
            calls.append(target)
            release.wait(5)
            return {'ok': True}
        self.tasks.register('regrid', work)
        try:
            with ThreadPoolExecutor(8) as pool:
                jobs = list(pool.map(lambda _: self.tasks.submit('regrid', 'map-a', {'res': .05}), range(8)))
            self.assertEqual(len({job['id'] for job in jobs}), 1)
        finally:
            release.set()
        self.assertEqual(finished(self.tasks, jobs[0]['id'])['state'], 'succeeded')
        self.assertEqual(calls, ['map-a'])

    def test_queue_limit_and_shared_resource_serialization(self):
        tasks = Tasks(self.root / 'bounded', workers=2, limit=2)
        release, entered = threading.Event(), threading.Event()
        calls = []
        def work(target, params, progress):
            calls.append(target)
            entered.set()
            release.wait(5)
            return {'target': target}
        tasks.register('evaluate', work, resource='evo')
        tasks.register('eval_export', work, resource='evo')
        try:
            first = tasks.submit('evaluate', 'a', {})
            self.assertTrue(entered.wait(2))
            second = tasks.submit('eval_export', 'b', {})
            self.assertEqual(tasks.get(second['id'])['state'], 'queued')
            with self.assertRaises(HTTPException) as caught:
                tasks.submit('evaluate', 'c', {})
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(calls, ['a'])
        finally:
            release.set()
            tasks.close(wait=True)
        self.assertEqual(finished(tasks, first['id'])['state'], 'succeeded')
        self.assertEqual(finished(tasks, second['id'])['state'], 'succeeded')

    def test_failure_is_visible_and_retry_preserves_old_attempt(self):
        def work(target, params, progress):
            raise HTTPException(422, detail={'code': 'evaluation_failed', 'params': {'reason': 'bad trajectory'}})
        self.tasks.register('evaluate', work)
        first = self.tasks.submit('evaluate', 'bad-run', {})
        old = finished(self.tasks, first['id'])
        self.assertEqual(old['error']['params']['reason'], 'bad trajectory')
        self.tasks.register('evaluate', lambda *args: {'recovered': True})
        new = self.tasks.retry(first['id'])
        self.assertNotEqual(first['id'], new['id'])
        self.assertEqual(new['retry_of'], first['id'])
        self.assertEqual(finished(self.tasks, new['id'])['result'], {'recovered': True})
        self.assertEqual(self.tasks.get(first['id'])['state'], 'failed')

    def test_restart_marks_unfinished_work_interrupted_without_automatically_rerunning(self):
        folder = self.root / 'restart'
        folder.mkdir()
        ident = 'a' * 32
        record = {'id': ident, 'kind': 'evaluate', 'target': 'run-1', 'params': {'force': True},
                  'fingerprint': 'old', 'created': 1, 'updated': 1, 'finished': None,
                  'state': 'running', 'phase': 'evaluating', 'error': None, 'retry_of': None}
        (folder / f'{ident}.json').write_text(json.dumps(record))
        restarted = Tasks(folder)
        restarted.register('evaluate', lambda *args: {'ok': True})
        try:
            old = restarted.get(ident)
            self.assertEqual(old['state'], 'interrupted')
            self.assertEqual(old['error']['code'], 'task_interrupted')
            self.assertEqual(restarted._threads, [])
            new = restarted.retry(ident)
            self.assertEqual(finished(restarted, new['id'])['state'], 'succeeded')
        finally:
            restarted.close(wait=True)

    def test_missing_results_and_unknown_jobs_are_explicit_errors(self):
        self.tasks.register('evaluate', lambda *args: {'ok': True})
        job = self.tasks.submit('evaluate', 'a', {})
        finished(self.tasks, job['id'])
        (self.tasks.root / (job['id'] + '.result.json')).unlink()
        with self.assertRaises(HTTPException) as caught:
            self.tasks.get(job['id'])
        self.assertEqual(caught.exception.status_code, 410)
        with self.assertRaises(HTTPException):
            self.tasks.get('../outside')

    def test_succeeded_jobs_cannot_be_retried(self):
        self.tasks.register('evaluate', lambda *args: {'ok': True})
        job = self.tasks.submit('evaluate', 'a', {})
        finished(self.tasks, job['id'])
        with self.assertRaises(HTTPException) as caught:
            self.tasks.retry(job['id'])
        self.assertEqual(caught.exception.status_code, 409)

    def test_saved_projection_destination_survives_restart(self):
        self.tasks.register('regrid', lambda *args: {'token': 'b' * 32})
        job = self.tasks.submit('regrid', 'map-a', {})
        finished(self.tasks, job['id'])
        self.tasks.mark_saved_preview('b' * 32, 'map-new')
        recovered = Tasks(self.tasks.root)
        self.assertEqual(recovered.get(job['id'])['saved_map'], 'map-new')

    def test_corrupt_records_are_skipped_without_breaking_future_submissions(self):
        folder = self.root / 'corrupt'
        folder.mkdir()
        (folder / ('a' * 32 + '.json')).write_text(json.dumps({'id': 'a' * 32, 'state': 'queued'}))
        (folder / ('b' * 32 + '.json')).write_text('{broken')
        recovered = Tasks(folder)
        try:
            self.assertEqual(recovered.list(), [])
            recovered.register('evaluate', lambda *args: {'ok': True})
            job = recovered.submit('evaluate', 'run', {})
            self.assertEqual(finished(recovered, job['id'])['state'], 'succeeded')
        finally:
            recovered.close(wait=True)

    def test_worker_survives_failure_to_persist_an_error(self):
        self.tasks.register('evaluate', lambda *args: {'ok': True})
        original = self.tasks._write
        failures = []
        def write(job):
            if job['state'] in ('running', 'failed') and job['target'] == 'bad':
                failures.append(job['state'])
                raise OSError('disk unavailable')
            original(job)
        with patch.object(self.tasks, '_write', side_effect=write):
            bad = self.tasks.submit('evaluate', 'bad', {})
            self.assertEqual(finished(self.tasks, bad['id'])['state'], 'failed')
            good = self.tasks.submit('evaluate', 'good', {})
            self.assertEqual(finished(self.tasks, good['id'])['state'], 'succeeded')
        self.assertEqual(failures, ['running', 'failed'])


class TaskHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = {'mapping': {'runs_dir': str(root / 'runs')}, 'server': {'venv': '/unused'}}
        self.app = FastAPI()
        self.tasks = tasks_for(self.app, self.cfg)
        self.server = None

    def request(self, method, path, body=None):
        if self.server is None:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            self.base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
            self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error'))
            self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
            self.thread.start()
            for _ in range(100):
                if self.server.started:
                    break
                time.sleep(.01)
            self.assertTrue(self.server.started)
        data = json.dumps(body).encode() if body is not None else None
        try:
            response = urlopen(Request(self.base + path, data=data, method=method,
                                      headers={'Content-Type': 'application/json'}), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return self._response(response)

    @staticmethod
    def _response(response):
        raw = response.read().decode()
        return SimpleNamespace(status_code=response.status, text=raw, json=lambda: json.loads(raw))

    def tearDown(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive())
        self.tasks.close(wait=True)
        self.tmp.cleanup()

    def test_evaluation_acknowledges_without_waiting_and_is_findable_after_refresh(self):
        run = Path(self.cfg['mapping']['runs_dir']) / 'run-1'
        run.mkdir(parents=True)
        (run / 'run.json').write_text('{}')
        evaluation = register_evaluation_routes(self.app, self.cfg, None)
        release, entered = threading.Event(), threading.Event()
        def evaluate(run_id, force=False, progress=lambda phase: None):
            progress('evaluating')
            entered.set()
            release.wait(5)
            return {'id': run_id, 'force': force}
        with patch.object(evaluation, 'evaluate', side_effect=evaluate):
            try:
                response = self.request('POST', '/api/evaluation/runs/run-1/evaluate?background=true&force=true')
                self.assertEqual(response.status_code, 200, response.text)
                ident = response.json()['id']
                self.assertTrue(entered.wait(2))
                listed = self.request('GET', '/api/tasks?kind=evaluate&target=run-1').json()['tasks']
                self.assertEqual(listed[0]['id'], ident)
                self.assertEqual(listed[0]['phase'], 'evaluating')
                # A page reload does not start another computation, even when force differs.
                duplicate = self.request('POST', '/api/evaluation/runs/run-1/evaluate?background=true').json()
                self.assertEqual(duplicate['id'], ident)
            finally:
                release.set()
            self.assertEqual(finished(self.tasks, ident)['result'], {'id': 'run-1', 'force': True})

    def test_projection_uses_same_registry_and_rejects_bad_parameters_before_submission(self):
        maps = Path(self.tmp.name) / 'maps'
        maps.mkdir()
        service = register_regrid_routes(self.app, SimpleNamespace(root=maps, _locate=lambda name: ('folder', maps)), self.cfg)
        with patch.object(service, 'preview', return_value={'token': 'b' * 32, 'parent': 'map-a'}) as worker:
            bad = self.request('POST', '/api/maps/map-a/regrid?background=true', {'res': -1})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(self.tasks.list(), [])
            response = self.request('POST', '/api/maps/map-a/regrid?background=true', {'res': .1})
            self.assertEqual(response.status_code, 200, response.text)
            ident = response.json()['id']
            self.assertEqual(finished(self.tasks, ident)['result']['parent'], 'map-a')
            self.assertEqual(self.request('GET', '/api/tasks').json()['tasks'][0]['kind'], 'regrid')
            self.assertEqual(worker.call_count, 1)
