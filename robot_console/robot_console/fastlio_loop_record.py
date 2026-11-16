"""Record FAST-LIO body-frame scans and odometry for the offline loop-closure back-end.

Writes <out>/scans.bin (float32 x y z intensity, appended scan by scan) and <out>/index.csv
(stamp, point offset, point count, pose x y z qx qy qz qw). A scan is stored once its odometry
message with the same stamp has arrived.
"""
import argparse
import os
import sys

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--odom-topic', default='/Odometry')
    parser.add_argument('--cloud-topic', default='/cloud_registered_body')
    args = parser.parse_args()
    out = args.output
    os.makedirs(out, exist_ok=True)
    if os.path.exists(os.path.join(out, 'index.csv')) or os.path.exists(os.path.join(out, 'scans.bin')):
        raise FileExistsError(f'Refusing to overwrite an existing recording: {out}')
    fb = open(os.path.join(out, 'scans.bin'), 'xb')
    fi = open(os.path.join(out, 'index.csv'), 'x')
    poses, clouds, state = {}, {}, {'off': 0, 'n': 0}


    def key(stamp):
        return stamp.sec * 1000000000 + stamp.nanosec


    def flush():
        for k in sorted(set(poses) & set(clouds)):
            pts, pose = clouds.pop(k), poses.pop(k)
            fb.write(pts.tobytes())
            fb.flush()
            fi.write(f'{k * 1e-9:.6f} {state["off"]} {len(pts)} ' + ' '.join(f'{v:.6f}' for v in pose) + '\n')
            fi.flush()
            state['off'] += len(pts)
            state['n'] += 1
        # drop unmatched leftovers that are far behind
        for d in (poses, clouds):
            if len(d) > 100:
                for k in sorted(d)[:-100]:
                    d.pop(k)


    def on_odom(m):
        p, q = m.pose.pose.position, m.pose.pose.orientation
        poses[key(m.header.stamp)] = (p.x, p.y, p.z, q.x, q.y, q.z, q.w)
        flush()


    def on_cloud(m):
        f = {x.name: x.offset for x in m.fields}
        raw = np.frombuffer(m.data, dtype=np.uint8).reshape(-1, m.point_step)
        cols = [raw[:, f[c]:f[c] + 4].copy().view(np.float32) for c in ('x', 'y', 'z', 'intensity')]
        pts = np.hstack(cols)
        clouds[key(m.header.stamp)] = pts[np.isfinite(pts).all(axis=1)]
        flush()


    rclpy.init()
    node = Node('rec_scans')
    qos = QoSProfile(depth=300, reliability=ReliabilityPolicy.RELIABLE)
    node.create_subscription(Odometry, args.odom_topic, on_odom, qos)
    node.create_subscription(PointCloud2, args.cloud_topic, on_cloud, qos)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        fb.close()
        fi.close()
        print('scans', state['n'], 'points', state['off'])


if __name__ == '__main__':
    main()
