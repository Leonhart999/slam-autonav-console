"""Map management page (step 4a): read-only catalog of the map library (~/maps).

Lists every map with its metadata, checks completeness (2D grid, 3D cloud, scene, meta.json) and
describes its files, so problems show up here instead of when navigation is started.
Nothing in the library is modified.

Map layouts that are recognized:
  ~/maps/<name>/      folder map (saved by the mapping page or imported): map.yaml, map.pcd, meta.json, ...
  ~/maps/<name>.yaml  legacy flat map (2D only, written by pcd_to_grid before the console existed)
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .map_library import NAME_RE, pcd_points

# Linux path of the WSL home as seen from Windows Explorer (shown so files are easy to find)
WSL_DISTRO = os.environ.get('WSL_DISTRO_NAME', 'Ubuntu-22.04')
CLOUD_FILES = ('map_voxel.pcd', 'map.pcd')
PREVIEW_POINTS = 200000

# Known files and folders in a map folder; the description is translated by the web page
KNOWN = {
    'map.pcd': 'dense_cloud', 'map_voxel.pcd': 'voxel_cloud', 'map.yaml': 'grid_yaml', 'map.pgm': 'grid_image',
    'map.png': 'grid_image', 'map_preview.png': 'grid_preview', 'meta.json': 'meta',
    'figures': 'figures', 'data': 'data', 'goals.yaml': 'goals',
}


class CatalogError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


def windows_path(p):
    return '\\\\wsl.localhost\\' + WSL_DISTRO + str(p).replace('/', '\\')


def dir_size(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class MapCatalog:

    def __init__(self, root, solids=None):
        self.root = Path(root).expanduser().resolve()
        self.solids = solids

    # ---------------- locating maps ----------------

    def _entries(self):
        """{name: (kind, path)}; kind = 'folder' or 'flat'."""
        found = {}
        if not self.root.is_dir():
            return found
        for p in sorted(self.root.iterdir()):
            if p.name.startswith('.') or not NAME_RE.fullmatch(p.stem if p.is_file() else p.name):
                continue
            if p.is_symlink():
                continue
            if p.is_dir():
                if any((p / f).is_file() for f in ('map.yaml', 'meta.json')):
                    found[p.name] = ('folder', p)
            elif p.suffix == '.yaml' and p.stem not in found:
                found[p.stem] = ('flat', p)
        return found

    def _locate(self, name):
        if not NAME_RE.fullmatch(name or ''):
            raise CatalogError('invalid_map_name', name=name)
        entry = self._entries().get(name)
        if entry is None:
            raise CatalogError('map_not_found', name=name)
        return entry

    # ---------------- reading one map ----------------

    def _grid(self, yaml_path):
        """Grid info from a map_server YAML; (grid, error)."""
        try:
            info = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
            image = (yaml_path.parent / info['image']).resolve()
            if not image.is_relative_to(self.root) or not image.is_file():
                raise ValueError(f"image {info['image']} not found")
            res = float(info['resolution'])
            origin = [float(v) for v in info['origin']]
            if res <= 0 or len(origin) != 3 or not all(math.isfinite(v) for v in origin):
                raise ValueError('invalid resolution or origin')
            with Image.open(image) as im:
                w, h = im.size
            return {'resolution': res, 'width': w, 'height': h, 'origin': origin,
                    'size_m': [round(w * res, 2), round(h * res, 2)], 'image': image.name}, None
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as e:
            return None, str(e)

    def _summary(self, name, kind, path):
        folder = path if kind == 'folder' else None
        meta, meta_error = {}, None
        if folder and (folder / 'meta.json').is_file():
            try:
                meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
                if not isinstance(meta, dict):
                    meta, meta_error = {}, 'meta.json is not an object'
            except (OSError, ValueError) as e:
                meta_error = str(e)
        yaml_path = folder / 'map.yaml' if folder else path
        grid, grid_error = (self._grid(yaml_path) if yaml_path.is_file() else (None, None))
        cloud = next((folder / f for f in CLOUD_FILES if (folder / f).is_file()), None) if folder else None
        dense = folder / 'map.pcd' if folder and (folder / 'map.pcd').is_file() else None
        points = meta.get('points')
        if points is None and dense is not None:
            try:
                points = pcd_points(dense)
            except (OSError, ValueError):
                points = None
        stamp = meta.get('created') or time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime((folder or path).stat().st_mtime))
        preset = meta.get('preset')

        # Completeness: problems that stop navigation are 'error', others 'warn' or 'info'
        issues = []
        if grid is None:
            issues.append({'code': 'grid_invalid' if grid_error else 'no_grid', 'level': 'error',
                           'params': {'reason': grid_error or ''}})
        if cloud is None:
            issues.append({'code': 'only_2d' if grid else 'no_cloud', 'level': 'warn', 'params': {}})
        if not preset:
            issues.append({'code': 'no_scene', 'level': 'warn', 'params': {}})
        if kind == 'flat':
            issues.append({'code': 'legacy_flat', 'level': 'info', 'params': {}})
        elif meta_error:
            issues.append({'code': 'meta_invalid', 'level': 'warn', 'params': {'reason': meta_error}})
        elif not meta:
            issues.append({'code': 'no_meta', 'level': 'warn', 'params': {}})
        if meta.get('project_error'):
            issues.append({'code': 'project_error', 'level': 'warn', 'params': {'reason': meta['project_error']}})
        if meta.get('figures_error'):
            issues.append({'code': 'figures_error', 'level': 'info', 'params': {'reason': meta['figures_error']}})

        return {
            'name': name, 'kind': kind, 'created': stamp, 'source': meta.get('source', 'mapping' if meta else None),
            'algorithm': meta.get('algorithm'), 'preset': preset, 'points': points,
            'voxels': meta.get('voxels'), 'has_grid': grid is not None, 'has_cloud': cloud is not None,
            'has_solid': bool(self.solids and grid and kind == 'folder' and self.solids.get(preset)),
            'grid': grid, 'extent': meta.get('extent'), 'navigable': grid is not None,
            'issues': issues,
            '_folder': folder, '_path': path, '_meta': meta, '_cloud': cloud,
        }

    @staticmethod
    def _public(s):
        return {k: v for k, v in s.items() if not k.startswith('_')}

    # ---------------- API ----------------

    def list(self):
        out = []
        for name, (kind, path) in self._entries().items():
            try:
                out.append(self._public(self._summary(name, kind, path)))
            except OSError:
                continue
        out.sort(key=lambda m: m['created'] or '', reverse=True)
        return out

    def detail(self, name):
        kind, path = self._locate(name)
        s = self._summary(name, kind, path)
        meta = s['_meta']
        files = []
        if kind == 'folder':
            location = path
            for p in sorted(path.iterdir(), key=lambda p: (p.is_dir(), p.name)):
                if p.name.startswith('.'):
                    continue
                is_dir = p.is_dir()
                files.append({'name': p.name + ('/' if is_dir else ''), 'dir': is_dir,
                              'size': dir_size(p) if is_dir else p.stat().st_size,
                              'count': sum(len(f) for _, _, f in os.walk(p)) if is_dir else None,
                              'role': KNOWN.get(p.name)})
        else:
            # Flat map: the YAML plus its image and preview, all next to each other in ~/maps
            location = path.parent
            names = [path.name]
            if s['grid']:
                names.append(s['grid']['image'])
            names.append(path.stem + '_preview.png')
            for n in names:
                p = path.parent / n
                if p.is_file():
                    files.append({'name': n, 'dir': False, 'size': p.stat().st_size, 'count': None,
                                  'role': 'grid_yaml' if n.endswith('.yaml') else
                                  'grid_preview' if n.endswith('_preview.png') else 'grid_image'})
        info = {k: meta.get(k) for k in ('frame', 'run', 'duration', 'length', 'voxel_size', 'sim_cmd',
                                         'slam_cmd')}
        return {**self._public(s), **info,
                'location': str(location), 'location_win': windows_path(location),
                'yaml_path': str(s['_folder'] / 'map.yaml' if s['_folder'] else path) if s['has_grid'] else None,
                'files': files, 'total_size': sum(f['size'] for f in files)}

    def cloud(self, name):
        """Up to PREVIEW_POINTS xyz points as little-endian float32 bytes (works for maps without a grid)."""
        from lidar_nav.pcd_to_grid import read_pcd_xyz
        kind, path = self._locate(name)
        s = self._summary(name, kind, path)
        if s['_cloud'] is None:
            raise CatalogError('navigation_cloud_missing', name=name)
        pts = read_pcd_xyz(str(s['_cloud']))
        pts = pts[np.isfinite(pts).all(axis=1)]
        pts = pts[::max(1, math.ceil(len(pts) / PREVIEW_POINTS))]
        return np.asarray(pts, dtype='<f4').tobytes()


def register_map_routes(app, cfg, solids=None, library=None, procs=None):
    """GET /api/maps, /api/maps/{name}, /api/maps/{name}/cloud (2D image comes from the navigation API)."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    catalog = MapCatalog(cfg['mapping'].get('maps_dir', '~/maps'), solids)
    from .map_trash import register_trash_routes
    from .map_regrid import register_regrid_routes
    register_trash_routes(app, catalog, library, procs, cfg)
    register_regrid_routes(app, catalog, cfg)

    def fail(e):
        status = 404 if e.code in ('map_not_found', 'navigation_cloud_missing') else 400
        return HTTPException(status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/maps')
    def maps_list():
        return catalog.list()

    @app.get('/api/maps/{name}')
    def maps_detail(name: str):
        try:
            return catalog.detail(name)
        except CatalogError as e:
            raise fail(e)

    @app.get('/api/maps/{name}/cloud')
    def maps_cloud(name: str):
        try:
            data = catalog.cloud(name)
        except CatalogError as e:
            raise fail(e)
        except (OSError, ValueError, KeyError, AssertionError) as e:
            raise HTTPException(400, detail={'code': 'navigation_map_invalid', 'params': {'name': name,
                                                                                           'reason': str(e)}})
        return Response(data, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    return catalog
