"""SQLite history index and per-call usage ledger. Transcripts stay in JSON files."""
from contextlib import contextmanager
import sqlite3
import threading

from .providers import TOKEN_FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions_index (
    id TEXT PRIMARY KEY, title TEXT, created REAL, updated REAL, pinned INT, turns INT,
    model TEXT, profile_id TEXT, profile_name TEXT, tokens_in INT, tokens_out INT,
    tokens_cache INT, preview TEXT, search TEXT
);
CREATE INDEX IF NOT EXISTS history_order ON sessions_index(pinned DESC, updated DESC, id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, session TEXT, round INT, source TEXT CHECK(source IN ('chat','test')),
    profile_id TEXT, profile_name TEXT, format TEXT, model TEXT, effort TEXT,
    status TEXT CHECK(status IN ('ok','error','cancelled')), error TEXT,
    input INT, output INT, cache_read INT, cache_write INT, reasoning INT, ms INT, cost REAL, currency TEXT
);
CREATE INDEX IF NOT EXISTS usage_time ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_profile ON usage(profile_id, ts);
CREATE INDEX IF NOT EXISTS usage_model ON usage(model, ts);
CREATE INDEX IF NOT EXISTS usage_session ON usage(session);
PRAGMA user_version = 1;
"""
SUMS = ', '.join(f'coalesce(sum({k}), 0) AS {k}' for k in TOKEN_FIELDS)
INDEX_SUMS = 'coalesce(sum(input),0), coalesce(sum(output),0), coalesce(sum(cache_read + cache_write),0)'
ITEM = 'id,title,created,updated,pinned,turns,model,profile_name,tokens_in,tokens_out,tokens_cache,preview'


class Store:
    def __init__(self, path):
        self.path = path
        self.write_lock = threading.Lock()
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
            elif version != 1:
                raise RuntimeError(f'Unsupported assistant database version: {version}')

    @contextmanager
    def connection(self, streaming=False):
        db = sqlite3.connect(self.path, timeout=15, check_same_thread=not streaming)
        try:
            db.row_factory = sqlite3.Row
            db.create_function('lower_text', 1, str.lower)
            with db:
                yield db
        finally:
            db.close()

    def add_usage(self, row):
        fields = ('ts', 'session', 'round', 'source', 'profile_id', 'profile_name', 'format', 'model', 'effort',
                  'status', 'error', *TOKEN_FIELDS, 'ms', 'cost', 'currency')
        with self.write_lock, self.connection() as db:
            db.execute(f"INSERT INTO usage ({','.join(fields)}) VALUES ({','.join(':'+k for k in fields)})", row)

    def session_usage(self, sid):
        with self.connection() as db:
            return dict(db.execute(f'SELECT {SUMS} FROM usage WHERE session=?', (sid,)).fetchone())

    INDEX = """INSERT OR REPLACE INTO sessions_index
               (id,title,created,updated,pinned,turns,model,profile_id,profile_name,preview,search,
                tokens_in,tokens_out,tokens_cache)
               SELECT :id,:title,:created,:updated,:pinned,:turns,:model,:profile_id,:profile_name,:preview,:search,"""

    def index(self, row):
        with self.write_lock, self.connection() as db:
            db.execute(self.INDEX + INDEX_SUMS + ' FROM usage WHERE session=:id', row)

    def delete_index(self, sid):
        with self.write_lock, self.connection() as db:
            db.execute('DELETE FROM sessions_index WHERE id=?', (sid,))

    def reconcile(self, disk_rows):
        """Synchronize sessions_index against the JSON conversations on disk and real usage sums."""
        with self.write_lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            indexed = {r['id']: (r['updated'], r['turns'], r['tokens_in'], r['tokens_out'], r['tokens_cache'],
                                 bool(r['pinned']), r['title'])
                       for r in db.execute('SELECT id, updated, turns, tokens_in, tokens_out, tokens_cache, pinned, title '
                                           'FROM sessions_index')}
            sums = {r['session']: (r[1], r[2], r[3])
                    for r in db.execute(f'SELECT session, {INDEX_SUMS} FROM usage WHERE session IS NOT NULL GROUP BY session')}
            stale = [(sid,) for sid in indexed if sid not in disk_rows]
            if stale:
                db.executemany('DELETE FROM sessions_index WHERE id=?', stale)
            upsert = []
            for sid, row in disk_rows.items():
                tok = sums.get(sid, (0, 0, 0))
                prev = indexed.get(sid)
                if (prev is None or abs((row['updated'] or 0) - (prev[0] or 0)) > 0.001
                        or row['turns'] != prev[1] or tok != prev[2:5]
                        or bool(row['pinned']) != prev[5] or row['title'] != prev[6]):
                    upsert.append({**row, 'tokens_in': tok[0], 'tokens_out': tok[1], 'tokens_cache': tok[2]})
            if upsert:
                db.executemany("""INSERT OR REPLACE INTO sessions_index
                    (id,title,created,updated,pinned,turns,model,profile_id,profile_name,preview,search,
                     tokens_in,tokens_out,tokens_cache)
                    VALUES (:id,:title,:created,:updated,:pinned,:turns,:model,:profile_id,:profile_name,:preview,:search,
                            :tokens_in,:tokens_out,:tokens_cache)""", upsert)

    def backfill(self, rows):
        """Legacy entry point kept for compatibility."""
        self.reconcile({r['id']: r for r in rows})

    @staticmethod
    def _item(row):
        item = dict(row)
        item['pinned'] = bool(item['pinned'])
        item['tokens'] = {k: item.pop(col) for k, col in [('input', 'tokens_in'), ('output', 'tokens_out'),
                                                          ('cache', 'tokens_cache')]}
        return item

    def item(self, sid):
        with self.connection() as db:
            row = db.execute(f'SELECT {ITEM} FROM sessions_index WHERE id=?', (sid,)).fetchone()
            return self._item(row) if row else None

    def history(self, q, limit, offset):
        q = q.lower()
        where = 'WHERE instr(lower_text(title), :q) OR instr(search, :q)' if q else ''
        order = '(instr(lower_text(title), :q)>0) DESC' if q else 'pinned DESC'
        snippet = (", CASE WHEN instr(search,:q)>0 THEN substr(search,max(1,instr(search,:q)-30),90) "
                   "ELSE '' END AS snippet") if q else ''
        args = {'q': q, 'limit': limit, 'offset': offset}
        with self.connection() as db:
            db.execute('BEGIN')
            total = db.execute(f'SELECT count(*) FROM sessions_index {where}', args).fetchone()[0]
            rows = db.execute(f'SELECT {ITEM}{snippet} FROM sessions_index {where} '
                              f'ORDER BY {order}, updated DESC, id LIMIT :limit OFFSET :offset', args)
            return {'total': total, 'items': [self._item(r) for r in rows]}

    def prune_usage(self, before, preview=False):
        where, args = ('', ()) if before is None else (' WHERE ts < ?', (before,))
        if preview:
            with self.connection() as db:
                return db.execute('SELECT count(*) FROM usage' + where, args).fetchone()[0]
        with self.write_lock, self.connection() as db:
            deleted = db.execute('DELETE FROM usage' + where, args).rowcount
            if deleted:
                db.execute(f'''UPDATE sessions_index SET (tokens_in,tokens_out,tokens_cache) =
                               (SELECT {INDEX_SUMS} FROM usage WHERE session=sessions_index.id)''')
            return deleted
