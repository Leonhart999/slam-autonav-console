"""2D grid editing on the map management page (design doc 3.3, step 6): eraser / obstacle pen / unknown pen.

Edits never touch the source map. Saving writes a complete new map folder: the source cloud (hard link or copy),
goals.yaml, meta.json with an appended `edits` entry, map.yaml with the same resolution and origin, and the edited
map.pgm + map_preview.png. Everything is written into a hidden staging folder first and renamed into place.

The grid is exchanged as raw bytes in map.pgm row order (row 0 = top = largest y), one byte per cell, values
0 (occupied), 205 (unknown) or 254 (free).

What the editor shows is what Nav2 reads: the source image is classified with its own map.yaml
(negate, occupied_thresh, free_thresh) by map_server's rule. The new version stores that classification as
0/205/254 with OUT_THRESH, so every untouched cell means the same to map_server as before. Old maps with
free_thresh 0.25 make map_server read 205 as free; those cells are counted (legacy_free) and can optionally be
set back to unknown with the pcd2grid rule (option legacy_unknown, off by default, recorded in meta.json).
"""
import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import yaml
from fastapi import HTTPException, Request
from fastapi.responses import Response
from PIL import Image

from .map_library import NAME_RE

OCCUPIED, UNKNOWN, FREE = 0, 205, 254
VALUES = (OCCUPIED, UNKNOWN, FREE)
MODE_OF = {FREE: 'free', OCCUPIED: 'occupied', UNKNOWN: 'unknown'}
MAX_CELLS = 25_000_000
# Written into every new version. 254 -> p 0.004 (free), 205 -> p 0.196 (unknown), 0 -> p 1 (occupied)
OUT_THRESH = {'occupied_thresh': 0.65, 'free_thresh': 0.15}
PCD2GRID = {'negate': 0, **OUT_THRESH}     # pcd2grid's thresholds, used for the legacy_unknown option


class EditError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def classify(raw, info):
    """Grey image -> 0/205/254 by map_server's rule (nav2_map_server map_io, trinary mode):
    p = (255 - v) / 255 (v / 255 with negate); p > occupied_thresh -> occupied, p < free_thresh -> free,
    otherwise unknown. In scale mode map_server keeps intermediate p as a cost; those cells are shown and
    stored as unknown. raw mode stores occupancy values directly and is not supported."""
    if str(info.get('mode', 'trinary')).lower() == 'raw':
        raise EditError('map_edit_failed', reason='map.yaml mode "raw" is not supported')
    raw = raw.astype(np.float32)
    p = raw / 255.0 if int(info.get('negate', 0)) else (255.0 - raw) / 255.0
    out = np.full(raw.shape, UNKNOWN, dtype=np.uint8)
    out[p > float(info.get('occupied_thresh', 0.65))] = OCCUPIED
    out[p < float(info.get('free_thresh', 0.25))] = FREE
    return out


def legacy_free(raw, info):
    """Cells map_server reads as free from this map.yaml although pcd2grid's thresholds call them unknown
    (old maps: 205 with free_thresh 0.25)."""
    return (classify(raw, info) == FREE) & (classify(raw, PCD2GRID) == UNKNOWN)


