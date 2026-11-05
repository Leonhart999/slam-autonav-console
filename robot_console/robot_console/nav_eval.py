"""Navigation run records on the evaluation page (format written by nav_mission.py, read-only here).

~/runs/nav_<time>/run.json holds the goals with their results and a summary; amcl.csv the AMCL error over
time; path.png and amcl_error.png are drawn by nav_figures.py when the mission ends.

Per-run metrics (nav_metrics) and per-variant pooling (by_variant) are defined here once so that the page,
the CSV export and the figures agree:
  success_rate     succeeded / finished goals
  mean_time_s      mean time of the succeeded goals
  mean_path_m      mean driven distance of the succeeded goals
  mean_goal_error_m  mean arrival error of the succeeded goals (ground truth vs goal)
  mean_loc_error_m   mean localization error at arrival (map -> base_link vs ground truth), all finished goals
  amcl_*           over every /amcl_pose of the run (from run.json summary.amcl_error)
"""
import csv
import math

FIGURES = ('path.png', 'amcl_error.png')
MAX_SERIES = 2000


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def nav_metrics(info):
    goals = info.get('goals') or []
    done = [g for g in goals if g.get('result')]
    ok = [g for g in done if g['result'] == 'succeeded']
    summary = info.get('summary') or {}
    amcl = summary.get('amcl_error') or {}
    clear = [g.get('min_clearance_m') for g in done if g.get('min_clearance_m') is not None]
    return {
        'variant': info.get('variant'), 'map': info.get('map'), 'state': info.get('state'),
        'goals': len(goals), 'finished': len(done), 'succeeded': len(ok),
        'success_rate': round(len(ok) / len(done), 3) if done else None,
        'mean_time_s': _mean([g.get('time_s') for g in ok]),
        'mean_path_m': _mean([g.get('path_m') for g in ok]),
        'mean_goal_error_m': _mean([g.get('goal_error_m') for g in ok]),
        'mean_loc_error_m': _mean([g.get('loc_error_m') for g in done]),
        'total_time_s': round(sum(g.get('time_s') or 0 for g in done), 1),
        'total_path_m': round(sum(g.get('path_m') or 0 for g in done), 2),
        'recoveries': sum(g.get('recoveries') or 0 for g in done),
        'min_clearance_m': min(clear) if clear else None,
        'amcl_samples': amcl.get('samples'), 'amcl_rmse_m': amcl.get('rmse_m'),
        'amcl_mean_m': amcl.get('mean_m'), 'amcl_max_m': amcl.get('max_m'),
        'has_gt': bool(info.get('ground_truth')),
    }


def by_variant(runs):
    """runs: [(info, metrics)] -> one pooled row per Nav2 parameter variant (goals pooled across runs)."""
    groups = {}
    for info, m in runs:
        groups.setdefault(m['variant'] or '—', []).append((info, m))
    rows = []
    for variant, items in groups.items():
        goals = [g for info, _ in items for g in (info.get('goals') or []) if g.get('result')]
        ok = [g for g in goals if g['result'] == 'succeeded']
        n = sum(m['amcl_samples'] or 0 for _, m in items if m['amcl_rmse_m'] is not None)
        sq = sum((m['amcl_samples'] or 0) * m['amcl_rmse_m'] ** 2 for _, m in items if m['amcl_rmse_m'] is not None)
        mean = sum((m['amcl_samples'] or 0) * m['amcl_mean_m'] for _, m in items if m['amcl_mean_m'] is not None)
        rows.append({
            'variant': variant, 'runs': len(items), 'goals': len(goals), 'succeeded': len(ok),
            'success_rate': round(len(ok) / len(goals), 3) if goals else None,
            'mean_time_s': _mean([g.get('time_s') for g in ok]),
            'mean_path_m': _mean([g.get('path_m') for g in ok]),
            'mean_goal_error_m': _mean([g.get('goal_error_m') for g in ok]),
            'mean_loc_error_m': _mean([g.get('loc_error_m') for g in goals]),
            'amcl_rmse_m': round(math.sqrt(sq / n), 3) if n else None,
            'amcl_mean_m': round(mean / n, 3) if n else None,
            'recoveries': sum(g.get('recoveries') or 0 for g in goals),
        })
    rows.sort(key=lambda r: r['variant'])
    return rows


def amcl_series(path):
    """amcl.csv -> {'t': [...], 'error': [...], 'goals': [[goal index, first t], ...]}, at most MAX_SERIES points."""
    t, err, starts = [], [], {}
    try:
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    ti = float(row['t'])
                except (KeyError, ValueError):
                    continue
                goal = row.get('goal')
                if goal and goal not in starts:
                    starts[goal] = ti
                if row.get('error_xy'):
                    t.append(ti)
                    err.append(float(row['error_xy']))
    except OSError:
        return None
    if len(t) > MAX_SERIES:
        step = len(t) / MAX_SERIES
        idx = sorted({int(i * step) for i in range(MAX_SERIES)} | {len(t) - 1})
        t, err = [t[i] for i in idx], [err[i] for i in idx]
    return {'t': [round(v, 2) for v in t], 'error': [round(v, 4) for v in err],
            'goals': [[int(k), round(v, 2)] for k, v in sorted(starts.items(), key=lambda kv: kv[1])]}


GOAL_FIELDS = ['run_id', 'variant', 'index', 'x', 'y', 'yaw', 'result', 'time_s', 'path_m', 'goal_error_m',
               'goal_yaw_error_deg', 'loc_error_m', 'recoveries', 'min_clearance_m', 'speed_scale']
RUN_FIELDS = ['run_id', 'scene', 'map', 'variant', 'started', 'state', 'goals', 'finished', 'succeeded',
              'success_rate', 'mean_time_s', 'mean_path_m', 'mean_goal_error_m', 'mean_loc_error_m',
              'amcl_rmse_m', 'amcl_mean_m', 'amcl_max_m', 'amcl_samples', 'recoveries', 'min_clearance_m',
              'total_time_s', 'total_path_m']
VARIANT_FIELDS = ['variant', 'runs', 'goals', 'succeeded', 'success_rate', 'mean_time_s', 'mean_path_m',
                  'mean_goal_error_m', 'mean_loc_error_m', 'amcl_rmse_m', 'amcl_mean_m', 'recoveries']


def _cell(v):
    return '' if v is None else v


def write_csvs(out, runs):
    """runs: [(run_id, info, metrics)]. Writes nav_summary.csv, nav_goals.csv, nav_by_variant.csv."""
    with open(out / 'nav_summary.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(RUN_FIELDS)
        for run_id, info, m in runs:
            row = {**m, 'run_id': run_id, 'scene': info.get('preset'), 'started': info.get('started')}
            w.writerow([_cell(row.get(k)) for k in RUN_FIELDS])
    with open(out / 'nav_goals.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(GOAL_FIELDS)
        for run_id, info, m in runs:
            for g in info.get('goals') or []:
                row = {**g, 'run_id': run_id, 'variant': m['variant']}
                w.writerow([_cell(row.get(k)) for k in GOAL_FIELDS])
    with open(out / 'nav_by_variant.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(VARIANT_FIELDS)
        for row in by_variant([(info, m) for _, info, m in runs]):
            w.writerow([_cell(row.get(k)) for k in VARIANT_FIELDS])
    return ['nav_summary.csv', 'nav_goals.csv', 'nav_by_variant.csv']
