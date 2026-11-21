"""AI assistant plugin backend: chat with the user's own model service over read-only console tools.

Routes (under /api/ext/ai_assistant):
  GET  /status          active service and model, a short list of all services (for the panel's switcher),
                        configured or not (never a key)
  GET  /providers       model service profiles (keys only as set / hint), see profiles.py
  POST /providers       {profile} -> new profile;  PUT /providers/{id} {profile} -> changed profile
  DELETE /providers/{id};  POST /providers/{id}/activate {model?};  POST /providers/order {ids}
  POST /probe/models    {id?, profile} -> models the service lists (profile = the editor's unsaved values)
  POST /probe/latency   {id?, profile, urls} -> latency of each candidate address
  POST /test            {id?, profile?} -> one short round trip (default: the active profile)
  POST /chat            {message, session?, page?, access?} -> NDJSON events (session, status, text, tool, confirm,
                        tool_result, done, error); the conversation is kept on the server under its session id.
                        access: chat | read (default) | ask | auto, see agent.py
  POST /chat/confirm    {session, id, approve} -> the user's answer to a run tool call waiting for approval
  GET  /session/{id}    the conversation as the drawer shows it
"""
import asyncio
import json
import os
import threading

from fastapi import Body
from fastapi.responses import StreamingResponse

from . import actions, agent, profiles, providers
from .sessions import Sessions

MAX_MESSAGE = 8000
TOOL_TIMEOUT = 120
CONFIRM_TIMEOUT = 900          # an unanswered approval counts as declined after this
LEGACY_KEYS = ('provider', 'base_url', 'api_key', 'model', 'proxy')


def legacy_settings(ctx):
    """Plugin settings of version 0.1 (one service on the Plugins page), read once to migrate them."""
    try:
        stored = ctx._manager.state.settings(ctx.id)
    except AttributeError:
        return {}
    return {k: stored.get(k) for k in LEGACY_KEYS if stored.get(k)}


