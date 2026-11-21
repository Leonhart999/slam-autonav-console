"""启动导航：地图服务器 + 定位（AMCL 或真值定位）+ Nav2 导航 + 点云转 2D 激光。

需要先启动仿真（ros2 launch sim_robot sim.launch.py），本文件只负责导航部分。
用法：
    ros2 launch lidar_nav nav.launch.py                               # 默认地图 ~/maps/office_fastlio.yaml，AMCL
    ros2 launch lidar_nav nav.launch.py map:=/path/to/map.yaml localizer:=gt start_x:=-6.5 start_y:=-3.5
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml

PKG = get_package_share_directory('lidar_nav')
NAV2 = get_package_share_directory('nav2_bringup')


def _nav2_actions(context):
    map_yaml = LaunchConfiguration('map').perform(context)
    params = LaunchConfiguration('params_file').perform(context)
    localizer = LaunchConfiguration('localizer').perform(context).strip().lower()
    if localizer not in ('amcl', 'gt'):
        raise ValueError(f'Unknown localizer {localizer!r} (expected amcl or gt)')

    # Separate processes (use_composition:=False) avoid Humble's nav2_container load_node race/hang
    # under CPU load and shut down cleanly on SIGINT.
    if localizer == 'amcl':
        return [IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(NAV2, 'launch', 'bringup_launch.py')),
            launch_arguments={'map': map_yaml, 'params_file': params,
                              'use_sim_time': 'True', 'autostart': 'True', 'slam': 'False',
                              'use_composition': 'False'}.items())]

    start_x = float(LaunchConfiguration('start_x').perform(context))
    start_y = float(LaunchConfiguration('start_y').perform(context))
    start_yaw = float(LaunchConfiguration('start_yaw').perform(context))
    remappings = [('/tf', 'tf'), ('/tf_static', 'tf_static')]
    configured_params = ParameterFile(
        RewrittenYaml(
            source_file=params,
            root_key='',
            param_rewrites={'use_sim_time': 'True', 'yaml_filename': map_yaml},
            convert_types=True),
        allow_substs=True)

    return [
        Node(
            package='nav2_map_server',
            executable='map_server',
            name='map_server',
            output='screen',
            parameters=[configured_params],
            remappings=remappings),
        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_localization',
            output='screen',
            parameters=[{'use_sim_time': True, 'autostart': True, 'node_names': ['map_server']}]),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(NAV2, 'launch', 'navigation_launch.py')),
            launch_arguments={'use_sim_time': 'True', 'autostart': 'True', 'params_file': params,
                              'use_composition': 'False'}.items()),
        Node(
            package='lidar_nav', executable='gt_localizer', name='gt_localizer',
            parameters=[{'use_sim_time': True,
                         'start_x': start_x, 'start_y': start_y, 'start_yaw': start_yaw}],
            output='screen'),
    ]


def generate_launch_description():
    # 3D 点云 -> 2D 激光：只取雷达坐标系中 z 在 [-0.3, 0.3] m 之间的点（离地约 0.1–0.7 m），
    # 和 2D 地图投影时用的障碍物高度带一致，AMCL 和代价地图才能把激光和地图对上
    scan = Node(
        package='pointcloud_to_laserscan', executable='pointcloud_to_laserscan_node', name='cloud_to_scan',
        remappings=[('cloud_in', '/points_fastlio'), ('scan', '/scan')],
        parameters=[{
            'use_sim_time': True,
            'target_frame': 'lidar_link',
            'min_height': -0.3, 'max_height': 0.3,
            'angle_min': -3.14159, 'angle_max': 3.14159, 'angle_increment': 0.0035,
            'scan_time': 0.1, 'range_min': 0.35, 'range_max': 30.0,
            'use_inf': True,
        }], output='screen')

    rviz = Node(package='rviz2', executable='rviz2',
                arguments=['-d', os.path.join(NAV2, 'rviz', 'nav2_default_view.rviz')],
                parameters=[{'use_sim_time': True}], condition=IfCondition(LaunchConfiguration('rviz')))

    return LaunchDescription([
        DeclareLaunchArgument('map', default_value=os.path.expanduser('~/maps/office_fastlio.yaml'),
                              description='2D 栅格地图（pcd_to_grid 生成的 yaml）'),
        DeclareLaunchArgument('params_file', default_value=os.path.join(PKG, 'config', 'nav2_params_sim.yaml')),
        DeclareLaunchArgument('localizer', default_value='amcl', description='定位方式：amcl 或 gt'),
        DeclareLaunchArgument('start_x', default_value='0.0', description='map 原点在 world 坐标系中的 x（gt 模式）'),
        DeclareLaunchArgument('start_y', default_value='0.0', description='map 原点在 world 坐标系中的 y（gt 模式）'),
        DeclareLaunchArgument('start_yaw', default_value='0.0', description='map 原点在 world 坐标系中的 yaw（gt 模式）'),
        DeclareLaunchArgument('rviz', default_value='false'),
        scan, OpaqueFunction(function=_nav2_actions), rviz,
    ])
