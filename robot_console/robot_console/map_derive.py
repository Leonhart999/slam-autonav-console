"""New map versions made by tools (plugins): a folder map with a replacement 2D grid and/or point cloud.

POST /api/maps/{name}/derive  (multipart/form-data)
  new_name   name of the new version (required; must not exist)
  tool       who made it, e.g. "Patchwork++ ground segmentation" (shown in meta.json, at most 120 characters)
  note       optional JSON object with the tool's parameters and statistics (at most 16 KiB)
  grid_mode  keep     the source's 2D grid unchanged (default)
             replace  the file `grid`: raw bytes in map.pgm row order (row 0 = top), one per cell, 0 occupied /
                      205 unknown / 254 free, exactly the source grid's width x height (same resolution and origin)
             regrid   project the (new) cloud again with pcd2grid: the source's own projection parameters when it
                      was projected from its cloud, else the defaults of a cloud import
  cloud      optional PCD (ASCII or binary, x y z) that replaces map.pcd; map_voxel.pcd is rebuilt from it

Like the other version-making routes (regrid, editor) the source is never changed. The new folder keeps
everything that stays valid: data/ (trajectories, the scan trajectory, so the scan-based tools keep working),
goals.yaml, the scene link and the rest of meta.json, which gets `source: derived`, `parent` and an entry in the
`derived` list. figures/ is not copied (it shows the old cloud). Everything is written into a hidden staging
folder and moved into place; map.yaml goes last (the catalog's publication marker).
"""
import asyncio
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import yaml
from fastapi import HTTPException, Request
from PIL import Image
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from .map_edit import OUT_THRESH, VALUES
from .map_import import MapImportError, read_cloud
from .map_library import NAME_RE, write_xyz_pcd
from .map_regrid import auto_params, run_worker

MIB = 1024 * 1024
MAX_REQUEST = 600 * MIB
MAX_DERIVED_XYZ_POINTS = 20_000_000  # Packed binary XYZ from map tools; keep import limits unchanged.
MAX_CELLS = 25_000_000
GRID_MODES = ('keep', 'replace', 'regrid')
VOXEL = 0.1                    # m, map_voxel.pcd (the Maps page preview), as when a map is saved
REGRID_TIMEOUT = 300           # s
TOOL_RE = re.compile(r'^[^\x00-\x1f]{1,120}$')


class DeriveError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def failed(reason, status=400):
    return DeriveError('map_derive_failed', status, reason=str(reason))


def voxel_points(xyz, size=VOXEL):
    """One point (the first) per occupied voxel."""
    keys = np.floor(xyz / size).astype(np.int64)
    keys -= keys.min(axis=0)
    span = keys.max(axis=0) + 1
    flat = (keys[:, 0] * span[1] + keys[:, 1]) * span[2] + keys[:, 2]
    _, first = np.unique(flat, return_index=True)
    return xyz[np.sort(first)]


def cloud_points(path):
    try:
        return read_cloud(path, max_binary_xyz_points=MAX_DERIVED_XYZ_POINTS)
    except MapImportError as e:
        raise failed(e.params.get('reason') or e.code)


