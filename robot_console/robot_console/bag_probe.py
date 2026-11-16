"""Read the first messages of a bag's point cloud and IMU topics and print what they hold (JSON on stdout).
Used by the sensor setup of the Playback page (sensor_profiles.py), which turns the numbers into a guessed
sensor profile. Runs as its own process so a broken bag or storage plugin cannot hang or crash the console.

Usage: python bag_probe.py <bag folder> <storage id>

Output:
    clouds {topic: {frame_id, stamp (s), width, height, point_step, points, finite,
                    fields [{name, datatype, offset, count}],
                    stats {field: {min, max, median}}  (numeric scalar fields over the finite points)}}
    imus   {topic: {frame_id, samples, rate (Hz), acc_mean [x y z], acc_norm, acc_norm_std, gyro_norm}}
    odoms  [nav_msgs/Odometry topics]
Clouds: the statistics merge the first CLOUD_SAMPLES scans; the stamp is the first scan's.
"""
import json
import sys
import time

import numpy as np

CLOUD = 'sensor_msgs/msg/PointCloud2'
IMU = 'sensor_msgs/msg/Imu'
ODOM = 'nav_msgs/msg/Odometry'
CLOUD_SAMPLES = 3
IMU_SAMPLES = 400           # 2 s at 200 Hz
MAX_MESSAGES = 50000        # stop reading after this many messages of the wanted topics
MAX_SECONDS = 40.0
PF_TYPES = {1: 'i1', 2: 'u1', 3: 'i2', 4: 'u2', 5: 'i4', 6: 'u4', 7: 'f4', 8: 'f8'}


def cloud_values(msg):
    """{field: float64 array over the points with finite x y z}, number of points, number of finite points."""
    fields, seen = [], set()
    for f in msg.fields:
        if f.datatype in PF_TYPES and f.count == 1 and f.name and f.name not in seen:
            seen.add(f.name)
            fields.append((f.name, f.offset, f.datatype))
    names = [n for n, _, _ in fields]
    dt = np.dtype({'names': names, 'formats': ['<' + PF_TYPES[d] for _, _, d in fields],
                   'offsets': [o for _, o, _ in fields], 'itemsize': msg.point_step})
    data = bytes(msg.data)
    a = np.frombuffer(data, dtype=dt, count=len(data) // msg.point_step) if msg.point_step else np.zeros(0, dt)
    ok = np.ones(len(a), bool)
    for k in ('x', 'y', 'z'):
        if k in names:
            ok &= np.isfinite(a[k])
    return {n: a[n][ok].astype(np.float64) for n in names}, len(a), int(ok.sum())


def main():
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    path, storage = sys.argv[1], sys.argv[2]
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=path, storage_id=storage),
                rosbag2_py.ConverterOptions(input_serialization_format='cdr', output_serialization_format='cdr'))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    clouds = sorted(n for n, t in types.items() if t == CLOUD)
    imus = sorted(n for n, t in types.items() if t == IMU)
    out = {'clouds': {}, 'imus': {}, 'odoms': sorted(n for n, t in types.items() if t == ODOM)}
    want = clouds + imus
    if not want:
        print(json.dumps(out))
        return
    reader.set_filter(rosbag2_py.StorageFilter(topics=want))
    msg_types = {n: get_message(types[n]) for n in want}
    scans = {n: [] for n in clouds}         # [(values, points, finite)]
    first = {}                              # topic -> first message (header data)
    imu = {n: [] for n in imus}             # [(t, acc xyz, gyro xyz)]
    t0, read = time.monotonic(), 0
    while reader.has_next():
        if all(len(v) >= CLOUD_SAMPLES for v in scans.values()) and all(len(v) >= IMU_SAMPLES for v in imu.values()):
            break
        if read >= MAX_MESSAGES or time.monotonic() - t0 > MAX_SECONDS:
            break
        topic, raw, _ = reader.read_next()
        read += 1
        if topic in scans and len(scans[topic]) < CLOUD_SAMPLES:
            msg = deserialize_message(raw, msg_types[topic])
            first.setdefault(topic, msg)
            scans[topic].append(cloud_values(msg))
        elif topic in imu and len(imu[topic]) < IMU_SAMPLES:
            m = deserialize_message(raw, msg_types[topic])
            first.setdefault(topic, m)
            a, g = m.linear_acceleration, m.angular_velocity
            imu[topic].append((m.header.stamp.sec + m.header.stamp.nanosec * 1e-9, (a.x, a.y, a.z), (g.x, g.y, g.z)))
    for topic, samples in scans.items():
        if not samples:
            continue
        m = first[topic]
        stats = {}
        for name in samples[0][0]:
            v = np.concatenate([s[0][name] for s in samples if name in s[0]])
            v = v[np.isfinite(v)]
            if len(v):
                stats[name] = {'min': float(v.min()), 'max': float(v.max()), 'median': float(np.median(v))}
        out['clouds'][topic] = {
            'frame_id': m.header.frame_id, 'stamp': m.header.stamp.sec + m.header.stamp.nanosec * 1e-9,
            'width': m.width, 'height': m.height, 'point_step': m.point_step,
            'points': samples[0][1], 'finite': samples[0][2], 'scans': len(samples),
            'fields': [{'name': f.name, 'datatype': f.datatype, 'offset': f.offset, 'count': f.count} for f in m.fields],
            'stats': stats,
        }
    for topic, samples in imu.items():
        if not samples:
            continue
        t = np.array([s[0] for s in samples])
        acc = np.array([s[1] for s in samples], float)
        gyro = np.array([s[2] for s in samples], float)
        norm = np.linalg.norm(acc, axis=1)
        span = t[-1] - t[0]
        out['imus'][topic] = {
            'frame_id': first[topic].header.frame_id, 'samples': len(samples),
            'rate': (len(samples) - 1) / span if span > 0 else None,
            'acc_mean': acc.mean(axis=0).tolist(), 'acc_norm': float(norm.mean()), 'acc_norm_std': float(norm.std()),
            'gyro_norm': float(np.linalg.norm(gyro, axis=1).mean()),
        }
    print(json.dumps(out))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:      # one line on stderr; the console shows it
        print(f'{type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
