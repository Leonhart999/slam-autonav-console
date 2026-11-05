"""航点驾驶节点：让仿真小车按一串固定航点自动行驶，用来生成可重复的 SLAM 测试数据。

注意：它直接用 Gazebo 的真值 /ground_truth 做闭环控制，只是"数据采集司机"，
不属于要评估的 SLAM / 导航系统本身。之后的自主导航由 Nav2 完成，不用这个节点。

控制方法（最简单的"先转向、再前进"）：
  - 航向误差大于 turn_in_place（默认 0.4 rad）时原地转向；
  - 否则一边前进一边修正航向，速度随航向误差减小；
  - 离当前航点小于 tolerance（默认 0.15 m）就切到下一个航点；
  - 全部走完后停车并退出，返回码 0。超时（timeout 秒）也会停车退出，返回码 1。
"""
import math
import signal
import sys
import time
import rclpy
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

# 默认路线：在 indoor_office 场景里绕两个房间一圈，最后回到起点附近（便于以后评估回环）
DEFAULT_WAYPOINTS = [
    -3.0, -3.5,   -3.0, 0.0,   2.0, 0.0,   6.5, -1.2,   6.5, 3.8,
    2.0, 3.8,     1.5, 0.0,    -2.5, 0.0,  -6.0, 0.5,   -6.5, -3.5,
]


def wrap(a):
    """把角度限制在 [-pi, pi]"""
    return math.atan2(math.sin(a), math.cos(a))


class WaypointDriver(Node):
    def __init__(self):
        super().__init__('waypoint_driver')
        flat = self.declare_parameter('waypoints', DEFAULT_WAYPOINTS).value
        self.waypoints = list(zip(flat[0::2], flat[1::2]))
        self.v_max = self.declare_parameter('max_speed', 0.5).value          # m/s
        self.w_max = self.declare_parameter('max_turn_rate', 0.8).value      # rad/s
        self.tol = self.declare_parameter('tolerance', 0.15).value           # m
        self.turn_in_place = self.declare_parameter('turn_in_place', 0.4).value
        self.start_delay = self.declare_parameter('start_delay', 3.0).value  # 先静止几秒，给 FAST-LIO 初始化 IMU
        self.timeout = self.declare_parameter('timeout', 300.0).value

        self.pose = None          # (x, y, yaw)
        self.idx = 0
        self.t0 = None
        self.result = None        # 结束时设为 0（完成）或 1（超时）
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.create_subscription(Odometry, '/ground_truth', self.on_odom, 10)
        self.create_timer(0.05, self.step)                                   # 20 Hz 控制
        self.get_logger().info(f'共 {len(self.waypoints)} 个航点，最高 {self.v_max} m/s')

    def on_odom(self, msg):
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        self.pose = (p.x, p.y, yaw)

    def now(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def step(self):
        if self.pose is None or self.result is not None:
            return
        if self.t0 is None:
            self.t0 = self.now()
        t = self.now() - self.t0
        cmd = Twist()
        if t < self.start_delay:
            self.pub.publish(cmd)                    # 起步前保持静止
            return
        if t > self.timeout:
            self.get_logger().error('超时，停车')
            self.finish(1)
            return

        x, y, yaw = self.pose
        gx, gy = self.waypoints[self.idx]
        dist = math.hypot(gx - x, gy - y)
        if dist < self.tol:
            self.get_logger().info(f'到达航点 {self.idx + 1}/{len(self.waypoints)}  ({gx}, {gy})')
            self.idx += 1
            if self.idx == len(self.waypoints):
                self.finish(0)
            return

        err = wrap(math.atan2(gy - y, gx - x) - yaw)
        cmd.angular.z = max(-self.w_max, min(self.w_max, 2.0 * err))
        if abs(err) < self.turn_in_place:
            # 航向基本对准后前进；接近航点时减速
            cmd.linear.x = min(self.v_max, 0.8 * dist) * math.cos(err)
        self.pub.publish(cmd)

    def finish(self, code):
        self.pub.publish(Twist())
        self.result = code


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    # rclpy's own SIGINT handler shuts the context down at once, so the zero-velocity command below could no
    # longer be published and the robot kept driving after the node was stopped. Handle SIGINT/SIGTERM
    # ourselves: leave the loop, send the stop command, then shut down.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    node = WaypointDriver()
    try:
        while rclpy.ok() and node.result is None:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        node.result = 1
    # A second signal (ros2 run / launch forward one) must not interrupt the stop command
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for _ in range(5):                            # 多发几次停车指令
        node.pub.publish(Twist())
        time.sleep(0.05)
    code = node.result if node.result is not None else 1
    node.destroy_node()
    rclpy.try_shutdown()
    sys.exit(code)
