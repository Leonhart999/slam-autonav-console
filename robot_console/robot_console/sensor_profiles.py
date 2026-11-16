"""Sensor setup of the Playback page: sensor profiles made in the browser instead of by editing YAML.

A sensor profile (see config/sensor_profiles.yaml for the keys) tells the sensor adapter (sensor_adapter.py) how to
bring a robot's lidar and IMU into the frame and units the SLAM configurations expect. Built-in profiles come from
config/sensor_profiles.yaml (read only); profiles made on the page are kept in playback.user_sensor_profiles_file
(default ${data_root}/sensor_profiles.yaml), are checked before the built-in ones, and all get the SLAM
configurations of playback.sensor_slam_args (console.yaml).

Probe (POST /api/playback/sensor_profiles/probe?bag=): bag_probe.py reads the first scans of every point cloud
topic and ~2 s of every IMU topic; guess() turns that into a proposed profile:
    points      the PointCloud2 topic with the most messages; points2 is never proposed (it needs an extrinsic)
    imu         the Imu topic with the most messages, preferring one below the lidar's namespace
    point_time  a field named like a time (TIME_NAMES or containing "time"), its unit and whether it is absolute:
                absolute when value * unit is within ABS_TOL of the header stamp; otherwise the unit whose spread
                over one scan is closest to 0.1 s (a 10 Hz scan) among spreads of 5 ms .. 0.5 s
    ring / intensity fields by name
    imu_acc_unit  g when the mean acceleration norm is about 1, m/s2 when about 9.8
    extrinsics    identity
Warnings (codes with params, translated on the page): no_time, time_negative, imu_unit_unknown, imu_moving,
gravity_tilt, no_imu, no_cloud.

Routes: GET /api/playback/sensor_profiles, POST .../probe?bag=, POST /api/playback/sensor_profiles?bag= (create),
PUT .../{name}?bag= (update a page-made profile), DELETE .../{name}. Saving checks the topics against the bag the
profile was made for. Profiles cannot change while a playback runs (the adapter reads its profile at start).
"""
import asyncio
import copy
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import yaml
from fastapi import HTTPException

from .config import bilingual
from .playback import PlaybackError, sensor_profile_of
from .sensor_adapter import RING2_OFFSET, orthonormal

NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
FIELD_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,31}$')
CLOUD = 'sensor_msgs/msg/PointCloud2'
IMU = 'sensor_msgs/msg/Imu'
ODOM = 'nav_msgs/msg/Odometry'
TIME_UNITS = {'s': 1.0, 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9}
TIME_NAMES = ('time', 't', 'timestamp', 'offset_time', 'time_offset', 'ts', 'time_stamp')
RING_NAMES = ('ring', 'line', 'channel', 'laser_id', 'ring_id')
INTENSITY_NAMES = ('intensity', 'reflectivity', 'i', 'intensities')
ABS_TOL = 1.0               # s, absolute point times lie this close to the header stamp
SCAN_SPAN = 0.1             # s, the spread of point times expected over one scan (10 Hz)
SPAN_RANGE = (0.005, 0.5)   # s, spreads accepted as one scan
G = 9.80665
TILT_WARN = 10.0            # deg, gravity this far from +z of the IMU frame is reported
PROBE_TIMEOUT = 90          # s
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


class SensorProfileError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def _invalid(reason):
    raise SensorProfileError('sensor_profile_invalid', reason=reason)


# ---------------- guessing (no ROS, unit tested) ----------------

def guess_time(cloud):
    """{'field', 'unit', 'absolute', 'span', 'negative'} for a probed cloud, or None when no field looks like a
    per-point time."""
    stats = cloud.get('stats') or {}
    stamp = float(cloud.get('stamp') or 0.0)
    cands = [n for n in stats if n.lower() in TIME_NAMES or 'time' in n.lower()]
    best = None                                 # (score, result); absolute matches score below every relative one
    for name in cands:
        s = stats[name]
        for unit, scale in TIME_UNITS.items():
            lo, hi, med = s['min'] * scale, s['max'] * scale, s['median'] * scale
            span = hi - lo
            if stamp > 1.0 and abs(med - stamp) < ABS_TOL:
                score, absolute = -1.0 + abs(med - stamp), True
            elif SPAN_RANGE[0] <= span <= SPAN_RANGE[1] and -SPAN_RANGE[1] <= lo and hi <= SPAN_RANGE[1]:
                score, absolute = abs(math.log(span / SCAN_SPAN)), False
            else:
                continue
            if name.lower() in TIME_NAMES:
                score -= 0.01                   # prefer the usual names on a tie
            res = {'field': name, 'unit': unit, 'absolute': absolute, 'span': round(span, 4),
                   'negative': not absolute and lo < -1e-3}
            if best is None or score < best[0]:
                best = (score, res)
    return best[1] if best else None


