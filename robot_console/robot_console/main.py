"""robot_console 入口：ROS2 节点在后台线程运行，网页服务器在主线程运行。

用法：
    ros2 run robot_console console                         # 用安装目录里的 config/console.yaml
    ros2 run robot_console console --config 某个/console.yaml
然后在 Windows 浏览器打开 http://localhost:8000（开发时用 http://localhost:5173）
按 Ctrl+C 退出，退出前会停掉从网页启动的所有进程。
"""
import argparse
import glob
import os
import signal
import socket
import sys
import threading
import time

from . import config


def _use_venv(venv):
    """把虚拟环境的 site-packages 加到 sys.path 最前面，不用激活虚拟环境也能 import fastapi。

    虚拟环境里只装了 fastapi、uvicorn 和它们的依赖；numpy、rclpy 等仍然用系统的。
    只影响本进程，从网页启动的子进程（ros2 launch 等）看不到这些包。
    """
    paths = glob.glob(os.path.join(venv, 'lib', 'python3*', 'site-packages'))
    if not paths:
        sys.exit(f'Python venv {venv} not found (server.venv in console.yaml). '
                 'Install it with: bash <robot_console>/install/install.sh')
    for p in paths:
        if p not in sys.path:
            sys.path.insert(1, p)


def _port_free(host, port):
    """端口没被占用（没有另一个控制台在运行）才返回 True。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _restart_command(argv=None, executable=None):
    """Build the command passed to os.execv; restores `-m robot_console.main` when launched via python -m."""
    exe = executable or sys.executable
    args = list(sys.argv if argv is None else argv)
    if not args or not args[0] or os.path.basename(args[0]) == 'main.py':
        return [exe, '-m', 'robot_console.main', *args[1:]]
    return [exe, *args]


def main():
    raw_argv = list(sys.argv)
    import rclpy
    from rclpy.utilities import remove_ros_args

    parser = argparse.ArgumentParser(description='机器人网页控制台后端')
    parser.add_argument('--config', help='console.yaml 的路径')
    args = parser.parse_args(remove_ros_args(raw_argv)[1:])
    cfg = config.load(args.config)
    _use_venv(cfg['server']['venv'])
    host, port = cfg['server']['host'], int(cfg['server']['port'])
    # 先确认没有另一个控制台在用这个端口：否则下面清理"残留进程"时会把它正在用的进程停掉
    if not _port_free(host, port):
        sys.exit(f'端口 {port} 已被占用：可能已经有一个控制台在运行，直接打开 http://localhost:{port} 即可')

    import uvicorn
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.signals import SignalHandlerOptions
    from .mapping import MappingData
    from .map_library import MapLibrary
    from .navigation import NavigationData, NavigationMaps
    from .process_manager import ProcessManager
    from .ros_node import ConsoleNode
    from .server import create_app
    from .teleop import Teleop

    # Ctrl+C 交给 uvicorn 处理（它会先关闭网页服务，再执行下面 finally 里的清理）
    rclpy.init(args=sys.argv, signal_handler_options=SignalHandlerOptions.NO)
    # 直接关掉终端窗口时进程收到的是 SIGHUP，默认会立刻退出、不做清理，
    # 从网页启动的仿真等进程（在单独的进程组里）就会留在后台。这里把它当成 Ctrl+C 处理
    signal.signal(signal.SIGHUP, lambda *_: os.kill(os.getpid(), signal.SIGINT))
    node = ConsoleNode(cfg)
    mapping = MappingData(node, cfg['mapping'])

    def waypoints_running():
        s = procs.get(mapping.stacks.get('waypoints'))
        return s is not None and s.state() == 'running'
    # 自动航点运行时不能遥控（procs 在下面创建，这个函数被调用时已经有了）
    teleop = Teleop(node, cfg['teleop'], busy=waypoints_running)
    # 单线程 executor：所有回调都很短，不需要多线程；/clock 每秒 1000 条，
    # MultiThreadedExecutor 每条回调都要经过线程池，开销大得多
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()

    procs = ProcessManager(cfg['stacks'], cfg['server']['log_dir'], node, f'.running_{port}.json')
    procs.set_exclusive(mapping.profiles)
    # Recording follows the actual child process, independently of browser polling. A stop hook may
    # still be processing GLIM's final trajectory after exit; it can update the already closed run.
    def slam_running(name):
        stack = procs.get(name)
        return stack is not None and stack.proc is not None and stack.proc.poll() is None
    mapping.is_running = slam_running
    node.create_timer(0.5, mapping.finish_if_stopped)
    cleaned = procs.cleanup_orphans()
    if cleaned:
        node.get_logger().warn(f'已停止上次残留的进程：{", ".join(cleaned)}')
    from .playback import Playback
    playback = Playback(cfg, node, procs, mapping)
    # Layered SLAM configuration: the console writes the parameter file of these stacks at every start
    from .playback import SENSOR_PRESET
    from .slam_config import SlamConfigs
    slam_configs = SlamConfigs(cfg['mapping'], mapping.profiles, SENSOR_PRESET, node.get_logger(),
                               config.Expander(cfg['paths']['data_root']))
    for slam_name in slam_configs.specs:
        if procs.get(slam_name) is not None:
            procs.on_start(slam_name, lambda extra, preset, n=slam_name: slam_configs.start_args(n, extra, preset))

    def session_info():
        """当前场景预设和实际执行的命令，写进运行记录（run.json）和地图的 meta.json。"""
        info = {}
        for key in ('sim', 'slam', 'waypoints'):
            s = procs.get(mapping.stacks.get(key))
            if s is not None and s.state() in ('running', 'stopping'):   # GLIM is 'stopping' while it saves
                info[f'{key}_cmd'] = ' '.join(s.run_cmd or s.cmd)
                if key == 'sim':
                    info['preset'] = s.preset
        info.update(playback.session_info())   # bag, when the SLAM run was fed from a bag
        slam_config = slam_configs.info(mapping.stacks.get('slam'))
        if slam_config and 'slam_cmd' in info:
            info['slam_config'] = slam_config      # the parameters this run uses and the layer of each
        return info
    mapping.run_info = session_info
    library = MapLibrary(cfg['mapping'], node, mapping, session_info, procs)
    # SLAMs that write their final result on exit (GLIM): after every stop, put the globally optimized
    # trajectory into the run records of that process (est.tum, online estimate kept as est_online.tum)
    from . import glim_dump
    for slam_name, prof in mapping.profiles.items():
        if prof['save_mode'] == 'dump':
            procs.on_stopped(slam_name, lambda s, p=prof: glim_dump.apply_to_runs(mapping, s, p))
    from .solid_models import SolidModels
    nav_maps = NavigationMaps(cfg['mapping'].get('maps_dir', '~/maps'), SolidModels(cfg['presets']))
    navdata = NavigationData(node)

    from .plugins import PluginManager
    plugins = PluginManager(cfg, node.get_logger())
    restart_req = threading.Event()
    server = None

    def request_restart():
        restart_req.set()
        if server is not None:
            server.should_exit = True

    app = create_app(cfg, node, procs, mapping, teleop, library, nav_maps, navdata, playback, slam_configs, plugins,
                     on_restart=request_restart)
    node.get_logger().info(f'网页控制台：http://localhost:{port}（监听 {host}）')
    # Uvicorn replays shutdown signals after restoring the original handlers. SIGTERM's
    # default action terminates Python immediately, skipping this finally block. Convert
    # that replay into SystemExit so process cleanup and the recording queue can finish.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level='warning',
                                           timeout_graceful_shutdown=3))
    try:
        server.run()
    finally:
        # 清理期间再按 Ctrl+C 或关窗口都不打断，否则进程会留在后台
        # （存大地图可能要十几秒，之前就是在这时又按了一次 Ctrl+C，FAST-LIO 留在了后台）
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        reason = 'restart' if restart_req.is_set() else 'estop'
        if restart_req.is_set():
            node.get_logger().info('正在重启控制台：等待正在保存的地图和后台任务，停止所有子进程……')
        else:
            node.get_logger().info('正在停止从网页启动的进程（存地图可能要十几秒，请不要关闭窗口）……')
        prepare = getattr(app.state, 'prepare_shutdown', None)
        if prepare is not None:
            prepare(reason)
        else:
            procs.close()
            library.wait_idle(timeout=25)
        plugins.shutdown()            # plugins stop their own threads before the console's processes go
        tasks = getattr(app.state, 'console_tasks', None)
        if tasks is not None:
            tasks.close(wait=True, terminate=True)
        procs.stop_all()
        executor.shutdown()
        mapping.close()               # 回调停止后，排空轨迹写盘队列并结束当前运行记录
        node.destroy_node()
        rclpy.try_shutdown()

    if restart_req.is_set():
        for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
        # Wait briefly for the listening socket to release before the fresh process binds it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not _port_free(host, port):
            time.sleep(0.05)
        cmd = _restart_command(raw_argv, sys.executable)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(cmd[0], cmd)


if __name__ == '__main__':
    main()
