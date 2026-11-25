"""Backend tests for console restart endpoint, process locking, and restart command helper."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import config, main as console_main
from robot_console.process_manager import ProcessManager, StackClosing
from robot_console.server import create_app
from robot_console.tasks import tasks_for


class _FakeNode:
    def __init__(self):
        self.estop_calls = 0

    def snapshot(self):
        return {'clock': None, 'topics': [], 'tf': {'expected': [], 'other': []}, 'nodes': []}

    def create_subscription(self, *args, **kwargs):
        return None

    def create_timer(self, *args, **kwargs):
        return None

    def estop(self):
        self.estop_calls += 1
        return ['/navigate_to_pose']

    def get_logger(self):
        return SimpleNamespace(warn=lambda *a: None, info=lambda *a: None)


class _FakeMapping:
    def __init__(self, runs_dir):
        self.stacks = {'sim': 'sim', 'slam': 'fastlio', 'waypoints': 'waypoints'}
        self.profiles = {'fastlio': {}}
        self.runs_dir = runs_dir

    def status(self):
        return {'state': 'idle', 'run': None}

    def select(self, _name):
        pass

    def reset(self):
        pass


class _FakeTeleop:
    def __init__(self):
        self.locked = False

    def lock(self):
        self.locked = True

    def unlock(self):
        self.locked = False

    def status(self):
        return {'locked': self.locked}


class _FakeLibrary:
    def __init__(self):
        self.waited = []

    def wait_idle(self, timeout=25):
        self.waited.append(timeout)

    def status(self):
        return {'state': 'idle'}


class _FakeNavData:
    def set_speed_limit(self, _v):
        pass

    def speed_limit_listeners(self):
        return 0

    def cancel_goal(self):
        return False


class _FakePlayback:
    sim_stacks = set()
    stack = 'playback'

    def __init__(self, bag_dir):
        self.roots = [str(bag_dir)]

    def set_profiles(self, profiles, files, user):
        self.profiles = profiles

    def player_active(self):
        return False

    def status(self):
        return {'state': 'idle'}


class ConsoleRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-restart-test-')
        root = Path(self.tmp.name)
        (root / 'nav2_params.yaml').write_text('controller_server: {}\n', encoding='utf-8')
        (root / 'console.yaml').write_text(f"""
            paths: {{data_root: {root / 'data'}}}
            server: {{host: 127.0.0.1, port: 8000, venv: {sys.prefix}, web_dist: {root / 'missing'}, log_dir: {root / 'logs'}}}
            mapping: {{presets_file: presets.yaml}}
            navigation: {{params_file: {root / 'nav2_params.yaml'}}}
        """, encoding='utf-8')
        (root / 'stacks.yaml').write_text("""
            sim: {cmd: [sleep, '30']}
            fastlio: {cmd: [sleep, '30']}
            waypoints: {cmd: [sleep, '30'], stop_on_estop: true}
            navigation: {cmd: [sleep, '30']}
        """, encoding='utf-8')
        (root / 'presets.yaml').write_text("office: {label: Office}\n", encoding='utf-8')
        self.cfg = config.load(str(root / 'console.yaml'))
        self.node = _FakeNode()
        self.procs = ProcessManager(self.cfg['stacks'], self.cfg['server']['log_dir'], self.node)
        self.teleop = _FakeTeleop()
        self.library = _FakeLibrary()
        self.playback = _FakePlayback(Path(self.cfg['paths']['data_root']) / 'bags')
        self.restart_called = threading.Event()
        with patch('robot_console.server.get_package_share_directory', return_value=str(root)), \
                patch('robot_console.server.write_console_params', side_effect=OSError('skip')):
            self.app = create_app(
                self.cfg, self.node, self.procs, _FakeMapping(self.cfg['mapping']['runs_dir']),
                self.teleop, self.library,
                SimpleNamespace(solids=None, list=lambda: []),
                _FakeNavData(), self.playback,
                on_restart=self.restart_called.set, boot_id='boot1234',
            )
        self.routes = {(r.path, tuple(sorted(getattr(r, 'methods', set())))): r.endpoint
                       for r in self.app.routes if hasattr(r, 'endpoint')}

    def tearDown(self):
        self.procs.stop_all()
        self.tmp.cleanup()

    def test_restart_command_helper_handles_module_and_entrypoint(self):
        self.assertEqual(console_main._restart_command(['/opt/ros/console', '--config', '/a.yaml'], '/usr/bin/python3'),
                         ['/usr/bin/python3', '/opt/ros/console', '--config', '/a.yaml'])
        self.assertEqual(
            console_main._restart_command(['/repo/robot_console/main.py', '--config', '/a.yaml'], '/usr/bin/python3'),
            ['/usr/bin/python3', '-m', 'robot_console.main', '--config', '/a.yaml'],
        )
        self.assertEqual(console_main._restart_command([], '/usr/bin/python3'), ['/usr/bin/python3', '-m', 'robot_console.main'])

    def test_restart_endpoint_stops_work_locks_procs_and_triggers_callback(self):
        self.procs.start('sim')
        self.procs.start('fastlio')
        restart = self.routes[('/api/restart', ('POST',))]
        start_stack = self.routes[('/api/stacks/{name}/start', ('POST',))]
        status_fn = self.routes[('/api/status', ('GET',))]

        res = restart()
        self.assertTrue(res['restarting'])
        self.assertEqual(res['boot_id'], 'boot1234')
        self.assertEqual(res['cancelled_actions'], ['/navigate_to_pose'])
        self.assertEqual(set(res['stopped_stacks']), {'sim', 'fastlio'})
        self.assertTrue(self.teleop.locked)
        self.assertEqual(self.node.estop_calls, 1)
        self.assertTrue(self.restart_called.wait(timeout=2.0))

        st = status_fn()
        self.assertEqual(st['boot_id'], 'boot1234')
        self.assertTrue(st['restarting'])

        # Duplicate restarts and new stack starts must be refused while restarting.
        with self.assertRaises(HTTPException) as ctx:
            restart()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail['code'], 'console_restarting')

        with self.assertRaises(HTTPException) as ctx2:
            start_stack('navigation')
        self.assertEqual(ctx2.exception.status_code, 409)
        self.assertEqual(ctx2.exception.detail['code'], 'console_restarting')

        # prepare_shutdown waits for library.wait_idle and leaves all stacks stopped when followed by stop_all.
        self.app.state.prepare_shutdown('restart')
        self.assertEqual(self.library.waited, [25])
        self.procs.stop_all()
        self.assertEqual([s['name'] for s in self.procs.status() if s['state'] == 'running'], [])

    def test_restart_returns_501_without_handler(self):
        root = Path(self.tmp.name)
        with patch('robot_console.server.get_package_share_directory', return_value=str(root)), \
                patch('robot_console.server.write_console_params', side_effect=OSError('skip')):
            app = create_app(
                self.cfg, self.node, self.procs, _FakeMapping(self.cfg['mapping']['runs_dir']),
                self.teleop, self.library,
                SimpleNamespace(solids=None, list=lambda: []),
                _FakeNavData(), self.playback,
            )
        restart = next(r.endpoint for r in app.routes if getattr(r, 'path', None) == '/api/restart')
        with self.assertRaises(HTTPException) as ctx:
            restart()
        self.assertEqual(ctx.exception.status_code, 501)
        self.assertEqual(ctx.exception.detail['code'], 'restart_unsupported')

    def test_process_manager_close_rejects_concurrent_start(self):
        self.procs.close()
        with self.assertRaises(StackClosing):
            self.procs.start('sim')

    def test_restart_is_atomic_under_concurrent_requests(self):
        restart = self.routes[('/api/restart', ('POST',))]
        barrier = threading.Barrier(2)

        def call_restart():
            barrier.wait(timeout=5)
            try:
                return ('ok', restart()['restarting'])
            except HTTPException as exc:
                return ('err', exc.detail['code'])

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: call_restart(), range(2)))
        self.assertEqual(sorted(results), [('err', 'console_restarting'), ('ok', True)])

    def test_interrupt_endpoint_stops_all_stacks_tasks_and_keeps_console_open(self):
        self.procs.start('sim')
        self.procs.start('fastlio')
        started = threading.Event()
        tasks = tasks_for(self.app, self.cfg)

        def slow_task(_target, _params, _progress):
            proc = subprocess.Popen(['sleep', '30'], start_new_session=True)
            with tasks.track_process(proc):
                started.set()
                rc = proc.wait(timeout=10)
            if rc != 0:
                raise RuntimeError(f'exited with {rc}')
            return {'ok': True}

        tasks.register('slow_test', slow_task)
        job = tasks.submit('slow_test', 'map_a', {'v': 1})
        self.assertTrue(started.wait(timeout=3.0))

        plugin_cancels = []
        fake_plugins = SimpleNamespace(cancel_jobs=lambda: plugin_cancels.append('called') or ['pjob1'])
        root = Path(self.tmp.name)
        interrupt = self.routes[('/api/interrupt', ('POST',))]
        start_stack = self.routes[('/api/stacks/{name}/start', ('POST',))]

        res = interrupt()
        self.assertEqual(res['cancelled_actions'], ['/navigate_to_pose'])
        self.assertEqual(set(res['stopped_stacks']), {'sim', 'fastlio'})
        self.assertEqual(res['interrupted_tasks'], [job['id']])
        self.assertEqual(res['cancelled_plugin_jobs'], [])
        self.assertTrue(self.teleop.locked)
        self.assertEqual(self.node.estop_calls, 1)
        self.assertFalse(self.restart_called.is_set())

        # Wait for all stopped stacks and interrupted task to finish stopping.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            states = {s['name']: s['state'] for s in self.procs.status()}
            task_state = tasks.get(job['id'])['state']
            if states['sim'] not in ('running', 'stopping') and states['fastlio'] not in ('running', 'stopping') and task_state == 'interrupted':
                break
            time.sleep(0.05)
        self.assertEqual(tasks.get(job['id'])['state'], 'interrupted')
        self.assertEqual([s['name'] for s in self.procs.status() if s['state'] in ('running', 'stopping')], [])

        # Console stays open after interrupt: new stacks can still be started.
        info = start_stack('navigation')
        self.assertEqual(info['state'], 'running')
        self.procs.stop_all()

        # Also verify plugin job cancellation is invoked when plugins are attached.
        with patch('robot_console.server.get_package_share_directory', return_value=str(root)), \
                patch('robot_console.server.write_console_params', side_effect=OSError('skip')), \
                patch('robot_console.server.register_plugin_routes'):
            app2 = create_app(
                self.cfg, self.node, self.procs, _FakeMapping(self.cfg['mapping']['runs_dir']),
                self.teleop, self.library,
                SimpleNamespace(solids=None, list=lambda: []),
                _FakeNavData(), self.playback,
                plugins=fake_plugins, on_restart=self.restart_called.set, boot_id='boot5678',
            )
        interrupt2 = next(r.endpoint for r in app2.routes if getattr(r, 'path', None) == '/api/interrupt')
        res2 = interrupt2()
        self.assertEqual(plugin_cancels, ['called'])
        self.assertEqual(res2['cancelled_plugin_jobs'], ['pjob1'])

        # Interrupt is rejected while a restart is already in progress.
        restart2 = next(r.endpoint for r in app2.routes if getattr(r, 'path', None) == '/api/restart')
        restart2()
        with self.assertRaises(HTTPException) as ctx:
            interrupt2()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail['code'], 'console_restarting')
        tasks.close(wait=True, terminate=True)


if __name__ == '__main__':
    unittest.main()
