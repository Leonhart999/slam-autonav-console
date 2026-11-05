"""Figures for one navigation run (~/runs/nav_*/); separate process, started by nav_mission.py.

  path.png        2D map + ground-truth path + localization estimate + goals (numbered, green = reached)
  amcl_error.png  AMCL position error vs time, with goal boundaries

Usage: python -m robot_console.nav_figures <run folder>
"""
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image

BLUE, ORANGE, GREEN, RED = '#2a78d6', '#eb6834', '#16a34a', '#e11d48'


def save(fig, path):
    fig.savefig(path, dpi=180, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def columns(path, names):
    """Float columns of a csv (NaN for empty cells)."""
    out = {n: [] for n in names}
    if not path.is_file():
        return {n: np.empty(0) for n in names}
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            for n in names:
                v = row.get(n, '')
                out[n].append(float(v) if v not in ('', None) else math.nan)
    return {n: np.asarray(v, dtype=float) for n, v in out.items()}


def draw_map(ax, map_yaml):
    info = yaml.safe_load(map_yaml.read_text(encoding='utf-8'))
    image = (map_yaml.parent / info['image']).resolve()
    with Image.open(image) as im:
        grid = np.asarray(im.convert('L'))
    res = float(info['resolution'])
    ox, oy = float(info['origin'][0]), float(info['origin'][1])
    h, w = grid.shape
    ax.imshow(grid, cmap='gray', vmin=0, vmax=255, extent=[ox, ox + w * res, oy, oy + h * res],
              interpolation='nearest')


def generate(folder):
    d = Path(folder)
    run = json.loads((d / 'run.json').read_text(encoding='utf-8'))
    goals = run.get('goals') or []
    track = columns(d / 'track.csv', ['t', 'goal', 'gt_x', 'gt_y', 'est_x', 'est_y'])
    amcl = columns(d / 'amcl.csv', ['t', 'goal', 'error_xy'])

    fig, ax = plt.subplots(figsize=(7, 6))
    try:
        draw_map(ax, Path(run['map_yaml']))
    except (KeyError, OSError, ValueError, TypeError, yaml.YAMLError):
        pass                                   # path without the map underneath
    if np.isfinite(track['gt_x']).any():
        ax.plot(track['gt_x'], track['gt_y'], color=BLUE, lw=1.6, label='Ground truth')
    if np.isfinite(track['est_x']).any():
        ax.plot(track['est_x'], track['est_y'], color=ORANGE, lw=1.0, ls='--', label='Estimate (map→base_link)')
    for g in goals:
        ok = g.get('result') == 'succeeded'
        ax.plot(g['x'], g['y'], marker='o', ms=9, mfc='white', mec=GREEN if ok else RED, mew=2)
        ax.annotate(str(g['index']), (g['x'], g['y']), xytext=(6, 6), textcoords='offset points', fontsize=9,
                    color=GREEN if ok else RED, weight='bold')
    s = run.get('summary') or {}
    ax.set_title(f"{run.get('map', '')} · {s.get('succeeded', 0)}/{s.get('goals', len(goals))} goals reached",
                 fontsize=11)
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    ax.set_aspect('equal')
    ax.legend(loc='best', fontsize=8)
    save(fig, d / 'path.png')

    err = amcl['error_xy']
    ok = np.isfinite(err)
    fig, ax = plt.subplots(figsize=(8, 3.2))
    if ok.any():
        ax.plot(amcl['t'][ok], err[ok], color=BLUE, lw=1.2)
        # Goal boundaries: first sample of each goal
        for k in np.unique(amcl['goal'][np.isfinite(amcl['goal'])]):
            t0 = amcl['t'][amcl['goal'] == k].min()
            ax.axvline(t0, color='#94a3b8', lw=0.8, ls=':')
            ax.text(t0, ax.get_ylim()[1], f' {int(k)}', va='top', fontsize=8, color='#64748b')
        rmse = math.sqrt(float(np.mean(err[ok] ** 2)))
        ax.set_title(f'AMCL position error · mean {np.mean(err[ok]):.3f} m · RMSE {rmse:.3f} m · '
                     f'max {np.max(err[ok]):.3f} m', fontsize=10)
    else:
        ax.text(0.5, 0.5, 'No ground truth', ha='center', va='center', transform=ax.transAxes, color='#64748b')
    ax.set_xlabel(f"time ({run.get('clock', 'sim')}, s)")
    ax.set_ylabel('error (m)')
    ax.grid(alpha=0.3)
    save(fig, d / 'amcl_error.png')


if __name__ == '__main__':
    generate(sys.argv[1])
