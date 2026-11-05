"""导航测试节点：依次把一串目标点发给 Nav2，记录每个目标的结果，用仿真真值评估。

对每个目标记录：
  - 结果：成功 / 失败 / 超时
  - 用时（仿真时间）、实际行驶路程（按真值累计）
  - 到点误差：停下时真实位置（真值）到目标点的距离
  - 定位误差：停下时 AMCL 估计位置和真实位置的差
真值 /ground_truth 是 Gazebo world 坐标系，要换到 map 坐标系：2D 地图来自 FAST-LIO2，
以小车起步时的位姿为原点和朝向，所以先减去起点 (start_x, start_y)，再转过 -start_yaw。

用法：ros2 run lidar_nav nav_goals --ros-args -p use_sim_time:=true -p out:=~/results/nav_test.csv
      换场景时用参数文件给出起点和目标点：--params-file lidar_nav/config/goals_warehouse.yaml
"""
import csv
import math
import os
import time
import rclpy
from rclpy.duration import Duration
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

# 默认目标点（map 坐标系，单位 m），在 indoor_office 场景里依次是：
# 西屋中部、穿门到东屋、东屋东北角、东屋南侧、回西屋进凹室、回起点
DEFAULT_GOALS = [3.5, 0.0, 8.5, 3.5, 13.0, 7.0, 10.5, 2.5, 0.5, 6.5, 0.0, 0.0]


def make_pose(nav, x, y, yaw=0.0):
    p = PoseStamped()
    p.header.frame_id = 'map'
    p.header.stamp = nav.get_clock().now().to_msg()
    p.pose.position.x, p.pose.position.y = x, y
    p.pose.orientation.z, p.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
    return p


class Watcher:
    """订阅真值和 AMCL 位姿。订阅直接挂在 BasicNavigator 节点上：
    BasicNavigator 在等待任务完成时会 spin 自己，这些回调就会跟着执行。
    （单独建一个节点、在循环里 spin_once 它的话，实测一条真值都收不到。）"""
    def __init__(self, node, sx, sy, syaw):
        self.sx, self.sy = sx, sy
        self.c, self.s = math.cos(-syaw), math.sin(-syaw)
        self.gt = None       # map 坐标系下的真实位置
        self.amcl = None
        self.path = 0.0      # 累计路程
        node.create_subscription(Odometry, '/ground_truth', self.on_gt, 10)
        node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.on_amcl, 10)

    def on_gt(self, m):
        dx, dy = m.pose.pose.position.x - self.sx, m.pose.pose.position.y - self.sy
        p = (self.c * dx - self.s * dy, self.s * dx + self.c * dy)      # 转到 map 坐标系
        if self.gt is not None:
            self.path += math.dist(p, self.gt)
        self.gt = p

    def on_amcl(self, m):
        self.amcl = (m.pose.pose.position.x, m.pose.pose.position.y)


def main():
    rclpy.init()
    nav = BasicNavigator()
    flat = nav.declare_parameter('goals', DEFAULT_GOALS).value
    goals = list(zip(flat[0::2], flat[1::2]))
    timeout = nav.declare_parameter('timeout', 120.0).value
    out = os.path.expanduser(nav.declare_parameter('out', '~/results/nav_test.csv').value)
    sx = nav.declare_parameter('start_x', -6.5).value
    sy = nav.declare_parameter('start_y', -3.5).value
    syaw = nav.declare_parameter('start_yaw', 0.0).value
    w = Watcher(nav, sx, sy, syaw)

    def spin(sec):
        """让导航节点处理一会儿消息（真值、AMCL 位姿）"""
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(nav, timeout_sec=0.02)

    while w.gt is None:
        nav.get_logger().info('等待真值 /ground_truth ...')
        spin(1.0)

    nav.setInitialPose(make_pose(nav, 0.0, 0.0))
    nav.waitUntilNav2Active(localizer='amcl')

    rows = []
    for i, (gx, gy) in enumerate(goals, 1):
        nav.get_logger().info(f'目标 {i}/{len(goals)}：({gx}, {gy})')
        spin(0.2)
        w.path = 0.0
        t0 = nav.get_clock().now()
        nav.goToPose(make_pose(nav, gx, gy))
        while not nav.isTaskComplete():          # isTaskComplete 内部会 spin 导航节点
            if nav.get_clock().now() - t0 > Duration(seconds=timeout):
                nav.cancelTask()
        res = nav.getResult()
        dt = (nav.get_clock().now() - t0).nanoseconds * 1e-9
        spin(0.5)                                # 再收一会儿消息，拿到停稳后的位姿
        status = {TaskResult.SUCCEEDED: '成功', TaskResult.CANCELED: '超时', TaskResult.FAILED: '失败'}.get(res, str(res))
        goal_err = math.dist(w.gt, (gx, gy))
        loc_err = math.dist(w.gt, w.amcl) if w.amcl else float('nan')
        rows.append([i, gx, gy, status, f'{dt:.1f}', f'{w.path:.2f}', f'{goal_err:.3f}', f'{loc_err:.3f}'])
        nav.get_logger().info(f'  {status}：用时 {dt:.1f} s，路程 {w.path:.2f} m，'
                              f'到点误差 {goal_err:.3f} m，AMCL 定位误差 {loc_err:.3f} m')

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', newline='') as f:
        cw = csv.writer(f)
        cw.writerow(['序号', '目标x', '目标y', '结果', '用时s', '路程m', '到点误差m', 'AMCL定位误差m'])
        cw.writerows(rows)
    ok = sum(r[3] == '成功' for r in rows)
    nav.get_logger().info(f'完成：{ok}/{len(rows)} 个目标成功，结果已保存到 {out}')
    nav.destroy_node()
    rclpy.shutdown()
