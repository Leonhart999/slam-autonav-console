"""启动网页控制台后端。

用法：ros2 launch robot_console console.launch.py
等价于 ros2 run robot_console console，方便以后和其他节点写在一起启动。
"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        # 控制台本身不用仿真时间：话题频率按本机时间算，仿真时间直接读 /clock 显示
        Node(package='robot_console', executable='console', name='robot_console', output='screen',
             sigterm_timeout='40'),   # 留时间给它停掉子进程（存图最多 15 s + 逐级发信号）
    ])
