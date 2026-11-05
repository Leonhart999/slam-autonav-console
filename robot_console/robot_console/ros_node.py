"""控制台的 ROS2 节点：监控话题频率、TF、节点列表、仿真时间，执行急停等命令。

这个节点在后台线程里由 executor 运行；网页服务器在主线程里，通过 snapshot() 读取状态。
所有共享状态都用 self._lock 保护。
"""
import threading
import time
from collections import deque

from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy, qos_profile_sensor_data)
from rosgraph_msgs.msg import Clock
from rosidl_runtime_py.utilities import get_message
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage

HZ_WINDOW = 2.0      # 计算话题频率用最近多少秒的消息
GRAPH_PERIOD = 2.0   # 多久检查一次 ROS 图（话题、节点是否出现）


class TopicStat:
    """一个被监控的话题：记录最近收到消息的时刻，用来算频率。"""

    def __init__(self, name, expected_hz, qos=None):
        self.name = name
        self.expected_hz = expected_hz
        self.qos = qos              # 配置里指定的 QoS，None 表示自动
        self.type = None
        self.sub = None
        self.publishers = 0
        # 200 Hz × 2 s = 400 条，多留一些余量
        self.stamps = deque(maxlen=1000)

    def hz(self, now):
        recent = [t for t in self.stamps if now - t <= HZ_WINDOW]
        if len(recent) < 2:
            return 0.0
        return (len(recent) - 1) / (recent[-1] - recent[0])


