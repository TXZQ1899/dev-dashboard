#!/usr/bin/env python3
"""Read-only inventory of /var/www/webapps application trees via JumpServer.

Run inside environment-web, for example:
  python3 runtime/list_webapps_files.py --ip 10.179.1.224 \
    --apps alipaymini-admin,alipaymini-web --max-depth 3

The command only uses find/stat-style metadata; it never reads file contents.
"""
from __future__ import annotations
import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

_RUNTIME_DIR = Path(__file__).resolve().parent
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))

import check_traffic_cli as cli
from origin_checker import load_credentials, load_snapshots

DATA = Path(cli.DEFAULT_DATA)
SAFE_NAME = re.compile(r'^[A-Za-z0-9_.-]+$')
MARKER = 'WEBAPPS_TREE_DONE'


def stamp(raw: str):
    if not raw or raw in ('-', '0'):
        return None
    try:
        return datetime.fromtimestamp(float(raw), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def inspect_tree(terminal, app: str, max_depth: int, max_entries: int):
    if not SAFE_NAME.fullmatch(app) or app in ('.', '..'):
        raise ValueError(f'invalid app directory name: {app!r}')
    path = f'/var/www/webapps/{app}'
    # GNU stat reports birth time as Unix seconds, or 0 if unavailable.
    script = (
        'root=$1; depth=$2; limit=$3; '
        'if [ ! -d "$root" ]; then printf "WEBAPPS_TREE_DONE\\tmissing\\n"; exit 0; fi; '
        'find "$root" -maxdepth "$depth" -exec stat --printf="%F\\t%Y\\t%W\\t%s\\t%n\\n" -- {} + 2>/dev/null | '
        'head -n "$limit"; rc=${PIPESTATUS[0]}; '
        'printf "WEBAPPS_TREE_DONE\\t%s\\n" "$rc"'
    )
    command = 'bash -c ' + shlex.quote(script) + ' _ ' + ' '.join(map(shlex.quote, (path, str(max_depth), str(max_entries))))
    rc, body = terminal.run(command, timeout=90)
    entries = []
    outcome = None
    for line in body.splitlines():
        if line.startswith(MARKER + '\t'):
            outcome = line.split('\t', 1)[1].strip()
            continue
        fields = line.split('\t', 4)
        if len(fields) != 5 or fields[0] not in ('directory', 'regular file', 'regular empty file', 'symbolic link'):
            continue
        entries.append({
            'type': {'directory': 'directory', 'regular file': 'file', 'regular empty file': 'file', 'symbolic link': 'symlink'}[fields[0]],
            'modifiedAt': stamp(fields[1]),
            'createdAt': stamp(fields[2]),
            'sizeBytes': int(fields[3]) if fields[3].isdigit() else None,
            'path': fields[4],
        })
    if rc != 0 or outcome is None:
        raise RuntimeError(f'could not inspect {path}; terminal exit={rc}')
    return {'app': app, 'path': path, 'status': 'missing' if outcome == 'missing' else 'complete' if outcome == '0' else 'partial', 'entries': entries}


def main():
    parser = argparse.ArgumentParser(description='只读列出 JumpServer 资产 /var/www/webapps 应用目录和文件元数据')
    parser.add_argument('--ip', required=True, help='JumpServer 资产 IP')
    parser.add_argument('--apps', help='逗号分隔的一级应用目录名；默认读取快照中的全部一级目录')
    parser.add_argument('--max-depth', type=int, default=2, help='find 深度（1=仅应用目录；默认 2=含直接子项）')
    parser.add_argument('--max-entries', type=int, default=10000, help='每个应用目录最多返回条目数')
    args = parser.parse_args()
    if not 1 <= args.max_depth <= 8 or not 1 <= args.max_entries <= 10000:
        parser.error('--max-depth 必须在 1–8，--max-entries 必须在 1–10000')
    version_dir = cli._resolve_version(None)
    snapshots = load_snapshots(version_dir)
    asset = cli._find_asset(snapshots, args.ip)
    apps = [x.strip() for x in args.apps.split(',') if x.strip()] if args.apps else None
    if apps is None:
        inspection = asset.get('inspection') or {}
        apps = sorted({x.get('name', '') for x in ((inspection.get('webapps') or {}).get('apps') or []) if x.get('name')})
    if not apps:
        parser.error('当前快照没有应用目录清单，请用 --apps 指定')
    creds = load_credentials(DATA / 'secrets' / 'cookies.json', DATA / 'secrets' / 'ip-password-map.json')
    terminal = cli._open_terminal(asset, creds)
    try:
        results = [inspect_tree(terminal, app, args.max_depth, args.max_entries) for app in apps]
    finally:
        terminal.close()
    print(json.dumps({'sourceVersion': version_dir.name, 'ip': args.ip, 'readOnly': True,
                      'maxDepth': args.max_depth, 'maxEntriesPerApp': args.max_entries,
                      'results': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
