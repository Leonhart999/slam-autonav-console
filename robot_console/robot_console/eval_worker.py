"""Trajectory evaluation and figure export with evo (runs in ~/venvs/evo, see evaluation.py).

Usage: python -I eval_worker.py <job.json>
  {"task": "evaluate", "est": path, "gt": path, "out": eval.json, "params": {...}}
      APE/RPE on the translation part after SE(3) Umeyama alignment, the same as
      `evo_ape tum gt.tum est.tum -a` and `evo_rpe tum gt.tum est.tum -a --delta 1 --delta_unit m`.
      Optional "planar": true flattens both trajectories to z = 0 first (a reference without height, e.g. wheel
      odometry). The same task compares two estimates (gt = the baseline run's est.tum) on the Evaluation page.
  {"task": "export", "out_dir": dir, "runs": [{"id", "label", "eval": eval.json path}, ...]}
      Writes PNG figures (trajectory overlay, APE/RPE over time, comparison) with matplotlib.
evo lives in its own venv (NumPy 2) so it never shares an interpreter with ROS 2.
"""
import json
import os
import sys
import time

import numpy as np

MAX_SERIES = 2000      # points per curve sent to the browser


def downsample(n, limit=MAX_SERIES):
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).round().astype(int))


def stats_of(metric):
    s = metric.get_all_statistics()
    return {k: round(float(s[k]), 6) for k in ('rmse', 'mean', 'median', 'std', 'min', 'max')}


def evaluate(job):
    import evo
    from evo.core import metrics, sync
    from evo.tools import file_interface

    p = {'max_diff': 0.01, 'rpe_delta': 1.0, **job.get('params', {})}
    ref = file_interface.read_tum_trajectory_file(job['gt'])
    est = file_interface.read_tum_trajectory_file(job['est'])
    ref, est = sync.associate_trajectories(ref, est, max_diff=p['max_diff'])
    if est.num_poses < 3:
        raise ValueError(f'only {est.num_poses} matched poses (max_diff {p["max_diff"]} s)')
    if job.get('planar'):
        # Reference without height (wheel odometry): compare x, y only, both trajectories flattened
        from evo.core.trajectory import PoseTrajectory3D

        def flat(tr):
            xyz = tr.positions_xyz.copy()
            xyz[:, 2] = 0.0
            return PoseTrajectory3D(positions_xyz=xyz, orientations_quat_wxyz=tr.orientations_quat_wxyz,
                                    timestamps=tr.timestamps)
        ref, est = flat(ref), flat(est)
    rot, trans, _ = est.align(ref, correct_scale=False)

    ape = metrics.APE(metrics.PoseRelation.translation_part)
    ape.process_data((ref, est))
    out = {
        'evo': evo.__version__, 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'params': {**p, 'align': 'se3', 'pose_relation': 'translation_part', 'planar': bool(job.get('planar'))},
        'matched_poses': int(est.num_poses),
        'duration': round(float(est.timestamps[-1] - est.timestamps[0]), 2),
        'length': round(float(est.path_length), 3),
        'ref_length': round(float(ref.path_length), 3),
        'alignment': {'rotation': np.round(rot, 6).tolist(), 'translation': np.round(trans, 4).tolist()},
        'ape': stats_of(ape), 'rpe': None,
    }
    t = est.timestamps - est.timestamps[0]
    i = downsample(len(t))
    series = {
        'traj_est': np.round(est.positions_xyz[i, :2], 3).tolist(),
        'traj_gt': np.round(ref.positions_xyz[i, :2], 3).tolist(),
        'ape': [np.round(t[i], 2).tolist(), np.round(ape.error[i], 4).tolist()],
        'rpe': None,
    }
    try:
        rpe = metrics.RPE(metrics.PoseRelation.translation_part, delta=p['rpe_delta'],
                          delta_unit=metrics.Unit.meters, all_pairs=False)
        rpe.process_data((ref, est))
        err = np.asarray(rpe.error)
        out['rpe'] = {**stats_of(rpe), 'pairs': int(len(err))}
        tr = t[np.asarray(rpe.delta_ids)[:len(err)]]
        j = downsample(len(tr))
        series['rpe'] = [np.round(tr[j], 2).tolist(), np.round(err[j], 4).tolist()]
    except Exception as e:      # Too short for one delta: keep APE, report why RPE is missing
        out['rpe_error'] = f'{type(e).__name__}: {e}'
    out['series'] = series
    tmp = job['out'] + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, job['out'])


