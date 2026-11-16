"""slam_config: layered SLAM configuration (base -> input -> options -> console -> user) and the shipped
FAST-LIO specification, which must reproduce the four hand-written files it replaced."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import slam_config as sc  # noqa: E402
from robot_console.mapping import slam_profiles  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / 'config' / 'slam' / 'fastlio.yaml'
MAP = '/home/USER/results/fastlio_map.pcd'
CONSOLE = {'common.lid_topic': '/points_fastlio', 'common.imu_topic': '/imu', 'map_file_path': MAP}
# The hand-written files (sim_robot/config) the specification replaced: (input kind, options) -> file
LEGACY = {('sim', ()): 'fastlio_sim_vlp16.yaml', ('sim', ('lidar',)): 'fastlio_lidar_only.yaml',
          ('adapter', ()): 'fastlio_bag.yaml', ('adapter', ('lidar',)): 'fastlio_bag_lidar_only.yaml'}
# Parameters those files leave to FAST-LIO's declared defaults
DECLARED = {'common.imu_en': True, 'mapping.nn_every_iter': False}


def legacy_dir():
    """sim_robot/config with the old files: beside this repository, or the installed package; None if absent."""
    for d in (REPO.parent / 'sim_robot' / 'config', REPO.parent.parent / 'sim_robot' / 'config'):
        if (d / 'fastlio_sim_vlp16.yaml').is_file():
            return d
    try:
        from ament_index_python.packages import get_package_share_directory
        d = Path(get_package_share_directory('sim_robot')) / 'config'
        return d if (d / 'fastlio_sim_vlp16.yaml').is_file() else None
    except Exception:
        return None


class Layers(unittest.TestCase):
    def setUp(self):
        self.spec = sc.load_spec(str(SPEC))

    def test_flatten_round_trip(self):
        tree = {'a': 1, 'b': {'c': [1.0, 2.0], 'd': {'e': 'x'}}}
        flat = sc.flatten(tree)
        self.assertEqual(flat, {'a': 1, 'b.c': [1.0, 2.0], 'b.d.e': 'x'})
        self.assertEqual(sc.unflatten(flat), tree)

    def test_sim_values(self):
        p, src = sc.build(self.spec, 'sim', [], CONSOLE)
        self.assertEqual((p['preprocess.scan_line'], p['preprocess.blind'], p['mapping.extrinsic_T']),
                         (16, 0.4, [0.1, 0.0, 0.3]))
        self.assertEqual((p['common.imu_en'], p['ground_constraint.enable']), (True, False))
        self.assertEqual(p['map_file_path'], MAP)
        self.assertEqual((src['point_filter_num'], src['preprocess.blind'], src['map_file_path']),
                         ('base', 'input:sim', 'console'))

    def test_layer_order(self):
        """options win over the input, the user over everything."""
        p, src = sc.build(self.spec, 'adapter', ['lidar', 'ground'], CONSOLE, {'preprocess.blind': 1})
        self.assertEqual((p['preprocess.scan_line'], src['preprocess.scan_line']), (256, 'option:lidar'))
        self.assertEqual((p['mapping.extrinsic_T'], src['mapping.extrinsic_T']), ([0.0, 0.0, 0.0], 'input:adapter'))
        self.assertEqual((p['common.imu_en'], p['mapping.nn_every_iter'], p['ground_constraint.enable']),
                         (False, True, True))
        self.assertEqual((p['preprocess.blind'], src['preprocess.blind']), (1.0, 'user'))
        self.assertIsInstance(p['preprocess.blind'], float)      # an integer for a float parameter is converted

    def test_rejects(self):
        for user in ({'preprocess.blnd': 0.5},                   # misspelt key
                     {'point_filter_num': 2.5},                  # float for an integer
                     {'common.imu_en': 1},                       # number for a bool
                     {'mapping.extrinsic_T': [0.0, 0.0]},        # wrong length
                     {'preprocess.blind': 'near'}):
            with self.assertRaises(sc.SlamConfigError, msg=str(user)):
                sc.build(self.spec, 'sim', [], CONSOLE, user)
        with self.assertRaises(sc.SlamConfigError):
            sc.build(self.spec, 'livox', [], CONSOLE)
        with self.assertRaises(sc.SlamConfigError):
            sc.build(self.spec, 'sim', ['loop'], CONSOLE)

    def test_written_file_keeps_types(self):
        """The file FAST-LIO reads: same values, floats still floats (its parameters are typed)."""
        p, _ = sc.build(self.spec, 'sim', ['lidar'], CONSOLE)
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'sub' / 'x.yaml')
            sc.write_ros_params(path, p, 'line one\nline two')
            text = Path(path).read_text()
            back = sc.flatten(yaml.safe_load(text)['/**']['ros__parameters'])
        self.assertTrue(text.startswith('# line one\n# line two\n'))
        self.assertEqual(back, p)
        for key, value in p.items():
            self.assertIs(type(back[key]), type(value), key)
        self.assertIsInstance(back['mapping.b_acc_cov'], float)
        self.assertIsInstance(back['cube_side_length'], float)

    def test_same_as_replaced_files(self):
        """Exactly the parameters of the four hand-written files (values and types)."""
        d = legacy_dir()
        if d is None:
            self.skipTest('sim_robot/config not found')
        for (kind, options), name in LEGACY.items():
            old = sc.flatten(yaml.safe_load((d / name).read_text())['/**']['ros__parameters'])
            old = {**DECLARED, **old}
            new, _ = sc.build(self.spec, kind, options, {**CONSOLE, 'map_file_path': old['map_file_path']})
            self.assertEqual(new, old, name)
            for key in new:
                self.assertIs(type(new[key]), type(old[key]), f'{name}: {key}')

    def test_validated_records(self):
        for v in self.spec['validated']:
            self.assertIn(v['input'], self.spec['inputs'])
            for o in v.get('options') or []:
                self.assertIn(o, self.spec['options'])
        self.assertIsNotNone(sc.validated_entry(self.spec, 'sim', ['ground', 'lidar']))
        self.assertIsNone(sc.validated_entry(self.spec, 'adapter', ['lidar']))
        self.assertIn('warehouse', sc.validated_entry(self.spec, 'sim', [])['data']['en'])
        v = sc.validated_entry(self.spec, 'adapter', ['ground'])
        self.assertTrue(v['data']['zh'] and v['data']['en'] and v['result']['zh'] and v['result']['en'])


class Stacks(unittest.TestCase):
    """SlamConfigs on the shipped console.yaml / stacks.yaml."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = yaml.safe_load((REPO / 'config' / 'console.yaml').read_text())['mapping']
        for prof in cfg['slam_profiles'].values():
            if prof.get('config'):
                prof['config'] = str(REPO / 'config' / prof['config'])
            if (prof.get('save') or {}).get('pcd_path'):
                prof['save']['pcd_path'] = MAP
        cfg['slam_config_dir'] = self.tmp.name
        cfg['slam_overrides_file'] = str(Path(self.tmp.name) / 'overrides.yaml')
        cfg['slam_facts_file'] = str(Path(self.tmp.name) / 'sensor_facts.yaml')
        self.profiles = slam_profiles(cfg)
        # ${pkg:sim_robot} of glim.yaml: the sim_robot source beside this repository, when it is there
        sim_robot = legacy_dir().parent if legacy_dir() else Path('/nonexistent')
        self.expand = lambda s: str(s).replace('${pkg:sim_robot}', str(sim_robot))
        self.has_sim_robot = (sim_robot / 'config' / 'glim' / 'config.json').is_file()
        self.configs = sc.SlamConfigs(cfg, self.profiles, expand=self.expand)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fastlio_stacks_configured(self):
        self.assertEqual(sorted(n for n in self.configs.specs if 'fastlio' in n),
                         ['mapping_fastlio', 'mapping_fastlio_ground',
                          'mapping_fastlio_lidar', 'mapping_fastlio_lidar_ground'])
        self.assertTrue(self.configs.has('mapping_glim_lidar'))
        self.assertEqual(self.configs.has('mapping_glim'), self.has_sim_robot)

    def test_start_args(self):
        extra = ['config_path:=/old', 'config_file:=fastlio_bag.yaml', 'use_sim_time:=true']
        args = self.configs.start_args('mapping_fastlio_lidar_ground', extra, 'sensor:heu61')
        path = Path(self.tmp.name) / 'mapping_fastlio_lidar_ground.yaml'
        self.assertEqual(args, ['use_sim_time:=true', f'config_path:={self.tmp.name}',
                                'config_file:=mapping_fastlio_lidar_ground.yaml'])
        p = sc.flatten(yaml.safe_load(path.read_text())['/**']['ros__parameters'])
        self.assertEqual((p['preprocess.blind'], p['common.imu_en'], p['ground_constraint.enable'],
                          p['map_file_path'], p['common.lid_topic']), (0.5, False, True, MAP, '/points_fastlio'))
        rec = self.configs.info('mapping_fastlio_lidar_ground')
        self.assertEqual((rec['input'], rec['sensor'], rec['options'], rec['modified']),
                         ('adapter', 'heu61', ['ground', 'lidar'], False))
        self.assertEqual(rec['changed']['preprocess.scan_line'], {'value': 256, 'from': 'option:lidar'})
        self.assertEqual(rec['params'], p)
        self.assertIsNone(self.configs.info('mapping_fastlio'))          # never started

    def test_input_of(self):
        self.assertEqual(self.configs.input_of(None), ('sim', None))
        self.assertEqual(self.configs.input_of('warehouse'), ('sim', None))
        self.assertEqual(self.configs.input_of('sensor:my_robot'), ('adapter', 'my_robot'))

    def test_validated_marks(self):
        self.assertIsNotNone(self.configs.plan('mapping_fastlio_ground', 'sensor:heu61')['validated'])
        self.assertIsNone(self.configs.plan('mapping_fastlio', 'sensor:heu61')['validated'])
        self.assertIsNotNone(self.configs.plan('mapping_fastlio', 'office')['validated'])

    def test_ground_launch_argument_agrees(self):
        """stacks.yaml still passes ground_constraint:= to the launch file, which overrides the parameter
        file: both must say the same."""
        stacks = yaml.safe_load((REPO / 'config' / 'stacks.yaml').read_text())
        for name in self.configs.specs:
            arg = next((a for a in stacks[name]['cmd'] if str(a).startswith('ground_constraint:=')), None)
            if arg is not None:
                want = self.configs.plan(name)['params']['ground_constraint.enable']
                self.assertEqual(arg.split(':=')[1] == 'true', want, name)

    def test_user_values(self):
        """Stored per stack and data source; outside the tunable list or range is refused."""
        c = self.configs
        d = c.describe('mapping_fastlio', 'sensor:heu61')
        self.assertEqual((d['scope'], d['modified'], d['user_count']), ('sensor:heu61', False, 0))
        self.assertEqual([r['key'] for r in d['params']][:2], ['point_filter_num', 'filter_size_surf'])
        row = {r['key']: r for r in d['params']}
        self.assertEqual((row['preprocess.blind']['base'], row['preprocess.blind']['auto'],
                          row['preprocess.blind']['source']), (0.4, 0.5, 'input:adapter'))
        self.assertEqual(row['preprocess.blind']['tunable']['type'], 'float')
        self.assertIsNone(row['mapping.extrinsic_T']['tunable'])

        # blind equal to the automatic value is not stored; an integer for a float is converted
        d = c.set_user('mapping_fastlio', 'sensor:heu61', {'preprocess.blind': 0.5, 'filter_size_surf': 1,
                                                          'point_filter_num': 2})
        self.assertEqual((d['modified'], d['user_count']), (True, 2))
        row = {r['key']: r for r in d['params']}
        self.assertEqual((row['filter_size_surf']['user'], row['filter_size_surf']['value']), (1.0, 1.0))
        self.assertIsNone(row['preprocess.blind']['user'])
        rec = c.plan('mapping_fastlio', 'sensor:heu61')
        self.assertEqual((rec['modified'], rec['user']), (True, {'filter_size_surf': 1.0, 'point_filter_num': 2}))
        self.assertEqual(rec['changed']['point_filter_num'], {'value': 2, 'from': 'user'})
        # another data source and another stack are untouched
        self.assertFalse(c.plan('mapping_fastlio', 'office')['modified'])
        self.assertFalse(c.plan('mapping_fastlio_ground', 'sensor:heu61')['modified'])
        # a second console reading the same file sees the values
        again = sc.SlamConfigs({'slam_config_dir': self.tmp.name, 'slam_overrides_file': c.overrides.path},
                               self.profiles, expand=self.expand)
        self.assertEqual(again.plan('mapping_fastlio', 'sensor:heu61')['params']['point_filter_num'], 2)

        for bad in ({'mapping.extrinsic_T': [0.0, 0.0, 1.0]},      # not tunable
                    {'point_filter_num': 0}, {'filter_size_surf': 5.0},   # outside the range
                    {'point_filter_num': 2.5}, {'mapping.nn_every_iter': 1}):
            with self.assertRaises(sc.SlamConfigError, msg=str(bad)):
                c.set_user('mapping_fastlio', 'sensor:heu61', bad)
        self.assertEqual(c.plan('mapping_fastlio', 'sensor:heu61')['user'],
                         {'filter_size_surf': 1.0, 'point_filter_num': 2})   # a refused change stores nothing

        d = c.set_user('mapping_fastlio', 'sensor:heu61', {})               # reset
        self.assertEqual((d['modified'], c.overrides.get('mapping_fastlio', 'sensor:heu61')), (False, {}))

    def test_stale_stored_value_is_ignored(self):
        c = self.configs
        c.overrides.set('mapping_fastlio', 'sim', {'no.such.key': 1, 'point_filter_num': 999, 'max_iteration': 5})
        self.assertEqual(c.plan('mapping_fastlio')['user'], {'max_iteration': 5})

    def test_tunable_ranges_hold_every_layer_value(self):
        """No layer of a shipped specification sets a tunable parameter outside its range."""
        for stack in ('mapping_fastlio', 'mapping_glim_lidar'):
            spec = self.configs.specs[stack]
            for layer in [spec['base'], *spec['inputs'].values(), *spec['options'].values()]:
                sc.check_user(spec, {k: v for k, v in layer.items() if k in spec['tunable']})
            for t in spec['tunable'].values():
                self.assertTrue(t['label']['zh'] and t['label']['en'] and t['help']['zh'] and t['help']['en'])

    def test_derived_values(self):
        """Values computed from the measured facts of a sensor profile: enabled rules are applied, the others
        only suggested; the simulation and a profile without facts keep the fixed input values."""
        c = self.configs
        self.assertEqual(c.plan('mapping_fastlio', 'sensor:heu61')['params']['preprocess.scan_line'], 128)
        c.facts.set('heu61', {'rings': 224, 'scan_rate': 9.72, 'points_per_scan': 125759, 'imu_rate': 200.0})
        rec = c.plan('mapping_fastlio', 'sensor:heu61')
        self.assertEqual((rec['params']['preprocess.scan_line'], rec['changed']['preprocess.scan_line']['from']),
                         (224, 'derived:rings'))
        self.assertEqual((rec['params']['preprocess.scan_rate'], rec['changed']['preprocess.scan_rate']['from']),
                         (10, 'derived:scan_rate'))
        self.assertEqual(rec['facts']['rings'], 224)
        self.assertFalse(rec['modified'])                       # derived values are not user changes
        spec = c.specs['mapping_fastlio']
        d = sc.derive(spec, 'adapter', rec['facts'])['point_filter_num']
        self.assertEqual((d['value'], d['rule']), (17, 'decimate'))
        rows = {r['key']: r for r in c.describe('mapping_fastlio', 'sensor:heu61')['params']}
        if d['enabled']:
            self.assertEqual((rows['point_filter_num']['auto'], rows['point_filter_num']['suggest']), (17, None))
        else:
            self.assertEqual((rows['point_filter_num']['auto'], rows['point_filter_num']['suggest']),
                             (4, {'value': 17, 'rule': 'decimate'}))
        # the LiDAR-only switch sets scan_line after the derived value: the rule's value is only suggested
        rows = {r['key']: r for r in c.describe('mapping_fastlio_lidar', 'sensor:heu61')['params']}
        self.assertEqual((rows['preprocess.scan_line']['auto'], rows['preprocess.scan_line']['source'],
                          rows['preprocess.scan_line']['suggest']['value']), (256, 'option:lidar', 224))
        # other data sources are untouched
        self.assertEqual(c.plan('mapping_fastlio', 'office')['params']['preprocess.scan_line'], 16)
        self.assertIsNone(c.plan('mapping_fastlio', 'office')['facts'])
        self.assertEqual(c.plan('mapping_fastlio', 'sensor:other')['params']['preprocess.scan_line'], 128)

    def test_rules(self):
        self.assertEqual(sc.RULES['rings']({'rings': 32}, {}), 32)
        self.assertIsNone(sc.RULES['rings']({'rings': None}, {}))
        self.assertEqual(sc.RULES['scan_rate']({'scan_rate': 19.6}, {}), 20)
        self.assertIsNone(sc.RULES['scan_rate']({}, {}))
        p = {'target': 7200, 'min': 1, 'max': 20}
        self.assertEqual([sc.RULES['decimate']({'points_per_scan': n}, p) for n in (2000, 28708, 125759, 900000)],
                         [1, 4, 17, 20])
        self.assertIsNone(sc.RULES['decimate']({}, p))
        rn = {'sparse_max_rings': 24, 'sparse': 30, 'dense': 20}
        self.assertEqual([sc.RULES['ring_neighbors']({'rings': r}, rn) for r in (16, 24, 64, 224)], [30, 30, 20, 20])
        self.assertIsNone(sc.RULES['ring_neighbors']({}, rn))
        # incomplete facts give no value instead of an error
        spec = self.configs.specs['mapping_fastlio']
        self.assertEqual(sc.derive(spec, 'adapter', {'rings': 'many'}), {})
        self.assertEqual(sc.derive(spec, 'adapter', None), {})
        self.assertEqual(sc.derive(spec, 'sim', {'rings': 64}), {})

    def test_measure_facts(self):
        from robot_console.sensor_profiles import measure_facts
        bag = {'path': '/bags/HEU61', 'duration': 1125.0,
               'topics': [{'name': '/front_lidar', 'count': 11250, 'type': 'sensor_msgs/msg/PointCloud2'},
                          {'name': '/rear_lidar', 'count': 11250, 'type': 'sensor_msgs/msg/PointCloud2'},
                          {'name': '/front_lidar/imu', 'count': 225000, 'type': 'sensor_msgs/msg/Imu'}]}
        probe = {'clouds': {'/front_lidar': {'finite': 62939, 'stats': {'ring': {'min': 0.0, 'max': 95.0, 'median': 53.0}}},
                            '/rear_lidar': {'finite': 62820, 'stats': {'ring': {'min': 0.0, 'max': 95.0, 'median': 54.0}}}}}
        profile = {'points': '/front_lidar', 'points2': '/rear_lidar', 'imu': '/front_lidar/imu'}
        f = measure_facts(bag, profile, probe)
        self.assertEqual((f['rings'], f['points_per_scan'], f['scan_rate'], f['imu_rate'], f['bag']),
                         (224, 125759, 10.0, 200.0, 'HEU61'))
        one = measure_facts(bag, {'points': '/front_lidar'}, probe)
        self.assertEqual((one['rings'], one['points_per_scan'], one['imu_rate']), (96, 62939, None))
        # no ring field in the cloud: the rule has nothing to say
        self.assertIsNone(measure_facts(bag, {'points': '/front_lidar', 'ring_field': ''}, probe)['rings'])

    def test_json_comments(self):
        text = '{ /* block\n comment */ "a": 1, // line\n "url": "http://x/*y*/", "q": "a\\"b//c" }'
        self.assertEqual(json.loads(sc.strip_json_comments(text)), {'a': 1, 'url': 'http://x/*y*/', 'q': 'a"b//c'})

    def test_glim_lidar_folder(self):
        """GLIM (LiDAR only): the folder of JSON files GLIM starts with, the cloud topic from console.yaml."""
        c = self.configs
        base = sc.flatten(sc.load_json_dir(str(REPO / 'config' / 'glim_lidar_only')))
        rec = c.plan('mapping_glim_lidar')
        self.assertEqual(rec['params'], base)                    # nothing but the shipped folder
        self.assertEqual(rec['params']['config_ros.glim_ros.points_topic'], '/playback/points')
        c.set_user('mapping_glim_lidar', None, {'config_preprocess.preprocess.k_correspondences': 15})
        args = c.start_args('mapping_glim_lidar', ['config_path:=/old', 'use_sim_time:=true'], None)
        folder = Path(self.tmp.name) / 'mapping_glim_lidar'
        self.assertEqual(args, ['use_sim_time:=true', f'config_path:={folder}'])
        written = sc.load_json_dir(str(folder))
        self.assertEqual(sorted(written), sorted(sc.load_json_dir(str(REPO / 'config' / 'glim_lidar_only'))))
        self.assertEqual(written['config_preprocess']['preprocess']['k_correspondences'], 15)
        self.assertEqual(written['config']['global']['config_odometry'], 'config_odometry_ct.json')
        self.assertTrue(c.info('mapping_glim_lidar')['modified'])
        (folder / 'stale.json').write_text('{}')
        c.start_args('mapping_glim_lidar', [], None)
        self.assertFalse((folder / 'stale.json').exists())

    def test_glim_same_as_replaced_folder(self):
        """GLIM with IMU: for the simulation exactly the hand-written folder; identity extrinsic for the adapter."""
        if not self.has_sim_robot:
            self.skipTest('sim_robot/config/glim not found')
        c = self.configs
        old = sc.flatten(sc.load_json_dir(str(legacy_dir() / 'glim')))
        new = c.plan('mapping_glim')['params']
        self.assertEqual(new, old)
        for key in new:
            self.assertIs(type(new[key]), type(old[key]), key)
        real = c.plan('mapping_glim', 'sensor:heu61')
        self.assertEqual(real['params']['config_sensors.sensors.T_lidar_imu'], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
        self.assertEqual(real['changed']['config_preprocess.preprocess.k_correspondences']['from'], 'input:adapter')
        self.assertEqual(real['params']['config_ros.glim_ros.extension_modules'],
                         ['libmemory_monitor.so', 'librviz_viewer.so'])
        # with measured sensor facts, GLIM derives k_correspondences from the ring count
        c.facts.set('heu61', {'rings': 224})
        self.assertEqual((c.plan('mapping_glim', 'sensor:heu61')['params']['config_preprocess.preprocess.k_correspondences'],
                          c.plan('mapping_glim', 'sensor:heu61')['changed']['config_preprocess.preprocess.k_correspondences']['from']),
                         (20, 'derived:ring_neighbors'))
        c.facts.set('sparse16', {'rings': 16})
        self.assertEqual(c.plan('mapping_glim', 'sensor:sparse16')['params']['config_preprocess.preprocess.k_correspondences'], 30)
        self.assertEqual(real['params']['config_ros.glim_ros.imu_topic'], '/imu')
        spec = c.specs['mapping_glim']
        for layer in [spec['base'], *spec['inputs'].values()]:
            sc.check_user(spec, {k: v for k, v in layer.items() if k in spec['tunable']})

    def test_missing_specification_is_skipped(self):
        cfg = {'slam_config_dir': self.tmp.name}
        profiles = {'a': {'config': '/nonexistent/x.yaml', 'options': [], 'inputs': {}, 'pcd_path': None}}
        self.assertEqual(sc.SlamConfigs(cfg, profiles).specs, {})


if __name__ == '__main__':
    unittest.main()
