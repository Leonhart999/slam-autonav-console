"""Livox CustomMsg (ROS1 serialization) -> PointCloud2 point buffer. numpy only, shared by the inspector and the
conversion worker (and the console's unit tests).

ROS1 layout of livox_ros_driver/CustomMsg and livox_ros_driver2/CustomMsg (identical):
  std_msgs/Header header   uint32 seq, time stamp (uint32 sec, uint32 nsec), string frame_id (uint32 length + bytes)
  uint64 timebase          ns, time of the first point
  uint32 point_num
  uint8 lidar_id
  uint8[3] rsvd            fixed-size array: no length prefix
  CustomPoint[] points     uint32 count, then 19 packed bytes per point:
                           uint32 offset_time (ns from timebase), float32 x y z, uint8 reflectivity tag line

Output point (24 bytes, little endian): x y z intensity (float32, offsets 0 4 8 12), time (float32 s relative to the
header stamp, offset 16), ring (uint16 = line, offset 20), tag (uint8, offset 22), 1 byte padding.
"""
import struct

import numpy as np

LIVOX_TYPES = ('livox_ros_driver/msg/CustomMsg', 'livox_ros_driver2/msg/CustomMsg')
POINT = np.dtype([('t', '<u4'), ('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('r', 'u1'), ('tag', 'u1'),
                  ('line', 'u1')])
OUT = np.dtype({'names': ['x', 'y', 'z', 'intensity', 'time', 'ring', 'tag'],
                'formats': ['<f4', '<f4', '<f4', '<f4', '<f4', '<u2', 'u1'],
                'offsets': [0, 4, 8, 12, 16, 20, 22], 'itemsize': 24})
# sensor_msgs/PointField datatypes
FLOAT32, UINT16, UINT8 = 7, 4, 2
FIELDS = [('x', 0, FLOAT32), ('y', 4, FLOAT32), ('z', 8, FLOAT32), ('intensity', 12, FLOAT32),
          ('time', 16, FLOAT32), ('ring', 20, UINT16), ('tag', 22, UINT8)]


def parse_ros1(raw):
    """(sec, nsec, frame_id, timebase ns, points structured array) of one ROS1-serialized CustomMsg."""
    raw = memoryview(raw)
    _seq, sec, nsec, flen = struct.unpack_from('<IIII', raw, 0)
    off = 16 + flen
    frame = bytes(raw[16:off]).decode('utf-8', 'replace')
    timebase, _num, _lidar = struct.unpack_from('<QIB', raw, off)
    off += 8 + 4 + 1 + 3
    (count,) = struct.unpack_from('<I', raw, off)
    off += 4
    if off + count * POINT.itemsize > len(raw):
        raise ValueError(f'CustomMsg says {count} points but has room for {(len(raw) - off) // POINT.itemsize}')
    pts = np.frombuffer(raw, dtype=POINT, count=count, offset=off)
    return sec, nsec, frame, timebase, pts


def to_cloud(sec, nsec, timebase, pts):
    """24-byte points (OUT) of the non-zero points, time relative to the header stamp (timebase when the stamp
    is zero)."""
    keep = (pts['x'] != 0) | (pts['y'] != 0) | (pts['z'] != 0)
    pts = pts[keep]
    stamp = sec * 1_000_000_000 + nsec or timebase
    out = np.zeros(len(pts), dtype=OUT)
    out['x'], out['y'], out['z'] = pts['x'], pts['y'], pts['z']
    out['intensity'] = pts['r']
    out['time'] = ((timebase - stamp) + pts['t'].astype(np.int64)) * 1e-9
    out['ring'] = pts['line']
    out['tag'] = pts['tag']
    return out
