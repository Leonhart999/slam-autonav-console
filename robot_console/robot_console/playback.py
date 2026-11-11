"""Rosbag playback (the Playback page): play a recorded bag instead of the simulation into a SLAM.

Flow on the page: (1) choose a bag, (2) start a SLAM, (3) choose the part of the bag, play / pause / change
the rate, (4) save the map.
The player is the whitelisted stack `playback` (stacks.yaml: `ros2 bag play`); this module only adds the
arguments: the bag path (must be one found under playback.bag_dirs), rate, topics, remaps and the clock.

Which topics are played: the inputs of the running SLAM (console.yaml mapping.slam_profiles.<name>.inputs,
e.g. {points: /points_fastlio, imu: /imu}) plus the ground truth topic if the bag has it. A bag topic with the
input's name and message type is used as is; otherwise the bag topic of that type with the most messages is
remapped onto the input name. A SLAM whose inputs the bag cannot provide (e.g. no IMU) is not offered.

Real robots (sensor profiles, config/sensor_profiles.yaml): a bag that has all `match` topics of a profile
is played through the sensor adapter (robot_console/sensor_adapter.py, stack `sensor_adapter`): the player
remaps the robot's cloud and IMU onto /playback/raw/*, the adapter converts them (extrinsics, units, point time)
and publishes the SLAM's inputs. The SLAM itself must then run the profile's configuration (slam_args), so it
is started from the Playback page with the bag (`bag=` on /api/stacks/{name}/start); its stack remembers the
profile as preset `sensor:<name>`, and a playback refuses a SLAM started for another sensor set-up.

Clock: bags recorded from the simulation contain /clock (simulation time, the stamps in their messages);
it is played as is. Other bags get `--clock`, so /clock follows the recording time.

Part of the bag: the start is `--start-offset`; Humble's player has no end option, so a monitor thread stops
the player when the position reaches the end. There is no seeking during a playback: a jump inside one SLAM
run breaks the SLAM (backwards it mixes two passes, forwards it sees a gap).

QoS: the player offers each topic the QoS it was recorded with. Bags written by other recorders (e.g. the
group's robot, libmcap) can carry `avoid_ros_namespace_conventions: true`; the player then publishes under the raw
DDS name and no ROS node ever sees the topic (the SLAM gets nothing, the player still runs to the end). For such
topics the recorded profile is passed back through `--qos-profile-overrides-path` with that flag off.

Run records: a SLAM run fed from a bag is recorded as type 'playback' (runs.py, session_info() below). Ground
truth is recorded only when the bag has the ground-truth topic; a sensor profile may name a `reference` odometry
(e.g. HEU61 wheel odometry), which is played onto REF_TOPIC and recorded as ref.tum (compared on the Evaluation
page as "deviation from reference", never as ATE). The monitor thread samples the SLAM lag about once a second.

Progress: with our own clock it is /clock - bag start (exact). With a recorded /clock (simulation time, other
offset) the first /clock is anchored to a wall-time estimate and the position follows /clock afterwards.

A SLAM that already received one playback is not fed a second one: the bag time would jump back and the SLAM
would mix both runs. Restart the SLAM first (the page says so).
"""
import os
import threading
import time

import yaml
from fastapi import HTTPException

PLAYER_NODE = '/rosbag2_player'
INPUT_TYPES = {'points': 'sensor_msgs/msg/PointCloud2', 'imu': 'sensor_msgs/msg/Imu'}
SCAN_DEPTH = 4              # how deep below a bag folder root bags are searched
CLOCK_HZ = 100              # /clock rate when the bag has no recorded clock
READY_CACHE = 1.0           # s, subscriber counts for the status are refreshed at most this often
LAG_DONE = 1.0              # s, after the playback the SLAM counts as caught up when its odometry is this close
MONITOR_PERIOD = 0.02       # s, how often the monitor thread checks the end of the chosen part (8x: 0.16 s of bag)
MIN_PART = 1.0              # s, shortest part of a bag that can be played
ADAPTER_IN = {'points': '/playback/raw/points', 'points2': '/playback/raw/points2',   # player -> sensor adapter
              'imu': '/playback/raw/imu'}
