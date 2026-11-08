"""网页服务器：REST 接口（命令）+ WebSocket（持续推送状态）+ 前端静态文件。"""
import asyncio
import json
import math
import os
import re
import threading
import time

import psutil
import yaml
from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .map_library import NAME_RE, SaveError
from .nav_speed import write_console_params
from .navigation import NavigationError
from .nav_mission import MAX_GOALS, NavMission, map_origin
from .solid_models import asset_path as solid_asset_path
from .process_manager import StackRunning
from .scene_import import SceneLibrary
from .upload_routes import register_import_routes
from .map_catalog import register_map_routes
from .evaluation import register_evaluation_routes
from .batch import register_batch_routes
from .run_trash import register_run_trash_routes
from .map_edit import register_map_edit_routes
from .playback import register_playback_routes
from .bag_files import register_bag_file_routes
from .open_folder import register_open_folder_routes
from .map_scene import register_map_scene_routes

STREAMS = {'mapping'}       # 网页可以订阅的数据流；不订阅就不发（监控页不需要点云）
STREAM_PERIOD = 0.2         # 订阅的数据流多久推送一次（s）
WAYPOINT_RE = re.compile(r'到达航点 (\d+)/(\d+)')
TOTAL_RE = re.compile(r'共 (\d+) 个航点')


