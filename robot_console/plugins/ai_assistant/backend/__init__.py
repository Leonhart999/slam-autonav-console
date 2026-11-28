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
                        tool_result, usage, done, error); the conversation is kept on the server under its session id.
                        access: chat | read (default) | ask | auto, see agent.py
  POST /chat/confirm    {session, id, approve} -> the user's answer to a run tool call waiting for approval
  GET  /session/{id}    the conversation as the drawer shows it, with pin state and token counts
  GET  /sessions        paged history or substring search; PATCH / DELETE /sessions/{id} edit or remove it
  GET  /sessions/{id}/export   Markdown attachment or displayed transcript as JSON
  GET  /usage/{summary,series,breakdown,filters,export}   aggregates, filter choices and CSV (see usage.py)
  GET  /usage/prune/preview; POST /usage/prune   preview and delete usage records
  /pricing/...          model prices for the usage estimate, synced from models.dev (see pricing.py)
  /cpa/...              the embedded CLIProxyAPI engine and its OAuth sign-ins (see cpa.py, cpa_api.py)
"""
import asyncio
import json
import os
import re
import sqlite3
import threading
import time

from fastapi import Body
from fastapi.responses import Response, StreamingResponse

from . import actions, agent, cpa, cpa_api, pricing, profiles, providers, usage
from .sessions import ID_RE, SessionBusy, Sessions, markdown
from .store import Store

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
    ledger = Store(os.path.join(ctx.data_dir, 'assistant.db'))
    sessions = Sessions(os.path.join(ctx.data_dir, 'sessions'), ledger)
    usage.setup(ctx, ledger)
    waiting = {}                  # (session id, tool call id) -> {'event', 'approve'}: run tools waiting for the user
    waiting_lock = threading.Lock()
    store = profiles.ProfileStore(os.path.join(ctx.data_dir, 'providers.json'))
    migrated = store.migrate(legacy_settings(ctx))
    if migrated:
        ctx.log.info(f'model service settings moved to profile "{migrated["name"]}"')
    prices = pricing.Pricing(os.path.join(ctx.data_dir, 'pricing.json'), os.path.join(ctx.data_dir, 'models_dev.json'))
    pricing.register(ctx, prices, ledger)
    moved = prices.migrate(store.all())
    if moved:
        ctx.log.info(f'{moved} model prices moved from the model services to the pricing table')
    engine = cpa.Engine(ctx.data_dir)
    ctx.on_shutdown(engine.stop)
    # The engine-owned profile is recorded in providers.json itself (managed_by) and in the engine's metadata; either
    # record is enough to keep it bound to the running engine (losing engine.json must not make it a plain profile).
    owned = store.get(engine.data.get('cpa_profile_id')) if engine.data.get('cpa_profile_id') else None
    if owned and not owned.get('managed_by'):
        store.mark_managed(owned['id'])
    flagged = store.managed()
    if flagged and not engine.data.get('cpa_profile_id'):
        try:
            engine.remember_profile(flagged['id'])
        except OSError as e:
            ctx.log.warn(f'engine metadata not saved: {e}')

    def is_managed(p):
        if isinstance(p, str):
            p = store.get(p)
        return bool(p) and (p.get('managed_by') == 'cpa' or engine.is_managed(p['id']))

    def managed_conn(p):
        """(base_url, api_key) of the live engine for an engine-owned profile, None for other profiles. Raises 409
        cpa_not_ready while the engine is not running: nothing is sent to whatever now listens on its old port."""
        if not is_managed(p):
            return None
        try:
            base, _, key = engine.connection()
        except cpa.Problem as exc:
            raise ctx.error(exc.status, exc.code, reason=exc.detail)
        return base + '/v1', key

    def lock_managed(pid, raw):
        old = store.get(pid) if pid else None
        if old and is_managed(old):
            for field in ('format', 'base_url', 'full_url', 'endpoints', 'proxy', 'api_key', 'clear_key'):
                raw.pop(field, None)
                if field in old:
                    raw[field] = old[field]
        return raw

    def bad(e, status=400):
        return ctx.error(404 if e.code == 'profile_missing' else status, e.code, **e.params)

    def managed_view(p):
        return profiles.view(p, managed=is_managed(p))

    def managed_listing():
        data = store.listing()
        return {'active': data['active'], 'profiles': [managed_view(store.get(p['id']) or p) for p in data['profiles']]}

    def profile_settings(p):
        s = profiles.settings_for(p, ctx.settings())
        conn = managed_conn(p)
        if conn is not None:
            s.update(provider='openai', base_url=conn[0], full_url=False, api_key=conn[1], proxy='')
        return s

    def upstream_error(e, settings):
        # Provider HTTP errors have a "status" parameter as well as our own HTTP status.
        error = ctx.error(502, e.code)
        error.detail['params'] = profiles.safe_error(e.params, settings)
        return error

    def active_settings():
        p = store.active()
        if p is None:
            raise ctx.error(409, 'no_profile')
        return p, profile_settings(p)

    def provider_or_error(settings):
        try:
            return providers.make_provider(settings)
        except providers.ProviderError as e:
            raise ctx.error(409, e.code, **profiles.safe_error(e.params, settings))

    def draft(body):
        pid = body.get('id') or None
        raw = lock_managed(pid, dict(body.get('profile') or {}))
        try:
            p, s = profiles.draft_settings(store, ctx.settings(), pid, raw)
        except profiles.ProfileError as e:
            raise bad(e)
        conn = managed_conn(store.get(pid)) if pid else None
        if conn is not None:
            s.update(provider='openai', base_url=conn[0], full_url=False, api_key=conn[1], proxy='')
        return p, s

    @ctx.router.get('/status')
    def status():
        p = store.active()
        out = {'tools': [{'name': t['name'], 'kind': t['kind']} for t in actions.ALL], 'active': p['id'] if p else '',
               'access_levels': list(agent.ACCESS), 'effort': '',
               'profiles': [{'id': x['id'], 'name': x['name'], 'color': x.get('color', ''), 'format': x['format'],
                             'model': x.get('model', ''), 'managed': is_managed(x),
                             'models': [{'id': m['id'], 'label': m['label'], 'effort': m.get('effort', ''),
                                         'efforts': m.get('efforts') or []}
                                        for m in x.get('models') or []]}
                            for x in store.all()]}
        if p is None:
            return {**out, 'provider': '', 'model': '', 'base_url': '', 'key_set': False, 'name': '',
                    'problem': {'code': 'no_profile', 'params': {}}}
        managed = is_managed(p)
        base_url = profiles.mask_url(p.get('base_url', ''))
        if managed:
            try:
                s = profile_settings(p)
                base_url = s['base_url']
            except Exception as exc:
                detail = getattr(exc, 'detail', None) or {}
                code = str(detail.get('code') or 'cpa_not_ready').removeprefix(f'plugin.{ctx.id}.')
                return {**out, 'name': p['name'], 'provider': 'openai', 'base_url': '',
                        'key_set': bool(p.get('api_key')), 'effort': '', 'model': p.get('model', ''),
                        'managed': True, 'problem': {'code': code, 'params': detail.get('params', {})}}
        else:
            s = profiles.settings_for(p, ctx.settings())
        out.update({'name': p['name'], 'provider': s['provider'], 'base_url': base_url,
                    'key_set': bool(s.get('api_key')), 'effort': s.get('effort') or '',
                    'model': s['model'] or (providers.ANTHROPIC_DEFAULT_MODEL if s['provider'] == 'anthropic' else '')})
        if managed:
            out['managed'] = True
        try:
            providers.make_provider(s)
            out['problem'] = None
        except providers.ProviderError as e:
            out['problem'] = {'code': e.code, 'params': profiles.safe_error(e.params, s)}
        return out

    @ctx.router.get('/providers')
    def list_providers():
        return managed_listing()

    @ctx.router.post('/providers')
    def create_provider(body: dict = Body(...)):
        try:
            return managed_view(store.create(body.get('profile')))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.put('/providers/{pid}')
    def update_provider(pid: str, body: dict = Body(...)):
        raw = lock_managed(pid, dict(body.get('profile') or {}))
        try:
            return managed_view(store.update(pid, raw))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.delete('/providers/{pid}')
    def delete_provider(pid: str):
        try:
            store.delete(pid)
            engine.forget_profile(pid)
        except profiles.ProfileError as e:
            raise bad(e)
        return managed_listing()

    @ctx.router.post('/providers/order')
    def order_providers(body: dict = Body(...)):
        try:
            store.reorder(body.get('ids'))
        except profiles.ProfileError as e:
            raise bad(e)
        return managed_listing()

    @ctx.router.post('/providers/{pid}/activate')
    def activate_provider(pid: str, body: dict = Body(default={})):
        try:
            body = body or {}
            return managed_view(store.activate(pid, body.get('model'), body.get('effort')))
        except profiles.ProfileError as e:
            raise bad(e)

    @ctx.router.post('/probe/models')
    async def probe_models(body: dict = Body(...)):
        _, s = draft(body)
        try:
            return {'models': await asyncio.to_thread(providers.list_models, s)}
        except providers.ProviderError as e:
            raise upstream_error(e, s)

    @ctx.router.post('/probe/latency')
    async def probe_latency(body: dict = Body(...)):
        if body.get('id') and is_managed(str(body.get('id'))):
            # the engine's address is fixed: other addresses would receive the engine's key
            raise ctx.error(400, 'profile_invalid', field='urls')
        _, s = draft(body)
        urls = body.get('urls')
        if not isinstance(urls, list) or not urls or len(urls) > profiles.MAX_ENDPOINTS + 1 \
                or not all(isinstance(u, str) and u.startswith(('http://', 'https://')) for u in urls):
            raise ctx.error(400, 'profile_invalid', field='urls')
        results = await asyncio.to_thread(providers.ping_all, s, [u.strip().rstrip('/') for u in urls])
        for r in results:
            if isinstance(r.get('error'), str):
                r['error'] = profiles.safe_error({'reason': r['error']}, s)['reason']
        return {'results': results}

    @ctx.router.post('/test')
    async def test(body: dict = Body(default={})):
        if (body or {}).get('profile'):
            p, s = draft(body)
        elif (body or {}).get('id'):
            p = store.get(body['id'])
            if not p:
                raise ctx.error(404, 'profile_missing', id=body['id'])
            s = profile_settings(p)
        else:
            p, s = active_settings()
        provider = provider_or_error(s)
        record = usage.recorder(ctx, ledger, prices, p, provider, 'test')
        try:
            model, seconds, reply = await asyncio.to_thread(providers.check, provider, record)
        except providers.ProviderError as e:
            raise upstream_error(e, s)
        return {'model': model, 'seconds': seconds, 'reply': reply}

    def check_id(sid):
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            raise ctx.error(404, 'session_missing')

    def transcript(sid):
        check_id(sid)
        session = sessions.load(sid)
        if session is None:
            raise ctx.error(404, 'session_missing')
        return session, {'id': sid, 'title': session.get('title', ''), 'created': session.get('created'),
                         'items': display(session['turns'])}

    @ctx.router.get('/session/{sid}')
    def get_session(sid: str):
        session, view = transcript(sid)
        return {**view, 'pinned': session['pinned'], 'usage': ledger.session_usage(sid)}

    @ctx.router.get('/sessions')
    def list_sessions(q: str = '', limit: str = '50', offset: str = '0'):
        q = q.strip()
        if len(q) > 200:
            raise ctx.error(400, 'session_invalid', field='q')

        def integer(value, field, minimum):
            try:
                n = int(value)
                if not minimum <= n < 2**63:
                    raise ValueError
                return n
            except ValueError:
                raise ctx.error(400, 'session_invalid', field=field)

        sessions.ensure_synced()
        return ledger.history(q, integer(limit, 'limit', 1), integer(offset, 'offset', 0))

    @ctx.router.patch('/sessions/{sid}')
    def patch_session(sid: str, body: dict = Body(...)):
        check_id(sid)
        for field in body:
            if field not in ('title', 'pinned'):
                raise ctx.error(400, 'session_invalid', field=field)
        if 'title' in body:
            if not isinstance(body['title'], str) or not 1 <= len(body['title'].strip()) <= 120:
                raise ctx.error(400, 'session_invalid', field='title')
            body['title'] = body['title'].strip()
        if 'pinned' in body and not isinstance(body['pinned'], bool):
            raise ctx.error(400, 'session_invalid', field='pinned')
        try:
            item = sessions.patch(sid, body)
        except (OSError, sqlite3.Error) as e:
            raise ctx.error(500, 'internal', reason=f'{type(e).__name__}: {e}'[:300])
        if item is None:
            raise ctx.error(404, 'session_missing')
        return item

    @ctx.router.delete('/sessions/{sid}')
    def delete_session(sid: str):
        check_id(sid)
        try:
            if not sessions.delete(sid):
                raise ctx.error(404, 'session_missing')
        except SessionBusy:
            raise ctx.error(409, 'session_busy')
        except (OSError, sqlite3.Error) as e:
            raise ctx.error(500, 'internal', reason=f'{type(e).__name__}: {e}'[:300])
        return {'ok': True}

    @ctx.router.get('/sessions/{sid}/export')
    def export_session(sid: str, format: str = 'md'):
        _, view = transcript(sid)
        if format == 'json':
            return view
        if format != 'md':
            raise ctx.error(400, 'session_invalid', field='format')
        safe = re.sub(r'[^A-Za-z0-9._-]+', '-', view['title']).strip('.-') or 'conversation'
        item = ledger.item(sid)
        date = time.strftime('%Y%m%d', time.localtime(item['created'] if item else time.time()))
        return Response(markdown(view['title'], view['items']), media_type='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename="{safe}-{date}.md"'})

    @ctx.router.post('/chat')
    async def chat(body: dict = Body(...)):
        message = body.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
            raise ctx.error(400, 'message_invalid', max=MAX_MESSAGE)
        page = body.get('page') if isinstance(body.get('page'), str) else ''
        access = body.get('access') or 'read'
        if access not in agent.ACCESS:
            raise ctx.error(400, 'access_invalid', access=str(access)[:20])
        p, settings = active_settings()
        provider = provider_or_error(settings)
        sid = body.get('session')
        if sid is not None and sid != '':
            check_id(sid)
        try:
            session = sessions.begin(sid)
        except SessionBusy:
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
                               access, confirm, usage.recorder(ctx, ledger, prices, p, provider, 'chat', session))
            except agent.Cancelled:
                emit({'type': 'error', 'code': 'cancelled', 'params': {}})
            except providers.ProviderError as e:
                emit({'type': 'error', 'code': e.code, 'params': profiles.safe_error(e.params, settings)})
            except Exception as e:  # noqa: BLE001  the stream must always end with an event
                safe = profiles.safe_error({'reason': f'{type(e).__name__}: {e}'[:300]}, settings)
                ctx.log.error(f"chat failed: {safe['reason']}")
                emit({'type': 'error', 'code': 'internal', 'params': safe})
            finally:
                try:
                    sessions.save(session)
                except (OSError, sqlite3.Error) as e:
                    ctx.log.warn(f'conversation not saved: {e}')
                finally:
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
        sid = body.get('session')
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            raise ctx.error(404, 'confirm_missing')
        key = (sid, str(body.get('id') or ''))
        with waiting_lock:
            entry = waiting.get(key)
        if entry is None:
            raise ctx.error(404, 'confirm_missing')
        entry['approve'] = bool(body.get('approve'))
        entry['event'].set()
        return {'ok': True}

    ctx.log.info(f'{len(actions.ALL)} tools ({len(actions.ACTIONS)} run tools, used only when the user allows them)')

    cpa_api.register(ctx, engine, store)


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
                items.append({'kind': 'assistant', 'text': t['text'], 'final': t.get('final', not calls)})
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
