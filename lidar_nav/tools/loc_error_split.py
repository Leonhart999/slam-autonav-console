#!/usr/bin/env python3
"""Split AMCL error into (a) a constant offset between the map and the world and (b) the rest.

The 2D map comes from FAST-LIO2; any rotation/shift of the map relative to the true world shows up
as AMCL "error" even if AMCL tracks the map perfectly. This script fits one 2D rigid transform
(rotation + translation, least squares) from the ground-truth path to the AMCL path, and reports
the error before and after removing it.

  ~/venvs/evo/bin/python loc_error_split.py ~/bags/nav/nav_wh_both ~/ros2_ws/src/lidar_nav/config/goals_warehouse.yaml
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_nav_run import load_goals, read_bag, world_to_map  # noqa: E402


def fit_rigid_2d(src, dst):
    """Least-squares rotation R and translation t with dst ≈ R @ src + t (Kabsch / Umeyama, no scale)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    h = (src - mu_s).T @ (dst - mu_d)
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[1] *= -1
        r = vt.T @ u.T
    return r, mu_d - r @ mu_s


def main():
    bag, goals = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None
    cfg = load_goals(goals)
    gt, amcl = read_bag(bag)
    gt_map = world_to_map(gt[:, 1:], cfg['start_x'], cfg['start_y'], cfg['start_yaw'])
    g = np.c_[np.interp(amcl[:, 0], gt[:, 0], gt_map[:, 0]), np.interp(amcl[:, 0], gt[:, 0], gt_map[:, 1])]
    a = amcl[:, 1:3]
    raw = np.linalg.norm(a - g, axis=1)
    r, t = fit_rigid_2d(g, a)
    aligned = np.linalg.norm(a - (g @ r.T + t), axis=1)
    yaw = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    print(f'{bag.rstrip("/").split("/")[-1]}: map-vs-world offset: rotation {yaw:+.2f} deg, '
          f'translation ({t[0]:+.3f}, {t[1]:+.3f}) m')
    print(f'   AMCL error before removing it: mean {raw.mean():.3f}  max {raw.max():.3f} m')
    print(f'   AMCL error after  removing it: mean {aligned.mean():.3f}  max {aligned.max():.3f} m')


if __name__ == '__main__':
    main()
