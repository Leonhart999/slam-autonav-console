"""Ground-truth localizer for simulation ablation: publishes map -> odom from /ground_truth.

In simulation Nav2 experiments, comparing AMCL against this node isolates localization error from
planner and controller performance.

TF chain:
  - Gazebo OdometryPublisher publishes /ground_truth (world -> base_link)
  - Gazebo DiffDrive publishes /tf (odom -> base_link)
  - Given the world pose of the map origin (start_x, start_y, start_yaw), we compute:
      T_map_base = inv(T_world_map) * T_world_base
      T_map_odom = T_map_base * inv(T_odom_base)
    and broadcast map -> odom on /tf.
"""
import math

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener


def _wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def _quat_yaw(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class GtLocalizer(Node):
    def __init__(self):
        super().__init__('gt_localizer')
        self.gt_topic = self.declare_parameter('gt_topic', '/ground_truth').value
        self.map_frame = self.declare_parameter('map_frame', 'map').value
        self.odom_frame = self.declare_parameter('odom_frame', 'odom').value
        self.base_frame = self.declare_parameter('base_frame', 'base_link').value
        self.sx = float(self.declare_parameter('start_x', 0.0).value)
        self.sy = float(self.declare_parameter('start_y', 0.0).value)
        self.syaw = float(self.declare_parameter('start_yaw', 0.0).value)
        self.tol = float(self.declare_parameter('transform_tolerance', 0.1).value)
        self.min_period = float(self.declare_parameter('min_period', 0.02).value)

        self._c = math.cos(-self.syaw)
        self._s = math.sin(-self.syaw)
        self._last_pub_sec = -1.0
        self._warned = False

        self.buffer = Buffer(cache_time=Duration(seconds=10))
        self.listener = TransformListener(self.buffer, self)
        self.broadcaster = TransformBroadcaster(self)

        qos = QoSProfile(depth=50, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Odometry, self.gt_topic, self._on_gt, qos)
        self.get_logger().info(
            f'GT localizer active: {self.gt_topic} -> {self.map_frame}->{self.odom_frame} '
            f'(map origin in world: x={self.sx:.3f}, y={self.sy:.3f}, yaw={self.syaw:.4f})'
        )

    def _on_gt(self, msg):
        stamp = msg.header.stamp
        sec = stamp.sec + stamp.nanosec * 1e-9
        if sec <= 0.0:
            return
        if self._last_pub_sec > 0.0 and 0.0 <= sec - self._last_pub_sec < self.min_period:
            return

        try:
            tf_ob = self.buffer.lookup_transform(
                self.odom_frame, self.base_frame, Time.from_msg(stamp), timeout=Duration(seconds=0)
            )
        except TransformException:
            try:
                tf_ob = self.buffer.lookup_transform(
                    self.odom_frame, self.base_frame, Time(), timeout=Duration(seconds=0)
                )
            except TransformException:
                if not self._warned:
                    self.get_logger().info(f'Waiting for {self.odom_frame} -> {self.base_frame} TF...')
                    self._warned = True
                return

        self._warned = False
        p = msg.pose.pose.position
        dx, dy = p.x - self.sx, p.y - self.sy
        mx = self._c * dx - self._s * dy
        my = self._s * dx + self._c * dy
        myaw = _wrap(_quat_yaw(msg.pose.pose.orientation) - self.syaw)

        ob_t = tf_ob.transform.translation
        ob_yaw = _quat_yaw(tf_ob.transform.rotation)
        mo_yaw = _wrap(myaw - ob_yaw)
        cm, sm = math.cos(mo_yaw), math.sin(mo_yaw)
        mo_x = mx - (cm * ob_t.x - sm * ob_t.y)
        mo_y = my - (sm * ob_t.x + cm * ob_t.y)

        out_sec = sec + self.tol
        out = TransformStamped()
        out.header.stamp.sec = int(out_sec)
        out.header.stamp.nanosec = int((out_sec - int(out_sec)) * 1e9)
        out.header.frame_id = self.map_frame
        out.child_frame_id = self.odom_frame
        out.transform.translation.x = mo_x
        out.transform.translation.y = mo_y
        out.transform.translation.z = 0.0
        out.transform.rotation.z = math.sin(mo_yaw * 0.5)
        out.transform.rotation.w = math.cos(mo_yaw * 0.5)
        self.broadcaster.sendTransform(out)
        self._last_pub_sec = sec


def main():
    rclpy.init()
    node = GtLocalizer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
