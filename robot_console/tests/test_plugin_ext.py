"""Plugin interface additions: python.requirements in manifests, per-plugin venvs (PythonEnvs), background jobs
(ctx.jobs) with their routes, ctx.run_python, and multipart / raw bodies through ctx.api. HTTP calls go through
plugins.asgi_request (the venv has no HTTP test client)."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request  # noqa: E402

from robot_console import plugins  # noqa: E402


def call(app, method, path, body=None):
    return asyncio.run(plugins.asgi_request(app, method, path, body))


WORKER = '''
import json, os, sys, time
job = sys.argv[1]
with open(os.path.join(job, 'input.json')) as f:
    inp = json.load(f)
p = inp['params']
print(json.dumps({'progress': 0.5, 'phase': 'half'}), flush=True)
print('a plain log line', flush=True)
if p.get('sleep'):
    time.sleep(p['sleep'])
if p.get('fail') == 'coded':
    with open('error.json', 'w') as f:
        json.dump({'code': 'plugin.jobs.bad_input', 'params': {'why': 'test'}}, f)
    sys.exit(1)
if p.get('fail') == 'crash':
    raise RuntimeError('worker exploded')
with open('out.txt', 'w') as f:
    f.write('made by the worker')
with open('result.json', 'w') as f:
    json.dump({'echo': p, 'cwd_is_job': os.path.realpath(os.getcwd()) == os.path.realpath(job)}, f)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-plugin-ext-test-')
        self.base = Path(self.tmp.name)
        self.dir = self.base / 'plugins'
        self.cfg = {'paths': {'data_root': str(self.base)},
                    'plugins': {'dirs': [str(self.dir)], 'state_file': str(self.base / 'state.json'),
                                'data_dir': str(self.base / 'plugin_data'), 'envs_dir': str(self.base / 'envs'),
                                # pip must not reach any index in the tests
                                'pip': {'index_url': 'http://127.0.0.1:9/simple', 'timeout': 2, 'retries': 0}}}
        self.env = mock.patch.dict(os.environ, {plugins.NO_PLUGINS_ENV: ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for name in [m for m in sys.modules if m.startswith(plugins.MODULE_PREFIX + '.')]:
            del sys.modules[name]
        self.tmp.cleanup()

    def make(self, pid, extra='', backend=None, files=None):
        d = self.dir / pid
        d.mkdir(parents=True)
        manifest = f'id: {pid}\napi: 1\nversion: 0.1.0\nname: Plugin {pid}\n' + textwrap.dedent(extra)
        if backend is not None:
            manifest += 'backend: {module: backend}\n'
            (d / 'backend.py').write_text(textwrap.dedent(backend), encoding='utf-8')
        for rel, text in (files or {}).items():
            (d / rel).write_text(text, encoding='utf-8')
        (d / 'plugin.yaml').write_text(manifest, encoding='utf-8')
        return d

    def start(self, enable=()):
        manager = plugins.PluginManager(self.cfg)
        for pid in enable:
            manager.set_enabled(pid, True)
        manager = plugins.PluginManager(self.cfg)
        app = FastAPI()

        @app.post('/api/echo-form')
        async def echo_form(request: Request):
            form = await request.form()
            out = {}
            for k, v in form.multi_items():
                out[k] = (v.filename, (await v.read()).decode()) if hasattr(v, 'filename') else v
            return out

        @app.post('/api/echo-raw')
        async def echo_raw(request: Request):
            return {'type': request.headers.get('content-type'), 'size': len(await request.body())}

        plugins.register_plugin_routes(app, manager)
        return manager, app

    def info(self, manager, pid):
        return next(p for p in manager.list()['plugins'] if p['id'] == pid)


class ManifestPythonTests(Base):
    def test_requirements_are_checked(self):
        good = ['rosbags==0.11.9', 'numpy>=1.23,<3', 'pypatchworkpp', 'foo[bar,baz]~=1.2']
        self.make('ok', extra='python:\n  requirements: ' + json.dumps(good) + '\n')
        for i, bad in enumerate(['--index-url=http://x', 'pkg @ https://x/y.whl', './local.whl', 'a; os_name=="nt"',
                                 '', 'pkg==1.0 --hash=sha256:00']):
            self.make(f'bad{i}', extra='python:\n  requirements: ' + json.dumps([bad]) + '\n')
        self.make('empty', extra='python: {requirements: []}\n')
        manager, _ = self.start()
        self.assertEqual(self.info(manager, 'ok')['python']['requirements'], good)
        self.assertEqual(self.info(manager, 'ok')['python']['state'], 'missing')
        for pid in [f'bad{i}' for i in range(6)] + ['empty']:
            p = self.info(manager, pid)
            self.assertEqual(p['state'], 'invalid', pid)
            self.assertEqual(p['issue'], {'code': 'plugin_manifest_field', 'params': {'field': 'python.requirements'}})

    def test_plugin_without_requirements(self):
        self.make('plain')
        manager, app = self.start()
        self.assertIsNone(self.info(manager, 'plain')['python'])
        self.assertEqual(call(app, 'POST', '/api/plugins/plain/python/install')[1]['detail']['code'],
                         'plugin_python_none')


class PythonEnvTests(Base):
    def test_install_status_outdated_and_remove(self):
        # 'pip' is already in a fresh venv: pip installs nothing and needs no index
        self.make('envy', extra="python: {requirements: ['pip']}\n")
        manager, app = self.start()
        env = mock.patch.dict(os.environ, {'PYTHONPATH': str(Path(plugins.__file__).resolve().parents[1])})
        env.start()
        self.addCleanup(env.stop)
        status, body = call(app, 'POST', '/api/plugins/envy/python/install')
        self.assertEqual(status, 200)
        self.assertIn(body['python']['state'], ('installing', 'ready'))
        self.assertEqual(call(app, 'POST', '/api/plugins/envy/python/install')[0], 409)   # one install at a time
        manager.envs.wait(240)
        p = self.info(manager, 'envy')['python']
        self.assertEqual(p['state'], 'ready', call(app, 'GET', '/api/plugins/envy/python/log')[1])
        self.assertTrue(any(x.startswith('pip==') for x in p['packages']))
        # only what the venv has: nothing from the console's PYTHONPATH (ROS) and no console packages
        self.assertLess(len(p['packages']), 5, p['packages'])
        cfg = (Path(manager.envs.env_dir('envy')) / 'pyvenv.cfg').read_text()
        self.assertNotIn('venvs/console', cfg)
        self.assertTrue(os.path.isfile(manager.envs.python_of('envy')))
        self.assertIn('Installed.', call(app, 'GET', '/api/plugins/envy/python/log')[1]['text'])
        # the manifest asks for something else now: the env is outdated and not used
        (self.dir / 'envy' / 'plugin.yaml').write_text(
            "id: envy\napi: 1\nversion: 0.2.0\nname: E\npython: {requirements: ['pip', 'setuptools']}\n")
        manager.scan()
        self.assertEqual(self.info(manager, 'envy')['python']['state'], 'outdated')
        status, body = call(app, 'DELETE', '/api/plugins/envy/python')
        self.assertEqual((status, body['python']['state']), (200, 'missing'))
        self.assertFalse(os.path.exists(manager.envs.env_dir('envy')))

    def test_failed_install_leaves_no_env(self):
        self.make('nope', extra="python: {requirements: ['surely-not-a-real-package-xyz==9.9']}\n")
        manager, app = self.start()
        call(app, 'POST', '/api/plugins/nope/python/install')
        manager.envs.wait(240)
        p = self.info(manager, 'nope')['python']
        self.assertEqual(p['state'], 'failed')
        self.assertIn('pip exited with code', p['error'])
        self.assertIn('surely-not-a-real-package-xyz', p['error'])
        self.assertFalse(os.path.exists(manager.envs.env_dir('nope')))
        self.assertIn('surely-not-a-real-package-xyz', call(app, 'GET', '/api/plugins/nope/python/log')[1]['text'])

    def test_ctx_python_needs_a_ready_env(self):
        self.make('needs', extra="python: {requirements: ['pip']}\n", backend='''
            def setup(ctx):
                @ctx.router.get('/py')
                def py():
                    return {'python': ctx.python, 'state': ctx.python_status()['state']}

                @ctx.router.post('/run')
                async def run():
                    return await ctx.run_python(['-c', 'print(1)'])
            ''')
        manager, app = self.start(enable=['needs'])
        self.assertEqual(call(app, 'GET', '/api/ext/needs/py')[1], {'python': None, 'state': 'missing'})
        self.assertEqual(call(app, 'POST', '/api/ext/needs/run')[1]['detail']['code'], 'plugin_python_missing')


class JobTests(Base):
    BACKEND = '''
        def setup(ctx):
            @ctx.router.post('/start')
            def start(body: dict):
                return ctx.jobs.submit('echo', body, title='Echo', target='t1')

            @ctx.router.post('/py')
            async def py():
                return await ctx.run_python(['-c', 'import sys; print(sys.stdin.read().upper())'], input_data='hi')
        '''

    def wait(self, app, jid, states=('succeeded', 'failed', 'cancelled'), timeout=30, pid='jobs'):
        end = time.time() + timeout
        while time.time() < end:
            status, job = call(app, 'GET', f'/api/ext/{pid}/jobs/{jid}')
            self.assertEqual(status, 200, job)
            if job['state'] in states:
                return job
            time.sleep(0.05)
        self.fail(f'job {jid} still {job["state"]}')

    def test_job_runs_reports_progress_result_files_and_log(self):
        self.make('jobs', backend=self.BACKEND, files={'worker.py': WORKER})
        manager, app = self.start(enable=['jobs'])
        status, job = call(app, 'POST', '/api/ext/jobs/start', {'x': 1})
        self.assertEqual((status, job['state'], job['kind'], job['target']), (200, 'queued', 'echo', 't1'))
        done = self.wait(app, job['id'])
        self.assertEqual(done['state'], 'succeeded')
        self.assertEqual(done['result'], {'echo': {'x': 1}, 'cwd_is_job': True})
        self.assertEqual((done['progress'], done['phase']), (1.0, 'half'))
        self.assertEqual(call(app, 'GET', f"/api/ext/jobs/jobs/{job['id']}/files/out.txt"),
                         (200, 'made by the worker'))
        self.assertEqual(call(app, 'GET', f"/api/ext/jobs/jobs/{job['id']}/files/../job.json")[0], 404)
        self.assertIn('a plain log line', call(app, 'GET', f"/api/ext/jobs/jobs/{job['id']}/log")[1]['text'])
        listed = call(app, 'GET', '/api/ext/jobs/jobs')[1]
        self.assertIsNone(listed['python'])
        self.assertEqual([j['id'] for j in listed['jobs']], [job['id']])
        self.assertEqual(listed['jobs'][0]['result'], done['result'])       # small results come with the list
        self.assertEqual(call(app, 'POST', '/api/ext/jobs/py')[1], [0, 'HI\n', ''])
        manager.shutdown()

    def test_failures_cancel_remove_and_restart(self):
        self.make('jobs', backend=self.BACKEND, files={'worker.py': WORKER})
        manager, app = self.start(enable=['jobs'])
        coded = call(app, 'POST', '/api/ext/jobs/start', {'fail': 'coded'})[1]
        crash = call(app, 'POST', '/api/ext/jobs/start', {'fail': 'crash'})[1]
        self.assertEqual(self.wait(app, coded['id'])['error'],
                         {'code': 'plugin.jobs.bad_input', 'params': {'why': 'test'}})
        err = self.wait(app, crash['id'])['error']
        self.assertEqual(err['code'], 'plugin_job_failed')
        self.assertIn('worker exploded', err['params']['reason'])
        slow = call(app, 'POST', '/api/ext/jobs/start', {'sleep': 30})[1]
        queued = call(app, 'POST', '/api/ext/jobs/start', {'x': 2})[1]
        self.wait(app, slow['id'], states=('running',))
        self.assertEqual(call(app, 'DELETE', f"/api/ext/jobs/jobs/{slow['id']}")[0], 409)   # still running
        self.assertEqual(call(app, 'POST', f"/api/ext/jobs/jobs/{queued['id']}/cancel")[1]['state'], 'cancelled')
        t0 = time.time()
        call(app, 'POST', f"/api/ext/jobs/jobs/{slow['id']}/cancel")
        self.assertEqual(self.wait(app, slow['id'])['state'], 'cancelled')
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(call(app, 'DELETE', f"/api/ext/jobs/jobs/{coded['id']}"), (200, {'ok': True}))
        self.assertEqual(call(app, 'GET', f"/api/ext/jobs/jobs/{coded['id']}")[0], 404)
        # a job that was running when the console stopped is interrupted after the next start
        hang = call(app, 'POST', '/api/ext/jobs/start', {'sleep': 30})[1]
        self.wait(app, hang['id'], states=('running',))
        manager.shutdown()
        job_file = self.base / 'plugin_data' / 'jobs' / 'jobs' / hang['id'] / 'job.json'
        record = json.loads(job_file.read_text())
        record['state'] = 'running'                 # as if the process had died without cleaning up
        job_file.write_text(json.dumps(record))
        manager, app = self.start()
        status, job = call(app, 'GET', f"/api/ext/jobs/jobs/{hang['id']}")
        self.assertEqual((job['state'], job['error']['code']), ('interrupted', 'plugin_job_interrupted'))
        manager.shutdown()

    def test_jobs_need_the_python_env(self):
        self.make('envjob', extra="python: {requirements: ['pip']}\n", backend='''
            def setup(ctx):
                @ctx.router.post('/start')
                def start():
                    return ctx.jobs.submit('x', {})
            ''', files={'worker.py': WORKER})
        manager, app = self.start(enable=['envjob'])
        self.assertEqual(call(app, 'POST', '/api/ext/envjob/start')[1]['detail']['code'], 'plugin_python_missing')
        self.assertEqual(call(app, 'GET', '/api/ext/envjob/jobs')[1]['python']['state'], 'missing')

    def test_cancel_while_spawning_kills_process(self):
        self.make('race', backend=self.BACKEND, files={'worker.py': WORKER})
        manager, app = self.start(enable=['race'])
        jobs = manager.records['race'].context.jobs
        real_popen = plugins.subprocess.Popen
        entered = []

        def slow_popen(*args, **kwargs):
            # Cancel arrives right before Popen returns and sets self.proc
            if not entered:
                entered.append(True)
                jid = next(iter(jobs.jobs))
                jobs.cancel(jid)
            return real_popen(*args, **kwargs)

        with mock.patch.object(plugins.subprocess, 'Popen', side_effect=slow_popen):
            job = call(app, 'POST', '/api/ext/race/start', {'sleep': 30})[1]
            done = self.wait(app, job['id'], timeout=10, pid='race')
        self.assertEqual(done['state'], 'cancelled')
        manager.shutdown()

    def test_small_result_cached_and_large_result_omitted_from_list(self):
        big_worker = '''
import json, os, sys
job = sys.argv[1]
with open(os.path.join(job, 'input.json')) as f:
    big = json.load(f)['params'].get('big')
with open(os.path.join(job, 'result.json'), 'w') as f:
    json.dump({'data': 'x' * (70000 if big else 10)}, f)
'''
        self.make('cachejob', backend=self.BACKEND, files={'worker.py': big_worker})
        manager, app = self.start(enable=['cachejob'])
        small = self.wait(app, call(app, 'POST', '/api/ext/cachejob/start', {'big': False})[1]['id'], pid='cachejob')
        big = self.wait(app, call(app, 'POST', '/api/ext/cachejob/start', {'big': True})[1]['id'], pid='cachejob')
        self.assertIn('data', small['result'])
        self.assertIn('data', big['result'])
        listed = {j['id']: j for j in call(app, 'GET', '/api/ext/cachejob/jobs')[1]['jobs']}
        self.assertEqual(listed[small['id']]['result'], {'data': 'x' * 10})
        self.assertNotIn('result', listed[big['id']])
        # Second list() uses the in-memory cache cleanly
        listed2 = {j['id']: j for j in call(app, 'GET', '/api/ext/cachejob/jobs')[1]['jobs']}
        self.assertEqual(listed2[small['id']]['result'], {'data': 'x' * 10})
        self.assertNotIn('result', listed2[big['id']])
        manager.shutdown()


class ApiBodyTests(Base):
    def test_multipart_and_raw_bodies(self):
        self.make('poster', backend='''
            def setup(ctx):
                @ctx.router.post('/go')
                async def go():
                    form = await ctx.api.post('/api/echo-form', data={'name': 'v2'},
                                              files={'grid': ('grid.bin', b'abc')})
                    raw = await ctx.api.post('/api/echo-raw', content=b'12345', content_type='application/x-test')
                    return {'form': form, 'raw': raw}
            ''')
        manager, app = self.start(enable=['poster'])
        self.assertEqual(call(app, 'POST', '/api/ext/poster/go'), (200, {
            'form': {'name': 'v2', 'grid': ['grid.bin', 'abc']},
            'raw': {'type': 'application/x-test', 'size': 5}}))


if __name__ == '__main__':
    unittest.main()
