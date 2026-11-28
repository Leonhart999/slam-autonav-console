"""Settings API tests on a bare FastAPI app. Network requests never leave the process."""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import config, open_folder, settings

API = '/api/settings'


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='console-settings-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.shipped = self.root / 'console.yaml'
        self.target = self.root / 'overrides' / 'console.yaml'
        self.data = self.root / 'data'
        self.shipped.write_text(yaml.safe_dump({
            'paths': {'data_root': self.data.as_posix()},
            'server': {'host': '127.0.0.1', 'port': 8000, 'log_dir': '${data_root}/logs'},
            'mapping': {'slam_profiles': {'one': {}, 'two': {}}, 'maps_dir': '${data_root}/maps'},
            'teleop': {'max_v': 5.0, 'recommended_v': [0.5, 3]},
            'batch': {'slam_stacks': ['one']}, 'evaluation': {'evo_python': sys.executable},
            'playback': {'rates': [0.5, 1, 2], 'bag_dirs': ['${data_root}/bags', '${data_root}/others'],
                         'space_check': [self.root.as_posix(), (self.root / 'missing').as_posix()]},
            'plugins': {'pip': {'proxy': 'http://pip.proxy:1234', 'index_url': ''},
                        'market': {'index_urls': ['https://market.example/index']}},
        }), encoding='utf-8')
        (self.root / 'stacks.yaml').write_text('one: {label: {en: One}, cmd: [one]}\n', encoding='utf-8')
        env = mock.patch.dict(os.environ, ROBOT_CONSOLE_USER_CONFIG=str(self.target))
        env.start()
        self.addCleanup(env.stop)
        self.start()

    def start(self, plugins=None):
        self.cfg = config.load(str(self.shipped))
        app = FastAPI()
        settings.register_settings_routes(app, self.cfg, plugins)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def seed(self, text):
        self.target.parent.mkdir(exist_ok=True)
        self.target.write_text(text, encoding='utf-8')

    def raw(self):
        return yaml.safe_load(self.target.read_text(encoding='utf-8'))

    def put(self, values=None, reset=()):
        return self.client.put(API, json={'set': values or {}, 'reset': list(reset)})

    def success(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def failure(self, response, status, code, **params):
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()['detail']
        self.assertEqual(set(detail), {'code', 'params'})
        self.assertEqual(detail['code'], code)
        for key, value in params.items():
            self.assertEqual(detail['params'][key], value)

    def test_raw_defaults_overrides_labels_and_startup_snapshot(self):
        self.seed('server: {port: 8100}\nmapping: {slam_profiles: {custom: {}}}\n')
        self.start()
        payload = self.success(self.client.get(API))
        self.assertEqual(payload['user_file'], str(self.target))
        self.assertTrue(payload['writable'])
        self.assertIsNone(payload['reason'])
        self.assertFalse(payload['pending_restart'])
        self.assertEqual(payload['fields']['server.port'], dict(value=8100, default=8000, overridden=True))
        self.assertEqual(payload['fields']['mapping.maps_dir'],
                         dict(value='${data_root}/maps', default='${data_root}/maps', overridden=False))
        self.assertEqual(payload['read_only'], {'server.host': '127.0.0.1'})
        self.assertEqual(payload['options']['batch.slam_stacks'], [
            dict(name='one', label={'en': 'One', 'zh': 'One'}),
            dict(name='two', label={'en': 'two', 'zh': 'two'}),
            dict(name='custom', label={'en': 'custom', 'zh': 'custom'})])
        self.success(self.put({'batch.slam_stacks': ['custom', 'two']}))
        self.assertTrue(self.success(self.client.get(API))['pending_restart'])
        self.assertEqual(self.cfg['batch']['slam_stacks'], ['one'])
        self.seed('server: {port: 8100}\n')
        self.assertFalse(self.success(self.client.get(API))['pending_restart'])

    def test_set_default_equal_reset_pruning_and_header(self):
        result = self.success(self.put({'server.port': 8123, 'plugins.market.proxy': 'http://localhost:1080',
                                       'mapping.maps_dir': '~/other'}))
        self.assertEqual(set(result['changed']), {'server.port', 'plugins.market.proxy', 'mapping.maps_dir'})
        self.assertTrue(result['restart_required'])
        self.assertTrue(self.target.read_text(encoding='utf-8').startswith('#'))
        self.assertEqual(self.cfg['server']['port'], 8000)
        self.assertEqual(self.success(self.put({'server.port': 8123}))['changed'], [])
        self.success(self.put({'server.port': 8000}, ['plugins.market.proxy']))
        self.assertEqual(self.raw(), {'mapping': {'maps_dir': '~/other'}})
        empty = self.success(self.put(reset=['mapping.maps_dir']))
        self.assertFalse(empty['restart_required'])
        self.assertFalse(empty['fields']['mapping.maps_dir']['overridden'])
        self.assertEqual(self.target.read_text(encoding='utf-8'), settings.HEADER)
        self.success(self.put({'server.port': 8133}))
        self.assertTrue(self.target.read_text(encoding='utf-8').startswith(settings.HEADER))

    def test_reset_preserves_unrelated_values_and_comments(self):
        if settings.YAML is not None:
            from ruamel.yaml.comments import CommentedMap

            # Generic shallow copies can drop YAML metadata on Python 3.10. Exercise that case on every runtime.
            generic_copy = mock.patch.object(CommentedMap, '__copy__', lambda m: CommentedMap(m.items()), create=True)
            generic_copy.start()
            self.addCleanup(generic_copy.stop)
        self.seed('# existing header\nserver:\n  port: 8200\n  host: localhost  # keep host comment\n'
                  'plugins:\n  pip: &pip\n    proxy: "http://localhost"\n    timeout: 80  # keep timeout comment\n'
                  'saved_pip: *pip\nother: [red, blue]\n')
        self.success(self.client.post(API + '/reset_all', json={}))
        self.assertEqual(self.raw(), {'server': {'host': 'localhost'}, 'plugins': {'pip': {'timeout': 80}},
                                      'saved_pip': {'proxy': 'http://localhost', 'timeout': 80}, 'other': ['red', 'blue']})
        if settings.YAML is not None:
            text = self.target.read_text(encoding='utf-8')
            self.assertIn('# existing header', text)
            codec = settings.YAML(typ='rt')
            saved = codec.load(text)
            # Inspect each branch separately so a comment on the alias cannot hide its loss on the edited branch.
            for branch, line in [(saved['server'], r'host: localhost +# keep host comment'),
                                 (saved['plugins']['pip'], r'timeout: 80 +# keep timeout comment'),
                                 (saved['saved_pip'], r'timeout: 80 +# keep timeout comment')]:
                with self.subTest(keys=list(branch)):
                    output = io.StringIO()
                    codec.dump(branch, output)
                    self.assertRegex(output.getvalue(), '(?m)^' + line + '$')

    def test_invalid_request_is_atomic_for_every_reason(self):
        self.seed('other: [1, 2] # preserve these bytes\n')
        original = self.target.read_bytes()
        cases = [
            ('server.port', True, 'type'), ('batch.max_runs', 1.0, 'type'), ('estop.zero_duration', False, 'type'),
            ('server.status_rate', 21, 'range'), ('batch.run_timeout', 29, 'range'),
            ('navigation.default_map', '/maps/x.yml', 'format'), ('paths.data_root', '/${data_root}', 'format'),
            ('mapping.maps_dir', '/a\nb', 'format'), ('mapping.runs_dir', '~' * 513, 'format'),
            ('evaluation.results_dir', 'results', 'format'), ('teleop.topic', '/9cmd', 'format'),
            ('mapping.gt_topic', '/cmd/', 'format'), ('plugins.pip.proxy', 'ftp://proxy', 'format'),
            ('plugins.pip.index_url', 'file:///pip', 'format'),
            ('plugins.market.index_urls', ['http://market.example'], 'format'),
            ('batch.slam_stacks', ['missing'], 'option'), ('navigation.speed_scale_range', [2, 2], 'order'),
            ('navigation.speed_scale_range', [1], 'count'), ('plugins.market.proxy', 'socks5://proxy:1080', 'format'),
            ('playback.bag_dirs', [], 'count'), ('playback.rates', [2, 2.0], 'duplicate'),
            ('batch.speeds', [[]], 'type'),
        ]
        for key, value, reason in cases:
            with self.subTest(key=key, value=value):
                self.failure(self.put({'server.log_dir': '~/new', key: value}), 422, 'setting_invalid',
                             key=key, reason=reason)
                self.assertEqual(self.target.read_bytes(), original)
        self.failure(self.put({'server.host': 'outside'}), 422, 'setting_unknown', key='server.host')
        self.failure(self.put(reset=['plugins.dirs']), 422, 'setting_unknown', key='plugins.dirs')
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_reset_removes_inherited_yaml_key_without_editing_its_source(self):
        self.seed('base: &base {port: 8200, host: keep}\nserver: {<<: *base}\nflags: {y: yes, n: no}\n')
        result = self.success(self.put(reset=['server.port']))
        self.assertFalse(result['fields']['server.port']['overridden'])
        self.assertEqual(self.raw(), {'base': {'port': 8200, 'host': 'keep'}, 'server': {'host': 'keep'},
                                      'flags': {'y': True, 'n': False}})
        self.assertFalse(self.success(self.client.get(API))['fields']['server.port']['overridden'])

    def test_failed_replace_leaves_previous_contents_and_no_temp_file(self):
        self.seed('server: {port: 8200} # existing\n')
        before = self.target.read_bytes()
        with mock.patch.object(settings.os, 'replace', side_effect=OSError('write failed')):
            self.failure(self.put({'server.port': 8300}), 500, 'settings_file_error', reason='write')
        self.assertEqual(self.target.read_bytes(), before)
        backup, = self.target.parent.glob('console.yaml.bak_*')
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(set(self.target.parent.iterdir()), {self.target, backup})

    def test_external_edits_to_defaults_detected_against_registration_snapshot(self):
        defaults = yaml.safe_load(self.shipped.read_bytes())
        defaults['server']['port'] = 9000
        self.shipped.write_text(yaml.safe_dump(defaults), encoding='utf-8')
        payload = self.success(self.client.get(API))
        self.assertTrue(payload['pending_restart'])
        self.assertEqual(payload['fields']['server.port'], dict(value=9000, default=9000, overridden=False))
        self.assertEqual(self.success(self.client.get(API + '/about'))['port'], 8000)

    def test_effective_velocity_limit_in_both_directions(self):
        self.failure(self.put({'teleop.max_v': 2}), 422, 'setting_invalid',
                     key='teleop.recommended_v', reason='range', max=2)
        self.assertFalse(self.target.exists())
        self.success(self.put({'teleop.recommended_v': [0, 6], 'teleop.max_v': 6}))
        before = self.target.read_bytes()
        self.failure(self.put(reset=['teleop.max_v']), 422, 'setting_invalid', key='teleop.recommended_v',
                     reason='range', max=5)
        self.assertEqual(self.target.read_bytes(), before)
        self.success(self.put({'teleop.max_v': 1, 'teleop.recommended_v': [0.1, 1]}))
        self.assertIsInstance(self.raw()['teleop']['max_v'], int)
        self.assertFalse(self.success(self.client.post(API + '/reset_all', json={}))['pending_restart'])

    def test_read_only_and_config_target_selection(self):
        self.assertEqual(self.cfg['user_config_target'], str(self.target))
        self.assertEqual(self.cfg['config_files'], [str(self.shipped)])
        os.environ.pop('ROBOT_CONSOLE_USER_CONFIG')
        self.start()
        result = self.success(self.client.get(API))
        self.assertFalse(result['writable'])
        self.assertIsNone(result['user_file'])
        self.assertEqual(result['reason'], 'no_user_config')
        for request in (self.put({'server.port': 8123}), self.client.post(API + '/reset_all', json={}),
                        self.client.post(API + '/import', json={'console_yaml': ''})):
            self.failure(request, 409, 'settings_read_only')
        self.assertFalse(self.target.exists())
        with mock.patch.object(config, 'default_config_path', return_value=str(self.shipped)), \
                mock.patch.object(config, 'USER_CONFIG', str(self.target)):
            cfg = config.load()
        self.assertEqual(cfg['user_config_target'], str(self.target))
        self.assertEqual(cfg['config_files'], [str(self.shipped)])

    def test_backup_rotation_contents_and_modes(self):
        self.success(self.put({'server.port': 8200}))
        if os.name == 'posix':
            # may hold proxy credentials: owner only
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
            self.target.chmod(0o640)
        for port in range(8201, 8208):
            self.success(self.put({'server.port': port}))
        backups = sorted(self.target.parent.glob('console.yaml.bak_*'))
        self.assertEqual([yaml.safe_load(b.read_bytes())['server']['port'] for b in backups], list(range(8202, 8207)))
        for path in backups:
            datetime.strptime(path.name, 'console.yaml.bak_%Y%m%d_%H%M%S')
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
            for path in backups:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_concurrent_writes_keep_both_changes(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.put, change) for change in ({'server.port': 8200}, {'mapping.maps_dir': '~/M'})]
            for job in jobs:
                self.success(job.result())
        self.assertEqual(self.raw(), {'server': {'port': 8200}, 'mapping': {'maps_dir': '~/M'}})

    def test_export_import_round_trip_and_ignored_keys(self):
        self.seed('private: keep\nplugins: {pip: {timeout: 20}}\n')
        self.assertEqual(self.success(self.client.get(API + '/export'))['console_yaml'], '')
        self.success(self.put({'server.port': 8123, 'playback.rates': [1, 2]}))
        exported = self.success(self.client.get(API + '/export'))
        self.assertEqual((exported['format'], exported['version']), ('robot_console.settings', 1))
        self.assertIsNotNone(datetime.fromisoformat(exported['exported_at']).tzinfo)
        data = yaml.safe_load(exported['console_yaml'])
        self.assertEqual(data, {'server': {'port': 8123}, 'playback': {'rates': [1, 2]}})
        self.success(self.client.post(API + '/reset_all', json={}))
        self.success(self.put({'mapping.gt_topic': '/another'}))
        data['server']['host'] = 'ignored'
        data['private'] = 'also ignored'
        result = self.success(self.client.post(API + '/import', json={'console_yaml': yaml.safe_dump(data)}))
        self.assertEqual(set(result['ignored']), {'server.host', 'private'})
        self.assertEqual(set(result['changed']), {'server.port', 'playback.rates', 'mapping.gt_topic'})
        self.assertEqual(self.raw(), {'private': 'keep', 'plugins': {'pip': {'timeout': 20}},
                                      'server': {'port': 8123}, 'playback': {'rates': [1, 2]}})
        self.success(self.client.post(API + '/import', json={'console_yaml': '# empty\n'}))
        self.assertEqual(self.raw(), {'private': 'keep', 'plugins': {'pip': {'timeout': 20}}})

    def test_import_rejects_bad_shape_size_yaml_and_values_without_writing(self):
        self.seed('server: {port: 8333}\n')
        original = self.target.read_bytes()
        for content, reason in [(None, 'type'), ('[]', 'mapping'), ('7', 'mapping'), ('x: [', 'yaml'),
                                ('!!python/object:os.X {}', 'yaml'), ('#' + 'é' * (128 * 1024), 'size')]:
            with self.subTest(reason=reason):
                self.failure(self.client.post(API + '/import', json={'console_yaml': content}),
                             422, 'settings_import_invalid', reason=reason)
        self.failure(self.client.post(API + '/import', json={'console_yaml': 'batch: {max_runs: true}'}),
                     422, 'setting_invalid', reason='type', key='batch.max_runs')
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_storage_uses_running_tree_excludes_hidden_items_and_deduplicates_disks(self):
        maps = self.data / 'maps'
        (maps / '.trash').mkdir(parents=True)
        (maps / 'nested').mkdir()
        (maps / 'nested' / 'a').write_bytes(b'123')
        (maps / 'map').write_bytes(b'abcde')
        (maps / '.trash' / 'old').write_bytes(b'!')
        expected = 2
        if os.name == 'posix':
            (maps / 'cycle').symlink_to(maps, target_is_directory=True)
            expected += 1
        self.success(self.put({'mapping.maps_dir': str(self.root / 'pending-maps')}))
        result = self.success(self.client.get(API + '/storage'))
        folders = result['folders']
        self.assertEqual([f['key'] for f in folders], ['data_root', 'maps', 'runs', 'bags', 'bags', 'results', 'logs'])
        self.assertEqual(folders[1], dict(key='maps', path=self.cfg['mapping']['maps_dir'], exists=True, items=expected,
                                          bytes=9, partial=False))
        self.assertFalse(folders[2]['exists'])
        self.assertEqual(len(result['disks']), 1)
        disk = result['disks'][0]
        self.assertEqual(disk['path'], self.cfg['paths']['data_root'])
        self.assertGreaterEqual(disk['total'], disk['free'])
        roots = open_folder.allowed_roots(self.cfg)
        for path in (self.cfg['paths']['data_root'], self.cfg['server']['log_dir']):
            self.assertIn(os.path.realpath(path), roots)

    def test_about_running_configuration_and_plugins(self):
        plugins = mock.Mock()
        plugins.list.return_value = {'plugins': [{'enabled': True}, {'enabled': False}]}
        self.start(plugins)
        self.success(self.put({'server.port': 8200}))
        with mock.patch.dict(os.environ, ROS_DISTRO='humble', ROS_DOMAIN_ID='9', RMW_IMPLEMENTATION='rmw_test'):
            about = self.success(self.client.get(API + '/about'))
        self.assertEqual((about['version'], about['repo']), (settings.VERSION, 'Leonhart999/slam-autonav-console'))
        self.assertEqual((about['ros_distro'], about['domain_id'], about['rmw']), ('humble', '9', 'rmw_test'))
        self.assertEqual((about['plugins_enabled'], about['plugins_total']), (1, 2))
        self.assertEqual(about['config_files'], [str(self.shipped)])
        self.assertEqual(about['port'], 8000)
        self.assertTrue(about['evo_ok'])
        plugins.list.side_effect = RuntimeError('unavailable')
        about = self.success(self.client.get(API + '/about'))
        self.assertIsNone(about['plugins_enabled'])
        self.assertIsNone(about['plugins_total'])

    def test_storage_partial_on_deadline(self):
        self.data.mkdir()
        (self.data / 'child').touch()
        with mock.patch.object(settings, 'time', wraps=settings.time) as timer:
            timer.monotonic.side_effect = [1, 1, 3.1]
            result = settings.folder_info('data_root', str(self.data))
        self.assertTrue(result['partial'])
        self.assertTrue(result['exists'])
        self.assertEqual(result['items'], 0)

    def test_release_comparison_cache_force_and_expiration(self):
        def release(tag):
            return io.BytesIO(json.dumps(dict(tag_name=tag, html_url='https://example/release',
                                              published_at='2026-11-01T00:00:00Z')).encode())
        with mock.patch.object(settings, 'urlopen', side_effect=[release('v0.10.0'), release(f'v{settings.VERSION}'),
                                                                 urllib.error.URLError('offline')]) as fetch, \
                mock.patch.object(settings, 'time', wraps=settings.time) as timer:
            timer.monotonic.return_value = 100
            first = self.success(self.client.get(API + '/update'))
            self.assertEqual((first['current'], first['latest'], first['newer']), (settings.VERSION, '0.10.0', True))
            self.assertIsNone(first['error'])
            self.assertEqual(first, self.success(self.client.get(API + '/update?force=true')))
            timer.monotonic.return_value = 131
            self.assertEqual(first, self.success(self.client.get(API + '/update')))
            current = self.success(self.client.get(API + '/update?force=true'))
            self.assertFalse(current['newer'])
            self.assertIsNone(current['error'])
            timer.monotonic.return_value += 6 * 3600
            offline = self.success(self.client.get(API + '/update'))
            self.assertEqual(offline['error'], 'network')
            self.assertIsNone(offline['latest'])
            self.assertEqual(fetch.call_count, 3)
            fetch.assert_called_with('https://api.github.com/repos/Leonhart999/slam-autonav-console/releases/latest',
                                     'http://pip.proxy:1234', 10)

    def test_release_http_and_parse_errors(self):
        for response, reason in [(urllib.error.HTTPError('url', 403, 'rate limit', {}, None), 'http_403'),
                                 (io.BytesIO(b'{"tag_name":"broken"}'), 'parse')]:
            self.start()
            with mock.patch.object(settings, 'urlopen', side_effect=[response]):
                self.assertEqual(self.success(self.client.get(API + '/update'))['error'], reason)

    def test_github_request_headers_and_market_proxy_precedence(self):
        self.seed('plugins: {market: {proxy: "https://market.proxy:8080"}}\n')
        self.start()
        with mock.patch.object(settings.urllib.request, 'build_opener') as build:
            opener = build.return_value
            opener.open.return_value = io.BytesIO(b'{"tag_name":"v0.99.0"}')
            response = self.success(self.client.get(API + '/update'))
            self.assertTrue(response['newer'])
            proxy_handler = build.call_args.args[-1]
            self.assertEqual(proxy_handler.proxy, 'https://market.proxy:8080')
            request, = opener.open.call_args.args
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(dict(request.header_items()), {'User-agent': 'robot_console'})
            self.assertEqual(opener.open.call_args.kwargs, {'timeout': 10})

    def test_socks5h_probe_sends_get_through_configured_proxy(self):
        # The first stream carries SOCKS5 negotiation, the second carries the HTTP reply.
        sock = mock.Mock()
        sock.makefile.side_effect = [io.BytesIO(bytes.fromhex('05 00 05 00 00 01 00 00 00 00 00 00')),
                                     io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')]
        with mock.patch.object(settings.socket, 'create_connection', return_value=sock) as connect:
            result = settings.network_probe('pip', 'http://pip.example/simple/', 'socks5h://proxy.example:1080')
        self.assertTrue(result['ok'], result)
        connect.assert_called_once_with(('proxy.example', 1080), 8)
        sent = [call.args[0] for call in sock.sendall.call_args_list]
        self.assertEqual(sent[0], b'\x05\x01\x00')
        self.assertIn(b'pip.example', sent[1])
        self.assertIn(b'GET /simple/ HTTP/1.1', sent[2])
        self.assertIn(b'User-Agent: robot_console', sent[2])

    def test_network_test_unsaved_validation_and_concurrent_probes(self):
        barrier = threading.Barrier(3, timeout=3)

        def fetch(url, proxy, timeout):
            self.assertEqual((proxy, timeout), ('http://localhost:1080', 8))
            barrier.wait()
            if url == settings.GITHUB:
                raise urllib.error.HTTPError(url, 503, 'offline', {}, None)
            response = io.BytesIO()
            response.status = 200
            return response

        with mock.patch.object(settings, 'urlopen', side_effect=fetch) as opener:
            body = {'proxy': 'http://localhost:1080', 'market_index_url': 'https://market.test/index',
                    'pip_index_url': 'http://pip.test/simple'}
            results = self.success(self.client.post(API + '/network_test', json=body))['results']
            self.assertEqual([r['target'] for r in results], ['market', 'pip', 'github'])
            self.assertEqual([r['url'] for r in results], [body['market_index_url'], body['pip_index_url'], settings.GITHUB])
            self.assertEqual([r['ok'] for r in results], [True, True, False])
            self.assertEqual((results[2]['status'], results[2]['error']), (503, 'http_503'))
            for key, value in [('proxy', None), ('pip_index_url', 'ftp://bad'), ('market_index_url', 'relative')]:
                self.failure(self.client.post(API + '/network_test', json={key: value}),
                             422, 'setting_invalid', key=key)
            self.assertEqual(opener.call_count, 3)
        self.assertFalse(self.target.exists())

    def test_network_local_file_and_running_fallbacks(self):
        with mock.patch.object(settings, 'urlopen', side_effect=urllib.error.URLError('offline')) as opener:
            for url in (str(self.shipped), self.shipped.as_uri()):
                results = self.success(self.client.post(API + '/network_test', json={'market_index_url': url}))['results']
                self.assertEqual(results[0], dict(target='market', url=url, ok=True, ms=0, status=None, error=None))
                self.assertEqual(results[1]['url'], 'https://pypi.org/simple/')
                self.assertEqual(results[2]['error'], 'network')
            self.assertEqual(opener.call_count, 4)
            results = self.success(self.client.post(API + '/network_test', json={}))['results']
            self.assertEqual(results[0]['url'], 'https://market.example/index')
            for call in opener.call_args_list:
                self.assertEqual(call.args[1:], ('http://pip.proxy:1234', 8))

    # ---- guards for the review findings of round 1

    def test_estop_safeguards_cannot_be_switched_off(self):
        self.failure(self.put({'estop.zero_duration': 0}), 422, 'setting_invalid', key='estop.zero_duration',
                     reason='range')
        # the cancel list is safety-critical and not editable here at all
        self.failure(self.put({'estop.cancel_actions': []}), 422, 'setting_unknown', key='estop.cancel_actions')

    def test_import_ignores_machine_folders(self):
        self.success(self.put({'mapping.maps_dir': '~/mine'}))
        text = yaml.safe_dump({'paths': {'data_root': '/mnt/shared/experiment'}, 'mapping': {'maps_dir': '/x'},
                               'playback': {'bag_dirs': ['/y']}, 'server': {'port': 8124}})
        result = self.success(self.client.post(API + '/import', json={'console_yaml': text}))
        self.assertEqual(set(result['ignored']), {'paths.data_root', 'mapping.maps_dir', 'playback.bag_dirs'})
        self.assertEqual(self.raw(), {'mapping': {'maps_dir': '~/mine'}, 'server': {'port': 8124}})

    def test_folders_must_be_creatable(self):
        afile = self.root / 'a-file'
        afile.write_text('x')
        self.failure(self.put({'server.log_dir': str(afile)}), 422, 'setting_invalid', key='server.log_dir',
                     reason='not_dir')
        if os.name == 'posix' and os.geteuid() != 0:
            locked = self.root / 'locked'
            locked.mkdir()
            locked.chmod(0o500)
            self.addCleanup(locked.chmod, 0o700)
            self.failure(self.put({'mapping.runs_dir': str(locked / 'runs')}), 422, 'setting_invalid',
                         key='mapping.runs_dir', reason='not_writable')
            self.failure(self.put({'playback.bag_dirs': [str(self.root / 'ok'), str(locked / 'b')]}), 422,
                         'setting_invalid', key='playback.bag_dirs', reason='not_writable')
        self.success(self.put({'mapping.runs_dir': str(self.root / 'new' / 'runs')}))
        self.assertFalse(self.target.with_name('console.yaml.lock').exists())

    def test_port_in_use_is_refused(self):
        import socket
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            port = busy.getsockname()[1]
            if port < 1024:
                self.skipTest('ephemeral port below the allowed range')
            self.failure(self.put({'server.port': port}), 422, 'setting_invalid', key='server.port', reason='busy')
        self.assertFalse(self.target.exists())

    def test_null_override_file_starts_like_an_empty_one(self):
        self.seed('null\n')
        self.start()
        self.assertFalse(self.success(self.client.get(API))['fields']['server.port']['overridden'])
        self.success(self.put({'server.port': 8125}))
        self.assertEqual(self.raw(), {'server': {'port': 8125}})

    def test_round_trip_that_changes_a_value_falls_back_to_plain_rewrite(self):
        self.seed('# note\nother: {val: 5}\n')
        broken = lambda original, changes: 'other: {val: 6}\nserver: {port: 8126}\n'
        with mock.patch.object(settings, 'render', side_effect=broken):
            self.success(self.put({'server.port': 8126}))
        self.assertEqual(self.raw(), {'other': {'val': 5}, 'server': {'port': 8126}})

    # ---- guards for the review findings of round 2

    def test_reset_cannot_bring_back_a_busy_port(self):
        import socket
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            port = busy.getsockname()[1]
            if port < 1024:
                self.skipTest('ephemeral port below the allowed range')
            shipped = yaml.safe_load(self.shipped.read_text(encoding='utf-8'))
            shipped['server']['port'] = port
            self.shipped.write_text(yaml.safe_dump(shipped), encoding='utf-8')
            self.seed('server: {port: 8100}\n')
            self.start()                       # running on 8100; the default is now the busy port
            self.cfg['server']['port'] = 8100
            self.failure(self.client.post(API + '/reset_all', json={}), 422, 'setting_invalid', key='server.port',
                         reason='busy')
        self.assertEqual(self.raw(), {'server': {'port': 8100}})

    def test_moving_data_root_checks_derived_folders(self):
        root = self.root / 'new-data'
        root.mkdir()
        (root / 'logs').write_text('not a folder')
        self.failure(self.put({'paths.data_root': str(root)}), 422, 'setting_invalid', key='server.log_dir',
                     reason='not_dir')
        self.assertFalse(self.target.exists())

    def test_falsy_override_documents_still_start(self):
        for text in ('[]\n', "''\n", 'false\n'):
            with self.subTest(text=text):
                self.seed(text)
                self.start()
                self.assertFalse(self.success(self.client.get(API))['fields']['server.port']['overridden'])

    def test_state_changing_routes_need_json(self):
        self.success(self.put({'server.port': 8127}))
        for path, kwargs in ((API + '/reset_all', {}), (API + '/reset_all', {'data': {'x': '1'}}),
                             (API + '/import', {'content': b'{}', 'headers': {'content-type': 'text/plain'}})):
            with self.subTest(path=path, kwargs=kwargs):
                self.failure(self.client.post(path, **kwargs), 415, 'settings_json_required')
        self.assertEqual(self.raw(), {'server': {'port': 8127}})

    def test_backup_is_never_world_readable(self):
        if os.name != 'posix':
            self.skipTest('POSIX modes')
        self.success(self.put({'server.port': 8128}))
        seen = []
        real_open = settings.os.open
        def spy(path, flags, mode=0o777, *args):
            if '.bak_' in str(path):
                seen.append(mode)
            return real_open(path, flags, mode, *args)
        old = os.umask(0o022)
        try:
            with mock.patch.object(settings.os, 'open', side_effect=spy):
                self.success(self.put({'server.port': 8129}))
        finally:
            os.umask(old)
        self.assertEqual(seen, [0o600])


if __name__ == '__main__':
    unittest.main()
