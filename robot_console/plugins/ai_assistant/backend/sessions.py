"""Conversations, kept as provider-neutral turns in <plugin data>/sessions/<id>.json:

  {'role': 'user', 'text': ...}
  {'role': 'assistant', 'text': ..., 'tool_calls': [{'id', 'name', 'input'}], 'raw': <provider-native>, 'raw_provider': ...}
  {'role': 'tool', 'results': [{'id', 'name', 'content', 'is_error'}]}

Turns are only appended (never edited), so providers that bind reasoning to the exact history accept them.
"""
import json
import os
import re
import threading
import time
import uuid

ID_RE = re.compile(r'^[a-f0-9]{32}$')
KEEP = 50                     # newest conversations kept on disk


class Sessions:
    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        self.busy = set()

    def _path(self, sid):
        return os.path.join(self.folder, sid + '.json')

    def new(self):
        sid = uuid.uuid4().hex
        return {'id': sid, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'title': '', 'turns': []}

    def load(self, sid):
        """The conversation, or None when the id is unknown (expired or never existed)."""
        if not sid or not ID_RE.match(sid):
            return None
        try:
            with open(self._path(sid), encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get('turns'), list) else None

    def save(self, session):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self._path(session['id']) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(session, f, ensure_ascii=False)
        os.replace(tmp, self._path(session['id']))
        self._prune()

    def _prune(self):
        try:
            files = sorted((os.path.getmtime(os.path.join(self.folder, n)), n)
                           for n in os.listdir(self.folder) if n.endswith('.json'))
        except OSError:
            return
        for _, name in files[:-KEEP]:
            try:
                os.remove(os.path.join(self.folder, name))
            except OSError:
                pass

    def acquire(self, sid):
        with self.lock:
            if sid in self.busy:
                return False
            self.busy.add(sid)
            return True

    def release(self, sid):
        with self.lock:
            self.busy.discard(sid)
