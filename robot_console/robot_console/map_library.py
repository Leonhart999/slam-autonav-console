"""地图库：把当前建图结果保存到 ~/maps/<名称>/。

保存步骤（在后台线程里做，进度通过建图状态推给网页）：
  1. map_save  FAST-LIO：调 /map_save，它把累积的稠密点云写到 pcd_path（每次覆盖同一个文件）
     stop_slam GLIM：停止进程（它退出时才写出全局优化后的地图），读 dump_dir 里的子图拼成点云
  2. copy      复制成 <名称>/map.pcd；另存控制台自己的 0.1 m 体素地图 map_voxel.pcd（小，地图管理页预览用）
  3. project   用 lidar_nav 的 pcd_to_grid 投影成 2D 栅格：map.pgm、map.yaml、map_preview.png
  4. figures   生成二维、三维、投影对比和实验图片，保存可复查的数据快照
  5. meta      写 meta.json（场景、算法、时长、轨迹长度、点数、栅格大小、对应的运行记录）
名称只允许字母、数字、_、-（防止路径穿越）；同名地图已存在时拒绝，不覆盖。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class SaveError(Exception):
    """保存失败：code、params 由网页翻译成当前语言。"""

    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def write_xyz_pcd(path, pts):
    """把 (N, 3) float32 点写成二进制 PCD（只有 x y z）。"""
    n = len(pts)
    header = (f'# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z\n'
              f'SIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH {n}\nHEIGHT 1\n'
              f'VIEWPOINT 0 0 0 1 0 0 0\nPOINTS {n}\nDATA binary\n')
    with open(path, 'wb') as f:
        f.write(header.encode('ascii'))
        f.write(pts.astype('<f4').tobytes())


def pcd_points(path):
    """读 PCD 头里的点数（不读数据）。"""
    with open(path, 'rb') as f:
        for _ in range(20):
            line = f.readline().decode('ascii', errors='replace')
            if line.startswith('POINTS'):
                return int(line.split()[1])
    return None


def pgm_size(path):
    """读二进制 PGM（P5）的宽、高。"""
    with open(path, 'rb') as f:
        tokens = []
        while len(tokens) < 3:
            line = f.readline()
            if not line.startswith(b'#'):
                tokens += line.split()
    return int(tokens[1]), int(tokens[2])


class MapLibrary:

    def __init__(self, cfg, node, mapping, info_fn, procs=None):
        """info_fn：无参函数，返回当前场景预设、命令等信息（写进 meta.json）。
        procs: ProcessManager, needed for SLAM profiles with save_mode 'dump' (saving stops the SLAM process).
        Where each SLAM writes its map comes from mapping.profile (console.yaml mapping.slam_profiles)."""
        self.maps_dir = os.path.expanduser(cfg.get('maps_dir', '~/maps'))
        self._node = node
        self._mapping = mapping
        self._procs = procs
        self._info_fn = info_fn
        self._lock = threading.Lock()
        self._job = {'state': 'idle'}       # idle / running / done / failed

    def map_dir(self, name):
        return os.path.join(self.maps_dir, name)

    def exists(self, name):
        # 旧的平铺地图（~/maps/<名称>.yaml）也算重名
        return os.path.exists(self.map_dir(name)) or os.path.exists(os.path.join(self.maps_dir, name + '.yaml'))

    def status(self):
        with self._lock:
            return dict(self._job)

    def start_save(self, name):
        """开始保存。名称不合法、已存在、正在保存时抛 SaveError（由接口返回 4xx）。"""
        if not NAME_RE.match(name or ''):
            raise SaveError('invalid_map_name', name=name)
        if self.exists(name):
            raise SaveError('map_exists', name=name)
        first = 'stop_slam' if self._mapping.profile['save_mode'] == 'dump' else 'map_save'
        with self._lock:
            if self._job['state'] == 'running':
                raise SaveError('save_busy', name=self._job.get('name'))
            self._job = {'state': 'running', 'name': name, 'step': first, 'started': time.time()}
        threading.Thread(target=self._run, args=(name,), daemon=True).start()

    def _step(self, step):
        with self._lock:
            self._job['step'] = step

    def _run(self, name):
        d = self.map_dir(name)
        created = False
        try:
            # 先独占目录，避免保存和上传同时使用同一个名称；失败只清理自己创建的目录。
            try:
                os.makedirs(d)
            except FileExistsError:
                raise SaveError('map_exists', name=name)
            created = True
            result = self._save(name, d)
            with self._lock:
                self._job.update(state='done', result=result, finished=time.time())
            return
        except SaveError as e:
            code, params = e.code, e.params
        except Exception as e:                  # 意外错误也要让网页知道，不能一直显示"保存中"
            code, params = 'save_error', {'reason': repr(e)}
        # 没保存完整（没写出 meta.json）的目录删掉，方便用同一个名字重试；
        # 目录是这次新建的（开始前检查过不存在），FAST-LIO 的原始文件仍在 pcd_path
        if created and os.path.isdir(d) and not os.path.exists(os.path.join(d, 'meta.json')):
            shutil.rmtree(d, ignore_errors=True)
        with self._lock:
            self._job.update(state='failed', code=code, params=params, finished=time.time())

    def _save_from_service(self, prof, d):
        """FAST-LIO: /map_save writes the dense map to pcd_path, which is copied into the map folder."""
        t_req = time.time()
        r = self._node.call_trigger(prof['service'], prof['timeout'])
        if r['code'] != 'service_ok':
            raise SaveError(r['code'], **r['params'])
        pcd = prof['pcd_path']
        if not os.path.exists(pcd) or os.path.getmtime(pcd) < t_req - 1:
            raise SaveError('pcd_missing', path=pcd)
        self._step('copy')
        shutil.copy2(pcd, os.path.join(d, 'map.pcd'))

    def _save_from_dump(self, slam, prof, d):
        """GLIM: the globally optimized map exists only after the process exits. Stop it (the stop hook in
        main.py also swaps the run's est.tum for the optimized trajectory), then merge the dumped submaps."""
        from . import glim_dump
        s = self._procs.get(slam) if self._procs else None
        if s is None or s.started_at is None:
            raise SaveError('slam_not_running', name=slam)
        if s.state() == 'running':
            self._procs.stop(slam)
        dump = prof['dump_dir']
        if not glim_dump.fresh(dump, s.started_at):
            raise SaveError('glim_dump_missing', path=dump)
        self._step('copy')
        try:
            pts = glim_dump.map_points(dump)
        except glim_dump.DumpError as e:
            raise SaveError(e.code, **e.params)
        write_xyz_pcd(os.path.join(d, 'map.pcd'), pts)
        # keep GLIM's own trajectories next to the map (small text files) for later checks
        out = os.path.join(d, 'data', 'glim')
        os.makedirs(out, exist_ok=True)
        for f in ('traj_imu.txt', 'odom_imu.txt', 'traj_lidar.txt', 'odom_lidar.txt', 'graph.txt'):
            if os.path.isfile(os.path.join(dump, f)):
                shutil.copy2(os.path.join(dump, f), os.path.join(out, f))

    def _save(self, name, d):
        # 1. 让 SLAM 写出稠密地图，复制成 <名称>/map.pcd
        prof, slam = self._mapping.profile, self._mapping.stacks['slam']
        if prof['save_mode'] == 'dump':
            self._save_from_dump(slam, prof, d)
            map_frame = 'glim_map'          # GLIM's optimized world frame (= glim_odom at the start)
        else:
            self._save_from_service(prof, d)
            map_frame = prof['frame']

        # 2. 另存体素预览
        self._step('copy')
        voxels = self._mapping.voxel_points()
        write_xyz_pcd(os.path.join(d, 'map_voxel.pcd'), voxels)
        st = self._mapping.status()
        self._mapping.export_run(os.path.join(d, 'data'))

        # 3. 投影成 2D 栅格（失败时 3D 地图仍然保留，meta 里 grid 为 null）
        self._step('project')
        grid, project_error = None, None
        try:
            p = subprocess.run(['ros2', 'run', 'lidar_nav', 'pcd_to_grid', os.path.join(d, 'map.pcd'),
                                os.path.join(d, 'map')],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                project_error = (p.stderr.strip().splitlines() or ['exit code %d' % p.returncode])[-1]
            else:
                w, h = pgm_size(os.path.join(d, 'map.pgm'))
                res = 0.05
                with open(os.path.join(d, 'map.yaml'), encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('resolution:'):
                            res = float(line.split(':')[1])
                grid = {'width': w, 'height': h, 'resolution': res,
                        'size_m': [round(w * res, 2), round(h * res, 2)]}
        except (OSError, subprocess.TimeoutExpired) as e:
            project_error = repr(e)

        # 4. 先归档预览，再独立生成成果图及完整图片清单。
        # 出图失败不删除原始点云；接口兼容旧地图的预览位置。
        self._step('figures')
        preview = os.path.join(d, 'map_preview.png')
        if os.path.isfile(preview):
            os.makedirs(os.path.join(d, 'figures', '2d'), exist_ok=True)
            shutil.move(preview, os.path.join(d, 'figures', '2d', 'map_preview.png'))
        figures_error = None
        try:
            import sys
            p = subprocess.run([sys.executable, '-m', 'robot_console.map_figures', d],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
            if p.returncode:
                figures_error = (p.stderr.strip().splitlines() or ['figure export failed'])[-1]
        except (OSError, subprocess.TimeoutExpired) as e:
            figures_error = repr(e)
        # 5. meta.json，并在运行记录里登记这张地图
        self._step('meta')
        dump = prof['save_mode'] == 'dump'
        meta = {
            'name': name, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'algorithm': prof['algorithm'],
            'slam': slam, 'frame': map_frame, 'run': st['run'], **self._info_fn(),
            'duration': st['duration'], 'length': st['length'], 'extent': st['extent'],
            'points': pcd_points(os.path.join(d, 'map.pcd')), 'voxels': len(voxels), 'voxel_size': st['voxel'],
            'grid': grid, 'project_error': project_error, 'figures_error': figures_error,
            'files': {'map.pcd': ('GLIM 全局优化后的子图点云' if dump else f'{prof["algorithm"]} 稠密点云'),
                      'map_voxel.pcd': '0.1 m 体素预览',
                      'map.pgm / map.yaml': '2D 栅格（Nav2 map_server 格式）',
                      'figures/': '2D、3D、投影对比及实验数据图片',
                      'data/': '保存时刻轨迹快照、CSV 和统计数据'
                               + ('；data/glim/ 为 GLIM 输出的轨迹' if dump else '')},
        }
        with open(os.path.join(d, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        self._mapping.add_map_to_run(name)
        return {'dir': d, 'points': meta['points'], 'voxels': meta['voxels'], 'grid': grid,
                'project_error': project_error, 'figures_error': figures_error, 'run': st['run']}
