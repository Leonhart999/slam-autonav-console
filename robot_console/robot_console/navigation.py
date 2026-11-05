"""Navigation telemetry (pose, scan, paths, costmaps), goals, and validated saved-map assets."""
import io
import json
import math
import threading
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid, Path as NavPath
from nav2_msgs.action import NavigateToPose
from nav2_msgs.msg import SpeedLimit
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from tf2_ros import Buffer, TransformException, TransformListener

from .map_library import NAME_RE


class NavigationError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


class NavigationMaps:
    def __init__(self, root, solids=None):
        self.root = Path(root).expanduser().resolve()
        self.solids = solids      # SolidModels or None

    def resolve(self, name):
        if not NAME_RE.fullmatch(name or ''):
            raise NavigationError('invalid_map_name', name=name)
        folder = self.root / name
        path = folder / 'map.yaml' if folder.is_dir() else self.root / (name + '.yaml')
        if not path.is_file() or not path.resolve().is_relative_to(self.root):
            raise NavigationError('map_not_found', name=name)
        return path.resolve()

    def assets(self, name):
        path = self.resolve(name)
        try:
            info = yaml.safe_load(path.read_text(encoding='utf-8'))
            image = (path.parent / info['image']).resolve()
            if not image.is_relative_to(self.root) or not image.is_file():
                raise ValueError('Map image must be inside the map library')
            resolution = float(info['resolution'])
            origin = [float(v) for v in info['origin']]
            if resolution <= 0 or len(origin) != 3 or not np.isfinite([resolution, *origin]).all():
                raise ValueError('Invalid resolution or origin')
            with Image.open(image) as im:
                w, h = im.size
            meta_path = path.parent / 'meta.json' if path.name == 'map.yaml' else None
            meta = json.loads(meta_path.read_text()) if meta_path and meta_path.is_file() else {}
            cloud = None
            if path.name == 'map.yaml':
                for filename in ('map_voxel.pcd', 'map.pcd'):
                    p = path.parent / filename
                    if p.is_file() and p.resolve().is_relative_to(self.root):
                        cloud = p
                        break
            has_solid = bool(self.solids and path.name == 'map.yaml' and self.solids.get(meta.get('preset')))
            return {'name': name, 'has_cloud': cloud is not None, 'has_solid': has_solid, 'preset': meta.get('preset'),
                    'grid': {'resolution': resolution, 'width': w, 'height': h, 'origin': origin},
                    '_yaml': path, '_image': image, '_cloud': cloud}
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            raise NavigationError('navigation_map_invalid', reason=str(exc), name=name) from exc

    def list(self):
        if not self.root.is_dir():
            return []
        names = {p.name for p in self.root.iterdir() if p.is_dir() and (p / 'map.yaml').is_file()}
        names |= {p.stem for p in self.root.glob('*.yaml')}
        result = []
        for name in sorted(names):
            try:
                result.append({k: v for k, v in self.assets(name).items() if not k.startswith('_')})
            except NavigationError:
                continue
        return result

    def png(self, name):
        asset = self.assets(name)
        with Image.open(asset['_image']) as im:
            output = io.BytesIO()
            rgba = im.convert('RGBA')
            background = Image.new('RGBA', rgba.size, (205, 205, 205, 255))
            background.alpha_composite(rgba)
            background.convert('L').save(output, format='PNG')
        return output.getvalue()

    def cloud(self, name):
        from lidar_nav.pcd_to_grid import read_pcd_xyz
        asset = self.assets(name)
        if asset['_cloud'] is None:
            return b''
        points = read_pcd_xyz(asset['_cloud'])
        points = points[np.isfinite(points).all(axis=1)]
        points = points[::max(1, math.ceil(len(points) / 200000))]
        return np.asarray(points, dtype='<f4').tobytes()


def transform_points(points, transform):
    """Apply the complete quaternion transform, not just planar yaw."""
    q = transform.rotation
    x, y, z, w = q.x, q.y, q.z, q.w
    rotation = np.array([
        [1 - 2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1 - 2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1 - 2*(x*x+y*y)]])
    t = transform.translation
    return np.asarray(points) @ rotation.T + [t.x, t.y, t.z]


