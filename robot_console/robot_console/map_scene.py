"""Simulation scene of a saved map, for maps that do not have one (bag playback, imported maps).

Navigation needs a closed loop: a robot that moves and a sensor that sees the result. A bag cannot be driven, so a
map built from a bag can only be navigated in a simulation. Two ways to get one:

  bind      the bag was recorded in one of the console's scenes (e.g. ~/bags/sim/*): link the map to that scene.
            The map's mapping run has est.tum + gt.tum, so nav_mission.map_origin() still finds where the map
            frame is in the world, and suggest() uses the first ground-truth pose to propose the scene.
  generate  real-robot data (no scene exists): build a static Gazebo world from the map's own occupancy grid.
            Occupied cells become walls (one STL mesh), the world frame is the map frame, and the robot spawns
            at the map origin (where the SLAM started). The result is an uploaded-style custom scene
            (scene_import.SceneLibrary, custom_<name>), so it also appears on the mapping page and can be
            deleted there.

The choice is stored in the map's meta.json: preset, scene_source ('bound' or 'generated').
"""
import json
import math
import os
import re
import struct
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .nav_mission import RUN_ID_RE, _quat_yaw, _tum_rows, _wrap, origin_from_world, world_file
from .scene_import import MAX_TRIANGLES, NAME_RE as SCENE_NAME_RE, SceneImportError

WALL_HEIGHT = 2.0          # m; above the simulated lidar and the laser-scan height band
SPAWN_Z = 0.1              # same as the built-in worlds
MATCH_DIST = 0.5           # m; first ground-truth pose within this of a scene's spawn pose -> suggested
MATCH_YAW = 0.35           # rad
TRIANGLES_PER_BOX = 10     # 4 sides + top (the floor is a separate plane)


class MapSceneError(Exception):
    def __init__(self, code, status=409, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


# ---------------- meta.json ----------------

def read_meta(folder):
    p = Path(folder) / 'meta.json'
    try:
        meta = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def write_meta(folder, meta):
    p = Path(folder) / 'meta.json'
    tmp = p.with_name('meta.json.tmp')
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, p)


def map_source(meta):
    """'bag' when the map (or the map it was regridded from) was built from a bag, 'sim' when it was mapped in the simulation."""
    if meta.get('bag'):
        return 'bag'
    return 'sim' if meta.get('sim_cmd') else None


# ---------------- suggestion for sim-recorded bags ----------------

def first_gt_pose(runs_dir, meta):
    """(x, y, yaw) of the first ground-truth pose of the map's mapping run (world frame), or None."""
    run = meta.get('run')
    if not isinstance(run, str) or not RUN_ID_RE.fullmatch(run):
        return None
    try:
        row = next(_tum_rows(Path(runs_dir).expanduser() / run / 'gt.tum', 1), None)
    except (OSError, ValueError):
        return None
    return (row[1], row[2], _quat_yaw(*row[4:8])) if row else None


def suggest(presets, runs_dir, meta):
    """Built-in or uploaded scene whose spawn pose matches where the bag's robot started, or None.
    Bags recorded in the console's simulation start at the spawn pose; real-robot bags have no ground truth."""
    gt = first_gt_pose(runs_dir, meta)
    if gt is None:
        return None
    best = None
    for name, p in list(presets.items()):
        args = (p.get('args') or {}).get('sim')
        try:
            spawn = origin_from_world(world_file(args if isinstance(args, list) else []))
        except Exception:                    # noqa: BLE001 - missing / unreadable world: not a candidate
            continue
        if spawn is None:
            continue
        d = math.hypot(gt[0] - spawn[0], gt[1] - spawn[1])
        if d <= MATCH_DIST and abs(_wrap(gt[2] - spawn[2])) <= MATCH_YAW and (best is None or d < best[1]):
            best = (name, d)
    return best[0] if best else None


# ---------------- scene from the occupancy grid ----------------

def occupied_cells(yaml_path):
    """Boolean array (rows = image rows, top row = largest y) of occupied cells, plus the map.yaml contents."""
    info = yaml.safe_load(Path(yaml_path).read_text(encoding='utf-8'))
    image = Path(yaml_path).parent / info['image']
    with Image.open(image) as im:
        pixels = np.asarray(im.convert('L'), dtype=np.float32)
    p = pixels / 255.0 if int(info.get('negate', 0)) else (255.0 - pixels) / 255.0
    return p > float(info.get('occupied_thresh', 0.65)), info


def merge_boxes(occ):
    """Occupied cells merged into rectangles (r0, c0, r1, c1), end exclusive: runs along each row, then runs with
    the same columns in consecutive rows are joined. Cuts the triangle count by 1-2 orders of magnitude."""
    boxes, open_runs = [], {}
    for r in range(occ.shape[0] + 1):
        runs = set()
        if r < occ.shape[0]:
            row = np.concatenate(([False], occ[r], [False]))
            edges = np.flatnonzero(row[1:] != row[:-1])
            runs = {(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])}
        for key in list(open_runs):
            if key not in runs:
                boxes.append((open_runs.pop(key), key[0], r, key[1]))
        for key in runs:
            open_runs.setdefault(key, r)
    return boxes


