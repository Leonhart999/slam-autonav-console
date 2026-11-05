"""启动导航：地图服务器 + AMCL 定位 + Nav2 导航 + 点云转 2D 激光。

需要先启动仿真（ros2 launch sim_robot sim.launch.py），本文件只负责导航部分。
用法：
    ros2 launch lidar_nav nav.launch.py                               # 默认地图 ~/maps/office_fastlio.yaml
    ros2 launch lidar_nav nav.launch.py map:=/path/to/map.yaml rviz:=true
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

PKG = get_package_share_directory('lidar_nav')
NAV2 = get_package_share_directory('nav2_bringup')


def generate_launch_description():
    map_yaml = LaunchConfiguration('map')
    params = LaunchConfiguration('params_file')

    # 3D 点云 -> 2D 激光：只取雷达坐标系中 z 在 [-0.3, 0.3] m 之间的点（离地约 0.1–0.7 m），
    # 和 2D 地图投影时用的障碍物高度带一致，AMCL 才能把激光和地图对上
    scan = Node(
        package='pointcloud_to_laserscan', executable='pointcloud_to_laserscan_node', name='cloud_to_scan',
        remappings=[('cloud_in', '/points_fastlio'), ('scan', '/scan')],
        parameters=[{
            'use_sim_time': True,
            'target_frame': 'lidar_link',
            'min_height': -0.3, 'max_height': 0.3,
            'angle_min': -3.14159, 'angle_max': 3.14159, 'angle_increment': 0.0035,   # 0.2°，和雷达一样
            'scan_time': 0.1, 'range_min': 0.35, 'range_max': 30.0,
            'use_inf': True,
        }], output='screen')

    # Nav2 官方的 bringup：map_server + AMCL（localization）+ 规划/控制等（navigation）
    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(NAV2, 'launch', 'bringup_launch.py')),
        launch_arguments={'map': map_yaml, 'params_file': params,
                          'use_sim_time': 'True', 'autostart': 'True', 'slam': 'False'}.items())

    rviz = Node(package='rviz2', executable='rviz2',
                arguments=['-d', os.path.join(NAV2, 'rviz', 'nav2_default_view.rviz')],
                parameters=[{'use_sim_time': True}], condition=IfCondition(LaunchConfiguration('rviz')))

    return LaunchDescription([
        DeclareLaunchArgument('map', default_value=os.path.expanduser('~/maps/office_fastlio.yaml'),
                              description='2D 栅格地图（pcd_to_grid 生成的 yaml）'),
        DeclareLaunchArgument('params_file', default_value=os.path.join(PKG, 'config', 'nav2_params_sim.yaml')),
        DeclareLaunchArgument('rviz', default_value='false'),
        scan, nav2, rviz,
    ])