# Nav2 costmaps shown on the 2D view. Rendered to PNG on request, at most once per period (the global
# costmap of the warehouse is ~600k cells; converting every message would waste CPU).
COSTMAPS = {'global': '/global_costmap/costmap', 'local': '/local_costmap/costmap'}
COSTMAP_PERIOD = {'global': 1.0, 'local': 0.5}


def _costmap_lut():
    """RGBA for OccupancyGrid values -1..100 (index = value + 1). Free and unknown cells are transparent."""
    lut = np.zeros((102, 4), dtype=np.uint8)
    k = np.linspace(0, 1, 98)[:, None]             # values 1..98: inflation gradient, sky blue -> rose
    lut[2:100, :3] = np.round((1 - k) * [56, 189, 248] + k * [244, 63, 94])
    lut[2:100, 3] = np.round(40 + 120 * k[:, 0])
    lut[100] = [225, 29, 72, 180]                  # 99: inscribed (robot centre here means collision)
    lut[101] = [136, 19, 55, 220]                  # 100: lethal (obstacle)
    return lut


COSTMAP_LUT = _costmap_lut()


def _yaw(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class NavigationData:
    RETRIES = 2                       # resends of a goal that Nav2 rejected

    def __init__(self, node):
        self.node = node
        self.lock = threading.Lock()
        self.buffer = Buffer(cache_time=Duration(seconds=10))
        self.listener = TransformListener(self.buffer, node, spin_thread=False)
        self.action = ActionClient(node, NavigateToPose, '/navigate_to_pose')
        self.scan = None
        self.paths = {'global_path': None, 'local_path': None}
        self.epoch = 0
        self.enabled = False
        self.goal = None              # current/last goal: {'seq','x','y','yaw','state','feedback',...}
        self.goal_handle = None
        self.goal_seq = 0
        self.cancel_requested = False
        self.amcl = None              # (AMCL standard deviations, receive time)
        self.initial_pub = node.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        # Navigation speed multiplier (see nav_speed.py). Republished every second because the controller's
        # subscription is volatile: a restarted Nav2 would otherwise run at its (high) yaml limit.
        self.speed_limit = None       # m/s, absolute; None = do not publish
        self.speed_pub = node.create_publisher(SpeedLimit, '/speed_limit', 10)
        node.create_timer(1.0, self._publish_speed_limit)
        # AMCL publishes /amcl_pose as transient-local, reliable
        amcl_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL)
        node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self._on_amcl, amcl_qos)
        node.create_subscription(LaserScan, '/scan', self._scan, qos_profile_sensor_data)
        node.create_subscription(NavPath, '/plan', lambda m: self._path('global_path', m), 10)
        node.create_subscription(NavPath, '/local_plan', lambda m: self._path('local_path', m), 10)
        # Costmaps are published transient-local + reliable with depth 1, like /amcl_pose
        self.costmaps = dict.fromkeys(COSTMAPS)         # key -> (msg, count)
        self.costmap_cache = dict.fromkeys(COSTMAPS)    # key -> rendered {'count','made','png','headers'}
        self.costmap_count = 0
        for key, topic in COSTMAPS.items():
            node.create_subscription(OccupancyGrid, topic, lambda m, k=key: self._costmap(k, m), amcl_qos)

    # ---------------- Speed multiplier ----------------

    def set_speed_limit(self, v):
        with self.lock:
            self.speed_limit = None if v is None else round(float(v), 3)
        self._publish_speed_limit()

    def _publish_speed_limit(self):
        with self.lock:
            v = self.speed_limit
        if v is not None:
            self.speed_pub.publish(SpeedLimit(percentage=False, speed_limit=v))

    def speed_limit_listeners(self):
        return self.speed_pub.get_subscription_count()

    # ---------------- Initial pose and goals (3b) ----------------

    def _on_amcl(self, msg):
        c = msg.pose.covariance
        with self.lock:
            if self.enabled:
                self.amcl = ({'std_x': math.sqrt(max(c[0], 0)), 'std_y': math.sqrt(max(c[7], 0)),
                              'std_yaw': math.sqrt(max(c[35], 0))}, time.monotonic())

    def set_initial_pose(self, x, y, yaw):
        """Publish /initialpose in the map frame (what rviz2's "2D Pose Estimate" does)."""
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'          # stamp 0: AMCL uses the latest odometry
        msg.pose.pose.position.x, msg.pose.pose.position.y = float(x), float(y)
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        cov = [0.0] * 36
        cov[0] = cov[7] = 0.25               # 0.5 m standard deviation, same as rviz2
        cov[35] = 0.0685                      # about 15 degrees
        msg.pose.covariance = cov
        self.initial_pub.publish(msg)
        with self.lock:
            self.amcl = None                  # wait for AMCL to report the new estimate

    def send_goal(self, x, y, yaw, on_done=None):
        """Send a NavigateToPose goal. A previous goal is replaced by Nav2 (it preempts it).

        on_done(state, goal) is called once when this goal ends (succeeded / aborted / canceled / rejected),
        from the executor thread, unless a newer goal or a reset replaced it first."""
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.pose.position.x, goal.pose.pose.position.y = float(x), float(y)
        goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        with self.lock:
            self.goal_seq += 1
            seq, epoch = self.goal_seq, self.epoch
            self.goal = {'seq': seq, 'x': x, 'y': y, 'yaw': yaw, 'state': 'pending', 'feedback': None,
                         'started': time.monotonic(), 'finished': None}
            self.goal_handle = None
            self.cancel_requested = False
        attempts = [0]                        # resends after a rejection (see on_response)

        def current():
            return self.goal is not None and self.goal['seq'] == seq and self.epoch == epoch

        def finish(state):
            with self.lock:
                if not current():
                    return
                self.goal.update(state=state, finished=time.monotonic())
                info = dict(self.goal)
            if on_done is not None:
                on_done(state, info)

        def on_feedback(msg):
            fb = msg.feedback
            with self.lock:
                if current():
                    self.goal['feedback'] = {
                        'distance': round(fb.distance_remaining, 2),
                        'nav_time': round(fb.navigation_time.sec + fb.navigation_time.nanosec * 1e-9, 1),
                        'eta': round(fb.estimated_time_remaining.sec + fb.estimated_time_remaining.nanosec * 1e-9, 1),
                        'recoveries': fb.number_of_recoveries}

        def on_result(future):
            status = future.result().status if future.result() is not None else None
            finish({GoalStatus.STATUS_SUCCEEDED: 'succeeded', GoalStatus.STATUS_CANCELED: 'canceled',
                    GoalStatus.STATUS_ABORTED: 'aborted'}.get(status, 'aborted'))

        def on_response(future):
            try:
                handle = future.result()
            except Exception as exc:          # noqa: BLE001 - report any client-side failure as a rejection
                self.node.get_logger().warn(f'导航目标 {seq} 发送失败：{exc!r}')
                handle = None
            if handle is None or not handle.accepted:
                # Workaround: the very first goal after Nav2 comes up is sometimes rejected although
                # bt_navigator logs nothing (seen 2026-11-04; later goals are accepted). A rejected goal
                # never ran, so resending it is safe. Give up after RETRIES attempts.
                with self.lock:
                    retry = current() and not self.cancel_requested and attempts[0] < self.RETRIES
                    attempts[0] += 1
                if retry:
                    self.node.get_logger().warn(f'导航目标 {seq} 被拒绝，1 s 后重发（第 {attempts[0]} 次）')
                    threading.Timer(1.0, lambda: self.node.executor.create_task(send)).start()
                    return
                self.node.get_logger().warn(f'导航目标 {seq} 被拒绝（handle={handle}）')
                finish('rejected')
                return
            with self.lock:
                if not current():
                    return
                self.goal_handle = handle
                cancel = self.cancel_requested
                self.goal['state'] = 'canceling' if cancel else 'active'
            handle.get_result_async().add_done_callback(on_result)
            if cancel:
                handle.cancel_goal_async()

        def send():
            # Runs in the executor thread: rclpy action clients are not safe to drive from other threads
            self.action.send_goal_async(goal, feedback_callback=on_feedback).add_done_callback(on_response)

        self.node.executor.create_task(send)
        return seq

    def cancel_goal(self):
        """Cancel the current goal. Returns False when there is no goal to cancel."""
        with self.lock:
            if self.goal is None or self.goal['state'] not in ('pending', 'active'):
                return False
            self.cancel_requested = True
            handle = self.goal_handle
            self.goal['state'] = 'canceling'
        if handle is not None:
            self.node.executor.create_task(handle.cancel_goal_async)
        return True

    def goal_status(self):
        with self.lock:
            g = dict(self.goal) if self.goal else None
            amcl = self.amcl
        if g:
            end = g.pop('finished') or time.monotonic()
            g['elapsed'] = round(end - g.pop('started'), 1)
        loc = None
        if amcl and time.monotonic() - amcl[1] < 30:
            loc = {k: round(v, 3) for k, v in amcl[0].items()}
        return {'goal': g, 'amcl': loc}

    def reset(self, enabled=False):
        with self.lock:
            self.epoch += 1
            self.enabled = enabled
            self.scan = None
            self.paths = dict.fromkeys(self.paths)
            self.goal = None
            self.goal_handle = None
            self.amcl = None
            self.costmaps = dict.fromkeys(COSTMAPS)
            self.costmap_cache = dict.fromkeys(COSTMAPS)
        self.buffer.clear()

    def _scan(self, msg):
        with self.lock:
            if self.enabled:
                self.scan = (msg, time.monotonic())

    def _path(self, key, msg):
        with self.lock:
            if self.enabled:
                self.paths[key] = (msg, time.monotonic())

    def _costmap(self, key, msg):
        with self.lock:
            if self.enabled:
                self.costmap_count += 1
                self.costmaps[key] = (msg, self.costmap_count)

    def costmap_versions(self):
        """{key: version or None}; the page fetches the PNG again when the version changes."""
        with self.lock:
            return {k: (v[1] if v else None) for k, v in self.costmaps.items()}

    def costmap_png(self, key):
        """(png bytes, headers with the placement in the map frame) or None when nothing was received.

        Headers: X-Origin "x,y,yaw" of the grid's lower-left corner in the map frame, X-Resolution (m/cell),
        X-Size "width,height" (cells), X-Version. Row 0 of the PNG is the top (largest y), like a PGM."""
        with self.lock:
            item, cache, epoch = self.costmaps.get(key), self.costmap_cache.get(key), self.epoch
        if item is None:
            return None
        msg, count = item
        if cache and (cache['count'] == count or time.monotonic() - cache['made'] < COSTMAP_PERIOD[key]):
            return cache['png'], cache['headers']
        info = msg.info
        w, h = info.width, info.height
        data = np.asarray(msg.data, dtype=np.int8)
        if w * h == 0 or data.size != w * h:
            return None
        rgba = COSTMAP_LUT[np.clip(data.astype(np.int16), -1, 100) + 1].reshape(h, w, 4)
        out = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(rgba[::-1]), 'RGBA').save(out, format='PNG', compress_level=1)
        # Grid origin in its own frame (map for the global costmap, odom for the rolling local one)
        o = info.origin
        x, y, yaw = o.position.x, o.position.y, _yaw(o.orientation)
        frame = msg.header.frame_id.lstrip('/')
        if frame and frame != 'map':
            try:
                tf = self._transform(frame)
            except TransformException:
                return (cache['png'], cache['headers']) if cache else None
            x, y, _ = transform_points([[x, y, 0.0]], tf)[0]
            yaw += _yaw(tf.rotation)
        headers = {'X-Origin': f'{x:.4f},{y:.4f},{yaw:.5f}', 'X-Resolution': f'{info.resolution:.5f}',
                   'X-Size': f'{w},{h}', 'X-Version': str(count), 'Cache-Control': 'no-store'}
        with self.lock:
            if epoch == self.epoch:
                self.costmap_cache[key] = {'count': count, 'made': time.monotonic(),
                                           'png': out.getvalue(), 'headers': headers}
        return out.getvalue(), headers

    def robot_pose(self):
        """Latest map -> base_link as (x, y, yaw), or None."""
        try:
            tf = self.buffer.lookup_transform('map', 'base_link', Time(), timeout=Duration(seconds=0))
        except TransformException:
            return None
        t = tf.transform
        return t.translation.x, t.translation.y, _yaw(t.rotation)

    def clearance(self):
        """Smallest distance (m) from base_link to a point of the latest scan (< 1 s old), or None."""
        with self.lock:
            scan = self.scan
        if not scan or time.monotonic() - scan[1] > 1.0:
            return None
        msg = scan[0]
        ranges = np.asarray(msg.ranges)
        angles = msg.angle_min + np.arange(len(ranges)) * msg.angle_increment
        valid = np.isfinite(ranges) & (ranges >= msg.range_min) & (ranges <= msg.range_max)
        if not valid.any():
            return None
        xyz = np.c_[ranges[valid] * np.cos(angles[valid]), ranges[valid] * np.sin(angles[valid]),
                    np.zeros(np.count_nonzero(valid))]
        try:
            tf = self.buffer.lookup_transform('base_link', msg.header.frame_id.lstrip('/'), Time(),
                                              timeout=Duration(seconds=0)).transform
        except TransformException:
            return None
        xyz = transform_points(xyz, tf)
        return float(np.min(np.hypot(xyz[:, 0], xyz[:, 1])))

    def _transform(self, frame, stamp=None):
        frame = frame.lstrip('/')
        if not frame:
            raise ValueError('Missing frame')
        if frame == 'map':
            return None
        return self.buffer.lookup_transform('map', frame, Time.from_msg(stamp) if stamp else Time(),
                                            timeout=Duration(seconds=0)).transform

    def snapshot(self):
        out = {'pose': None, 'scan': [], 'global_path': [], 'local_path': [],
               'tf_ok': False, 'age': None, 'ready': False, 'error': None}
        with self.lock:
            scan, paths, epoch, enabled = self.scan, dict(self.paths), self.epoch, self.enabled
        if not enabled:
            return out
        try:
            tf = self.buffer.lookup_transform('map', 'base_link', Time(), timeout=Duration(seconds=0))
            stamp = tf.header.stamp.sec + tf.header.stamp.nanosec * 1e-9
            # The console node runs on wall time; compare TF stamps against simulated /clock when present.
            now = self.node.sim_now()
            age = abs((time.time() if now is None else now) - stamp)
            out['age'] = round(age, 2)
            if age > 2:
                raise ValueError('Stale map to base_link transform')
            q, p = tf.transform.rotation, tf.transform.translation
            out['pose'] = {'x': p.x, 'y': p.y, 'z': p.z,
                           'yaw': math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))}
            out['tf_ok'] = True
            out['ready'] = self.action.server_is_ready()
            if scan and time.monotonic() - scan[1] < 2:
                msg = scan[0]
                ranges = np.asarray(msg.ranges)
                angles = msg.angle_min + np.arange(len(ranges)) * msg.angle_increment
                valid = np.isfinite(ranges) & (ranges >= msg.range_min) & (ranges <= msg.range_max)
                xyz = np.c_[ranges[valid]*np.cos(angles[valid]), ranges[valid]*np.sin(angles[valid]),
                            np.zeros(np.count_nonzero(valid))]
                try:
                    tf_scan = self._transform(msg.header.frame_id, msg.header.stamp)
                except TransformException:
                    # AMCL may not have published map->odom for this exact stamp yet; latest is fine for display.
                    tf_scan = self._transform(msg.header.frame_id)
                if tf_scan is not None:
                    xyz = transform_points(xyz, tf_scan)
                out['scan'] = np.round(xyz[::max(1, math.ceil(len(xyz)/2000))], 3).tolist()
        except (TransformException, ValueError) as exc:
            out['error'] = str(exc)
        if out['tf_ok']:
            for key, value in paths.items():
                if not value or time.monotonic() - value[1] > 5:
                    continue
                msg = value[0]
                if not msg.poses:
                    continue
                try:
                    # A Path uses one shared frame; reject inconsistent per-pose frames.
                    if any(p.header.frame_id and p.header.frame_id != msg.header.frame_id for p in msg.poses):
                        continue
                    xyz = np.array([[p.pose.position.x, p.pose.position.y, p.pose.position.z] for p in msg.poses])
                    tf = self._transform(msg.header.frame_id)
                    if tf is not None:
                        xyz = transform_points(xyz, tf)
                    xyz = xyz[np.isfinite(xyz).all(axis=1)]
                    out[key] = np.round(xyz[::max(1, math.ceil(len(xyz)/4000))], 3).tolist()
                except (TransformException, ValueError):
                    pass
        with self.lock:
            if epoch != self.epoch:
                return {'pose': None, 'scan': [], 'global_path': [], 'local_path': [],
                        'tf_ok': False, 'age': None, 'ready': False, 'error': None}
        return out
