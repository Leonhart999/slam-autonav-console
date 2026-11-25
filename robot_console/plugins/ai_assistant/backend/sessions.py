"""Provider-neutral JSON conversations and their searchable index."""
import json
import math
import os
import re
import threading
import time
import uuid

from . import agent

ID_RE = re.compile(r'^[a-f0-9]{32}\Z')
KEEP = 500                     # maximum number of unpinned conversations
SEARCH_BYTES = 200_000


class SessionBusy(Exception):
    pass


def index_row(session, updated):
    body, preview, count = bytearray(), '', 0
    for turn in session['turns']:
        text = turn.get('text') or ''
        if turn.get('role') == 'user' and text != agent.LIMIT_NOTE:
            text = agent.strip_context(text)
            count += 1
        elif turn.get('role') == 'assistant' and turn.get('final', not turn.get('tool_calls')):
            preview = text[:120]
        else:
            continue
        if text and len(body) < SEARCH_BYTES:
            if body:
                body.extend(b'\n')
            left = SEARCH_BYTES - len(body)
            body.extend(text[:left].lower().encode('utf-8')[:left])
    created = session.get('created')
    try:
        created = (float(created) if isinstance(created, (float, int)) else
                   time.mktime(time.strptime(created, '%Y-%m-%d %H:%M:%S')))
    except (ValueError, TypeError, OverflowError, OSError):
        created = updated
    return {'id': session['id'], 'title': session.get('title', ''),
            'created': created if math.isfinite(created) else updated,
            'updated': updated, 'pinned': bool(session.get('pinned')), 'turns': count, 'preview': preview,
            'search': body.decode('utf-8', 'ignore'),
            **{k: session.get(k, '') for k in ('model', 'profile_id', 'profile_name')}}


