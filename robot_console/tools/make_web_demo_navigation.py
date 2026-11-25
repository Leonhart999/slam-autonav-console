#!/usr/bin/env python3
"""Export an office navigation run for the static demo, keeping only chart and table data.

Usage: python3 -I tools/make_web_demo_navigation.py --run <exported-run> --out <sample-directory>
"""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    src = Path(__file__).resolve().parents[1] / 'robot_console' / 'nav_eval.py'
    spec = importlib.util.spec_from_file_location('nav_eval', src)
    nav_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nav_eval)
    info = json.loads((args.run / 'run.json').read_text(encoding='utf-8'))
    if (info.get('type'), info.get('preset'), info.get('state')) != ('navigation', 'office', 'finished'):
        parser.error('expected a completed office simulation navigation run')
    metrics = nav_eval.nav_metrics(info)
    metrics.update(batch=None, map='office-demo')
    allowed = {'index', 'x', 'y', 'yaw', 'result', 'time_s', 'path_m', 'goal_error_m', 'goal_yaw_error_deg',
               'loc_error_m', 'recoveries', 'min_clearance_m', 'speed_scale'}
    public = dict(started=info['started'], ended=info['ended'], nav=metrics,
                  goals=[{k: v for k, v in goal.items() if k in allowed} for goal in info['goals']],
                  amcl=nav_eval.amcl_series(args.run / 'amcl.csv'))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'run.json').write_text(json.dumps(public, separators=(',', ':')) + '\n', encoding='utf-8')
    shutil.copyfile(args.run / 'path.png', args.out / 'path.png')


if __name__ == '__main__':
    main()
