"""nav_eval.py: per-run navigation metrics, pooling per Nav2 variant, AMCL series and CSV export.

Pure Python, no ROS needed.
"""
import csv
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console import nav_eval


def goal(result, t=10.0, path=5.0, err=0.1, loc=0.05, rec=0, clear=None):
    return {'result': result, 'time_s': t, 'path_m': path, 'goal_error_m': err,
            'loc_error_m': loc, 'recoveries': rec, 'min_clearance_m': clear}


def run(variant, goals, amcl=None):
    return {'variant': variant, 'map': 'office', 'state': 'done', 'ground_truth': True,
            'goals': goals, 'summary': {'amcl_error': amcl or {}}}


class NavMetricsTests(unittest.TestCase):
    def test_counts_and_means_use_the_right_goal_sets(self):
        info = run('default', [
            goal('succeeded', t=10, path=4, err=0.1, loc=0.02, clear=0.4),
            goal('succeeded', t=20, path=6, err=0.3, loc=0.04, rec=1, clear=0.2),
            goal('aborted', t=30, path=2, err=2.0, loc=0.30, rec=3),
            {'x': 1.0},                                      # not finished yet
        ], amcl={'samples': 100, 'rmse_m': 0.1, 'mean_m': 0.08, 'max_m': 0.3})
        m = nav_eval.nav_metrics(info)
        self.assertEqual((m['goals'], m['finished'], m['succeeded']), (4, 3, 2))
        self.assertAlmostEqual(m['success_rate'], 0.667)
        self.assertEqual(m['mean_time_s'], 15.0)            # succeeded only
        self.assertEqual(m['mean_path_m'], 5.0)
        self.assertEqual(m['mean_goal_error_m'], 0.2)
        self.assertEqual(m['mean_loc_error_m'], 0.12)       # all finished goals
        self.assertEqual(m['total_time_s'], 60.0)
        self.assertEqual(m['recoveries'], 4)
        self.assertEqual(m['min_clearance_m'], 0.2)
        self.assertEqual(m['amcl_rmse_m'], 0.1)
        self.assertTrue(m['has_gt'])

    def test_empty_run_has_no_rates(self):
        m = nav_eval.nav_metrics({'goals': []})
        self.assertIsNone(m['success_rate'])
        self.assertIsNone(m['mean_time_s'])
        self.assertIsNone(m['min_clearance_m'])
        self.assertFalse(m['has_gt'])

    def test_by_variant_pools_goals_and_weights_amcl_by_samples(self):
        a = run('a', [goal('succeeded', t=10), goal('aborted')], {'samples': 100, 'rmse_m': 0.1, 'mean_m': 0.1})
        b = run('a', [goal('succeeded', t=20)], {'samples': 300, 'rmse_m': 0.2, 'mean_m': 0.2})
        c = run(None, [goal('succeeded')])
        rows = nav_eval.by_variant([(i, nav_eval.nav_metrics(i)) for i in (a, b, c)])
        self.assertEqual([r['variant'] for r in rows], ['a', '—'])
        ra = rows[0]
        self.assertEqual((ra['runs'], ra['goals'], ra['succeeded']), (2, 3, 2))
        self.assertAlmostEqual(ra['success_rate'], 0.667)
        self.assertEqual(ra['mean_time_s'], 15.0)
        self.assertAlmostEqual(ra['amcl_rmse_m'], round(math.sqrt((100 * 0.01 + 300 * 0.04) / 400), 3))
        self.assertAlmostEqual(ra['amcl_mean_m'], 0.175)
        self.assertIsNone(rows[1]['amcl_rmse_m'])


class AmclSeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-naveval-test-')
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_none(self):
        self.assertIsNone(nav_eval.amcl_series(self.dir / 'nope.csv'))

    def test_goal_starts_bad_rows_and_downsampling(self):
        path = self.dir / 'amcl.csv'
        n = nav_eval.MAX_SERIES * 3
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['t', 'goal', 'error_xy'])
            w.writerow(['bad', '0', '1.0'])
            for i in range(n):
                w.writerow([i * 0.1, i * 2 // n, '' if i == 5 else 0.01 * (i % 7)])
        s = nav_eval.amcl_series(path)
        self.assertLessEqual(len(s['t']), nav_eval.MAX_SERIES + 1)
        self.assertEqual(len(s['t']), len(s['error']))
        self.assertEqual(s['t'][-1], round((n - 1) * 0.1, 2))   # last sample kept
        self.assertEqual(s['goals'], [[0, 0.0], [1, round(n // 2 * 0.1, 2)]])

    def test_write_csvs_headers_and_rows(self):
        info = run('a', [goal('succeeded'), goal('aborted')])
        files = nav_eval.write_csvs(self.dir, [('nav_1', info, nav_eval.nav_metrics(info))])
        self.assertEqual(files, ['nav_summary.csv', 'nav_goals.csv', 'nav_by_variant.csv'])
        with open(self.dir / 'nav_goals.csv', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], nav_eval.GOAL_FIELDS)
        self.assertEqual(len(rows), 3)
        with open(self.dir / 'nav_summary.csv', encoding='utf-8-sig') as f:
            summary = list(csv.DictReader(f))
        self.assertEqual(summary[0]['run_id'], 'nav_1')
        self.assertEqual(summary[0]['min_clearance_m'], '')      # None -> empty cell


if __name__ == '__main__':
    unittest.main()
