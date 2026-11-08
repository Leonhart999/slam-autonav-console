"""建图页的数据：用 FAST-LIO 的当前帧点云累积体素地图，记录估计轨迹和仿真真值，打包后推给网页。

为什么不直接用 FAST-LIO 的 /Laser_map：它每次都把从开始到现在的全部点重发一遍（不降采样、不清空），
地图越大越卡。这里改为订阅当前帧 /cloud_registered（已在 camera_init 坐标系），按 0.1 m 体素去重，
只把"新出现的体素"发给网页。

SLAM 算法可以切换（console.yaml mapping.slam_profiles，见 slam_profiles()）：FAST-LIO2 用 /cloud_registered +
/Odometry，GLIM 用 /glim_ros/aligned_points + /glim_ros/odom。所有算法的话题都订阅，只处理当前选中的那个。

线程分工：
- ROS 回调（executor 线程）：点云只放进"最新一帧"槽位；里程计、真值很小，直接处理。
- 工作线程 _worker：处理点云（降采样、找新增体素），处理不过来就丢旧帧。
- 网页服务器（asyncio）：定时调用 status()、pull() 取数据发给网页。
共享数据都用 self._lock 保护。
"""
import json
import math
import os
import struct
import threading
import time
from collections import deque

import numpy as np
from nav_msgs.msg import Odometry
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2

from .runs import RunRecorder

KEY_OFF = 1 << 20          # 体素坐标加上这个偏移后都是正数，每个轴占 21 位，拼成一个 int64
KEY_MAX = 1 << 21
TRAJ_STEP = 0.02           # 轨迹每走 2 cm 记一个点
LENGTH_STEP = 0.01         # 轨迹长度只累加超过 1 cm 的位移，过滤静止时的抖动
GT_SYNC = 0.05             # 真值和估计按时间配对时允许的最大时间差（s）
PULL_MAX_POINTS = 200_000  # 每次最多发多少个地图点，大地图分几次发完


class PointBuffer:
    """只增不减的 (N, 3) float32 点数组，容量不够时翻倍。"""

    def __init__(self, capacity=4096):
        self.data = np.empty((capacity, 3), np.float32)
        self.n = 0

    def append(self, pts):
        need = self.n + len(pts)
        if need > len(self.data):
            grown = np.empty((max(need, 2 * len(self.data)), 3), np.float32)
            grown[:self.n] = self.data[:self.n]
            self.data = grown
        self.data[self.n:need] = pts
        self.n = need

    def last(self):
        return self.data[self.n - 1] if self.n else None


def cloud_xyz(msg):
    """PointCloud2 → (N, 3) float32 数组，去掉 NaN/inf。不依赖点类型，按字段偏移读 x、y、z。"""
    off = {f.name: f.offset for f in msg.fields if f.datatype == 7}   # 7 = FLOAT32
    if not all(k in off for k in 'xyz') or msg.is_bigendian:
        return None
    n = msg.width * msg.height
    dt = np.dtype({'names': ['x', 'y', 'z'], 'formats': ['<f4'] * 3,
                   'offsets': [off['x'], off['y'], off['z']], 'itemsize': msg.point_step})
    arr = np.frombuffer(msg.data, dtype=dt, count=n)
    xyz = np.empty((n, 3), np.float32)
    xyz[:, 0], xyz[:, 1], xyz[:, 2] = arr['x'], arr['y'], arr['z']
    return xyz[np.isfinite(xyz).all(axis=1)]


def voxel_keys(xyz, size):
    """每个点所在体素的编号，以及体素的整数坐标。超出 ±10 万米范围的点编号为 -1。"""
    idx = np.floor(xyz / size).astype(np.int64)
    shifted = idx + KEY_OFF
    keys = (shifted[:, 0] << 42) | (shifted[:, 1] << 21) | shifted[:, 2]
    bad = ((shifted < 0) | (shifted >= KEY_MAX)).any(axis=1)
    keys[bad] = -1
    return keys, idx


