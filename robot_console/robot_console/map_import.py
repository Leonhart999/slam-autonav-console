"""导航地图导入：只读取上传数据，规范化后发布到地图库，不执行其中的代码。"""
import json
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .map_library import write_xyz_pcd

IMPORT_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,48}$')
MIB = 1024 * 1024
MAX_REQUEST = 384 * MIB
MAX_CLOUD_POINTS = 5_000_000
MAX_BINARY_CLOUD_BYTES = 256 * MIB


class MapImportError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def invalid(reason):
    raise MapImportError('import_invalid', reason=str(reason))


def read_cloud(path, *, max_binary_xyz_points=MAX_CLOUD_POINTS):
    """Read bounded ASCII/binary PCD as float32 XYZ; only packed binary XYZ can opt into a larger limit."""
    header = {}
    with open(path, 'rb') as f:
        consumed = 0
        for _ in range(100):
            line = f.readline(65537)
            consumed += len(line)
            if consumed > 65536 or not line:
                invalid('PCD header is missing DATA or exceeds 64 KiB')
            parts = line.decode('ascii').strip().split()
            if not parts or parts[0].startswith('#'):
                continue
            header[parts[0].upper()] = parts[1:]
            if parts[0].upper() == 'DATA':
                break
        else:
            invalid('PCD header is too long')
        fields = header['FIELDS']
        sizes = list(map(int, header['SIZE']))
        types = header['TYPE']
        counts = list(map(int, header.get('COUNT', ['1'] * len(fields))))
        n = int(header['POINTS'][0])
        if not 3 <= len(fields) <= 64:
            invalid('PCD must contain 3 to 64 fields')
        if len(set(fields)) != len(fields) or not (len(fields) == len(sizes) == len(types) == len(counts)):
            invalid('PCD field declarations do not match')
        encoding = header['DATA'][0].lower()
        packed_xyz = (encoding == 'binary' and fields == ['x', 'y', 'z'] and sizes == [4, 4, 4]
                      and types == ['F', 'F', 'F'] and counts == [1, 1, 1])
        max_points = min(max_binary_xyz_points, MAX_BINARY_CLOUD_BYTES // 12) if packed_xyz else MAX_CLOUD_POINTS
        if not 0 < n <= max_points:
            invalid(f'PCD declares {n:,} points; this format requires 1 to {max_points:,} points')
        if any(c < 1 or c > 64 for c in counts) or (not packed_xyz and sum(counts) * n > 24_000_000):
            invalid('PCD contains too many values')
        if any(k not in fields or counts[fields.index(k)] != 1 for k in ('x', 'y', 'z')):
            invalid('PCD requires scalar x, y, z fields')
        if packed_xyz:
            if os.fstat(f.fileno()).st_size - f.tell() != n * 12:
                invalid('PCD binary payload size does not match POINTS')
            xyz = np.fromfile(f, dtype='<f4', count=n * 3)
            if xyz.size != n * 3:
                invalid('PCD binary payload size does not match POINTS')
            xyz = xyz.reshape(n, 3)
        elif encoding == 'binary':
            kinds = {'F': 'f', 'I': 'i', 'U': 'u'}
            descr = []
            for key, size, kind, count in zip(fields, sizes, types, counts):
                if kind not in kinds or size not in (1, 2, 4, 8) or (kind == 'F' and size not in (4, 8)):
                    invalid('Unsupported PCD field type')
                descr.append((key, '<' + kinds[kind] + str(size), (count,)) if count > 1
                             else (key, '<' + kinds[kind] + str(size)))
            dtype = np.dtype(descr)
            if n * dtype.itemsize > MAX_BINARY_CLOUD_BYTES:
                invalid('PCD binary data exceeds 256 MiB')
            raw = f.read(n * dtype.itemsize + 1)
            if len(raw) != n * dtype.itemsize:
                invalid('PCD binary payload size does not match POINTS')
            data = np.frombuffer(raw, dtype=dtype, count=n)
            xyz = np.column_stack([data[k] for k in ('x', 'y', 'z')]).astype(np.float32)
        elif encoding == 'ascii':
            columns = np.cumsum([0, *counts[:-1]])
            usecols = [int(columns[fields.index(k)]) for k in ('x', 'y', 'z')]
            xyz = np.loadtxt(f, dtype=np.float32, usecols=usecols, ndmin=2, max_rows=n + 1)
            if xyz.shape != (n, 3):
                invalid('PCD ASCII row count does not match POINTS')
        else:
            invalid('Only ASCII and binary PCD are supported; binary_compressed is not supported')
    finite = np.isfinite(xyz).all(axis=1)
    if not finite.all():
        xyz = xyz[finite]
    if not len(xyz) or xyz.min() < -1_000_000 or xyz.max() > 1_000_000:
        invalid('PCD has no finite points or coordinates exceed 1,000,000 m')
    return xyz


def import_navigation(root, name, yaml_path, image_path, image_filename, cloud_path=None, preset=None,
                      auto_grid=None):
    """Normalize uploads; publish map.yaml (2D) or meta.json (cloud-only) last.

    auto_grid: for a cloud without a YAML/image pair, callable(cloud, out_dir, (dx, dy)) that projects the cloud
    into out_dir/map.pgm + map.yaml and returns the projection record (map_regrid.run_worker). The map then has a
    2D grid right away; when the projection fails the cloud is still imported and meta.json keeps the error."""
    has_grid = yaml_path is not None
    if has_grid != (image_path is not None):
        invalid('Map YAML and image must be supplied together')
    if not has_grid and cloud_path is None:
        invalid('Supply a map YAML/image pair or a PCD cloud')
    if not IMPORT_NAME_RE.fullmatch(name or ''):
        raise MapImportError('import_name_invalid', name=name)
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / name
    if dest.exists() or (root / (name + '.yaml')).exists():
        raise MapImportError('import_exists', name=name)
    published = False
    try:
        grid = None
        files = {}
        with tempfile.TemporaryDirectory(prefix='.import-', dir=root) as tmp:
            stage = Path(tmp)
            if has_grid:
                info = yaml.safe_load(Path(yaml_path).read_text(encoding='utf-8-sig'))
                if not isinstance(info, dict):
                    invalid('Map YAML must be a mapping')
                image_ref = info.get('image')
                if not isinstance(image_ref, str) or not image_ref.strip():
                    invalid('Map YAML requires an image field')
                # Only compare the basename; never access paths from uploaded YAML.
                basename = image_ref.replace('\\', '/').rsplit('/', 1)[-1]
                if basename != image_filename:
                    invalid(f'YAML image is {basename}; the selected image is {image_filename}')
                res = float(info['resolution'])
                if not isinstance(info.get('origin'), list) or len(info['origin']) != 3:
                    invalid('Map origin must be a list of 3 numbers')
                origin = [float(v) for v in info['origin']]
                free = float(info.get('free_thresh', 0.25))
                occupied = float(info.get('occupied_thresh', 0.65))
                if not all(math.isfinite(v) for v in (res, *origin, free, occupied)):
                    invalid('Map resolution, origin and thresholds must be finite; origin needs 3 values')
                if not (0 < res <= 100 and 0 <= free < occupied <= 1):
                    invalid('Require 0 < resolution <= 100 and 0 <= free_thresh < occupied_thresh <= 1')
                negate = info.get('negate', 0)
                if negate not in (0, 1):
                    invalid('Map negate must be 0 or 1')
                mode = info.get('mode', 'trinary')
                if mode not in ('trinary', 'scale', 'raw'):
                    invalid('Map mode must be trinary, scale or raw')
                with Image.open(image_path) as im:
                    if im.format not in ('PNG', 'PPM'):
                        invalid('Map image must be a PGM or PNG file')
                    w, h = im.size
                    if w < 1 or h < 1 or w * h > 25000000:
                        invalid('Map image must contain 1 to 25,000,000 pixels')
                    if im.mode.startswith('I') or im.mode == 'F':
                        invalid('Map image must use 8-bit channels; convert 16-bit PNG/PGM first')
                    im.load()
                    # Preserve color and alpha for Nav2 occupancy interpretation.
                    normalized = im.convert('RGBA' if 'A' in im.getbands() or 'transparency' in im.info else 'RGB')
                    normalized.save(stage / 'map.png')
                    preview = normalized.convert('RGBA')
                    background = Image.new('RGBA', preview.size, (205, 205, 205, 255))
                    background.alpha_composite(preview)
                    background.convert('L').save(stage / 'map_preview.png')
                grid_yaml = {'image': 'map.png', 'mode': mode, 'resolution': res, 'origin': origin,
                             'negate': int(negate), 'occupied_thresh': occupied, 'free_thresh': free}
                (stage / 'map.yaml').write_text(yaml.safe_dump(grid_yaml, sort_keys=False), encoding='utf-8')
                grid = {'width': w, 'height': h, 'resolution': res, 'origin': origin,
                        'size_m': [round(w * res, 2), round(h * res, 2)]}
                files.update({'map.yaml': 'Nav2 map metadata', 'map.png': 'Occupancy image',
                              'map_preview.png': 'Map preview'})
            points = preview_points = None
            if cloud_path is not None:
                xyz = read_cloud(cloud_path)
                points = len(xyz)
                write_xyz_pcd(stage / 'map.pcd', xyz)
                preview_cloud = xyz[::max(1, math.ceil(len(xyz) / 200000))]
                preview_points = len(preview_cloud)
                write_xyz_pcd(stage / 'map_voxel.pcd', preview_cloud)
            auto = None
            if cloud_path is not None and not has_grid and auto_grid is not None:
                auto = {'ok': False, 'error': None, 'warnings': [], 'resolution': None}
                out = stage / '.grid'
                out.mkdir()
                try:
                    extent = (xyz[:, 0].max() - xyz[:, 0].min(), xyz[:, 1].max() - xyz[:, 1].min())
                    record = auto_grid(stage / 'map.pcd', out, extent)
                    os.rename(out / 'map.pgm', stage / 'map.pgm')
                    os.rename(out / 'map.yaml', stage / 'map.yaml')
                    with Image.open(stage / 'map.pgm') as im:
                        im.save(stage / 'map_preview.png')
                    grid = record['grid']
                    auto.update(ok=True, warnings=list(record.get('warnings') or []),
                                resolution=grid['resolution'])
                    files.update({'map.pgm': 'Occupancy grid projected from the cloud (pcd2grid)',
                                  'map.yaml': 'Nav2 map metadata', 'map_preview.png': 'Map preview'})
                except Exception as exc:        # the cloud is still imported; the Maps page offers a regrid
                    record = None
                    auto['error'] = str(exc) or type(exc).__name__
                shutil.rmtree(out, ignore_errors=True)
            meta = {'name': name, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': 'upload',
                    'algorithm': None, 'frame': 'map', 'preset': preset, 'grid': grid,
                    'points': points, 'voxels': None, 'preview_points': preview_points,
                    'files': files}
            if auto is not None:
                meta['project_error'] = auto['error']
                if record is not None:
                    meta['regrid'] = {**record, 'auto': True}
            if cloud_path is not None:
                meta['files'].update({'map.pcd': 'Imported XYZ cloud', 'map_voxel.pcd': 'Sampled cloud preview'})
            (stage / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
            # mkdir 是互斥点：和保存地图并发时也不会替换另一个任务新建的目录。
            try:
                dest.mkdir()
            except FileExistsError:
                raise MapImportError('import_exists', name=name)
            published = True
            marker = 'map.yaml' if (stage / 'map.yaml').exists() else 'meta.json'
            for file in stage.iterdir():
                if file.name != marker:
                    os.rename(file, dest / file.name)
            os.rename(stage / marker, dest / marker)
        return {'name': name, 'has_cloud': cloud_path is not None, 'has_solid': False, 'preset': preset,
                'grid': {k: v for k, v in grid.items() if k != 'size_m'} if grid is not None else None,
                'auto_grid': auto}
    except MapImportError:
        if published:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            Image.DecompressionBombError, yaml.YAMLError) as exc:
        if published:
            shutil.rmtree(dest, ignore_errors=True)
        raise MapImportError('import_invalid', reason=str(exc)) from exc