def guess_field(cloud, names):
    fields = [f['name'] for f in cloud.get('fields') or []]
    lower = {f.lower(): f for f in fields}
    return next((lower[n] for n in names if n in lower), '')


def guess_acc_unit(imu):
    """('g' | 'm/s2', sure) from the mean acceleration norm."""
    n = float(imu.get('acc_norm') or 0.0)
    if 0.7 <= n <= 1.3:
        return 'g', True
    if 0.7 * G <= n <= 1.3 * G:
        return 'm/s2', True
    if n <= 0:
        return 'm/s2', False
    return ('g' if abs(math.log(n)) < abs(math.log(n / G)) else 'm/s2'), False


def gravity_tilt(imu):
    """Angle (deg) between the mean acceleration (gravity reaction when standing) and +z of the IMU frame."""
    a = np.asarray(imu.get('acc_mean') or [0, 0, 0], float)
    n = np.linalg.norm(a)
    if n <= 0:
        return None
    return float(np.degrees(np.arccos(np.clip(a[2] / n, -1.0, 1.0))))


def pick_imu(imus, counts, points):
    """The IMU topic to propose: one below the lidar topic's namespace first, then the most messages."""
    if not imus:
        return None
    ns = (points or '').rstrip('/') + '/'
    return sorted(imus, key=lambda n: (not n.startswith(ns), -counts.get(n, 0), n))[0]


def cloud_fields(cloud):
    """Form values for the per-point fields of one probed cloud."""
    t = guess_time(cloud)
    return {'time_field': t['field'] if t else '', 'time_unit': t['unit'] if t else 's',
            'time_absolute': t['absolute'] if t else False, 'time_negative': bool(t and t['negative']),
            'ring_field': guess_field(cloud, RING_NAMES), 'intensity_field': guess_field(cloud, INTENSITY_NAMES)}


def guess(bag, probe):
    """Proposed profile (the page's form), per-topic guesses (applied when the page switches topics) and warnings
    for a bag (playback.read_bag summary) and its probe."""
    counts = {t['name']: t['count'] for t in bag['topics']}
    clouds = probe.get('clouds') or {}
    imus = probe.get('imus') or {}
    warnings = []
    per_topic = {'clouds': {n: cloud_fields(c) for n, c in clouds.items()},
                 'imus': {n: guess_acc_unit(s)[0] for n, s in imus.items()}}
    points = max(clouds, key=lambda n: (counts.get(n, 0), n)) if clouds else None
    form = {'name': '', 'label': '', 'points': points, 'points2': None, 'lidar2_to_lidar': IDENTITY,
            'imu': None, 'time_field': '', 'time_unit': 's', 'time_absolute': False,
            'ring_field': '', 'intensity_field': '', 'imu_acc_unit': 'm/s2',
            'lidar_to_imu': IDENTITY, 'imu_to_base': IDENTITY, 'reference': None}
    if points is None:
        warnings.append({'code': 'no_cloud', 'params': {}})
    else:
        c = clouds[points]
        t = guess_time(c)
        if t is None:
            warnings.append({'code': 'no_time', 'params': {}})
        else:
            form.update(time_field=t['field'], time_unit=t['unit'], time_absolute=t['absolute'])
            if t['negative']:
                warnings.append({'code': 'time_negative', 'params': {'field': t['field']}})
        form['ring_field'] = guess_field(c, RING_NAMES)
        form['intensity_field'] = guess_field(c, INTENSITY_NAMES)
    imu = pick_imu(list(imus), counts, points)
    if imu is None:
        warnings.append({'code': 'no_imu', 'params': {}})
    else:
        form['imu'] = imu
        s = imus[imu]
        unit, sure = guess_acc_unit(s)
        form['imu_acc_unit'] = unit
        if not sure:
            warnings.append({'code': 'imu_unit_unknown', 'params': {'norm': round(s.get('acc_norm') or 0, 3)}})
        norm = s.get('acc_norm') or 0
        if norm > 0 and ((s.get('acc_norm_std') or 0) > 0.05 * norm or (s.get('gyro_norm') or 0) > 0.1):
            warnings.append({'code': 'imu_moving', 'params': {}})
        tilt = gravity_tilt(s)
        if tilt is not None and tilt > TILT_WARN:
            warnings.append({'code': 'gravity_tilt', 'params': {'deg': round(tilt, 1)}})
    return form, per_topic, warnings


