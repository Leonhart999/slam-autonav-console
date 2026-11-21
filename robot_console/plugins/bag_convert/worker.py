"""Conversion job of the bag converter plugin: one ROS1 .bag -> one ROS2 bag folder (runs in the plugin's venv).

Protocol (plugin interface v1 jobs): argv[1] = job folder with input.json; progress as JSON lines on stdout;
result.json on success, error.json ({code, params}) on a known failure.

params: src (.bag), dst (final ROS2 bag folder, must not exist), stage (empty folder on the same disk),
        topics (list; empty = all), livox (bool: CustomMsg -> PointCloud2), storage ('sqlite3' | 'mcap')

The bag is written into <stage>/<name> and renamed to dst at the end, so the Playback page never lists a half
written bag. Messages other than converted Livox clouds go through rosbags' own ROS1 -> ROS2 converters
(rosbags.convert.converter.create_connections_converters, as `rosbags-convert` does).
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from livox import FIELDS, LIVOX_TYPES, parse_ros1, to_cloud


def emit(**msg):
    print(json.dumps(msg), flush=True)


def fail(job, code, **params):
    with open(os.path.join(job, 'error.json'), 'w', encoding='utf-8') as f:
        json.dump({'code': f'plugin.bag_convert.{code}', 'params': params}, f)
    sys.exit(1)


def main():
    job = sys.argv[1]
    with open(os.path.join(job, 'input.json'), encoding='utf-8') as f:
        p = json.load(f)['params']
    src, dst, stage = Path(p['src']), Path(p['dst']), Path(p['stage'])
    if dst.exists():
        fail(job, 'output_exists', name=dst.name)
    from rosbags.convert.converter import create_connections_converters
    from rosbags.highlevel import AnyReader
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    t0 = time.time()
    stage.mkdir(parents=True, exist_ok=True)
    out = stage / dst.name
    shutil.rmtree(out, ignore_errors=True)
    ros2 = get_typestore(Stores.ROS2_HUMBLE)
    PointCloud2 = ros2.types['sensor_msgs/msg/PointCloud2']
    PointField = ros2.types['sensor_msgs/msg/PointField']
    Header = ros2.types['std_msgs/msg/Header']
    Time = ros2.types['builtin_interfaces/msg/Time']
    fields = [PointField(name=n, offset=o, datatype=d, count=1) for n, o, d in FIELDS]
    storage = StoragePlugin.MCAP if p.get('storage') == 'mcap' else StoragePlugin.SQLITE3
    wanted = set(p.get('topics') or [])
    counts, points = {}, {}
    emit(phase='opening', progress=0)
    try:
        with AnyReader([src]) as reader:
            conns = [c for c in reader.connections if not wanted or c.topic in wanted]
            if not conns:
                fail(job, 'no_topics')
            livox = [c for c in conns if p.get('livox') and c.msgtype in LIVOX_TYPES]
            plain = [c for c in conns if c not in livox]
            start, span = reader.start_time, max(reader.end_time - reader.start_time, 1)
            with Writer(out, version=8, storage_plugin=storage) as writer:
                connmap, convmap = create_connections_converters(plain, None, reader, writer) if plain else ({}, {})
                cloud_conns = {}
                for c in livox:
                    if c.topic not in cloud_conns:
                        cloud_conns[c.topic] = writer.add_connection(c.topic, 'sensor_msgs/msg/PointCloud2',
                                                                     typestore=ros2)
                last = 0.0
                emit(phase='converting', progress=0)
                for conn, stamp, raw in reader.messages(connections=conns):
                    if conn.topic in cloud_conns and conn in livox:
                        sec, nsec, frame, timebase, pts = parse_ros1(raw)
                        cloud = to_cloud(sec, nsec, timebase, pts)
                        msg = PointCloud2(
                            header=Header(stamp=Time(sec=sec, nanosec=nsec), frame_id=frame), height=1,
                            width=len(cloud), fields=fields, is_bigendian=False, point_step=cloud.itemsize,
                            row_step=cloud.itemsize * len(cloud), data=cloud.view(np.uint8).reshape(-1),
                            is_dense=True)
                        writer.write(cloud_conns[conn.topic], stamp,
                                     ros2.serialize_cdr(msg, 'sensor_msgs/msg/PointCloud2'))
                        points[conn.topic] = points.get(conn.topic, 0) + len(cloud)
                    else:
                        writer.write(connmap[(conn.id, conn.owner)], stamp, convmap[conn.msgtype](raw))
                    counts[conn.topic] = counts.get(conn.topic, 0) + 1
                    frac = (stamp - start) / span
                    if frac - last > 0.005:
                        last = frac
                        emit(progress=round(frac * 0.98, 4), phase='converting')
        emit(phase='finishing', progress=0.99)
        os.rename(out, dst)
    except SystemExit:
        raise
    except OSError as e:
        shutil.rmtree(out, ignore_errors=True)
        fail(job, 'write_failed', reason=str(e))
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(out, ignore_errors=True)
        fail(job, 'convert_failed', reason=f'{type(e).__name__}: {e}')
    finally:
        try:
            stage.rmdir()
        except OSError:
            pass
    size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
    types = {c.topic: c.msgtype for c in conns}
    result = {'output': str(dst), 'name': dst.name, 'size': size, 'seconds': round(time.time() - t0, 1),
              'messages': sum(counts.values()), 'storage': p.get('storage') or 'sqlite3',
              'topics': [{'topic': t, 'type': 'sensor_msgs/msg/PointCloud2' if t in points else types[t],
                          'source_type': types[t], 'count': n, 'points': points.get(t)}
                         for t, n in sorted(counts.items())]}
    result_path = os.path.join(job, 'result.json')
    result_tmp = result_path + '.tmp'
    with open(result_tmp, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(result_tmp, result_path)


if __name__ == '__main__':
    main()
