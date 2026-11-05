"""为已保存的地图生成可复查的静态成果图；独立进程运行，避免阻塞 ROS。"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image
from lidar_nav.pcd_to_grid import read_pcd_xyz

BLUE, ORANGE = '#2a78d6', '#eb6834'


def save(fig, path):
    fig.savefig(path, dpi=180, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def trajectory(path):
    if not path.exists() or not path.stat().st_size:
        return np.empty((0, 8))
    data = np.loadtxt(path, ndmin=2)
    return data[np.isfinite(data).all(axis=1)]


def generate(directory):
    d = Path(directory)
    root = d / 'figures'
    for sub in ('2d', '3d', 'comparison', 'experiments'):
        (root / sub).mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.labelcolor': '#30343b', 'text.color': '#30343b',
                         'axes.edgecolor': '#d1d5db', 'grid.color': '#e5e7eb'})
    xyz = read_pcd_xyz(d / 'map_voxel.pcd')
    # 只对图片做确定性抽样，原始 PCD 不变。
    pts = xyz[::max(1, int(np.ceil(len(xyz) / 60000)))]
    if not len(pts):
        raise ValueError('Empty voxel cloud')
    vmin, vmax = np.percentile(pts[:, 2], [2, 98])
    vmax = max(vmax, vmin + 0.01)

    def cloud(ax):
        s = ax.scatter(*pts.T, c=pts[:, 2], cmap='Blues', vmin=vmin, vmax=vmax,
                       s=0.6, linewidths=0, rasterized=True)
        ax.set(xlabel='X (m)', ylabel='Y (m)', zlabel='Z (m)')
        ax.view_init(elev=48, azim=-60)
        ax.set_box_aspect(np.maximum(np.ptp(pts, axis=0), 0.5))
        return s

    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection='3d')
    s = cloud(ax)
    ax.set_title('3D point cloud | voxel preview, height-colored')
    fig.colorbar(s, ax=ax, shrink=0.55, pad=0.1, label='Z (m)')
    save(fig, root / '3d' / 'pointcloud.png')

    grid_path = d / 'map.yaml'
    extent = image = None
    if grid_path.exists():
        info = yaml.safe_load(grid_path.read_text())
        image = np.array(Image.open(d / info['image']))
        x, y, _ = info['origin']
        res = info['resolution']
        extent = [x, x + image.shape[1] * res, y, y + image.shape[0] * res]
        Image.fromarray(image).save(root / '2d' / 'occupancy_grid.png')
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.imshow(image, cmap='gray', vmin=0, vmax=254, extent=extent, origin='upper')
        ax.set(xlabel='X (m)', ylabel='Y (m)', title='2D occupancy grid | black: occupied, white: free, gray: unknown')
        save(fig, root / '2d' / 'occupancy_grid_axes.png')

        fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharex=True, sharey=True)
        s = axes[0].scatter(pts[:, 0], pts[:, 1], c=pts[:, 2], cmap='Blues',
                            vmin=vmin, vmax=vmax, s=0.5, linewidths=0)
        axes[0].set_title('3D cloud: XY top view')
        axes[1].imshow(image, cmap='gray', vmin=0, vmax=254, extent=extent, origin='upper')
        axes[1].set_title('Projected 2D occupancy grid')
        for ax in axes:
            ax.set(xlabel='X (m)', ylabel='Y (m)', aspect='equal',
                   xlim=extent[:2], ylim=extent[2:])
        fig.colorbar(s, ax=axes[0], shrink=0.6, label='Z (m)')
        fig.suptitle('3D to 2D | same map frame, bounds and metric scale')
        save(fig, root / 'comparison' / 'cloud_to_grid.png')

    exp = d / 'data'
    exp.mkdir(exist_ok=True)
    est, gt = trajectory(exp / 'est.tum'), trajectory(exp / 'gt.tum')
    metrics = {'estimate_samples': len(est), 'ground_truth_samples': len(gt),
               'alignment': 'SE(3) rigid least-squares, no scale; not an evo report',
               'association_tolerance_s': 0.05}
    if len(est):
        time = est[:, 0] - est[0, 0]
        np.savetxt(exp / 'trajectory.csv', np.c_[time, est[:, 1:4]], delimiter=',',
                   header='elapsed_s,x_m,y_m,z_m', comments='')
        fig, ax = plt.subplots(figsize=(9, 7))
        if image is not None:
            ax.imshow(image, cmap='gray', vmin=0, vmax=254, extent=extent, origin='upper', alpha=0.65)
        ax.plot(est[:, 1], est[:, 2], color=BLUE, lw=2, label='Estimated trajectory')
        ax.plot(est[0, 1], est[0, 2], 'o', color=BLUE, ms=8)
        ax.set(xlabel='X (m)', ylabel='Y (m)', title='Mapping trajectory (SLAM frame)', aspect='equal')
        ax.legend()
        save(fig, root / 'experiments' / 'trajectory.png')

        dt = np.diff(est[:, 0])
        valid = dt > 1e-6
        speed = np.linalg.norm(np.diff(est[:, 1:4], axis=0), axis=1)[valid] / dt[valid]
        np.savetxt(exp / 'speed.csv', np.c_[time[1:][valid], speed], delimiter=',',
                   header='elapsed_s,speed_m_s', comments='')
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(time[1:][valid], speed, color=BLUE, lw=2)
        ax.set(xlabel='Elapsed time (s)', ylabel='Speed (m/s)', title='Estimated translational speed')
        ax.grid(alpha=0.5)
        save(fig, root / 'experiments' / 'speed.png')

    # 一对一、最近时间关联；真值保持原始文件不变，只对用于比较的坐标做刚体对齐。
    if len(est) >= 3 and len(gt) >= 3:
        gt = gt[np.argsort(gt[:, 0], kind='stable')]
        indices = np.searchsorted(gt[:, 0], est[:, 0])
        right = np.clip(indices, 0, len(gt) - 1)
        left = np.clip(indices - 1, 0, len(gt) - 1)
        indices = np.where(abs(gt[left, 0] - est[:, 0]) <= abs(gt[right, 0] - est[:, 0]), left, right)
        used, pairs = set(), []
        for i in np.argsort(abs(gt[indices, 0] - est[:, 0]), kind='stable'):
            j = int(indices[i])
            if j not in used and abs(gt[j, 0] - est[i, 0]) <= 0.05:
                used.add(j)
                pairs.append((i, j))
        pairs.sort()
        metrics['associated_samples'] = len(pairs)
        if len(pairs) >= 3:
            a, b = np.array(pairs).T
            source, target = gt[b, 1:4], est[a, 1:4]
            u, _, vt = np.linalg.svd((source - source.mean(0)).T @ (target - target.mean(0)))
            correction = np.eye(3)
            correction[-1, -1] = np.linalg.det(u @ vt)
            rotation = u @ correction @ vt
            aligned = (source - source.mean(0)) @ rotation + target.mean(0)
            error = np.linalg.norm(target - aligned, axis=1)
            metrics.update(position_rmse_m=float(np.sqrt(np.mean(error**2))),
                           position_mean_m=float(error.mean()), position_max_m=float(error.max()))
            np.savetxt(exp / 'aligned_error.csv', np.c_[est[a, 0], target, aligned, error],
                       delimiter=',', header='stamp_s,est_x,est_y,est_z,gt_x_aligned,gt_y_aligned,gt_z_aligned,error_m', comments='')
            fig, ax = plt.subplots(figsize=(9, 7))
            ax.plot(*target[:, :2].T, color=BLUE, lw=2, label='Estimate')
            ax.plot(*aligned[:, :2].T, color=ORANGE, lw=2, ls='--', label='Ground truth (rigid aligned)')
            ax.set(xlabel='X (m)', ylabel='Y (m)', aspect='equal', title='Estimated and aligned ground-truth trajectories')
            ax.legend()
            save(fig, root / 'experiments' / 'trajectory_comparison.png')
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(est[a, 0] - est[a[0], 0], error, color=BLUE, lw=2)
            ax.set(xlabel='Elapsed time (s)', ylabel='Position error (m)',
                   title=f'Rigid-aligned position error | RMSE {metrics["position_rmse_m"]:.3f} m (no scale)')
            ax.grid(alpha=0.5)
            save(fig, root / 'experiments' / 'position_error.png')
    (exp / 'metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    files = [str(p.relative_to(d)) for p in sorted(root.rglob('*.png'))]
    (exp / 'figures.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    generate(parser.parse_args().directory)
