"""点云适配节点：把 Gazebo 的点云整理成 FAST-LIO（velodyne 模式）能直接用的格式。

Gazebo gpu_lidar 输出的点云有两个问题：
  1. 没打到东西的方向会输出 inf 点。FAST-LIO 的 velodyne 处理函数不检查 inf，会把它们加进地图。
  2. 没有每个点的时间戳字段 time。FAST-LIO 发现没有 time 时，会按方位角推算每个点的时间，
     再做运动畸变补偿。但 Gazebo 的 gpu_lidar 是在同一个仿真时刻一次性渲染整圈，点云本来
     就没有运动畸变，按方位角推算反而会引入假的"补偿"误差。

所以本节点做三件事：
  - 去掉非有限值的点，以及距离不在 [min_range, max_range] 内的点；
  - 加上 time 字段，所有点都填一个极小的正数（1 微秒）。FAST-LIO 只要看到最后一个点的
    time > 0 就会使用这个字段，而 1 微秒的偏移几乎等于"整圈同一时刻"；
  - 保留 ring 字段（FAST-LIO 用它区分扫描线）。

输入：/lidar/points（Gazebo 原始点云）  输出：/points_fastlio
"""
import signal

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import PointCloud2, PointField

# 输出点格式：x y z intensity time（都是 float32）+ ring（uint16），对齐到 24 字节
OUT_DTYPE = np.dtype({
    'names': ['x', 'y', 'z', 'intensity', 'time', 'ring'],
    'formats': ['<f4', '<f4', '<f4', '<f4', '<f4', '<u2'],
    'offsets': [0, 4, 8, 12, 16, 20],
    'itemsize': 24,
})
OUT_FIELDS = [
    PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
    PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
    PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
    PointField(name='intensity', offset=12, datatype=PointField.FLOAT32, count=1),
    PointField(name='time', offset=16, datatype=PointField.FLOAT32, count=1),
    PointField(name='ring', offset=20, datatype=PointField.UINT16, count=1),
]
# PointField 数据类型编号 -> numpy 类型
NP_TYPE = {PointField.FLOAT32: '<f4', PointField.FLOAT64: '<f8',
           PointField.UINT16: '<u2', PointField.INT16: '<i2',
           PointField.UINT32: '<u4', PointField.INT32: '<i4',
           PointField.UINT8: 'u1', PointField.INT8: 'i1'}


class CloudAdapter(Node):
    def __init__(self):
        super().__init__('cloud_adapter')
        # 下限用 0.35 而不是雷达的最小量程 0.3：Gazebo（ogre1 渲染）每帧会在正好 0.30 m 处
        # 产生约 1500 个假点，各个方位都有，跟着车一起移动，会在地图上沿轨迹留下一串圆圈
        self.min_range = self.declare_parameter('min_range', 0.35).value
        self.max_range = self.declare_parameter('max_range', 100.0).value
        self.point_time = self.declare_parameter('point_time', 1e-6).value   # 秒
        self.pub = self.create_publisher(PointCloud2, '/points_fastlio', 10)
        # 用默认的 reliable QoS。一帧点云近 1 MB，best-effort（sensor_data）在 DDS 分片传输时
        # 会大量丢帧，实测只收到约 8 Hz
        self.create_subscription(PointCloud2, '/lidar/points', self.on_cloud, 10)
        self.get_logger().info(
            f'点云适配：/lidar/points -> /points_fastlio，保留 {self.min_range}–{self.max_range} m 的点')

    def on_cloud(self, msg):
        # 1. 按输入消息的字段描述，把字节流解析成 numpy 结构化数组
        names = [f.name for f in msg.fields]
        in_dtype = np.dtype({
            'names': names,
            'formats': [NP_TYPE[f.datatype] for f in msg.fields],
            'offsets': [f.offset for f in msg.fields],
            'itemsize': msg.point_step,
        })
        pts = np.frombuffer(bytes(msg.data), dtype=in_dtype)

        # 2. 去掉 inf / nan，以及距离超出范围的点
        x, y, z = pts['x'], pts['y'], pts['z']
        r = np.sqrt(x * x + y * y + z * z)
        keep = np.isfinite(r) & (r >= self.min_range) & (r <= self.max_range)
        pts = pts[keep]

        # 3. 组装输出点云
        out = np.zeros(len(pts), dtype=OUT_DTYPE)
        out['x'], out['y'], out['z'] = pts['x'], pts['y'], pts['z']
        out['intensity'] = pts['intensity'] if 'intensity' in names else 0.0
        out['ring'] = pts['ring'] if 'ring' in names else 0
        out['time'] = self.point_time

        cloud = PointCloud2()
        cloud.header = msg.header             # 时间戳和坐标系（lidar_link）保持不变
        cloud.height, cloud.width = 1, len(out)
        cloud.fields = OUT_FIELDS
        cloud.is_bigendian = False
        cloud.point_step = OUT_DTYPE.itemsize
        cloud.row_step = cloud.point_step * cloud.width
        cloud.is_dense = True
        cloud.data = out.tobytes()
        self.pub.publish(cloud)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    # Handle SIGINT/SIGTERM ourselves and ignore repeats during cleanup: launch forwards a second SIGINT,
    # which used to land inside destroy_node() and print a KeyboardInterrupt traceback on every shutdown.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    node = CloudAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    node.destroy_node()
    rclpy.try_shutdown()
