"""Read snapshot evidence inside envscope-local; emit one JSON document on stdout."""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, '/app/runtime')
from application_files import redact_config


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def scan_nginx(data, folder, snapshot, endpoints):
    """Inspect downloaded configuration, preserving raw references if routes cannot be resolved."""
    from server_inventory import parse_nginx
    assets = {str(a.get('id')): a for a in snapshot.get('assets', [])}
    sources = []
    errors, warnings, rows = [], [], []
    archive_dir = folder/'nginx-configs'
    if archive_dir.is_dir():
        for path in sorted(archive_dir.glob('*.json')):
            try:
                archive = read(path)
                files = [(f.get('path', ''), f.get('content', '')) for f in archive.get('files', []) if isinstance(f.get('content'), str) and f.get('path')]
                asset = assets.get(str(archive.get('assetId')), {})
                sources.append((asset.get('ip',''), archive.get('collectedAt',''), '快照归档/'+path.name, files, ''))
            except (OSError, ValueError, TypeError) as exc:
                errors.append('读取归档失败 '+path.name+'：'+type(exc).__name__)
    download = data/'download_config'/'Nginx'
    if download.is_dir():
        for group in sorted(download.iterdir()):
            if not group.is_dir() or group.is_symlink(): continue
            try:
                manifest = read(group/'manifest.json')
                files = []
                root = group/'files'
                for current, dirs, names in os.walk(root, followlinks=False, onerror=lambda exc: errors.append('配置目录读取失败 '+group.name+': '+type(exc).__name__)):
                    dirs[:] = [d for d in dirs if not (Path(current)/d).is_symlink()]
                    for name in sorted(names):
                        path = Path(current)/name
                        if path.is_symlink() or not path.is_file(): continue
                        try: files.append(('/'+path.relative_to(root).as_posix(),path.read_text(encoding='utf-8',errors='replace')))
                        except OSError as exc: errors.append('配置读取失败 '+group.name+': '+type(exc).__name__)
                sources.append((manifest.get('ip',''),manifest.get('finishedAt',''),'下载配置/'+group.name,files,manifest.get('configPath','')))
            except (OSError, ValueError, TypeError) as exc:
                errors.append('配置清单读取失败 '+group.name+'：'+type(exc).__name__)
    if not archive_dir.is_dir() and not download.is_dir(): errors.append('没有已下载的 Nginx 配置目录')
    for server, observed, source, files, main_path in sources:
        if not files: continue
        # A literal endpoint cannot be expanded to a route if it does not
        # occur in any file in this configuration set. Hostname/variable
        # targets remain explicitly outside this scan's matching scope.
        if not any(re.search(r'(?<![\d.])'+re.escape(ip)+r'\s*:\s*'+re.escape(port)+r'(?!\d)', content)
                   for _, content in files for ip, port in endpoints):
            continue
        files.sort(key=lambda f: (0 if f[0]==main_path and main_path else 1 if f[0].endswith('/nginx.conf') else 2, f[0]))
        contents = dict(files)
        dump = ''.join('# configuration file '+name+':\n'+content+'\n' for name,content in files)
        try: routes, parse_warnings = parse_nginx(dump)
        except (ValueError, IndexError, TypeError) as exc:
            routes = []; errors.append(source+' 解析失败：'+type(exc).__name__)
        else:
            if parse_warnings: warnings.append(source+'：'+str(len(parse_warnings))+' 条解析提示')
        matched_files = set()
        for route in routes:
            matched = [(str(b.get('host')),str(b.get('port'))) for b in route.get('backends',[]) if (str(b.get('host')),str(b.get('port'))) in endpoints]
            if not matched: continue
            path = route.get('file',''); matched_files.add(path)
            uri = route.get('uri',''); text = contents.get(path,''); snippet = ''
            if uri:
                hits = list(re.finditer(r'(?m)^\s*location\s+(?:[=~^*]+\s+)?'+re.escape(uri)+r'\s*\{', text))
                if len(hits)==1:
                    end = text.find('}',hits[0].end()); body = text[hits[0].end():end] if end>=0 else ''
                    if end>=0 and '{' not in body and not re.search(r'["\'].*[{}].*["\']',body): snippet=redact_config(text[hits[0].start():end+1])
            rows.append({'serverIp':server,'file':path,'uri':uri,'domains':route.get('domains',[]),'target':route.get('target',''),'backends':route.get('backends',[]),'matchedEndpoints':matched,'snippet':snippet,'snippetStatus':'INFERRED' if snippet else 'UNKNOWN','checkedAt':observed,'source':source,'evidence':'解析路由'})
        # A literal reference may be in an upstream file rather than the route file.
        for path, content in files:
            if path in matched_files: continue
            matched = [(ip,port) for ip,port in endpoints if re.search(r'(?<![\d.])'+re.escape(ip)+r'\s*:\s*'+re.escape(port)+r'(?!\d)',content)]
            if matched:
                rows.append({'serverIp':server,'file':path,'uri':'','domains':[],'target':'','backends':[],'matchedEndpoints':matched,'snippet':'','snippetStatus':'UNKNOWN','checkedAt':observed,'source':source,'evidence':'原文地址引用；路由关系未确认'})
    return rows, errors, warnings, sum(len(x[3]) for x in sources)


