"""Read only saved application configuration snapshots from the data volume."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from application_files import redact_config
from application_config_mapper import mapper_path, mapper_content

LIMIT = 256 * 1024
GROUPS = ('dev', 'test', 'uat', 'prod', 'other')


def _entries(folder, manifest):
    entries = []
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for item in manifest.get('files', []):
        if not isinstance(item, dict): continue
        rel = item.get('file')
        if not isinstance(rel, str) or not rel or '\\' in rel or '\0' in rel: continue
        parts = rel.split('/')
        if len(parts) < 3 or parts[0] not in GROUPS or any(p in ('', '.', '..') for p in parts): continue
        target = folder.joinpath(*parts)
        try:
            if target.resolve().is_relative_to(folder.resolve()) and target.is_file():
                if item.get('source') == 'repository':
                    if mapper_path('/'.join(parts[2:])): continue
                    if rel.lower().endswith('.xml'):
                        with target.open('rb') as source:
                            if mapper_content(source.read(65536)): continue
                token = hashlib.sha256((revision + '\0' + rel).encode()).hexdigest()
                entries.append((token, item, target))
        except OSError:
            continue
    return entries


def _snapshots(data):
    root = data / 'application-config'
    found = {}
    if not root.is_dir(): return found
    for folder in root.iterdir():
        if folder.name.startswith('.') or not folder.is_dir() or folder.is_symlink(): continue
        try:
            manifest_file = folder / 'other' / 'manifest.json'
            if not manifest_file.resolve().is_relative_to(folder.resolve()): continue
            manifest = json.loads(manifest_file.read_text())
            app_id = str(manifest.get('appId', ''))
            if not re.fullmatch(r'[0-9]+', app_id) or not folder.name.endswith('-' + app_id): continue
            entries = _entries(folder, manifest)
            if entries: found.setdefault(app_id, []).append((folder, manifest, entries, manifest_file.stat().st_mtime))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    # Never choose arbitrarily if old directories share an application ID.
    return {key: value[0] for key, value in found.items() if len(value) == 1}


def list_ids(data):
    return {'appIds': sorted(_snapshots(data))}


def detail(data, app_id, file_id=None):
    if not re.fullmatch(r'[0-9]+', app_id or ''): raise ValueError('应用 ID 无效')
    snapshot = _snapshots(data).get(app_id)
    if not snapshot: raise ValueError('应用配置不存在或无法唯一定位')
    folder, manifest, entries, modified = snapshot
    if file_id is not None:
        matches = [(item, path) for token, item, path in entries if token == file_id]
        if len(matches) != 1: raise ValueError('配置文件不存在或无法唯一定位')
        path = matches[0][1]
        with path.open('rb') as source: raw = source.read(LIMIT + 1)
        return {'content': redact_config(raw[:LIMIT].decode('utf-8', errors='replace'), hide_password=False), 'truncated': len(raw) > LIMIT}
    files = []
    for token, item, path in entries:
        files.append({'id': token, 'group': PurePosixPath(item['file']).parts[0],
                      'source': item.get('source', ''), 'path': item.get('path') or item.get('dataId') or item['file'],
                      'file': item['file'], 'size': path.stat().st_size,
                      'namespace': item.get('namespace', ''), 'nacosGroup': item.get('group', ''), 'profile': item.get('profile', '')})
    return {'appId': app_id, 'name': manifest.get('name', ''), 'status': manifest.get('status', ''),
            'snapshotVersion': manifest.get('snapshotVersion', ''), 'branch': manifest.get('branch', ''),
            'module': manifest.get('module', ''), 'modifiedAt': modified,
            'warnings': manifest.get('warnings', []), 'notes': manifest.get('notes', []), 'files': files}
