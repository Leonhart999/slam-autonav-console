"""nav_batch.py: NavBatchRunner validation, summary aggregation, spawn_to_map, and API routes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import nav_batch


class _FakeMaps:
    def __init__(self, preset='office'):
        self.preset = preset

    def assets(self, name):
        if name != 'office_map':
            from robot_console.navigation import NavigationError
            raise NavigationError('map_not_found', name=name)
        return {
            'name': name, 'preset': self.preset,
            'grid': {'resolution': 0.05, 'width': 400, 'height': 400, 'origin': [-10.0, -10.0, 0.0]},
            '_yaml': Path('/tmp/office_map/map.yaml'),
        }


def _parse_goals(body, _grid):
    goals = body.get('goals') or []
    if not isinstance(goals, list) or not goals:
        raise ValueError('bad goals')
    return [(float(g['x']), float(g['y']), float(g.get('yaw', 0.0))) for g in goals]


class NavBatchUnitTests(unittest.TestCase):
    def test_spawn_to_map(self):
        self.assertEqual(nav_batch.spawn_to_map((-6.5, -3.5, 0.0), (-6.5, -3.5, 0.0)), (0.0, 0.0, 0.0))
        rx, ry, ryaw = nav_batch.spawn_to_map((1.0, 2.0, 1.570796), (1.0, 1.0, 1.570796))
        self.assertAlmostEqual(rx, 1.0, places=3)
        self.assertAlmostEqual(ry, 0.0, places=3)
        self.assertAlmostEqual(ryaw, 0.0, places=3)
        self.assertEqual(nav_batch.spawn_to_map((5.0, 5.0, 1.0), None), (0.0, 0.0, 0.0))

    def test_summarize_nav_runs(self):
        runs = [
            {'index': 1, 'ok': True, 'all_succeeded': True, 'goals': 3, 'finished': 3, 'succeeded': 3,
             'success_rate': 1.0, 'total_time_s': 20.0, 'mean_time_s': 6.67, 'total_path_m': 10.0,
             'mean_path_m': 3.33, 'recoveries': 0, 'mean_goal_error_m': 0.1, 'mean_loc_error_m': 0.05,
             'amcl_rmse_m': 0.06, 'min_clearance_m': 0.45, 'wall_s': 22.0},
            {'index': 2, 'ok': True, 'all_succeeded': False, 'goals': 3, 'finished': 3, 'succeeded': 2,
             'success_rate': 0.667, 'total_time_s': 30.0, 'mean_time_s': 10.0, 'total_path_m': 12.0,
             'mean_path_m': 4.0, 'recoveries': 2, 'mean_goal_error_m': 0.2, 'mean_loc_error_m': 0.15,
             'amcl_rmse_m': 0.08, 'min_clearance_m': 0.25, 'wall_s': 32.0},
            {'index': 3, 'ok': False, 'code': 'nav_not_ready'},
        ]
        s = nav_batch.summarize_nav_runs(runs)
        self.assertEqual(s['runs'], 3)
        self.assertEqual(s['completed'], 2)
        self.assertEqual(s['all_goals_runs'], 1)
        self.assertEqual((s['total_goals'], s['total_succeeded']), (6, 5))
        self.assertAlmostEqual(s['pooled_success_rate'], round(5 / 6, 4))
        self.assertAlmostEqual(s['total_time_s']['mean'], 25.0)
        self.assertEqual(s['total_recoveries'], 2)
        self.assertAlmostEqual(s['min_clearance_m']['min'], 0.25)


class NavBatchRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-navbatch-test-')
        self.runs = Path(self.tmp.name) / 'runs'
        self.runs.mkdir()
        self.cfg = {
            'mapping': {'runs_dir': str(self.runs), 'gt_topic': '/ground_truth'},
            'presets': {'office': {'args': {'sim': ['world:=indoor_office.sdf']}}},
            'nav_batch': {'max_runs': 10},
        }
        self.procs = SimpleNamespace(get=lambda n: None)
        self.node = SimpleNamespace(snapshot=lambda: {'topics': [], 'clock': None})
        self.helpers = {
            'nav_name': 'navigation', 'sim_name': 'sim',
            'variants': lambda: {'navfn_dwb': '/tmp/a.yaml', 'smac_mppi': '/tmp/b.yaml'},
            'default_variant': 'navfn_dwb', 'scale_range': (0.5, 4.0), 'goal_timeout': 240.0,
            'parse_goals': _parse_goals, 'map_meta': lambda a: {},
            'prepare_params': lambda src, s: [], 'set_active_run': lambda v, p, l: None,
            'speed_fn': lambda: 1.0, 'mapping_batch_running': lambda: False, 'other_busy': lambda: [],
        }
        self.app = FastAPI()
        self.runner = nav_batch.register_nav_batch_routes(
            self.app, self.cfg, self.node, self.procs, _FakeMaps(), SimpleNamespace(), SimpleNamespace(), self.helpers
        )
        self.endpoints = {r.path: r.endpoint for r in self.app.routes if hasattr(r, 'endpoint')}

    def tearDown(self):
        self.tmp.cleanup()

    def test_options_and_history_and_validation(self):
        d = self.endpoints['/api/nav-batch/options']()
        self.assertEqual(d['variants'], ['navfn_dwb', 'smac_mppi'])
        self.assertEqual(d['localizers'], ['amcl', 'gt'])

        with self.assertRaises(HTTPException) as c:
            self.endpoints['/api/nav-batch/start']({
                'map': 'office_map', 'variant': 'bad', 'localizer': 'amcl',
                'goals': [{'x': 1, 'y': 0, 'yaw': 0}], 'n': 3,
            })
        self.assertEqual((c.exception.status_code, c.exception.detail['code']), (400, 'navigation_variant_unknown'))

        with self.assertRaises(HTTPException) as c:
            self.endpoints['/api/nav-batch/start']({
                'map': 'office_map', 'variant': 'smac_mppi', 'localizer': 'lidar3d',
                'goals': [{'x': 1, 'y': 0, 'yaw': 0}], 'n': 3,
            })
        self.assertEqual((c.exception.status_code, c.exception.detail['code']), (400, 'navigation_localizer_unknown'))

        bid = 'nav_batch_20261116_120000'
        (self.runs / bid).mkdir()
        (self.runs / bid / 'batch.json').write_text(json.dumps({
            'id': bid, 'type': 'batch_navigation', 'created': '2026-11-16 12:00:00',
            'map': 'office_map', 'preset': 'office', 'variant': 'smac_mppi', 'localizer': 'gt',
            'speed_scale': 1.0, 'n': 2, 'goals': [{'x': 1, 'y': 0, 'yaw': 0}],
            'state': 'finished', 'runs': [{'index': 1, 'run_id': 'nav_1', 'ok': True}], 'summary': {'completed': 1},
        }), encoding='utf-8')
        hist = self.endpoints['/api/nav-batch/history']()
        self.assertEqual(len(hist), 1)
        self.assertEqual((hist[0]['variant'], hist[0]['localizer'], hist[0]['run_ids']), ('smac_mppi', 'gt', ['nav_1']))
        self.assertEqual(self.endpoints['/api/nav-batch/{batch_id}'](bid)['id'], bid)
        with self.assertRaises(HTTPException) as c:
            self.endpoints['/api/nav-batch/{batch_id}']('bad_id')
        self.assertEqual(c.exception.status_code, 404)


if __name__ == '__main__':
    unittest.main()
