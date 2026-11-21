"""Model services the user connects: OpenAI-compatible Chat Completions (plain HTTP) and the Anthropic Messages API
(official `anthropic` SDK).

Both turn the conversation (the provider-neutral turns of sessions.py) into their own request format and return a
Reply. A provider-native copy of each assistant turn is kept (`raw`) and sent back unchanged to the same provider,
so provider-specific parts (Claude's thinking blocks, DeepSeek's reasoning_content) survive between rounds.

Settings (one profile of profiles.py, flattened by profiles.settings_for): provider, base_url, full_url (OpenAI
only: base_url is the complete /chat/completions endpoint), api_key, model, effort (reasoning effort of the model:
OpenAI `reasoning_effort`, Anthropic `output_config.effort`), proxy, user_agent, headers and body (extra request
headers / top-level body fields, applied last), timeout.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request

ANTHROPIC_DEFAULT_MODEL = 'claude-opus-5-5'
OPENAI_DEFAULT_URL = 'https://api.openai.com/v1'
# Claude models that take the server-side refusal fallback (`fallbacks: "default"`) on Anthropic's own API
FALLBACK_MODELS = {'claude-fable-5-2', 'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'}
MAX_TOKENS = 16000


class ProviderError(Exception):
    """A coded problem with the model service; `code` is translated by the plugin's messages."""

    def __init__(self, code, **params):
        super().__init__(f'{code} {params}')
        self.code = code
        self.params = params


class Reply:
    """One model response. stop: end | tool | max_tokens | refusal | pause."""

    def __init__(self, text, tool_calls, stop, raw, usage=None, model='', detail=''):
        self.text = text
        self.tool_calls = tool_calls        # [{'id', 'name', 'input'}]
        self.stop = stop
        self.raw = raw                      # provider-native assistant message content
        self.usage = usage or {}
        self.model = model
        self.detail = detail                # e.g. the refusal category


def _short(text, n=400):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n] + '…'


def make_provider(settings):
    kind = settings.get('provider') or 'openai'
    if kind == 'anthropic':
        return AnthropicProvider(settings)
    if kind == 'openai':
        return OpenAIProvider(settings)
    raise ProviderError('provider_unknown', provider=kind)


# ---------------- OpenAI-compatible ----------------

CHAT_PATH = '/chat/completions'


def openai_urls(settings, base=None):
    """(chat completions URL, models URL or None) of an OpenAI-compatible service."""
    base = (base if base is not None else settings.get('base_url') or OPENAI_DEFAULT_URL).strip().rstrip('/')
    if not base.startswith(('http://', 'https://')):
        raise ProviderError('base_url_invalid', url=base)
    if settings.get('full_url'):
        root = base[:-len(CHAT_PATH)] if base.endswith(CHAT_PATH) else None
        return base, (root + '/models' if root else None)
    if base.endswith(CHAT_PATH):
        base = base[:-len(CHAT_PATH)]
    return base + CHAT_PATH, base + '/models'


def openai_headers(settings):
    headers = {'Content-Type': 'application/json'}
    if settings.get('api_key'):
        headers['Authorization'] = f"Bearer {settings['api_key']}"
    if settings.get('user_agent'):
        headers['User-Agent'] = settings['user_agent']
    headers.update(settings.get('headers') or {})          # overrides last (e.g. api-key for Azure-style gateways)
    return headers


def opener_for(settings):
    proxy = (settings.get('proxy') or '').strip()
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {}))


def _http_error(e):
    body = e.read().decode('utf-8', 'replace')
    try:
        err = json.loads(body).get('error')
        body = err.get('message') if isinstance(err, dict) else (err or body)
    except (ValueError, AttributeError):
        pass
    return ProviderError('http_error', status=e.code, reason=_short(body))