def _pool(occ, k):
    h, w = (occ.shape[0] + k - 1) // k, (occ.shape[1] + k - 1) // k
    # pad at the top: the bottom image row is at the map origin and must stay there
    padded = np.zeros((h * k, w * k), dtype=bool)
    padded[h * k - occ.shape[0]:, :occ.shape[1]] = occ
    return padded.reshape(h, k, w, k).any(axis=(1, 3))


def write_stl(path, boxes, res, origin, rows, height):
    """Binary STL of the merged boxes in the map frame (map.yaml origin, rotation included)."""
    ox, oy, oyaw = origin
    c, s = math.cos(oyaw), math.sin(oyaw)

    def xy(col, row_from_bottom):
        u, v = col * res, row_from_bottom * res
        return ox + c * u - s * v, oy + s * u + c * v

    with open(path, 'wb') as f:
        f.write(b'robot_console occupancy grid walls'.ljust(80, b' '))
        f.write(struct.pack('<I', len(boxes) * TRIANGLES_PER_BOX))
        for r0, c0, r1, c1 in boxes:
            # image rows grow downwards, the map's y upwards
            b0, b1 = rows - r1, rows - r0
            p = [xy(c0, b0), xy(c1, b0), xy(c1, b1), xy(c0, b1)]        # counter-clockwise seen from above
            tris = []
            for i in range(4):
                (x0, y0), (x1, y1) = p[i], p[(i + 1) % 4]
                n = (y1 - y0, x0 - x1, 0.0)                             # outward for a CCW polygon
                tris.append((n, (x0, y0, 0), (x1, y1, 0), (x1, y1, height)))
                tris.append((n, (x0, y0, 0), (x1, y1, height), (x0, y0, height)))
            top = [(x, y, height) for x, y in p]
            tris.append(((0, 0, 1), top[0], top[1], top[2]))
            tris.append(((0, 0, 1), top[0], top[2], top[3]))
            for n, a, b, d in tris:
                norm = math.hypot(*n) or 1.0
                f.write(struct.pack('<12fH', n[0] / norm, n[1] / norm, n[2] / norm, *a, *b, *d, 0))


WORLD_SDF = """<?xml version="1.0" ?>
<sdf version="1.8">
  <world name="map_scene">
    <model name="ground">
      <static>true</static>
      <link name="link">
        <collision name="collision"><geometry><plane><normal>0 0 1</normal><size>{gw} {gh}</size></plane></geometry></collision>
        <visual name="visual">
          <geometry><plane><normal>0 0 1</normal><size>{gw} {gh}</size></plane></geometry>
          <material><ambient>0.8 0.8 0.8 1</ambient><diffuse>0.8 0.8 0.8 1</diffuse></material>
        </visual>
      </link>
      <pose>{gx} {gy} 0 0 0 0</pose>
    </model>
    <model name="map_walls">
      <static>true</static>
      <link name="link">
        <collision name="collision"><geometry><mesh><uri>walls.stl</uri></mesh></geometry></collision>
        <visual name="visual">
          <geometry><mesh><uri>walls.stl</uri></mesh></geometry>
          <material><ambient>0.55 0.6 0.68 1</ambient><diffuse>0.55 0.6 0.68 1</diffuse></material>
        </visual>
      </link>
    </model>
  </world>
</sdf>
"""


def build_scene_package(yaml_path, out_zip, height=WALL_HEIGHT):
    """Write a scene ZIP (world.sdf + walls.stl) for SceneLibrary.import_scene. Returns facts for the page."""
    occ, info = occupied_cells(yaml_path)
    res = float(info['resolution'])
    origin = [float(v) for v in info['origin']]
    k = 1
    while True:
        grid = _pool(occ, k) if k > 1 else occ
        boxes = merge_boxes(grid)
        if len(boxes) * TRIANGLES_PER_BOX <= MAX_TRIANGLES:
            break
        k *= 2                               # very large or noisy grid: coarser walls instead of a refusal
    if not boxes:
        raise MapSceneError('map_scene_empty', 400)
    rows, cols = grid.shape
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / 'walls.stl'
        write_stl(stl, boxes, res * k, origin, rows, height)
        # ground plane covering the grid with a margin, centred on it
        w, h = cols * res * k, rows * res * k
        c, s = math.cos(origin[2]), math.sin(origin[2])
        gx, gy = origin[0] + c * w / 2 - s * h / 2, origin[1] + s * w / 2 + c * h / 2
        side = max(w, h) + 20.0
        sdf = WORLD_SDF.format(gw=f'{side:.2f}', gh=f'{side:.2f}', gx=f'{gx:.3f}', gy=f'{gy:.3f}')
        with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('world.sdf', sdf)
            z.write(stl, 'walls.stl')
    return {'boxes': len(boxes), 'cell': round(res * k, 4), 'height': height}