class MapDeriver:
    def __init__(self, root, python):
        self.root = Path(os.path.expanduser(root)).resolve()
        self.python = python
        self.lock = threading.Lock()

    def _source(self, name):
        if not NAME_RE.fullmatch(name or ''):
            raise DeriveError('invalid_map_name', name=name)
        folder = self.root / name
        if not folder.is_dir() or folder.is_symlink():
            if (self.root / f'{name}.yaml').is_file():
                raise failed('Only folder maps can get new versions this way')
            raise DeriveError('map_not_found', 404, name=name)
        meta = {}
        if (folder / 'meta.json').is_file():
            try:
                meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
            except (OSError, ValueError) as e:
                raise failed(f'meta.json of the source is unreadable: {e}')
            if not isinstance(meta, dict):
                raise failed('meta.json of the source is not an object')
        return folder, meta

    @staticmethod
    def _grid_info(folder):
        yml = folder / 'map.yaml'
        if not yml.is_file() or yml.is_symlink():
            raise failed('The source map has no 2D grid (map.yaml)')
        try:
            info = yaml.safe_load(yml.read_text(encoding='utf-8'))
            image = (folder / info['image']).resolve()
            res = float(info['resolution'])
            origin = [float(v) for v in info['origin']]
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
            raise failed(f'map.yaml: {e}')
        if not image.is_relative_to(folder.resolve()) or not image.is_file():
            raise failed('The image of the source grid is missing')
        with Image.open(image) as im:
            w, h = im.size
        return info, image, res, origin, w, h

    def derive(self, name, new_name, tool, note, grid_mode, grid_file=None, cloud_file=None):
        if not NAME_RE.fullmatch(new_name or ''):
            raise DeriveError('invalid_map_name', name=new_name)
        if grid_mode not in GRID_MODES:
            raise failed(f'grid_mode must be one of {", ".join(GRID_MODES)}')
        if not TOOL_RE.fullmatch(tool or ''):
            raise failed('tool must be 1 to 120 printable characters')
        if grid_mode == 'replace' and grid_file is None:
            raise failed('grid_mode replace needs the grid file')
        if grid_mode != 'replace' and grid_file is not None:
            raise failed('A grid file is only accepted with grid_mode replace')
        if grid_mode == 'keep' and cloud_file is None:
            raise failed('Nothing to change: give a cloud, a grid or grid_mode regrid')
        if not self.lock.acquire(blocking=False):
            raise failed('Another new map version is being written; try again shortly', 409)
        try:
            folder, meta = self._source(name)
            dest = self.root / new_name
            if dest.exists() or (self.root / f'{new_name}.yaml').exists():
                raise DeriveError('map_exists', 409, name=new_name)
            stage = self.root / f'.derive_{uuid.uuid4().hex}'
            stage.mkdir()
            try:
                record = self._build(stage, folder, meta, name, new_name, tool, note, grid_mode, grid_file,
                                     cloud_file)
                try:
                    dest.mkdir()
                except FileExistsError:
                    raise DeriveError('map_exists', 409, name=new_name)
                try:
                    items = sorted(stage.iterdir(), key=lambda p: p.name == 'map.yaml')
                    for p in items:
                        os.rename(p, dest / p.name)
                except BaseException:
                    shutil.rmtree(dest, ignore_errors=True)
                    raise
            finally:
                shutil.rmtree(stage, ignore_errors=True)
            return record
        finally:
            self.lock.release()

    def _build(self, stage, folder, meta, parent, new_name, tool, note, grid_mode, grid_file, cloud_file):
        derived = {'parent': parent, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'tool': tool, 'note': note,
                   'cloud': 'replaced' if cloud_file is not None else 'kept', 'grid': grid_mode}
        meta = dict(meta)
        files = {}
        extent_xy = None
        # --- cloud
        if cloud_file is not None:
            xyz = cloud_points(cloud_file)
            write_xyz_pcd(stage / 'map.pcd', xyz)
            preview = voxel_points(xyz)
            write_xyz_pcd(stage / 'map_voxel.pcd', preview)
            lo, hi = xyz.min(axis=0), xyz.max(axis=0)
            extent_xy = tuple(float(v) for v in (hi - lo)[:2])
            meta.update(points=int(len(xyz)), voxels=int(len(preview)), voxel_size=VOXEL,
                        extent=[round(float(v), 2) for v in (*lo, *hi)])
            del xyz, preview
            files.update({'map.pcd': f'Point cloud from {tool}', 'map_voxel.pcd': '0.1 m voxel preview'})
        else:
            for filename in ('map.pcd', 'map_voxel.pcd'):
                src = folder / filename
                if src.is_file() and not src.is_symlink():
                    try:
                        os.link(src, stage / filename)
                    except OSError:
                        shutil.copy2(src, stage / filename)
            files.update({'map.pcd': 'Cloud of the parent map', 'map_voxel.pcd': 'Cloud preview'})
        # --- grid
        if grid_mode == 'keep':
            info, image, res, origin, w, h = self._grid_info(folder)
            shutil.copy2(image, stage / image.name)
            for extra in ('map_preview.png',):
                if (folder / extra).is_file() and not (folder / extra).is_symlink() and extra != image.name:
                    shutil.copy2(folder / extra, stage / extra)
            shutil.copy2(folder / 'map.yaml', stage / 'map.yaml')
            files.update({image.name: 'Occupancy grid of the parent map', 'map.yaml': 'Nav2 map metadata'})
        elif grid_mode == 'replace':
            info, image, res, origin, w, h = self._grid_info(folder)
            if w * h > MAX_CELLS:
                raise failed('The grid is too large')
            size = os.path.getsize(grid_file)
            if size != w * h:
                raise failed(f'The grid has {size} cells; the source grid has {w} x {h} = {w * h}')
            grid = np.fromfile(grid_file, dtype=np.uint8).reshape(h, w)
            if not np.isin(grid, VALUES).all():
                raise failed('Grid cells must be 0 (occupied), 205 (unknown) or 254 (free)')
            Image.fromarray(grid, mode='L').save(stage / 'map.pgm')
            Image.fromarray(grid, mode='L').save(stage / 'map_preview.png')
            out = {'image': 'map.pgm', 'mode': 'trinary', 'resolution': res, 'origin': origin, 'negate': 0,
                   **OUT_THRESH}
            (stage / 'map.yaml').write_text(yaml.safe_dump(out, sort_keys=False, default_flow_style=None),
                                            encoding='utf-8')
            meta['grid'] = {'resolution': res, 'width': w, 'height': h, 'origin': origin,
                            'size_m': [round(w * res, 2), round(h * res, 2)]}
            derived['cells'] = {k: int(np.count_nonzero(grid == v)) for k, v in
                                (('occupied', 0), ('unknown', 205), ('free', 254))}
            files.update({'map.pgm': f'Occupancy grid from {tool}', 'map.yaml': 'Nav2 map metadata'})
        else:
            cloud = stage / 'map.pcd'
            if not cloud.is_file():
                raise failed('The source map has no map.pcd to project')
            old = meta.get('regrid') if isinstance(meta.get('regrid'), dict) else None
            params = dict(old['params']) if old and isinstance(old.get('params'), dict) and \
                old.get('method', 'cloud') != 'scans' and 'res' in old['params'] else None
            if params is None:
                if extent_xy is None:
                    xyz = cloud_points(cloud)
                    extent_xy = (float(np.ptp(xyz[:, 0])), float(np.ptp(xyz[:, 1])))
                    del xyz
                params = auto_params(extent_xy)
            work = stage / '.grid'
            work.mkdir()
            try:
                result = run_worker(self.python, cloud, work, params, REGRID_TIMEOUT)
            except RuntimeError as e:
                raise failed(f'Projection failed: {e}')
            os.rename(work / 'map.pgm', stage / 'map.pgm')
            os.rename(work / 'map.yaml', stage / 'map.yaml')
            shutil.rmtree(work, ignore_errors=True)
            with Image.open(stage / 'map.pgm') as im:
                im.save(stage / 'map_preview.png')
            meta['grid'] = result.get('grid')
            meta['regrid'] = {**result, 'params': params, 'derived': True}
            derived['regrid'] = params
            files.update({'map.pgm': 'Occupancy grid projected from the cloud (pcd2grid)',
                          'map.yaml': 'Nav2 map metadata'})
        # --- what stays valid
        data = folder / 'data'
        if data.is_dir() and not data.is_symlink():
            shutil.copytree(data, stage / 'data', symlinks=True)
            files['data/'] = 'Data of the parent map (trajectories)'
        if (folder / 'goals.yaml').is_file() and not (folder / 'goals.yaml').is_symlink():
            shutil.copy2(folder / 'goals.yaml', stage / 'goals.yaml')
        history = list(meta.get('derived') or []) if isinstance(meta.get('derived'), list) else []
        history.append(derived)
        meta.update(name=new_name, created=time.strftime('%Y-%m-%d %H:%M:%S'), source='derived', parent=parent,
                    derived=history, project_error=None, figures_error=None, files=files)
        (stage / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'name': new_name, 'parent': parent, **{k: derived[k] for k in ('cloud', 'grid')},
                'cells': derived.get('cells'), 'points': meta.get('points')}


