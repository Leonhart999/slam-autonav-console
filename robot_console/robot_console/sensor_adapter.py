"""Sensor adapter for bag playback: brings a real robot's lidar(s) + IMU into the form the console's SLAM configs
expect (robot_console/config/sensor_profiles.yaml, one profile per robot).

Input: the bag's raw point clouds and IMU (the player remaps them onto /playback/raw/*).
Output: the SLAM's inputs (e.g. /points_fastlio, /imu), both in one frame `body`:
    origin = the IMU, axes = the robot base (x forward, y left, z up).
So the SLAM config needs no extrinsic (identity) and its map comes out with z up, like the simulation maps.

    points:  p_body = R_base_imu (R_imu_lidar p_lidar + t_imu_lidar)
             fields x y z intensity (float32), time (float32, s after the header stamp), ring (uint16);
             NaN points (no return) are dropped
    IMU:     angular velocity and linear acceleration rotated by R_base_imu, acceleration scaled to m/s^2
             (some drivers publish g); orientation is not used (covariance -1)

Second lidar (optional, profile keys points2 + lidar2_to_lidar): its scans are moved into the first lidar's
frame and merged with the first lidar's scan of the same period (stamps within PAIR_TOL), so the SLAM sees one
cloud with both views. The merged header stamp is the earlier of the two; point times are offsets from it.
A scan without a partner (a dropped frame) is published alone. Its rings are shifted by RING2_OFFSET.

The lever arm IMU -> base is not applied: the SLAM then tracks the IMU point, which is what its filter models.
Extrinsics in the profile are 4x4 "A_to_B" matrices mapping points from A into B (as in the group's
calibration_results.yaml); slightly non-orthonormal rotations are projected with an SVD.
"""
import array
import os

import numpy as np
import yaml

G = 9.80665
FRAME = 'body'
OUT_DTYPE = np.dtype({'names': ['x', 'y', 'z', 'intensity', 'time', 'ring'],
                      'formats': ['<f4', '<f4', '<f4', '<f4', '<f4', '<u2'],
                      'offsets': [0, 4, 8, 12, 16, 20], 'itemsize': 22})
PF_TYPES = {1: 'i1', 2: 'u1', 3: 'i2', 4: 'u2', 5: 'i4', 6: 'u4', 7: 'f4', 8: 'f8'}   # PointField datatypes
PAIR_TOL = 0.02         # s, scans of the two lidars with stamps this close are merged
WAIT = 0.25             # s of bag time a scan waits for its partner before it is published alone
RING2_OFFSET = 128      # ring numbers of the second lidar start here


def load_profiles(path):
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def orthonormal(T):
    T = np.array(T, dtype=float)
    if T.shape != (4, 4):
        raise ValueError('extrinsic must be a 4x4 matrix')
    u, _, vt = np.linalg.svd(T[:3, :3])
    R = u @ vt
    if np.linalg.det(R) < 0:
        raise ValueError('extrinsic rotation is a reflection')
    T[:3, :3] = R
    return T


