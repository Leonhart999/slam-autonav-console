"""AI assistant plugin (plugins/ai_assistant): the tool loop against fake OpenAI-compatible and Anthropic servers,
the read-only tools on a fake console API, trajectory summaries, sessions and the drawer transcript.
No network: both model services are local HTTP servers that replay scripted answers."""
import asyncio
import http.server
import json
from pathlib import Path
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
        events = self.chat(app, 'newest run?')
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