def register_derive_routes(app, catalog, cfg):
    python = Path(cfg['server']['venv']).expanduser() / 'bin/python'
    deriver = MapDeriver(catalog.root, python)

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    async def read_form(request):
        length = request.headers.get('content-length')
        try:
            if length and int(length) > MAX_REQUEST:
                raise failed(f'The request is larger than {MAX_REQUEST // MIB} MiB', 413)
        except ValueError:
            raise failed('Invalid Content-Length')
        receive = request._receive
        consumed = 0

        async def limited():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get('body', b''))
            if consumed > MAX_REQUEST:
                raise MultiPartException('Upload too large')
            return message

        request._receive = limited
        try:
            return await request.form(max_files=2, max_fields=6, max_part_size=65536)
        except (StarletteHTTPException, MultiPartException) as e:
            raise failed(getattr(e, 'detail', e))

    async def save_upload(upload, path):
        with open(path, 'xb') as f:
            while chunk := await upload.read(MIB):
                await asyncio.to_thread(f.write, chunk)
        return path

    @app.post('/api/maps/{name}/derive')
    async def derive(name: str, request: Request):
        form = None
        try:
            form = await read_form(request)
            keys = [k for k, _ in form.multi_items()]
            if len(keys) != len(set(keys)) or set(keys) - {'new_name', 'tool', 'note', 'grid_mode', 'grid', 'cloud'}:
                raise failed('Duplicate or unexpected form fields')
            text = {k: form.get(k) for k in ('new_name', 'tool', 'note', 'grid_mode')}
            if any(v is not None and not isinstance(v, str) for v in text.values()):
                raise failed('new_name, tool, note and grid_mode must be text fields')
            note = None
            if text['note']:
                if len(text['note']) > 16384:
                    raise failed('note is larger than 16 KiB')
                try:
                    note = json.loads(text['note'])
                except ValueError:
                    raise failed('note must be JSON')
                if not isinstance(note, dict):
                    raise failed('note must be a JSON object')
            with tempfile.TemporaryDirectory(prefix='.derive-upload-', dir=deriver.root) as tmp:
                paths = {}
                for key in ('grid', 'cloud'):
                    upload = form.get(key)
                    if upload is None:
                        continue
                    if not isinstance(upload, UploadFile):
                        raise failed(f'{key} must be a file')
                    paths[key] = await save_upload(upload, Path(tmp) / key)
                return await asyncio.to_thread(
                    deriver.derive, name, (text['new_name'] or '').strip(), (text['tool'] or '').strip(), note,
                    text['grid_mode'] or 'keep', paths.get('grid'), paths.get('cloud'))
        except DeriveError as e:
            raise fail(e)
        finally:
            if form is not None:
                await form.close()

    return deriver