class MapEditor:
    def __init__(self, root):
        self.root = Path(os.path.expanduser(root)).resolve()
        self.lock = threading.Lock()

    def _source(self, name):
        """(folder or None, map.yaml path, yaml dict, image path) of a folder map or a legacy flat map."""
        if not NAME_RE.fullmatch(name or ''):
            raise EditError('invalid_map_name', name=name)
        folder = self.root / name
        if folder.is_dir() and not folder.is_symlink():
            yml = folder / 'map.yaml'
        else:
            folder, yml = None, self.root / f'{name}.yaml'
        if not yml.is_file() or yml.is_symlink():
            raise EditError('map_not_found', 404, name=name)
        try:
            info = yaml.safe_load(yml.read_text(encoding='utf-8'))
            image = (yml.parent / info['image']).resolve()
            float(info['resolution'])
            [float(v) for v in info['origin']]
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
            raise EditError('map_edit_failed', reason=f'map.yaml: {e}')
        if not image.is_relative_to(self.root) or not image.is_file():
            raise EditError('map_edit_failed', reason='map image missing')
        return folder, yml, info, image

    @staticmethod
    def _raw(image):
        with Image.open(image) as im:
            return np.asarray(im.convert('L'))

    def grid(self, name):
        """Source map as Nav2 reads it: (bytes, width, height, info, legacy_free cell count)."""
        _, _, info, image = self._source(name)
        raw = self._raw(image)
        g = classify(raw, info)
        return g.tobytes(), g.shape[1], g.shape[0], info, int(np.count_nonzero(legacy_free(raw, info)))

    def save(self, name, new_name, body, legacy_unknown=False):
        if not NAME_RE.fullmatch(new_name or ''):
            raise EditError('invalid_map_name', name=new_name)
        with self.lock:
            folder, yml, info, image = self._source(name)
            raw = self._raw(image)
            original = classify(raw, info)
            legacy = legacy_free(raw, info)
            h, w = original.shape
            if len(body) != w * h:
                raise EditError('map_edit_size', expected=w * h, got=len(body), width=w, height=h)
            grid = np.frombuffer(body, dtype=np.uint8).reshape(h, w).copy()
            if not np.isin(grid, VALUES).all():
                raise EditError('map_edit_values')
            dest = self.root / new_name
            if dest.exists() or (self.root / f'{new_name}.yaml').exists():
                raise EditError('map_exists', 409, name=new_name)
            changed = grid != original
            counts = {MODE_OF[v]: int(np.count_nonzero(changed & (grid == v))) for v in VALUES}
            # Optional pcd2grid fix, only on cells the user did not paint
            restore = legacy & ~changed if legacy_unknown else np.zeros_like(changed)
            grid[restore] = UNKNOWN
            fix = {'legacy_free_cells': int(np.count_nonzero(legacy)), 'legacy_unknown': bool(legacy_unknown),
                   'restored_unknown': int(np.count_nonzero(restore)),
                   'source_thresh': {k: info.get(k) for k in ('mode', 'negate', 'occupied_thresh', 'free_thresh')}}
            if not changed.any() and not restore.any():
                raise EditError('map_edit_unchanged')

            stage = self.root / f'.edit_{uuid.uuid4().hex}'
            stage.mkdir()
            created = False
            try:
                self._write(stage, folder, info, grid, name, new_name, counts, fix)
                # Claim the name exclusively (the same way regrid and import do), then move the files in;
                # map.yaml goes last because the catalog treats it as the publication marker.
                dest.mkdir()
                created = True
                files = sorted((p for p in stage.iterdir()), key=lambda p: p.name == 'map.yaml')
                for p in files:
                    os.rename(p, dest / p.name)
                stage.rmdir()
            except FileExistsError:
                shutil.rmtree(stage, ignore_errors=True)
                raise EditError('map_exists', 409, name=new_name)
            except OSError as e:
                shutil.rmtree(stage, ignore_errors=True)
                if created:
                    shutil.rmtree(dest, ignore_errors=True)
                raise EditError('map_edit_failed', 500, reason=str(e))
            except BaseException:
                shutil.rmtree(stage, ignore_errors=True)
                if created:
                    shutil.rmtree(dest, ignore_errors=True)
                raise
        return {'name': new_name, 'parent': name, 'changed': counts, 'width': w, 'height': h, **fix}

    @staticmethod
    def _write(stage, folder, info, grid, parent, new_name, counts, fix):
        if folder is not None:
            for filename in ('map.pcd', 'map_voxel.pcd'):
                src = folder / filename
                if src.is_file() and not src.is_symlink():
                    try:
                        os.link(src, stage / filename)
                    except OSError:
                        shutil.copy2(src, stage / filename)
            if (folder / 'goals.yaml').is_file() and not (folder / 'goals.yaml').is_symlink():
                shutil.copy2(folder / 'goals.yaml', stage / 'goals.yaml')
        Image.fromarray(grid, mode='L').save(stage / 'map.pgm')
        Image.fromarray(grid, mode='L').save(stage / 'map_preview.png')

        original = folder / 'meta.json' if folder is not None else None
        meta = json.loads(original.read_text(encoding='utf-8')) if original and original.is_file() else {}
        edits = list(meta.get('edits') or [])
        edits.append({'parent': parent, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'cells': counts,
                      'tool': 'robot_console map editor', **fix})
        h, w = grid.shape
        res = float(info['resolution'])
        origin = [float(v) for v in info['origin']]
        meta.update(name=new_name, created=time.strftime('%Y-%m-%d %H:%M:%S'), source='edit', parent=parent,
                    edits=edits, project_error=None, figures_error=None,
                    grid={'resolution': res, 'width': w, 'height': h, 'origin': origin,
                          'size_m': [round(w * res, 2), round(h * res, 2)]},
                    files={'map.pcd': 'Original cloud', 'map_voxel.pcd': 'Cloud preview',
                           'map.pgm': 'Edited occupancy grid', 'map.yaml': 'Nav2 map metadata'})
        (stage / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        # Same geometry. The pixels already hold map_server's classification of the source; OUT_THRESH reads
        # 0/205/254 back as exactly that
        out = {'image': 'map.pgm', 'mode': 'trinary', 'resolution': res, 'origin': origin, 'negate': 0,
               **OUT_THRESH}
        (stage / 'map.yaml').write_text(yaml.safe_dump(out, sort_keys=False, default_flow_style=None),
                                        encoding='utf-8')


def register_map_edit_routes(app, cfg):
    editor = MapEditor(cfg['mapping'].get('maps_dir', '~/maps'))

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/maps/{name}/edit/grid')
    def edit_grid(name: str):
        try:
            data, w, h, info, legacy = editor.grid(name)
        except EditError as e:
            raise fail(e)
        return Response(data, media_type='application/octet-stream', headers={
            'X-Grid-Width': str(w), 'X-Grid-Height': str(h), 'X-Grid-Resolution': str(float(info['resolution'])),
            'X-Grid-Legacy-Free': str(legacy), 'X-Grid-Free-Thresh': str(info.get('free_thresh', 0.25)),
            'Cache-Control': 'no-store'})

    @app.post('/api/maps/{name}/edit/save')
    async def edit_save(name: str, new_name: str, request: Request, legacy_unknown: bool = False):
        body = await request.body()
        if len(body) > MAX_CELLS:
            raise fail(EditError('map_edit_size', expected=MAX_CELLS, got=len(body), width=0, height=0))
        try:
            return await asyncio.to_thread(editor.save, name, new_name, body, legacy_unknown)
        except EditError as e:
            raise fail(e)

    return editor
