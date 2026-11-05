"""config.py: loading console.yaml / stacks.yaml / presets, '~' expansion and bilingual labels.

Needs ament_index_python (source /opt/ros/humble/setup.bash), nothing else from ROS.
"""
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import config

REPO_CONFIG = Path(__file__).resolve().parents[1] / 'config'
HOME = os.path.expanduser('~')


class HelperTests(unittest.TestCase):
    def test_bilingual(self):
        self.assertEqual(config.bilingual('Sim'), {'zh': 'Sim', 'en': 'Sim'})
        self.assertEqual(config.bilingual({'zh': '仿真'}), {'zh': '仿真', 'en': '仿真'})
        self.assertEqual(config.bilingual({'en': 'Sim'}), {'zh': 'Sim', 'en': 'Sim'})
        self.assertEqual(config.bilingual(None, 'x'), {'zh': 'x', 'en': 'x'})
        self.assertEqual(config.bilingual(None), {'zh': '', 'en': ''})

    def test_expand_arg(self):
        self.assertEqual(config._expand_arg('~/maps/a.yaml'), HOME + '/maps/a.yaml')
        self.assertEqual(config._expand_arg('map:=~/maps/a.yaml'), 'map:=' + HOME + '/maps/a.yaml')
        self.assertEqual(config._expand_arg('use_sim_time:=true'), 'use_sim_time:=true')
        self.assertEqual(config._expand_arg(5), '5')


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-config-test-')
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.dir / name).write_text(textwrap.dedent(text), encoding='utf-8')

    def test_minimal_config(self):
        self.write('console.yaml', """
            server: {host: 127.0.0.1, port: 8000, venv: ~/venvs/x, web_dist: ~/dist, log_dir: ~/logs}
            mapping: {presets_file: presets.yaml}
        """)
        self.write('stacks.yaml', """
            sim:
              label: {zh: 仿真, en: Simulation}
              cmd: [ros2, launch, sim_robot, sim.launch.py, 'config_path:=~/cfg']
            bare:
              cmd: [true]
        """)
        self.write('presets.yaml', """
            office:
              label: 办公室
              args: {waypoints: [--params-file, ~/route.yaml], sim: null}
        """)
        cfg = config.load(str(self.dir / 'console.yaml'))
        self.assertEqual(cfg['server']['venv'], HOME + '/venvs/x')
        self.assertEqual(cfg['stacks']['sim']['cmd'][-1], 'config_path:=' + HOME + '/cfg')
        self.assertEqual(cfg['stacks']['sim']['label'], {'zh': '仿真', 'en': 'Simulation'})
        self.assertEqual(cfg['stacks']['bare']['label'], {'zh': 'bare', 'en': 'bare'})
        self.assertEqual(cfg['stacks']['bare']['description'], {'zh': '', 'en': ''})
        self.assertEqual(cfg['presets']['office']['args'], {'waypoints': ['--params-file', HOME + '/route.yaml'],
                                                             'sim': []})
        for key in ('topics', 'tf_edges', 'estop', 'teleop'):
            self.assertIn(key, cfg)

    def test_shipped_config_loads(self):
        """The config files in the repository must stay loadable and self-consistent."""
        cfg = config.load(str(REPO_CONFIG / 'console.yaml'))
        stacks = cfg['stacks']
        for role, name in (cfg['mapping'].get('stacks') or {}).items():
            self.assertIn(name, stacks, f'mapping.stacks.{role} -> unknown stack {name}')
        for name, spec in stacks.items():
            self.assertTrue(spec['cmd'], name)
            self.assertTrue(all(isinstance(a, str) for a in spec['cmd']), name)
            self.assertTrue(spec['label']['zh'] and spec['label']['en'], name)
        default = cfg['mapping'].get('default_preset')
        if default:
            self.assertIn(default, cfg['presets'])
        for name, preset in cfg['presets'].items():
            for stack in preset['args']:
                self.assertIn(stack, (cfg['mapping'].get('stacks') or {}), f'preset {name}: unknown role {stack}')

    def test_shipped_config_is_machine_independent(self):
        """No paths of one particular machine in the shipped files (they belong in the user override)."""
        for f in REPO_CONFIG.glob('*.yaml'):
            text = f.read_text(encoding='utf-8')
            for bad in ('/mnt/', '/home/', 'ros2_ws', '毕设'):
                self.assertNotIn(bad, text, f'{f.name} contains {bad!r}')

    def test_placeholders(self):
        self.write('console.yaml', """
            paths: {data_root: ~/rc_data}
            server: {host: 127.0.0.1, port: 8000}
            mapping:
              slam_profiles:
                s: {scan_topic: /a, odom_topic: /b, save: {dump_dir: '${data_root}/dump'}}
        """)
        self.write('stacks.yaml', """
            s: {cmd: [x, '${data_root}/a', 'p:=${pkg:ament_index_python}/resource', 'q:=${pkg:no_such_pkg_xyz}']}
        """)
        cfg = config.load(str(self.dir / 'console.yaml'))
        root = HOME + '/rc_data'
        self.assertEqual(cfg['paths']['data_root'], root)
        cmd = cfg['stacks']['s']['cmd']
        self.assertEqual(cmd[1], root + '/a')
        self.assertTrue(cmd[2].startswith('p:=/') and cmd[2].endswith('/ament_index_python/resource'), cmd[2])
        self.assertEqual(cmd[3], 'q:=${pkg:no_such_pkg_xyz}')
        self.assertEqual(cfg['missing_packages'], ['no_such_pkg_xyz'])
        # Unset data folders default to data_root
        self.assertEqual(cfg['mapping']['maps_dir'], root + '/maps')
        self.assertEqual(cfg['mapping']['runs_dir'], root + '/runs')
        self.assertEqual(cfg['server']['log_dir'], root + '/logs')
        self.assertEqual(cfg['evaluation']['results_dir'], root + '/results')
        self.assertEqual(cfg['mapping']['slam_profiles']['s']['save']['dump_dir'], root + '/dump')

    def test_user_override(self):
        self.write('console.yaml', """
            server: {host: 127.0.0.1, port: 8000, log_dir: ~/logs}
            mapping: {maps_dir: '${data_root}/maps', voxel: 0.1}
            topics: [{name: /a}, {name: /b}]
        """)
        self.write('stacks.yaml', "{}\n")
        self.write('user.yaml', """
            server: {port: 8123}
            mapping: {maps_dir: ~/my_maps}
            topics: [{name: /c}]
        """)
        old = os.environ.get('ROBOT_CONSOLE_USER_CONFIG')
        os.environ['ROBOT_CONSOLE_USER_CONFIG'] = str(self.dir / 'user.yaml')
        try:
            cfg = config.load(str(self.dir / 'console.yaml'))
        finally:
            if old is None:
                os.environ.pop('ROBOT_CONSOLE_USER_CONFIG')
            else:
                os.environ['ROBOT_CONSOLE_USER_CONFIG'] = old
        self.assertEqual(cfg['server']['port'], 8123)
        self.assertEqual(cfg['server']['host'], '127.0.0.1')          # untouched keys stay
        self.assertEqual(cfg['mapping']['maps_dir'], HOME + '/my_maps')
        self.assertEqual(cfg['mapping']['voxel'], 0.1)
        self.assertEqual(cfg['topics'], [{'name': '/c'}])               # lists are replaced
        self.assertEqual(cfg['config_files'][-1], str(self.dir / 'user.yaml'))

    def test_explicit_config_ignores_home_override(self):
        """--config (test instances) must not pick up ~/.config/robot_console/console.yaml."""
        old = os.environ.pop('ROBOT_CONSOLE_USER_CONFIG', None)
        try:
            self.assertIsNone(config.user_config_path(explicit=True))
        finally:
            if old is not None:
                os.environ['ROBOT_CONSOLE_USER_CONFIG'] = old

    def test_preset_requires(self):
        """A preset whose required file is missing is hidden (and the default preset moves on)."""
        self.write('console.yaml', """
            server: {host: 127.0.0.1, port: 8000}
            mapping: {presets_file: presets.yaml, default_preset: bim}
        """)
        self.write('stacks.yaml', "{}\n")
        (self.dir / 'here.txt').write_text('x')
        self.write('presets.yaml', f"""
            bim: {{label: B, requires: ['{self.dir}/missing_model.sdf']}}
            office: {{label: O, requires: ['{self.dir}/here.txt']}}
        """)
        cfg = config.load(str(self.dir / 'console.yaml'))
        self.assertEqual(list(cfg['presets']), ['office'])
        self.assertEqual(cfg['unavailable_presets'], {'bim': [f'{self.dir}/missing_model.sdf']})
        self.assertEqual(cfg['mapping']['default_preset'], 'office')

    def test_merge(self):
        self.assertEqual(config.merge({'a': {'b': 1, 'c': 2}, 'l': [1]}, {'a': {'c': 3}, 'l': [2], 'n': None}),
                         {'a': {'b': 1, 'c': 3}, 'l': [2], 'n': None})


if __name__ == '__main__':
    unittest.main()
