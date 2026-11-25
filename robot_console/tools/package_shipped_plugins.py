#!/usr/bin/env python3
"""Package bundled plugins in plugins/<id>/ into reproducible market zip archives and index entries.

    python3 tools/package_shipped_plugins.py [id ...] [--out DIR] [--release-repo OWNER/REPO]
                                             [--source-repo OWNER/REPO] [--tag-prefix PREFIX]

Writes into --out (default: dist/shipped_plugins under the repo root):
  <id>-<version>.zip         reproducible plugin zip with a single top-level <id>/ folder
                             (skips node_modules, caches, dotfiles, web/ source tree is kept for transparency)
  <id>-<version>.entry.json  market entry compatible with tools/add_entry.py of
                             github.com/Leonhart999/slam-console-plugins

By default, release zips are expected at:
  https://github.com/Leonhart999/slam-console-plugins/releases/download/bundled-<id>-v<version>/<id>-<version>.zip
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / 'plugins'
SKIP_DIRS = {'node_modules', '__pycache__', '.pytest_cache', '.vite', 'dist'}
SKIP_SUFFIXES = ('.pyc', '.pyo', '.log')
STAMP = (2020, 1, 1, 0, 0, 0)
DEFAULT_RELEASE_REPO = 'Leonhart999/slam-console-plugins'
DEFAULT_SOURCE_REPO = 'Leonhart999/slam-autonav-console'


def files_of(folder: Path):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES) or name.startswith('.'):
                continue
            full = Path(root) / name
            if full.is_symlink():
                sys.exit(f'symbolic links are not allowed in a plugin: {full}')
            out.append(full)
    return out


def discover_ids(plugins_dir: Path):
    return sorted(
        p.name for p in plugins_dir.iterdir()
        if p.is_dir() and not p.name.startswith(('_', '.')) and (p / 'plugin.yaml').is_file()
    )


def package_one(pid: str, out_dir: Path, release_repo: str, source_repo: str, tag_prefix: str):
    folder = PLUGINS_DIR / pid
    manifest_path = folder / 'plugin.yaml'
    if not manifest_path.is_file():
        sys.exit(f'plugin not found: {folder}')
    m = yaml.safe_load(manifest_path.read_text(encoding='utf-8'))
    if m.get('id') != pid:
        sys.exit(f'plugin.yaml id {m.get("id")!r} differs from folder name {pid!r}')
    fe = m.get('frontend') or {}
    if fe and not (folder / fe.get('dir', 'ui') / fe.get('script', 'plugin.js')).is_file():
        sys.exit(f'{pid}: frontend is not built (run `cd web && npm run build:plugins` first)')
    version = str(m['version'])
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f'{pid}-{version}.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for full in files_of(folder):
            rel = full.relative_to(PLUGINS_DIR).as_posix()
            info = zipfile.ZipInfo(rel, STAMP)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, full.read_bytes())
    data = zpath.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    tag = f'{tag_prefix}{pid}-v{version}'
    url = f'https://github.com/{release_repo}/releases/download/{tag}/{zpath.name}'
    source = f'https://github.com/{source_repo}/tree/main/robot_console/plugins/{pid}'
    homepage = m.get('homepage') or source
    entry = {
        'id': pid,
        'version': version,
        'api': m['api'],
        'name': m['name'],
        'description': m['description'],
        'author': m.get('author', ''),
        'license': m.get('license', ''),
        'homepage': homepage,
        'source': source,
        'upstream': m.get('upstream', ''),
        'requirements': list((m.get('python') or {}).get('requirements') or []),
        'url': url,
        'sha256': sha,
        'size': len(data),
        'published': datetime.date.today().isoformat(),
    }
    epath = out_dir / f'{pid}-{version}.entry.json'
    epath.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{pid} {version}\n  zip:   {zpath} ({len(data)} bytes)\n  sha:   {sha}\n  tag:   {tag}\n  url:   {url}')
    return zpath, epath, tag


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ids', nargs='*', help='Plugin IDs to package (default: all bundled plugins)')
    ap.add_argument('--out', type=Path, default=REPO_ROOT / 'dist' / 'shipped_plugins')
    ap.add_argument('--release-repo', default=DEFAULT_RELEASE_REPO)
    ap.add_argument('--source-repo', default=DEFAULT_SOURCE_REPO)
    ap.add_argument('--tag-prefix', default='bundled-')
    args = ap.parse_args()
    ids = args.ids or discover_ids(PLUGINS_DIR)
    for pid in ids:
        package_one(pid, args.out, args.release_repo, args.source_repo, args.tag_prefix)


if __name__ == '__main__':
    main()
