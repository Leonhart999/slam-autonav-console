"""Evaluation page (step 5a): run list, evo APE/RPE per run, CSV + PNG export.

Run folders are written by runs.py (~/runs/<id>/{est.tum, gt.tum, run.json}). The metrics are computed by
eval_worker.py inside the evo venv and cached as <run>/eval.json; the cache is reused while it is newer
than both trajectory files and was made with the same parameters.
Navigation runs (nav_<time>, written by nav_mission.py) are read through nav_eval.py; no evo involved.
Exports go to evaluation.results_dir/<time>/ (default <data_root>/results), or <data_root>/results/<time>/
when the configured folder's parent does not exist (e.g. a Windows drive that another machine does not have).
"""
import csv
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import Body, HTTPException
from fastapi.responses import FileResponse

from .nav_eval import FIGURES as NAV_FIGURES, amcl_series, nav_metrics, write_csvs

RUN_RE = re.compile(r'^[A-Za-z0-9_\-]{1,80}$')
WORKER = Path(__file__).with_name('eval_worker.py')


class EvalError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def windows_path(path):
    """/mnt/d/a/b -> D:\\a\\b so the page can show a path the user can paste into Explorer."""
    m = re.match(r'^/mnt/([a-z])(/.*)?$', str(path))
    return f'{m.group(1).upper()}:{(m.group(2) or "/").replace("/", chr(92))}' if m else str(path)