class Sessions:
    def __init__(self, folder, store):
        self.folder = folder
        self.store = store
        self.lock = threading.RLock()
        self.busy = set()
        self._dirty_flag = os.path.join(self.folder, '.needs_reconcile')
        self._dirty = os.path.exists(self._dirty_flag)
        if os.path.isdir(folder):
            self.reconcile()

    def _set_dirty(self, dirty):
        self._dirty = dirty
        try:
            if dirty:
                os.makedirs(self.folder, exist_ok=True)
                with open(self._dirty_flag, 'w', encoding='utf-8') as f:
                    f.write('1')
            elif os.path.exists(self._dirty_flag):
                os.remove(self._dirty_flag)
        except OSError:
            pass

    def _cleanup_leftovers(self):
        for name in os.listdir(self.folder):
            full = os.path.join(self.folder, name)
            if name.endswith('.json.tmp'):
                try:
                    os.remove(full)
                except OSError:
                    pass
            elif name.endswith('.json.del'):
                sid = name[:-9]
                if ID_RE.fullmatch(sid) and not os.path.exists(self._path(sid)) and self.store.item(sid):
                    try:
                        os.replace(full, self._path(sid))
                        continue
                    except OSError:
                        pass
                try:
                    os.remove(full)
                except OSError:
                    pass

    def reconcile(self):
        with self.lock:
            if os.path.isdir(self.folder):
                self._cleanup_leftovers()
                disk = {}
                for name in os.listdir(self.folder):
                    if name.endswith('.json'):
                        sid = name[:-5]
                        session = self.load(sid)
                        if session:
                            mtime = os.path.getmtime(self._path(sid))
                            disk[sid] = index_row(session, mtime)
            else:
                disk = {}
            self.store.reconcile(disk)
            self._prune()
            self._set_dirty(False)

    def ensure_synced(self):
        if self._dirty or os.path.exists(self._dirty_flag):
            self.reconcile()

    def _legacy_rows(self):
        for name in os.listdir(self.folder):
            if name.endswith('.json'):
                session = self.load(name[:-5])
                if session:
                    yield index_row(session, os.path.getmtime(self._path(session['id'])))

    def _path(self, sid):
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            raise ValueError('Invalid session id')
        return os.path.join(self.folder, sid + '.json')

    def new(self):
        return {'id': uuid.uuid4().hex, 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                'title': '', 'pinned': False, 'title_locked': False, 'turns': []}

    def load(self, sid):
        try:
            with open(self._path(sid), encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get('id') == sid and isinstance(data.get('turns'), list):
            data.setdefault('pinned', False)
            data.setdefault('title_locked', False)
            return data
        return None

    def begin(self, sid):
        """Load and reserve together, so delete and concurrent follow-ups cannot race the load."""
        with self.lock:
            if sid in self.busy:
                raise SessionBusy()
            session = self.load(sid) or self.new()
            self.acquire(session['id'])
            return session

    def save(self, session):
        with self.lock:
            if session['id'] in self.busy:
                # A pin or rename can arrive while the provider is answering.
                old = self.load(session['id'])
                if old:
                    session['pinned'] = old['pinned']
                    if old['title_locked']:
                        session.update(title_locked=True, title=old['title'])
            self._write(session)

    def _write(self, session):
        os.makedirs(self.folder, exist_ok=True)
        path = self._path(session['id'])
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(session, f, ensure_ascii=False)
        os.replace(path + '.tmp', path)
        mtime = time.time()
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass
        try:
            self.store.index(index_row(session, mtime))
            if self._dirty or os.path.exists(self._dirty_flag):
                self.reconcile()               # an earlier index write failed: the index may be stale, fix it first
            else:
                self._prune()
        except Exception:
            self._set_dirty(True)
            raise

    def patch(self, sid, changes):
        with self.lock:
            self.ensure_synced()
            session = self.load(sid)
            if session is None:
                return None
            session.update(changes)
            if 'title' in changes:
                session['title_locked'] = True
            self._write(session)
            return self.store.item(sid)

    def delete(self, sid):
        with self.lock:
            if sid in self.busy:
                raise SessionBusy()
            path = self._path(sid)
            del_path = path + '.del'
            try:
                os.replace(path, del_path)
            except FileNotFoundError:
                self.store.delete_index(sid)
                return False
            try:
                self.store.delete_index(sid)
            except Exception:
                try:
                    os.replace(del_path, path)
                except OSError:
                    pass
                raise
            try:
                os.remove(del_path)
            except OSError:
                pass
            return True

    def _prune(self):
        with self.store.write_lock, self.store.connection() as db:
            rows = db.execute('''SELECT id FROM sessions_index WHERE pinned=0
                                 ORDER BY updated DESC, id LIMIT -1 OFFSET ?''', (KEEP,)).fetchall()
            for row in rows:
                if row['id'] in self.busy:
                    continue
                on_disk = self.load(row['id'])
                if on_disk and on_disk.get('pinned'):
                    # the JSON (the source of truth) says pinned but the index does not: repair, never delete
                    db.execute('UPDATE sessions_index SET pinned=1 WHERE id=?', (row['id'],))
                    continue
                try:
                    os.remove(self._path(row['id']))
                except FileNotFoundError:
                    pass
                db.execute('DELETE FROM sessions_index WHERE id=?', (row['id'],))

    def acquire(self, sid):
        with self.lock:
            if sid in self.busy:
                return False
            self.busy.add(sid)
            return True

    def release(self, sid):
        with self.lock:
            self.busy.discard(sid)


def markdown(title, items):
    blocks = [f'# {title}']
    for item in items:
        if item['kind'] == 'tool':
            args = json.dumps(item['input'], ensure_ascii=False, separators=(',', ':'))
            outcome = 'ok' if item['state'] == 'ok' else 'error'
            name = ' '.join(item['name'].splitlines())
            blocks.append(f'> tool: {name}({args}) - {outcome}')
        else:
            role = 'You' if item['kind'] == 'user' else 'Assistant'
            blocks.append(f"**{role}**\n\n{item['text']}")
    return '\n\n'.join(blocks) + '\n'
