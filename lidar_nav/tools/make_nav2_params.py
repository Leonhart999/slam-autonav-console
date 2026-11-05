#!/usr/bin/env python3
"""由 Nav2 官方默认参数（nav2_bringup/params/nav2_params.yaml，为 TurtleBot3 写的）生成本项目仿真小车用的参数。

只做少量文字替换，保留官方文件里的全部注释和其余默认值，方便对照。每一处改动都在下面的 EDITS 里说明。
用法：python3 make_nav2_params.py   →  生成 ../config/nav2_params_sim.yaml
"""
import os

SRC = '/opt/ros/humble/share/nav2_bringup/params/nav2_params.yaml'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'nav2_params_sim.yaml')

# (原文, 新文, 次数, 说明)
EDITS = [
    ('base_frame_id: "base_footprint"', 'base_frame_id: "base_link"', 1,
     'AMCL：我们的小车没有 base_footprint，车体坐标系是 base_link'),
    ('    scan_topic: scan\n',
     '    scan_topic: scan\n'
     '    # 小车每次都从建图时的起点出发，起点就是 map 坐标系原点（FAST-LIO2 以起步位置为原点）\n'
     '    set_initial_pose: true\n'
     '    initial_pose: {x: 0.0, y: 0.0, z: 0.0, yaw: 0.0}\n', 1,
     'AMCL：自动设置初始位姿，不用每次在 rviz2 里手点'),
    ('laser_max_range: 100.0', 'laser_max_range: 30.0', 1,
     'AMCL：室内场景 30 m 足够，和 pointcloud_to_laserscan 的 range_max 一致'),
    ('max_vel_x: 0.26', 'max_vel_x: 0.5', 1, 'DWB：最高线速度 0.5 m/s（和建图采数据时一样）'),
    ('max_speed_xy: 0.26', 'max_speed_xy: 0.5', 1, 'DWB：同上'),
    ('robot_radius: 0.22', 'robot_radius: 0.33', 2,
     '代价地图：车体 0.5 m × 0.4 m，外接圆半径约 0.32 m，取 0.33'),
    ('max_velocity: [0.26, 0.0, 1.0]', 'max_velocity: [0.5, 0.0, 1.0]', 1, '速度平滑器：和 DWB 一致'),
    ('min_velocity: [-0.26, 0.0, -1.0]', 'min_velocity: [-0.5, 0.0, -1.0]', 1, '速度平滑器：同上'),
]

# Parameter variants for the AMCL experiments. Each variant = base EDITS + its own edits,
# written to config/nav2_params_<variant>.yaml. Change one group of parameters per variant
# so the effect of each group can be measured on its own.
VARIANTS = {
    # Sensor model: use more beams and trust the laser more. Defaults (60 beams, z_hit = z_rand = 0.5)
    # make the likelihood flat in a large hall with repeated shelves.
    'amcl_sensor': [
        ('max_beams: 60', 'max_beams: 180', 1, 'AMCL sensor model: 180 beams instead of 60'),
        ('z_hit: 0.5', 'z_hit: 0.9', 1, 'AMCL sensor model: z_hit 0.9'),
        ('z_rand: 0.5', 'z_rand: 0.1', 1, 'AMCL sensor model: z_rand 0.1 (z_hit + z_rand = 1)'),
        ('sigma_hit: 0.2', 'sigma_hit: 0.1', 1, 'AMCL sensor model: sigma_hit 0.1 m (sim range noise is 1 cm)'),
    ],
    # Motion model and update rate: simulated wheel odometry has almost no slip, so the default
    # alphas (0.2) spread the particles far more than needed; also update more often.
    'amcl_motion': [
        ('alpha1: 0.2', 'alpha1: 0.05', 1, 'AMCL motion model: alpha1 0.05'),
        ('alpha2: 0.2', 'alpha2: 0.05', 1, 'AMCL motion model: alpha2 0.05'),
        ('alpha3: 0.2', 'alpha3: 0.05', 1, 'AMCL motion model: alpha3 0.05'),
        ('alpha4: 0.2', 'alpha4: 0.05', 1, 'AMCL motion model: alpha4 0.05'),
        ('update_min_d: 0.25', 'update_min_d: 0.1', 1, 'AMCL: update every 0.1 m instead of 0.25 m'),
        ('update_min_a: 0.2', 'update_min_a: 0.1', 1, 'AMCL: update every 0.1 rad instead of 0.2 rad'),
    ],
}
VARIANTS['amcl_both'] = VARIANTS['amcl_sensor'] + VARIANTS['amcl_motion']


def build(edits, out):
    text = open(SRC).read()
    for old, new, count, why in edits:
        n = text.count(old)
        assert n == count, f'"{old}" 应该出现 {count} 次，实际 {n} 次（官方文件版本变了？）'
        text = text.replace(old, new)
    header = ('# 本文件由 lidar_nav/tools/make_nav2_params.py 生成，不要手改。\n'
              f'# 来源：{SRC}\n# 改动：\n' + ''.join(f'#   - {why}\n' for *_, why in edits) + '\n')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        f.write(header + text)
    print('已生成', os.path.normpath(out))


def main():
    build(EDITS, OUT)
    for name, extra in VARIANTS.items():
        build(EDITS + extra, OUT.replace('nav2_params_sim.yaml', f'nav2_params_{name}.yaml'))


if __name__ == '__main__':
    main()
