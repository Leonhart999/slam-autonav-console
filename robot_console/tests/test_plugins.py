"""plugins.py: discovery without running code, disabled by default, loading/isolation, settings and secrets,
plugin routes and assets, and the in-process console API. HTTP calls go through plugins.asgi_request
(the venv has no HTTP test client)."""
import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI  # noqa: E402

from robot_console import plugins  # noqa: E402

UI = {'ui/plugin.js': 'window.loaded = true\n', 'ui/plugin.css': '.x {}\n'}


def call(app, method, path, body=None):
    return asyncio.run(plugins.asgi_request(app, method, path, body))


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-plugins-test-')
        self.base = Path(self.tmp.name)
        self.dir = self.base / 'plugins'
        self.cfg = {'paths': {'data_root': str(self.base)},
                    'plugins': {'dirs': [str(self.dir)], 'state_file': str(self.base / 'state.json'),
                                'data_dir': str(self.base / 'plugin_data')}}
        self.env = mock.patch.dict(os.environ, {plugins.NO_PLUGINS_ENV: ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for name in [m for m in sys.modules if m.startswith(plugins.MODULE_PREFIX + '.')]:
            del sys.modules[name]
        self.tmp.cleanup()

    def make(self, pid, extra='', backend=None, files=None, folder=None, base=None):
        d = (base or self.dir) / (folder or pid)
        d.mkdir(parents=True)
        manifest = f'id: {pid}\napi: 1\nversion: 0.1.0\nname: {{zh: 插件 {pid}, en: Plugin {pid}}}\n' + textwrap.dedent(extra)
        if backend is not None:
            manifest += 'backend: {module: backend}\n'
            (d / 'backend.py').write_text(textwrap.dedent(backend), encoding='utf-8')
        for rel, text in (files or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text, encoding='utf-8')
        (d / 'plugin.yaml').write_text(manifest, encoding='utf-8')
        return d

    def start(self):
        """A console start: a new manager and app, plugin routes registered (loads enabled plugins)."""
        manager = plugins.PluginManager(self.cfg)
        app = FastAPI()

        @app.get('/api/core')
        def core():
            return {'core': True}

        plugins.register_plugin_routes(app, manager)
        return manager, app

    def info(self, manager, pid):
        return next(p for p in manager.list()['plugins'] if p['id'] == pid)

    # ---------------- discovery ----------------

    def test_scan_runs_no_code_and_new_plugins_start_disabled(self):
        marker = self.base / 'imported'
        self.make('hello', backend=f'open({str(marker)!r}, "w").close()\ndef setup(ctx):\n    pass\n')
        manager, app = self.start()
        p = self.info(manager, 'hello')
        self.assertEqual((p['state'], p['enabled'], p['restart_needed']), ('disabled', False, False))
        self.assertFalse(marker.exists())
        self.assertEqual(call(app, 'GET', '/api/ext/hello/x')[0], 404)

    def test_enabled_plugin_loads_at_next_start_with_routes_assets_and_api(self):
        self.make('hello', backend='''
            def setup(ctx):
                @ctx.router.get('/ping')
                async def ping():
                    core = await ctx.api.get('/api/core')
                    return {'id': ctx.id, 'core': core['core'], 'greeting': ctx.settings()['greeting']}

                @ctx.router.get('/fail')
                def fail():
                    raise ctx.error(409, 'nope', x=1)
            ''', extra='''
            frontend: {dir: ui, script: plugin.js, style: plugin.css}
            settings:
              - {key: greeting, type: string, label: Greeting, default: hi}
            ''', files=UI)
        manager, _ = self.start()
        p = manager.set_enabled('hello', True)
        self.assertTrue(p['restart_needed'])                     # takes effect at the next start
        manager, app = self.start()
        p = self.info(manager, 'hello')
        self.assertEqual((p['state'], p['restart_needed']), ('active', False))
        self.assertEqual(p['frontend'], {'script': '/ext/hello/plugin.js', 'style': '/ext/hello/plugin.css'})
        self.assertEqual(call(app, 'GET', '/api/ext/hello/ping'), (200, {'id': 'hello', 'core': True, 'greeting': 'hi'}))
        self.assertEqual(call(app, 'GET', '/api/ext/hello/fail'),
                         (409, {'detail': {'code': 'plugin.hello.nope', 'params': {'x': 1}}}))
        status, body = call(app, 'GET', '/ext/hello/plugin.js')
        self.assertEqual((status, body), (200, 'window.loaded = true\n'))
        for bad in ('/ext/hello/../plugin.yaml', '/ext/hello/%2e%2e/plugin.yaml', '/ext/hello/missing.js',
                    '/ext/other/plugin.js'):
            self.assertEqual(call(app, 'GET', bad)[0], 404, bad)
        # disabling: still running until the restart
        p = manager.set_enabled('hello', False)
        self.assertEqual((p['state'], p['restart_needed']), ('active', True))

    def test_a_failing_plugin_is_reported_and_does_not_stop_others(self):
        self.make('broken', backend='def setup(ctx):\n    @ctx.router.get("/x")\n    def x():\n        return 1\n    raise RuntimeError("boom")\n')
        self.make('nosetup', backend='VALUE = 1\n')
        self.make('badimport', backend='import not_a_module_xyz\n')
        self.make('exits', backend='import sys\nsys.exit(3)\n')
        self.make('good', backend='def setup(ctx):\n    @ctx.router.get("/x")\n    def x():\n        return 1\n')
        manager, _ = self.start()
        for pid in ('broken', 'nosetup', 'badimport', 'exits', 'good'):
            manager.set_enabled(pid, True)
        manager, app = self.start()
        self.assertEqual(self.info(manager, 'broken')['state'], 'failed')
        self.assertIn('RuntimeError: boom', self.info(manager, 'broken')['issue']['params']['reason'])
        self.assertFalse(self.info(manager, 'broken')['restart_needed'])   # failed at this start: nothing pending
        self.assertEqual(self.info(manager, 'nosetup')['issue']['code'], 'plugin_no_setup')
        self.assertIn('ModuleNotFoundError', self.info(manager, 'badimport')['issue']['params']['reason'])
        self.assertIn('SystemExit', self.info(manager, 'exits')['issue']['params']['reason'])
        self.assertEqual(call(app, 'GET', '/api/ext/broken/x')[0], 404)    # routes of a failed setup are not mounted
        self.assertEqual(call(app, 'GET', '/api/ext/good/x'), (200, 1))
        # a disabled failed plugin is shown as disabled
        manager.set_enabled('broken', False)
        self.assertEqual((self.info(manager, 'broken')['state'], self.info(manager, 'broken')['issue']), ('disabled', None))

    def test_invalid_manifests_are_listed_with_their_problem(self):
        (self.dir / 'empty').mkdir(parents=True)
        self.make('mismatch', folder='other_name')
        self.make('future', extra='', folder='future')
        (self.dir / 'future' / 'plugin.yaml').write_text(
            (self.dir / 'future' / 'plugin.yaml').read_text().replace('api: 1', 'api: 99'))
        self.make('secretdef', extra='settings:\n  - {key: k, type: secret, label: K, default: abc}\n')
        self.make('nobackend', extra='backend: {module: missing}\n')
        self.make('badui', extra='frontend: {dir: ../.., script: x.js}\n')
        self.make('badrange', extra='settings:\n  - {key: n, type: integer, label: N, min: 5, max: 1}\n')
        self.make('baddefault', extra='settings:\n  - {key: n, type: select, label: N, options: [a, b], default: c}\n')
        (self.dir / 'yamlerror').mkdir()
        (self.dir / 'yamlerror' / 'plugin.yaml').write_text('id: [unclosed\n')
        (self.dir / '.hidden').mkdir()
        other = self.base / 'more'
        self.make('twice', base=other)
        self.make('twice')
        self.cfg['plugins']['dirs'].append(str(other))
        manager, _ = self.start()
        listed = manager.list()['plugins']
        by_dir = {Path(p['path']).name + ('@more' if str(other) in p['path'] else ''): p for p in listed}
        self.assertNotIn('.hidden', by_dir)
        expect = {
            'empty': ('invalid', 'plugin_manifest_missing'), 'other_name': ('invalid', 'plugin_id_mismatch'),
            'future': ('incompatible', 'plugin_api_unsupported'), 'secretdef': ('invalid', 'plugin_manifest_field'),
            'nobackend': ('invalid', 'plugin_file_missing'), 'badui': ('invalid', 'plugin_manifest_field'),
            'badrange': ('invalid', 'plugin_manifest_field'), 'baddefault': ('invalid', 'plugin_manifest_field'),
            'yamlerror': ('invalid', 'plugin_manifest_unreadable'),
            'twice': ('disabled', None), 'twice@more': ('duplicate', 'plugin_duplicate'),
        }
        for name, (state, code) in expect.items():
            p = by_dir[name]
            self.assertEqual((p['state'], p['issue'] and p['issue']['code']), (state, code), name)
        with self.assertRaises(plugins.PluginError) as e:
            manager.set_enabled('future', True)
        self.assertEqual(e.exception.code, 'plugin_unusable')
        with self.assertRaises(plugins.PluginError) as e:
            manager.set_enabled('nope', True)
        self.assertEqual(e.exception.status, 404)

    def test_rescan_finds_new_plugins_without_loading_them(self):
        manager, _ = self.start()
        self.assertEqual(manager.list()['plugins'], [])
        self.make('late', backend='raise RuntimeError("must not be imported")\n')
        listed = manager.scan()['plugins']
        self.assertEqual([(p['id'], p['state']) for p in listed], [('late', 'disabled')])
        manager.set_enabled('late', True)
        self.assertTrue(self.info(manager, 'late')['restart_needed'])

    def test_env_variable_skips_loading(self):
        self.make('hello', backend='def setup(ctx):\n    @ctx.router.get("/x")\n    def x():\n        return 1\n')
        manager, _ = self.start()
        manager.set_enabled('hello', True)
        with mock.patch.dict(os.environ, {plugins.NO_PLUGINS_ENV: '1'}):
            manager, app = self.start()
        listed = manager.list()
        self.assertTrue(listed['disabled_by_env'])
        self.assertEqual((listed['plugins'][0]['state'], listed['plugins'][0]['restart_needed']), ('disabled', False))
        self.assertEqual(call(app, 'GET', '/api/ext/hello/x')[0], 404)

    # ---------------- settings ----------------

    SETTINGS = '''
        settings:
          - {key: name, type: string, label: Name, default: robot, max_length: 5}
          - {key: count, type: integer, label: Count, min: 1, max: 3}
          - {key: gain, type: number, label: Gain, default: 0.5, min: 0, max: 1}
          - {key: flag, type: boolean, label: Flag}
          - key: mode
            type: select
            label: Mode
            options: [{value: a, label: {zh: 甲, en: A}}, {value: 2, label: Two}]
          - {key: token, type: secret, label: Token}
        '''

    def test_settings_defaults_checks_and_secrets(self):
        self.make('cfg', extra=self.SETTINGS, backend='''
            def setup(ctx):
                @ctx.router.get('/token')
                def token():
                    return ctx.settings()['token']
            ''')
        manager, app = self.start()
        self.assertEqual(manager.settings_view('cfg'), {
            'values': {'name': 'robot', 'count': 1, 'gain': 0.5, 'flag': False, 'mode': 'a'}, 'secrets': {'token': False}})
        spec = self.info(manager, 'cfg')['settings']
        self.assertNotIn('default', next(s for s in spec if s['type'] == 'secret'))
        self.assertEqual(next(s for s in spec if s['key'] == 'mode')['options'][1], {'value': 2, 'label': {'zh': 'Two', 'en': 'Two'}})
        bad = [({'name': 'too long'}, 'plugin_setting_length'), ({'count': 4}, 'plugin_setting_range'),
               ({'count': 1.5}, 'plugin_setting_type'), ({'gain': True}, 'plugin_setting_type'),
               ({'flag': 1}, 'plugin_setting_type'), ({'mode': '2'}, 'plugin_setting_option'),
               ({'extra': 1}, 'plugin_setting_unknown'), ({'token': 5}, 'plugin_setting_type')]
        for values, code in bad:
            with self.assertRaises(plugins.PluginError, msg=str(values)) as e:
                manager.save_settings('cfg', {'name': 'ok', **values})
            self.assertEqual(e.exception.code, code, values)
        self.assertEqual(manager.settings_view('cfg')['values']['name'], 'robot')   # nothing written on errors
        view = manager.save_settings('cfg', {'count': 3, 'mode': 2, 'token': 's3cret'})
        self.assertEqual((view['values']['count'], view['values']['mode'], view['secrets']), (3, 2, {'token': True}))
        self.assertNotIn('token', view['values'])
        self.assertNotIn('s3cret', json.dumps(manager.list()))
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg['plugins']['state_file']).st_mode), 0o600)
        # secret: '' keeps it, clear removes it; the backend sees the value
        manager.save_settings('cfg', {'token': ''})
        manager, app = self.start()
        manager.set_enabled('cfg', True)
        manager, app = self.start()
        self.assertEqual(call(app, 'GET', '/api/ext/cfg/token'), (200, 's3cret'))
        with self.assertRaises(plugins.PluginError):
            manager.save_settings('cfg', {}, clear=['name'])          # only secrets can be cleared
        manager.save_settings('cfg', {}, clear=['token'])
        self.assertEqual(call(app, 'GET', '/api/ext/cfg/token'), (200, ''))   # applies without a restart

    def test_stored_values_that_no_longer_fit_fall_back_to_the_default(self):
        self.make('cfg', extra=self.SETTINGS)
        state = {'version': 1, 'plugins': {'cfg': {'enabled': False, 'settings': {'count': 9, 'mode': 'gone', 'name': 'abc'}}}}
        Path(self.cfg['plugins']['state_file']).write_text(json.dumps(state))
        manager, _ = self.start()
        self.assertEqual({k: manager.values('cfg')[k] for k in ('count', 'mode', 'name')}, {'count': 1, 'mode': 'a', 'name': 'abc'})

    def test_broken_state_file_is_kept_aside(self):
        Path(self.cfg['plugins']['state_file']).write_text('{not json')
        with mock.patch('sys.stderr'):
            manager, _ = self.start()
        self.assertTrue(Path(self.cfg['plugins']['state_file'] + '.broken').exists())
        self.assertEqual(manager.list()['plugins'], [])

    # ---------------- routes ----------------

    def test_management_routes(self):
        self.make('cfg', extra=self.SETTINGS)
        _, app = self.start()
        status, body = call(app, 'GET', '/api/plugins')
        self.assertEqual((status, [p['id'] for p in body['plugins']], body['api_version']), (200, ['cfg'], 1))
        self.assertEqual(call(app, 'PUT', '/api/plugins/cfg/enabled', {'enabled': 'yes'})[0], 400)
        status, body = call(app, 'PUT', '/api/plugins/cfg/enabled', {'enabled': True})
        self.assertEqual((status, body['enabled'], body['restart_needed']), (200, True, True))
        status, body = call(app, 'PUT', '/api/plugins/cfg/settings', {'values': {'count': 7}})
        self.assertEqual((status, body['detail']['code']), (400, 'plugin_setting_range'))
        status, body = call(app, 'PUT', '/api/plugins/cfg/settings', {'values': {'count': 2, 'token': 'x'}})
        self.assertEqual((status, body['values']['count'], body['secrets']['token']), (200, 2, True))
        self.assertEqual(call(app, 'GET', '/api/plugins/missing/settings')[0], 404)
        self.assertEqual(call(app, 'POST', '/api/plugins/rescan')[0], 200)

    def test_console_api_refuses_plugin_management_and_foreign_urls(self):
        api = plugins.ConsoleApi(lambda: FastAPI())
        for path in ('/api/plugins', '/api/plugins/x/settings', '/api/ext/../plugins/x/settings',
                     '/api/ext/%2e%2e/plugins'):
            with self.assertRaises(PermissionError, msg=path):
                asyncio.run(api.get(path))
        for path in ('http://example.com/api/x', '/ext/x/plugin.js', 'api/x'):
            with self.assertRaises(ValueError, msg=path):
                asyncio.run(api.get(path))

    def test_console_api_errors_carry_the_coded_detail(self):
        self.make('hello', backend='''
            from robot_console.plugins import ApiCallError
            def setup(ctx):
                @ctx.router.get('/probe')
                async def probe():
                    try:
                        await ctx.api.post('/api/core')
                    except ApiCallError as e:
                        return {'status': e.status}
            ''')
        manager, _ = self.start()
        manager.set_enabled('hello', True)
        _, app = self.start()
        self.assertEqual(call(app, 'GET', '/api/ext/hello/probe'), (200, {'status': 405}))

    def test_shutdown_hooks_run_and_failures_are_contained(self):
        log = self.base / 'log'
        self.make('hooks', backend=f'''
            def setup(ctx):
                ctx.on_shutdown(lambda: open({str(log)!r}, 'a').write('first\\n'))
                ctx.on_shutdown(lambda: 1 / 0)
                ctx.data_dir
            ''')
        manager, _ = self.start()
        manager.set_enabled('hooks', True)
        manager, _ = self.start()
        with mock.patch('sys.stderr'):
            manager.shutdown()
        self.assertEqual(log.read_text(), 'first\n')
        self.assertTrue((self.base / 'plugin_data' / 'hooks').is_dir())

    def test_loading_logs_with_the_ros_logger(self):
        """rclpy raises when one call site logs with two severities; a failed load logs error after info."""
        try:
            from rclpy.logging import get_logger
        except ImportError:
            self.skipTest('rclpy not available')
        self.make('good', backend='def setup(ctx):\n    ctx.log.info("a")\n    ctx.log.warn("b")\n    ctx.log.error("c")\n')
        self.make('bad', backend='def setup(ctx):\n    raise RuntimeError("x")\n')
        manager = plugins.PluginManager(self.cfg)
        manager.set_enabled('good', True)
        manager.set_enabled('bad', True)
        manager = plugins.PluginManager(self.cfg, get_logger('plugin_test'))
        with mock.patch('sys.stderr'):
            manager.load(FastAPI())
            manager.load(FastAPI())
        self.assertEqual((self.info(manager, 'good')['state'], self.info(manager, 'bad')['state']), ('active', 'failed'))

    def test_example_plugin_manifest_is_valid(self):
        example = Path(__file__).resolve().parents[1] / 'examples' / 'plugins' / 'hello_console'
        m = plugins.read_manifest(str(example))
        self.assertEqual((m['id'], m['api'], bool(m['backend']), m['frontend']['script']),
                         ('hello_console', 1, True, 'plugin.js'))


if __name__ == '__main__':
    unittest.main()
