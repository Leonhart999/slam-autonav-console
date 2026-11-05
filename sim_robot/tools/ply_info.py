#!/usr/bin/env python3
"""Read PLY meshes (ascii or binary, polygon faces) and print counts and bounding boxes.
read_ply() returns vertices (N,3) and triangles (M,3); polygons are fan-triangulated.
Usage: python3 ply_info.py a.ply [b.ply ...]"""
import sys
import numpy as np

TYPES = {'char': 'i1', 'uchar': 'u1', 'short': 'i2', 'ushort': 'u2', 'int': 'i4', 'uint': 'u4',
         'float': 'f4', 'double': 'f8', 'int8': 'i1', 'uint8': 'u1', 'int16': 'i2', 'uint16': 'u2',
         'int32': 'i4', 'uint32': 'u4', 'float32': 'f4', 'float64': 'f8'}


def _fan(polys):
    tris = []
    for p in polys:
        for k in range(1, len(p) - 1):
            tris.append((p[0], p[k], p[k + 1]))
    return np.array(tris, dtype=np.int64).reshape(-1, 3)


def read_ply(path):
    f = open(path, 'rb')
    assert f.readline().strip() == b'ply'
    fmt, elems = None, []
    while True:
        line = f.readline().decode('ascii', 'replace').strip()
        if line.startswith('format'):
            fmt = line.split()[1]
        elif line.startswith('element'):
            _, name, n = line.split()
            elems.append([name, int(n), []])
        elif line.startswith('property'):
            elems[-1][2].append(line.split()[1:])
        elif line == 'end_header':
            break
    verts, polys = None, []
    if fmt == 'ascii':
        text = f.read().split(b'\n')
        pos = 0
        for name, n, _ in elems:
            rows = text[pos:pos + n]
            pos += n
            if name == 'vertex':
                verts = np.array([r.split()[:3] for r in rows], dtype=float)
            elif name == 'face':
                for r in rows:
                    vals = list(map(int, r.split()))
                    polys.append(vals[1:1 + vals[0]])
        return verts, _fan(polys)
    end = '<' if fmt == 'binary_little_endian' else '>'
    buf = f.read()
    off = 0
    for name, n, props in elems:
        if name == 'vertex':
            dt = np.dtype([(p[1], end + TYPES[p[0]]) for p in props])
            v = np.frombuffer(buf, dtype=dt, count=n, offset=off)
            off += n * dt.itemsize
            verts = np.c_[v['x'], v['y'], v['z']].astype(float)
        elif name == 'face':
            p = props[0]
            ct, it = np.dtype(end + TYPES[p[1]]), np.dtype(end + TYPES[p[2]])
            for _ in range(n):
                c = int(np.frombuffer(buf, ct, 1, off)[0])
                off += ct.itemsize
                polys.append(np.frombuffer(buf, it, c, off).tolist())
                off += c * it.itemsize
    return verts, _fan(polys)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        v, t = read_ply(path)
        lo, hi = v.min(0), v.max(0)
        print(f'{path.split("BIM/")[-1]:24s} verts {len(v):8d} tris {len(t):8d}  '
              f'x {lo[0]:8.2f}..{hi[0]:8.2f}  y {lo[1]:8.2f}..{hi[1]:8.2f}  z {lo[2]:7.2f}..{hi[2]:7.2f}')
