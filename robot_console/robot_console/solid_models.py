"""3D solid models for saved navigation maps, taken from the original Gazebo world (not rebuilt by hand).

Sources:
  - world-level <model> entries with box / cylinder visuals (the office world);
  - <include> entries (Fuel models such as Warehouse, shelf, pallet_box_mobile, cart_model_2):
    every <visual> of the included model.sdf, with box / cylinder / mesh (.dae, .stl) geometry.

All poses are composed as 4x4 matrices (include -> model -> link -> visual, then mesh scale) and moved
into the map frame. The map frame is the robot spawn pose in the world:
    T_map_world = inverse(T_world_spawn)
Mesh files are only served from inside the sim_robot models directory and only with known extensions.
"""
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

def sim_robot_dir():
    """Installed sim_robot package (share/sim_robot: worlds/, models/), or None when it is not installed."""
    from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
    try:
        return Path(get_package_share_directory('sim_robot'))
    except PackageNotFoundError:
        return None


def model_dirs():
    base = sim_robot_dir()
    return [base / 'models', base / 'models' / 'fuel'] if base else []


ASSET_SUFFIXES = {'.dae', '.stl', '.png', '.jpg', '.jpeg'}
MODEL_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
FUEL_URL_RE = re.compile(r'^https?://fuel\.[^/]+/1\.0/[^/]+/models/([^/]+)/[^/]+/files/(.+)$')


def _floats(text, n, default=0.0):
    vals = [float(v) for v in (text or '').split()]
    return (vals + [default] * n)[:n]


def pose_matrix(text):
    """SDF pose 'x y z roll pitch yaw' -> 4x4 (R = Rz(yaw) Ry(pitch) Rx(roll))."""
    x, y, z, r, p, yw = _floats(text, 6)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(yw), math.sin(yw)
    m = np.eye(4)
    m[:3, :3] = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                 [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                 [-sp, cp * sr, cp * cr]]
    m[:3, 3] = [x, y, z]
    return m


def model_dir(name):
    """Directory of a model by name (only inside the sim_robot models directories)."""
    if not MODEL_RE.fullmatch(name or ''):
        return None
    for base in model_dirs():
        d = base / name
        if (d / 'model.sdf').is_file():
            return d
    return None


def asset_path(model, relative):
    """Resolve a model asset for HTTP serving; None unless it is a known file type inside the model dir.

    The containment check is lexical (normpath removes '..'), not on resolved symlinks: with
    colcon --symlink-install every installed file is a symlink into the build/source tree, so a resolved
    path is never inside the installed model dir. The model dirs themselves come from the installed
    sim_robot package; only `relative` comes from the request."""
    d = model_dir(model)
    if d is None:
        return None
    p = Path(os.path.normpath(d / relative))
    if p.suffix.lower() not in ASSET_SUFFIXES or not p.is_file() or not p.is_relative_to(d):
        return None
    return p


def _mesh_ref(uri, own_model):
    """Mesh URI -> (model name, path inside that model), or None if it cannot be resolved locally."""
    uri = (uri or '').strip()
    m = FUEL_URL_RE.match(uri)
    if m:                                  # Fuel URL: use the locally downloaded copy of that model
        model, rel = m.group(1), m.group(2)
    elif uri.startswith('model://'):
        model, _, rel = uri[len('model://'):].partition('/')
    else:
        model, rel = own_model, uri
    return (model, rel) if asset_path(model, rel) is not None else None


