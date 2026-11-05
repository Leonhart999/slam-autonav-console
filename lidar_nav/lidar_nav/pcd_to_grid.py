#!/usr/bin/env python3
"""把 FAST-LIO2 保存的 3D 点云地图（PCD）投影成 Nav2 能用的 2D 栅格地图（pgm + yaml）。

做法（"高度带"投影）：
  1. 估计地面：先用 z 直方图的峰值找到大致地面高度，再迭代拟合一个地面平面
     （SLAM 地图往往有轻微倾斜，单一高度在远处会出错）。
  2. 障碍物：离地高度在 [obs_min, obs_max] 之间的点所在的格子标成"占据"。
     下限过滤掉地面本身，上限过滤掉高于机器人的东西（天花板、吊灯等）。
  3. 可通行：有地面点、又没有被标成占据的格子标成"空闲"。
  4. 其余格子是"未知"。
  5. 对空闲区域做一次形态学闭运算，补上地面点稀疏造成的小空洞。

坐标系：输出地图和 PCD 用同一个坐标系（FAST-LIO2 的 camera_init，原点是起步时 IMU 的位置），
所以 Nav2 里的 map 坐标系就是 FAST-LIO2 建图时的坐标系。

用法：
  python3 pcd_to_grid.py 地图.pcd 输出前缀 [--res 0.05] [--obs-min 0.1] [--obs-max 0.7]
输出：输出前缀.pgm、输出前缀.yaml、输出前缀_preview.png（方便在 Windows 里直接看）
"""
import argparse
import os
import numpy as np
from scipy import ndimage
from PIL import Image

OCC, FREE, UNKNOWN = 0, 254, 205      # pgm 里的灰度值：黑 = 占据，白 = 空闲，灰 = 未知


def read_pcd_xyz(path):
    """读取二进制 PCD 的 x y z（只支持 DATA binary，FAST-LIO2 存的就是这种）"""
    with open(path, 'rb') as f:
        header = {}
        while True:
            line = f.readline().decode('ascii').strip()
            key, *vals = line.split()
            header[key] = vals
            if key == 'DATA':
                break
        assert header['DATA'][0] == 'binary', '只支持 binary 格式的 PCD'
        fields, sizes, types = header['FIELDS'], list(map(int, header['SIZE'])), header['TYPE']
        np_type = {('F', 4): '<f4', ('F', 8): '<f8', ('U', 4): '<u4', ('I', 4): '<i4',
                   ('U', 2): '<u2', ('I', 2): '<i2', ('U', 1): 'u1', ('I', 1): 'i1'}
        dtype = np.dtype([(n, np_type[(t, s)]) for n, t, s in zip(fields, types, sizes)])
        n = int(header['POINTS'][0])
        data = np.frombuffer(f.read(n * dtype.itemsize), dtype=dtype, count=n)
    xyz = np.stack([data['x'], data['y'], data['z']], axis=1).astype(np.float64)
    return xyz[np.isfinite(xyz).all(axis=1)]


def estimate_ground(z, bin_size=0.02):
    """z 直方图里点数最多的那一格，作为地面高度"""
    lo, hi = np.percentile(z, 1), np.percentile(z, 99)
    hist, edges = np.histogram(z, bins=np.arange(lo, hi + bin_size, bin_size))
    k = np.argmax(hist)
    return 0.5 * (edges[k] + edges[k + 1])


