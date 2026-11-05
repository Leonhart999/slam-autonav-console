#!/usr/bin/env python3
"""Plot one navigation run: robot path over the 2D map and AMCL localization error over time.

Inputs
  bag        rosbag2 directory recorded by run_nav_test.sh (/ground_truth, /amcl_pose, /plan ...)
  map_yaml   the 2D occupancy map used for navigation
  goals      (optional) goals yaml with start_x/start_y/start_yaw and the goal list; without it the
             office defaults are used (start (-6.5, -3.5), yaw 0, office goal list)

Ground truth is in the Gazebo world frame; it is moved into the map frame (the FAST-LIO start pose)
by subtracting the start position and rotating by -start_yaw.

Outputs (prefix given by --out)
  <out>_path.png       map + ground-truth path + AMCL path + goals
  <out>_loc_error.png  AMCL position error vs time
  <out>_loc_error.csv  the error time series
and a short text summary on stdout.

Run with the evo venv python (it has rosbags, numpy and matplotlib):
  ~/venvs/evo/bin/python plot_nav_run.py ~/bags/nav/nav_wh_02 ~/maps/warehouse_fastlio.yaml \
      ~/ros2_ws/src/lidar_nav/config/goals_warehouse.yaml --out ~/results/nav_wh_02
"""
import argparse
import math
import os
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore

OFFICE_DEFAULT = {'start_x': -6.5, 'start_y': -3.5, 'start_yaw': 0.0,
                  'goals': [3.5, 0.0, 8.5, 3.5, 13.0, 7.0, 10.5, 2.5, 0.5, 6.5, 0.0, 0.0]}
GOAL_TOLERANCE = 0.25   # Nav2 xy_goal_tolerance used in nav2_params_sim.yaml


def load_goals(path):
    if not path:
        return OFFICE_DEFAULT
    doc = yaml.safe_load(open(path))
    params = next(iter(doc.values()))['ros__parameters']
    return {**OFFICE_DEFAULT, **params}


def read_bag(bag):
    gt, amcl = [], []
    with AnyReader([Path(bag)], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as r:
        conns = [c for c in r.connections if c.topic in ('/ground_truth', '/amcl_pose')]
        for conn, _, raw in r.messages(connections=conns):
            m = r.deserialize(raw, conn.msgtype)
            t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
            p = m.pose.pose.position
            (gt if conn.topic == '/ground_truth' else amcl).append((t, p.x, p.y))
    return np.array(gt), np.array(amcl)


def world_to_map(xy, sx, sy, syaw):
    c, s = math.cos(-syaw), math.sin(-syaw)
    dx, dy = xy[:, 0] - sx, xy[:, 1] - sy
    return np.c_[c * dx - s * dy, s * dx + c * dy]


def load_map(map_yaml):
    meta = yaml.safe_load(open(map_yaml))
    img = np.array(Image.open(os.path.join(os.path.dirname(map_yaml), meta['image'])))
    res, (ox, oy, _) = meta['resolution'], meta['origin']
    extent = [ox, ox + img.shape[1] * res, oy, oy + img.shape[0] * res]
    return img, extent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bag')
    ap.add_argument('map_yaml')
    ap.add_argument('goals', nargs='?')
    ap.add_argument('--out', required=True, help='output prefix, e.g. ~/results/nav_wh_02')
    args = ap.parse_args()
    out = os.path.expanduser(args.out)

    cfg = load_goals(args.goals)
    goals = np.array(cfg['goals'], dtype=float).reshape(-1, 2)
    gt, amcl = read_bag(args.bag)
    gt_map = world_to_map(gt[:, 1:], cfg['start_x'], cfg['start_y'], cfg['start_yaw'])

    # AMCL error: ground truth interpolated at each AMCL timestamp
    gx = np.interp(amcl[:, 0], gt[:, 0], gt_map[:, 0])
    gy = np.interp(amcl[:, 0], gt[:, 0], gt_map[:, 1])
    err = np.hypot(amcl[:, 1] - gx, amcl[:, 2] - gy)
    t0 = gt[0, 0]

    # --- path over the map ---
    img, extent = load_map(args.map_yaml)
    x0, x1 = min(gt_map[:, 0].min(), goals[:, 0].min()) - 3, max(gt_map[:, 0].max(), goals[:, 0].max()) + 3
    y0, y1 = min(gt_map[:, 1].min(), goals[:, 1].min()) - 3, max(gt_map[:, 1].max(), goals[:, 1].max()) + 3
    fig, ax = plt.subplots(figsize=(10, 10 * (y1 - y0) / (x1 - x0) + 0.8))
    ax.imshow(img, cmap='gray', extent=extent, vmin=0, vmax=255)
    ax.plot(gt_map[:, 0], gt_map[:, 1], color='#1f77b4', lw=1.6, label='ground truth path')
    ax.plot(amcl[:, 1], amcl[:, 2], color='#ff7f0e', lw=1.0, ls='--', label='AMCL estimate')
    ax.plot(0, 0, 'k^', ms=9, label='start')
    for i, (x, y) in enumerate(goals, 1):
        ax.add_patch(plt.Circle((x, y), GOAL_TOLERANCE, color='#d62728', fill=False, lw=1.2))
        ax.annotate(str(i), (x, y), xytext=(4, 4), textcoords='offset points', color='#d62728', fontsize=11,
                    weight='bold')
    ax.plot(goals[:, 0], goals[:, 1], 'r+', ms=8, label='goals (circle = 0.25 m tolerance)')
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_xlabel('x (m, map frame)')
    ax.set_ylabel('y (m, map frame)')
    ax.set_title(f'{Path(args.bag).name}: navigation path')
    ax.legend(loc='best', fontsize=9)
    fig.tight_layout()
    fig.savefig(out + '_path.png', dpi=150)
    plt.close(fig)

    # --- localization error over time ---
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(amcl[:, 0] - t0, err, color='#ff7f0e', lw=1.2)
    ax.axhline(GOAL_TOLERANCE, color='gray', ls=':', lw=1, label='goal tolerance 0.25 m')
    ax.set_xlabel('time (s, sim)')
    ax.set_ylabel('AMCL position error (m)')
    ax.set_title(f'{Path(args.bag).name}: AMCL error vs ground truth '
                 f'(mean {err.mean():.3f} m, max {err.max():.3f} m)')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out + '_loc_error.png', dpi=150)
    plt.close(fig)

    np.savetxt(out + '_loc_error.csv', np.c_[amcl[:, 0] - t0, err], delimiter=',',
               header='t_s,amcl_error_m', comments='', fmt='%.3f')
    print(f'AMCL updates: {len(err)}, error mean {err.mean():.3f} m, median {np.median(err):.3f} m, '
          f'max {err.max():.3f} m, share of time > {GOAL_TOLERANCE} m: {(err > GOAL_TOLERANCE).mean():.0%}')
    print(f'saved {out}_path.png, {out}_loc_error.png, {out}_loc_error.csv')


if __name__ == '__main__':
    main()