class OpenAIProvider:
    name = 'openai'

    def __init__(self, settings):
        self.url, _ = openai_urls(settings)
        self.headers = openai_headers(settings)
        self.model = (settings.get('model') or '').strip()
        if not self.model:
            raise ProviderError('model_missing')
        self.effort = settings.get('effort') or ''
        self.body = settings.get('body') or {}
        self.timeout = int(settings.get('timeout') or 180)
        self.opener = opener_for(settings)

    @staticmethod
    def tools(defs):
        return [{'type': 'function', 'function': {'name': d['name'], 'description': d['description'],
                                                  'parameters': d['schema']}} for d in defs]

    def messages(self, system, turns):
        out = [{'role': 'system', 'content': system}]
        for t in turns:
            if t['role'] == 'user':
                out.append({'role': 'user', 'content': t['text']})
            elif t['role'] == 'assistant':
                raw = t.get('raw') if t.get('raw_provider') == self.name else None
                if raw:
                    out.append(raw)
                    continue
                msg = {'role': 'assistant', 'content': t.get('text') or None}
                if t.get('tool_calls'):
                    msg['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {
                        'name': c['name'], 'arguments': json.dumps(c['input'], ensure_ascii=False)}} for c in t['tool_calls']]
                out.append(msg)
            elif t['role'] == 'tool':
                for r in t['results']:
                    out.append({'role': 'tool', 'tool_call_id': r['id'], 'content': r['content']})
        return out

    def _post(self, payload):
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method='POST', headers=self.headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as res:
                return json.loads(res.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise _http_error(e)
        except urllib.error.URLError as e:
            raise ProviderError('connect_failed', reason=_short(e.reason))
        except TimeoutError:
            raise ProviderError('timeout', seconds=self.timeout)
        except ValueError:
            raise ProviderError('bad_response', reason='not JSON')

    def complete(self, system, turns, tool_defs, tool_choice='auto'):
        payload = {'model': self.model, 'messages': self.messages(system, turns)}
        if tool_defs:
            payload['tools'] = self.tools(tool_defs)
            if tool_choice == 'none':
                payload['tool_choice'] = 'none'
        if self.effort:
            payload['reasoning_effort'] = self.effort
        payload.update(self.body)
        data = self._post(payload)
        try:
            choice = data['choices'][0]
            msg = choice['message']
        except (KeyError, IndexError, TypeError):
            raise ProviderError('bad_response', reason=_short(json.dumps(data)[:300]))
        calls = []
        for c in msg.get('tool_calls') or []:
            fn = c.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                args = {'_invalid_arguments': fn.get('arguments')}
            calls.append({'id': c.get('id') or f'call_{len(calls)}', 'name': fn.get('name') or '', 'input': args})
        finish = choice.get('finish_reason')
        stop = 'tool' if calls else {'length': 'max_tokens', 'content_filter': 'refusal'}.get(finish, 'end')
        raw = {'role': 'assistant', 'content': msg.get('content')}
        if msg.get('tool_calls'):
            raw['tool_calls'] = msg['tool_calls']
        if msg.get('reasoning_content'):
            raw['reasoning_content'] = msg['reasoning_content']   # DeepSeek & co. want it back within a turn
        usage = data.get('usage') or {}
        return Reply(msg.get('content') or '', calls, stop, raw,
                     {'input': usage.get('prompt_tokens'), 'output': usage.get('completion_tokens')},
                     data.get('model') or self.model)


# ---------------- Anthropic ----------------

class AnthropicProvider:
    name = 'anthropic'

    def __init__(self, settings):
        try:
            import anthropic
        except ImportError:
            raise ProviderError('anthropic_sdk_missing')
        self.sdk = anthropic
        self.model = (settings.get('model') or '').strip() or ANTHROPIC_DEFAULT_MODEL
        base = (settings.get('base_url') or '').strip().rstrip('/') or None
        self.client = anthropic_client(settings, base)
        self.effort = settings.get('effort') or ''
        self.body = settings.get('body') or {}
        # Server-side refusal fallback only on Anthropic's own API (relays and gateways may not know the beta)
        self.fallback = base is None and self.model in FALLBACK_MODELS

    @staticmethod
    def tools(defs):
        return [{'name': d['name'], 'description': d['description'], 'input_schema': d['schema']} for d in defs]

    def messages(self, turns):
        out = []
        for t in turns:
            if t['role'] == 'user':
                out.append({'role': 'user', 'content': t['text']})
            elif t['role'] == 'assistant':
                raw = t.get('raw') if t.get('raw_provider') == self.name else None
                if raw:
                    out.append({'role': 'assistant', 'content': raw})   # thinking blocks go back unchanged
                    continue
                blocks = [{'type': 'text', 'text': t['text']}] if t.get('text') else []
                blocks += [{'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': c['input']}
                           for c in t.get('tool_calls') or []]
                out.append({'role': 'assistant', 'content': blocks or [{'type': 'text', 'text': '…'}]})
            elif t['role'] == 'tool':
                out.append({'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': r['id'], 'content': r['content'], 'is_error': r['is_error']}
                    for r in t['results']]})
        return out

    def complete(self, system, turns, tool_defs, tool_choice='auto'):
        sdk = self.sdk
        params = {'model': self.model, 'max_tokens': MAX_TOKENS, 'system': system, 'messages': self.messages(turns),
                  'cache_control': {'type': 'ephemeral'}}    # tools + system + history are a stable prefix
        if tool_defs:
            params['tools'] = self.tools(tool_defs)
            if tool_choice == 'none':
                params['tool_choice'] = {'type': 'none'}
        if self.effort in ('low', 'medium', 'high', 'xhigh', 'max'):
            params['output_config'] = {'effort': self.effort}
        if self.body:
            params['extra_body'] = self.body
        try:
            if self.fallback:
                response = self.client.beta.messages.create(betas=['server-side-fallback-2026-07-01'],
                                                            fallbacks='default', **params)
            else:
                response = self.client.messages.create(**params)
        except sdk.AuthenticationError:
            raise ProviderError('http_error', status=401, reason='invalid API key')
        except sdk.PermissionDeniedError as e:
            raise ProviderError('http_error', status=403, reason=_short(e.message))
        except sdk.NotFoundError as e:
            raise ProviderError('http_error', status=404, reason=_short(e.message))
        except sdk.RateLimitError as e:
            raise ProviderError('rate_limited', reason=_short(e.message))
        except sdk.BadRequestError as e:
            raise ProviderError('http_error', status=400, reason=_short(e.message))
        except sdk.APITimeoutError:
            raise ProviderError('timeout', seconds=int(self.client.timeout if isinstance(self.client.timeout, (int, float)) else 0))
        except sdk.APIStatusError as e:
            raise ProviderError('http_error', status=e.status_code, reason=_short(e.message))
        except sdk.APIConnectionError as e:
            raise ProviderError('connect_failed', reason=_short(e))
        texts, calls = [], []
        for block in response.content:
            if block.type == 'text':
                texts.append(block.text)
            elif block.type == 'tool_use':
                calls.append({'id': block.id, 'name': block.name, 'input': dict(block.input or {})})
        reason = response.stop_reason
        stop = {'tool_use': 'tool', 'max_tokens': 'max_tokens', 'refusal': 'refusal', 'pause_turn': 'pause'}.get(reason, 'end')
        detail = ''
        if reason == 'refusal' and getattr(response, 'stop_details', None):
            detail = getattr(response.stop_details, 'category', None) or ''
        usage = response.usage
        return Reply('\n\n'.join(t for t in texts if t), calls, stop, [b.to_dict() for b in response.content],
                     {'input': getattr(usage, 'input_tokens', None), 'output': getattr(usage, 'output_tokens', None),
                      'cache_read': getattr(usage, 'cache_read_input_tokens', None)},
                     response.model, detail)


