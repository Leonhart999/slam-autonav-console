"""遥控：网页通过 WebSocket 以 10 Hz 发来 {v, w}（线速度 m/s、角速度 rad/s），限幅后发布到 /cmd_vel。

安全措施：
- 看门狗：超过 timeout 秒没收到新指令（网页断开、切到别的页面、卡住）就发一次零速度并停止。
- 急停后锁定：急停时调用 lock()，网页上点"解除锁定"才能继续遥控，避免急停后又被摇杆带着走。
- 自动航点运行时拒绝遥控，避免两个来源同时发速度。
发布在网页服务器的线程里进行；看门狗是 ROS 定时器，在 executor 线程里。共享状态用 self._lock 保护。
"""
import math
import threading
import time

from geometry_msgs.msg import Twist


def _clamp(x, lim):
    return max(-lim, min(lim, x))


class Teleop:

    def __init__(self, node, cfg, busy):
        """busy：无参函数，返回 True 表示现在不能遥控（例如自动航点在运行）。"""
        self.topic = cfg.get('topic', '/cmd_vel')
        self.max_v = float(cfg.get('max_v', 0.6))
        self.max_w = float(cfg.get('max_w', 1.2))
        # Recommended speed band shown on the web slider (display only; max_v is the hard limit)
        rec = cfg.get('recommended_v') or [0.5, min(3.0, self.max_v)]
        self.recommended_v = [float(rec[0]), min(float(rec[1]), self.max_v)]
        self.timeout = float(cfg.get('timeout', 0.5))
        self._busy = busy
        self._pub = node.create_publisher(Twist, self.topic, 10)
        self._lock = threading.Lock()
        self._active = False        # 正在发非零速度
        self._last = 0.0            # 最近一条指令的本机时刻
        self._owner = None          # 正在遥控的网页连接
        self._cmd = (0.0, 0.0)
        self._locked = False
        self._generation = 0
        node.create_timer(0.1, self._watchdog)

    def _publish(self, v, w):
        msg = Twist()
        msg.linear.x = v
        msg.angular.z = w
        self._pub.publish(msg)

    def command(self, v, w, owner):
        """执行一条遥控指令。返回 None 表示已执行，否则返回拒绝原因：teleop_locked、teleop_busy。"""
        try:
            v, w = float(v), float(w)
        except (TypeError, ValueError):
            return 'teleop_invalid'
        if not (math.isfinite(v) and math.isfinite(w)):
            return 'teleop_invalid'
        with self._lock:
            if self._locked:
                return 'teleop_locked'
            generation = self._generation
        if self._busy():
            return 'teleop_busy'
        v, w = _clamp(v, self.max_v), _clamp(w, self.max_w)
        with self._lock:
            # The busy check may race with estop. Check again when committing the command,
            # and serialize publication with lock/release/watchdog so an old command cannot
            # overtake a stop (or an old stop overtake a new command).
            if self._locked or generation != self._generation:
                return 'teleop_locked'
            self._cmd = (v, w)
            self._active = (v, w) != (0.0, 0.0)
            self._last = time.monotonic()
            self._owner = owner
            self._publish(v, w)
        return None

    def release(self, owner):
        """某个网页连接断开：如果正是它在遥控，立即停车。"""
        with self._lock:
            if owner != self._owner or not self._active:
                return
            self._active = False
            self._cmd = (0.0, 0.0)
            self._publish(0.0, 0.0)

    def lock(self):
        """急停时调用：锁定并立即发零速度；node.estop 继续发送零速度。"""
        with self._lock:
            self._locked = True
            self._generation += 1
            self._active = False
            self._cmd = (0.0, 0.0)
            self._publish(0.0, 0.0)

    def unlock(self):
        with self._lock:
            self._locked = False

    def _watchdog(self):
        with self._lock:
            expired = self._active and time.monotonic() - self._last > self.timeout
            if expired:
                self._active = False
                self._cmd = (0.0, 0.0)
                self._publish(0.0, 0.0)

    def status(self):
        with self._lock:
            v, w = self._cmd
            st = {'active': self._active, 'locked': self._locked, 'v': round(v, 3), 'w': round(w, 3),
                  'max_v': self.max_v, 'max_w': self.max_w, 'recommended_v': self.recommended_v}
        st['busy'] = bool(self._busy())
        return st