def measure_facts(bag, profile, probe):
    """What the SLAM configuration derives values from (slam_config.py), for a bag played with `profile`:
        rings            ring numbers the SLAM's cloud can carry (highest ring + 1; a second lidar's rings are
                         shifted by RING2_OFFSET in the sensor adapter); None without a ring field
        points_per_scan  valid points of one scan (both lidars)
        scan_rate, imu_rate  Hz, from the message counts and the duration of the bag
    bag: playback.read_bag summary; probe: bag_probe.py output."""
    clouds = probe.get('clouds') or {}
    counts = {t['name']: t['count'] for t in bag['topics']}
    duration = float(bag.get('duration') or 0.0)
    ring_field = profile.get('ring_field', 'ring')

    def rings_of(topic):
        s = ((clouds.get(topic) or {}).get('stats') or {}).get(ring_field) if ring_field else None
        return int(round(s['max'])) + 1 if s else None

    def rate(topic):
        n = counts.get(topic, 0)
        return round(n / duration, 2) if topic and n > 1 and duration > 0 else None

    points, points2 = profile.get('points'), profile.get('points2')
    first = rings_of(points)
    second = rings_of(points2) if points2 else None
    rings = RING2_OFFSET + second if second else first
    per_scan = sum(int((clouds.get(t) or {}).get('finite') or 0) for t in (points, points2) if t)
    return {'rings': rings, 'points_per_scan': per_scan or None, 'scan_rate': rate(points),
            'imu_rate': rate(profile.get('imu')), 'bag': os.path.basename(bag['path']),
            'measured': time.strftime('%Y-%m-%d %H:%M:%S'), 'profile_updated': profile.get('updated')}


# ---------------- validation and storage ----------------

def _matrix(value, key):
    try:
        m = [[float(v) for v in row] for row in value]
    except (TypeError, ValueError):
        _invalid(f'{key} must be a 4x4 matrix of numbers')
    if len(m) != 4 or any(len(r) != 4 for r in m) or not all(math.isfinite(v) for r in m for v in r):
        _invalid(f'{key} must be a 4x4 matrix of finite numbers')
    if m[3] != [0.0, 0.0, 0.0, 1.0]:
        _invalid(f'{key}: the last row must be 0 0 0 1')
    R = np.array(m)[:3, :3]
    if np.abs(R.T @ R - np.eye(3)).max() > 0.05 or np.linalg.det(R) <= 0:
        _invalid(f'{key}: the 3x3 block is not a rotation')
    if any(abs(r[3]) > 100 for r in m[:3]):
        _invalid(f'{key}: translation above 100 m')
    try:
        orthonormal(m)
    except ValueError as e:
        _invalid(f'{key}: {e}')
    return m


def _field(value, key):
    if value in (None, ''):
        return ''
    if not isinstance(value, str) or not FIELD_RE.fullmatch(value):
        _invalid(f'{key} is not a field name')
    return value


