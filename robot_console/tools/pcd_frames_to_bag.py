#!/usr/bin/env python3
"""Convert a folder of per-frame lidar PCD files into a ROS 2 bag the console can play (mapping page, "Bag").

Input layout (an MCAP recording exported frame by frame, as used for the HEU lab's real-robot dataset):
  DATASET/frames.csv                 columns topic, header_stamp_ns, file (relative path of the PCD)
  DATASET/front_lidar/*.pcd          binary PCD 0.7, fields x y z intensity ring timestamp (absolute s),
  DATASET/rear_lidar/*.pcd           organized, NaN for missing returns, each in its own lidar frame
  DATASET/calibration_results.yaml   extrinsic_transforms: lidar_front_to_imu_front, imu_front_to_base,
                                     lidar_back_to_lidar_front (4x4, "A_to_B" maps points from A into B)

Output: one sensor_msgs/PointCloud2 per front scan on /points, frame base_link, sqlite3 storage:
  - NaN points dropped, points transformed into base_link with the calibration
  - field t = per-point time relative to header.stamp (float32 s) for deskewing, ring kept
  - with --rear, the nearest rear scan (|dt| < 50 ms) is merged in (its rings + 96)
No IMU, odometry or ground truth: on the mapping page use "GLIM (LiDAR only)".

Size and speed: about 2.6 MB per merged scan (26 MB per second of recording); conversion runs at about
0.4x real time when the PCDs are read from a Windows drive in WSL. Convert a time window first.

Usage (ROS 2 sourced):
  python3 tools/pcd_frames_to_bag.py DATASET OUT_BAG [--start S] [--duration S] [--rear]
  e.g. python3 tools/pcd_frames_to_bag.py /path/to/HEU61 ~/robot_console_data/bags/heu61_0_120 --duration 120 --rear
"""
import argparse
import csv
import os
import sys

import numpy as np
import yaml

DTYPE = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('intensity', '<f4'),
                  ('ring', '<u2'), ('timestamp', '<f8')])
OUT_DTYPE = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('intensity', '<f4'),
                      ('ring', '<u2'), ('t', '<f4')])
REAR_MAX_DT = 50_000_000     # ns, a rear scan further away than this from the front scan is not merged


def load_pcd(path):
    with open(path, 'rb') as f:
        data = f.read()
    end = data.index(b'DATA binary\n') + len(b'DATA binary\n')
    header = data[:end].decode()
    if 'FIELDS x y z intensity ring timestamp' not in header:
        raise ValueError(f'{path}: unexpected PCD fields')
    n = int(header.split('POINTS ')[1].split()[0])
    pts = np.frombuffer(data[end:], dtype=DTYPE, count=n)
    return pts[np.isfinite(pts['x']) & np.isfinite(pts['y']) & np.isfinite(pts['z'])]


def orthonormal(T):
    """Project the rotation block onto SO(3) (calibration files carry entries like 1.00006)."""
    u, _, vt = np.linalg.svd(T[:3, :3])
    out = np.eye(4)
    out[:3, :3] = u @ vt
    out[:3, 3] = T[:3, 3]
    return out


def extrinsics(dataset):
    """(T_base_front, T_base_rear): 4x4 transforms of points from each lidar frame into base_link."""
    with open(os.path.join(dataset, 'calibration_results.yaml'), encoding='utf-8') as f:
        ext = yaml.safe_load(f)['extrinsic_transforms']
    T_imu_front = orthonormal(np.array(ext['lidar_front_to_imu_front'], dtype=float))
    T_base_imu = orthonormal(np.array(ext['imu_front_to_base'], dtype=float))
    T_front_rear = orthonormal(np.array(ext['lidar_back_to_lidar_front'], dtype=float))
    T_base_front = T_base_imu @ T_imu_front
    return T_base_front, T_base_front @ T_front_rear


