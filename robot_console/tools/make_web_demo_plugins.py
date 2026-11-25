#!/usr/bin/env python3
"""Build the static plugin catalog from the released manifests and a public market index.

python3 -I tools/make_web_demo_plugins.py --index <public-index.json> --out web/demo/public/data/plugins.json

Requires PyYAML. Never scans installed plugins, loads plugin code, or exports settings or defaults.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml

MARKET = 'https://github.com/Leonhart999/slam-console-plugins'
CONSOLE = 'https://github.com/Leonhart999/slam-autonav-console'
MARKET_FIELDS = ('id', 'version', 'api', 'name', 'description', 'author', 'license', 'homepage', 'source',
                 'upstream', 'requirements', 'url', 'sha256', 'size', 'published')


def public_url(value: str) -> str:
    url = urlsplit(value)
    return value if url.scheme == 'https' and url.hostname and not (url.username or url.password) else ''


def make_catalog(shipped: Path, index: dict) -> dict:
    if index.get('format') != 1:
        raise ValueError('expected a v1 market index')
    bundled = []
    for path in sorted(shipped.glob('*/plugin.yaml')):
        m = yaml.safe_load(path.read_text(encoding='utf-8'))
        pid = m['id']
        if pid != path.parent.name:
            raise ValueError(f'manifest id does not match its folder: {pid}')
        bundled.append(dict(
            id=pid, dir=f'plugins/{pid}', path=f'plugins/{pid}', state='disabled', enabled=False,
            restart_needed=False, issue=None, has_backend=bool(m.get('backend')),
            has_frontend=bool(m.get('frontend')), frontend=None, settings=[], origin='bundled',
            manifest=dict(api=m['api'], name=m['name'], description=m['description'], version=str(m['version']),
                          author=m.get('author', ''), license=m.get('license', ''),
                          homepage=f'{CONSOLE}/tree/main/robot_console/plugins/{pid}'),
        ))
    entries = []
    for entry in index['plugins']:
        e = {k: entry[k] for k in MARKET_FIELDS}
        for key in ('source', 'upstream', 'homepage', 'url'):
            e[key] = public_url(e[key])
        entries.append(dict(e, status='available' if e['api'] == 1 else 'incompatible', installed=None))
    return dict(
        bundled=dict(api_version=1, dirs=[], paths=[], disabled_by_env=False, plugins=bundled),
        market=dict(index_urls=[], source=MARKET, fetched=None, name=index['name'], updated=index['updated'],
                    stale=False, error=None, skipped=[], install_dir=None, api_version=1, supported_apis=[1],
                    busy=False, plugins=entries),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--index', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    shipped = Path(__file__).resolve().parents[1] / 'plugins'
    data = make_catalog(shipped, json.loads(args.index.read_text(encoding='utf-8')))
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
