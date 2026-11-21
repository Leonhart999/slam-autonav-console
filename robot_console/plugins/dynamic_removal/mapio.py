"""Map, scan and trajectory files of the console, read with numpy only (runs in a plugin's own venv).

The same module is shipped in ground_seg and dynamic_removal so each plugin folder stands alone.

  read_pcd_xyz / write_pcd_xyz   PCD (ascii, binary) <-> (N, 3) float32
  read_tum                       TUM trajectory: stamps (N,), poses (N, 7) = x y z qx qy qz qw
  ScanRecording                  scans.bin + index.csv written by the console's scan recorder
                                 (fastlio_loop_record.py: float32 x y z intensity per point, body frame)
  read_grid                      map.yaml + image -> geometry and the 0/205/254 cells as map_server reads them
  rotations                      quaternions (x y z w) -> rotation matrices
"""
import json
import os
import sys

import numpy as np
import yaml

OCCUPIED, UNKNOWN, FREE = 0, 205, 254


def emit(**msg):
    """A progress line for the console's job runner."""
    print(json.dumps(msg), flush=True)


def fail(job, plugin, code, **params):
    """Stop the job with a coded error, translated by the plugin's messages (errors.<code>)."""
    with open(os.path.join(job, 'error.json'), 'w', encoding='utf-8') as f:
        json.dump({'code': f'plugin.{plugin}.{code}', 'params': params}, f)
    sys.exit(1)


def read_pcd_xyz(path):
    header = {}
    with open(path, 'rb') as f:
        for _ in range(64):
            line = f.readline()
            if not line:
                break
            parts = line.decode('ascii', 'replace').strip().split()
            if not parts or parts[0].startswith('#'):
                continue
            header[parts[0].upper()] = parts[1:]
            if parts[0].upper() == 'DATA':
                break
        for req in ('FIELDS', 'SIZE', 'TYPE', 'POINTS', 'DATA'):
            if req not in header:
                raise ValueError(f'PCD header missing {req}')
        fields = header['FIELDS']
        sizes = [int(v) for v in header['SIZE']]
        types = header['TYPE']
        counts = [int(v) for v in header.get('COUNT', ['1'] * len(fields))]
        n = int(header['POINTS'][0])
        kind = header['DATA'][0].lower()
        if n == 0:
            return np.empty((0, 3), dtype=np.float32)
        if kind == 'binary':
            names = {}
            descr = []
            for i, (name, size, typ, count) in enumerate(zip(fields, sizes, types, counts)):
                key = name if name not in names and name != '_' else f'_pad{i}'
                names[key] = True
                code = {'F': 'f', 'I': 'i', 'U': 'u'}[typ] + str(size)
                descr.append((key, '<' + code, (count,)) if count > 1 else (key, '<' + code))
            dtype = np.dtype(descr)
            data = np.frombuffer(f.read(n * dtype.itemsize), dtype=dtype, count=n)
            xyz = np.column_stack([data['x'], data['y'], data['z']]).astype(np.float32)
        elif kind == 'ascii':
            cols = np.cumsum([0] + counts[:-1])
            use = [int(cols[fields.index(k)]) for k in ('x', 'y', 'z')]
            xyz = np.loadtxt(f, dtype=np.float32, usecols=use, ndmin=2, max_rows=n)
            if xyz.size == 0:
                return np.empty((0, 3), dtype=np.float32)
        else:
            raise ValueError(f'PCD DATA {kind} is not supported (only ascii and binary)')
    return xyz[np.isfinite(xyz).all(axis=1)]


def write_pcd_xyz(path, xyz):
    xyz = np.ascontiguousarray(xyz, dtype='<f4')
    n = len(xyz)
    header = (f'# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n'
              f'COUNT 1 1 1\nWIDTH {n}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {n}\nDATA binary\n')
    path = str(path)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(header.encode('ascii'))
        f.write(xyz.tobytes())
    os.replace(tmp, path)


