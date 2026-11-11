"""PointCloud2 layouts and incremental frame consistency across clients and resets."""
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from sensor_msgs.msg import PointCloud2, PointField

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import mapping


def cloud(big=False):
    return PointCloud2(height=2, width=2, point_step=12, row_step=36, is_bigendian=big,
                      fields=[PointField(name=k, offset=i * 4, datatype=7, count=1)
                              for i, k in enumerate('xyz')],
                      data=struct.pack(('>' if big else '<') + '18f',
                                       1, 2, 3, 4, 5, 6, 0, 0, 0, 7, 8, 9, 10, 11, 12, 0, 0, 0))


def header(frame):
    return json.loads(frame[4:4 + struct.unpack('<I', frame[:4])[0]])


class CloudTests(unittest.TestCase):
    def test_row_padding_and_byte_order(self):
        for big in (False, True):
            np.testing.assert_array_equal(mapping.cloud_xyz(cloud(big)),
                                          [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]])

    def test_empty_cloud_and_invalid_row(self):
        msg = cloud()
        msg.width = 0
        self.assertEqual(mapping.cloud_xyz(msg).shape, (0, 3))
        msg = cloud()
        msg.row_step = 12
        with self.assertRaises(ValueError):
            mapping.cloud_xyz(msg)


class MappingStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        node = SimpleNamespace(get_logger=lambda: SimpleNamespace(warn=lambda *a: None),
                               create_subscription=lambda *a, **kw: None,
                               create_timer=lambda *a: None)
        self.data = mapping.MappingData(node, {'runs_dir': self.tmp.name})

    def tearDown(self):
        self.data.close()
        self.tmp.cleanup()

    def test_clients_reuse_scan_bytes_and_keep_separate_cursors(self):
        self.data._process(cloud(), self.data.epoch, self.data._keys)
        a, b = {}, {}
        first, second = self.data.pull(a), self.data.pull(b)
        self.assertEqual([header(f)['type'] for f in first if isinstance(f, bytes)], ['map', 'scan'])
        self.assertIs(first[-1], second[-1])
        self.assertEqual(self.data.pull(a), [])
        self.data.reset()
        self.assertEqual(self.data.pull(a), [{'type': 'mapping_reset', 'epoch': self.data.epoch}])

    def test_encoding_releases_mapping_lock_and_survives_reset(self):
        self.data._map.append(np.array([[1, 2, 3]], dtype=np.float32))
        old_epoch = self.data.epoch
        original = mapping.encode
        def encode(head, points):
            self.assertTrue(self.data._lock.acquire(blocking=False))
            self.data._lock.release()
            self.data.reset()
            return original(head, points)
        with patch.object(mapping, 'encode', encode):
            frames = self.data.pull({})
        self.assertEqual(header(frames[1])['epoch'], old_epoch)
        self.assertEqual(header(frames[1])['count'], 1)