def scene_name(map_name, taken):
    """Scene folder name for a map (SceneLibrary allows 48 characters); preset is custom_<name>."""
    base = re.sub(r'[^A-Za-z0-9_-]', '_', 'map_' + map_name)[:44]
    name, k = base, 2
    while name in taken or 'custom_' + name in taken:
        name, k = f'{base[:44 - len(str(k)) - 1]}_{k}', k + 1
    if not SCENE_NAME_RE.fullmatch(name):
        raise MapSceneError('map_scene_name', 400, name=map_name)
    return name


# ---------------- routes ----------------

def register_map_scene_routes(app, cfg, nav_maps, scenes, presets, procs, nav_name):
    from fastapi import HTTPException
    from .navigation import NavigationError

    runs_dir = cfg['mapping'].get('runs_dir', '~/runs')

    def fail(code, status=409, **params):
        return HTTPException(status, detail={'code': code, 'params': params})

    def folder_of(name):
        try:
            asset = nav_maps.assets(name)
        except NavigationError as e:
            raise fail(e.code, 404 if e.code == 'map_not_found' else 400, **e.params)
        if asset['_yaml'].name != 'map.yaml':
            raise fail('map_scene_legacy', 400, name=name)       # flat ~/maps/<name>.yaml has no meta.json
        return asset, asset['_yaml'].parent

    def nav_uses(name):
        s = procs.get(nav_name)
        if s is None or s.state() not in ('running', 'stopping'):
            return False
        cmd = s.run_cmd or s.cmd
        return any(a.startswith('map:=') and Path(a.split(':=', 1)[1]).parent.name == name for a in cmd)

    def public(name):
        asset, folder = folder_of(name)
        meta = read_meta(folder)
        preset = meta.get('preset')
        return {'name': name, 'preset': preset, 'scene_source': meta.get('scene_source'),
                'source': map_source(meta), 'bag': meta.get('bag'),
                'preset_ok': bool(preset and preset in presets),
                'suggested': suggest(presets, runs_dir, meta) if not preset or preset not in presets else None,
                'has_gt': first_gt_pose(runs_dir, meta) is not None}

    def store(name, preset, source):
        if nav_uses(name):
            raise fail('map_scene_in_use', name=name)
        _, folder = folder_of(name)
        meta = read_meta(folder)
        if not meta and not (folder / 'meta.json').is_file():
            meta = {'name': name}
        meta['preset'] = preset
        if preset is None:
            meta.pop('scene_source', None)
        else:
            meta['scene_source'] = source
        try:
            write_meta(folder, meta)
        except OSError as e:
            raise fail('map_scene_failed', 500, reason=str(e))
        return public(name)

    @app.get('/api/navigation/maps/{name}/scene')
    def map_scene_get(name: str):
        return public(name)

    @app.post('/api/navigation/maps/{name}/scene')
    def map_scene_bind(name: str, preset: str):
        """Link a map without a scene to an existing scene (the one its bag was recorded in)."""
        if preset not in presets:
            raise fail('preset_not_found', 404, name=preset)
        _, folder = folder_of(name)
        current = read_meta(folder)
        if current.get('preset') in presets and current.get('scene_source') is None:
            raise fail('map_scene_recorded', name=name)      # mapped in the simulation: keep the true scene
        return store(name, preset, 'bound')

    @app.delete('/api/navigation/maps/{name}/scene')
    def map_scene_unbind(name: str):
        _, folder = folder_of(name)
        if read_meta(folder).get('scene_source') is None:
            raise fail('map_scene_recorded', name=name)
        return store(name, None, None)

    @app.post('/api/navigation/maps/{name}/scene/generate')
    def map_scene_generate(name: str, height: float = WALL_HEIGHT):
        """Build a static world from the map's occupancy grid and link the map to it."""
        if not (math.isfinite(height) and 0.5 <= height <= 5.0):
            raise fail('map_scene_height', 400, lo=0.5, hi=5.0)
        asset, folder = folder_of(name)
        current = read_meta(folder)
        if current.get('preset') in presets and current.get('scene_source') is None:
            raise fail('map_scene_recorded', name=name)
        if nav_uses(name):
            raise fail('map_scene_in_use', name=name)
        taken = set(presets) | ({p.name for p in scenes.root.iterdir()} if scenes.root.is_dir() else set())
        scene = scene_name(name, taken)
        scenes.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scenes.root, prefix='.mapscene-') as tmp:
            package = Path(tmp) / 'scene.zip'
            try:
                facts = build_scene_package(asset['_yaml'], package, height)
                # spawn at the map origin: there the SLAM started, so the cell is free and AMCL's default
                # initial pose (0, 0, 0) is right
                scenes.import_scene(scene, package, 'scene.zip', [0.0, 0.0, SPAWN_Z, 0.0])
            except MapSceneError as e:
                raise fail(e.code, e.status, **e.params)
            except SceneImportError as e:
                raise fail(e.code, 400, **e.params)
            except (OSError, ValueError, KeyError, TypeError) as e:
                raise fail('map_scene_failed', 500, reason=str(e))
        out = store(name, 'custom_' + scene, 'generated')
        out['generated'] = facts
        return out