class ConsoleNode(Node):

    def __init__(self, cfg):
        super().__init__('robot_console')
        self._lock = threading.Lock()

        # ---------- 仿真时间 ----------
        self._sim_clock = None          # (仿真时间秒数, 收到时的本机时刻)
        self.create_subscription(Clock, '/clock', self._on_clock, qos_profile_sensor_data)

        # ---------- 话题频率 ----------
        self._topics = [TopicStat(t['name'], t.get('hz'), t.get('qos')) for t in cfg['topics']]

        # ---------- TF ----------
        # 直接订阅 /tf 和 /tf_static，记录每一条父子边最后一次更新的时刻
        self._tf_expected = [tuple(e) for e in cfg['tf_edges']]
        self._tf = {}               # (父, 子) -> {'static': bool, 'last': 本机时刻, 'stamps': deque}
        self.create_subscription(TFMessage, '/tf', lambda m: self._on_tf(m, False), 100)
        static_qos = QoSProfile(depth=100, history=HistoryPolicy.KEEP_LAST,
                                reliability=ReliabilityPolicy.RELIABLE,
                                durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(TFMessage, '/tf_static', lambda m: self._on_tf(m, True), static_qos)

        # ---------- 节点列表 ----------
        self._nodes = []

        # ---------- 急停 ----------
        est = cfg['estop']
        self._cmd_pub = self.create_publisher(Twist, est.get('cmd_vel_topic', '/cmd_vel'), 10)
        self._zero_until = 0.0      # 在这个时刻之前一直发零速度
        self._zero_duration = float(est.get('zero_duration', 1.0))
        self._cancel_clients = {
            a: self.create_client(CancelGoal, a + '/_action/cancel_goal')
            for a in est.get('cancel_actions', [])}
        self.create_timer(0.1, self._zero_tick)

        self._update_graph()
        self.create_timer(GRAPH_PERIOD, self._update_graph)

    # ======================= 回调 =======================

    def _on_clock(self, msg):
        with self._lock:
            self._sim_clock = (msg.clock.sec + msg.clock.nanosec * 1e-9, time.monotonic())

    def _on_topic(self, stat):
        # raw=True 订阅：收到的是未解析的字节，不做反序列化，大点云也几乎不耗 CPU
        def cb(_raw):
            with self._lock:
                stat.stamps.append(time.monotonic())
        return cb

    def _on_tf(self, msg, static):
        now = time.monotonic()
        with self._lock:
            for t in msg.transforms:
                key = (t.header.frame_id.lstrip('/'), t.child_frame_id.lstrip('/'))
                edge = self._tf.setdefault(key, {'static': static, 'last': now,
                                                 'stamps': deque(maxlen=500)})
                edge['static'] = static
                edge['last'] = now
                edge['stamps'].append(now)

    def _update_graph(self):
        """定时检查 ROS 图：被监控的话题出现后再订阅，同时刷新发布者数量和节点列表。"""
        types = dict(self.get_topic_names_and_types())
        for stat in self._topics:
            pubs = self.get_publishers_info_by_topic(stat.name) if stat.name in types else []
            with self._lock:
                stat.publishers = len(pubs)
            if stat.sub is not None or stat.name not in types:
                continue
            stat.type = types[stat.name][0]
            try:
                msg_cls = get_message(stat.type)
            except (AttributeError, ModuleNotFoundError, ValueError) as e:
                # 消息类型的包没装（例如 livox 的 CustomMsg 没 source），不监控这个话题
                self.get_logger().warn(f'{stat.name}: 无法加载消息类型 {stat.type}：{e}')
                stat.sub = False
                continue
            stat.sub = self.create_subscription(
                msg_cls, stat.name, self._on_topic(stat), self._choose_qos(stat, pubs), raw=True)

        names = [ns.rstrip('/') + '/' + n for n, ns in self.get_node_names_and_namespaces()]
        with self._lock:
            self._nodes = sorted(names)

    @staticmethod
    def _choose_qos(stat, pubs):
        """按发布者的 QoS 选择订阅的可靠性。

        - 发布者全是 reliable 时用 reliable：大点云用 best_effort 订阅会丢帧（DDS 分片时丢包），频率偏低。
        - 只要有 best_effort 发布者就用 best_effort：reliable 订阅收不到 best_effort 发布者的数据。
        """
        if stat.qos == 'reliable':
            rel = ReliabilityPolicy.RELIABLE
        elif stat.qos == 'best_effort':
            rel = ReliabilityPolicy.BEST_EFFORT
        elif pubs and all(p.qos_profile.reliability == ReliabilityPolicy.RELIABLE for p in pubs):
            rel = ReliabilityPolicy.RELIABLE
        else:
            rel = ReliabilityPolicy.BEST_EFFORT
        return QoSProfile(depth=10, history=HistoryPolicy.KEEP_LAST, reliability=rel,
                          durability=DurabilityPolicy.VOLATILE)

    # ======================= 急停 =======================

    def estop(self):
        """急停：取消所有导航目标，并在接下来一段时间里连续发零速度。"""
        with self._lock:
            self._zero_until = time.monotonic() + self._zero_duration
        cancelled = []
        for action, client in self._cancel_clients.items():
            if client.service_is_ready():
                # goal_id 全零、时间戳为 0 的请求表示"取消这个动作的全部目标"
                client.call_async(CancelGoal.Request())
                cancelled.append(action)
        self._cmd_pub.publish(Twist())
        return cancelled

    def _zero_tick(self):
        with self._lock:
            active = time.monotonic() < self._zero_until
        if active:
            self._cmd_pub.publish(Twist())

    # ======================= 服务调用 =======================

    def call_trigger(self, service, timeout):
        """调用 std_srvs/Trigger 服务（例如 FAST-LIO 的 /map_save），阻塞等待结果。

        不能在 executor 线程里调用（会死锁），只在网页服务器或进程管理的线程里用。
        返回一条结果消息 {'code': ..., 'params': {...}}，由网页按当前语言翻译成文字：
          service_ok / service_failed（params.text 是服务自己返回的说明）、service_missing、service_timeout
        """
        client = self.create_client(Trigger, service)
        try:
            if not client.wait_for_service(timeout_sec=2.0):
                return {'code': 'service_missing', 'params': {'service': service}}
            done = threading.Event()
            future = client.call_async(Trigger.Request())
            future.add_done_callback(lambda _f: done.set())
            if not done.wait(timeout):
                return {'code': 'service_timeout', 'params': {'service': service, 'timeout': timeout}}
            res = future.result()
            return {'code': 'service_ok' if res.success else 'service_failed',
                    'params': {'service': service, 'text': res.message}}
        finally:
            self.destroy_client(client)

    # ======================= 状态快照 =======================

    def sim_now(self):
        """Estimated current simulation time in seconds, or None without a fresh /clock."""
        with self._lock:
            if self._sim_clock is None:
                return None
            sim, recv = self._sim_clock
        elapsed = time.monotonic() - recv
        return None if elapsed > 2.0 else sim + elapsed

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            if self._sim_clock is None:
                clock = None
            else:
                sim, recv = self._sim_clock
                clock = {'sim_time': sim, 'stale': now - recv > 2.0}

            topics = []
            for s in self._topics:
                hz = s.hz(now)
                last = s.stamps[-1] if s.stamps else None
                topics.append({
                    'name': s.name, 'type': s.type, 'publishers': s.publishers,
                    'expected_hz': s.expected_hz, 'hz': round(hz, 1),
                    'age': None if last is None else round(now - last, 1),
                })

            def edge_info(key):
                e = self._tf.get(key)
                if e is None:
                    return {'parent': key[0], 'child': key[1], 'present': False}
                recent = [t for t in e['stamps'] if now - t <= HZ_WINDOW]
                hz = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) >= 2 else 0.0
                return {'parent': key[0], 'child': key[1], 'present': True, 'static': e['static'],
                        'age': round(now - e['last'], 1), 'hz': round(hz, 1)}

            tf = {'expected': [edge_info(k) for k in self._tf_expected],
                  'other': [edge_info(k) for k in sorted(self._tf) if k not in self._tf_expected]}
            nodes = list(self._nodes)
        return {'clock': clock, 'topics': topics, 'tf': tf, 'nodes': nodes}