ADAPTER_READY = 20.0        # s to wait for the sensor adapter to subscribe before the player starts
REF_TOPIC = '/playback/ref_odom'   # a bag's reference odometry is remapped here and recorded as ref.tum (runs.py)
LAG_PERIOD = 1.0            # s between lag samples written into the run record (lag.csv)
SENSOR_PRESET = 'sensor:'   # stack preset of a SLAM started for a sensor profile
QOS_NAMES = {   # rmw enum numbers in metadata.yaml -> names accepted by --qos-profile-overrides-path
    'history': {0: 'system_default', 1: 'keep_last', 2: 'keep_all'},
    'reliability': {0: 'system_default', 1: 'reliable', 2: 'best_effort'},
    'durability': {0: 'system_default', 1: 'transient_local', 2: 'volatile'},
}
QOS_FILE = '/tmp/robot_console_playback_qos_{pid}.yaml'   # QoS overrides for the current playback


class PlaybackError(Exception):
    def __init__(self, code, status=409, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def storage_available(storage):
    """Can `ros2 bag play` open this storage format here? sqlite3 is built in; mcap needs its plugin."""
    if storage in ('sqlite3', None, ''):
        return True
    from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
    try:
        get_package_share_directory(f'rosbag2_storage_{storage}')
        return True
    except PackageNotFoundError:
        return False


def raw_name_qos(topic_metadata):
    """The recorded QoS profile of a topic with the ROS name conventions switched back on, or None when the
    recording already uses them (the normal case)."""
    try:
        profiles = yaml.safe_load(topic_metadata.get('offered_qos_profiles') or '') or []
    except yaml.YAMLError:
        return None
    if not isinstance(profiles, list) or not profiles or not isinstance(profiles[0], dict):
        return None
    if not any(isinstance(q, dict) and q.get('avoid_ros_namespace_conventions') for q in profiles):
        return None
    # `ros2 bag play` reads the override file with rclpy short names, not the enum numbers of metadata.yaml;
    # deadline / lifespan / liveliness stay at the defaults (the recorded "infinite" values overflow there)
    q = profiles[0]
    out = {'history': QOS_NAMES['history'].get(q.get('history'), 'keep_last'),
           'depth': max(int(q.get('depth') or 0), 10),
           'reliability': QOS_NAMES['reliability'].get(q.get('reliability'), 'reliable'),
           'durability': QOS_NAMES['durability'].get(q.get('durability'), 'volatile'),
           'avoid_ros_namespace_conventions': False}
    return out


def read_bag(path):
    """Summary of a rosbag2 folder from its metadata.yaml, or None when it is not a readable bag."""
    try:
        with open(os.path.join(path, 'metadata.yaml'), encoding='utf-8') as f:
            info = (yaml.safe_load(f) or {})['rosbag2_bagfile_information']
        entries = info.get('topics_with_message_count') or []
        topics = [{'name': t['topic_metadata']['name'], 'type': t['topic_metadata']['type'],
                   'count': int(t.get('message_count', 0))} for t in entries]
        qos_fix = {}
        for t in entries:
            q = raw_name_qos(t['topic_metadata'])
            if q is not None:
                qos_fix[t['topic_metadata']['name']] = q
        size = 0
        for name in info.get('relative_file_paths') or []:
            try:
                size += os.path.getsize(os.path.join(path, name))
            except OSError:
                pass
        return {
            'path': path,
            'storage': info.get('storage_identifier') or 'sqlite3',
            'duration': int(info['duration']['nanoseconds']) * 1e-9,
            'start': int(info['starting_time']['nanoseconds_since_epoch']) * 1e-9,
            'messages': int(info.get('message_count', 0)),
            'size': size,
            'topics': sorted(topics, key=lambda t: t['name']),
            'qos_fix': qos_fix,
        }
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def find_bags(roots):
    """Every bag folder (a folder with metadata.yaml) below the given roots, at most SCAN_DEPTH levels down.
    Hidden folders are skipped; a bag folder is not searched further."""
    out = []
    for root in roots:
        root = os.path.realpath(root)
        if not os.path.isdir(root):
            continue
        for d, subdirs, files in os.walk(root, followlinks=False):
            depth = 0 if d == root else os.path.relpath(d, root).count(os.sep) + 1
            subdirs[:] = sorted(s for s in subdirs if not s.startswith('.'))
            if 'metadata.yaml' in files:
                subdirs[:] = []
                bag = read_bag(d)
                if bag:
                    bag['root'] = root
                    bag['name'] = os.path.relpath(d, root) if d != root else os.path.basename(d)
                    out.append(bag)
            elif depth >= SCAN_DEPTH:
                subdirs[:] = []
    return out


def match_inputs(topics, inputs):
    """Map a SLAM's inputs onto bag topics. Returns (plan, missing):
    plan {kind: {'from': bag topic, 'to': SLAM topic}}, missing [kinds the bag has no topic for]."""
    plan, missing = {}, []
    for kind, target in (inputs or {}).items():
        typ = INPUT_TYPES.get(kind)
        cands = [t for t in topics if t['type'] == typ]
        pick = next((t for t in cands if t['name'] == target), None) or \
            (max(cands, key=lambda t: t['count']) if cands else None)
        if pick is None:
            missing.append(kind)
        else:
            plan[kind] = {'from': pick['name'], 'to': target}
    return plan, missing


def sensor_profile_of(bag, profiles):
    """Name of the first sensor profile whose `match` topics the bag has (with the right types), else None."""
    types = {t['name']: t['type'] for t in bag['topics']}
    for name, p in (profiles or {}).items():
        need = p.get('match') or [p.get('points')]
        if all(t in types for t in need) and types.get(p.get('points')) == INPUT_TYPES['points'] and \
                (not p.get('imu') or types.get(p['imu']) == INPUT_TYPES['imu']):
            return name
    return None


def profile_plan(profile, inputs):
    """Like match_inputs for a bag with a sensor profile: bag topic -> adapter input (via) -> SLAM input (to)."""
    plan, missing = {}, []
    for kind, target in (inputs or {}).items():
        src = profile.get(kind)
        if kind not in ADAPTER_IN or not src:
            missing.append(kind)
        else:
            plan[kind] = {'from': src, 'to': target, 'via': ADAPTER_IN[kind]}
    if profile.get('points2') and 'points' in plan:     # second lidar, merged into the same SLAM input
        plan['points2'] = {'from': profile['points2'], 'to': plan['points']['to'], 'via': ADAPTER_IN['points2']}
    return plan, missing


def play_args(bag, plan, rate, gt_topic=None, qos_path=None, ref=None):
    """Arguments appended to `ros2 bag play` (the stack command) for one playback. When a played topic needs a
    QoS override (see the module doc) and qos_path is given, the overrides are written there.
    ref: the bag's reference odometry topic, played onto REF_TOPIC."""
    names = {t['name'] for t in bag['topics']}
    own_clock = '/clock' not in names
    topics = sorted({p['from'] for p in plan.values()})
    if gt_topic and gt_topic in names:
        topics.append(gt_topic)
    if ref and ref in names:
        topics.append(ref)
    if not own_clock:
        topics.append('/clock')
    args = [bag['path'], '--rate', f'{rate:g}', '--disable-keyboard-controls', '--topics', *topics]
    remaps = [f"{p['from']}:={p.get('via') or p['to']}" for p in plan.values() if p['from'] != (p.get('via') or p['to'])]
    if ref and ref in names:
        remaps.append(f'{ref}:={REF_TOPIC}')
    if remaps:
        args += ['--remap', *remaps]
    if own_clock:
        args += ['--clock', str(CLOCK_HZ)]
    fix = {t: q for t, q in (bag.get('qos_fix') or {}).items() if t in topics}
    if fix and qos_path:
        with open(qos_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(fix, f, sort_keys=False)
        args += ['--qos-profile-overrides-path', qos_path]
    return args, own_clock


class Playback:

    def __init__(self, cfg, node, procs, mapping):
        p = cfg.get('playback') or {}
        self.stack = p.get('stack', 'playback')
        self.roots = [os.path.expanduser(d) for d in (p.get('bag_dirs') or [])]
        self.rates = sorted({float(r) for r in (p.get('rates') or [0.5, 1, 2, 4, 8]) if float(r) > 0} | {1.0})
        self.gt_topic = cfg['mapping'].get('gt_topic') or None
        self._qos_path = QOS_FILE.format(pid=os.getpid())
        self.sim_stacks = [n for n in (mapping.stacks.get('sim'), 'sim_gui') if n]
        self.adapter_stack = p.get('adapter_stack', 'sensor_adapter')
        self.profiles = p.get('sensor_profiles') or {}
        self.profiles_file = p.get('sensor_profiles_file') or ''
        self._node, self._procs, self._mapping = node, procs, mapping
        self._lock = threading.Lock()
        self._cur = None            # the current / last playback, see start()
        self._played = None         # (SLAM name, its started_at) that has already received a playback
        self._ready = (0.0, None)   # (monotonic time, subscriber count) cache for status()

    # ---------------- bag list ----------------

    def bags(self):
        """Bags with what each SLAM would get from them (shown on the page as the bag list)."""
        out = []
        for b in find_bags(self.roots):
            names = {t['name'] for t in b['topics']}
            sensor = sensor_profile_of(b, self.profiles)
            slams = {}
            for name, prof in self._mapping.profiles.items():
                if 'bag' not in prof['sources'] or not prof['inputs']:
                    continue
                plan, missing = self._plan(b, sensor, name, prof['inputs'])
                slams[name] = {'ok': not missing, 'missing': missing, 'plan': plan,
                               'remapped': any(p['from'] != p['to'] for p in plan.values())}
            supported = storage_available(b['storage'])
            out.append({
                **{k: b[k] for k in ('path', 'name', 'root', 'storage', 'messages', 'size', 'topics')},
                'duration': round(b['duration'], 2), 'start': b['start'],
                'supported': supported, 'reason': None if supported else 'storage',
                'points': [t['name'] for t in b['topics'] if t['type'] == INPUT_TYPES['points']],
                'imu': [t['name'] for t in b['topics'] if t['type'] == INPUT_TYPES['imu']],
                'has_clock': '/clock' in names, 'has_gt': bool(self.gt_topic and self.gt_topic in names),
                'reference': self._reference(sensor, names),
                'slams': slams,
                'sensor': {'name': sensor, 'label': self.profiles[sensor]['label']} if sensor else None,
            })
        return out

    def _reference(self, sensor, names):
        """{topic, label} of the sensor profile's reference odometry when the bag has it, else None."""
        ref = (self.profiles[sensor].get('reference') or {}) if sensor else {}
        topic = ref.get('topic')
        if not topic or topic not in names:
            return None
        return {'topic': topic, 'label': ref.get('label') or topic}

    def _plan(self, bag, sensor, slam, inputs):
        """(plan, missing) for feeding `slam` from `bag`; with a sensor profile, a SLAM the profile has no
        configuration for is missing 'sensor'."""
        if sensor is None:
            return match_inputs(bag['topics'], inputs)
        prof = self.profiles[sensor]
        if slam not in (prof.get('slam_args') or {}):
            return {}, ['sensor']
        return profile_plan(prof, inputs)

    def slam_start_args(self, slam, path):
        """(preset, extra args) for starting `slam` on the Playback page for the bag at `path`: the sensor
        profile's SLAM configuration, or (None, []) for a bag without a profile."""
        bag = self._find(path)
        sensor = sensor_profile_of(bag, self.profiles)
        if sensor is None:
            return None, []
        args = (self.profiles[sensor].get('slam_args') or {}).get(slam)
        if args is None:
            raise PlaybackError('playback_sensor_unsupported', name=slam, sensor=sensor)
        return SENSOR_PRESET + sensor, list(args)

    def _find(self, path):
        real = os.path.realpath(path or '')
        for b in find_bags(self.roots):
            if b['path'] == real:
                return b
        raise PlaybackError('bag_not_found', 404, path=path)

    # ---------------- state ----------------

    def _running_slam(self):
        for name in self._mapping.profiles:
            s = self._procs.get(name)
            if s is not None and s.state() in ('running', 'stopping'):
                return name, s
        return None, None

    def _player(self):
        return self._procs.get(self.stack)

    def player_active(self):
        s = self._player()
        return s is not None and s.state() in ('running', 'stopping')

    def _position(self, cur, now=None):
        """Seconds into the bag (caller holds the lock).
        Own clock: /clock - bag start. Recorded clock (simulation time, unknown offset): the first /clock seen
        is anchored to the wall-time estimate, later positions follow /clock, so a player that cannot reach
        the requested rate (8x) is not shown ahead of where it is."""
        now = time.monotonic() if now is None else now
        estimate = cur['pos0'] + (0.0 if cur['paused'] else (now - cur['wall0']) * cur['rate'])
        clock = self._node.sim_now()
        if cur['own_clock']:
            if clock is not None and clock >= cur['start'] - 1.0:
                cur['last_pos'] = clock - cur['start']
            pos = cur['last_pos']
        elif clock is not None:
            if cur.get('clock0') is None:
                cur['clock0'] = clock - estimate
            cur['last_pos'] = pos = clock - cur['clock0']
        else:
            pos = cur['last_pos'] if cur.get('clock0') is not None else estimate
        return min(max(pos, 0.0), cur['duration'])

    def _rebase(self, cur):
        """Before changing pause / rate: fold the elapsed time into pos0 (the wall-time estimate)."""
        now = time.monotonic()
        cur['pos0'] = cur['pos0'] + (0.0 if cur['paused'] else (now - cur['wall0']) * cur['rate'])
        cur['wall0'] = now

    def status(self):
        s = self._player()
        state = s.state() if s else 'idle'
        slam, ss = self._running_slam()
        clock = self._node.sim_now() if state == 'running' else None
        with self._lock:
            if self._cur and clock is not None:
                self._cur['clock_last'] = clock      # last /clock of the playback, kept after it ends
            cur = dict(self._cur) if self._cur else None
            finished = False
            if cur:
                pos = self._position(self._cur)
                # the whole part has been played: the player exited by itself, or the monitor stopped it at the
                # end of the part (finished already while the player is still shutting down)
                finished = (state == 'exited' and s.exit_code == 0) or cur.get('stopped_at_end', False)
                if finished:
                    pos = cur['end']
            restart = slam is not None and self._played == (slam, ss.started_at) and state not in (
                'running', 'stopping')
        out = {'stack': self.stack, 'available': s is not None, 'state': state, 'rates': self.rates,
               'roots': self.roots, 'slam': slam, 'needs_slam_restart': restart, 'slam_ready': False,
               'slam_lag': None, 'catching_up': False}
        if cur:
            out.update(bag=cur['path'], name=cur['name'], rate=cur['rate'], paused=cur['paused'],
                       duration=cur['duration'], position=round(pos, 2), plan=cur['plan'], finished=finished,
                       range=[round(cur['begin'], 2), round(cur['end'], 2)],
                       exit_code=s.exit_code if state == 'exited' else None)
            # run record of this playback (Evaluation page link); kept after the SLAM stopped. None until the
            # estimate moved 0.5 m (runs.py)
            rid = getattr(getattr(self._mapping, 'recorder', None), 'run_id', None)
            if rid and slam == cur['slam'] and self._cur is not None and self._cur.get('path') == cur['path']:
                self._cur['run'] = rid
            out['run'] = (self._cur or {}).get('run')
            # How far the SLAM's odometry is behind the playback clock: a heavy SLAM at 2x falls behind and
            # keeps working on queued scans after the bag has ended (saving then would cut the map short)
            stamp = self._mapping.last_stamp()
            if slam is not None and slam == cur['slam'] and cur.get('clock_last') is not None and stamp is not None:
                lag = max(0.0, cur['clock_last'] - stamp)
                out['slam_lag'] = round(lag, 1)
                out['catching_up'] = finished and lag > LAG_DONE
        if slam is not None:
            out['slam_ready'] = self._slam_listening(slam)
        return out

    def _slam_listening(self, slam):
        """Has the SLAM subscribed to its point cloud input yet (it takes a few seconds to start)?"""
        now = time.monotonic()
        t, n = self._ready
        if n is None or now - t > READY_CACHE or n[0] != slam:
            topic = (self._mapping.profiles[slam]['inputs'] or {}).get('points')
            count = self._node.count_subscribers(topic) if topic else 0
            self._ready = (now, (slam, count))
            n = (slam, count)
        return n[1] > 0

    def session_info(self):
        """Bag of the current SLAM run, written into run.json / meta.json (empty when it came from the sim).
        Uses the selected SLAM even after it exited: saving a GLIM map stops GLIM before meta.json is written."""
        slam = self._mapping.stacks.get('slam')
        s = self._procs.get(slam) if slam else None
        with self._lock:
            cur = self._cur
            if cur is None or s is None or self._played != (slam, s.started_at):
                return {}
            out = {'type': 'playback', 'source': 'bag', 'bag': cur['path'], 'bag_name': cur['name'],
                   'bag_rate': cur['rate'], 'sensor': cur.get('sensor'),
                   'bag_range': [round(cur['begin'], 2), round(cur['end'], 2)],
                   'gt_source': 'bag' if cur.get('has_gt') else 'none'}
            if cur.get('sensor'):
                out['sensor_label'] = self.profiles[cur['sensor']].get('label')
            if cur.get('ref'):
                out['ref_source'] = cur['ref']['topic']
                out['ref_label'] = cur['ref']['label']
            return out

    # ---------------- commands ----------------

    def start(self, path, rate=1.0, begin=0.0, end=None):
        """Play the part [begin, end] (s into the bag; end None = to the end) at `rate` into the running SLAM."""
        s = self._player()
        if s is None:
            raise PlaybackError('stack_not_found', 404, name=self.stack)
        if s.state() in ('running', 'stopping'):
            raise PlaybackError('stack_running', name=self.stack)
        sim = next((n for n in self.sim_stacks if self._procs.get(n)
                    and self._procs.get(n).state() in ('running', 'stopping')), None)
        if sim:
            raise PlaybackError('playback_sim_running', name=sim)
        if float(rate) not in self.rates:
            raise PlaybackError('playback_bad_rate', 400, rate=rate)
        bag = self._find(path)
        begin = float(begin or 0.0)
        end = bag['duration'] if end is None else min(float(end), bag['duration'])
        if begin < 0 or end - begin < MIN_PART:
            raise PlaybackError('playback_bad_range', 400, begin=round(begin, 1), end=round(end, 1))
        if not storage_available(bag['storage']):
            raise PlaybackError('bag_unsupported', storage=bag['storage'])
        slam, ss = self._running_slam()
        if slam is None or ss.state() != 'running':
            raise PlaybackError('playback_need_slam')
        prof = self._mapping.profiles[slam]
        if 'bag' not in prof['sources'] or not prof['inputs']:
            raise PlaybackError('playback_slam_unsupported', name=slam)
        sensor = sensor_profile_of(bag, self.profiles)
        plan, missing = self._plan(bag, sensor, slam, prof['inputs'])
        if 'sensor' in missing:
            raise PlaybackError('playback_sensor_unsupported', name=slam, sensor=sensor)
        if missing:
            labels = {'points': 'PointCloud2', 'imu': 'IMU'}
            raise PlaybackError('bag_missing_inputs', name=slam, inputs=', '.join(labels.get(k, k) for k in missing))
        if self._played == (slam, ss.started_at):
            raise PlaybackError('playback_restart_slam', name=slam)
        preset = getattr(ss, 'preset', None) or ''
        slam_sensor = preset[len(SENSOR_PRESET):] if preset.startswith(SENSOR_PRESET) else None
        if slam_sensor != sensor:
            # the SLAM runs the configuration of another sensor set-up (or the simulation's)
            raise PlaybackError('playback_sensor_restart', name=slam, sensor=sensor or '-')
        if self._node.count_subscribers(plan['points']['to']) == 0:
            raise PlaybackError('playback_slam_not_ready', name=slam, topic=plan['points']['to'])
        names = {t['name'] for t in bag['topics']}
        ref = self._reference(sensor, names)
        args, own_clock = play_args(bag, plan, float(rate), self.gt_topic, self._qos_path,
                                    ref['topic'] if ref else None)
        if begin > 0:
            args += ['--start-offset', f'{begin:g}']
        if sensor:
            self._start_adapter(sensor, plan)
        try:
            info = self._procs.start(self.stack, args, None, 'playback')
        except Exception:
            self._stop_adapter()
            raise
        with self._lock:
            self._played = (slam, ss.started_at)
            self._cur = {'path': bag['path'], 'name': os.path.relpath(bag['path'], bag['root'])
                         if bag['path'] != bag['root'] else os.path.basename(bag['path']),
                         'rate': float(rate), 'paused': False, 'duration': bag['duration'], 'start': bag['start'],
                         'begin': begin, 'end': end, 'own_clock': own_clock,
                         'pos0': begin, 'wall0': time.monotonic(), 'last_pos': begin, 'plan': plan, 'slam': slam,
                         'sensor': sensor, 'has_gt': bool(self.gt_topic and self.gt_topic in names), 'ref': ref}
            cur = self._cur
        threading.Thread(target=self._monitor, args=(cur,), daemon=True).start()
        return info

    def _start_adapter(self, sensor, plan):
        """Start the sensor adapter and wait until it listens, so the first scans of the bag are not lost."""
        a = self._procs.get(self.adapter_stack)
        if a is None:
            raise PlaybackError('stack_not_found', 404, name=self.adapter_stack)
        if a.state() in ('running', 'stopping'):
            self._procs.stop(self.adapter_stack)
        params = {'profiles_file': self.profiles_file, 'profile': sensor}
        for kind, p in plan.items():
            params[f'{kind}_in'] = p['via']
            if kind != 'points2':           # the second lidar goes into the same output as the first
                params[f'{kind}_out'] = p['to']
        args = ['--ros-args', *[x for k, v in params.items() for x in ('-p', f'{k}:={v}')]]
        self._procs.start(self.adapter_stack, args, None, 'playback')
        deadline = time.monotonic() + ADAPTER_READY
        while self._node.count_subscribers(plan['points']['via']) == 0:
            if a.state() != 'running' or time.monotonic() > deadline:
                self._stop_adapter()
                raise PlaybackError('playback_adapter_failed', 500, name=self.adapter_stack)
            time.sleep(0.2)

    def _stop_adapter(self):
        a = self._procs.get(self.adapter_stack)
        if a is not None and a.state() == 'running':
            self._procs.stop(self.adapter_stack)

    def _monitor(self, cur):
        """While the player runs: keep the last /clock (for the SLAM lag) and stop the player at the end of the
        chosen part. Runs whether or not a page polls the status. Stops the sensor adapter when the player ends."""
        try:
            self._watch(cur)
        finally:
            if self._cur is cur:
                self._stop_adapter()

    def _watch(self, cur):
        stop_at_end = cur['end'] < cur['duration'] - 0.05
        next_lag = 0.0
        while True:
            s = self._player()
            if s is None or s.state() != 'running' or self._cur is not cur:
                return
            clock = self._node.sim_now()
            now = time.monotonic()
            record_lag = getattr(self._mapping, 'record_lag', None)
            if clock is not None and record_lag and now >= next_lag and self._mapping.stacks.get('slam') == cur['slam']:
                next_lag = now + LAG_PERIOD
                record_lag(clock)
            with self._lock:
                if clock is not None:
                    cur['clock_last'] = clock
                pos = self._position(cur)
                done = stop_at_end and pos >= cur['end']
                if done:
                    cur['stopped_at_end'] = True
            if done:
                self._procs.stop(self.stack)
                return
            time.sleep(MONITOR_PERIOD)

    def _call(self, srv_name, srv_type, request, timeout=3.0):
        """Call a rosbag2 player service (blocking; not from the executor thread)."""
        name = f'{PLAYER_NODE}/{srv_name}'
        client = self._node.create_client(srv_type, name)
        try:
            if not client.wait_for_service(timeout_sec=2.0):
                raise PlaybackError('playback_service_missing', 503, service=name)
            done = threading.Event()
            future = client.call_async(request)
            future.add_done_callback(lambda _f: done.set())
            if not done.wait(timeout):
                raise PlaybackError('playback_service_timeout', 504, service=name)
            return future.result()
        finally:
            self._node.destroy_client(client)

    def _require_running(self):
        s = self._player()
        if s is None or s.state() != 'running' or self._cur is None:
            raise PlaybackError('playback_not_running')

    def pause(self, paused):
        from rosbag2_interfaces.srv import Pause, Resume
        self._require_running()
        if paused:
            self._call('pause', Pause, Pause.Request())
        else:
            self._call('resume', Resume, Resume.Request())
        with self._lock:
            self._rebase(self._cur)
            self._cur['paused'] = bool(paused)

    def set_rate(self, rate):
        from rosbag2_interfaces.srv import SetRate
        self._require_running()
        if float(rate) not in self.rates:
            raise PlaybackError('playback_bad_rate', 400, rate=rate)
        res = self._call('set_rate', SetRate, SetRate.Request(rate=float(rate)))
        if not res.success:
            raise PlaybackError('playback_service_failed', 500, service=f'{PLAYER_NODE}/set_rate')
        with self._lock:
            self._rebase(self._cur)
            self._cur['rate'] = float(rate)


def register_playback_routes(app, pb, procs):

    def fail(e):
        return HTTPException(e.status, detail={'code': e.code, 'params': e.params})

    @app.get('/api/playback/bags')
    def playback_bags():
        return {'roots': pb.roots, 'bags': pb.bags()}

    @app.get('/api/playback/status')
    def playback_status():
        return pb.status()

    @app.post('/api/playback/start')
    def playback_start(bag: str, rate: float = 1.0, begin: float = 0.0, end: float | None = None):
        try:
            pb.start(bag, rate, begin, end)
        except PlaybackError as e:
            raise fail(e)
        except OSError as e:
            raise HTTPException(500, detail={'code': 'start_failed', 'params': {'name': pb.stack, 'reason': str(e)}})
        return pb.status()

    @app.post('/api/playback/pause')
    def playback_pause(paused: bool = True):
        try:
            pb.pause(paused)
        except PlaybackError as e:
            raise fail(e)
        return pb.status()

    @app.post('/api/playback/rate')
    def playback_rate(rate: float):
        try:
            pb.set_rate(rate)
        except PlaybackError as e:
            raise fail(e)
        return pb.status()

    @app.post('/api/playback/stop')
    def playback_stop():
        threading.Thread(target=procs.stop, args=(pb.stack,), daemon=True).start()
        return pb.status()