class SolidModels:
    def __init__(self, presets):
        self.presets = presets
        self._cache = {}

    def _world_path(self, preset):
        args = (self.presets.get(preset) or {}).get('args', {}).get('sim', [])
        world = next((a.split(':=', 1)[1] for a in args if a.startswith('world:=')), None)
        if not world:
            return None
        base = sim_robot_dir()
        p = base / 'worlds' / world if base else None
        return p if p is not None and p.is_file() else None

    def get(self, preset):
        """{'frame', 'primitives', 'meshes'} in the map frame, or None when no solid model is available."""
        if not preset:
            return None
        if preset not in self._cache:
            path = self._world_path(preset)
            try:
                self._cache[preset] = self._parse(path) if path else None
            except (ET.ParseError, ValueError, OSError):
                self._cache[preset] = None
        return self._cache[preset]

    @staticmethod
    def _visuals(model_el, model_name, base, out_prims, out_meshes):
        """Append every visual of a <model> element; base = world matrix of the model frame."""
        base = base @ pose_matrix(model_el.findtext('pose'))
        for link in model_el.findall('link'):
            t_link = base @ pose_matrix(link.findtext('pose'))
            seen, seen_sub = set(), set()   # files drawn in this link / drawn whole because of a submesh
            for vis in link.findall('visual'):
                geom = vis.find('geometry')
                if geom is None:
                    continue
                t = t_link @ pose_matrix(vis.findtext('pose'))
                color = _floats(vis.findtext('./material/diffuse'), 4, 0.7)[:3]
                if geom.find('box') is not None:
                    out_prims.append({'type': 'box', 'size': _floats(geom.findtext('./box/size'), 3, 1.0),
                                      'm': t, 'color': color})
                elif geom.find('cylinder') is not None:
                    out_prims.append({'type': 'cylinder', 'radius': float(geom.findtext('./cylinder/radius')),
                                      'length': float(geom.findtext('./cylinder/length')), 'm': t, 'color': color})
                elif geom.find('mesh') is not None:
                    ref = _mesh_ref(geom.findtext('./mesh/uri'), model_name)
                    if ref is None:
                        continue
                    # Submesh visuals pick parts of one file; the browser draws the whole file,
                    # so draw it once per link. Plain repeated meshes (e.g. wheel supports) are all kept.
                    is_sub = geom.find('./mesh/submesh') is not None
                    if ref in seen_sub or (is_sub and ref in seen):
                        continue
                    seen.add(ref)
                    if is_sub:
                        seen_sub.add(ref)
                    s = np.diag([*_floats(geom.findtext('./mesh/scale'), 3, 1.0), 1.0])
                    out_meshes.append({'model': ref[0], 'file': ref[1], 'm': t @ s})

    def _parse(self, path):
        world = ET.parse(path).getroot().find('world')
        spawn = None
        for inc in world.findall('include'):
            if (inc.findtext('uri') or '').strip() == 'model://sim_robot':
                spawn = pose_matrix(inc.findtext('pose'))
        if spawn is None:
            return None
        prims, meshes = [], []
        for m in world.findall('model'):
            if m.get('name') != 'ground':
                self._visuals(m, None, np.eye(4), prims, meshes)
        for inc in world.findall('include'):
            uri = (inc.findtext('uri') or '').strip()
            if not uri.startswith('model://') or uri == 'model://sim_robot':
                continue
            name = uri[len('model://'):].strip('/')
            d = model_dir(name)
            if d is None:
                continue
            model_el = ET.parse(d / 'model.sdf').getroot().find('model')
            if model_el is not None:
                self._visuals(model_el, name, pose_matrix(inc.findtext('pose')), prims, meshes)
        if not prims and not meshes:
            return None
        to_map = np.linalg.inv(spawn)

        def flat(m):
            # three.js Matrix4.fromArray expects column-major order
            return [round(float(v), 6) for v in (to_map @ m).T.reshape(-1)]

        return {'frame': 'map',
                'primitives': [{**{k: v for k, v in p.items() if k != 'm'}, 'matrix': flat(p['m'])} for p in prims],
                'meshes': [{'model': m['model'], 'file': m['file'], 'format': Path(m['file']).suffix[1:].lower(),
                            'matrix': flat(m['m'])} for m in meshes]}

    def public(self, preset):
        return self.get(preset)
