"""启动室内仿真：Gazebo（含小车）+ ROS2 桥接 + 静态 TF + 点云适配节点。

用法：
    ros2 launch sim_robot sim.launch.py              # 无界面（后台跑，最快）
    ros2 launch sim_robot sim.launch.py gui:=true    # 打开 Gazebo 窗口
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess,
                            OpaqueFunction, SetEnvironmentVariable)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

PKG = get_package_share_directory('sim_robot')


def gazebo(context):
    """启动 Gazebo。服务端（物理 + 传感器）和界面分成两个进程，用不同的渲染方式：

    - 服务端：ogre2 + 软件渲染（LIBGL_ALWAYS_SOFTWARE=1）。WSL2 的显卡驱动跑 ogre2 会崩溃；
      而 ogre1 虽然能用显卡，但它的 gpu_lidar 测距误差很大（地面点 z 误差标准差约 0.1 m，
      远处偏差 0.33 m），会把 FAST-LIO 的地图弄弯。软件渲染的 ogre2 误差约 0.03 m，
      仿真速度仍是实时（实测实时因子 1.0）。
    - 界面：默认也用 ogre2 + 软件渲染。ogre1 + 显卡在 WSLg 里显示会出错：
      墙一会儿出现一会儿消失（只是显示问题，传感器数据不受影响）。
      想要更流畅可以加 gui_gpu:=true 改回 ogre1 + 显卡。
    """
    world = os.path.join(PKG, 'worlds', LaunchConfiguration('world').perform(context))
    gui = LaunchConfiguration('gui').perform(context).lower() == 'true'
    gui_gpu = LaunchConfiguration('gui_gpu').perform(context).lower() == 'true'
    procs = [ExecuteProcess(cmd=['ign', 'gazebo', '-s', '-r', '--headless-rendering', world],
                            additional_env={'LIBGL_ALWAYS_SOFTWARE': '1'}, output='screen')]
    if gui and gui_gpu:
        procs.append(ExecuteProcess(cmd=['ign', 'gazebo', '-g', '--render-engine', 'ogre'], output='screen'))
    elif gui:
        procs.append(ExecuteProcess(cmd=['ign', 'gazebo', '-g', '--render-engine', 'ogre2'],
                                    additional_env={'LIBGL_ALWAYS_SOFTWARE': '1'}, output='screen'))
    return procs


def generate_launch_description():
    sim_time = {'use_sim_time': True}

    # Gazebo 话题 <-> ROS2 话题。"[" 表示 Gazebo→ROS，"]" 表示 ROS→Gazebo
    bridge = Node(
        package='ros_gz_bridge', executable='parameter_bridge', name='gz_bridge',
        arguments=[
            '/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
            '/lidar/points@sensor_msgs/msg/PointCloud2[ignition.msgs.PointCloudPacked',
            '/imu@sensor_msgs/msg/Imu[ignition.msgs.IMU',
            '/ground_truth@nav_msgs/msg/Odometry[ignition.msgs.Odometry',
            '/wheel_odom@nav_msgs/msg/Odometry[ignition.msgs.Odometry',
            '/cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist',
            # 差速插件发布的 odom -> base_link，改名成 /tf，给 Nav2 当里程计用
            '/wheel_odom_tf@tf2_msgs/msg/TFMessage[ignition.msgs.Pose_V',
        ],
        remappings=[('/wheel_odom_tf', '/tf')],
        parameters=[sim_time], output='screen')

    # 传感器安装位置（和 model.sdf 一致），给 rviz2 和以后的 Nav2 用
    tf_lidar = Node(package='tf2_ros', executable='static_transform_publisher', name='tf_base_lidar',
                    arguments=['--x', '0.1', '--z', '0.3',
                               '--frame-id', 'base_link', '--child-frame-id', 'lidar_link'],
                    parameters=[sim_time])
    tf_imu = Node(package='tf2_ros', executable='static_transform_publisher', name='tf_base_imu',
                  arguments=['--frame-id', 'base_link', '--child-frame-id', 'imu_link'],
                  parameters=[sim_time])

    adapter = Node(package='sim_robot', executable='cloud_adapter',
                   parameters=[sim_time], output='screen')

    return LaunchDescription([
        DeclareLaunchArgument('gui', default_value='false', description='是否打开 Gazebo 窗口'),
        DeclareLaunchArgument('gui_gpu', default_value='false',
                              description='窗口是否用显卡渲染（ogre1，流畅但在 WSLg 里墙会闪烁）'),
        DeclareLaunchArgument('world', default_value='indoor_office.sdf', description='worlds/ 下的场景文件'),
        # 让 Gazebo 能通过 model://sim_robot 找到小车模型，model://shelf 等找到 Fuel 下载的仓库模型
        SetEnvironmentVariable('IGN_GAZEBO_RESOURCE_PATH',
                               os.path.join(PKG, 'models') + ':' + os.path.join(PKG, 'models', 'fuel')),
        OpaqueFunction(function=gazebo),
        bridge, tf_lidar, tf_imu, adapter,
    ])
