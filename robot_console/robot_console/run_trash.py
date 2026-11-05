"""Run-record trash for the evaluation page: move run folders (map_*, nav_*) to <runs_dir>/.trash, restore
them, or delete them for good.

Rules
  - "Delete" only moves a run folder into <runs_dir>/.trash/<run id>__<YYYYmmdd_HHMMSS>/; restore moves it back.
    Only "delete permanently" / "empty" remove files.
  - Refused while a batch experiment runs (it creates and evaluates runs), and per run when
      * the run is still being recorded (current mapping run, or a navigation mission still running), or
      * a saved map refers to it (meta.json "run"): the navigation page derives the ground-truth origin of
        that map from the run's est.tum / gt.tum. Delete the map first if the run is really not needed.
    A refused run stays where it is; the response lists why.
  - Folders in .trash without a run.json at the top (e.g. ~/runs/.trash/p0_20261105, 473 empty runs moved there
    in one go) are listed as a group; they can be deleted for good but not restored from the page.
Only plain directories directly inside runs_dir / .trash are touched; names are checked against a whitelist,
symlinks are never followed.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import Body, HTTPException

RUN_RE = re.compile(r'^[A-Za-z0-9_\-]{1,80}$')
ENTRY_RE = re.compile(r'^[A-Za-z0-9_\-]{1,120}$')
SEP = '__'
STAMP_RE = re.compile(r'__(\d{8}_\d{6})$')
MAX_IDS = 500


class RunTrashError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def _plain_dir(p):
    return p.is_dir() and not p.is_symlink()


def _size(path):
    """Bytes below path, without following symlinks."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        try:
                            total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
        except OSError:
            pass
    return total


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class RunTrash:
    def __init__(self, runs_dir, maps_dir, recording=lambda: None, batch_running=lambda: False):
        """recording(): id of the run being recorded right now (or None); batch_running(): a batch is active."""
        self.runs_dir = Path(os.path.expanduser(runs_dir))
        self.maps_dir = Path(os.path.expanduser(maps_dir))
        self.trash = self.runs_dir / '.trash'
        self.recording = recording
        self.batch_running = batch_running

    # ---------------- helpers ----------------

    def _maps_using(self):
        """{run id: [map names]} from the meta.json of the maps in the map library (not its trash)."""
        out = {}
        if not self.maps_dir.is_dir():
            return out
        for d in self.maps_dir.iterdir():
            if d.name.startswith('.') or not _plain_dir(d):
                continue
            meta = _read_json(d / 'meta.json')
            run = meta.get('run') if isinstance(meta, dict) else None
            if isinstance(run, str) and run:
                out.setdefault(run, []).append(d.name)
        return out

    def _entry_dir(self, entry):
        if not ENTRY_RE.fullmatch(entry or ''):
            raise RunTrashError('run_trash_not_found', 404, entry=entry)
        d = self.trash / entry
        if not _plain_dir(d) or d.parent != self.trash:
            raise RunTrashError('run_trash_not_found', 404, entry=entry)
        return d

    # ---------------- API ----------------

    def move(self, ids):
        """Move runs to the trash. Returns {'moved': [ids], 'refused': [{id, reason, maps?}]}."""
        if not isinstance(ids, list) or not ids or len(ids) > MAX_IDS:
            raise RunTrashError('run_trash_invalid', 400, n=MAX_IDS)
        if self.batch_running():
            raise RunTrashError('run_batch_running', 409)
        used = self._maps_using()
        recording = self.recording()
        stamp = time.strftime('%Y%m%d_%H%M%S')
        moved, refused = [], []
        for run_id in dict.fromkeys(str(i) for i in ids):      # unique, order kept
            d = self.runs_dir / run_id
            if not RUN_RE.fullmatch(run_id) or not _plain_dir(d) or not (d / 'run.json').is_file():
                refused.append({'id': run_id, 'reason': 'not_found'})
                continue
            info = _read_json(d / 'run.json') or {}
            if run_id == recording or info.get('state') == 'running':
                refused.append({'id': run_id, 'reason': 'recording'})
                continue
            if run_id in used:
                refused.append({'id': run_id, 'reason': 'used_by_map', 'maps': sorted(used[run_id])})
                continue
            self.trash.mkdir(exist_ok=True)
            target = self.trash / f'{run_id}{SEP}{stamp}'
            n = 1
            while target.exists():                              # same run deleted twice within a second
                target = self.trash / f'{run_id}{SEP}{stamp}_{n}'
                n += 1
            try:
                os.rename(d, target)
            except OSError as e:
                refused.append({'id': run_id, 'reason': 'failed', 'detail': str(e)})
                continue
            moved.append(run_id)
        return {'moved': moved, 'refused': refused}

    def list(self):
        entries = []
        if self.trash.is_dir():
            for d in self.trash.iterdir():
                if not _plain_dir(d) or not ENTRY_RE.fullmatch(d.name):
                    continue
                m = STAMP_RE.search(d.name)
                deleted = (time.strftime('%Y-%m-%d %H:%M:%S', time.strptime(m.group(1), '%Y%m%d_%H%M%S')) if m
                           else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d.stat().st_mtime)))
                info = _read_json(d / 'run.json')
                if isinstance(info, dict):
                    run_id = d.name[:m.start()] if m else d.name
                    entries.append({'entry': d.name, 'run': run_id, 'type': info.get('type', 'mapping'),
                                    'preset': info.get('preset'), 'started': info.get('started'),
                                    'deleted': deleted, 'runs': 1, 'size': _size(d), 'restorable': True})
                else:
                    n = sum(1 for c in d.iterdir() if _plain_dir(c) and (c / 'run.json').is_file())
                    entries.append({'entry': d.name, 'run': None, 'type': 'group', 'preset': None, 'started': None,
                                    'deleted': deleted, 'runs': n, 'size': _size(d), 'restorable': False})
        entries.sort(key=lambda e: e['deleted'], reverse=True)
        return {'entries': entries, 'size': sum(e['size'] for e in entries)}

    def restore(self, entry):
        d = self._entry_dir(entry)
        if not (d / 'run.json').is_file():
            raise RunTrashError('run_trash_not_restorable', 409, entry=entry)
        m = STAMP_RE.search(entry)
        run_id = entry[:m.start()] if m else entry
        target = self.runs_dir / run_id
        if not RUN_RE.fullmatch(run_id) or os.path.lexists(target):
            raise RunTrashError('run_trash_exists', 409, id=run_id)
        os.rename(d, target)
        return {'restored': run_id}

    def purge(self, entry):
        d = self._entry_dir(entry)
        shutil.rmtree(d)
        return {'deleted': entry}

    def empty(self):
        names = [e['entry'] for e in self.list()['entries']]
        for name in names:
            shutil.rmtree(self._entry_dir(name))
        return {'deleted': len(names)}


def register_run_trash_routes(app, cfg, mapping, batch):
    trash = RunTrash(cfg['mapping']['runs_dir'], cfg['mapping']['maps_dir'],
                     recording=lambda: getattr(getattr(mapping, 'recorder', None), 'run_id', None),
                     batch_running=lambda: bool(batch and batch.running()))

    def call(fn, *args):
        try:
            return fn(*args)
        except RunTrashError as e:
            raise HTTPException(e.status, detail={'code': e.code, 'params': e.params})
        except OSError as e:
            raise HTTPException(500, detail={'code': 'run_trash_failed', 'params': {'reason': str(e)}})

    @app.post('/api/run-trash/move')
    def run_trash_move(ids: list = Body(..., embed=True)):
        return call(trash.move, ids)

    @app.get('/api/run-trash')
    def run_trash_list():
        return call(trash.list)

    @app.post('/api/run-trash/{entry}/restore')
    def run_trash_restore(entry: str):
        return call(trash.restore, entry)

    @app.delete('/api/run-trash/{entry}')
    def run_trash_purge(entry: str):
        return call(trash.purge, entry)

    @app.delete('/api/run-trash')
    def run_trash_empty():
        return call(trash.empty)

    return trash
