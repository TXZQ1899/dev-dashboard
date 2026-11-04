"""Shared Nginx configuration discovery for inventory and file snapshots."""
import base64
import hashlib
import os
import posixpath
import re
from pathlib import Path

from application_config import ignore as config_ignore


def is_master(process):
    return bool(re.match(r'^nginx:\s+master process(?:\s|$)', process.get('command') or ''))


def collect(terminal, rows):
    """Collect every reachable nginx config file.

    Strategy: run nginx -T per running instance, then always sweep
    <prefix>/conf.d/*.conf and base64-read every conf.d file that -T did not
    yield (unloaded orphans or read failures). When no -T succeeded, the main
    config and its include targets are read directly and routes are parsed from
    the raw files on a best-effort basis.

    Returns (files, routes, warnings, notes, via_nginx_t); `warnings` are
    integrity issues that lower the collection status, `notes` are informational.
    """
    import server_inventory as inventory
    prefix, main_conf = inventory.nginx_process_config(rows)
    # Containerized nginx lives in its own mount namespace: host-side `nginx -T`
    # cannot see its config, so read the container files through /proc/<pid>/root.
    fs_root = inventory.nginx_mount_root(terminal, rows)
    commands = [] if fs_root else inventory.nginx_commands(rows)
    all_files, all_routes, warnings, notes = [], [], [], []
    collected = set()
    via_nginx_t = False
    for command in commands:
        rc, dump = terminal.run(command, timeout=60)
        if rc != 0 or not dump:
            warnings.append('Nginx -T 读取失败：检查配置权限、证书权限或 nginx 版本')
            continue
        via_nginx_t = True
        paths = list(dict.fromkeys(re.findall(r'(?m)^# configuration file (.+):\s*$', dump)))
        files, file_errors = inventory.collect_config_files(terminal, paths, command.split()[0])
        all_files.extend(files); warnings.extend(file_errors)
        collected.update(f['path'] for f in files)
        try:
            routes, parse_warnings = inventory.parse_nginx(dump)
            for route in routes: route['instance'] = command.split()[0]
            all_routes.extend(routes); warnings.extend(parse_warnings)
        except ValueError:
            warnings.append('Nginx 配置解析不完整')
    # conf.d 全量补采：-T 未列出（未加载）或读取失败的 .conf 一律直接读取
    missing = [p for p in inventory.list_conf_d(terminal, prefix, fs_root) if p not in collected]
    if missing:
        files, errors = inventory.collect_config_files(terminal, missing, 'folidev', fs_root)
        all_files.extend(files); warnings.extend(errors)
        collected.update(f['path'] for f in files)
        if via_nginx_t and files:
            notes.append('conf.d 中存在未被加载的配置文件，已一并采集：'+', '.join(f['path'] for f in files))
    if fs_root:
        notes.append('Nginx 运行于容器（独立挂载命名空间），已通过 '+fs_root+' 读取容器内配置')
    if not via_nginx_t:
        warnings.extend(inventory.read_nginx_without_t(terminal, prefix, main_conf, collected, all_files, all_routes, fs_root))
    return all_files, all_routes, warnings, notes, via_nginx_t


def catalogue(terminal, process):
    import application_check as check
    from application_files import run_script

    def guard():
        body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'], 'process-args')
        if 'FILES_DONE' not in body.splitlines():
            raise ValueError('Nginx 进程身份检查不完整')

    guard()
    files, routes, warnings, notes, via_t = collect(terminal, [process])
    guard()
    result = {'files': [], 'roots': {'config': [], 'log': []}, 'warnings': list(warnings) + list(notes),
              'observedAt': check.now(), 'pid': process['pid'], 'startTicks': process['startTicks'],
              'logScopes': [], 'processType': 'NGINX', 'typeConfidence': 'EXACT',
              'typeEvidence': '实时进程为 Nginx master', 'discoveryVersion': 'nginx-v1',
              'nginxRoutes': [], '_nginxContents': {}}
    result['warnings'].append('配置包含已读取的主配置、include 和 conf.d 文件；未加载文件不代表正在生效' if via_t
                              else '未通过 nginx -T 确认生效配置，直接读取结果仅供参考')
    seen = set()
    ignored = False
    for item in files:
        path = item.get('path', '')
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or posixpath.normpath(path) != path:
            result['warnings'].append('已跳过无效的 Nginx 配置路径')
            continue
        if config_ignore.is_ignored(path):
            ignored = True
            result['warnings'].append('部分 Nginx 配置文件已按 ignorelist 排除')
            continue
        if path in seen:
            result['warnings'].append('重复的 Nginx 配置路径已去重：'+path)
            continue
        try:
            raw = base64.b64decode(item['base64'], validate=True)
        except (KeyError, ValueError):
            result['warnings'].append('Nginx 配置文件内容校验失败：'+path)
            continue
        seen.add(path)
        identity = hashlib.sha256(('config\0'+path).encode()).hexdigest()[:32]
        root = posixpath.dirname(path)
        result['files'].append({'id':identity, 'kind':'config', 'path':path, 'root':root,
                                'size':len(raw), 'modifiedAt':None,
                                'discoveryEvidence':'Nginx 配置采集'})
        result['_nginxContents'][identity] = raw
        if root not in result['roots']['config']: result['roots']['config'].append(root)
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    if ignored:
        result['warnings'].append('存在排除的配置文件，未展示可能依赖其内容的解析路由')
    else:
        result['nginxRoutes'] = [route for route in routes if route.get('file') in seen]
    return result


def snapshot(catalogue, folder):
    from application_files import MAX_CONFIG_SNAPSHOT_FILE, MAX_CONFIG_SNAPSHOT_TOTAL
    import application_check as check
    folder = Path(folder)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    contents = catalogue.pop('_nginxContents')
    saved = 0
    for entry in catalogue['files']:
        raw = contents.get(entry['id'])
        if raw is None:
            entry['snapshotError'] = 'Nginx 配置内容不可用'
        elif len(raw) > MAX_CONFIG_SNAPSHOT_FILE:
            entry['snapshotError'] = '文件超过 2 MiB，未采集快照'
        elif saved + len(raw) > MAX_CONFIG_SNAPSHOT_TOTAL:
            entry['snapshotError'] = '配置快照超过 128 MiB 总量限制，未采集该文件'
        else:
            fd = os.open(folder/entry['id'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out: out.write(raw)
            saved += len(raw)
            entry.update(snapshotName=entry['id'], snapshotObservedAt=check.now(), snapshotTruncated=len(raw)>262144)
