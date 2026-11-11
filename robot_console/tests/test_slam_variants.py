"""mapping.slam_profiles: variant profiles (variant_of + options) for the option switches on the pages,
and the shipped console.yaml (every variant points at a profile that exists, no two share an option set)."""
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.mapping import slam_profiles  # noqa: E402

BASE = {'scan_topic': '/s', 'odom_topic': '/o', 'save': {'service': '/save', 'pcd_path': '/tmp/m.pcd'}}


class VariantOptions(unittest.TestCase):
    def test_options(self):
        p = slam_profiles({'slam_profiles': {
            'a': dict(BASE),
            'a_x': dict(BASE, variant_of='a', options=['lidar', 'ground']),
            'a_y': dict(BASE, variant_of='a', option='ground'),      # older single-option key
            'b_z': dict(BASE, variant_of='missing', options=['lidar']),
        }})
        self.assertEqual((p['a']['variant_of'], p['a']['options']), (None, []))
        self.assertEqual((p['a_x']['variant_of'], p['a_x']['options']), ('a', ['ground', 'lidar']))
        self.assertEqual(p['a_y']['options'], ['ground'])
        # a variant of an unknown profile is shown as an algorithm of its own
        self.assertEqual((p['b_z']['variant_of'], p['b_z']['options']), (None, []))

    def test_shipped_config(self):
        cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / 'config' / 'console.yaml').read_text())
        p = slam_profiles(cfg['mapping'])
        seen = set()
        for name, prof in p.items():
            base = prof['variant_of'] or name
            self.assertIn(base, p)
            self.assertIsNone(p[base]['variant_of'], name)        # one level: variants of a base profile
            key = (base, tuple(prof['options']))
            self.assertNotIn(key, seen, name)
            seen.add(key)
        self.assertEqual(p['mapping_fastlio_lidar_ground']['options'], ['ground', 'lidar'])
        self.assertEqual(p['mapping_glim_lidar']['variant_of'], 'mapping_glim')


if __name__ == '__main__':
    unittest.main()