class Evaluation:
    def __init__(self, cfg, mapping):
        ev = cfg.get('evaluation') or {}
        self.runs_dir = Path(os.path.expanduser(cfg['mapping'].get('runs_dir', '~/runs'))).resolve()
        self.python = Path(os.path.expanduser(ev.get('evo_python', '~/venvs/evo/bin/python')))
        data_root = Path(os.path.expanduser((cfg.get('paths') or {}).get('data_root', '~/robot_console_data')))
        self.fallback_results = data_root / 'results'
        self.results_dir = Path(os.path.expanduser(ev.get('results_dir') or self.fallback_results))
        self.params = {'max_diff': float(ev.get('max_diff', 0.01)), 'rpe_delta': float(ev.get('rpe_delta', 1.0))}
        self.mapping = mapping
        self.lock = threading.Lock()      # one evo process at a time

    # ---------------- run folders ----------------

    def _dir(self, run_id):
        if not RUN_RE.fullmatch(run_id or ''):
            raise EvalError('run_not_found', 404, id=run_id)
        d = (self.runs_dir / run_id).resolve()
        if d.parent != self.runs_dir or not (d / 'run.json').is_file():
            raise EvalError('run_not_found', 404, id=run_id)
        return d

    def _recording(self):
        return getattr(self.mapping.recorder, 'run_id', None) if self.mapping else None

    def _cached(self, d, name='eval.json'):
        """eval.json (or eval_error.json) if still valid for the current trajectories and parameters."""
        path = d / name
        try:
            newest = max((d / f).stat().st_mtime for f in ('est.tum', 'gt.tum'))
            if path.stat().st_mtime < newest:
                return None
            ev = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        p = ev.get('params', {})
        if any(p.get(k) != v for k, v in self.params.items()):
            return None
        return ev

    def _entry(self, d, recording, info=None):
        info = info or json.loads((d / 'run.json').read_text(encoding='utf-8'))
        kind = info.get('type', 'mapping')
        if kind == 'navigation':
            m = nav_metrics(info)
            return {
                'id': d.name, 'type': kind, 'preset': info.get('preset'),
                'started': info.get('started'), 'ended': info.get('ended'),
                'duration': m['total_time_s'], 'length': m['total_path_m'],
                'maps': [info['map']] if info.get('map') else [], 'recording': info.get('state') == 'running',
                'evaluable': m['finished'] > 0, 'variant': m['variant'], 'nav': m,
                'figures': [f for f in NAV_FIGURES if (d / f).is_file()],
                'ape_rmse': None, 'rpe_rmse': None, 'evaluated': None, 'eval_error': None,
            }
        try:
            gt_ok = (d / 'gt.tum').stat().st_size > 0 and (d / 'est.tum').stat().st_size > 0
        except OSError:
            gt_ok = False
        ev = self._cached(d)
        failed = None if ev else self._cached(d, 'eval_error.json')
        return {
            'id': d.name, 'type': kind, 'preset': info.get('preset'),
            'started': info.get('started'), 'ended': info.get('ended'),
            'duration': info.get('duration'), 'length': info.get('length'),
            'maps': info.get('maps') or [], 'recording': d.name == recording,
            'evaluable': kind == 'mapping' and gt_ok, 'variant': None, 'nav': None, 'figures': [],
            'ape_rmse': ev['ape']['rmse'] if ev else None,
            'rpe_rmse': ev['rpe']['rmse'] if ev and ev.get('rpe') else None,
            'evaluated': ev['created'] if ev else None,
            'eval_error': failed['reason'] if failed else None,
        }

    def list(self):
        recording = self._recording()
        out = []
        if self.runs_dir.is_dir():
            for d in self.runs_dir.iterdir():
                if d.name.startswith('.') or not d.is_dir() or not (d / 'run.json').is_file():
                    continue
                try:
                    out.append(self._entry(d, recording))
                except (OSError, ValueError):
                    continue        # A half-written run.json: skip it, it shows up on the next refresh
        out.sort(key=lambda r: r['id'], reverse=True)
        return {'runs': out, 'params': self.params}

    def detail(self, run_id):
        d = self._dir(run_id)
        info = json.loads((d / 'run.json').read_text(encoding='utf-8'))
        entry = self._entry(d, self._recording(), info)
        if entry['type'] == 'navigation':
            return {**entry, 'info': info, 'eval': None, 'amcl': amcl_series(d / 'amcl.csv')}
        return {**entry, 'info': info, 'eval': self._cached(d), 'amcl': None}

    def figure(self, run_id, name):
        d = self._dir(run_id)
        if name not in NAV_FIGURES or not (d / name).is_file():
            raise EvalError('run_not_found', 404, id=f'{run_id}/{name}')
        return d / name

    # ---------------- evo ----------------

    def _worker(self, job, workdir, timeout):
        job_path = Path(workdir) / f'.eval_job_{threading.get_ident()}.json'
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        env = dict(os.environ, MPLBACKEND='Agg', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        try:
            p = subprocess.run([str(self.python), '-I', str(WORKER), str(job_path)], cwd='/tmp', env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise EvalError('evaluation_failed', 500, reason=f'{self.python} not found')
        except subprocess.TimeoutExpired:
            raise EvalError('evaluation_failed', 500, reason=f'evo took longer than {timeout} s')
        finally:
            job_path.unlink(missing_ok=True)
        if p.returncode:
            raise EvalError('evaluation_failed', 500,
                            reason=(p.stderr.strip().splitlines() or ['evo failed'])[-1])

    def evaluate(self, run_id, force=False):
        d = self._dir(run_id)
        entry = self._entry(d, self._recording())
        if not entry['evaluable']:
            raise EvalError('run_not_evaluable', 409, id=run_id)
        if entry['type'] == 'navigation':
            return self.detail(run_id)      # metrics come from nav_mission.py's record, nothing to compute
        if entry['eval_error'] and not force:
            raise EvalError('evaluation_failed', 422, reason=entry['eval_error'])
        with self.lock:
            ev = None if force else self._cached(d)
            if ev is None:
                job = {'task': 'evaluate', 'est': str(d / 'est.tum'), 'gt': str(d / 'gt.tum'),
                       'out': str(d / 'eval.json'), 'params': self.params}
                try:
                    self._worker(job, d, 120)
                    (d / 'eval_error.json').unlink(missing_ok=True)
                except EvalError as e:
                    # Remember the failure (bad data does not get better by retrying); force=True retries
                    (d / 'eval_error.json').write_text(json.dumps(
                        {'reason': e.params.get('reason'), 'params': self.params,
                         'created': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False), encoding='utf-8')
                    e.status = 422
                    raise
        return self.detail(run_id)

    def _results_root(self):
        """Configured export folder, or <data_root>/results when the configured folder's parent does not
        exist (e.g. a drive this machine does not have). The export result reports the folder actually used."""
        if self.results_dir == self.fallback_results or self.results_dir.parent.is_dir():
            return self.results_dir
        return self.fallback_results

    def export(self, ids, batch=None):
        """batch: optional batch id; adds that batch's mean ± std tables to the export."""
        if not isinstance(ids, list) or not ids or len(ids) > 50:
            raise EvalError('export_empty')
        rows, nav_rows = [], []
        for run_id in dict.fromkeys(ids):          # keep order, drop duplicates
            d = self._dir(run_id)
            entry = self._entry(d, self._recording())
            if not entry['evaluable']:
                continue
            if entry['type'] == 'navigation':
                info = json.loads((d / 'run.json').read_text(encoding='utf-8'))
                nav_rows.append((run_id, d, info, entry['nav']))
                continue
            try:
                ev = self.evaluate(run_id)['eval']
            except EvalError:
                continue            # Listed under 'skipped'
            info = json.loads((d / 'run.json').read_text(encoding='utf-8'))
            rows.append((run_id, d, info, ev))
        if not rows and not nav_rows:
            raise EvalError('export_empty')

        stamp = time.strftime('%Y%m%d_%H%M%S')
        out = self._results_root() / stamp
        n = 2
        while out.exists():
            out = out.parent / f'{stamp}_{n}'
            n += 1
        try:
            out.mkdir(parents=True)
        except OSError as e:
            raise EvalError('export_failed', 500, reason=str(e))

        files = ['summary.csv'] if rows else []
        try:
            if nav_rows:
                files += write_csvs(out, [(run_id, info, m) for run_id, _, info, m in nav_rows])
                (out / 'runs').mkdir(exist_ok=True)
                for run_id, d, _, _ in nav_rows:
                    shutil.copy2(d / 'run.json', out / 'runs' / f'{run_id}_run.json')
                    for f in NAV_FIGURES:
                        if (d / f).is_file():
                            shutil.copy2(d / f, out / f'{run_id}_{f}')
                            files.append(f'{run_id}_{f}')
            if rows:
                self._write_mapping_csv(out, rows)
            if batch:
                files += self._write_batch(out, batch)
        except OSError as e:
            raise EvalError('export_failed', 500, reason=str(e))

        job = {'task': 'export', 'out_dir': str(out),
               'runs': [{'id': run_id, 'label': run_id, 'eval': str(d / 'eval.json')} for run_id, d, _, _ in rows],
               'nav': [{'id': run_id, 'label': run_id, 'amcl_csv': str(d / 'amcl.csv'), 'metrics': m}
                       for run_id, d, _, m in nav_rows]}
        with self.lock:
            self._worker(job, out, 180)
        figures = json.loads((out / 'figures.json').read_text(encoding='utf-8'))
        (out / 'figures.json').unlink()
        done = [r[0] for r in rows] + [r[0] for r in nav_rows]
        return {'dir': windows_path(out), 'files': [*files, *figures], 'runs': done,
                'skipped': [i for i in ids if i not in set(done)]}

    def _write_batch(self, out, batch_id):
        """batch.json of a batch experiment (batch.py) -> batch_summary.csv (mean ± std) + batch_runs.csv."""
        if not re.fullmatch(r'batch_[0-9_]{8,40}', batch_id):
            raise EvalError('batch_not_found', 404, id=batch_id)
        src = self.runs_dir / batch_id / 'batch.json'
        try:
            b = json.loads(src.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise EvalError('batch_not_found', 404, id=batch_id)
        shutil.copy2(src, out / f'{batch_id}.json')
        s = b.get('summary') or {}
        with open(out / 'batch_summary.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['batch_id', 'scene', 'slam', 'runs', 'succeeded', 'metric', 'mean', 'std', 'min', 'max', 'n'])
            for key, label in (('ape_rmse', 'ate_rmse_m'), ('rpe_rmse', 'rpe_rmse_m'), ('length', 'length_m'),
                               ('duration', 'duration_s'), ('rtf', 'rtf'), ('wall_s', 'wall_s')):
                m = s.get(key) or {}
                w.writerow([batch_id, b.get('preset'), b.get('slam'), s.get('runs'), s.get('succeeded'), label,
                            m.get('mean', ''), m.get('std', ''), m.get('min', ''), m.get('max', ''), m.get('n', 0)])
        with open(out / 'batch_runs.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['batch_id', 'index', 'run_id', 'ok', 'failure', 'ate_rmse_m', 'rpe_rmse_m', 'length_m',
                        'duration_s', 'rtf', 'drive_sim_s', 'drive_wall_s', 'wall_s', 'map', 'started', 'ended'])
            for r in b.get('runs') or []:
                w.writerow([batch_id, r.get('index'), r.get('run_id') or '', r.get('ok'), r.get('code') or '',
                            r.get('ape_rmse', ''), r.get('rpe_rmse', ''), r.get('length', ''), r.get('duration', ''),
                            r.get('rtf', ''), r.get('drive_sim_s', ''), r.get('drive_wall_s', ''), r.get('wall_s', ''),
                            r.get('map') or '', r.get('started'), r.get('ended')])
        return ['batch_summary.csv', 'batch_runs.csv', f'{batch_id}.json']

    @staticmethod
    def _write_mapping_csv(out, rows):
        fields = ['run_id', 'type', 'scene', 'started', 'duration_s', 'length_m', 'matched_poses',
                  'ate_rmse_m', 'ate_mean_m', 'ate_median_m', 'ate_std_m', 'ate_max_m',
                  'rpe_rmse_m', 'rpe_mean_m', 'rpe_max_m', 'rpe_delta_m', 'max_diff_s', 'evo', 'maps']
        # utf-8-sig so that Excel opens Chinese text correctly
        with open(out / 'summary.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(fields)
            for run_id, _, info, ev in rows:
                a, r = ev['ape'], ev.get('rpe') or {}
                w.writerow([run_id, info.get('type', 'mapping'), info.get('preset') or '', info.get('started'),
                            ev['duration'], ev['length'], ev['matched_poses'],
                            a['rmse'], a['mean'], a['median'], a['std'], a['max'],
                            r.get('rmse', ''), r.get('mean', ''), r.get('max', ''),
                            ev['params']['rpe_delta'], ev['params']['max_diff'], ev['evo'],
                            ';'.join(info.get('maps') or [])])
        for run_id, d, _, _ in rows:
            (out / 'runs').mkdir(exist_ok=True)
            for f in ('run.json', 'eval.json'):
                shutil.copy2(d / f, out / 'runs' / f'{run_id}_{f}')


def register_evaluation_routes(app, cfg, mapping):
    evaluation = Evaluation(cfg, mapping)

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/evaluation/runs')
    def evaluation_runs():
        return evaluation.list()

    @app.get('/api/evaluation/runs/{run_id}')
    def evaluation_run(run_id: str):
        try:
            return evaluation.detail(run_id)
        except EvalError as e:
            raise fail(e)

    @app.post('/api/evaluation/runs/{run_id}/evaluate')
    def evaluation_evaluate(run_id: str, force: bool = False):
        try:
            return evaluation.evaluate(run_id, force)
        except EvalError as e:
            raise fail(e)

    @app.get('/api/evaluation/runs/{run_id}/files/{name}')
    def evaluation_file(run_id: str, name: str):
        try:
            return FileResponse(evaluation.figure(run_id, name), media_type='image/png')
        except EvalError as e:
            raise fail(e)

    @app.post('/api/evaluation/export')
    def evaluation_export(ids: list = Body(..., embed=True), batch: str | None = Body(None, embed=True)):
        try:
            return evaluation.export([str(i) for i in ids], batch)
        except EvalError as e:
            raise fail(e)

    return evaluation
