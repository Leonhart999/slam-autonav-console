"""AI assistant plugin (plugins/ai_assistant): the tool loop against fake OpenAI-compatible and Anthropic servers,
the read-only tools on a fake console API, trajectory summaries, sessions and the drawer transcript.
No network: both model services are local HTTP servers that replay scripted answers."""
import asyncio
from contextlib import closing
import csv
import http.server
import inspect
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI  # noqa: E402

from robot_console import plugins  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class FakeModel:
    """Local HTTP server answering /chat/completions and /v1/messages from a script of JSON answers."""

    def __init__(self):
        self.script, self.requests = [], []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': body})
                status, answer = owner.script.pop(0) if owner.script else (500, {'error': {'message': 'no script'}})
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):              # model lists: OpenAI /v1/models and Anthropic /v1/models
                owner.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': None})
                data = json.dumps(owner.models).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.models = {'data': []}
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def openai_answer(content=None, calls=None, finish='stop'):
    msg = {'role': 'assistant', 'content': content}
    if calls:
        msg['tool_calls'] = [{'id': f'call_{i}', 'type': 'function',
                              'function': {'name': n, 'arguments': json.dumps(a)}} for i, (n, a) in enumerate(calls)]
        finish = 'tool_calls'
    return 200, {'id': 'x', 'model': 'fake-gpt', 'choices': [{'index': 0, 'message': msg, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}


def anthropic_answer(blocks, stop='end_turn'):
    return 200, {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5', 'content': blocks,
                 'stop_reason': stop, 'stop_sequence': None,
                 'usage': {'input_tokens': 50, 'output_tokens': 10, 'cache_read_input_tokens': 0}}


RUNS = {'runs': [
    {'id': 'map_a', 'type': 'mapping', 'started': '2026-11-14 08:58:17', 'algorithm': 'FAST-LIO2', 'preset': 'office',
     'duration': 106.7, 'length': 40.68, 'ape_rmse': 0.030736, 'rpe_rmse': 0.018284, 'maps': [], 'playback': None},
    {'id': 'play_b', 'type': 'playback', 'started': '2026-11-15 00:21:29', 'algorithm': 'FAST-LIO2', 'maps': ['heu'],
     'playback': {'bag_name': 'uploads/HEU61', 'stats': {'z_end': 0.011, 'z_max_abs': 0.126}}},
], 'params': {'max_diff': 0.01, 'rpe_delta': 1.0}}


def console_app():
    app = FastAPI()

    @app.get('/api/evaluation/runs')
    def runs():
        return RUNS

    @app.get('/api/evaluation/runs/{rid}')
    def run(rid: str):
        r = next(x for x in RUNS['runs'] if x['id'] == rid)
        return {**r, 'info': {'id': rid, 'slam_config': {'stack': 'mapping_fastlio', 'params': [
            {'key': 'blind', 'value': 0.4 if rid == 'map_a' else 0.8, 'source': 'base'}]}},
            'eval': {'ape': {'rmse': 0.03}, 'rpe': {'rmse': 0.018}, 'series': {'traj_est': [[0] * 4] * 500}},
            'ref_eval': None}

    @app.get('/api/evaluation/runs/{rid}/trajectory')
    def traj(rid: str):
        n = 101
        return {'id': rid, 't': [i * 0.1 for i in range(n)], 'x': [i * 0.5 for i in range(n)], 'y': [0.0] * n,
                'z': [0.0 if i < 50 else 0.3 for i in range(n)]}

    @app.post('/api/stacks/{name}/start')
    def start(name: str):           # a write route: the tools must never reach it
        raise AssertionError('write route called')

    # batch routes for the run tools: every call is recorded in app.state.calls
    app.state.calls = []

    @app.get('/api/batch/options')
    def batch_options():
        return {'presets': [{'name': 'office', 'label': {'zh': '办公室', 'en': 'Office'}, 'description': ''}],
                'slam': [{'name': 'mapping_fastlio', 'label': 'FAST-LIO2'}], 'max_runs': 20, 'speeds': [1.0, 2.0]}

    @app.get('/api/batch/status')
    def batch_status():
        return {'state': 'idle'}

    @app.post('/api/batch/start')
    def batch_start(body: dict):
        app.state.calls.append(('batch_start', body))
        return {'id': 'batch_x', 'state': 'running', 'preset': body['preset'], 'slam': body['slam'], 'n': body['n'],
                'runs': [], 'phase': 'starting'}

    @app.get('/api/navigation/maps')
    def nav_maps():
        return [{'name': 'office_map', 'preset': 'office'}, {'name': 'heu', 'preset': None}]

    @app.get('/api/navigation/maps/{name}/goals')
    def nav_goals(name: str):
        return {'goals': [{'x': 1.0, 'y': 2.0, 'yaw': 0.0}] if name == 'office_map' else []}

    @app.post('/api/nav-batch/start')
    def nav_batch_start(body: dict):
        app.state.calls.append(('nav_batch_start', body))
        return {'id': 'nav_batch_x', 'state': 'running', 'map': body['map'], 'n': body['n'], 'goals': body['goals']}
    return app


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ai-assistant-test-')
        base = Path(self.tmp.name)
        self.cfg = {'paths': {'data_root': str(base)},
                    'plugins': {'dirs': [str(REPO / 'plugins')], 'state_file': str(base / 'state.json'),
                                'data_dir': str(base / 'plugin_data')}}
        self.env = mock.patch.dict('os.environ', {plugins.NO_PLUGINS_ENV: ''})
        self.env.start()
        self.model = FakeModel()
        manager = plugins.PluginManager(self.cfg)
        manager.set_enabled('ai_assistant', True)

    def tearDown(self):
        self.model.close()
        self.env.stop()
        for name in [m for m in sys.modules if m.startswith(plugins.MODULE_PREFIX + '.')]:
            del sys.modules[name]
        self.tmp.cleanup()

    GENERAL = ('timeout', 'max_tool_rounds', 'instructions')
    API = '/api/ext/ai_assistant'

    def start(self, **settings):
        """Start the plugin; general settings go to the Plugins page settings, the rest becomes the active model
        service profile (provider -> format, model -> the selected model; extra profile fields pass through)."""
        manager = plugins.PluginManager(self.cfg)
        general = {k: v for k, v in settings.items() if k in self.GENERAL}
        if general:
            manager.save_settings('ai_assistant', general)
        app = console_app()
        plugins.register_plugin_routes(app, manager)
        self.assertEqual(manager.list()['plugins'][0]['state'], 'active', manager.list())
        service = {k: v for k, v in settings.items() if k not in self.GENERAL}
        if service:
            profile = {'name': 'test', 'format': service.pop('provider', 'openai'), **service}
            status, body = self.call(app, 'POST', f'{self.API}/providers', {'profile': profile})
            self.assertEqual(status, 200, body)
            self.assertEqual(self.call(app, 'POST', f'{self.API}/providers/{body["id"]}/activate', {})[0], 200)
        return manager, app

    def call(self, app, method, path, body=None):
        return asyncio.run(plugins.asgi_request(app, method, path, body))

    def chat(self, app, message, session=None, access=None):
        req = {'message': message, 'session': session, 'page': '/evaluation'}
        if access:
            req['access'] = access
        status, body = self.call(app, 'POST', '/api/ext/ai_assistant/chat', req)
        self.assertEqual(status, 200, body)
        return [json.loads(line) for line in body.splitlines() if line.strip()] if isinstance(body, str) else body

    def test_not_configured_is_reported_before_any_request(self):
        _, app = self.start()                     # no profile yet
        status, body = self.call(app, 'GET', '/api/ext/ai_assistant/status')
        self.assertEqual((status, body['problem']['code'], body['key_set']), (200, 'no_profile', False))
        status, body = self.call(app, 'POST', '/api/ext/ai_assistant/chat', {'message': 'hi'})
        self.assertEqual((status, body['detail']['code']), (409, 'plugin.ai_assistant.no_profile'))
        _, app = self.start(provider='openai', base_url=self.model.url)        # a profile without a model
        status, body = self.call(app, 'GET', '/api/ext/ai_assistant/status')
        self.assertEqual(body['problem']['code'], 'model_missing')
        self.assertEqual(self.model.requests, [])

    def test_profiles_keep_keys_on_the_server(self):
        _, app = self.start()
        new = {'name': 'Relay', 'format': 'openai', 'base_url': self.model.url + '/v1/', 'api_key': 'sk-secret-123456',
               'models': [{'id': 'a', 'label': 'A', 'effort': 'high', 'efforts': ['max', 'low', 'high', 'low']},
                          {'id': 'b'}], 'color': 'violet'}
        status, p = self.call(app, 'POST', f'{self.API}/providers', {'profile': new})
        self.assertEqual(status, 200, p)
        self.assertNotIn('api_key', p)
        self.assertEqual((p['key_set'], p['key_hint'], p['model'], p['base_url']),
                         (True, 'sk-…3456', 'a', self.model.url + '/v1'))
        self.assertEqual(p['models'][0]['efforts'], ['low', 'high', 'max'])        # canonical order, no duplicates
        self.assertEqual(p['models'][1], {'id': 'b', 'label': 'b', 'effort': '', 'efforts': []})
        path = Path(self.cfg['plugins']['data_dir']) / 'ai_assistant' / 'providers.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('sk-secret', json.dumps(self.call(app, 'GET', f'{self.API}/providers')[1]))
        # an empty key keeps the stored one, clear_key removes it
        status, p2 = self.call(app, 'PUT', f'{self.API}/providers/{p["id"]}', {'profile': {**new, 'api_key': ''}})
        self.assertEqual((status, p2['key_set']), (200, True))
        status, p3 = self.call(app, 'PUT', f'{self.API}/providers/{p["id"]}', {'profile': {**new, 'clear_key': True}})
        self.assertFalse(p3['key_set'])
        # a second profile; the first stays active until switched, unknown models are refused
        _, q = self.call(app, 'POST', f'{self.API}/providers', {'profile': {'name': 'Claude', 'format': 'anthropic'}})
        listing = self.call(app, 'GET', f'{self.API}/providers')[1]
        self.assertEqual((listing['active'], [x['name'] for x in listing['profiles']]), (p['id'], ['Relay', 'Claude']))
        status, body = self.call(app, 'POST', f'{self.API}/providers/{p["id"]}/activate', {'model': 'zzz'})
        self.assertEqual((status, body['detail']['code']), (400, 'plugin.ai_assistant.model_unknown'))
        self.assertEqual(self.call(app, 'POST', f'{self.API}/providers/{p["id"]}/activate', {'model': 'b'})[1]['model'], 'b')
        status, _ = self.call(app, 'POST', f'{self.API}/providers/order', {'ids': [q['id'], p['id']]})
        listing = self.call(app, 'GET', f'{self.API}/providers')[1]
        self.assertEqual([x['name'] for x in listing['profiles']], ['Claude', 'Relay'])
        status, st = self.call(app, 'GET', f'{self.API}/status')
        self.assertEqual((st['name'], st['model'], len(st['profiles'])), ('Relay', 'b', 2))
        # deleting the active profile activates the first remaining one
        listing = self.call(app, 'DELETE', f'{self.API}/providers/{p["id"]}')[1]
        self.assertEqual(listing['active'], q['id'])
        # checks
        bad = [({'name': ''}, 'profile_invalid'), ({'name': 'x', 'base_url': 'ftp://x'}, 'base_url_invalid'),
               ({'name': 'x', 'body': {'messages': []}}, 'body_reserved'),
               ({'name': 'x', 'headers': {'Bad Header': 'v'}}, 'profile_invalid'),
               ({'name': 'x', 'models': [{'id': 'm', 'efforts': ['turbo']}]}, 'profile_invalid'),
               ({'name': 'x', 'format': 'gemini'}, 'provider_unknown')]
        for profile, code in bad:
            status, body = self.call(app, 'POST', f'{self.API}/providers', {'profile': profile})
            self.assertEqual((status, body['detail']['code']), (400, f'plugin.ai_assistant.{code}'), profile)

    def test_request_overrides_reach_the_service(self):
        _, app = self.start(provider='openai', base_url=self.model.url + '/gw/chat/completions', full_url=True,
                            model='m', models=[{'id': 'm', 'effort': 'high'}], api_key='k',
                            user_agent='Mozilla/5.0 test', headers={'X-Provider': 'cc-switch'},
                            body={'temperature': 0.2})
        self.model.script = [openai_answer('ok')]
        self.chat(app, 'x')
        req = self.model.requests[0]
        self.assertEqual(req['path'], '/gw/chat/completions')
        self.assertEqual((req['headers']['X-Provider'], req['headers']['User-Agent']), ('cc-switch', 'Mozilla/5.0 test'))
        self.assertEqual((req['body']['temperature'], req['body']['reasoning_effort']), (0.2, 'high'))

    def test_model_list_and_latency_of_unsaved_profiles(self):
        _, app = self.start(provider='openai', base_url=self.model.url + '/v1', model='m', api_key='sk-stored')
        pid = self.call(app, 'GET', f'{self.API}/status')[1]['active']
        self.model.models = {'object': 'list', 'data': [{'id': 'zeta'}, {'id': 'alpha', 'context_length': 128000}]}
        # the editor's values (not saved) with an empty key field: the stored key is used
        draft = {'name': 'test', 'format': 'openai', 'base_url': self.model.url + '/v1', 'api_key': ''}
        status, body = self.call(app, 'POST', f'{self.API}/probe/models', {'id': pid, 'profile': draft})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['models'], [{'id': 'alpha', 'name': 'alpha', 'context': 128000},
                                          {'id': 'zeta', 'name': 'zeta', 'context': None}])
        get = self.model.requests[-1]
        self.assertEqual((get['path'], get['headers']['Authorization']), ('/v1/models', 'Bearer sk-stored'))
        status, body = self.call(app, 'POST', f'{self.API}/probe/latency',
                                 {'id': pid, 'profile': draft, 'urls': [self.model.url + '/v1', 'http://127.0.0.1:9']})
        fast, dead = body['results']
        self.assertEqual((fast['status'], isinstance(fast['ms'], int), fast['error']), (200, True, None))
        self.assertEqual((dead['status'], dead['ms']), (None, None))
        self.assertTrue(dead['error'])
        # a full URL that is not .../chat/completions has no model list
        status, body = self.call(app, 'POST', f'{self.API}/probe/models',
                                 {'profile': {**draft, 'base_url': self.model.url + '/x', 'full_url': True}})
        self.assertEqual((status, body['detail']['code']), (502, 'plugin.ai_assistant.models_unavailable'))

    def test_old_single_service_settings_become_a_profile(self):
        manager = plugins.PluginManager(self.cfg)
        manager.state.update('ai_assistant', settings={'provider': 'openai', 'base_url': self.model.url,
                                                       'model': 'old-model', 'api_key': 'sk-old', 'timeout': 60})
        _, app = self.start()
        listing = self.call(app, 'GET', f'{self.API}/providers')[1]
        self.assertEqual(len(listing['profiles']), 1)
        p = listing['profiles'][0]
        self.assertEqual((p['id'], p['base_url'], p['model'], p['key_set']),
                         (listing['active'], self.model.url, 'old-model', True))
        _, app = self.start()                                   # only once
        self.assertEqual(len(self.call(app, 'GET', f'{self.API}/providers')[1]['profiles']), 1)

    def test_openai_compatible_tool_loop(self):
        _, app = self.start(provider='openai', base_url=self.model.url + '/v1/', model='fake-gpt', api_key='sk-test')
        self.model.script = [openai_answer('Let me look.', [('list_runs', {'type': 'mapping', 'limit': 5}),
                                                            ('get_run', {'run_id': 'map_a'})]),
                             openai_answer('ATE of map_a is **0.031 m**.')]
        events = self.chat(app, 'What is the latest ATE?')
        kinds = [e['type'] for e in events]
        self.assertEqual(kinds[0], 'session')
        self.assertEqual([e['name'] for e in events if e['type'] == 'tool'], ['list_runs', 'get_run'])
        self.assertTrue(all(e['ok'] for e in events if e['type'] == 'tool_result'))
        self.assertEqual(events[-1]['type'], 'done')
        final = [e for e in events if e['type'] == 'text' and e['final']]
        self.assertEqual(final[0]['text'], 'ATE of map_a is **0.031 m**.')
        # requests: bearer key, tools offered, page context, tool results sent back in OpenAI format
        first, second = self.model.requests
        self.assertEqual(first['path'], '/v1/chat/completions')
        self.assertEqual(first['headers']['Authorization'], 'Bearer sk-test')
        self.assertEqual(len(first['body']['tools']), 18)                  # 14 read + 4 run tools
        self.assertNotIn('tool_choice', first['body'])
        self.assertIn('/evaluation', first['body']['messages'][1]['content'])
        self.assertIn('Tool access for this question: read', first['body']['messages'][1]['content'])
        tool_msgs = [m for m in second['body']['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tool_msgs], ['call_0', 'call_1'])
        runs = json.loads(tool_msgs[0]['content'])
        self.assertEqual([r['id'] for r in runs['runs']], ['map_a'])
        run = json.loads(tool_msgs[1]['content'])
        self.assertNotIn('series', json.dumps(run['eval']))                 # big series stay out
        self.assertEqual(run['trajectory']['z']['end_minus_start'], 0.3)
        self.assertEqual(run['slam_config']['params']['blind'], {'value': 0.4, 'source': 'base'})
        # the conversation continues in the same session, and the drawer can rebuild it
        sid = events[0]['id']
        self.model.script = [openai_answer('Still 0.031 m.')]
        self.chat(app, 'And again?', session=sid)
        self.assertEqual(len([m for m in self.model.requests[-1]['body']['messages'] if m['role'] == 'user']), 2)
        status, view = self.call(app, 'GET', f'/api/ext/ai_assistant/session/{sid}')
        self.assertEqual([i['kind'] for i in view['items']],
                         ['user', 'assistant', 'tool', 'tool', 'assistant', 'user', 'assistant'])
        self.assertEqual(view['items'][0]['text'], 'What is the latest ATE?')     # page prefix removed
        self.assertEqual(view['items'][2]['state'], 'ok')

    def test_bad_tool_calls_go_back_to_the_model_as_errors(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m')
        self.model.script = [openai_answer(None, [('get_run', {}), ('nope', {}), ('list_runs', {'type': 'secret'})]),
                             openai_answer('Sorry.')]
        events = self.chat(app, 'x')
        results = [e for e in events if e['type'] == 'tool_result']
        self.assertEqual([r['ok'] for r in results], [False, False, False])
        self.assertIn('missing argument: run_id', results[0]['error'])
        self.assertIn('unknown tool', results[1]['error'])
        self.assertNotIn('Authorization', self.model.requests[0]['headers'])     # no key -> no header (Ollama)

    def test_tool_round_limit(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m', max_tool_rounds=1)
        self.model.script = [openai_answer(None, [('list_maps', {})]), openai_answer(None, [('list_maps', {})]),
                             openai_answer('Done with what I have.')]
        events = self.chat(app, 'x')
        results = [e for e in events if e['type'] == 'tool_result']
        self.assertEqual([r['ok'] for r in results], [False, False])   # list_maps 404s here, then the limit
        self.assertIn('tool budget', results[1]['error'])
        self.assertEqual(events[-1]['type'], 'done')

    def test_provider_errors_are_coded(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m', api_key='bad')
        self.model.script = [(401, {'error': {'message': 'Incorrect API key provided'}})]
        events = self.chat(app, 'x')
        self.assertEqual(events[-1], {'type': 'error', 'code': 'http_error',
                                      'params': {'status': 401, 'reason': 'Incorrect API key provided'}})
        _, app = self.start(provider='openai', base_url='http://127.0.0.1:9', model='m')
        events = self.chat(app, 'x')
        self.assertEqual(events[-1]['code'], 'connect_failed')

    def test_anthropic_provider_with_the_sdk(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest('anthropic SDK not installed')
        _, app = self.start(provider='anthropic', base_url=self.model.url, api_key='sk-ant-test')
        self.model.script = [
            anthropic_answer([{'type': 'thinking', 'thinking': '', 'signature': 'sig1'},
                              {'type': 'tool_use', 'id': 'toolu_1', 'name': 'list_runs', 'input': {'limit': 1}}],
                             'tool_use'),
            anthropic_answer([{'type': 'text', 'text': 'The newest run is play_b.'}]),
        ]
        self.model.script[0][1]['usage'].update(cache_creation_input_tokens=9, cache_read_input_tokens=6)
        events = self.chat(app, 'newest run?')
        self.assertEqual(events[-1]['usage'], {'input': 100, 'output': 20, 'cache_write': 9,
                                               'cache_read': 6, 'reasoning': 0})
        records = self.get(app, '/usage/export')
        self.assertEqual([(r['cache_write'], r['cost']) for r in csv.DictReader(io.StringIO(records))],
                         [('9', ''), ('0', '')])
        self.assertEqual([e for e in events if e['type'] == 'text'][-1]['text'], 'The newest run is play_b.')
        first, second = self.model.requests
        self.assertEqual(first['path'], '/v1/messages')
        self.assertEqual(first['body']['model'], 'claude-opus-5-5')           # default model
        self.assertEqual(first['body']['cache_control'], {'type': 'ephemeral'})
        self.assertNotIn('fallbacks', first['body'])                           # custom base URL: no beta fallback
        self.assertIn('input_schema', first['body']['tools'][0])
        # thinking block goes back unchanged, tool result as a user tool_result block
        assistant = second['body']['messages'][1]
        self.assertEqual(assistant['content'][0], {'type': 'thinking', 'thinking': '', 'signature': 'sig1'})
        result = second['body']['messages'][2]['content'][0]
        self.assertEqual((result['type'], result['tool_use_id'], result['is_error']), ('tool_result', 'toolu_1', False))

    def test_anthropic_refusal(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest('anthropic SDK not installed')
        _, app = self.start(provider='anthropic', base_url=self.model.url, api_key='k', model='claude-sonnet-5-5',
                            models=[{'id': 'claude-sonnet-5-5', 'effort': 'low'}], headers={'X-Test': '1'},
                            body={'metadata': {'user_id': 'u1'}})
        status, answer = anthropic_answer([], 'refusal')
        answer['stop_details'] = {'type': 'refusal', 'category': 'cyber', 'explanation': None}
        self.model.script = [(status, answer)]
        events = self.chat(app, 'x')
        self.assertEqual(events[-1], {'type': 'error', 'code': 'refusal', 'params': {'category': 'cyber'}})
        req = self.model.requests[0]
        self.assertEqual((req['body']['output_config'], req['body']['metadata'], req['headers']['X-Test']),
                         ({'effort': 'low'}, {'user_id': 'u1'}, '1'))

    def test_message_checks_and_unknown_sessions(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m')
        self.assertEqual(self.call(app, 'POST', '/api/ext/ai_assistant/chat', {'message': '  '})[0], 400)
        self.assertEqual(self.call(app, 'GET', '/api/ext/ai_assistant/session/../../x')[0], 404)
        self.assertEqual(self.call(app, 'GET', '/api/ext/ai_assistant/session/' + 'a' * 32)[0], 404)
        self.model.script = [openai_answer('hello')]
        events = self.chat(app, 'x', session='f' * 32)             # unknown id -> a new conversation
        self.assertNotEqual(events[0]['id'], 'f' * 32)
        status, body = self.call(app, 'POST', '/api/ext/ai_assistant/chat', {'message': 'x', 'access': 'root'})
        self.assertEqual((status, body['detail']['code']), (400, 'plugin.ai_assistant.access_invalid'))

    START = ('start_mapping_batch', {'preset': 'office', 'slam': 'mapping_fastlio', 'n': 3})

    def test_run_tools_follow_the_access_level(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m')
        # read: run tools are refused before the console is called, the model is told how to allow them
        self.model.script = [openai_answer(None, [self.START]), openai_answer('Switch the access menu.')]
        events = self.chat(app, 'run 3 office batches')
        result = next(e for e in events if e['type'] == 'tool_result')
        self.assertEqual((result['ok'], result['decision']), (False, 'blocked'))
        self.assertIn('Ask before running', result['error'])
        self.assertEqual(next(e for e in events if e['type'] == 'tool')['kind'], 'run')
        self.assertEqual(app.state.calls, [])
        # chat: tools are still offered (the history has tool calls) but tool_choice is none
        self.model.script = [openai_answer('Just talking.')]
        self.chat(app, 'hi', access='chat')
        self.assertEqual(self.model.requests[-1]['body']['tool_choice'], 'none')
        self.assertIn('Tool access for this question: chat', self.model.requests[-1]['body']['messages'][-1]['content'])
        # auto: the batch starts without asking; options are readable
        self.model.script = [openai_answer(None, [('get_experiment_options', {})]),
                             openai_answer(None, [self.START]), openai_answer('Started batch_x.')]
        events = self.chat(app, 'run 3 office batches', access='auto')
        results = [e for e in events if e['type'] == 'tool_result']
        self.assertEqual([(r['ok'], r['decision']) for r in results], [(True, None), (True, 'auto')])
        self.assertEqual(app.state.calls, [('batch_start', {'preset': 'office', 'slam': 'mapping_fastlio', 'n': 3,
                                                            'speed': 1, 'save_maps': False})])
        tool_msgs = [m for m in self.model.requests[-1]['body']['messages'] if m['role'] == 'tool']
        options = json.loads(tool_msgs[-2]['content'])
        self.assertEqual(options['mapping_batch']['options']['presets'][0]['label'], 'Office / 办公室')
        self.assertEqual(options['navigation_maps'], [{'name': 'office_map', 'scene': 'office', 'saved_goals': 1},
                                                      {'name': 'heu', 'scene': None}])
        self.assertEqual(json.loads(tool_msgs[-1]['content'])['batch']['id'], 'batch_x')
        # a navigation batch without goals takes the map's saved goals; bad arguments never reach the console
        self.model.script = [openai_answer(None, [('start_nav_batch', {'map': 'office_map', 'n': 2}),
                                                  ('start_mapping_batch', {'preset': 'office', 'slam': 'x', 'n': 0})]),
                             openai_answer('ok')]
        events = self.chat(app, 'nav', access='auto')
        self.assertEqual([e['ok'] for e in events if e['type'] == 'tool_result'], [True, False])
        self.assertEqual(app.state.calls[-1][1]['goals'], [{'x': 1.0, 'y': 2.0, 'yaw': 0.0}])
        self.assertEqual(len(app.state.calls), 2)

    def test_ask_waits_for_the_users_answer(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='m')
        self.model.script = [openai_answer('hello')]
        sid = self.chat(app, 'hi')[0]['id']
        for approve in (False, True):
            self.model.script = [openai_answer(None, [self.START]), openai_answer('done')]
            box = {}
            worker = threading.Thread(target=lambda: box.update(events=self.chat(app, 'go', sid, 'ask')))
            worker.start()
            for _ in range(200):                      # the call waits until the user answers
                status, _ = self.call(app, 'POST', f'{self.API}/chat/confirm',
                                      {'session': sid, 'id': 'call_0', 'approve': approve})
                if status == 200:
                    break
                time.sleep(0.05)
            self.assertEqual(status, 200)
            worker.join(10)
            events = box['events']
            confirm = next(e for e in events if e['type'] == 'confirm')
            self.assertEqual((confirm['name'], confirm['input']['n']), ('start_mapping_batch', 3))
            result = next(e for e in events if e['type'] == 'tool_result')
            self.assertEqual((result['ok'], result['decision']), (approve, 'approved' if approve else 'denied'))
            self.assertEqual(len(app.state.calls), 1 if approve else 0)
        self.assertEqual(self.call(app, 'POST', f'{self.API}/chat/confirm',
                                   {'session': sid, 'id': 'call_0', 'approve': True})[0], 404)
        items = self.call(app, 'GET', f'{self.API}/session/{sid}')[1]['items']
        self.assertEqual([i['text'] for i in items if i['kind'] == 'user'], ['hi', 'go', 'go'])
        self.assertEqual([(i['tool'], i.get('decision')) for i in items if i['kind'] == 'tool'],
                         [('run', 'denied'), ('run', 'approved')])

    def get(self, app, path):
        status, body = self.call(app, 'GET', self.API + path)
        self.assertEqual(status, 200, body)
        return body

    @property
    def data(self):
        return Path(self.cfg['plugins']['data_dir']) / 'ai_assistant'

    def test_round_ledger_and_usage_reports(self):
        price = {'currency': 'CNY', 'input': 10, 'output': 30, 'cache_read': 2}
        _, app = self.start(model='alias', base_url=self.model.url, api_key='secret-api-key',
                            models=[{'id': 'alias', 'price': price, 'effort': 'high'}])
        st = self.get(app, '/status')
        pid = st['active']
        self.assertNotIn('price', st['profiles'][0]['models'][0])      # prices are no longer part of a service
        self.set_price(app, 'alias', **price)
        first = openai_answer('transient', [('list_runs', {})])
        first[1]['usage'].update(prompt_tokens_details={'cached_tokens': 40},
                                 completion_tokens_details={'reasoning_tokens': 6})
        self.model.script = [first, openai_answer('final')]
        events = self.chat(app, 'one?')
        sid = events[0]['id']
        sums = {'input': 160, 'output': 40, 'cache_read': 40, 'cache_write': 0, 'reasoning': 6}
        self.assertEqual(events[-1]['usage'], sums)
        self.assertEqual([e['round'] for e in events if e['type'] == 'usage'], [1, 2])
        self.assertEqual(self.get(app, '/session/' + sid)['usage'], sums)
        # The second turn fails on its second call. Its first call remains billable.
        self.model.script = [openai_answer(None, [('list_runs', {})]), (500, {'error': {'message': 'down'}})]
        self.assertEqual(self.chat(app, 'two?', sid)[-1]['code'], 'http_error')
        # A connection test has no conversation and can run against an unpriced draft.
        answer = openai_answer('OK')
        answer[1]['model'] = 'other'
        self.model.script = [answer]
        self.assertEqual(self.call(app, 'POST', self.API + '/test',
                                   {'profile': {'name': 'draft', 'base_url': self.model.url, 'model': 'other'}})[0], 200)
        export = self.get(app, '/usage/export?format=csv')
        self.assertNotIn('secret-api-key', export)
        rows = list(csv.DictReader(io.StringIO(export)))
        self.assertEqual([(r['round'], r['status']) for r in rows],
                         [('1', 'ok'), ('2', 'ok'), ('1', 'ok'), ('2', 'error'), ('1', 'ok')])
        self.assertEqual((rows[0]['input'], rows[0]['cache_read'], rows[0]['reasoning'], rows[0]['effort']),
                         ('60', '40', '6', 'high'))
        self.assertAlmostEqual(float(rows[0]['cost']), (60*10 + 20*30 + 40*2)/1e6)
        self.assertEqual((rows[3]['model'], rows[3]['error'], rows[3]['input']), ('alias', 'http_error', '0'))
        self.assertEqual((rows[4]['session'], rows[4]['source'], rows[4]['cost']), ('', 'test', ''))
        self.assertEqual(self.get(app, '/usage/summary')['unpriced_models'], ['other'])
        # Deterministic UTC range, timings and a half-hour local bucket boundary.
        with closing(sqlite3.connect(self.data / 'assistant.db')) as db, db:
            db.execute('UPDATE usage SET ts=1800 + (id-1)*3600, ms=id*10')
        window = 'from=1799&to=19800'
        summary = self.get(app, '/usage/summary?' + window)
        self.assertEqual([summary[k] for k in ('calls', 'sessions', 'input', 'errors', 'ms_avg', 'ms_p90')],
                         [5, 1, 360, 1, 30, 50])
        self.assertEqual(summary['cost'][0]['currency'], 'CNY')
        self.assertAlmostEqual(summary['cost'][0]['amount'], sum(float(r['cost'] or 0) for r in rows))
        series = self.get(app, '/usage/series?bucket=hour&tz=-330&' + window + '&profile=' + pid)
        self.assertEqual([p['t'] for p in series['points']], [-1800, 1800, 5400, 9000, 12600, 16200])
        self.assertEqual([p['calls'] for p in series['points']], [0, 1, 1, 1, 1, 0])
        self.assertEqual(series['points'][0]['cost'], [])
        self.assertEqual(series['points'][1]['cache_read'], 40)
        self.assertIn('cache_write', series['points'][1])
        model = self.get(app, '/usage/breakdown?by=model&' + window)['rows']
        self.assertEqual([r['key'] for r in model], ['fake-gpt', 'other', 'alias'])
        group = self.get(app, '/usage/breakdown?by=profile&model=fake-gpt&' + window)['rows'][0]
        self.assertEqual((group['key'], group['label'], group['calls'], group['last']), (pid, 'test', 3, 9000))
        group = self.get(app, '/usage/breakdown?by=session&' + window)['rows']
        self.assertEqual([(r['key'], r['title'], r['session_exists']) for r in group], [(sid, 'one?', True)])
        filters = self.get(app, '/usage/filters')
        self.assertIn({'id': pid, 'name': 'test'}, filters['profiles'])
        self.assertEqual(filters['models'], ['alias', 'fake-gpt', 'other'])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.get(app, '/usage/export?from=5400&to=9000'))))), 1)
        self.assertEqual(self.get(app, '/usage/prune/preview?before=5400'), {'rows': 1})
        self.assertEqual(self.call(app, 'POST', self.API + '/usage/prune', {'before': 5400})[1], {'deleted': 1})
        self.assertEqual(self.get(app, '/sessions')['items'][0]['tokens']['cache'], 0)
        self.assertEqual(self.get(app, '/usage/prune/preview'), {'rows': 4})
        self.assertEqual(self.call(app, 'POST', self.API + '/usage/prune', {'before': None})[1], {'deleted': 4})
        self.assertEqual(self.get(app, '/usage/filters'), {'profiles': [], 'models': []})

    def test_test_call_masks_credentials_and_records_only_error_code(self):
        _, app = self.start(model='m', base_url=self.model.url, api_key='sk-private',
                            headers={'api-key': 'header-private'})
        profile = self.get(app, '/providers')['profiles'][0]
        self.assertNotIn('header-private', json.dumps(profile))
        self.model.script = [(500, {'error': {'message': 'Invalid sk-private / header-private'}})]
        status, error = self.call(app, 'POST', self.API + '/test', {'id': profile['id'], 'profile': profile})
        self.assertEqual(status, 502)
        self.assertNotIn('private', json.dumps(error))
        self.assertEqual(error['detail']['params']['status'], 500)
        headers = {k.lower(): v for k, v in self.model.requests[-1]['headers'].items()}
        self.assertEqual(headers['api-key'], 'header-private')
        rows = list(csv.DictReader(io.StringIO(self.get(app, '/usage/export'))))
        self.assertEqual([(r['source'], r['session'], r['status'], r['error'], r['input']) for r in rows],
                         [('test', '', 'error', 'http_error', '0')])
        self.assertNotIn('private', json.dumps(rows))

    def test_usage_validation_and_failed_write(self):
        _, app = self.start(model='m', base_url=self.model.url)
        for path, field in [('summary?from=nan', 'from'), ('breakdown?to=foo', 'to'), ('breakdown?by=id', 'by'),
                            ('series?tz=0.25', 'tz'), ('series?bucket=week', 'bucket'), ('export?format=md', 'format'),
                            ('prune/preview?before=inf', 'before')]:
            status, body = self.call(app, 'GET', self.API + '/usage/' + path)
            self.assertEqual((status, body['detail']), (400, {'code': 'plugin.ai_assistant.usage_invalid',
                                                              'params': {'field': field}}))
        for payload in [{}, {'before': 'bad'}, {'before': True}]:
            self.assertEqual(self.call(app, 'POST', self.API + '/usage/prune', payload)[0], 400)
        for entry, field in [({'id': 'x', 'currency': 'EUR', 'input': 1}, 'currency'), ({'id': 'x', 'input': -1}, 'input'),
                             ({'id': 'x', 'output': True}, 'output'), ({'id': 'x', 'cache_write': '2'}, 'cache_write'),
                             ({'id': 'x'}, 'input'), ({'id': ' ', 'input': 1}, 'id'), ({'id': 'x', 'input': 2e6}, 'input')]:
            status, body = self.call(app, 'POST', self.API + '/pricing/entries', {'entry': entry})
            self.assertEqual((status, body['detail']['params']), (400, {'field': field}), entry)
        module = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.store']
        self.model.script = [openai_answer('paid')]
        with mock.patch.object(module.Store, 'add_usage', side_effect=sqlite3.OperationalError('disk full')):
            events = self.chat(app, 'hello')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(self.get(app, '/sessions')['items'][0]['preview'], 'paid')

    def test_history_search_edit_export_and_delete(self):
        manager, app = self.start(model='m', base_url=self.model.url)
        self.model.script = [openai_answer('hidden intermediate', [('list_runs', {})]), openai_answer('Find Needle!'),
                             openai_answer('other')]
        a, b = self.chat(app, 'first')[0]['id'], self.chat(app, 'Needle title')[0]['id']
        def patch(sid, body):
            return self.call(app, 'PATCH', self.API + '/sessions/' + sid, body)
        item = patch(a, {'title': 'Saved / notes', 'pinned': True})[1]
        self.assertEqual((item['pinned'], item['turns'], item['preview']), (True, 1, 'Find Needle!'))
        self.assertEqual(item['tokens'], {'input': 200, 'output': 40, 'cache': 0})
        self.model.script = [openai_answer('again'), openai_answer('latest')]
        self.chat(app, 'follow up', a)
        self.chat(app, 'latest question', b)
        listed = self.get(app, '/sessions?limit=1')
        self.assertEqual((listed['total'], listed['items'][0]['id'], listed['items'][0]['title']), (2, a, 'Saved / notes'))
        self.assertEqual(self.get(app, '/sessions?offset=1')['items'][0]['id'], b)
        matches = self.get(app, '/sessions?q=%20nEEdLe%20')['items']
        self.assertEqual([m['id'] for m in matches], [b, a])
        self.assertIn('find needle!', matches[1]['snippet'].lower())
        self.assertEqual(self.get(app, '/sessions?q=saved')['items'][0]['snippet'], '')
        for hidden in ('evaluation', 'intermediate'):
            self.assertEqual(self.get(app, '/sessions?q=' + hidden)['total'], 0)
        md = self.get(app, f'/sessions/{a}/export?format=md')
        for block in ['# Saved / notes', '**You**', '**Assistant**', '> tool: list_runs({}) - ok']:
            self.assertIn(block, md)
        exported = self.get(app, f'/sessions/{a}/export?format=json')
        self.assertEqual(set(exported), {'id', 'created', 'title', 'items'})
        self.assertEqual(exported['items'], self.get(app, '/session/' + a)['items'])
        for body in [{'title': ' '}, {'title': 'a'*121}, {'pinned': 'false'}]:
            self.assertEqual(patch(a, body)[0], 400)
        # Reserve the actual instance used by the routes, without a timing-dependent model call.
        router = manager.get('ai_assistant').context.router
        endpoint = next(r.endpoint for r in router.routes if r.name == 'delete_session')
        sessions = inspect.getclosurevars(endpoint).nonlocals['sessions']
        self.assertTrue(sessions.acquire(a))
        status, body = self.call(app, 'DELETE', self.API + '/sessions/' + a)
        self.assertEqual((status, body['detail']['code']), (409, 'plugin.ai_assistant.session_busy'))
        sessions.release(a)
        for status in [200, 404]:
            self.assertEqual(self.call(app, 'DELETE', self.API + '/sessions/' + a)[0], status)
        self.assertEqual(self.call(app, 'GET', self.API + '/session/' + a)[0], 404)
        groups = self.get(app, '/usage/breakdown?by=session')['rows']
        self.assertFalse(next(r for r in groups if r['key'] == a)['session_exists'])
        for method, path, body in [('PATCH', '/sessions/not-an-id', {}), ('DELETE', '/sessions/not-an-id', None),
                                   ('GET', '/sessions/..%2Fbad/export', None), ('POST', '/chat',
                                    {'message': 'x', 'session': ['invalid']})]:
            self.assertEqual(self.call(app, method, self.API + path, body)[0], 404)

    def test_usage_is_visible_before_turn_finishes(self):
        _, app = self.start(model='m', base_url=self.model.url)
        self.model.script = [openai_answer('hello'), openai_answer(None, [('list_runs', {})]), openai_answer('done')]
        sid = self.chat(app, 'hi')[0]['id']
        provider = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.providers'].OpenAIProvider
        complete = provider.complete
        waiting, resume = threading.Event(), threading.Event()

        def gated(service, system, turns, defs, choice):
            if turns[-1]['role'] == 'tool':
                waiting.set()
                if not resume.wait(10):
                    raise AssertionError('test did not release provider')
            return complete(service, system, turns, defs, choice)

        out = {}
        with mock.patch.object(provider, 'complete', gated):
            worker = threading.Thread(target=lambda: out.update(events=self.chat(app, 'go', sid)))
            worker.start()
            try:
                self.assertTrue(waiting.wait(10))
                self.assertEqual(self.get(app, '/usage/summary')['calls'], 2)
                result = self.call(app, 'PATCH', self.API + '/sessions/' + sid, {'pinned': True, 'title': 'While busy'})
                self.assertEqual(result[0], 200)
            finally:
                resume.set()
                worker.join(10)
        self.assertEqual(out['events'][-1]['type'], 'done')
        item = self.get(app, '/sessions')['items'][0]
        self.assertEqual((item['title'], item['pinned'], item['preview'], item['turns']), ('While busy', True, 'done', 2))

    def test_pinned_survives_history_pruning(self):
        _, app = self.start(model='m', base_url=self.model.url)
        mod = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.sessions']
        self.model.script = [openai_answer('ok') for _ in range(4)]
        with mock.patch.object(mod, 'KEEP', 2):
            pinned = self.chat(app, 'pin me')[0]['id']
            self.call(app, 'PATCH', self.API + '/sessions/' + pinned, {'pinned': True})
            ids = [self.chat(app, q)[0]['id'] for q in ('oldest', 'newer', 'newest')]
            self.assertFalse((self.data / 'sessions' / (ids[0] + '.json')).exists())
            self.assertEqual([r['id'] for r in self.get(app, '/sessions')['items']], [pinned, ids[2], ids[1]])
            self.assertEqual(self.get(app, '/usage/summary')['calls'], 4)

    def test_backfill_from_legacy_json(self):
        sid = 'a'*32
        folder = self.data / 'sessions'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (sid + '.json')
        path.write_text(json.dumps({'id': sid, 'title': 'Legacy', 'created': '2020-06-01 21:00:00',
                                    'turns': [{'role': 'user', 'text': '[Console page currently open: path]\nQuestion'},
                                              {'role': 'assistant', 'text': 'Answer'}]}), encoding='utf-8')
        os.utime(path, (1700000000, 1700000000))
        _, app = self.start()
        item = self.get(app, '/sessions?q=question')['items'][0]
        self.assertEqual((item['updated'], item['turns'], item['preview'], item['tokens']['input']),
                         (1700000000, 1, 'Answer', 0))
        self.assertEqual(time.strftime('%Y-%m-%d', time.localtime(item['created'])), '2020-06-01')
        self.assertEqual(self.get(app, '/sessions?q=path')['total'], 0)
        with closing(sqlite3.connect(self.data / 'assistant.db')) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_model_and_effort_from_the_composer(self):
        _, app = self.start(provider='openai', base_url=self.model.url, model='a',
                            models=[{'id': 'a'}, {'id': 'b', 'effort': 'low', 'efforts': ['low', 'high', 'ultra']}])
        pid = self.call(app, 'GET', f'{self.API}/status')[1]['active']
        status, p = self.call(app, 'POST', f'{self.API}/providers/{pid}/activate', {'model': 'b', 'effort': 'high'})
        self.assertEqual((status, p['model'], p['models'][1]['effort']), (200, 'b', 'high'))
        st = self.call(app, 'GET', f'{self.API}/status')[1]
        self.assertEqual((st['model'], st['effort'], st['profiles'][0]['models'][1]['effort'],
                          st['profiles'][0]['models'][1]['efforts']), ('b', 'high', 'high', ['low', 'high', 'ultra']))
        status, body = self.call(app, 'POST', f'{self.API}/providers/{pid}/activate', {'effort': 'turbo'})
        self.assertEqual(status, 400)
        self.model.script = [openai_answer('ok')]
        self.chat(app, 'x')
        self.assertEqual((self.model.requests[-1]['body']['model'], self.model.requests[-1]['body']['reasoning_effort']),
                         ('b', 'high'))

    def test_deep_credentials_and_proxy_userinfo_never_leak(self):
        _, app = self.start(
            model='m', base_url=self.model.url, api_key='sk-main-secret',
            proxy='http://alice:proxy-pass-9@127.0.0.1:19999',
            headers={'Authorization': 'Bearer hdr-token-7'},
            body={'nested': {'client_secret': 'cs-secret-1', 'refresh_token': 'rt-secret-2'},
                  'access_token': 'at-secret-3'})
        profile = self.get(app, '/providers')['profiles'][0]
        dumped = json.dumps(profile) + json.dumps(self.get(app, '/status'))
        for leaked in ('sk-main-secret', 'proxy-pass-9', 'hdr-token-7', 'cs-secret-1', 'rt-secret-2', 'at-secret-3'):
            self.assertNotIn(leaked, dumped)
        # Re-saving the masked view keeps both the body secrets and the proxy password intact.
        status, updated = self.call(app, 'PUT', f'{self.API}/providers/{profile["id"]}', {'profile': profile})
        self.assertEqual(status, 200)
        self.assertNotIn('proxy-pass-9', json.dumps(updated))
        stored = json.loads((self.data / 'providers.json').read_text(encoding='utf-8'))['profiles'][0]
        self.assertEqual(stored['proxy'], 'http://alice:proxy-pass-9@127.0.0.1:19999')
        self.assertEqual(stored['body']['nested']['client_secret'], 'cs-secret-1')

        # Remove proxy so FakeModel answers and echoes every secret back in a 500 error.
        stored_copy = {**profile, 'proxy': ''}
        self.call(app, 'PUT', f'{self.API}/providers/{profile["id"]}', {'profile': stored_copy})
        self.model.script = [(500, {'error': {'message': 'bad cs-secret-1 rt-secret-2 at-secret-3 Bearer tok-extra'}}),
                             (500, {'error': {'message': 'bad cs-secret-1 rt-secret-2 at-secret-3'}})]
        status, err = self.call(app, 'POST', f'{self.API}/test', {})
        self.assertEqual(status, 502)
        for leaked in ('cs-secret-1', 'rt-secret-2', 'at-secret-3', 'tok-extra'):
            self.assertNotIn(leaked, json.dumps(err))
        events = self.chat(app, 'hi')
        for leaked in ('cs-secret-1', 'rt-secret-2', 'at-secret-3'):
            self.assertNotIn(leaked, json.dumps(events))

    def test_r2_header_names_userinfo_restore_and_decoded_proxy_password(self):
        # Short gateway header names are credentials too; URL userinfo survives a save of the masked view.
        _, app = self.start(provider='anthropic', base_url='https://relay-user:relay-pass@relay.example',
                            api_key='sk-ant-x', headers={'X-Auth': 'xauth-secret', 'X-Trace': 'keep-me'},
                            proxy='http://bob:p%40ss%3Aw0rd@127.0.0.1:1')
        profile = self.get(app, '/providers')['profiles'][0]
        dumped = json.dumps(profile)
        for leaked in ('xauth-secret', 'relay-pass', 'p%40ss', 'p@ss:w0rd'):
            self.assertNotIn(leaked, dumped)
        self.assertEqual(profile['headers']['X-Trace'], 'keep-me')
        status, _ = self.call(app, 'PUT', f'{self.API}/providers/{profile["id"]}', {'profile': {**profile, 'name': 'Renamed'}})
        self.assertEqual(status, 200)
        stored = json.loads((self.data / 'providers.json').read_text(encoding='utf-8'))['profiles'][0]
        self.assertEqual((stored['name'], stored['base_url'], stored['headers']['X-Auth'], stored['proxy']),
                         ('Renamed', 'https://relay-user:relay-pass@relay.example', 'xauth-secret',
                          'http://bob:p%40ss%3Aw0rd@127.0.0.1:1'))
        mod = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.profiles']
        settings = mod.settings_for(stored, {})
        reason = mod.safe_error({'reason': 'proxy auth failed for bob:p@ss:w0rd and relay-pass'}, settings)['reason']
        for leaked in ('p@ss:w0rd', 'relay-pass'):
            self.assertNotIn(leaked, reason)

    def test_r2_gateway_cache_write_is_not_billed_as_input(self):
        # CPA (Claude behind Chat Completions): prompt_tokens = input + cache read + cache write
        price = {'currency': 'USD', 'input': 3, 'cache_read': 0.3, 'cache_write': 3.75, 'output': 15}
        _, app = self.start(model='c', base_url=self.model.url, models=[{'id': 'c'}])
        self.set_price(app, 'c', **price)
        answer = openai_answer('ok')
        answer[1]['usage'] = {'prompt_tokens': 170, 'completion_tokens': 10,
                              'prompt_tokens_details': {'cached_tokens': 50, 'cache_write_tokens': 20}}
        self.model.script = [answer]
        usage = self.chat(app, 'hi')[-1]['usage']
        self.assertEqual((usage['input'], usage['cache_read'], usage['cache_write']), (100, 50, 20))
        row = list(csv.DictReader(io.StringIO(self.get(app, '/usage/export'))))[0]
        self.assertAlmostEqual(float(row['cost']), (100 * 3 + 50 * 0.3 + 20 * 3.75 + 10 * 15) / 1e6)

    def set_price(self, app, mid, **price):
        status, body = self.call(app, 'POST', self.API + '/pricing/entries', {'entry': {'id': mid, **price}})
        self.assertEqual(status, 200, body)
        return body

    def pricing_module(self):
        return sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.pricing']

    CATALOG = {
        'anthropic': {'id': 'anthropic', 'name': 'Anthropic', 'models': {
            'claude-opus-5-5': {'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5',
                                'cost': {'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25}},
            'claude-opus-5': {'id': 'claude-opus-5', 'name': 'Claude Opus 5', 'cost': {'input': 15, 'output': 75}},
            'no-price': {'id': 'no-price', 'name': 'Free?'}}},
        'openai': {'id': 'openai', 'name': 'OpenAI', 'models': {
            'gpt-5': {'id': 'gpt-5', 'name': 'GPT-5', 'cost': {'input': 1.25, 'output': 10, 'cache_read': 0.125}},
            'claude-opus-5': {'id': 'claude-opus-5', 'name': 'dup', 'cost': {'input': 1, 'output': 1}}}},
        'openrouter': {'id': 'openrouter', 'name': 'OpenRouter', 'models': {
            'claude-opus-5-5': {'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5 (OR)', 'cost': {'input': 9, 'output': 9}}}},
        'bad id!': {'models': {'x': {'cost': {'input': 1}}}},
    }

    def test_pricing_migrates_old_per_model_prices_once(self):
        self.data.mkdir(parents=True)
        (self.data / 'providers.json').write_text(json.dumps({'version': 1, 'active': 'p1', 'profiles': [
            {'id': 'p1', 'name': 'old', 'format': 'openai', 'base_url': self.model.url, 'model': 'm', 'models': [
                {'id': 'm', 'label': 'My M', 'effort': '', 'efforts': [], 'price': {'currency': 'CNY', 'input': 2, 'output': 8}},
                {'id': 'n', 'label': 'n', 'effort': '', 'efforts': [], 'price': {'currency': 'USD'}}]}]}))
        _, app = self.start()
        self.call(app, 'PATCH', self.API + '/pricing/sync', {'enabled': False})
        entries = self.get(app, '/pricing')['entries']
        self.assertEqual([(e['id'], e['name'], e['currency'], e['input'], e['output'], e['cache_read'], e['source'])
                          for e in entries], [('m', 'My M', 'CNY', 2.0, 8.0, None, 'manual')])
        self.call(app, 'POST', self.API + '/pricing/entries/delete', {'ids': ['m']})
        _, app = self.start()                      # the table exists now: nothing is migrated again
        self.assertEqual(self.get(app, '/pricing')['entries'], [])

    def test_pricing_sync_import_hide_and_backfill(self):
        _, app = self.start(model='relay--claude-opus-5-5-kiro', base_url=self.model.url)
        answer = openai_answer('hi')
        answer[1]['model'] = 'relay--claude-opus-5-5-kiro'
        answer[1]['usage'] = {'prompt_tokens': 1000, 'completion_tokens': 100}
        self.model.script = [answer]
        self.chat(app, 'one')
        self.assertEqual(self.get(app, '/usage/summary')['unpriced_models'], ['relay--claude-opus-5-5-kiro'])
        mod = self.pricing_module()
        with mock.patch.object(mod, 'fetch_raw', return_value=self.CATALOG) as fetch:
            self.call(app, 'PATCH', self.API + '/pricing/sync', {'providers': ['anthropic', 'openai']})
            listing = self.get(app, '/pricing')                     # never synced: starts a sync in the background
            for _ in range(100):
                if listing['sync']['last'] and not listing['sync']['running']:
                    break
                time.sleep(0.05)
                listing = self.get(app, '/pricing')
            self.assertEqual(fetch.call_count, 1)
            entries = {e['id']: e for e in listing['entries']}
            self.assertEqual(sorted(entries), ['claude-opus-5', 'claude-opus-5-5', 'gpt-5'])
            # the first provider in the list wins a shared model id; models without a price are left out
            self.assertEqual((entries['claude-opus-5']['provider'], entries['claude-opus-5']['input']), ('anthropic', 15.0))
            self.assertEqual(entries['gpt-5']['cache_write'], None)
            self.assertEqual(listing['sync']['provider_names'], {'anthropic': 'Anthropic', 'openai': 'OpenAI'})
            self.assertFalse(self.get(app, '/pricing')['sync']['running'])          # not due again for a day
            self.assertEqual(fetch.call_count, 1)
            # the unpriced call got the longest matching price (claude-opus-5-5, not claude-opus-5)
            row = list(csv.DictReader(io.StringIO(self.get(app, '/usage/export'))))[0]
            self.assertAlmostEqual(float(row['cost']), (1000 * 5 + 100 * 25) / 1e6)
            self.assertEqual(row['currency'], 'USD')
            # a deleted synced entry stays away until unhidden; an edited one becomes manual and is kept
            self.call(app, 'POST', self.API + '/pricing/entries/delete', {'ids': ['gpt-5']})
            edited = self.call(app, 'POST', self.API + '/pricing/entries',
                               {'old_id': 'claude-opus-5', 'entry': {'id': 'claude-opus-5', 'input': 1, 'output': 2}})[1]
            self.assertEqual(edited['source'], 'manual')
            listing = self.call(app, 'POST', self.API + '/pricing/sync')[1]
            entries = {e['id']: e for e in listing['entries']}
            self.assertNotIn('gpt-5', entries)
            self.assertEqual((listing['hidden'], entries['claude-opus-5']['input']), (1, 1.0))
            listing = self.call(app, 'PATCH', self.API + '/pricing/sync', {'unhide': True})[1]
            self.assertEqual(listing['hidden'], 0)
            self.assertIn('gpt-5', {e['id'] for e in self.call(app, 'POST', self.API + '/pricing/sync')[1]['entries']})
            # import one model from a provider outside the list; it replaces the synced one and is refreshed by sync
            found = self.get(app, '/pricing/catalog?q=opus-5-5&provider=openrouter')
            self.assertEqual([(m['provider'], m['id'], m['added']) for m in found['models']],
                             [('openrouter', 'claude-opus-5-5', True)])
            self.assertNotIn('bad id!', [p['id'] for p in found['providers']])
            status, body = self.call(app, 'POST', self.API + '/pricing/import',
                                     {'models': [{'provider': 'openrouter', 'id': 'claude-opus-5-5'}]})
            self.assertEqual((status, body['imported']), (200, 1))
            listing = self.call(app, 'PATCH', self.API + '/pricing/sync', {'providers': ['openai']})[1]
            entries = {e['id']: e for e in listing['entries']}
            self.assertEqual(sorted(entries), ['claude-opus-5', 'claude-opus-5-5', 'gpt-5'])
            self.assertEqual((entries['claude-opus-5-5']['source'], entries['claude-opus-5-5']['input']), ('import', 9.0))
            self.assertEqual(entries['claude-opus-5']['source'], 'manual')
            status, body = self.call(app, 'POST', self.API + '/pricing/import', {'models': [{'provider': 'x', 'id': 'y'}]})
            self.assertEqual((status, body['detail']['code']), (404, 'plugin.ai_assistant.pricing_not_found'))
            # the earlier record keeps its price (a snapshot)
            row = list(csv.DictReader(io.StringIO(self.get(app, '/usage/export'))))[0]
            self.assertAlmostEqual(float(row['cost']), (1000 * 5 + 100 * 25) / 1e6)
        with mock.patch.object(mod, 'fetch_raw', side_effect=OSError('offline')):
            status, body = self.call(app, 'POST', self.API + '/pricing/sync')
            self.assertEqual((status, body['detail']['code']), (502, 'plugin.ai_assistant.pricing_fetch_failed'))
            self.assertEqual(self.get(app, '/pricing')['sync']['error'], 'offline')
        status, body = self.call(app, 'POST', self.API + '/pricing/entries', {'entry': {'id': 'gpt-5', 'input': 1}})
        self.assertEqual((status, body['detail']['code']), (409, 'plugin.ai_assistant.pricing_exists'))
        for payload in [{'providers': ['ok', 'not ok']}, {'enabled': 'yes'}, {'providers': 'openai'}]:
            self.assertEqual(self.call(app, 'PATCH', self.API + '/pricing/sync', payload)[0], 400)

    def test_r2_half_hour_dst_hour_buckets(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        _, app = self.start(model='m', base_url=self.model.url)
        self.model.script = [openai_answer(str(i)) for i in range(4)]
        for _ in range(4):
            self.chat(app, 'go')
        lh = ZoneInfo('Australia/Lord_Howe')        # +10:30 -> +11:00 at 02:00 local on 2026-10-04
        stamps = [datetime(2026, 10, 4, h, m, tzinfo=lh).timestamp() for h, m in ((1, 30), (2, 45), (3, 15), (4, 15))]
        with closing(sqlite3.connect(self.data / 'assistant.db')) as db, db:
            for i, ts in enumerate(stamps, 1):
                db.execute('UPDATE usage SET ts=? WHERE id=?', (ts, i))
        start = int(datetime(2026, 10, 4, 0, 0, tzinfo=lh).timestamp())
        end = int(datetime(2026, 10, 4, 6, 0, tzinfo=lh).timestamp())
        window = f'from={start}&to={end}'
        series = self.get(app, f'/usage/series?bucket=hour&tz_name=Australia/Lord_Howe&{window}')
        self.assertEqual(sum(p['calls'] for p in series['points']), self.get(app, '/usage/summary?' + window)['calls'])
        self.assertEqual(sum(p['calls'] for p in series['points']), 4)
        self.assertEqual(len({p['t'] for p in series['points']}), len(series['points']))

    def test_r2_pinned_survives_failed_index_then_prune(self):
        _, app = self.start(model='m', base_url=self.model.url)
        mod = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.sessions']
        store_mod = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.store']
        self.model.script = [openai_answer('ok') for _ in range(4)]
        with mock.patch.object(mod, 'KEEP', 3):          # the fourth conversation is the one that prunes
            oldest = self.chat(app, 'oldest')[0]['id']
            self.chat(app, 'middle')
            self.chat(app, 'newer')
            real = store_mod.Store.index
            with mock.patch.object(store_mod.Store, 'index', side_effect=sqlite3.OperationalError('locked')):
                status, _ = self.call(app, 'PATCH', self.API + '/sessions/' + oldest, {'pinned': True})
            self.assertEqual(status, 500)
            self.assertTrue(json.loads((self.data / 'sessions' / (oldest + '.json')).read_text())['pinned'])
            self.assertIs(store_mod.Store.index, real)
            self.chat(app, 'newest')                 # its save prunes: the pinned JSON must survive
            self.assertTrue((self.data / 'sessions' / (oldest + '.json')).exists())
            items = self.get(app, '/sessions')['items']
            self.assertEqual((items[0]['id'], items[0]['pinned']), (oldest, True))

    def test_csv_export_neutralises_formula_prefixes(self):
        _, app = self.start(name='=CMD|evil', model='+SUM(1,1)', base_url=self.model.url)
        ans = openai_answer('ok')
        ans[1]['model'] = '-2+3'
        self.model.script = [ans]
        self.chat(app, 'hi')
        rows = list(csv.DictReader(io.StringIO(self.get(app, '/usage/export?format=csv'))))
        self.assertEqual(rows[0]['profile_name'], "'=CMD|evil")
        self.assertEqual(rows[0]['model'], "'-2+3")
        self.assertEqual(rows[0]['input'], '100')

    def test_usage_all_time_dst_and_cache_write(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        _, app = self.start(provider='anthropic', base_url=self.model.url, api_key='sk-ant-test')
        ny = ZoneInfo('America/New_York')
        # Across the fall-back transition, the empty daily chart still covers 30 local calendar days.
        end = int(datetime(2026, 11, 25, 0, 30, tzinfo=ny).timestamp())
        empty_all = self.get(app, f'/usage/series?from=0&to={end}&bucket=day&tz_name=America/New_York')
        self.assertEqual(len(empty_all['points']), 30)
        self.assertEqual(empty_all['points'][0]['t'], int(datetime(2026, 10, 27, tzinfo=ny).timestamp()))

        self.model.script = [anthropic_answer([{'type': 'text', 'text': 'a'}]),
                             anthropic_answer([{'type': 'text', 'text': 'b'}]),
                             anthropic_answer([{'type': 'text', 'text': 'c'}]),
                             anthropic_answer([{'type': 'text', 'text': 'd'}])]
        for a in self.model.script:
            a[1]['usage']['cache_creation_input_tokens'] = 500
        for _ in range(4):
            self.chat(app, 'go')

        stamps = [
            datetime(2026, 10, 31, 23, 30, tzinfo=ny).timestamp(),  # EDT
            datetime(2026, 11, 1, 0, 30, tzinfo=ny).timestamp(),    # EDT (fall-back day, 25h)
            datetime(2026, 11, 1, 23, 30, tzinfo=ny).timestamp(),   # EST
            datetime(2026, 11, 2, 0, 30, tzinfo=ny).timestamp(),    # EST
        ]
        with closing(sqlite3.connect(self.data / 'assistant.db')) as db, db:
            for idx, ts in enumerate(stamps, 1):
                db.execute('UPDATE usage SET ts=? WHERE id=?', (ts, idx))
        end = int(datetime(2026, 11, 3, 0, 0, tzinfo=ny).timestamp())
        res = self.get(app, f'/usage/series?from=0&to={end}&bucket=day&tz_name=America/New_York')
        self.assertEqual([(p['calls'], p['cache_write']) for p in res['points']],
                         [(1, 500), (2, 1000), (1, 500)])

    def test_reconcile_recovers_failed_index_and_delete_rollback(self):
        _, app = self.start(model='m', base_url=self.model.url)
        self.model.script = [openai_answer('first'), openai_answer('second')]
        s1 = self.chat(app, 'one')[0]['id']
        store_mod = sys.modules[plugins.MODULE_PREFIX + '.ai_assistant.store']
        with mock.patch.object(store_mod.Store, 'index', side_effect=sqlite3.OperationalError('locked')):
            ev = self.chat(app, 'two')
        s2 = ev[0]['id']
        self.assertEqual(ev[-1]['type'], 'done')
        # Calling /sessions triggers reconcile without requiring a server restart.
        items = self.get(app, '/sessions')['items']
        self.assertEqual({x['id'] for x in items}, {s1, s2})
        item2 = next(x for x in items if x['id'] == s2)
        self.assertEqual(item2['tokens']['input'], 100)

        # Failed SQLite deletion restores the JSON file so nothing is half-deleted.
        with mock.patch.object(store_mod.Store, 'delete_index', side_effect=sqlite3.OperationalError('locked')):
            status, _ = self.call(app, 'DELETE', f'{self.API}/sessions/{s2}')
            self.assertEqual(status, 500)
        self.assertTrue((self.data / 'sessions' / f'{s2}.json').is_file())
        self.assertEqual(self.call(app, 'GET', f'{self.API}/session/{s2}')[0], 200)
        self.assertEqual(self.call(app, 'DELETE', f'{self.API}/sessions/{s2}')[0], 200)

        # Restart with a manually removed file + an unindexed file on a non-empty DB reconciles both.
        (self.data / 'sessions' / f'{s1}.json').unlink()
        s3 = 'c' * 32
        (self.data / 'sessions' / f'{s3}.json').write_text(json.dumps({
            'id': s3, 'title': 'Recovered', 'created': '2026-11-20 10:00:00',
            'turns': [{'role': 'user', 'text': 'hi'}, {'role': 'assistant', 'text': 'hey'}]}), encoding='utf-8')
        self.app2 = FastAPI()
        mgr = plugins.PluginManager(self.cfg)
        self.addCleanup(mgr.shutdown)
        plugins.register_plugin_routes(self.app2, mgr)
        _, after = asyncio.run(plugins.asgi_request(self.app2, 'GET', f'{self.API}/sessions'))
        self.assertEqual([x['id'] for x in after['items']], [s3])


class SummaryTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / 'plugins'))
        import importlib
        self.tools = importlib.import_module('ai_assistant.backend.tools')

    def tearDown(self):
        sys.path.remove(str(REPO / 'plugins'))
        for name in [m for m in sys.modules if m.startswith('ai_assistant')]:
            del sys.modules[name]

    def test_height_profile(self):
        n = 201                                   # 100 m straight, a 0.5 m height step between 40 and 41 m
        tr = {'t': [i * 0.1 for i in range(n)], 'x': [i * 0.5 for i in range(n)], 'y': [0.0] * n,
              'z': [0.0 if i <= 80 else 0.5 if i >= 82 else 0.25 for i in range(n)]}
        s = self.tools.trajectory_summary(tr)
        self.assertAlmostEqual(s['distance_m'], 100.0, delta=0.6)
        self.assertEqual(s['z']['end_minus_start'], 0.5)
        self.assertAlmostEqual(s['z']['per_100m'], 0.5, delta=0.01)
        self.assertEqual(s['z']['at_distance']['20m']['z'], 0.0)
        self.assertEqual(s['z']['at_distance']['50m']['z'], 0.5)
        step = s['z']['steepest_2m_window']
        self.assertEqual(step['dz'], 0.5)
        self.assertTrue(38 <= step['from_m'] <= 41, step)
        self.assertEqual(self.tools.trajectory_summary({'t': [0], 'x': [0], 'y': [0], 'z': [0]}), {'poses': 1})
        samples = self.tools.sampled_trajectory(tr, 11)
        self.assertEqual(len(samples), 11)
        self.assertEqual(samples[-1][4], 0.5)

    def test_only_get_routes(self):
        source = (REPO / 'plugins' / 'ai_assistant' / 'backend' / 'tools.py').read_text()
        self.assertNotIn('api.post', source)
        self.assertNotIn('api.put', source)
        self.assertNotIn('api.delete', source)


if __name__ == '__main__':
    unittest.main()


class PricingLookupTest(unittest.TestCase):
    """Matching a call's model ids against the pricing table (no plugin, no network)."""

    def setUp(self):
        sys.path.insert(0, str(REPO / 'plugins'))
        import importlib
        self.mod = importlib.import_module('ai_assistant.backend.pricing')
        self.tmp = tempfile.TemporaryDirectory(prefix='ai-pricing-test-')
        self.table = self.mod.Pricing(os.path.join(self.tmp.name, 'p.json'), os.path.join(self.tmp.name, 'c.json'))
        for mid, rate in [('claude-opus-5', 15), ('claude-opus-5-5', 5), ('claude-3-5-haiku-20241022', 0.8),
                          ('gpt-5', 1.25), ('o3', 2), ('Alias-Model', 7)]:
            self.table.save_entry({'id': mid, 'input': rate})

    def tearDown(self):
        sys.path.remove(str(REPO / 'plugins'))
        for name in [m for m in sys.modules if m.startswith('ai_assistant')]:
            del sys.modules[name]
        self.tmp.cleanup()

    def found(self, *ids):
        e = self.table.lookup(*ids)
        return e['id'] if e else None

    def test_lookup_order(self):
        self.assertEqual(self.found('claude-opus-5'), 'claude-opus-5')
        self.assertEqual(self.found('alias-model'), 'Alias-Model')                      # case
        self.assertEqual(self.found('anthropic/claude-opus-5-5'), 'claude-opus-5-5')    # vendor prefix
        self.assertEqual(self.found('claude-opus-5-5-20261001'), 'claude-opus-5-5')     # date suffix of the call
        self.assertEqual(self.found('claude-3-5-haiku'), 'claude-3-5-haiku-20241022')   # date suffix of the entry
        self.assertEqual(self.found('relay--claude-opus-5-5-kiro'), 'claude-opus-5-5')  # longest part wins
        self.assertEqual(self.found('xclaude-opus-5'), None)                            # not between separators
        self.assertEqual(self.found('o3-pro'), None)                                    # parts shorter than 4 are ignored
        self.assertEqual(self.found('gpt-5', 'claude-opus-5'), 'gpt-5')                 # the reply's model first
        self.assertEqual(self.found('relay--gpt-5-x', 'claude-opus-5'), 'claude-opus-5')  # an exact id beats a part
        self.assertEqual(self.found('', None), None)

    def test_cost(self):
        e = {'input': 2.0, 'output': None, 'cache_read': 0.5, 'cache_write': None}
        self.assertAlmostEqual(self.mod.entry_cost(e, {'input': 1000, 'output': 500, 'cache_read': 2000}), 0.003)
        self.assertIsNone(self.mod.entry_cost({f: None for f in self.mod.FIELDS}, {'input': 1}))