def anthropic_client(settings, base, timeout=None, max_retries=2):
    try:
        import anthropic
    except ImportError:
        raise ProviderError('anthropic_sdk_missing')
    if base and not base.startswith(('http://', 'https://')):
        raise ProviderError('base_url_invalid', url=base)
    if not settings.get('api_key'):
        raise ProviderError('api_key_missing')
    headers = dict(settings.get('headers') or {})
    if settings.get('user_agent'):
        headers.setdefault('User-Agent', settings['user_agent'])
    kwargs = {'api_key': settings['api_key'], 'base_url': base, 'max_retries': max_retries,
              'timeout': float(timeout or settings.get('timeout') or 180)}
    if headers:
        kwargs['default_headers'] = headers
    proxy = (settings.get('proxy') or '').strip()
    if proxy:
        kwargs['http_client'] = anthropic.DefaultHttpxClient(proxy=proxy)
    return anthropic.Anthropic(**kwargs)


def _anthropic_call(fn, timeout):
    """Run an SDK call, turning its errors into ProviderErrors."""
    import anthropic as sdk
    try:
        return fn()
    except sdk.AuthenticationError:
        raise ProviderError('http_error', status=401, reason='invalid API key')
    except sdk.RateLimitError as e:
        raise ProviderError('rate_limited', reason=_short(e.message))
    except sdk.APITimeoutError:
        raise ProviderError('timeout', seconds=timeout)
    except sdk.APIStatusError as e:
        raise ProviderError('http_error', status=e.status_code, reason=_short(e.message))
    except sdk.APIConnectionError as e:
        raise ProviderError('connect_failed', reason=_short(e))