def scan(data, app_id, environment):
    version = read(data / 'current.json')['id']
    folder = data / 'versions' / version
    apps = [a for a in read(folder / 'devops.json').get('apps', []) if str(a.get('id')) == app_id]
    if len(apps) != 1:
        raise ValueError('应用 ID 无法唯一定位')
    app = apps[0]
    deployments = app.get('envs', {}).get(environment, [])
    endpoints = set()
    warnings = []
    for dep in deployments:
        ip, port = str(dep.get('ip') or ''), str(dep.get('port') or '')
        if re.fullmatch(r'(?:\d{1,3}\.){3}\d{1,3}', ip) and port.isdigit() and 0 < int(port) < 65536:
            endpoints.add((ip, port))
        else:
            warnings.append('部署端点不完整：IP=%s port=%s' % (ip, port))
    if not endpoints:
        raise ValueError('所选环境没有可用的 IP 和端口')
    snapshot = read(folder / 'jumpserver-snapshot.json')
    statuses = {}
    for asset in snapshot.get('assets', []):
        ins = asset.get('inspection') or {}
        status = ins.get('nginxStatus') or 'unknown'
        statuses[status] = statuses.get(status, 0) + 1
    nginx, nginx_errors, nginx_warnings, nginx_files = scan_nginx(data, folder, snapshot, endpoints)
    references = []
    root = data / 'application-config'
    folders = sorted(root.iterdir()) if root.is_dir() else []
    config_errors = []
    if not root.is_dir():
        config_errors.append('配置目录不存在或不可访问')
    patterns = [(ip, port, re.compile(r'(?<![0-9.])' + re.escape(ip) + r'\s*:\s*' + re.escape(port) + r'(?!\d)')) for ip, port in sorted(endpoints)]
    scanned = 0
    for app_dir in folders:
        if not app_dir.is_dir() or app_dir.is_symlink() or app_dir.name.startswith('.'):
            continue
        manifest_path = app_dir / 'other' / 'manifest.json'
        try:
            manifest = read(manifest_path) if manifest_path.is_file() else {}
        except (OSError, ValueError, TypeError):
            manifest = {}
        match = re.fullmatch(r'(.+)-(\d+)', app_dir.name)
        app_name = manifest.get('name') or (match.group(1) if match else app_dir.name)
        app_id_found = str(manifest.get('appId') or (match.group(2) if match else ''))
        entries = {item.get('file'): item for item in manifest.get('files', []) if isinstance(item, dict)}
        discovered = []
        def onerror(exc):
            config_errors.append('目录遍历失败 %s：%s' % (app_dir.name, type(exc).__name__))
        for current, directories, names in os.walk(app_dir, onerror=onerror, followlinks=False):
            directories[:] = sorted(d for d in directories if not (Path(current)/d).is_symlink())
            discovered.extend(Path(current)/name for name in sorted(names))
        for path in discovered:
            if path == manifest_path or path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(app_dir).as_posix()
            parts = Path(rel).parts
            item = entries.get(rel, {})
            try:
                if not path.resolve().is_relative_to(app_dir.resolve()):
                    config_errors.append('文件超出应用目录：' + app_dir.name + '/' + rel)
                    continue
                with path.open(encoding='utf-8', errors='replace') as source:
                    scanned += 1
                    for number, line in enumerate(source, 1):
                        matches = [ip + ':' + port for ip, port, pattern in patterns if pattern.search(line)]
                        if matches:
                            references.append({'appId': app_id_found, 'appName': app_name,
                                               'file': item.get('path') or item.get('dataId') or rel,
                                               'line': number, 'content': redact_config(line.rstrip()),
                                               'matchedEndpoints': matches, 'group': parts[0]})
            except (OSError, ValueError, TypeError) as exc:
                config_errors.append('读取失败 %s/%s：%s' % (app_dir.name, rel, type(exc).__name__))
    return {'version': version, 'appId': app_id, 'appName': app.get('name'), 'environment': environment,
            'endpoints': sorted(endpoints), 'warnings': warnings, 'nginx': nginx,
            'nginxStatuses': statuses, 'nginxErrors': nginx_errors, 'nginxWarnings':nginx_warnings,'nginxFilesScanned':nginx_files, 'references': references, 'configErrors': config_errors, 'configFilesScanned': scanned}


if __name__ == '__main__':
    try:
        print(json.dumps(scan(Path(os.environ.get('ENVSCOPE_DATA', '/data')), sys.argv[1], sys.argv[2]), ensure_ascii=False))
    except Exception as exc:
        print('快照扫描失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
