"""Evaluation page (step 5a): run list, evo APE/RPE per run, CSV + PNG export.

Run folders are written by runs.py (~/runs/<id>/{est.tum, gt.tum, run.json}). The metrics are computed by
eval_worker.py inside the evo venv and cached as <run>/eval.json; the cache is reused while it is newer
than both trajectory files and was made with the same parameters.
Navigation runs (nav_<time>, written by nav_mission.py) are read through nav_eval.py; no evo involved.
Exports go to evaluation.results_dir/<time>/ (default <data_root>/results), or <data_root>/results/<time>/
when the configured folder's parent does not exist (e.g. a Windows drive that another machine does not have).

Playback runs (SLAM fed from a rosbag; type 'playback', older ones are mapping runs with source 'bag') have three
levels of reference (gt_kind):
  'gt'    the bag carried ground truth: ATE / RPE as for mapping runs
  'ref'   the bag carried reference odometry (ref.tum, e.g. wheel odometry): "deviation from reference", x/y only,
          cached as eval_ref.json; never shown as ATE
  'none'  only intrinsic numbers from est.tum (stats.json): start-to-end distance (= loop closure error when the
          bag part is marked as a loop, <runs_dir>/.bag_notes.json), height drift, SLAM lag
Older playback runs could record a simulation's /ground_truth that ran at the same time (gt_suspect): their gt.tum
is ignored. Playback runs of the same bag part can be compared with each other (relative(): APE of each run
against a baseline run's estimate, cached as rel_<baseline>.json).
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
MAX_TRAJ = 2000         # points per trajectory sent to the page
MAX_RELATIVE = 8        # runs in one relative comparison


def run_kind(info):
    """'mapping' | 'navigation' | 'playback'. Playback runs recorded before the type existed are mapping runs
    with source 'bag'."""
    kind = info.get('type') or 'mapping'
    if kind == 'mapping' and info.get('source') == 'bag':
        return 'playback'
    return kind


def gt_suspect(info, gt_topic='/ground_truth'):
    """A playback run whose gt.tum did not come from the bag. Before gt_source was recorded, a simulation running
    at the same time could leak its /ground_truth into the run: such an older run counts as suspect when a
    simulation was running (preset set) or its bag does not have the ground-truth topic (or is gone)."""
    if 'gt_source' in info:
        return info['gt_source'] != 'bag'
    if info.get('preset'):
        return True
    return not _bag_has_topic(info.get('bag') or '', gt_topic)


_BAG_TOPICS = {}


def _bag_has_topic(bag, topic):
    """Is `topic` in the rosbag2 metadata.yaml of the folder `bag`? (cached per folder and file time)"""
    import yaml
    meta = os.path.join(bag, 'metadata.yaml')
    try:
        mtime = os.stat(meta).st_mtime_ns
    except OSError:
        return False
    hit = _BAG_TOPICS.get(meta)
    if not hit or hit[0] != mtime:
        try:
            with open(meta, encoding='utf-8') as f:
                m = yaml.safe_load(f) or {}
            topics = {t['topic_metadata']['name'] for t in
                      (m.get('rosbag2_bagfile_information') or {}).get('topics_with_message_count') or []}
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            topics = set()
        hit = _BAG_TOPICS[meta] = (mtime, topics)
    return topic in hit[1]


def _nonempty(path):
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _pick(n, limit=MAX_TRAJ):
    if n <= limit:
        return list(range(n))
    step = (n - 1) / (limit - 1)
    return sorted({round(i * step) for i in range(limit)})


def read_tum_xyz(path):
    """(t, x, y, z) lists of a TUM file; malformed lines are skipped."""
    t, x, y, z = [], [], [], []
    with open(path, encoding='utf-8') as f:
        for line in f:
            p = line.split()
            if len(p) != 8:
                continue
            try:
                v = [float(a) for a in p[:4]]
            except ValueError:
                continue
            t.append(v[0])
            x.append(v[1])
            y.append(v[2])
            z.append(v[3])
    return t, x, y, z


def trajectory_stats(path):
    """Intrinsic numbers of an estimated trajectory (no reference needed) and a downsampled copy for the charts.
    Heights are relative to the first pose."""
    t, x, y, z = read_tum_xyz(path)
    if len(t) < 2:
        return None
    length = 0.0
    for i in range(1, len(t)):
        length += ((x[i] - x[i - 1]) ** 2 + (y[i] - y[i - 1]) ** 2 + (z[i] - z[i - 1]) ** 2) ** 0.5
    z0 = z[0]
    dz = [v - z0 for v in z]
    k = max(range(len(dz)), key=lambda i: abs(dz[i]))
    idx = _pick(len(t))
    return {
        'poses': len(t), 'duration': round(t[-1] - t[0], 2), 'length': round(length, 2),
        'start_end': round(((x[-1] - x[0]) ** 2 + (y[-1] - y[0]) ** 2 + (z[-1] - z[0]) ** 2) ** 0.5, 3),
        'start_end_xy': round(((x[-1] - x[0]) ** 2 + (y[-1] - y[0]) ** 2) ** 0.5, 3),
        'z_end': round(dz[-1], 3), 'z_max_abs': round(abs(dz[k]), 3), 'z_max_at': round(t[k] - t[0], 1),
        'series': {'t': [round(t[i] - t[0], 2) for i in idx], 'x': [round(x[i], 3) for i in idx],
                   'y': [round(y[i], 3) for i in idx], 'z': [round(dz[i], 3) for i in idx], 't0': t[0]},
    }


def read_lag(path):
    """lag.csv -> [bag time from the first sample (s)], [lag (s)]."""
    ts, ls = [], []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                p = line.strip().split(',')
                if len(p) == 2:
                    try:
                        ts.append(float(p[0]))
                        ls.append(float(p[1]))
                    except ValueError:
                        pass
    except OSError:
        return None
    if not ts:
        return None
    idx = _pick(len(ts))
    return {'t': [round(ts[i], 2) for i in idx], 'lag': [ls[i] for i in idx]}


def note_key(bag, rng):
    """Key of a bag part in .bag_notes.json (the part rounded to 0.1 s)."""
    b, e = (rng or [0, 0])[:2]
    return f'{bag}|{float(b):.1f}|{float(e):.1f}'


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
        self.notes_path = self.runs_dir / '.bag_notes.json'
        self.gt_topic = cfg['mapping'].get('gt_topic') or '/ground_truth'
        self._notes_lock = threading.Lock()

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

    def _cached(self, d, name='eval.json', ref='gt.tum'):
        """eval.json (or eval_error.json, eval_ref.json, rel_<id>.json) if still valid for the current trajectories
        and parameters. ref: the reference file name in d, or a path (another run's est.tum)."""
        path = d / name
        try:
            ref_path = d / ref if isinstance(ref, str) else ref
            newest = max(p.stat().st_mtime for p in (d / 'est.tum', ref_path))
            if path.stat().st_mtime < newest:
                return None
            ev = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        p = ev.get('params', {})
        if any(p.get(k) != v for k, v in self.params.items()):
            return None
        return ev

    def _stats(self, d):
        """trajectory_stats() of est.tum, cached as stats.json while est.tum is unchanged."""
        est = d / 'est.tum'
        try:
            st = est.stat()
        except OSError:
            return None
        key = [st.st_mtime_ns, st.st_size]
        cache = d / 'stats.json'
        try:
            c = json.loads(cache.read_text(encoding='utf-8'))
            if c.get('key') == key:
                return c['stats']
        except (OSError, ValueError, KeyError):
            pass
        try:
            s = trajectory_stats(est)
        except OSError:
            return None
        try:
            tmp = d / 'stats.json.tmp'
            tmp.write_text(json.dumps({'key': key, 'stats': s}), encoding='utf-8')
            os.replace(tmp, cache)
        except OSError:
            pass        # read-only run folder: computed again next time
        return s

    def _notes(self):
        try:
            return json.loads(self.notes_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def set_loop(self, run_id, loop):
        """Mark the bag part of a playback run as a loop (start and end at the same place), for all runs of it."""
        d = self._dir(run_id)
        info = json.loads((d / 'run.json').read_text(encoding='utf-8'))
        if run_kind(info) != 'playback' or not info.get('bag'):
            raise EvalError('run_not_evaluable', 409, id=run_id)
        with self._notes_lock:
            notes = self._notes()
            key = note_key(info['bag'], info.get('bag_range'))
            if loop:
                notes[key] = {**notes.get(key, {}), 'loop': True}
            else:
                notes.pop(key, None)
            tmp = self.notes_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, self.notes_path)
        return self.detail(run_id)

    def _entry(self, d, recording, info=None, notes=None):
        info = info or json.loads((d / 'run.json').read_text(encoding='utf-8'))
        kind = run_kind(info)
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
                'algorithm': None, 'playback': None,
            }
        has_est = _nonempty(d / 'est.tum')
        suspect = kind == 'playback' and gt_suspect(info, self.gt_topic)
        gt_ok = has_est and _nonempty(d / 'gt.tum') and not suspect
        ev = self._cached(d) if gt_ok else None
        failed = None if ev or not gt_ok else self._cached(d, 'eval_error.json')
        pb = None
        if kind == 'playback':
            has_ref = has_est and _nonempty(d / 'ref.tum')
            ref_ev = self._cached(d, 'eval_ref.json', 'ref.tum') if has_ref else None
            ref_failed = None if ref_ev or not has_ref else self._cached(d, 'eval_ref_error.json', 'ref.tum')
            stats = self._stats(d) if has_est else None
            bag = info.get('bag') or ''
            notes = self._notes() if notes is None else notes
            pb = {
                'bag': bag, 'bag_name': info.get('bag_name') or os.path.basename(bag.rstrip('/')) or '—',
                'range': info.get('bag_range'), 'rate': info.get('bag_rate'),
                'sensor': info.get('sensor'), 'sensor_label': info.get('sensor_label'),
                'gt_kind': 'gt' if gt_ok else 'ref' if has_ref else 'none',
                'gt_suspect': suspect and _nonempty(d / 'gt.tum'),
                'ref_source': info.get('ref_source'), 'ref_label': info.get('ref_label'),
                'ref_ape_rmse': ref_ev['ape']['rmse'] if ref_ev else None,
                'ref_error': ref_failed['reason'] if ref_failed else None,
                'loop': bool(notes.get(note_key(bag, info.get('bag_range')), {}).get('loop')),
                'lag': info.get('lag'),
                'stats': {k: v for k, v in stats.items() if k != 'series'} if stats else None,
            }
        return {
            'id': d.name, 'type': kind, 'preset': info.get('preset'),
            'started': info.get('started'), 'ended': info.get('ended'),
            'duration': info.get('duration'), 'length': info.get('length'),
            'maps': info.get('maps') or [], 'recording': d.name == recording,
            'evaluable': gt_ok and not info.get('recording_error'),
            'variant': None, 'nav': None, 'figures': [],
            'ape_rmse': ev['ape']['rmse'] if ev else None,
            'rpe_rmse': ev['rpe']['rmse'] if ev and ev.get('rpe') else None,
            'evaluated': ev['created'] if ev else None,
            'eval_error': info.get('recording_error') or (failed['reason'] if failed else None),
            'algorithm': info.get('algorithm'), 'playback': pb,
        }

    def list(self):
        recording = self._recording()
        notes = self._notes()
        out = []
        if self.runs_dir.is_dir():
            for d in self.runs_dir.iterdir():
                if d.name.startswith('.') or not d.is_dir() or not (d / 'run.json').is_file():
                    continue
                try:
                    out.append(self._entry(d, recording, notes=notes))
                except (OSError, ValueError):
                    continue        # A half-written run.json: skip it, it shows up on the next refresh
        # newest first; map_* and play_* ids no longer sort by time among each other
        out.sort(key=lambda r: (r['started'] or '', r['id']), reverse=True)
        return {'runs': out, 'params': self.params}

    def detail(self, run_id):
        d = self._dir(run_id)
        info = json.loads((d / 'run.json').read_text(encoding='utf-8'))
        entry = self._entry(d, self._recording(), info)
        if entry['type'] == 'navigation':
            return {**entry, 'info': info, 'eval': None, 'ref_eval': None, 'amcl': amcl_series(d / 'amcl.csv')}
        ref_eval = self._cached(d, 'eval_ref.json', 'ref.tum') if entry['type'] == 'playback' else None
        return {**entry, 'info': info, 'eval': self._cached(d) if entry['evaluable'] else None,
                'ref_eval': ref_eval, 'amcl': None}

    def trajectory(self, run_id):
        """Estimated trajectory (downsampled, time from its first pose, height relative to it) and the SLAM lag
        of a mapping / playback run; drawn without evo."""
        d = self._dir(run_id)
        s = self._stats(d)
        if not s:
            raise EvalError('run_not_evaluable', 409, id=run_id)
        lag = read_lag(d / 'lag.csv')
        if lag:
            t0 = s['series']['t0']
            lag['t'] = [round(t - t0, 2) for t in lag['t']]
        return {'id': run_id, **s['series'], 'lag': lag}

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

    def _run_evo(self, d, ref_path, out_name, error_name, planar=False, timeout=120):
        """evo APE/RPE of d/est.tum against ref_path into d/out_name; a failure is remembered in d/error_name
        (bad data does not get better by retrying; force=True retries)."""
        job = {'task': 'evaluate', 'est': str(d / 'est.tum'), 'gt': str(ref_path),
               'out': str(d / out_name), 'params': self.params, 'planar': planar}
        try:
            self._worker(job, d, timeout)
            (d / error_name).unlink(missing_ok=True)
        except EvalError as e:
            (d / error_name).write_text(json.dumps(
                {'reason': e.params.get('reason'), 'params': self.params,
                 'created': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False), encoding='utf-8')
            e.status = 422
            raise

    def evaluate(self, run_id, force=False):
        """Ground-truth APE/RPE (eval.json) and, for a playback run with reference odometry, the deviation from
        the reference (eval_ref.json, x/y only)."""
        d = self._dir(run_id)
        entry = self._entry(d, self._recording())
        if entry['type'] == 'navigation':
            if not entry['evaluable']:
                raise EvalError('run_not_evaluable', 409, id=run_id)
            return self.detail(run_id)      # metrics come from nav_mission.py's record, nothing to compute
        pb = entry['playback']
        has_ref = bool(pb and _nonempty(d / 'ref.tum'))
        if not entry['evaluable'] and not has_ref:
            raise EvalError('run_not_evaluable', 409, id=run_id)
        if has_ref and (force or not pb['ref_error']):
            with self.lock:
                if force or self._cached(d, 'eval_ref.json', 'ref.tum') is None:
                    try:
                        self._run_evo(d, d / 'ref.tum', 'eval_ref.json', 'eval_ref_error.json', planar=True)
                    except EvalError:
                        if not entry['evaluable']:
                            raise
        if not entry['evaluable']:
            return self.detail(run_id)
        if entry['eval_error'] and not force:
            raise EvalError('evaluation_failed', 422, reason=entry['eval_error'])
        with self.lock:
            if force or self._cached(d) is None:
                self._run_evo(d, d / 'gt.tum', 'eval.json', 'eval_error.json')
        return self.detail(run_id)

    def relative(self, ids, baseline):
        """APE of each run's estimate against the baseline run's estimate (same bag part, so the same bag time
        stamps), cached as <run>/rel_<baseline>.json. Not accuracy: it shows how far the runs differ."""
        if not isinstance(ids, list) or baseline not in ids or not 2 <= len(set(ids)) <= MAX_RELATIVE:
            raise EvalError('relative_bad_selection', 400, n=MAX_RELATIVE)
        base = self._dir(baseline)
        if not _nonempty(base / 'est.tum'):
            raise EvalError('run_not_evaluable', 409, id=baseline)
        rows = []
        for run_id in dict.fromkeys(ids):
            if run_id == baseline:
                continue
            d = self._dir(run_id)
            name = f'rel_{baseline}.json'
            ev = self._cached(d, name, base / 'est.tum')
            error = None
            if ev is None:
                try:
                    with self.lock:
                        self._run_evo(d, base / 'est.tum', name, f'rel_{baseline}_error.json')
                    ev = self._cached(d, name, base / 'est.tum')
                except EvalError as e:
                    error = e.params.get('reason')
            rows.append({'id': run_id, 'eval': ev, 'error': error})
        return {'baseline': baseline, 'rows': rows}

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
        rows, nav_rows, pb_rows = [], [], []
        for run_id in dict.fromkeys(ids):          # keep order, drop duplicates
            d = self._dir(run_id)
            entry = self._entry(d, self._recording())
            if entry['type'] == 'playback' and entry['playback']['stats']:
                # every playback run goes into playback_summary.csv, with or without a reference
                if entry['playback']['gt_kind'] == 'ref' and not entry['playback']['ref_error']:
                    try:
                        entry = self.evaluate(run_id)
                    except EvalError:
                        pass
                pb_rows.append(entry)
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
        if not rows and not nav_rows and not pb_rows:
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
            if pb_rows:
                recording = self._recording()
                files.append(self._write_playback_csv(
                    out, [self._entry(self._dir(e['id']), recording) for e in pb_rows]))
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
        done += [e['id'] for e in pb_rows if e['id'] not in set(done)]
        return {'dir': windows_path(out), 'files': [*files, *figures], 'runs': done,
                'skipped': [i for i in ids if i not in set(done)]}

    @staticmethod
    def _write_playback_csv(out, entries):
        """playback_summary.csv: bag part, configuration and the numbers that need no ground truth."""
        def label(v):
            return v.get('en') or v.get('zh') if isinstance(v, dict) else v or ''

        with open(out / 'playback_summary.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['run_id', 'started', 'bag', 'bag_begin_s', 'bag_end_s', 'rate', 'sensor', 'algorithm',
                        'reference', 'duration_s', 'length_m', 'start_end_m', 'start_end_xy_m', 'loop',
                        'z_end_m', 'z_max_abs_m', 'z_max_at_s', 'lag_mean_s', 'lag_max_s',
                        'ref_ape_rmse_xy_m', 'ate_rmse_m', 'rpe_rmse_m', 'maps'])
            for e in entries:
                p = e['playback']
                s = p['stats'] or {}
                rng = p['range'] or ['', '']
                lag = p['lag'] or {}
                w.writerow([e['id'], e['started'], p['bag'], rng[0], rng[1], p['rate'] or '', p['sensor'] or '',
                            e['algorithm'] or '', p['gt_kind'] if p['gt_kind'] != 'ref' else label(p['ref_label']),
                            s.get('duration', ''), s.get('length', ''), s.get('start_end', ''),
                            s.get('start_end_xy', ''), p['loop'], s.get('z_end', ''), s.get('z_max_abs', ''),
                            s.get('z_max_at', ''), lag.get('mean', ''), lag.get('max', ''),
                            p['ref_ape_rmse'] if p['ref_ape_rmse'] is not None else '',
                            e['ape_rmse'] if e['ape_rmse'] is not None else '',
                            e['rpe_rmse'] if e['rpe_rmse'] is not None else '', ';'.join(e['maps'])])
        return 'playback_summary.csv'

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
                w.writerow([run_id, run_kind(info), info.get('preset') or '', info.get('started'),
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

    @app.get('/api/evaluation/runs/{run_id}/trajectory')
    def evaluation_trajectory(run_id: str):
        try:
            return evaluation.trajectory(run_id)
        except EvalError as e:
            raise fail(e)

    @app.post('/api/evaluation/runs/{run_id}/loop')
    def evaluation_loop(run_id: str, loop: bool = Body(..., embed=True)):
        try:
            return evaluation.set_loop(run_id, loop)
        except EvalError as e:
            raise fail(e)
        except OSError as e:
            raise HTTPException(500, detail={'code': 'evaluation_failed', 'params': {'reason': str(e)}})

    @app.post('/api/evaluation/relative')
    def evaluation_relative(ids: list = Body(..., embed=True), baseline: str = Body(..., embed=True)):
        try:
            return evaluation.relative([str(i) for i in ids], baseline)
        except EvalError as e:
            raise fail(e)

    @app.post('/api/evaluation/export')
    def evaluation_export(ids: list = Body(..., embed=True), batch: str | None = Body(None, embed=True)):
        try:
            return evaluation.export([str(i) for i in ids], batch)
        except EvalError as e:
            raise fail(e)

    return evaluation
