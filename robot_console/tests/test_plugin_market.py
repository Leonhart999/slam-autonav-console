"""plugin_market.py: index reading (fallback URLs, cache, invalid entries), install from the index with size and
SHA-256 checks, archive safety, never replacing hand-placed plugins, updates keeping state, zip uploads,
uninstall. Everything runs on local file:// URLs (no network)."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI  # noqa: E402

from robot_console import plugins  # noqa: E402
from robot_console import plugin_market  # noqa: E402


def call(app, method, path, body=None, params=None, content=None, content_type=None):
    return asyncio.run(plugins.asgi_request(app, method, path, body, params=params, content=content,
                                            content_type=content_type))


def plugin_zip(pid, version='0.1.0', api=1, extra_files=None, top=None, backend=None):
    """Bytes of a plugin zip with one folder <top or pid>/."""
    top = top or pid
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        manifest = f'id: {pid}\napi: {api}\nversion: {version}\nname: {{zh: 插件 {pid}, en: Plugin {pid}}}\n'
        if backend is not None:
            manifest += 'backend: {module: backend}\n'
            z.writestr(f'{top}/backend.py', textwrap.dedent(backend))
        z.writestr(f'{top}/plugin.yaml', manifest)
        for name, data in (extra_files or {}).items():
            z.writestr(name if '/' in name else f'{top}/{name}', data)
    return buf.getvalue()


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-market-test-')
        self.base = Path(self.tmp.name)
        self.dir = self.base / 'plugins'
        self.shipped = self.base / 'shipped'
        self.repo = self.base / 'repo'                    # stands for GitHub: index.json and release zips
        self.repo.mkdir()
        self.index_path = self.repo / 'index.json'
        self.cfg = {'paths': {'data_root': str(self.base)},
                    'plugins': {'dirs': [str(self.dir), str(self.shipped)],
                                'state_file': str(self.base / 'state.json'),
                                'data_dir': str(self.base / 'plugin_data'),
                                'envs_dir': str(self.base / 'plugin_envs'),
                                'market': {'index_urls': [str(self.base / 'missing.json'), str(self.index_path)]}}}
        self.env = mock.patch.dict(os.environ, {plugins.NO_PLUGINS_ENV: ''})
        self.env.start()
        self.entries = []

    def tearDown(self):
        self.env.stop()
        for name in [m for m in sys.modules if m.startswith(plugins.MODULE_PREFIX + '.')]:
            del sys.modules[name]
        self.tmp.cleanup()

    def publish(self, pid, data, version='0.1.0', api=1, sha=None, size=None, **fields):
        path = self.repo / f'{pid}-{version}.zip'
        path.write_bytes(data)
        self.entries = [e for e in self.entries if e['id'] != pid]
        self.entries.append({'id': pid, 'version': version, 'api': api, 'name': {'zh': pid, 'en': pid},
                             'description': {'zh': '说明', 'en': 'About'}, 'url': path.as_uri(),
                             'sha256': sha or hashlib.sha256(data).hexdigest(), 'size': size or len(data),
                             'requirements': ['numpy>=1.23'], 'author': 'me', **fields})
        self.index_path.write_text(json.dumps({'format': 1, 'name': 'test', 'plugins': self.entries}),
                                   encoding='utf-8')

    def start(self):
        manager = plugins.PluginManager(self.cfg)
        app = FastAPI()
        plugins.register_plugin_routes(app, manager)
        return manager, app

    def leftovers(self):
        hidden = [p.name for p in self.dir.iterdir() if p.name.startswith('.')] if self.dir.exists() else []
        downloads = list((self.base / 'plugin_downloads').glob('*')) if (self.base / 'plugin_downloads').exists() else []
        return hidden, downloads

    # ---------------- index ----------------

    def test_index_falls_back_to_next_url_skips_bad_entries_and_is_cached(self):
        self.publish('good', plugin_zip('good'))
        data = json.loads(self.index_path.read_text(encoding='utf-8'))
        data['plugins'] += [{'id': 'Bad-Id', 'version': '1'}, {**self.entries[0], 'sha256': 'xyz'},
                            {**self.entries[0], 'url': 'ftp://x/y.zip'}]
        self.index_path.write_text(json.dumps(data), encoding='utf-8')
        manager, app = self.start()
        status, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual(status, 200)
        self.assertEqual(view['source'], str(self.index_path))
        self.assertEqual([p['id'] for p in view['plugins']], ['good'])
        self.assertEqual(view['plugins'][0]['status'], 'available')
        self.assertEqual(len(view['skipped']), 3)
        self.assertIsNone(view['error'])
        # GitHub unreachable later: the cached list is shown with the error
        self.index_path.unlink()
        manager, app = self.start()
        status, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual(status, 200)
        self.assertTrue(view['stale'])
        self.assertEqual(view['error']['code'], 'plugin_market_index_failed')
        self.assertEqual([p['id'] for p in view['plugins']], ['good'])

    def test_not_an_index(self):
        self.index_path.write_text('{"plugins": []}', encoding='utf-8')
        _, app = self.start()
        status, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual((status, view['error']['code'], view['plugins']), (200, 'plugin_market_index_failed', []))

    def test_version_order(self):
        key = plugin_market.version_key
        self.assertLess(key('0.9.1'), key('0.10.0'))
        self.assertLess(key('1.0.0rc1'), key('1.0.0'))
        self.assertEqual(key('1.2.0'), key('1.2.0'))

    # ---------------- install ----------------

    def test_install_places_disabled_plugin_with_provenance(self):
        marker = self.base / 'imported'
        self.publish('tool', plugin_zip('tool', backend=f'open({str(marker)!r}, "w").close()\ndef setup(ctx):\n    pass\n'))
        manager, app = self.start()
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual(status, 200, out)
        self.assertFalse(marker.exists())                            # no plugin code ran
        self.assertEqual((out['plugin']['state'], out['plugin']['enabled'], out['plugin']['origin']),
                         ('disabled', False, 'market'))
        prov = json.loads((self.dir / 'tool' / '.market.json').read_text(encoding='utf-8'))
        self.assertEqual((prov['source'], prov['verified'], prov['version']), ('market', True, '0.1.0'))
        entry = next(p for p in out['market']['plugins'] if p['id'] == 'tool')
        self.assertEqual((entry['status'], entry['installed']['origin']), ('installed', 'market'))
        self.assertEqual(self.leftovers(), ([], []))

    def test_checksum_or_size_mismatch_installs_nothing(self):
        data = plugin_zip('tool')
        self.publish('tool', data, sha='0' * 64)
        _, app = self.start()
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual((status, out['detail']['code']), (400, 'plugin_market_checksum'))
        self.publish('tool', data, size=len(data) - 1)
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual((status, out['detail']['code']), (413, 'plugin_market_too_large'))
        self.assertFalse((self.dir / 'tool').exists())
        self.assertEqual(self.leftovers(), ([], []))

    def test_unsafe_archives_are_refused(self):
        cases = {
            'parent': plugin_zip('tool', extra_files={'tool/../../evil.txt': 'x'}),
            'two_tops': plugin_zip('tool', extra_files={'other/x.txt': 'x'}),
            'wrong_id': plugin_zip('tool', top='tool2'),
            'manifest_id': plugin_zip('other', top='tool'),
            'api': plugin_zip('tool', api=99),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('tool/plugin.yaml', 'id: tool\napi: 1\nversion: 0.1.0\nname: x\n')
            link = zipfile.ZipInfo('tool/link')
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(link, '/etc/passwd')
        cases['symlink'] = buf.getvalue()
        _, app = self.start()
        for name, data in cases.items():
            self.publish('tool', data)
            status, out = call(app, 'POST', '/api/plugins/market/tool/install')
            self.assertEqual(status, 400, name)
            self.assertIn(out['detail']['code'], ('plugin_market_bad_archive', 'plugin_market_mismatch',
                                                  'plugin_id_mismatch', 'plugin_market_incompatible'), name)
            self.assertFalse((self.dir / 'tool').exists(), name)
            self.assertEqual(self.leftovers(), ([], []), name)
        self.assertFalse((self.base / 'evil.txt').exists())

    def test_incompatible_entry_is_listed_but_not_installed(self):
        self.publish('future', plugin_zip('future', api=2), api=2)
        _, app = self.start()
        _, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual(view['plugins'][0]['status'], 'incompatible')
        status, out = call(app, 'POST', '/api/plugins/market/future/install')
        self.assertEqual((status, out['detail']['code']), (400, 'plugin_market_incompatible'))

    def test_never_replaces_a_plugin_put_there_by_hand(self):
        (self.dir / 'tool').mkdir(parents=True)
        (self.dir / 'tool' / 'plugin.yaml').write_text('id: tool\napi: 1\nversion: 0.0.1\nname: mine\n',
                                                       encoding='utf-8')
        self.publish('tool', plugin_zip('tool', version='0.2.0'), version='0.2.0')
        _, app = self.start()
        _, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual((view['plugins'][0]['status'], view['plugins'][0]['installed']['origin']), ('update', 'manual'))
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual((status, out['detail']['code']), (409, 'plugin_market_conflict'))
        self.assertIn('0.0.1', (self.dir / 'tool' / 'plugin.yaml').read_text(encoding='utf-8'))
        status, out = call(app, 'DELETE', '/api/plugins/tool')
        self.assertEqual((status, out['detail']['code']), (409, 'plugin_market_not_installed'))
        self.assertTrue((self.dir / 'tool').exists())

    def test_a_shipped_plugin_can_be_overridden_from_the_market(self):
        (self.shipped / 'tool').mkdir(parents=True)
        (self.shipped / 'tool' / 'plugin.yaml').write_text('id: tool\napi: 1\nversion: 0.1.0\nname: x\n',
                                                           encoding='utf-8')
        self.publish('tool', plugin_zip('tool', version='0.2.0'), version='0.2.0')
        manager, app = self.start()
        _, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual((view['plugins'][0]['status'], view['plugins'][0]['installed']['origin']), ('update', 'bundled'))
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual(status, 200, out)
        self.assertEqual(out['plugin']['dir'], plugins.shown(str(self.dir / 'tool')))
        self.assertEqual(out['market']['plugins'][0]['status'], 'installed')

    def test_update_keeps_enabled_flag_and_settings_and_needs_a_restart(self):
        backend = 'def setup(ctx):\n    pass\n'
        self.publish('tool', plugin_zip('tool', backend=backend, extra_files={'old.txt': 'old'}))
        manager, app = self.start()
        self.assertEqual(call(app, 'POST', '/api/plugins/market/tool/install')[0], 200)
        manager.set_enabled('tool', True)
        manager.state.update('tool', settings={'x': 1})
        manager, app = self.start()                                   # loaded now
        self.assertEqual(manager.get('tool').active, True)
        self.publish('tool', plugin_zip('tool', version='0.2.0', backend=backend), version='0.2.0')
        _, view = call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        self.assertEqual(view['plugins'][0]['status'], 'update')
        status, out = call(app, 'POST', '/api/plugins/market/tool/install')
        self.assertEqual(status, 200, out)
        p = out['plugin']
        self.assertEqual((p['state'], p['enabled'], p['restart_needed'], p['pending_version']),
                         ('active', True, True, '0.2.0'))
        self.assertEqual(manager.state.settings('tool'), {'x': 1})
        self.assertFalse((self.dir / 'tool' / 'old.txt').exists())
        self.assertEqual(self.leftovers(), ([], []))
        manager, _ = self.start()
        p = next(q for q in manager.list()['plugins'] if q['id'] == 'tool')
        self.assertEqual((p['state'], p['manifest']['version'], p['restart_needed']), ('active', '0.2.0', False))

    # ---------------- upload and uninstall ----------------

    def test_upload_is_verified_only_when_the_index_lists_its_checksum(self):
        listed = plugin_zip('tool')
        self.publish('tool', listed)
        _, app = self.start()
        call(app, 'GET', '/api/plugins/market', params={'refresh': 'true'})
        status, out = call(app, 'POST', '/api/plugins/market/upload', params={'name': 'tool.zip'}, content=listed,
                           content_type='application/zip')
        self.assertEqual((status, out['verified'], out['plugin']['origin']), (200, True, 'file'), out)
        other = plugin_zip('tool', version='0.1.1')
        status, out = call(app, 'POST', '/api/plugins/market/upload', params={'name': 'tool.zip'}, content=other,
                           content_type='application/zip')
        self.assertEqual((status, out['verified']), (200, False))
        prov = json.loads((self.dir / 'tool' / '.market.json').read_text(encoding='utf-8'))
        self.assertEqual((prov['source'], prov['version'], prov['file']), ('file', '0.1.1', 'tool.zip'))
        status, out = call(app, 'POST', '/api/plugins/market/upload', content=b'not a zip',
                           content_type='application/zip')
        self.assertEqual((status, out['detail']['code']), (400, 'plugin_market_bad_archive'))
        self.assertEqual(self.leftovers(), ([], []))

    def test_uninstall_and_purge(self):
        self.publish('tool', plugin_zip('tool'))
        manager, app = self.start()
        call(app, 'POST', '/api/plugins/market/tool/install')
        manager.set_enabled('tool', True)
        manager.state.update('tool', settings={'x': 1})
        data = self.base / 'plugin_data' / 'tool'
        data.mkdir(parents=True)
        status, out = call(app, 'DELETE', '/api/plugins/tool')
        self.assertEqual(status, 200, out)
        self.assertFalse((self.dir / 'tool').exists())
        self.assertEqual((manager.state.enabled('tool'), manager.state.settings('tool')), (False, {'x': 1}))
        self.assertTrue(data.exists())
        self.assertEqual(out['market']['plugins'][0]['status'], 'available')
        call(app, 'POST', '/api/plugins/market/tool/install')
        status, out = call(app, 'DELETE', '/api/plugins/tool', params={'purge': 'true'})
        self.assertEqual(status, 200, out)
        self.assertEqual(manager.state.entry('tool'), {})
        self.assertFalse(data.exists())

    def test_plugins_cannot_call_the_market_through_ctx_api(self):
        self.publish('tool', plugin_zip('tool'))
        (self.dir / 'spy').mkdir(parents=True)
        (self.dir / 'spy' / 'plugin.yaml').write_text('id: spy\napi: 1\nversion: 0.1.0\nname: spy\n'
                                                      'backend: {module: backend}\n', encoding='utf-8')
        (self.dir / 'spy' / 'backend.py').write_text(textwrap.dedent('''
            def setup(ctx):
                @ctx.router.post('/go')
                async def go():
                    try:
                        await ctx.api.post('/api/plugins/market/tool/install')
                    except PermissionError:
                        return {'status': 'refused'}
                    return {'status': 'called'}
            '''), encoding='utf-8')
        manager, _ = self.start()
        manager.set_enabled('spy', True)
        _, app = self.start()
        self.assertEqual(call(app, 'POST', '/api/ext/spy/go'), (200, {'status': 'refused'}))
        self.assertFalse((self.dir / 'tool').exists())


if __name__ == '__main__':
    unittest.main()