# Categorical order of the console charts (same as web/src/evaluation/colors.ts)
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
GT_COLOR = '#7a879d'


def export(job):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.grid': True, 'grid.alpha': 0.25, 'savefig.dpi': 200})
    out_dir = job['out_dir']
    files = []

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, name))
        plt.close(fig)
        files.append(name)

    runs = []
    for r in job['runs']:
        with open(r['eval'], encoding='utf-8') as f:
            runs.append((r, json.load(f)))

    for r, ev in runs:
        s = ev['series']
        fig, ax = plt.subplots(figsize=(5.5, 5))
        gt, est = np.array(s['traj_gt']), np.array(s['traj_est'])
        ax.plot(gt[:, 0], gt[:, 1], '--', color=GT_COLOR, lw=1.5, label='Ground truth')
        ax.plot(est[:, 0], est[:, 1], color=SERIES[0], lw=1.5, label='Estimate (SE(3) aligned)')
        ax.plot(*gt[0], 'o', color=GT_COLOR, ms=5)
        ax.set_aspect('equal', adjustable='datalim')
        ax.set_xlabel('x (m)')
        ax.set_ylabel('y (m)')
        ax.set_title(f'{r["label"]}  ATE RMSE {ev["ape"]["rmse"]:.3f} m')
        ax.legend(loc='best', frameon=False)
        save(fig, f'{r["id"]}_traj.png')

        for key, title in (('ape', 'APE (translation)'), ('rpe', f'RPE per {ev["params"]["rpe_delta"]:g} m')):
            if not s.get(key) or not ev.get(key):
                continue
            fig, ax = plt.subplots(figsize=(7, 3))
            ax.plot(s[key][0], s[key][1], color=SERIES[0], lw=1.2)
            ax.axhline(ev[key]['rmse'], color=GT_COLOR, lw=1, ls='--')
            ax.annotate(f'RMSE {ev[key]["rmse"]:.3f} m', (1, ev[key]['rmse']),
                        xycoords=('axes fraction', 'data'), ha='right', va='bottom', fontsize=9, color='#4a5872')
            ax.set_xlabel('time (s)')
            ax.set_ylabel('error (m)')
            ax.set_title(f'{r["label"]}  {title}')
            ax.set_ylim(bottom=0)
            save(fig, f'{r["id"]}_{key}.png')

    if len(runs) > 1:
        fig, ax = plt.subplots(figsize=(7, 3.2))
        for k, (r, ev) in enumerate(runs[:len(SERIES)]):
            ax.plot(ev['series']['ape'][0], ev['series']['ape'][1], color=SERIES[k], lw=1.2,
                    label=f'{r["label"]} ({ev["ape"]["rmse"]:.3f} m)')
        ax.set_xlabel('time (s)')
        ax.set_ylabel('APE (m)')
        ax.set_ylim(bottom=0)
        ax.set_title('APE over time')
        ax.legend(frameon=False, fontsize=8)
        save(fig, 'compare_ape.png')

        fig, ax = plt.subplots(figsize=(max(4, 1.1 * len(runs) + 2), 3.2))
        x = np.arange(len(runs))
        w = 0.38
        ape = [ev['ape']['rmse'] for _, ev in runs]
        rpe = [ev['rpe']['rmse'] if ev.get('rpe') else 0 for _, ev in runs]
        ax.bar(x - w / 2 - 0.01, ape, w, color=SERIES[0], label='ATE RMSE')
        ax.bar(x + w / 2 + 0.01, rpe, w, color=SERIES[1], label='RPE RMSE')
        ax.set_xticks(x, [r['label'] for r, _ in runs], rotation=20, ha='right', fontsize=8)
        ax.set_ylabel('m')
        ax.legend(frameon=False)
        save(fig, 'compare_rmse.png')

    nav = job.get('nav') or []
    if nav:
        export_nav(nav, plt, save)

    with open(os.path.join(out_dir, 'figures.json'), 'w', encoding='utf-8') as f:
        json.dump(files, f)