class WebFiles(StaticFiles):
    """Built web page. index.html must be revalidated on every load: it names the hashed JS/CSS bundles, and a
    cached copy kept the old page (without new panels) after the console was updated and restarted."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.media_type == 'text/html' or response.headers.get('content-type', '').startswith('text/html'):
            response.headers['Cache-Control'] = 'no-cache'
        return response


def create_app(cfg, node, procs, mapping, teleop, library, nav_maps, navdata, playback):
    app = FastAPI(title='robot_console')
    period = 1.0 / float(cfg['server'].get('status_rate', 2.0))
    psutil.cpu_percent()            # 第一次调用只是开始计时，返回值无意义

    def shown(path):
        """Path for display on the page: the home directory written as ~."""
        home = os.path.expanduser('~')
        return '~' + path[len(home):] if path == home or path.startswith(home + '/') else path
    # Data folders shown on the pages (they come from the configuration, so the page must not hard-code them)
    paths_shown = {'maps': shown(cfg['mapping']['maps_dir']), 'runs': shown(cfg['mapping']['runs_dir']),
                   'results': shown(cfg['evaluation']['results_dir'])}

    def status():
        snap = node.snapshot()
        snap['type'] = 'status'
        snap['time'] = time.time()
        snap['stacks'] = procs.status()
        snap['resources'] = {'cpu': psutil.cpu_percent(), 'mem': psutil.virtual_memory().percent}
        snap['paths'] = paths_shown
        return snap

    def error(status, code, **params):
        """接口错误：只返回错误代码和参数，文字由网页按当前语言生成（见 web/src/locales）。"""
        return HTTPException(status, detail={'code': code, 'params': params})

    def stack_or_404(name):
        s = procs.get(name)
        if s is None:
            raise error(404, 'stack_not_found', name=name)
        return s

    # ---------------- 场景预设 ----------------

    presets = cfg['presets']
    scenes = SceneLibrary(os.path.expanduser(cfg['mapping'].get('scenes_dir', '~/console_scenes')), presets)
    scenes.load()
    register_import_routes(app, cfg, library, scenes, presets, procs)
    register_map_routes(app, cfg, nav_maps.solids, library, procs)
    evaluation = register_evaluation_routes(app, cfg, mapping)
    batch = register_batch_routes(app, cfg, node, procs, mapping, library, evaluation)
    register_run_trash_routes(app, cfg, mapping, batch)
    register_map_edit_routes(app, cfg)
    register_playback_routes(app, playback, procs)
    register_bag_file_routes(app, cfg, playback)
    register_open_folder_routes(app, cfg)
    sim_name = mapping.stacks.get('sim')

    def preset_args(name, preset):
        """决定这次启动用哪个预设、要追加哪些参数。

        - 网页指定了预设：用它（必须是 mapping_presets.yaml 里有的）
        - 没指定、启动的是仿真：用默认预设
        - 没指定、启动的是别的进程：沿用正在运行的仿真的预设（建图、航点要和场景配套）
        """
        if preset is None:
            if name == sim_name:
                preset = cfg['mapping'].get('default_preset')
            else:
                sim = procs.get(sim_name)
                preset = sim.preset if sim is not None and sim.state() == 'running' else None
        if preset is None:
            return None, []
        if preset not in presets:
            raise error(404, 'preset_not_found', name=preset)
        if name == mapping.stacks.get('waypoints'):
            sim = procs.get(sim_name)
            live_preset = sim.preset if sim is not None and sim.state() == 'running' else preset
            if presets[preset].get('waypoints') is False or presets.get(live_preset, {}).get('waypoints') is False:
                raise error(409, 'import_waypoints_unavailable')
        return preset, presets[preset]['args'].get(name, [])

    def waypoint_progress():
        """从航点进程的日志里读进度："到达航点 3/10"、"超时"（waypoint_driver 打印的中文日志）。"""
        wp = mapping.stacks.get('waypoints')
        s = procs.get(wp)
        if s is None or s.log_path is None:
            return None
        text = procs.log_end(wp)
        hits = WAYPOINT_RE.findall(text)
        total = TOTAL_RE.findall(text)
        done, n = (int(hits[-1][0]), int(hits[-1][1])) if hits else (0, int(total[-1]) if total else None)
        return {'done': done, 'total': n, 'timeout': '超时' in text, 'state': s.state(),
                'exit_code': s.exit_code}

    @app.get('/api/mapping/presets')
    def get_presets():
        return [{'name': n, 'label': p['label'], 'description': p['description'],
                 'custom': p.get('custom', False), 'waypoints': p.get('waypoints', True),
                 'spawn': p.get('spawn')} for n, p in list(presets.items())]

    # ---------------- REST ----------------

    @app.get('/api/status')
    def get_status():
        return status()

    @app.get('/api/stacks')
    def get_stacks():
        return procs.status()

    @app.post('/api/stacks/{name}/start')
    def start_stack(name: str, preset: str | None = None, origin: str | None = None):
        s = stack_or_404(name)
        if s.page_only:
            raise error(409, 'stack_page_only', name=name)
        # the simulation and bag playback both drive /clock and the SLAM inputs: only one at a time
        if name in playback.sim_stacks and playback.player_active():
            raise error(409, 'playback_running', name=playback.stack)
        preset, extra = preset_args(name, preset)
        if name == nav_name:
            extra = [*extra, *prepare_nav_params()]   # same speed-multiplier setup as the navigation page
        if origin not in ('mapping', 'navigation', 'playback'):
            origin = 'monitor'
        is_slam = name in mapping.profiles
        if is_slam:
            # one SLAM at a time: two would share the CPU and the mapping page shows only one
            other = next((n for n in mapping.profiles if n != name and procs.get(n)
                          and procs.get(n).state() in ('running', 'stopping')), None)
            if other:
                raise error(409, 'stack_running', name=other)
        try:
            info = procs.start(name, extra, preset, origin)
            if is_slam:
                mapping.select(name)
                mapping.reset()       # 每次开始建图都从空地图开始
            return info
        except StackRunning:
            raise error(409, 'stack_running', name=name)
        except OSError as e:          # 命令不存在等
            raise error(500, 'start_failed', name=name, reason=str(e))

    @app.post('/api/stacks/{name}/stop')
    def stop_stack(name: str):
        # 停止可能要几十秒（等存图、等进程退出），放到后台线程，立即返回；
        # 网页通过 WebSocket 推送的状态看到 stopping → exited
        s = stack_or_404(name)
        threading.Thread(target=procs.stop, args=(name,), daemon=True).start()
        info = s.info()
        if info['state'] == 'running':
            info['state'] = 'stopping'
        return info

    @app.get('/api/stacks/{name}/log')
    def stack_log(name: str, lines: int = 200):
        stack_or_404(name)
        return {'name': name, 'text': procs.log_tail(name, max(1, min(lines, 2000)))}

    @app.post('/api/estop')
    def estop():
        batch.abort('estop')          # a running batch stops starting new processes and cleans up
        cancelled = node.estop()
        teleop.lock()                 # 急停后遥控锁定，网页上解除后才能再遥控
        stopped = procs.estop_names()
        for n in stopped:
            threading.Thread(target=procs.stop, args=(n,), daemon=True).start()
        return {'cancelled_actions': cancelled, 'stopped_stacks': stopped}

    @app.post('/api/teleop/unlock')
    def teleop_unlock():
        teleop.unlock()
        return teleop.status()

    # ---------------- 地图库 ----------------

    @app.post('/api/mapping/save')
    def save_map(name: str):
        # 保存在后台进行（存大地图要十几秒），进度在建图状态的 save 字段里
        try:
            library.start_save(name)
        except SaveError as e:
            raise error(409 if e.code in ('map_exists', 'save_busy') else 400, e.code, **e.params)
        return library.status()

    @app.get('/api/maps/{name}/preview.png')
    def map_preview(name: str):
        if not NAME_RE.match(name):
            raise error(400, 'invalid_map_name', name=name)
        path = os.path.join(library.map_dir(name), 'figures', '2d', 'map_preview.png')
        if not os.path.exists(path):
            path = os.path.join(library.map_dir(name), 'map_preview.png')  # 兼容旧地图
        if not os.path.exists(path):
            raise error(404, 'map_not_found', name=name)
        return FileResponse(path, media_type='image/png')

    # ---------------- 导航（3a：选地图、启停、只读可视化） ----------------

    nav_cfg = cfg.get('navigation') or {}
    nav_name = nav_cfg.get('stack', 'navigation')
    nav_epoch = {'started_at': None}
    register_map_scene_routes(app, cfg, nav_maps, scenes, presets, procs, nav_name)

    # ---- Speed multiplier (see nav_speed.py): Nav2 runs with a derived params file whose limits are the top
    # speed, and the console publishes /speed_limit = 1x speed x multiplier (continuously, see NavigationData).
    cap_w = float(nav_cfg.get('max_w_cap', 2.5))
    scale_lo, scale_hi = [float(v) for v in nav_cfg.get('speed_scale_range', [0.5, 4.0])]
    nav_params_src = os.path.expanduser(nav_cfg.get('params_file') or os.path.join(
        get_package_share_directory('lidar_nav'), 'config', 'nav2_params_sim.yaml'))
    nav_params = os.path.join(cfg['server']['log_dir'], 'nav2_params_console.yaml')
    nav_speed = {'scale': 1.0, 'base_v': None, 'base_w': None, 'error': None}

    # ---- Nav2 parameter variants (3b): nav2_params_<variant>.yaml in lidar_nav/config, generated by
    # tools/make_nav2_params.py (e.g. amcl_both). The page may only pick from this list.
    variants_dir = os.path.join(get_package_share_directory('lidar_nav'), 'config')

    def variant_name(path):
        stem = os.path.splitext(os.path.basename(path))[0]
        return stem[len('nav2_params_'):] if stem.startswith('nav2_params_') else stem

    def nav_variants():
        found = {}
        try:
            for f in sorted(os.listdir(variants_dir)):
                if f.startswith('nav2_params_') and f.endswith('.yaml'):
                    found[variant_name(f)] = os.path.join(variants_dir, f)
        except OSError:
            pass
        found.setdefault(variant_name(nav_params_src), nav_params_src)
        return found
    default_variant = variant_name(nav_params_src)
    nav_run = {'variant': default_variant, 'params_file': nav_params_src}   # what the running Nav2 uses

    def prepare_nav_params(src=nav_params_src):
        """(Re)write the derived params file; returns the launch argument, or [] when it cannot be made."""
        try:
            nav_speed['base_v'], nav_speed['base_w'] = write_console_params(
                src, nav_params, scale_hi, cap_w)
            nav_speed['error'] = None
            navdata.set_speed_limit(nav_speed['base_v'] * nav_speed['scale'])
            return [f'params_file:={nav_params}']
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
            # Fall back to Nav2's own params: navigation still works, only the multiplier is unavailable
            nav_speed['error'] = f'{src}: {e!r}'
            navdata.set_speed_limit(None)
            return [f'params_file:={src}']

    def speed_status(running):
        bv, bw = nav_speed['base_v'], nav_speed['base_w']
        k = nav_speed['scale']
        return {'scale': k, 'max_v': round(bv * k, 3) if bv else None,
                'max_w': round(min(bw * k, cap_w), 3) if bw else None, 'range': [scale_lo, scale_hi],
                'applied': bool(running and bv and navdata.speed_limit_listeners() > 0),
                'error': nav_speed['error']}

    prepare_nav_params()

    def nav_map_name(s):
        """Active map name parsed from the actual launch command (also works when started from Monitor)."""
        cmd = s.run_cmd or s.cmd
        arg = next((a for a in cmd if a.startswith('map:=')), None)
        path = arg.split(':=', 1)[1] if arg else nav_cfg.get('default_map', '')
        base = os.path.basename(path)
        return os.path.basename(os.path.dirname(path)) if base == 'map.yaml' else os.path.splitext(base)[0]

    def nav_error(e):
        return error(404 if e.code == 'map_not_found' else 400, e.code, **e.params)

    @app.get('/api/navigation/maps')
    def navigation_maps():
        return nav_maps.list()

    @app.get('/api/navigation/maps/{name}/grid.png')
    def navigation_grid(name: str):
        try:
            return Response(nav_maps.png(name), media_type='image/png', headers={'Cache-Control': 'no-store'})
        except NavigationError as e:
            raise nav_error(e)

    @app.get('/api/navigation/maps/{name}/cloud')
    def navigation_cloud(name: str):
        try:
            data = nav_maps.cloud(name)
        except NavigationError as e:
            raise nav_error(e)
        except (OSError, ValueError, KeyError, AssertionError) as e:
            raise error(400, 'navigation_map_invalid', name=name, reason=str(e))
        if not data:
            raise error(404, 'navigation_cloud_missing', name=name)
        return Response(data, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    @app.get('/api/navigation/maps/{name}/solid')
    def navigation_solid(name: str):
        try:
            asset = nav_maps.assets(name)
        except NavigationError as e:
            raise nav_error(e)
        data = nav_maps.solids.public(asset['preset']) if nav_maps.solids and asset['has_solid'] else None
        if data is None:
            raise error(404, 'navigation_solid_missing', name=name)
        return data

    # Mesh files and their textures (Collada files reference textures by relative path, so the URL
    # keeps the model's own directory layout). Only known file types inside sim_robot/models are served.
    @app.get('/api/navigation/solid/{model}/{path:path}')
    def navigation_solid_asset(model: str, path: str):
        p = solid_asset_path(model, path)
        if p is None:
            raise error(404, 'navigation_solid_missing', name=model)
        media = {'.dae': 'model/vnd.collada+xml', '.stl': 'model/stl', '.png': 'image/png'}.get(p.suffix.lower(),
                                                                                                'image/jpeg')
        return FileResponse(p, media_type=media)

    @app.get('/api/navigation/variants')
    def navigation_variants():
        return {'variants': sorted(nav_variants()), 'default': default_variant}

    @app.post('/api/navigation/start')
    def navigation_start(name: str, variant: str | None = None):
        s = stack_or_404(nav_name)
        try:
            asset = nav_maps.assets(name)
        except NavigationError as e:
            raise nav_error(e)
        variant = variant or default_variant
        src = nav_variants().get(variant)
        if src is None:
            raise error(400, 'navigation_variant_unknown', name=variant)
        sim = procs.get(sim_name)
        if sim is None or sim.state() != 'running':
            raise error(409, 'navigation_needs_sim')
        if asset['preset'] and sim.preset and asset['preset'] != sim.preset:
            raise error(409, 'navigation_scene_mismatch', name=name, map=asset['preset'], sim=sim.preset)
        if s.state() in ('running', 'stopping'):
            raise error(409, 'stack_running', name=s.name)
        try:
            info = procs.start(nav_name, [f"map:={asset['_yaml']}", *prepare_nav_params(src)], sim.preset)
        except StackRunning:
            raise error(409, 'stack_running', name=s.name)
        except OSError as e:
            raise error(500, 'start_failed', name=s.name, reason=str(e))
        nav_run.update(variant=variant, params_file=src)
        return info

    def running_variant(s):
        """(variant, params file) of the running Nav2, also when it was started from the Monitor page."""
        cmd = s.run_cmd or s.cmd
        arg = next((a for a in cmd if a.startswith('params_file:=')), None)
        if arg is None:
            return 'launch_default', None
        path = arg.split(':=', 1)[1]
        if os.path.abspath(os.path.expanduser(path)) == os.path.abspath(nav_params):
            return nav_run['variant'], nav_run['params_file']
        return variant_name(path), path

    @app.post('/api/navigation/stop')
    def navigation_stop():
        mission.stop()
        return stop_stack(nav_name)

    # ---- Simulation switch of the navigation page. There is only one Gazebo, shared with the mapping page;
    # starting it here uses the scene recorded in the map, so the robot spawns where the map origin is
    # (AMCL's default initial pose). A simulation started elsewhere may have a robot that already moved.
    nav_sim_job = {'state': 'idle', 'error': None}     # idle / stopping / restarting

    def nav_sim_preset(name):
        try:
            preset = nav_maps.assets(name)['preset']
        except NavigationError as e:
            raise nav_error(e)
        if not preset or preset not in presets:
            raise error(409, 'navigation_sim_no_scene', name=name)
        return preset

    def start_nav_sim(preset):
        _, extra = preset_args(sim_name, preset)
        return procs.start(sim_name, extra, preset, 'navigation')

    def run_sim_job(kind, preset=None):
        """Background: stop navigation and the simulation (and start the simulation again for a restart)."""
        try:
            procs.stop(nav_name)               # Nav2 without a simulation only produces TF errors
            procs.stop(sim_name)
            if kind == 'restarting':
                start_nav_sim(preset)
            nav_sim_job.update(state='idle', error=None)
        except Exception as e:                  # noqa: BLE001 - shown on the page instead of a stuck state
            nav_sim_job.update(state='idle', error=repr(e))

    def launch_sim_job(kind, preset=None):
        if nav_sim_job['state'] != 'idle':
            raise error(409, 'navigation_sim_busy')
        nav_sim_job.update(state=kind, error=None)
        threading.Thread(target=run_sim_job, args=(kind, preset), daemon=True).start()
        return dict(nav_sim_job)

    @app.post('/api/navigation/sim/start')
    def navigation_sim_start(name: str):
        preset = nav_sim_preset(name)
        if nav_sim_job['state'] != 'idle':
            raise error(409, 'navigation_sim_busy')
        try:
            return start_nav_sim(preset)
        except StackRunning:
            raise error(409, 'stack_running', name=sim_name)
        except OSError as e:
            raise error(500, 'start_failed', name=sim_name, reason=str(e))

    @app.post('/api/navigation/sim/stop')
    def navigation_sim_stop():
        return launch_sim_job('stopping')

    @app.post('/api/navigation/sim/restart')
    def navigation_sim_restart(name: str):
        return launch_sim_job('restarting', nav_sim_preset(name))

    def inside(g, x, y):
        """Point inside a map grid (origin may be rotated)."""
        ox, oy, oyaw = g['origin']
        dx, dy = x - ox, y - oy
        u = dx * math.cos(oyaw) + dy * math.sin(oyaw)
        v = -dx * math.sin(oyaw) + dy * math.cos(oyaw)
        return 0 <= u <= g['width'] * g['resolution'] and 0 <= v <= g['height'] * g['resolution']

    def checked_pose(x, y, yaw):
        """Pose clicked on the map: navigation must be running and the point inside the active map."""
        s = stack_or_404(nav_name)
        if s.state() != 'running':
            raise error(409, 'navigation_not_running')
        if not all(math.isfinite(v) for v in (x, y, yaw)):
            raise error(400, 'navigation_pose_invalid')
        try:
            g = nav_maps.assets(nav_map_name(s))['grid']
        except NavigationError as e:
            raise nav_error(e)
        if not inside(g, x, y):
            raise error(400, 'navigation_pose_outside', x=round(x, 2), y=round(y, 2))
        return x, y, math.atan2(math.sin(yaw), math.cos(yaw))

    @app.post('/api/navigation/initial_pose')
    def navigation_initial_pose(x: float, y: float, yaw: float):
        x, y, yaw = checked_pose(x, y, yaw)
        navdata.set_initial_pose(x, y, yaw)
        return {'x': x, 'y': y, 'yaw': yaw}

    # ---- Missions (3b): every goal, single or a waypoint list, runs as a mission with a run record
    mission = NavMission(node, navdata, {**nav_cfg, 'runs_dir': cfg['mapping'].get('runs_dir', '~/runs'),
                                         'gt_topic': cfg['mapping'].get('gt_topic')})

    def parse_goals(body, g):
        """[(x, y, yaw)] from {"goals": [{x, y, yaw}, ...]}, every point finite and inside grid g."""
        goals = body.get('goals') if isinstance(body, dict) else None
        if not isinstance(goals, list) or not 1 <= len(goals) <= MAX_GOALS:
            raise error(400, 'navigation_goals_invalid', max=MAX_GOALS)
        out = []
        for i, p in enumerate(goals, 1):
            try:
                x, y, yaw = (float(p[k]) for k in ('x', 'y', 'yaw'))
            except (TypeError, KeyError, ValueError):
                raise error(400, 'navigation_goals_invalid', max=MAX_GOALS)
            if not all(math.isfinite(v) for v in (x, y, yaw)):
                raise error(400, 'navigation_pose_invalid')
            if not inside(g, x, y):
                raise error(400, 'navigation_pose_outside', x=round(x, 2), y=round(y, 2))
            out.append((round(x, 3), round(y, 3), round(math.atan2(math.sin(yaw), math.cos(yaw)), 4)))
        return out

    def map_meta(asset):
        p = asset['_yaml'].parent / 'meta.json'
        if asset['_yaml'].name != 'map.yaml' or not p.is_file():
            return {}
        try:
            meta = json.loads(p.read_text(encoding='utf-8'))
            return meta if isinstance(meta, dict) else {}
        except (OSError, ValueError):
            return {}

    def sim_cmd():
        sim = procs.get(sim_name)
        return (sim.run_cmd or sim.cmd) if sim is not None else None

    def sync_nav_epoch(s):
        """A new or stopped Nav2 run: drop scans/paths/TF/costmaps and close the mission of the earlier run."""
        active = s.state() in ('running', 'stopping')
        if active and nav_epoch['started_at'] != s.started_at:
            nav_epoch['started_at'] = s.started_at
            mission.stop()
            navdata.reset(enabled=True)
        elif not active and nav_epoch['started_at'] is not None:
            nav_epoch['started_at'] = None
            mission.stop()
            navdata.reset(enabled=False)

    def run_mission(goals):
        s = stack_or_404(nav_name)
        sync_nav_epoch(s)
        wp = procs.get(mapping.stacks.get('waypoints'))
        if wp is not None and wp.state() == 'running':
            raise error(409, 'navigation_waypoints_running')   # both would drive /cmd_vel
        if not navdata.action.server_is_ready():
            raise error(409, 'navigation_not_ready')
        name = nav_map_name(s)
        try:
            asset = nav_maps.assets(name)
        except NavigationError as e:
            raise nav_error(e)
        origin, source = map_origin(map_meta(asset), cfg['mapping'].get('runs_dir', '~/runs'), sim_cmd())
        variant, params_file = running_variant(s)
        sim = procs.get(sim_name)
        ctx = {'map': name, 'map_yaml': str(asset['_yaml']), 'preset': sim.preset if sim else None,
               'variant': variant, 'params_file': params_file, 'nav_cmd': ' '.join(s.run_cmd or s.cmd),
               'sim_cmd': ' '.join(sim_cmd() or []), 'origin': origin, 'origin_source': source}
        try:
            return mission.start(goals, ctx, lambda: nav_speed['scale'])
        except OSError as e:
            raise error(500, 'navigation_record_failed', reason=str(e))

    @app.post('/api/navigation/goal')
    def navigation_goal(x: float, y: float, yaw: float):
        x, y, yaw = checked_pose(x, y, yaw)
        return run_mission([(x, y, yaw)])

    @app.post('/api/navigation/mission')
    def navigation_mission(body: dict):
        s = stack_or_404(nav_name)
        if s.state() != 'running':
            raise error(409, 'navigation_not_running')
        try:
            g = nav_maps.assets(nav_map_name(s))['grid']
        except NavigationError as e:
            raise nav_error(e)
        return run_mission(parse_goals(body, g))

    # ---- Waypoint lists saved next to the map: ~/maps/<name>/goals.yaml. ROS parameter format, so
    # `ros2 run lidar_nav nav_goals --ros-args --params-file goals.yaml` runs the same list in batch experiments.
    def goals_path(name):
        try:
            path = nav_maps.resolve(name)
        except NavigationError as e:
            raise nav_error(e)
        if path.name != 'map.yaml':
            raise error(409, 'navigation_goals_flat', name=name)
        return path.parent / 'goals.yaml'

    @app.get('/api/navigation/maps/{name}/goals')
    def navigation_goals_get(name: str):
        path = goals_path(name)
        if not path.is_file():
            return {'goals': [], 'saved': None}
        try:
            p = (yaml.safe_load(path.read_text(encoding='utf-8')) or {})['/**']['ros__parameters']
            flat = [float(v) for v in p['goals']]
            yaws = [float(v) for v in p.get('yaws') or []]
            goals = [{'x': flat[i], 'y': flat[i + 1], 'yaw': yaws[i // 2] if i // 2 < len(yaws) else 0.0}
                     for i in range(0, len(flat) - 1, 2)]
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
            raise error(400, 'navigation_goals_unreadable', name=name, reason=str(e))
        return {'goals': goals, 'saved': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime))}

    @app.put('/api/navigation/maps/{name}/goals')
    def navigation_goals_put(name: str, body: dict):
        path = goals_path(name)
        try:
            asset = nav_maps.assets(name)
        except NavigationError as e:
            raise nav_error(e)
        goals = parse_goals(body, asset['grid'])
        origin, source = map_origin(map_meta(asset), cfg['mapping'].get('runs_dir', '~/runs'),
                                    world_cmd_for_preset(asset['preset']))
        params = {'goals': [v for x, y, _ in goals for v in (x, y)], 'yaws': [yaw for _, _, yaw in goals],
                  'timeout': float(nav_cfg.get('goal_timeout', 240.0))}
        if origin:
            params.update(start_x=round(origin[0], 4), start_y=round(origin[1], 4), start_yaw=round(origin[2], 5))
        text = (f'# Waypoints of map {name}, saved by robot_console on {time.strftime("%Y-%m-%d %H:%M:%S")}.\n'
                '# goals: x1, y1, x2, y2, ... in the map frame (m); yaws: heading at each goal (rad).\n'
                f'# start_*: world pose of the map origin ({source or "unknown"}), used to compare with ground truth.\n'
                '# Batch run: ros2 run lidar_nav nav_goals --ros-args --params-file goals.yaml -p use_sim_time:=true\n'
                + yaml.safe_dump({'/**': {'ros__parameters': params}}, default_flow_style=None, sort_keys=False))
        try:
            tmp = path.with_name('goals.yaml.tmp')
            tmp.write_text(text, encoding='utf-8')
            os.replace(tmp, path)
        except OSError as e:
            raise error(500, 'navigation_record_failed', reason=str(e))
        return navigation_goals_get(name)

    def world_cmd_for_preset(preset):
        """Simulation arguments of a scene preset (only world:= matters), for maps saved while no sim runs."""
        args = ((presets.get(preset) or {}).get('args') or {}).get('sim') if preset else None
        return list(args) if isinstance(args, list) else sim_cmd()

    @app.get('/api/navigation/costmap/{key}.png')
    def navigation_costmap(key: str):
        if key not in ('global', 'local'):
            raise error(404, 'navigation_costmap_missing', name=key)
        out = navdata.costmap_png(key)
        if out is None:
            raise error(404, 'navigation_costmap_missing', name=key)
        return Response(out[0], media_type='image/png', headers=out[1])

    @app.post('/api/navigation/speed')
    def navigation_speed(scale: float):
        if not (math.isfinite(scale) and scale_lo <= scale <= scale_hi):
            raise error(400, 'navigation_speed_invalid', lo=scale_lo, hi=scale_hi)
        if nav_speed['base_v'] is None:
            raise error(409, 'navigation_speed_failed', reason=nav_speed['error'] or '')
        nav_speed['scale'] = round(scale, 2)
        navdata.set_speed_limit(nav_speed['base_v'] * nav_speed['scale'])   # published right away and at 1 Hz
        return speed_status(stack_or_404(nav_name).state() == 'running')

    @app.post('/api/navigation/cancel')
    def navigation_cancel():
        # Cancelling stops a waypoint mission too (it does not move on to the next goal)
        if not mission.cancel() and not navdata.cancel_goal():
            raise error(409, 'navigation_no_goal')
        return navdata.goal_status()

    @app.get('/api/navigation/status')
    def navigation_status():
        s = stack_or_404(nav_name)
        state = s.state()
        active = state in ('running', 'stopping')
        sync_nav_epoch(s)
        if state == 'running':
            data = {**navdata.snapshot(), **navdata.goal_status(), 'costmaps': navdata.costmap_versions()}
        else:
            data = {'pose': None, 'scan': [], 'global_path': [], 'local_path': [], 'tf_ok': False,
                    'age': None, 'ready': False, 'error': None, 'goal': None, 'amcl': None,
                    'costmaps': {'global': None, 'local': None}}
        data['speed'] = speed_status(state == 'running')
        data['mission'] = mission.status()
        data['variant'] = running_variant(s)[0] if active else None
        sim = procs.get(sim_name)
        return {'type': 'navigation', 'state': state, 'map': nav_map_name(s) if active else None,
                'sim_running': sim is not None and sim.state() == 'running',
                'sim_preset': sim.preset if sim is not None else None,
                'sim': {'state': sim.state() if sim else 'idle', 'origin': sim.origin if sim else None,
                        'job': nav_sim_job['state'], 'error': nav_sim_job['error']},
                'exit_code': s.exit_code if state == 'exited' else None, **data}

    # ---------------- WebSocket ----------------

    # 每个网页连接两个任务：send_loop 定时发数据，ws 本身接收网页发来的消息。网页消息：
    #   {"type": "subscribe", "streams": ["mapping"]}：订阅数据流。每次订阅 mapping 都会从头重发整张地图
    #     （网页发现数据对不上时也用它来重新同步）
    #   {"type": "teleop", "v": 0.3, "w": 0.0}：遥控速度，网页按住摇杆时 10 Hz 发送；连接断开时立即停车

    async def send_loop(sock, client):
        next_status = 0.0
        try:
            while True:
                if time.monotonic() >= next_status:
                    next_status = time.monotonic() + period
                    # snapshot 里有 ROS 图查询和加锁，放到线程池里做，不卡住事件循环
                    await sock.send_json(await asyncio.to_thread(status))
                if 'mapping' in client['streams']:
                    ms = mapping.status()
                    ms['waypoints'] = await asyncio.to_thread(waypoint_progress)
                    ms['teleop'] = teleop.status()
                    ms['save'] = library.status()
                    ms['playback'] = await asyncio.to_thread(playback.status)
                    await sock.send_json(ms)
                    for f in await asyncio.to_thread(mapping.pull, client['cursor']):
                        if isinstance(f, bytes):
                            await sock.send_bytes(f)
                        else:
                            await sock.send_json(f)
                await asyncio.sleep(STREAM_PERIOD)
        except (WebSocketDisconnect, RuntimeError):
            pass                       # 浏览器关闭或刷新页面

    @app.websocket('/ws')
    async def ws(sock: WebSocket):
        await sock.accept()
        client = {'streams': set(), 'cursor': {}}
        sender = asyncio.create_task(send_loop(sock, client))
        try:
            while True:
                try:
                    msg = json.loads(await sock.receive_text())
                except ValueError:
                    continue           # 不是 JSON，忽略
                if isinstance(msg, dict) and msg.get('type') == 'subscribe':
                    streams = msg.get('streams')
                    client['streams'] = STREAMS & set(streams if isinstance(streams, list) else [])
                    client['cursor'] = {}
                elif isinstance(msg, dict) and msg.get('type') == 'teleop':
                    # 被拒绝（锁定、航点运行中）时不回复，网页从建图状态里的 teleop 字段看到原因
                    teleop.command(msg.get('v'), msg.get('w'), owner=id(client))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            teleop.release(id(client))
            sender.cancel()

    # ---------------- 前端页面 ----------------
    # 开发时用 npm run dev（端口 5173）；打包后（npm run build）由这里直接提供页面
    dist = cfg['server']['web_dist']
    if os.path.isdir(dist):
        app.mount('/', WebFiles(directory=dist, html=True), name='web')
    else:
        @app.get('/')
        def no_web():
            return {'message': f'前端还没打包：{dist} 不存在。开发时请访问 http://localhost:5173',
                    'message_en': f'Web UI not built: {dist} not found. For development open http://localhost:5173'}

    return app