def transform(pts, T, stamp_s, ring_offset=0):
    out = np.empty(len(pts), dtype=OUT_DTYPE)
    xyz = np.stack([pts['x'], pts['y'], pts['z']], axis=1).astype(np.float64) @ T[:3, :3].T + T[:3, 3]
    out['x'], out['y'], out['z'] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    out['intensity'] = pts['intensity']
    out['ring'] = pts['ring'] + ring_offset
    out['t'] = (pts['timestamp'] - stamp_s).astype(np.float32)
    return out


def read_index(dataset):
    frames = {'/front_lidar': [], '/rear_lidar': []}
    with open(os.path.join(dataset, 'frames.csv'), encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['topic'] in frames:
                frames[row['topic']].append((int(row['header_stamp_ns']), row['file']))
    for v in frames.values():
        v.sort()
    return frames


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dataset')
    ap.add_argument('out', help='bag folder to create (must not exist)')
    ap.add_argument('--start', type=float, default=0.0, help='s after the first front scan')
    ap.add_argument('--duration', type=float, default=120.0, help='s; <= 0 converts until the end')
    ap.add_argument('--rear', action='store_true', help='merge the rear lidar into each message')
    ap.add_argument('--topic', default='/points')
    ap.add_argument('--frame', default='base_link')
    args = ap.parse_args()
    if os.path.exists(args.out):
        sys.exit(f'{args.out} already exists')

    import rosbag2_py
    from rclpy.serialization import serialize_message
    from sensor_msgs.msg import PointCloud2, PointField

    T_base_front, T_base_rear = extrinsics(args.dataset)
    frames = read_index(args.dataset)
    front, rear = frames['/front_lidar'], frames['/rear_lidar']
    if not front:
        sys.exit('frames.csv lists no /front_lidar scans')
    rear_stamps = np.array([s for s, _ in rear], dtype=np.int64)
    t0 = front[0][0] + int(args.start * 1e9)
    t1 = t0 + int(args.duration * 1e9) if args.duration > 0 else 2 ** 62
    selected = [(s, f) for s, f in front if t0 <= s < t1]

    fields = [PointField(name=n, offset=OUT_DTYPE.fields[n][1], count=1,
                         datatype=PointField.UINT16 if n == 'ring' else PointField.FLOAT32)
              for n in OUT_DTYPE.names]
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(uri=args.out, storage_id='sqlite3'),
                rosbag2_py.ConverterOptions('cdr', 'cdr'))
    writer.create_topic(rosbag2_py.TopicMetadata(name=args.topic, type='sensor_msgs/msg/PointCloud2',
                                                 serialization_format='cdr'))
    merged = 0
    for i, (s, f) in enumerate(selected):
        p = transform(load_pcd(os.path.join(args.dataset, f)), T_base_front, s * 1e-9)
        if args.rear and len(rear_stamps):
            j = int(np.argmin(np.abs(rear_stamps - s)))
            if abs(rear_stamps[j] - s) < REAR_MAX_DT:
                q = transform(load_pcd(os.path.join(args.dataset, rear[j][1])), T_base_rear, s * 1e-9, 96)
                p = np.concatenate([p, q])
                p = p[np.argsort(p['t'], kind='stable')]
                merged += 1
        msg = PointCloud2()
        msg.header.stamp.sec, msg.header.stamp.nanosec = s // 1_000_000_000, s % 1_000_000_000
        msg.header.frame_id = args.frame
        msg.height, msg.width = 1, len(p)
        msg.fields = fields
        msg.is_bigendian = False
        msg.point_step = OUT_DTYPE.itemsize
        msg.row_step = msg.point_step * len(p)
        msg.is_dense = True
        msg.data = p.tobytes()
        writer.write(args.topic, serialize_message(msg), s)
        if i % 100 == 0:
            print(f'{i}/{len(selected)} scans', file=sys.stderr, flush=True)
    del writer                       # closes the bag and writes metadata.yaml
    print(f'wrote {len(selected)} scans ({merged} with the rear lidar) to {args.out}')


if __name__ == '__main__':
    main()
