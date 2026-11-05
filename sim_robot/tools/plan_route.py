#!/usr/bin/env python3
"""Plan a collision-free waypoint route through a building mesh for waypoint_driver.

Steps: slice the STL between 0.1 and 0.7 m into a 0.1 m occupancy grid, inflate by the robot radius,
then run A* through a list of rough goal points (each snapped to the nearest free cell). The cost
prefers cells far from walls, so the route stays near corridor centres. The path is simplified
(Douglas-Peucker) into waypoints and written as a waypoint_driver parameter file, plus a plot.

Usage:
  python3 plan_route.py building.stl route.yaml route.png  x1 y1  x2 y2 ...   (world coordinates)
"""
import heapq
import math
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import ndimage

RES = 0.1
ROBOT_R = 0.35         # robot circumscribed radius 0.32 m + margin
PREFER = 1.2           # distance (m) from walls beyond which no extra cost is added


def load_points(stl):
    raw = open(stl, 'rb').read()
    n = int.from_bytes(raw[80:84], 'little')
    tri = np.frombuffer(raw, dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')],
                        count=n, offset=84)['v'].astype(float)
    pts = [tri.reshape(-1, 3)]
    for a, b in [(0, 1), (1, 2), (0, 2)]:          # sample along edges every <= 5 cm
        L = np.linalg.norm(tri[:, b] - tri[:, a], axis=1)
        k = np.maximum(1, np.ceil(L / 0.05)).astype(int)
        for s in np.linspace(0, 1, int(k.max()) + 1)[1:-1]:
            m = k > 1
            pts.append(tri[m, a] + s * (tri[m, b] - tri[m, a]))
    # also sample triangle interiors (large flat wall faces)
    for u, v in [(0.33, 0.33), (0.6, 0.2), (0.2, 0.6), (0.1, 0.1)]:
        pts.append(tri[:, 0] + u * (tri[:, 1] - tri[:, 0]) + v * (tri[:, 2] - tri[:, 0]))
    p = np.concatenate(pts)
    return p[(p[:, 2] > 0.1) & (p[:, 2] < 0.7)][:, :2]


def astar(cost, start, goal):
    h, w = cost.shape
    openq = [(0.0, start)]
    g = {start: 0.0}
    came = {}
    nbrs = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414)]
    while openq:
        _, cur = heapq.heappop(openq)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1]
        for dy, dx, step in nbrs:
            nb = (cur[0] + dy, cur[1] + dx)
            if not (0 <= nb[0] < h and 0 <= nb[1] < w) or not np.isfinite(cost[nb]):
                continue
            ng = g[cur] + step * cost[nb]
            if ng < g.get(nb, math.inf):
                g[nb] = ng
                came[nb] = cur
                heapq.heappush(openq, (ng + math.hypot(nb[0] - goal[0], nb[1] - goal[1]), nb))
    return None


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    d = np.abs(np.cross(ab, pts - a)) / (np.linalg.norm(ab) + 1e-9)
    k = int(d.argmax())
    if d[k] > tol:
        return np.vstack([simplify(pts[:k + 1], tol)[:-1], simplify(pts[k:], tol)])
    return np.vstack([a, b])


def main():
    stl, out_yaml, out_png = sys.argv[1:4]
    goals = np.array(sys.argv[4:], dtype=float).reshape(-1, 2)
    pts = load_points(stl)
    lo = pts.min(0) - 2
    hi = pts.max(0) + 2
    shape = (int((hi[1] - lo[1]) / RES) + 1, int((hi[0] - lo[0]) / RES) + 1)
    occ = np.zeros(shape, bool)
    ij = ((pts - lo) / RES).astype(int)
    occ[ij[:, 1], ij[:, 0]] = True
    dist = ndimage.distance_transform_edt(~occ) * RES
    cost = np.where(dist > ROBOT_R, 1.0 + 4.0 * np.clip(PREFER - dist, 0, None), np.inf)
    # stay inside the building region: no cells within 1 m of the cropped geometry's bounding box edge
    # (outside the crop there are no walls, so the planner would otherwise take shortcuts around it)
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]]
    wx, wy = lo[0] + (xs + 0.5) * RES, lo[1] + (ys + 0.5) * RES
    pmin, pmax = pts.min(0) + 1.0, pts.max(0) - 1.0
    cost[(wx < pmin[0]) | (wx > pmax[0]) | (wy < pmin[1]) | (wy > pmax[1])] = np.inf

    def cell(p):
        c = (int((p[1] - lo[1]) / RES), int((p[0] - lo[0]) / RES))
        if np.isfinite(cost[c]):
            return c
        free = np.argwhere(np.isfinite(cost))
        return tuple(free[np.argmin(((free - c) ** 2).sum(1))])

    to_xy = lambda cs: np.array([[lo[0] + (c[1] + 0.5) * RES, lo[1] + (c[0] + 0.5) * RES] for c in cs])
    path, wps = [], []
    for a, b in zip(goals[:-1], goals[1:]):
        seg = astar(cost, cell(a), cell(b))
        if seg is None:
            sys.exit(f'no path from {a} to {b}')
        path += seg if not path else seg[1:]
        s = simplify(to_xy(seg), 0.15)            # simplify per leg (a closed loop would collapse)
        wps.append(s if not wps else s[1:])
    xy = to_xy(path)
    wp = np.vstack(wps)
    length = np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()
    clear = dist[[c[0] for c in path], [c[1] for c in path]]
    print(f'route: {len(wp)} waypoints, length {length:.1f} m, min clearance {clear.min():.2f} m')

    flat = ', '.join(f'{x:.2f}, {y:.2f}' for x, y in wp[1:])
    with open(out_yaml, 'w') as f:
        f.write(f'# generated by tools/plan_route.py from {stl.split("/")[-3]}\n'
                f'# A* on a 0.1 m grid sliced at 0.1-0.7 m, robot radius {ROBOT_R} m; length {length:.1f} m\n'
                f'waypoint_driver:\n  ros__parameters:\n    waypoints: [{flat}]\n'
                f'    max_speed: 0.5\n    tolerance: 0.2\n    timeout: 1800.0\n')
    fig, ax = plt.subplots(figsize=(10, 10 * shape[0] / shape[1]))
    ax.imshow(np.where(occ, 0, np.where(np.isfinite(cost), 255, 200)), cmap='gray', origin='lower',
              extent=[lo[0], lo[0] + shape[1] * RES, lo[1], lo[1] + shape[0] * RES], vmin=0, vmax=255)
    ax.plot(xy[:, 0], xy[:, 1], 'r-', lw=1.2, label=f'route {length:.0f} m')
    ax.plot(wp[:, 0], wp[:, 1], 'bo', ms=3, label='waypoints')
    ax.plot(goals[0, 0], goals[0, 1], 'g^', ms=10, label='start')
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print('saved', out_yaml, out_png)


if __name__ == '__main__':
    main()