def fit_ground_plane(xyz, z0, tols=(0.2, 0.08, 0.05)):
    """以直方图峰值 z0 为起点，迭代地用最小二乘拟合地面平面 z = a·x + b·y + c。
    SLAM 地图常常有零点几度的倾斜，十几米外地面高度就差出 5–10 cm，只用一个高度
    会把远处的地面当成障碍物或漏掉。每次迭代只保留离当前平面足够近的点，逐步收紧。"""
    a = b = 0.0
    c = z0
    for tol in tols:
        resid = xyz[:, 2] - (a * xyz[:, 0] + b * xyz[:, 1] + c)
        m = np.abs(resid) < tol
        A = np.c_[xyz[m, 0], xyz[m, 1], np.ones(m.sum())]
        a, b, c = np.linalg.lstsq(A, xyz[m, 2], rcond=None)[0]
    return a, b, c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcd')
    ap.add_argument('out_prefix')
    ap.add_argument('--res', type=float, default=0.05, help='栅格分辨率，米/格')
    ap.add_argument('--obs-min', type=float, default=0.1, help='障碍物离地高度下限，米')
    ap.add_argument('--obs-max', type=float, default=0.7, help='障碍物离地高度上限，米（略高于机器人）')
    ap.add_argument('--ground-tol', type=float, default=0.05, help='离地面多近的点算地面点，米')
    ap.add_argument('--min-points', type=int, default=2, help='一个格子至少有几个障碍点才算占据')
    args = ap.parse_args()

    xyz = read_pcd_xyz(args.pcd)
    g0 = estimate_ground(xyz[:, 2])
    a, b, c = fit_ground_plane(xyz, g0)
    h = xyz[:, 2] - (a * xyz[:, 0] + b * xyz[:, 1] + c)      # 离地高度（相对拟合的地面平面）
    tilt = np.degrees(np.arctan(np.hypot(a, b)))
    print(f'读入 {len(xyz)} 个点；地面峰值 z = {g0:.3f} m；拟合地面平面倾角 {tilt:.2f}°，'
          f'原点处地面高度 {c:.3f} m')

    # 栅格范围：所有点的 xy 外扩 0.5 m
    xmin, ymin = xyz[:, :2].min(axis=0) - 0.5
    xmax, ymax = xyz[:, :2].max(axis=0) + 0.5
    w = int(np.ceil((xmax - xmin) / args.res))
    hgt = int(np.ceil((ymax - ymin) / args.res))
    ix = ((xyz[:, 0] - xmin) / args.res).astype(int)
    iy = ((xyz[:, 1] - ymin) / args.res).astype(int)

    obs = (h >= args.obs_min) & (h <= args.obs_max)
    gnd = np.abs(h) <= args.ground_tol
    obs_count = np.zeros((hgt, w), dtype=np.int32)
    np.add.at(obs_count, (iy[obs], ix[obs]), 1)
    gnd_count = np.zeros((hgt, w), dtype=np.int32)
    np.add.at(gnd_count, (iy[gnd], ix[gnd]), 1)

    occupied = obs_count >= args.min_points
    free = (gnd_count > 0) & ~occupied
    # 闭运算：先膨胀再腐蚀，填掉空闲区域里一两格的小洞；不覆盖已经是障碍的格子
    free = ndimage.binary_closing(free, structure=np.ones((3, 3)), iterations=2) & ~occupied

    grid = np.full((hgt, w), UNKNOWN, dtype=np.uint8)
    grid[free] = FREE
    grid[occupied] = OCC
    img = np.flipud(grid)                               # 图片第一行是 y 最大的一边

    out_dir = os.path.dirname(os.path.abspath(args.out_prefix))
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(args.out_prefix)
    Image.fromarray(img).save(args.out_prefix + '.pgm')
    Image.fromarray(img).save(args.out_prefix + '_preview.png')
    with open(args.out_prefix + '.yaml', 'w') as f:
        f.write(f'image: {name}.pgm\n'
                f'mode: trinary\n'
                f'resolution: {args.res}\n'
                f'origin: [{xmin:.3f}, {ymin:.3f}, 0.0]\n'
                f'negate: 0\n'
                f'occupied_thresh: 0.65\n'
                f'free_thresh: 0.25\n')
    total = w * hgt
    print(f'栅格 {w} × {hgt}（{w * args.res:.1f} m × {hgt * args.res:.1f} m），'
          f'占据 {occupied.sum() / total:.1%}，空闲 {free.sum() / total:.1%}')
    print(f'左下角原点 ({xmin:.2f}, {ymin:.2f})；已保存 {args.out_prefix}.pgm / .yaml / _preview.png')


if __name__ == '__main__':
    main()