def setup(ctx):
    sessions = Sessions(ctx.data_dir + '/sessions')
    waiting = {}                  # (session id, tool call id) -> {'event', 'approve'}: run tools waiting for the user
    waiting_lock = threading.Lock()
    store = profiles.ProfileStore(os.path.join(ctx.data_dir, 'providers.json'))
    migrated = store.migrate(legacy_settings(ctx))
    if migrated:
        ctx.log.info(f'model service settings moved to profile "{migrated["name"]}"')

    def bad(e, status=400):
        return ctx.error(404 if e.code == 'profile_missing' else status, e.code, **e.params)

    def active_settings():
        p = store.active()
        if p is None:
            raise ctx.error(409, 'no_profile')
        return p, profiles.settings_for(p, ctx.settings())

    def provider_or_error(settings):
        try:
            return providers.make_provider(settings)
        except providers.ProviderError as e:
            raise ctx.error(409, e.code, **e.params)

    def draft(body):
        try:
            return profiles.draft_settings(store, ctx.settings(), body.get('id') or None, body.get('profile') or {})
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.get('/status')
    def status():
        p = store.active()
        out = {'tools': [{'name': t['name'], 'kind': t['kind']} for t in actions.ALL], 'active': p['id'] if p else '',
               'access_levels': list(agent.ACCESS), 'effort': '',
               'profiles': [{'id': x['id'], 'name': x['name'], 'color': x.get('color', ''), 'format': x['format'],
                             'model': x.get('model', ''),
                             'models': [{'id': m['id'], 'label': m['label'], 'effort': m.get('effort', ''),
                                         'efforts': m.get('efforts') or []}
                                        for m in x.get('models') or []]}
                            for x in store.all()]}
        if p is None:
            return {**out, 'provider': '', 'model': '', 'base_url': '', 'key_set': False, 'name': '',
                    'problem': {'code': 'no_profile', 'params': {}}}
        s = profiles.settings_for(p, ctx.settings())
        out.update({'name': p['name'], 'provider': p['format'], 'base_url': p.get('base_url', ''),
                    'key_set': bool(p.get('api_key')), 'effort': s.get('effort') or '',
                    'model': s['model'] or (providers.ANTHROPIC_DEFAULT_MODEL if p['format'] == 'anthropic' else '')})
        try:
            providers.make_provider(s)
            out['problem'] = None
        except providers.ProviderError as e:
            out['problem'] = {'code': e.code, 'params': e.params}
        return out

    @ctx.router.get('/providers')
    def list_providers():
        return store.listing()

    @ctx.router.post('/providers')
    def create_provider(body: dict = Body(...)):
        try:
            return profiles.view(store.create(body.get('profile')))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.put('/providers/{pid}')
    def update_provider(pid: str, body: dict = Body(...)):
        try:
            return profiles.view(store.update(pid, body.get('profile')))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.delete('/providers/{pid}')
    def delete_provider(pid: str):
        try:
            store.delete(pid)
        except profiles.ProfileError as e:
            raise bad(e)
        return store.listing()

    @ctx.router.post('/providers/order')
    def order_providers(body: dict = Body(...)):
        try:
            store.reorder(body.get('ids'))
        except profiles.ProfileError as e:
            raise bad(e)
        return store.listing()

    @ctx.router.post('/providers/{pid}/activate')
    def activate_provider(pid: str, body: dict = Body(default={})):
        try:
            body = body or {}
            return profiles.view(store.activate(pid, body.get('model'), body.get('effort')))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.post('/probe/models')
    async def probe_models(body: dict = Body(...)):
        _, s = draft(body)
        try:
            return {'models': await asyncio.to_thread(providers.list_models, s)}
        except providers.ProviderError as e:
            raise ctx.error(502, e.code, **e.params)

    @ctx.router.post('/probe/latency')
    async def probe_latency(body: dict = Body(...)):
        _, s = draft(body)
        urls = body.get('urls')
        if not isinstance(urls, list) or not urls or len(urls) > profiles.MAX_ENDPOINTS + 1 \
                or not all(isinstance(u, str) and u.startswith(('http://', 'https://')) for u in urls):
            raise ctx.error(400, 'profile_invalid', field='urls')
        return {'results': await asyncio.to_thread(providers.ping_all, s, [u.strip().rstrip('/') for u in urls])}

    @ctx.router.post('/test')
    async def test(body: dict = Body(default={})):
        if (body or {}).get('profile'):
            _, s = draft(body)
        else:
            _, s = active_settings()
        provider_or_error(s)
        try:
            model, seconds, reply = await asyncio.to_thread(providers.check, s)
        except providers.ProviderError as e:
            raise ctx.error(502, e.code, **e.params)
        return {'model': model, 'seconds': seconds, 'reply': reply}

    @ctx.router.get('/session/{sid}')
    def get_session(sid: str):
        session = sessions.load(sid)
        if session is None:
            raise ctx.error(404, 'session_missing')
        return {'id': session['id'], 'title': session.get('title', ''), 'items': display(session['turns'])}

    @ctx.router.post('/chat')
    async def chat(body: dict = Body(...)):
        message = body.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
            raise ctx.error(400, 'message_invalid', max=MAX_MESSAGE)
        page = body.get('page') if isinstance(body.get('page'), str) else ''
        access = body.get('access') or 'read'
        if access not in agent.ACCESS:
            raise ctx.error(400, 'access_invalid', access=str(access)[:20])
        _, settings = active_settings()
        provider = provider_or_error(settings)
        session = sessions.load(body.get('session')) if body.get('session') else None
        if session is None:
            session = sessions.new()
        if not sessions.acquire(session['id']):
            raise ctx.error(409, 'session_busy')

        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()

        def emit(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def call_tool(name, args):
            future = asyncio.run_coroutine_threadsafe(actions.run(ctx.api, name, args), loop)
            try:
                return future.result(timeout=TOOL_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                future.cancel()
                return f'Error: tool failed ({type(e).__name__})', True

        def confirm(call):
            """Wait for the user's answer to a run tool call: approved | denied | cancelled."""
            key = (session['id'], call['id'])
            entry = {'event': threading.Event(), 'approve': None}
            with waiting_lock:
                waiting[key] = entry
            emit({'type': 'confirm', 'id': call['id'], 'name': call['name'], 'input': call['input']})
            try:
                for _ in range(int(CONFIRM_TIMEOUT / 0.25)):
                    if entry['event'].wait(0.25):
                        return 'approved' if entry['approve'] else 'denied'
                    if stop.is_set():
                        return 'cancelled'
                return 'denied'
            finally:
                with waiting_lock:
                    waiting.pop(key, None)

        def worker():
            try:
                agent.run_turn(provider, session, message.strip(), page[:200], settings, call_tool, emit, stop.is_set,
                               access, confirm)
            except agent.Cancelled:
                emit({'type': 'error', 'code': 'cancelled', 'params': {}})
            except providers.ProviderError as e:
                emit({'type': 'error', 'code': e.code, 'params': e.params})
            except Exception as e:  # noqa: BLE001  the stream must always end with an event
                ctx.log.error(f'chat failed: {type(e).__name__}: {e}')
                emit({'type': 'error', 'code': 'internal', 'params': {'reason': f'{type(e).__name__}: {e}'[:300]}})
            finally:
                try:
                    sessions.save(session)
                except OSError as e:
                    ctx.log.warn(f'conversation not saved: {e}')
                sessions.release(session['id'])
                emit(None)

        # Started here, not inside the stream: the session lock is released even if the stream is never read
        threading.Thread(target=worker, daemon=True, name=f'ai_assistant_{session["id"][:8]}').start()

        async def events():
            yield json.dumps({'type': 'session', 'id': session['id']}) + '\n'
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield json.dumps(event, ensure_ascii=False, default=str) + '\n'
            finally:
                stop.set()        # the browser went away or the turn is over

        return StreamingResponse(events(), media_type='application/x-ndjson',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @ctx.router.post('/chat/confirm')
    def chat_confirm(body: dict = Body(...)):
        key = (str(body.get('session') or ''), str(body.get('id') or ''))
        with waiting_lock:
            entry = waiting.get(key)
        if entry is None:
            raise ctx.error(404, 'confirm_missing')
        entry['approve'] = bool(body.get('approve'))
        entry['event'].set()
        return {'ok': True}

    ctx.log.info(f'{len(actions.ALL)} tools ({len(actions.ACTIONS)} run tools, used only when the user allows them)')


def display(turns):
    """Conversation turns -> what the drawer shows: user messages, tool calls with their outcome, answers."""
    items, pending = [], {}
    for t in turns:
        if t['role'] == 'user':
            if t['text'] == agent.LIMIT_NOTE:
                continue
            items.append({'kind': 'user', 'text': agent.strip_context(t['text'])})
        elif t['role'] == 'assistant':
            calls = t.get('tool_calls') or []
            if t.get('text'):
                items.append({'kind': 'assistant', 'text': t['text'], 'final': not calls})
            for c in calls:
                item = {'kind': 'tool', 'id': c['id'], 'name': c['name'], 'input': c['input'], 'state': 'running',
                        'tool': actions.kind(c['name']) or 'read'}
                pending[c['id']] = item
                items.append(item)
        elif t['role'] == 'tool':
            for r in t['results']:
                if r['id'] in pending:
                    pending[r['id']]['state'] = 'error' if r['is_error'] else 'ok'
                    if r['is_error']:
                        pending[r['id']]['error'] = r['content'][:300]
                    if r.get('decision'):
                        pending[r['id']]['decision'] = r['decision']
    return items