def read_tum(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 8 and not parts[0].startswith('#'):
                try:
                    rows.append([float(v) for v in parts[:8]])
                except ValueError:
                    continue
    a = np.array(rows, dtype=np.float64).reshape(-1, 8)
    return a[:, 0], a[:, 1:8]


def rotations(q):
    """(N, 4) quaternions x y z w -> (N, 3, 3) rotation matrices (normalised first)."""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], axis=1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], axis=1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=1),
    ], axis=1)


class ScanRecording:
    """Scans of a recording with the poses of the map's trajectory (matched by time stamp)."""

    def __init__(self, recording, trajectory, max_dt=0.005):
        idx = np.loadtxt(os.path.join(recording, 'index.csv'), dtype=np.float64, ndmin=2)
        if idx.size == 0:
            raise ValueError('the scan recording is empty')
        size = os.path.getsize(os.path.join(recording, 'scans.bin')) // 16
        if size == 0:
            raise ValueError('the scan recording is empty')
        idx = idx[(idx[:, 1] + idx[:, 2]) <= size]           # a scan cut off at the end of the file is skipped
        self.points = np.memmap(os.path.join(recording, 'scans.bin'), dtype='<f4', mode='r', shape=(size, 4))
        stamps, poses = read_tum(trajectory)
        if not len(stamps):
            raise ValueError('the trajectory is empty')
        order = np.argsort(stamps)
        stamps, poses = stamps[order], poses[order]
        pos = np.clip(np.searchsorted(stamps, idx[:, 0]), 1, len(stamps) - 1) if len(stamps) > 1 else \
            np.zeros(len(idx), dtype=int)
        if len(stamps) > 1:
            left = np.abs(stamps[pos - 1] - idx[:, 0]) <= np.abs(stamps[pos] - idx[:, 0])
            pos = np.where(left, pos - 1, pos)
        ok = np.abs(stamps[pos] - idx[:, 0]) <= max_dt
        self.index = idx[ok]
        self.poses = poses[pos[ok]]
        self.unmatched = int((~ok).sum())

    def __len__(self):
        return len(self.index)

    def scan(self, i):
        """(points (n, 4) float32 in the body frame, pose x y z qx qy qz qw)"""
        off, n = int(self.index[i, 1]), int(self.index[i, 2])
        return np.asarray(self.points[off:off + n]), self.poses[i]


def read_grid(map_yaml):
    """Geometry and cells of a map: dict(res, origin, width, height) and the (h, w) uint8 array of
    0 / 205 / 254 as nav2 map_server reads the image with this map.yaml (row 0 = top = largest y)."""
    from PIL import Image
    with open(map_yaml, encoding='utf-8') as f:
        info = yaml.safe_load(f)
    image = os.path.join(os.path.dirname(map_yaml), info['image'])
    with Image.open(image) as im:
        raw = np.asarray(im.convert('L')).astype(np.float32)
    p = raw / 255.0 if int(info.get('negate', 0)) else (255.0 - raw) / 255.0
    cells = np.full(raw.shape, UNKNOWN, dtype=np.uint8)
    cells[p > float(info.get('occupied_thresh', 0.65))] = OCCUPIED
    cells[p < float(info.get('free_thresh', 0.25))] = FREE
    h, w = cells.shape
    return {'res': float(info['resolution']), 'origin': [float(v) for v in info['origin'][:2]],
            'width': w, 'height': h}, cells


def cell_index(xy, grid):
    """Flat cell index (row 0 = bottom = smallest y) of every point and a mask of the points inside the grid."""
    cx = np.floor((xy[:, 0] - grid['origin'][0]) / grid['res']).astype(np.int64)
    cy = np.floor((xy[:, 1] - grid['origin'][1]) / grid['res']).astype(np.int64)
    inside = (cx >= 0) & (cx < grid['width']) & (cy >= 0) & (cy < grid['height'])
    return cy * grid['width'] + cx, inside


def save_png(path, array):
    from PIL import Image
    path = str(path)
    tmp = path + '.tmp.png'
    Image.fromarray(np.ascontiguousarray(array)).save(tmp)
    os.replace(tmp, path)