def list_models(settings):
    """Models the service offers: [{'id', 'name', 'context'}] (context = input window, if the service says)."""
    timeout = min(int(settings.get('timeout') or 60), 60)
    if settings.get('provider') == 'anthropic':
        base = (settings.get('base_url') or '').strip().rstrip('/') or None
        client = anthropic_client(settings, base, timeout=timeout, max_retries=1)
        models = _anthropic_call(lambda: list(client.models.list(limit=100)), timeout)
        return [{'id': m.id, 'name': getattr(m, 'display_name', '') or m.id,
                 'context': getattr(m, 'max_input_tokens', None)} for m in models]
    _, url = openai_urls(settings)
    if not url:
        raise ProviderError('models_unavailable')
    headers = {k: v for k, v in openai_headers(settings).items() if k != 'Content-Type'}
    try:
        with opener_for(settings).open(urllib.request.Request(url, headers=headers), timeout=timeout) as res:
            data = json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise _http_error(e)
    except urllib.error.URLError as e:
        raise ProviderError('connect_failed', reason=_short(e.reason))
    except TimeoutError:
        raise ProviderError('timeout', seconds=timeout)
    except ValueError:
        raise ProviderError('bad_response', reason='not JSON')
    items = data.get('data') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ProviderError('bad_response', reason=_short(json.dumps(data)[:300]))
    out = []
    for m in items:
        info = m if isinstance(m, dict) else {'id': m}
        mid = info.get('id')
        if not isinstance(mid, str) or not mid:
            continue
        ctx = info.get('context_length') or info.get('context_window') or info.get('max_input_tokens')
        out.append({'id': mid, 'name': str(info.get('name') or mid), 'context': ctx if isinstance(ctx, int) else None})
    return sorted(out, key=lambda m: m['id'])


def ping(settings, url, timeout=10):
    """Latency of one candidate address: time until the service answers a model list request. Any HTTP answer
    counts as reachable (a 401 still shows how fast the address is). {'url', 'ms', 'status', 'error'}"""
    out = {'url': url, 'ms': None, 'status': None, 'error': None}
    t0 = time.monotonic()
    try:
        if settings.get('provider') == 'anthropic':
            import anthropic as sdk
            client = anthropic_client(dict(settings, api_key=settings.get('api_key') or 'none'), url.rstrip('/'),
                                      timeout=timeout, max_retries=0)
            try:
                client.models.list(limit=1)
                out['status'] = 200
            except sdk.APIStatusError as e:
                out['status'] = e.status_code
            except sdk.APIConnectionError as e:
                out['error'] = _short(e, 160)
        else:
            chat_url, models_url = openai_urls(settings, url)
            headers = {k: v for k, v in openai_headers(settings).items() if k != 'Content-Type'}
            try:
                with opener_for(settings).open(urllib.request.Request(models_url or chat_url, headers=headers),
                                               timeout=timeout) as res:
                    res.read(65536)
                    out['status'] = res.status
            except urllib.error.HTTPError as e:
                out['status'] = e.code
            except (urllib.error.URLError, OSError) as e:
                out['error'] = _short(getattr(e, 'reason', e), 160)
    except ProviderError as e:
        out['error'] = e.code
    if out['status'] is not None:
        out['ms'] = round((time.monotonic() - t0) * 1000)
    return out


def ping_all(settings, urls):
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as pool:
        return list(pool.map(lambda u: ping(settings, u), urls))


def check(settings):
    """A one-line round trip without tools: (model, seconds, reply text)."""
    provider = make_provider(settings)
    t0 = time.monotonic()
    reply = provider.complete('Reply with the single word OK.', [{'role': 'user', 'text': 'Connection test.'}], [])
    return reply.model or getattr(provider, 'model', ''), round(time.monotonic() - t0, 2), _short(reply.text, 80)