def encode(head, pts):
    """二进制帧：[4 字节头长度][JSON 头][float32 x y z ...]。
    头用空格补齐到 4 字节对齐，网页可以直接用 Float32Array 读数据部分，不用复制。"""
    h = json.dumps(head, separators=(',', ':')).encode()
    h += b' ' * (-(4 + len(h)) % 4)
    return struct.pack('<I', len(h)) + h + np.ascontiguousarray(pts, np.float32).tobytes()


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def stamp_of(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def slam_profiles(cfg):
    """SLAM algorithms the mapping page can run: stacks.yaml name -> topics, frame and how the map is saved.

    console.yaml mapping.slam_profiles; an old console.yaml without it gives one FAST-LIO profile built from
    the flat keys (frame, scan_topic, odom_topic, save_service, pcd_path).
    save_mode 'service': call a std_srvs/Trigger service that writes pcd_path (FAST-LIO /map_save).
    save_mode 'dump': the SLAM writes its optimized map only when it exits (GLIM); saving stops the process
    and reads dump_dir.
    inputs / sources: the topics bag playback has to provide, and whether the SLAM is offered for the
    simulation and / or for bags (see playback.py).
    """
    raw = cfg.get('slam_profiles')
    if not raw:
        name = (cfg.get('stacks') or {}).get('slam', 'mapping_fastlio')
        raw = {name: {'algorithm': 'FAST-LIO2', 'frame': cfg.get('frame', 'camera_init'),
                      'scan_topic': cfg.get('scan_topic', '/cloud_registered'),
                      'odom_topic': cfg.get('odom_topic', '/Odometry'),
                      'save': {'service': cfg.get('save_service', '/map_save'),
                               'pcd_path': cfg.get('pcd_path', '~/results/fastlio_map.pcd'),
                               'timeout': cfg.get('save_timeout', 60)}}}
    out = {}
    for name, p in raw.items():
        save = p.get('save') or {}
        out[name] = {
            'algorithm': p.get('algorithm', name), 'frame': p.get('frame', 'map'),
            'scan_topic': p['scan_topic'], 'odom_topic': p['odom_topic'],
            'save_mode': 'dump' if save.get('dump_dir') else 'service',
            'service': save.get('service'), 'timeout': float(save.get('timeout', 60)),
            'pcd_path': os.path.expanduser(save['pcd_path']) if save.get('pcd_path') else None,
            'dump_dir': os.path.expanduser(save['dump_dir']) if save.get('dump_dir') else None,
            # Topics the SLAM subscribes to ({points: ..., imu: ...}); bag playback feeds these (playback.py)
            'inputs': dict(p.get('inputs') or {}),
            # Where its data can come from: 'sim' (the Gazebo simulation) and / or 'bag' (rosbag playback)
            'sources': list(p.get('sources') or ['sim', 'bag']),
        }
    return out


class MappingData:

    def __init__(self, node, cfg):
        self._log = node.get_logger()
        self.voxel = float(cfg.get('voxel', 0.1))
        self.scan_voxel = float(cfg.get('scan_voxel', 0.05))
        self.scan_max = int(cfg.get('scan_max_points', 20000))
        self.min_period = 1.0 / float(cfg.get('process_rate', 5.0))
        # 建图页上各步骤对应 stacks.yaml 里的哪个进程；slam 是当前选中的算法，见 select()
        self.stacks = dict(cfg.get('stacks', {'sim': 'sim', 'slam': 'mapping_fastlio', 'waypoints': 'waypoints'}))
        self.profiles = slam_profiles(cfg)
        if self.stacks.get('slam') not in self.profiles:
            self.stacks['slam'] = next(iter(self.profiles))
        self.topics = {'gt': cfg.get('gt_topic') or None}
        self._use_profile(self.stacks['slam'])
        self._rng = np.random.default_rng(0)

        self._lock = threading.Lock()
        self.epoch = 0
        # 运行记录：每次建图（每个 epoch）一条，见 runs.py。run_info 由 main 设置，返回场景、命令等信息
        self.recorder = RunRecorder(cfg.get('runs_dir', '~/runs'), self._log)
        self.run_info = lambda: {}
        self._run_slam = {}                 # run id -> (SLAM stack name, wall time first seen)
        self._clear()
        self._pubs = {'scan': 0, 'odom': 0}  # 各话题的发布者数量，见 _count_publishers

        # 大点云用 reliable（best_effort 在 DDS 分片时会丢帧）；深度 2，处理不过来就丢旧帧
        cloud_qos = QoSProfile(depth=2, history=HistoryPolicy.KEEP_LAST,
                               reliability=ReliabilityPolicy.RELIABLE)
        # 位姿消息很小，用 best_effort 订阅：能收到 reliable 和 best_effort 两种发布者的数据
        small_qos = QoSProfile(depth=50, history=HistoryPolicy.KEEP_LAST,
                               reliability=ReliabilityPolicy.BEST_EFFORT)
        # Every SLAM profile's topics are subscribed once; only messages of the selected profile are used
        scan_topics = {p['scan_topic'] for p in self.profiles.values()}
        odom_topics = {p['odom_topic'] for p in self.profiles.values()}
        for topic in sorted(scan_topics):
            node.create_subscription(PointCloud2, topic, lambda m, t=topic: self._on_cloud(m, t), cloud_qos)
        for topic in sorted(odom_topics):
            node.create_subscription(Odometry, topic, lambda m, t=topic: self._on_odom(m, t), small_qos)
        if self.topics['gt']:
            node.create_subscription(Odometry, self.topics['gt'], self._on_gt, small_qos)

        self._slot = None                   # 最新一帧点云，等工作线程处理
        self._wake = threading.Event()
        self._last_warn = 0.0
        threading.Thread(target=self._worker, daemon=True).start()

        # 发布者数量：同一话题有两个发布者（例如上次没退出的 FAST-LIO 和新启动的同时在跑），
        # 两边的数据会交替到达。网页据此显示警告；这时也不再按"时间戳倒退"重置地图，否则会来回清空
        self._node = node
        node.create_timer(2.0, self._count_publishers)

    def _count_publishers(self):
        topics = dict(self.topics)
        counts = {k: self._node.count_publishers(topics[k]) for k in ('scan', 'odom')}
        with self._lock:
            self._pubs = counts

    # ======================= SLAM 算法 =======================

    def _use_profile(self, name):
        p = self.profiles[name]
        self.stacks['slam'] = name
        self.frame = p['frame']
        self.topics = {**self.topics, 'scan': p['scan_topic'], 'odom': p['odom_topic']}

    @property
    def profile(self):
        return self.profiles[self.stacks['slam']]

    def select(self, name):
        """Switch the mapping page to another SLAM algorithm (stacks.yaml name). The caller resets the map."""
        if name not in self.profiles:
            raise KeyError(name)
        with self._lock:
            if name != self.stacks['slam']:
                self._use_profile(name)
                self._pubs = {'scan': 0, 'odom': 0}

    def _note_run(self):
        """Remember which SLAM produced each run record (caller holds the lock)."""
        rid = self.recorder.run_id
        if rid and rid not in self._run_slam:
            self._run_slam[rid] = (self.stacks['slam'], time.time())

    def runs_of(self, slam, since):
        """Run ids recorded from `slam` since the wall-clock time `since` (process start)."""
        with self._lock:
            self._note_run()
            return [r for r, (s, t) in self._run_slam.items() if s == slam and t >= since - 5.0]

    def replace_estimate(self, run_id, lines, source, extra=None):
        with self._lock:
            return self.recorder.replace_est(run_id, lines, source, extra)

    # ======================= 状态 =======================

    def _clear(self):
        """开始新一次建图：清空所有数据，epoch 加 1，网页看到新 epoch 会清空画面。调用者持有锁。"""
        if self.recorder.run_id:
            self.recorder.end(**self._summary())
        self.epoch += 1
        self._keys = set()                  # 已有体素的编号
        self._map = PointBuffer(65536)      # 体素中心，顺序 = 出现顺序，网页按偏移量增量接收
        self._lo = self._hi = None          # 地图包围盒
        self._scan = None                   # (序号, 点数组)
        self._scan_seq = 0
        self._est = PointBuffer()           # 估计轨迹
        self._gt = PointBuffer()            # 真值轨迹（已对齐到 camera_init）
        self._pose = None                   # (x, y, z, yaw)
        self._t0 = self._t_last = None      # 第一条、最新一条里程计的时间戳
        self._wall_last = 0.0               # 最新一条里程计的本机时刻
        self._length = 0.0
        self._len_ref = None                # 轨迹长度上一次累加时的位置
        self._speed = 0.0
        self._prev = None                   # 上一条里程计 (t, xyz)，算速度用
        self._gt_buf = deque(maxlen=400)    # 最近的真值 (t, xyz, yaw)，按时间和估计配对
        self._align = None                  # 真值 world → camera_init 的变换 (cos, sin, 平移)
        self._error = None

    def reset(self):
        with self._lock:
            self._clear()

    def close(self):
        """控制台退出时：结束当前运行记录。"""
        with self._lock:
            if self.recorder.run_id:
                self.recorder.end(**self._summary())

    def _summary(self):
        """运行记录里的概况（调用者持有锁）。"""
        return {
            'duration': None if self._t0 is None else round(self._t_last - self._t0, 1),
            'length': round(self._length, 2), 'voxels': self._map.n,
            'extent': None if self._lo is None else [round(float(v), 2) for v in (*self._lo, *self._hi)],
            'last_error': None if self._error is None else round(self._error, 3),
        }

    def voxel_points(self):
        """当前体素地图的一份拷贝 (N, 3)，保存地图时写成轻量的预览点云。"""
        with self._lock:
            return self._map.data[:self._map.n].copy()

    def last_stamp(self):
        """Stamp (s) of the newest SLAM odometry message, or None. Bag playback compares it with /clock to see
        whether the SLAM keeps up with the playback rate."""
        with self._lock:
            return self._t_last

    def export_run(self, directory):
        """在同一把锁内刷新并复制当前运行记录，避免复制到半行轨迹。"""
        import os
        import shutil
        with self._lock:
            run = self.recorder.commit()   # a saved map keeps its run even if the robot barely moved
            if run is None:
                return None
            self._note_run()
            self.recorder.update(force=True, **self._summary())
            os.makedirs(directory, exist_ok=True)
            for filename in ('est.tum', 'gt.tum', 'run.json'):
                shutil.copy2(os.path.join(self.recorder.runs_dir, run, filename),
                             os.path.join(directory, filename))
            return run

    def add_map_to_run(self, name):
        """保存地图后，在当前运行记录里登记（运行记录的文件也被 ROS 回调写，所以要持有锁）。"""
        with self._lock:
            self.recorder.add_map(name)

    # ======================= 点云 =======================

    def _on_cloud(self, msg, topic=None):
        if topic is not None and topic != self.topics['scan']:
            return                          # another SLAM algorithm's topic
        with self._lock:
            self._slot = msg
        self._wake.set()

    def _worker(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                msg, self._slot = self._slot, None
                epoch, keys = self.epoch, self._keys
            if msg is None:
                continue
            t0 = time.monotonic()
            try:
                self._process(msg, epoch, keys)
            except Exception as e:          # 单帧出错不能让线程退出
                if time.monotonic() - self._last_warn > 10:
                    self._last_warn = time.monotonic()
                    self._log.warn(f'处理点云出错：{e!r}')
            # 限制处理频率：相邻帧几乎重叠，5 Hz 已经够把地图建全，还能省一半 CPU
            time.sleep(max(0.0, self.min_period - (time.monotonic() - t0)))

    def _process(self, msg, epoch, keys):
        xyz = cloud_xyz(msg)
        if xyz is None or len(xyz) == 0:
            return

        # 当前帧：细一些的体素去重，点数太多时随机抽样
        k, _ = voxel_keys(xyz, self.scan_voxel)
        _, first = np.unique(k, return_index=True)
        scan = xyz[first]
        if len(scan) > self.scan_max:
            scan = scan[self._rng.choice(len(scan), self.scan_max, replace=False)]

        # 地图：本帧去重后，挑出以前没出现过的体素
        k, idx = voxel_keys(xyz, self.voxel)
        uniq, first = np.unique(k, return_index=True)
        if len(uniq) and uniq[0] == -1:
            uniq, first = uniq[1:], first[1:]
        ul = uniq.tolist()
        new = np.fromiter((u not in keys for u in ul), dtype=bool, count=len(ul))
        centers = ((idx[first[new]] + 0.5) * self.voxel).astype(np.float32)

        with self._lock:
            if epoch != self.epoch:         # 处理期间已经开始了新一次建图，丢掉
                return
            self._scan_seq += 1
            self._scan = (self._scan_seq, scan)
            if len(centers):
                self._keys.update(u for u, n in zip(ul, new) if n)
                self._map.append(centers)
                lo, hi = centers.min(axis=0), centers.max(axis=0)
                self._lo = lo if self._lo is None else np.minimum(self._lo, lo)
                self._hi = hi if self._hi is None else np.maximum(self._hi, hi)

    # ======================= 里程计和真值 =======================

    def _on_odom(self, msg, topic=None):
        if topic is not None and topic != self.topics['odom']:
            return
        t = stamp_of(msg)
        p = msg.pose.pose.position
        xyz = np.array([p.x, p.y, p.z])
        yaw = yaw_of(msg.pose.pose.orientation)
        now = time.monotonic()
        with self._lock:
            if self._t_last is not None and self._pubs['odom'] <= 1:
                # 新一次建图的两种迹象：时间戳倒退（重新放录包、仿真重启）；
                # 或者里程计中断 3 s 以上后从原点重新开始（FAST-LIO 单独重启）。
                # 有多个发布者时不判断：两边交替到达，时间戳本来就会来回跳
                restarted = (now - self._wall_last > 3.0 and np.linalg.norm(xyz) < 0.3
                             and self._pose is not None and math.hypot(*self._pose[:2]) > 1.0)
                if t < self._t_last - 1.0 or restarted:
                    self._clear()
            if self._t0 is None:
                self._t0 = t
                p_ = self.profile
                self.recorder.begin({**self.run_info(), 'slam': self.stacks['slam'],
                                     'algorithm': p_['algorithm'], 'frame': p_['frame']})
            self.recorder.est(t, msg.pose.pose)
            self._note_run()

            if self._len_ref is None:
                self._len_ref = xyz
            elif np.linalg.norm(xyz - self._len_ref) >= LENGTH_STEP:
                self._length += float(np.linalg.norm(xyz - self._len_ref))
                self._len_ref = xyz
            if self._prev is not None and t > self._prev[0]:
                v = float(np.linalg.norm(xyz[:2] - self._prev[1][:2])) / (t - self._prev[0])
                self._speed = 0.7 * self._speed + 0.3 * v
            self._prev = (t, xyz)

            last = self._est.last()
            if last is None or np.linalg.norm(xyz - last) >= TRAJ_STEP:
                self._est.append(xyz[None, :])
            self._pose = (float(p.x), float(p.y), float(p.z), yaw)
            self._t_last, self._wall_last = t, now

            # 仿真：用第一条能配上真值的里程计，把真值对齐到 camera_init（只对齐水平旋转和平移）
            g = self._nearest_gt(t)
            if g is not None:
                if self._align is None:
                    da = g[2] - yaw
                    c, s = math.cos(da), math.sin(da)
                    rot = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
                    self._align = (rot, g[1] - rot @ xyz)
                self._error = float(np.linalg.norm(xyz - self._to_cam(g[1])))
            self.recorder.update(**self._summary())   # 内部限制最多 5 s 写一次盘

    def _on_gt(self, msg):
        t = stamp_of(msg)
        p = msg.pose.pose.position
        xyz = np.array([p.x, p.y, p.z])
        with self._lock:
            self.recorder.gt(t, msg.pose.pose)
            if self._gt_buf and t < self._gt_buf[-1][0] - 1.0:
                self._gt_buf.clear()
            self._gt_buf.append((t, xyz, yaw_of(msg.pose.pose.orientation)))
            if self._align is not None:
                cam = self._to_cam(xyz)
                last = self._gt.last()
                if last is None or np.linalg.norm(cam - last) >= TRAJ_STEP:
                    self._gt.append(cam[None, :])

    def _nearest_gt(self, t):
        if not self._gt_buf:
            return None
        g = min(self._gt_buf, key=lambda e: abs(e[0] - t))
        return g if abs(g[0] - t) <= GT_SYNC else None

    def _to_cam(self, world_xyz):
        rot, trans = self._align
        return rot.T @ (world_xyz - trans)

    # ======================= 给网页的数据 =======================

    def status(self):
        with self._lock:
            pose = None
            if self._pose is not None:
                x, y, z, yaw = self._pose
                pose = {'x': round(x, 3), 'y': round(y, 3), 'z': round(z, 3), 'yaw': round(yaw, 4)}
            extent = None
            if self._lo is not None:
                extent = [round(float(v), 2) for v in (*self._lo, *self._hi)]
            return {
                'type': 'mapping', 'epoch': self.epoch, 'frame': self.frame, 'voxel': self.voxel,
                'pose': pose, 'speed': round(self._speed, 3), 'initialized': self._pose is not None,
                'duration': None if self._t0 is None else round(self._t_last - self._t0, 1),
                'length': round(self._length, 2), 'voxels': self._map.n, 'extent': extent,
                'error': None if self._error is None else round(self._error, 3),
                'has_gt': self._align is not None,
                'publishers': dict(self._pubs),
                'run': self.recorder.run_id,
                'stacks': dict(self.stacks), 'topics': dict(self.topics),
                'slam': self.stacks['slam'], 'algorithm': self.profile['algorithm'],
                'save_mode': self.profile['save_mode'],
                'slam_options': [{'name': n, 'algorithm': p['algorithm'], 'save_mode': p['save_mode'],
                                  'sources': p['sources'], 'needs_imu': 'imu' in p['inputs']}
                                 for n, p in self.profiles.items()],
            }

    def pull(self, cur):
        """取出某个网页客户端还没收到的数据。cur 是这个客户端的进度（各类数据已发到第几个点），会被更新。

        返回要发送的帧列表：dict 按 JSON 文本发送，bytes 按二进制发送。
        客户端第一次来、或开始了新一次建图时，先发一条 mapping_reset 让网页清空画面。
        """
        frames = []
        with self._lock:
            if cur.get('epoch') != self.epoch:
                cur.clear()
                cur.update(epoch=self.epoch, map=0, est=0, gt=0, scan=0)
                frames.append({'type': 'mapping_reset', 'epoch': self.epoch})
            for kind, buf, limit in (('map', self._map, PULL_MAX_POINTS),
                                     ('est', self._est, None), ('gt', self._gt, None)):
                a = cur[kind]
                b = buf.n if limit is None else min(buf.n, a + limit)
                if b > a:
                    frames.append(encode({'type': kind, 'epoch': self.epoch, 'offset': a, 'count': b - a},
                                         buf.data[a:b]))
                    cur[kind] = b
            if self._scan is not None and self._scan[0] != cur['scan']:
                seq, pts = self._scan
                frames.append(encode({'type': 'scan', 'epoch': self.epoch, 'count': len(pts)}, pts))
                cur['scan'] = seq
        return frames