NAV_PANELS = [('success_rate', 'Success rate (%)', 100), ('mean_time_s', 'Mean time per goal (s)', 1),
              ('mean_path_m', 'Mean path per goal (m)', 1), ('amcl_rmse_m', 'AMCL error RMSE (m)', 1)]


def nav_bars(plt, save, labels, metrics, name, title):
    """Small multiples: one panel per metric, one bar per run (or variant). No shared y axis."""
    fig, axes = plt.subplots(1, len(NAV_PANELS), figsize=(3.1 * len(NAV_PANELS), 3.4))
    x = np.arange(len(labels))
    colors = [SERIES[k % len(SERIES)] for k in range(len(labels))]
    for ax, (key, ylabel, scale) in zip(axes, NAV_PANELS):
        vals = [m.get(key) for m in metrics]
        ax.bar(x, [0 if v is None else v * scale for v in vals], 0.62, color=colors)
        for xi, v in zip(x, vals):
            ax.annotate('n/a' if v is None else f'{v * scale:.3g}', (xi, 0 if v is None else v * scale),
                        ha='center', va='bottom', fontsize=8, color='#4a5872')
        ax.set_title(ylabel, fontsize=9.5)
        ax.set_xticks(x, labels, rotation=30, ha='right', fontsize=7.5)
        ax.grid(axis='x', visible=False)
    fig.suptitle(title, fontsize=11)
    save(fig, name)


def export_nav(nav, plt, save):
    import csv
    labels = [f'{r["label"]}\n{r["metrics"].get("variant") or ""}' for r in nav]
    nav_bars(plt, save, labels, [r['metrics'] for r in nav], 'nav_compare.png', 'Navigation runs')

    variants = {}
    for r in nav:
        variants.setdefault(r['metrics'].get('variant') or '-', []).append(r['metrics'])
    if len(variants) > 1:
        pooled = []
        for v, ms in variants.items():
            done = sum(m['finished'] for m in ms)
            ok = sum(m['succeeded'] for m in ms)

            def wmean(key):
                pairs = [(m[key], m['succeeded']) for m in ms if m.get(key) is not None and m['succeeded']]
                w = sum(n for _, n in pairs)
                return sum(val * n for val, n in pairs) / w if w else None
            n = sum(m['amcl_samples'] or 0 for m in ms if m.get('amcl_rmse_m') is not None)
            sq = sum((m['amcl_samples'] or 0) * m['amcl_rmse_m'] ** 2 for m in ms if m.get('amcl_rmse_m') is not None)
            pooled.append({'success_rate': ok / done if done else None, 'mean_time_s': wmean('mean_time_s'),
                           'mean_path_m': wmean('mean_path_m'), 'amcl_rmse_m': (sq / n) ** 0.5 if n else None})
        nav_bars(plt, save, list(variants), pooled, 'nav_by_variant.png', 'Navigation by Nav2 parameter variant')

    curves = []
    for r in nav[:len(SERIES)]:
        t, e = [], []
        try:
            with open(r['amcl_csv'], newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if row.get('error_xy'):
                        t.append(float(row['t']))
                        e.append(float(row['error_xy']))
        except OSError:
            continue
        if t:
            curves.append((r, t, e))
    if curves:
        fig, ax = plt.subplots(figsize=(7, 3.2))
        for k, (r, t, e) in enumerate(curves):
            rmse = r['metrics'].get('amcl_rmse_m')
            ax.plot(t, e, color=SERIES[k], lw=1.2,
                    label=f'{r["label"]} {r["metrics"].get("variant") or ""}'
                          + (f' ({rmse:.3f} m)' if rmse is not None else ''))
        ax.set_xlabel('time (s)')
        ax.set_ylabel('AMCL error (m)')
        ax.set_ylim(bottom=0)
        ax.set_title('AMCL position error over time')
        ax.legend(frameon=False, fontsize=8)
        save(fig, 'nav_compare_amcl.png')


def main():
    with open(sys.argv[1], encoding='utf-8') as f:
        job = json.load(f)
    {'evaluate': evaluate, 'export': export}[job['task']](job)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:      # One line on stderr; the console shows it to the user
        print(f'{type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