def validate(body, bag):
    """Profile to store (the keys sensor_adapter.py reads) from the page's form, checked against the bag."""
    if not isinstance(body, dict):
        _invalid('the profile must be an object')
    types = {t['name']: t['type'] for t in bag['topics']}

    def topic(key, typ, required=False):
        v = body.get(key)
        if v in (None, ''):
            if required:
                _invalid(f'{key} is required')
            return None
        if not isinstance(v, str) or types.get(v) != typ:
            _invalid(f'{key}: the bag has no {typ} topic {v!r}')
        return v

    label = body.get('label') or ''
    if not isinstance(label, str) or len(label) > 64:
        _invalid('label: at most 64 characters')
    points = topic('points', CLOUD, True)
    points2 = topic('points2', CLOUD)
    if points2 == points:
        _invalid('the second lidar must be another topic')
    imu = topic('imu', IMU)
    unit = body.get('time_unit', 's')
    if unit not in TIME_UNITS:
        _invalid('time_unit must be s, ms, us or ns')
    if not isinstance(body.get('time_absolute', False), bool):
        _invalid('time_absolute must be true or false')
    acc = body.get('imu_acc_unit', 'm/s2')
    if acc not in ('g', 'm/s2'):
        _invalid('imu_acc_unit must be g or m/s2')
    p = {'label': {'zh': label.strip(), 'en': label.strip()} if label.strip() else None,
         'match': [t for t in (points, points2, imu) if t],
         'points': points}
    if points2:
        p['points2'] = points2
        p['lidar2_to_lidar'] = _matrix(body.get('lidar2_to_lidar'), 'lidar2_to_lidar')
    if imu:
        p['imu'] = imu
        p['imu_acc_unit'] = acc
    p['point_time'] = {'field': _field(body.get('time_field'), 'time_field'),
                       'absolute': bool(body.get('time_absolute', False)), 'scale': TIME_UNITS[unit]}
    p['ring_field'] = _field(body.get('ring_field'), 'ring_field')
    p['intensity_field'] = _field(body.get('intensity_field'), 'intensity_field')
    p['lidar_to_imu'] = _matrix(body.get('lidar_to_imu', IDENTITY), 'lidar_to_imu')
    p['imu_to_base'] = _matrix(body.get('imu_to_base', IDENTITY), 'imu_to_base')
    ref = topic('reference', ODOM)
    if ref:
        p['reference'] = {'topic': ref, 'label': {'zh': f'参考里程计 {ref}', 'en': f'reference odometry {ref}'}}
    if p['label'] is None:
        del p['label']
    p['source_bag'] = os.path.basename(bag['path'])
    p['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return p


def to_form(name, p, user):
    """A stored profile (built-in or page-made) as the page's form."""
    pt = p.get('point_time') or {}
    scale = float(pt.get('scale', 1.0))
    unit = min(TIME_UNITS, key=lambda u: abs(math.log(TIME_UNITS[u] / scale)) if scale > 0 else 0)
    label = p.get('label') or {}
    return {'name': name, 'user': user, 'label': (label.get('zh') if isinstance(label, dict) else str(label or '')) or '',
            'points': p.get('points'), 'points2': p.get('points2'),
            'lidar2_to_lidar': p.get('lidar2_to_lidar') or IDENTITY, 'imu': p.get('imu'),
            'time_field': pt.get('field', 'time') if 'point_time' in p else 'time', 'time_unit': unit,
            'time_absolute': bool(pt.get('absolute', False)),
            'ring_field': p.get('ring_field', 'ring'), 'intensity_field': p.get('intensity_field', 'intensity'),
            'imu_acc_unit': p.get('imu_acc_unit', 'm/s2'),
            'lidar_to_imu': p.get('lidar_to_imu') or IDENTITY, 'imu_to_base': p.get('imu_to_base') or IDENTITY,
            'reference': (p.get('reference') or {}).get('topic'),
            'source_bag': p.get('source_bag'), 'updated': p.get('updated')}


def load_user(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f'{path}: not a mapping of sensor profiles')
    return data


def merged(builtin, user_file, slam_args):
    """(profiles, files, user names): page-made profiles first (with slam_args), then the built-in ones.
    files: profile name -> the YAML file the sensor adapter reads it from."""
    profiles, files, user = {}, {}, set()
    try:
        stored = load_user(user_file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f'[robot_console] sensor profiles {user_file} not loaded: {e}', file=sys.stderr)
        stored = {}
    for name, p in stored.items():
        if name in builtin or not isinstance(p, dict) or not NAME_RE.fullmatch(str(name)):
            continue
        p = copy.deepcopy(p)
        p['label'] = bilingual(p.get('label'), name)
        p['slam_args'] = copy.deepcopy(slam_args)
        profiles[name], files[name] = p, user_file
        user.add(name)
    for name, p in builtin.items():
        profiles[name] = p
    return profiles, files, user


class SensorProfiles:
    """The page-made profiles file (one writer at a time) and the probe."""

    def __init__(self, cfg, playback):
        p = cfg.get('playback') or {}
        self.pb = playback
        self.builtin = p.get('sensor_profiles') or {}
        self.user_file = p.get('user_sensor_profiles_file') or ''
        self.slam_args = p.get('sensor_slam_args') or {}
        self.lock = threading.Lock()
        self.probe_lock = threading.Lock()
        self.reload()

    def reload(self):
        profiles, files, user = merged(self.builtin, self.user_file, self.slam_args)
        self.pb.set_profiles(profiles, files, user)

    def list(self):
        return [to_form(n, p, n in self.pb.user_profiles) for n, p in self.pb.profiles.items()]

    def facts(self, bag_path, store):
        """Measured facts of the sensor profile `bag_path` is played with (None without a profile): read from
        `store` (slam_config.SensorFacts), measured from this bag when missing or older than the profile."""
        bag = self.pb._find(bag_path)
        name = sensor_profile_of(bag, self.pb.profiles)
        if not name:
            return None
        profile = self.pb.profiles[name]
        cached = store.get(name)
        if cached and cached.get('profile_updated') == profile.get('updated'):
            return cached
        facts = measure_facts(bag, profile, self._probe(bag, wait=True))
        store.set(name, facts)
        return facts

    def probe(self, bag_path):
        bag = self.pb._find(bag_path)
        probe = self._probe(bag)
        form, per_topic, warnings = guess(bag, probe)
        matched = None
        name = sensor_profile_of(bag, self.pb.profiles)
        if name:
            matched = to_form(name, self.pb.profiles[name], name in self.pb.user_profiles)
        return {'bag': {'path': bag['path'], 'name': os.path.basename(bag['path'])},
                'topics': bag['topics'], 'probe': probe, 'guess': form, 'per_topic': per_topic,
                'warnings': warnings, 'matched': matched}

    def _probe(self, bag, wait=False):
        """bag_probe.py output for a bag; one probe at a time (wait: queue behind a running one)."""
        if not self.probe_lock.acquire(blocking=wait, timeout=PROBE_TIMEOUT if wait else -1):
            raise SensorProfileError('sensor_probe_busy', 409)
        try:
            worker = Path(__file__).with_name('bag_probe.py')
            try:
                proc = subprocess.run([sys.executable, str(worker), bag['path'], bag['storage']], cwd='/tmp',
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                      timeout=PROBE_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise SensorProfileError('sensor_probe_failed', 500, reason=f'no result within {PROBE_TIMEOUT} s')
            if proc.returncode:
                reason = (proc.stderr.strip().splitlines() or ['bag_probe failed'])[-1]
                raise SensorProfileError('sensor_probe_failed', 500, reason=reason)
            return json.loads(proc.stdout)
        finally:
            self.probe_lock.release()

    def _write(self, change):
        """Apply change(dict of stored profiles) to the user file atomically and reload."""
        if self.pb.playback_active():
            raise SensorProfileError('sensor_profile_busy', 409)
        with self.lock:
            try:
                stored = load_user(self.user_file)
            except (OSError, ValueError, yaml.YAMLError) as e:
                raise SensorProfileError('sensor_profile_invalid', 500, reason=str(e))
            change(stored)
            os.makedirs(os.path.dirname(self.user_file) or '.', exist_ok=True)
            tmp = f'{self.user_file}.tmp{os.getpid()}'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write('# Sensor profiles made on the Playback page of robot_console (see sensor_profiles.py).\n')
                yaml.safe_dump(stored, f, sort_keys=False, allow_unicode=True, default_flow_style=None)
            os.replace(tmp, self.user_file)
            self.reload()

    def save(self, name, body, bag_path, create):
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise SensorProfileError('sensor_profile_name', name=str(name))
        if name in self.builtin:
            raise SensorProfileError('sensor_profile_builtin', name=name)
        profile = validate(body, self.pb._find(bag_path))

        def change(stored):
            if create and name in stored:
                raise SensorProfileError('sensor_profile_exists', 409, name=name)
            if not create and name not in stored:
                raise SensorProfileError('sensor_profile_not_found', 404, name=name)
            stored[name] = profile
        self._write(change)
        return to_form(name, self.pb.profiles[name], True)

    def delete(self, name):
        if name in self.builtin:
            raise SensorProfileError('sensor_profile_builtin', name=name)

        def change(stored):
            if name not in stored:
                raise SensorProfileError('sensor_profile_not_found', 404, name=name)
            del stored[name]
        self._write(change)
        return {'name': name}


def register_sensor_profile_routes(app, cfg, playback):
    service = SensorProfiles(cfg, playback)

    def call(fn, *args):
        try:
            return fn(*args)
        except (SensorProfileError, PlaybackError) as e:
            raise HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/playback/sensor_profiles')
    def profiles():
        return service.list()

    @app.post('/api/playback/sensor_profiles/probe')
    async def probe(bag: str):
        return await asyncio.to_thread(call, service.probe, bag)

    @app.post('/api/playback/sensor_profiles')
    def create(bag: str, body: dict):
        return call(service.save, body.get('name'), body, bag, True)

    @app.put('/api/playback/sensor_profiles/{name}')
    def update(name: str, bag: str, body: dict):
        return call(service.save, name, body, bag, False)

    @app.delete('/api/playback/sensor_profiles/{name}')
    def delete(name: str):
        return call(service.delete, name)

    return service
