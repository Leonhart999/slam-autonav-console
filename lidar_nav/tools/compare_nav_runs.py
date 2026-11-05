#!/usr/bin/env python3
"""Compare several navigation runs (one row per run): success, goal error, AMCL error, time.

Reads ~/results/<run>.csv (written by nav_goals) and ~/results/<run>_loc_error.csv (written by
plot_nav_run.py). Prints a table, writes <out>.csv and a bar chart <out>.png.

  ~/venvs/evo/bin/python compare_nav_runs.py --out ~/results/amcl_ablation \
      nav_wh_02:default nav_wh_sensor:sensor nav_wh_motion:motion nav_wh_both:both
(each argument is run_name[:label])
"""
import argparse
import csv
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

RESULTS = os.path.expanduser('~/results')


def load(run):
    with open(os.path.join(RESULTS, run + '.csv'), newline='') as f:
        rows = list(csv.reader(f))[1:]
    ok = sum(r[3] == '成功' for r in rows)          # nav_goals writes the result column in Chinese
    t = sum(float(r[4]) for r in rows)
    path = sum(float(r[5]) for r in rows)
    goal_err = np.array([float(r[6]) for r in rows])
    loc = np.loadtxt(os.path.join(RESULTS, run + '_loc_error.csv'), delimiter=',', skiprows=1)[:, 1]
    return {'goals': len(rows), 'ok': ok, 'time': t, 'path': path,
            'goal_mean': goal_err.mean(), 'goal_max': goal_err.max(),
            'loc_mean': loc.mean(), 'loc_median': np.median(loc), 'loc_max': loc.max(),
            'loc_over': (loc > 0.25).mean()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = os.path.expanduser(args.out)

    runs = [(r.split(':')[0], r.split(':')[-1]) for r in args.runs]
    stats = [(label, load(run)) for run, label in runs]
    cols = ['goals', 'ok', 'time', 'path', 'goal_mean', 'goal_max', 'loc_mean', 'loc_median', 'loc_max', 'loc_over']
    with open(out + '.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['run'] + cols)
        for label, s in stats:
            w.writerow([label] + [f'{s[c]:.3f}' if isinstance(s[c], float) else s[c] for c in cols])
    print(f'{"run":10s} {"ok":>5s} {"time s":>7s} {"goal err mean/max m":>20s} {"AMCL err mean/median/max m":>27s} {">0.25m":>7s}')
    for label, s in stats:
        print(f'{label:10s} {s["ok"]:>2d}/{s["goals"]:<2d} {s["time"]:7.1f} '
              f'{s["goal_mean"]:9.3f} / {s["goal_max"]:.3f} {s["loc_mean"]:10.3f} / {s["loc_median"]:.3f} / {s["loc_max"]:.3f} '
              f'{s["loc_over"]:7.0%}')

    labels = [l for l, _ in stats]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.bar(x - 0.27, [s['loc_mean'] for _, s in stats], 0.27, label='AMCL error mean')
    ax.bar(x, [s['loc_max'] for _, s in stats], 0.27, label='AMCL error max')
    ax.bar(x + 0.27, [s['goal_max'] for _, s in stats], 0.27, label='goal error max')
    ax.axhline(0.25, color='gray', ls=':', lw=1, label='goal tolerance 0.25 m')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel('error (m)')
    ax.set_title('Warehouse navigation: AMCL parameter variants')
    ax.legend(fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    fig.savefig(out + '.png', dpi=150)
    print(f'saved {out}.csv and {out}.png')


if __name__ == '__main__':
    main()