class Transform:
    """The profile's numbers turned into the operations of the adapter (no ROS needed, unit tested)."""

    def __init__(self, profile):
        T_il = orthonormal(profile['lidar_to_imu'])
        T_bi = orthonormal(profile['imu_to_base'])
        self.R_bi = T_bi[:3, :3]
        T_b = np.eye(4)
        T_b[:3, :3] = self.R_bi
        T_body_lidar = T_b @ T_il                          # lidar -> body (no lever arm)
        self.poses = [(T_body_lidar[:3, :3], T_body_lidar[:3, 3])]
        if profile.get('points2'):
            T_body_lidar2 = T_body_lidar @ orthonormal(profile['lidar2_to_lidar'])
            self.poses.append((T_body_lidar2[:3, :3], T_body_lidar2[:3, 3]))
        self.R, self.t = self.poses[0]
        pt = profile.get('point_time') or {}
        self.time_field = pt.get('field', 'time')
        self.time_absolute = bool(pt.get('absolute', False))
        self.time_scale = float(pt.get('scale', 1.0))      # to seconds
        self.ring_field = profile.get('ring_field', 'ring')
        self.intensity_field = profile.get('intensity_field', 'intensity')
        unit = profile.get('imu_acc_unit', 'm/s2')
        if unit not in ('g', 'm/s2'):
            raise ValueError(f'imu_acc_unit must be g or m/s2, not {unit}')
        self.acc_scale = G if unit == 'g' else 1.0

    def points(self, fields, point_step, data, stamp, lidar=0):
        """fields: [(name, offset, datatype)], data: bytes of the cloud, stamp: header time (s), lidar: 0 or 1.
        Returns a structured array in OUT_DTYPE (time relative to stamp)."""
        R, t = self.poses[lidar]
        layout = {n: (o, PF_TYPES[d]) for n, o, d in fields if d in PF_TYPES}
        for need in ('x', 'y', 'z'):
            if need not in layout:
                raise ValueError(f'point cloud has no field {need}')
        names = [n for n in ('x', 'y', 'z', self.intensity_field, self.time_field, self.ring_field) if n in layout]
        dt = np.dtype({'names': names, 'formats': ['<' + layout[n][1] for n in names],
                       'offsets': [layout[n][0] for n in names], 'itemsize': point_step})
        a = np.frombuffer(data, dtype=dt, count=len(data) // point_step)
        p = np.stack([a['x'], a['y'], a['z']], axis=1).astype(np.float64)
        ok = np.isfinite(p).all(axis=1)
        a, p = a[ok], p[ok]
        out = np.empty(len(a), dtype=OUT_DTYPE)
        q = p @ R.T + t
        out['x'], out['y'], out['z'] = q[:, 0], q[:, 1], q[:, 2]
        out['intensity'] = a[self.intensity_field] if self.intensity_field in names else 0.0
        if self.time_field in names:
            tt = a[self.time_field].astype(np.float64) * self.time_scale
            if self.time_absolute:
                tt = tt - stamp
            out['time'] = np.clip(tt, 0.0, None)
        else:
            out['time'] = 0.0
        ring = a[self.ring_field].astype(np.uint16) if self.ring_field in names else np.zeros(len(a), np.uint16)
        out['ring'] = ring + (RING2_OFFSET if lidar else 0)
        return out

    def imu(self, gyro, acc):
        return self.R_bi @ np.asarray(gyro, float), self.R_bi @ np.asarray(acc, float) * self.acc_scale


def merge(scans):
    """[(stamp, points)] of one period -> (earliest stamp, all points with times relative to it)."""
    t0 = min(s for s, _ in scans)
    parts = []
    for s, pts in scans:
        if s != t0:
            pts = pts.copy()
            pts['time'] += np.float32(s - t0)
        parts.append(pts)
    return t0, np.concatenate(parts) if len(parts) > 1 else parts[0]


class Pairer:
    """Pairs the scans of two lidars by stamp and releases them in stamp order (no ROS, unit tested).
    add(lidar, stamp, points) returns a list of (stamp, points) ready to publish."""

    def __init__(self, n_lidars):
        self.n = n_lidars
        self.pending = []           # [stamp, {lidar: points}], ordered by stamp
        self.newest = -np.inf
        self.last_out = -np.inf

    def add(self, lidar, stamp, pts):
        if self.n == 1:
            return [(stamp, pts)]
        self.newest = max(self.newest, stamp)
        if stamp <= self.last_out:          # older than what was already published: drop
            return []
        for slot in self.pending:
            if abs(slot[0] - stamp) <= PAIR_TOL and lidar not in slot[1]:
                slot[1][lidar] = (stamp, pts)
                break
        else:
            self.pending.append([stamp, {lidar: (stamp, pts)}])
            self.pending.sort(key=lambda s: s[0])
        out = []
        while self.pending:
            stamp0, parts = self.pending[0]
            if len(parts) < self.n and self.newest - stamp0 < WAIT:
                break
            self.pending.pop(0)
            t0, merged = merge(list(parts.values()))
            if t0 > self.last_out:
                self.last_out = t0
                out.append((t0, merged))
        return out


def main():
    import rclpy
    from builtin_interfaces.msg import Time
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Imu, PointCloud2, PointField

    class SensorAdapter(Node):
        def __init__(self):
            super().__init__('sensor_adapter')
            path = self.declare_parameter('profiles_file', '').value
            name = self.declare_parameter('profile', '').value
            profiles = load_profiles(path)
            if name not in profiles:
                raise SystemExit(f'sensor profile {name!r} not found in {path}')
            self.tf = Transform(profiles[name])
            topics = {k: self.declare_parameter(k, d).value for k, d in (
                ('points_in', '/playback/raw/points'), ('points2_in', '/playback/raw/points2'),
                ('imu_in', '/playback/raw/imu'), ('points_out', '/points_fastlio'), ('imu_out', '/imu'))}
            n = len(self.tf.poses)
            self.pairer = Pairer(n)
            qos = QoSProfile(depth=20, reliability=ReliabilityPolicy.RELIABLE)
            self.pub_pts = self.create_publisher(PointCloud2, topics['points_out'], qos)
            self.pub_imu = self.create_publisher(Imu, topics['imu_out'], QoSProfile(depth=2000))
            self.create_subscription(PointCloud2, topics['points_in'], lambda m: self.on_points(m, 0), qos)
            if n > 1:
                self.create_subscription(PointCloud2, topics['points2_in'], lambda m: self.on_points(m, 1), qos)
            self.create_subscription(Imu, topics['imu_in'], self.on_imu, QoSProfile(depth=2000))
            self.fields = [PointField(name=k, offset=OUT_DTYPE.fields[k][1], datatype=d, count=1)
                           for k, d in (('x', 7), ('y', 7), ('z', 7), ('intensity', 7), ('time', 7), ('ring', 4))]
            lidars = topics['points_in'] + (f' + {topics["points2_in"]}' if n > 1 else '')
            self.get_logger().info(f'sensor profile {name}: {lidars} -> {topics["points_out"]}, '
                                   f'{topics["imu_in"]} -> {topics["imu_out"]} (frame {FRAME})')

        def on_points(self, msg, lidar):
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            try:
                pts = self.tf.points([(f.name, f.offset, f.datatype) for f in msg.fields], msg.point_step,
                                     bytes(msg.data), stamp, lidar)
            except (ValueError, KeyError) as e:
                self.get_logger().error(f'cannot convert point cloud: {e}', throttle_duration_sec=5.0)
                return
            for t0, merged in self.pairer.add(lidar, stamp, pts):
                self.publish(t0, merged)

        def publish(self, t0, pts):
            out = PointCloud2()
            sec = int(np.floor(t0))
            out.header.stamp = Time(sec=sec, nanosec=min(int(round((t0 - sec) * 1e9)), 999_999_999))
            out.header.frame_id = FRAME
            out.height, out.width = 1, len(pts)
            out.fields = self.fields
            out.is_bigendian = False
            out.point_step = OUT_DTYPE.itemsize
            out.row_step = out.point_step * len(pts)
            out.is_dense = True
            out.data = array.array('B')
            out.data.frombytes(pts.tobytes())   # much faster than assigning bytes element-wise
            self.pub_pts.publish(out)

        def on_imu(self, msg):
            w, a = self.tf.imu((msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z),
                               (msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z))
            out = Imu()
            out.header.stamp = msg.header.stamp
            out.header.frame_id = FRAME
            out.orientation_covariance[0] = -1.0
            out.angular_velocity.x, out.angular_velocity.y, out.angular_velocity.z = (float(v) for v in w)
            out.linear_acceleration.x, out.linear_acceleration.y, out.linear_acceleration.z = (float(v) for v in a)
            out.angular_velocity_covariance = msg.angular_velocity_covariance
            out.linear_acceleration_covariance = msg.linear_acceleration_covariance
            self.pub_imu.publish(out)

    rclpy.init()
    node = SensorAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
