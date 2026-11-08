"""进程管理：启动、停止 stacks.yaml 里列出的命令（仿真、建图、导航等）。

之前写 shell 脚本时踩过的坑，在这里统一处理：
  - 每个进程放在单独的进程组里（start_new_session），停止时对整个组发信号。
    只结束 ros2 launch 外壳会留下残余节点（之前多出过一个 static_transform_publisher）。
  - 标准输入接 /dev/null，避免后台进程读终端时收到 SIGTTIN 被挂起。
  - 停止前可以先调用服务（FAST-LIO 的 /map_save），保证地图在退出前存好。
  - 正在运行的进程组记在 <log_dir>/.running_<端口>.json 里（每个端口一个，测试用的控制台互不影响）。
    控制台如果没正常退出（窗口被强行关掉、退出时又按了一次 Ctrl+C），这些进程会留在后台；
    下次启动时按记录把它们停掉。
    2026-11-04 就出过这个问题：上次残留的 FAST-LIO 和新启动的同时发布 /Odometry，建图页的地图来回跳。
"""
import json
import os
import signal
import subprocess
import threading
import time
from collections import deque


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)          # 信号 0 不做任何事，只检查进程组里还有没有进程
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_group(pgid, finished, result=None, sigint_wait=10):
    """SIGINT → SIGTERM → SIGKILL，每一步都发给整个进程组，直到 finished() 为真。
    返回没能按时退出的记录 [{'code': 'signal_timeout', ...}]。
    result (dict, optional) receives 'stage': the last signal sent ('sigint' / 'sigterm' / 'sigkill'),
    or None when the group was already gone before the first signal.
    sigint_wait: seconds to wait after SIGINT (stacks.yaml stop_timeout; GLIM optimizes and writes its map then)."""
    msgs = []
    for sig, wait in ((signal.SIGINT, sigint_wait), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        if result is not None:
            result['stage'] = signal.Signals(sig).name.lower()
        deadline = time.time() + wait
        while time.time() < deadline:
            if finished():
                break
            time.sleep(0.2)
        else:
            msgs.append({'code': 'signal_timeout',
                         'params': {'signal': signal.Signals(sig).name, 'wait': wait}})
            continue
        break
    return msgs


def _is_our_group(pgid, cmd):
    """进程组 pgid 是不是记录里的那个命令启动的（防止进程号被别的程序重用后误杀）。

    组长还在：它的命令行末尾必须和记录的命令一致（ros2 是 Python 脚本，实际命令行前面多了解释器）。
    组长已经退出、组里还有进程：Linux 不会把仍在使用的进程组号分配给新进程，所以一定是原来那一组。
    """
    try:
        with open(f'/proc/{pgid}/cmdline', 'rb') as f:
            actual = f.read().decode(errors='replace').split('\0')[:-1]
    except OSError:
        return True
    tail = cmd[1:]
    return (not tail or actual[-len(tail):] == tail) and \
        any(os.path.basename(a) == os.path.basename(cmd[0]) for a in actual)


def merge_args(cmd, extra):
    """在命令后面加参数（来自场景预设）。name:=value 形式的参数替换命令里同名的那一个，其余追加在末尾。"""
    cmd = list(cmd)
    for arg in extra or []:
        key = arg.split(':=', 1)[0] + ':=' if ':=' in arg and not arg.startswith('-') else None
        hit = next((i for i, a in enumerate(cmd) if key and a.startswith(key)), None)
        if hit is None:
            cmd.append(arg)
        else:
            cmd[hit] = arg
    return cmd


class Stack:
    """一个可以从网页启停的进程。"""

    def __init__(self, name, spec):
        self.name = name
        self.label = spec.get('label', name)
        self.description = spec.get('description', '')
        self.cmd = list(spec['cmd'])
        self.run_cmd = None         # 最近一次实际执行的命令（加上了预设参数）
        self.preset = None          # 最近一次启动时用的场景预设
        self.origin = None          # 从哪个页面启动的（mapping / navigation / monitor），导航页用它判断小车是否刚出生
        self.before_stop = spec.get('before_stop')
        self.stop_timeout = float(spec.get('stop_timeout', 10))   # s to wait after SIGINT before SIGTERM
        self.stop_on_estop = bool(spec.get('stop_on_estop', False))
        self.page_only = bool(spec.get('page_only', False))   # started only by its page (e.g. bag playback)
        self.proc = None
        self.started_at = None      # 本机时间戳（秒）
        self.exit_code = None
        self.log_path = None
        self.stopping = False
        # 最近一次停止时的结果（例如存图结果），每条是 {'code': ..., 'params': {...}}，由网页翻译成当前语言
        self.messages = []
        # Last stop requested from the console for the current run: {'duration', 'stage', 'exit_code'}.
        # stage 'sigint' is a normal stop (exit codes like -2 or 1 are expected then); 'sigterm' / 'sigkill'
        # mean the process had to be forced.
        self.last_stop = None

    def state(self):
        if self.proc is None:
            return 'idle'
        if self.proc.poll() is None:
            return 'stopping' if self.stopping else 'running'
        self.exit_code = self.proc.returncode
        return 'stopping' if self.stopping else 'exited'

    def info(self):
        st = self.state()
        return {
            'name': self.name, 'label': self.label, 'description': self.description,
            'cmd': ' '.join(self.run_cmd or self.cmd), 'state': st, 'preset': self.preset, 'origin': self.origin,
            'started_at': self.started_at,
            'uptime': round(time.time() - self.started_at, 1) if st in ('running', 'stopping') else None,
            'exit_code': self.exit_code if st == 'exited' else None,
            'messages': list(self.messages),
            'last_stop': dict(self.last_stop) if self.last_stop else None,
            'page_only': self.page_only,
        }


class StackRunning(Exception):
    """进程已经在运行，不能重复启动。"""


class ProcessManager:

    def __init__(self, stacks_cfg, log_dir, node, state_file='.running.json'):
        self._stacks = {n: Stack(n, s) for n, s in stacks_cfg.items()}
        self._log_dir = log_dir
        self._node = node           # 用来调用 before_stop 里的服务
        self._lock = threading.Lock()
        self._after_stop = {}       # stack name -> [fn(stack)], run in stop() after the process group is gone
        os.makedirs(log_dir, exist_ok=True)
        self._state_path = os.path.join(log_dir, state_file)

    def on_stopped(self, name, fn):
        """Call fn(stack) after a console-initiated stop of `name` has finished (e.g. collect GLIM's dump)."""
        self._after_stop.setdefault(name, []).append(fn)

    def get(self, name):
        return self._stacks.get(name)

    def status(self):
        with self._lock:
            return [s.info() for s in self._stacks.values()]

    # ---------------- 启动 ----------------

    def start(self, name, extra_args=None, preset=None, origin=None):
        s = self._stacks[name]
        with self._lock:
            if s.state() in ('running', 'stopping'):
                raise StackRunning(name)
            stamp = time.strftime('%Y%m%d_%H%M%S')
            s.log_path = os.path.join(self._log_dir, f'{name}_{stamp}.log')
            env = dict(os.environ, RCUTILS_COLORIZED_OUTPUT='0', PYTHONUNBUFFERED='1')
            cmd = merge_args(s.cmd, extra_args)
            with open(s.log_path, 'wb') as log:
                s.proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True, env=env)
            s.run_cmd = cmd
            s.preset = preset
            s.origin = origin
            s.started_at = time.time()
            s.exit_code = None
            s.stopping = False
            s.last_stop = None
            s.messages = []
            self._save_state()
        return s.info()

    # ---------------- 停止 ----------------

    def stop(self, name):
        """停止一个进程（阻塞，最长约 30 s）。网页服务器在单独的线程里调用它。"""
        s = self._stacks[name]
        with self._lock:
            if s.state() != 'running':
                return s.info()
            s.stopping = True
        msgs = []
        t0 = time.monotonic()
        result = {'stage': None}
        try:
            # 1. 停止前的服务调用（例如存地图）
            if s.before_stop:
                msgs.append(self._node.call_trigger(
                    s.before_stop['service'], float(s.before_stop.get('timeout', 15))))

            # 2. SIGINT → SIGTERM → SIGKILL，每一步都发给整个进程组
            pgid = s.proc.pid       # start_new_session=True 时进程组号就是它的 pid
            msgs += _signal_group(pgid, lambda: s.proc.poll() is not None and not _group_alive(pgid), result,
                                  s.stop_timeout)
            try:
                s.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                msgs.append({'code': 'not_exited', 'params': {}})
            for fn in self._after_stop.get(name, []):
                try:
                    m = fn(s)
                    if m:
                        msgs.append(m)
                except Exception as e:      # a failing hook must not leave the stack in 'stopping'
                    msgs.append({'code': 'after_stop_failed', 'params': {'reason': repr(e)}})
        finally:
            with self._lock:
                s.stopping = False
                s.messages = msgs
                s.last_stop = {'duration': round(time.monotonic() - t0, 2), 'stage': result['stage'],
                               'exit_code': s.proc.poll(), 'at': time.time()}
                self._save_state()
        return s.info()

    def stop_all(self):
        """控制台退出时停掉所有还在运行的进程。"""
        threads = [threading.Thread(target=self.stop, args=(n,))
                   for n, s in self._stacks.items() if s.state() == 'running']
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Stops started from the web page run in daemon threads; if the console exits while one is still
        # escalating SIGINT -> SIGTERM -> SIGKILL, that thread dies and leaves the process group behind
        # (seen with Nav2's component container, which can hang on SIGINT). Wait for them to finish.
        deadline = time.time() + 40
        while time.time() < deadline and any(s.stopping for s in self._stacks.values()):
            time.sleep(0.2)

    # ---------------- 残留进程 ----------------

    def _save_state(self):
        """把正在运行的进程组写进状态文件（调用者持有锁）。"""
        data = {n: {'pgid': s.proc.pid, 'cmd': s.run_cmd, 'started_at': s.started_at}
                for n, s in self._stacks.items() if s.proc is not None and s.proc.poll() is None}
        tmp = self._state_path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, self._state_path)
        except OSError as e:
            self._node.get_logger().warn(f'写不了 {self._state_path}：{e}')

    def cleanup_orphans(self):
        """停掉上次控制台留在后台的进程（按状态文件记录，只动自己启动过的进程组）。返回停掉的进程名。"""
        try:
            with open(self._state_path, encoding='utf-8') as f:
                old = json.load(f)
        except (OSError, ValueError):
            return []
        log = self._node.get_logger()
        cleaned = []
        for name, rec in old.items():
            pgid, cmd = int(rec['pgid']), rec.get('cmd') or []
            if not _group_alive(pgid) or not cmd or not _is_our_group(pgid, cmd):
                continue
            log.warn(f'发现上次没有退出的进程 {name}（进程组 {pgid}），正在停止……')
            _signal_group(pgid, lambda: not _group_alive(pgid))
            cleaned.append(name)
        with self._lock:
            self._save_state()
        return cleaned

    def estop_names(self):
        return [n for n, s in self._stacks.items() if s.stop_on_estop and s.state() == 'running']

    # ---------------- 日志 ----------------

    def log_tail(self, name, lines=200):
        s = self._stacks[name]
        if not s.log_path or not os.path.exists(s.log_path):
            return ''
        with open(s.log_path, encoding='utf-8', errors='replace') as f:
            return ''.join(deque(f, maxlen=lines))

    def log_end(self, name, nbytes=16384):
        """日志最后 nbytes 字节（不读整个文件，适合每秒调用几次）。"""
        s = self._stacks.get(name)
        if s is None or not s.log_path:
            return ''
        try:
            with open(s.log_path, 'rb') as f:
                f.seek(max(0, os.path.getsize(s.log_path) - nbytes))
                return f.read().decode('utf-8', errors='replace')
        except OSError:
            return ''
