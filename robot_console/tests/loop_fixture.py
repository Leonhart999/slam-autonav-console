"""Synthetic raw-scan recording for the loop back-end: a robot drives a circle 2.5 times in a room with pillars.

The odometry drifts in heading and position, so the back-end must find the revisits and pull the trajectory back.
Format as fastlio_loop_record.py writes it: scans.bin (float32 x y z intensity, body frame) + index.csv
(stamp offset count x y z qx qy qz qw, odometry pose of the scan). Also returns the true poses.
"""
import os

import numpy as np
from scipy.spatial.transform import Rotation as R

SENSOR_HEIGHT = 0.5


def room_points(rng):
    """World points of a 16 m x 12 m room (walls, floor, pillars, boxes); the floor is at z = -SENSOR_HEIGHT."""
    pts = []
    step = 0.08
    zs = np.arange(-SENSOR_HEIGHT, 2.0, step)
    for x0, x1, y0, y1 in ((-8, 8, -6, -6), (-8, 8, 6, 6), (-8, -8, -6, 6), (8, 8, -6, 6)):
        n = int(max(abs(x1 - x0), abs(y1 - y0)) / step)
        t = np.linspace(0, 1, n)
        xy = np.column_stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t])
        pts.append(np.column_stack([np.repeat(xy, len(zs), axis=0), np.tile(zs, len(xy))]))
    gx, gy = np.meshgrid(np.arange(-8, 8, 0.15), np.arange(-6, 6, 0.15))
    pts.append(np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, -SENSOR_HEIGHT)]))
    # pillars and boxes break the symmetry of the room, so registration has a unique answer
    for cx, cy, r in ((-5.5, 3.5, 0.3), (4.0, -4.0, 0.4), (6.0, 4.5, 0.25), (-2.0, -4.5, 0.35)):
        a = np.linspace(0, 2 * np.pi, 40, endpoint=False)
        ring = np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])
        pts.append(np.column_stack([np.repeat(ring, len(zs), axis=0), np.tile(zs, len(ring))]))
    for x0, y0, w, d, h in ((-7.5, -1.0, 1.0, 2.0, 0.8), (2.0, 5.0, 2.5, 0.8, 1.2)):
        bx, bz = np.meshgrid(np.arange(x0, x0 + w, step), np.arange(-SENSOR_HEIGHT, -SENSOR_HEIGHT + h, step))
        by, bz2 = np.meshgrid(np.arange(y0, y0 + d, step), np.arange(-SENSOR_HEIGHT, -SENSOR_HEIGHT + h, step))
        pts += [np.column_stack([bx.ravel(), np.full(bx.size, y0), bz.ravel()]),
                np.column_stack([bx.ravel(), np.full(bx.size, y0 + d), bz.ravel()]),
                np.column_stack([np.full(by.size, x0), by.ravel(), bz2.ravel()]),
                np.column_stack([np.full(by.size, x0 + w), by.ravel(), bz2.ravel()])]
    return np.vstack(pts)


def make_recording(path, laps=2.5, radius=4.0, step=0.2, yaw_drift_deg=6.0, xy_drift=0.6, seed=0):
    """Write the recording to `path`; returns (true poses (N, 4, 4), odometry poses (N, 4, 4), stamps)."""
    rng = np.random.default_rng(seed)
    world = room_points(rng)
    n = int(laps * 2 * np.pi * radius / step)
    theta = np.arange(n) * step / radius
    true = np.tile(np.eye(4), (n, 1, 1))
    true[:, 0, 3], true[:, 1, 3] = radius * np.cos(theta), radius * np.sin(theta)
    true[:, :3, :3] = R.from_euler('z', theta + np.pi / 2).as_matrix()
    # odometry: heading error grows linearly, plus a slow position bias; height and tilt stay exact
    frac = np.arange(n) / (n - 1)
    odom = true.copy()
    bias = R.from_euler('z', np.radians(yaw_drift_deg) * frac).as_matrix()
    odom[:, :3, :3] = bias @ true[:, :3, :3]
    odom[:, :2, 3] = np.einsum('nij,nj->ni', bias[:, :2, :2], true[:, :2, 3]) + np.outer(frac, [xy_drift, -xy_drift / 2])
    stamps = 100.0 + np.arange(n) * 0.1
    os.makedirs(path)
    offset = 0
    with open(os.path.join(path, 'scans.bin'), 'wb') as scans, open(os.path.join(path, 'index.csv'), 'w') as index:
        for k in range(n):
            local = (world - true[k, :3, 3]) @ true[k, :3, :3]
            keep = np.einsum('ij,ij->i', local, local) < 20.0 ** 2
            local = local[keep][rng.random(keep.sum()) < 0.35]
            local += rng.normal(0, 0.01, local.shape)
            block = np.column_stack([local, np.zeros(len(local))]).astype('<f4')
            scans.write(block.tobytes())
            q = R.from_matrix(odom[k, :3, :3]).as_quat()
            x, y, z = odom[k, :3, 3]
            index.write(f'{stamps[k]:.6f} {offset} {len(block)} {x:.6f} {y:.6f} {z:.6f} '
                        f'{q[0]:.9f} {q[1]:.9f} {q[2]:.9f} {q[3]:.9f}\n')
            offset += len(block)
    return true, odom, stamps


def write_scan_poses(recording, path):
    """data/scan_poses.tum as the console writes it for a map saved from a playback (one row per scan)."""
    idx = np.loadtxt(os.path.join(recording, 'index.csv'), ndmin=2)
    np.savetxt(path, idx[:, [0, 3, 4, 5, 6, 7, 8, 9]], fmt='%.6f')
