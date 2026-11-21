#!/usr/bin/env python3
"""由 Nav2 官方默认参数（nav2_bringup/params/nav2_params.yaml，为 TurtleBot3 写的）生成本项目仿真小车用的参数。

只做少量文字替换，保留官方文件里的全部注释和其余默认值，方便对照。每一处改动都在下面的 EDITS 里说明。
用法：python3 make_nav2_params.py   →  生成 ../config/nav2_params_*.yaml

关于未知区域（allow_unknown 与 free_thresh）：
  - pcd_to_grid.py 已修复为 free_thresh: 0.15，使灰度 205（p=0.196 > 0.15）被 map_server 正确识别为未知区；
  - global_costmap 开启 track_unknown_space: true，在代价地图中区分空闲（0）与未知（255）；
  - 各全局规划器保持 allow_unknown: true，确保优先走已知空闲区的同时，对稀疏点云或出生点盲区引起的零星未知格保持容错。
"""
import os
import re

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
    ('transform_tolerance: 0.2', 'transform_tolerance: 1.0', 1,
     '控制器 TF 容差：WSL2 仿真下 AMCL/雷达偶发 0.2–0.4 s 抖动，与 AMCL 1.0 s 对齐避免误判'),
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

# ---------------- Planner & Controller comparison variants (built on amcl_both) ----------------

MPPI_BLOCK = """    # MPPI controller parameters (Humble nav2_mppi_controller)
    FollowPath:
      plugin: "nav2_mppi_controller::MPPIController"
      time_steps: 48
      model_dt: 0.05
      batch_size: 1200
      vx_std: 0.2
      vy_std: 0.0
      wz_std: 0.4
      vx_max: 0.5
      vx_min: -0.25
      vy_max: 0.0
      wz_max: 1.0
      iteration_count: 1
      prune_distance: 1.7
      transform_tolerance: 1.0
      temperature: 0.3
      gamma: 0.015
      motion_model: "DiffDrive"
      visualize: false
      critics: ["ConstraintCritic", "CostCritic", "GoalCritic", "GoalAngleCritic", "PathAlignCritic", "PathFollowCritic", "PathAngleCritic", "PreferForwardCritic"]
      ConstraintCritic:
        enabled: true
        cost_power: 1
        cost_weight: 4.0
      GoalCritic:
        enabled: true
        cost_power: 1
        cost_weight: 5.0
        threshold_to_consider: 1.4
      GoalAngleCritic:
        enabled: true
        cost_power: 1
        cost_weight: 3.0
        threshold_to_consider: 0.5
      PreferForwardCritic:
        enabled: true
        cost_power: 1
        cost_weight: 5.0
        threshold_to_consider: 0.5
      CostCritic:
        enabled: true
        cost_power: 1
        cost_weight: 3.81
        critical_cost: 300.0
        consider_footprint: false
        collision_cost: 1000000.0
        near_goal_distance: 1.0
      PathAlignCritic:
        enabled: true
        cost_power: 1
        cost_weight: 14.0
        max_path_occupancy_ratio: 0.05
        trajectory_point_step: 4
        threshold_to_consider: 0.5
        offset_from_furthest: 20
        use_path_orientations: false
      PathFollowCritic:
        enabled: true
        cost_power: 1
        cost_weight: 5.0
        offset_from_furthest: 5
        threshold_to_consider: 1.4
      PathAngleCritic:
        enabled: true
        cost_power: 1
        cost_weight: 2.0
        offset_from_furthest: 4
        threshold_to_consider: 0.5
        max_angle_to_furthest: 1.0
        forward_preference: true
"""

SMAC_BLOCK = """    GridBased:
      plugin: "nav2_smac_planner/SmacPlanner2D"
      tolerance: 0.5
      downsample_costmap: false
      downsampling_factor: 1
      allow_unknown: true
      max_iterations: 1000000
      max_on_approach_iterations: 1000
      max_planning_time: 2.0
      cost_travel_multiplier: 2.0
      use_final_approach_orientation: false
      smoother:
        max_iterations: 1000
        w_smooth: 0.3
        w_data: 0.2
        tolerance: 1.0e-10
"""

THETA_BLOCK = """    GridBased:
      plugin: "nav2_theta_star_planner/ThetaStarPlanner"
      how_many_corners: 8
      w_euc_cost: 1.0
      w_traversal_cost: 2.0
      allow_unknown: true
"""

PLANNERS = {
    'navfn': (None, 'Global planner: NavFn (Dijkstra)'),
    'smac': (SMAC_BLOCK, 'Global planner: SmacPlanner2D (cost-aware 2D A* + smoother)'),
    'theta': (THETA_BLOCK, 'Global planner: ThetaStarPlanner (any-angle)'),
}

CONTROLLERS = {
    'dwb': (None, 'Local controller: DWBLocalPlanner'),
    'mppi': (MPPI_BLOCK, 'Local controller: MPPIController (DiffDrive)'),
}


def apply_edits(text, edits):
    for old, new, count, _ in edits:
        n = text.count(old)
        assert n == count, f'"{old}" 应该出现 {count} 次，实际 {n} 次（官方文件版本变了？）'
        text = text.replace(old, new)
    return text


def replace_section(text, start_marker, end_marker, replacement):
    pattern = re.compile(re.escape(start_marker) + r'.*?(?=\n' + re.escape(end_marker) + r')', re.DOTALL)
    new_text, n = pattern.subn(replacement.rstrip('\n'), text, count=1)
    assert n == 1, f'Could not replace section {start_marker!r} .. {end_marker!r}'
    return new_text


def write_yaml(text, whys, out):
    header = ('# 本文件由 lidar_nav/tools/make_nav2_params.py 生成，不要手改。\n'
              f'# 来源：{SRC}\n# 改动：\n' + ''.join(f'#   - {why}\n' for why in whys) + '\n')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(header + text)
    print('已生成', os.path.normpath(out))


def build(edits, out):
    text = apply_edits(open(SRC, encoding='utf-8').read(), edits)
    write_yaml(text, [why for *_, why in edits], out)


def main():
    build(EDITS, OUT)
    for name, extra in VARIANTS.items():
        build(EDITS + extra, OUT.replace('nav2_params_sim.yaml', f'nav2_params_{name}.yaml'))

    base_edits = EDITS + VARIANTS['amcl_both']
    base_text = apply_edits(open(SRC, encoding='utf-8').read(), base_edits)
    base_whys = [why for *_, why in base_edits]
    for p_key, (p_block, p_why) in PLANNERS.items():
        for c_key, (c_block, c_why) in CONTROLLERS.items():
            text = base_text
            if c_block is not None:
                text = replace_section(text, '    # DWB parameters\n    FollowPath:', 'local_costmap:', c_block)
            if p_block is not None:
                text = replace_section(text, '    GridBased:', 'smoother_server:', p_block)
            out = OUT.replace('nav2_params_sim.yaml', f'nav2_params_{p_key}_{c_key}.yaml')
            write_yaml(text, [*base_whys, p_why, c_why], out)


if __name__ == '__main__':
    main()
