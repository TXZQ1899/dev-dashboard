"""Live application files through JumpServer. Catalogues contain metadata only."""
import base64
import hashlib
import glob
import os
import posixpath
import re
import shlex
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import application_check as check
import application_config_ignore as config_ignore
import application_log_policy as log_policy
import application_nacos as nacos

MAX_ARCHIVE = 536870912
CHUNK = 524288
MAX_CONFIG_SNAPSHOT_FILE = 2 * 1024 * 1024
MAX_CONFIG_SNAPSHOT_TOTAL = 128 * 1024 * 1024


def roots_for(process, application=None):
    args = shlex.split(process.get('command', ''))
    config = check.config_roots(process)
    logs = []
    names = []
    cwd = process.get('cwd', '')
    if cwd.startswith('/'):
        config.extend([cwd+'/WEB-INF', cwd+'/config', cwd+'/conf'])
        logs.append(cwd+'/logs')
        if posixpath.basename(cwd) == 'bin':
            parent = posixpath.dirname(cwd)
            config.extend([parent+'/config', parent+'/conf'])
            logs.append(parent+'/logs')
    for arg in args:
        if arg.startswith(('-Dappid=', '-Dspring.application.name=', '--spring.application.name=')):
            names.append(arg.split('=', 1)[1])
        if arg.startswith('-Dcatalina.base='):
            base = arg.split('=', 1)[1]
            config.extend([base+'/webapps/ROOT/WEB-INF', base+'/webapps/ROOT/config'])
            logs.extend([base+'/logs', base+'/webapps/ROOT/logs'])
            folder = posixpath.basename(base).removeprefix('tomcat_').removeprefix('tomcat-')
            if folder not in ('tomcat', 'apache-tomcat'):
                names.append(folder)
    if (application or {}).get('appName'):
        names.append(application['appName'])
    for name in names:
        if re.fullmatch(r'[A-Za-z0-9_.-]+', name) and name not in ('.', '..') and not name.isdigit():
            directory = '/var/www/webapps/'+name
            config.extend([directory+'/WEB-INF', directory+'/config'])
            logs.append(directory+'/logs')
    if '-jar' in args and args.index('-jar')+1 < len(args):
        jar = args[args.index('-jar')+1]
        directory = posixpath.dirname(jar) if jar.startswith('/') else process.get('cwd', '')
        logs.append(directory+'/logs')
    def safe(values):
        return list(dict.fromkeys(posixpath.normpath(p) for p in values
            if p.startswith('/') and len([x for x in p.split('/') if x]) >= 3))[:16]
    # Expand Tomcat ROOT separately so a deployed symlink is resolved at the root.
    config = [p for p in config if not p.endswith('/webapps')]
    return {'config': safe(config), 'log': safe(logs)}


def run_script(terminal, script, *args, timeout=45):
    command = 'bash -c '+shlex.quote(log_policy.probe_script(script))+' _ '+ ' '.join(shlex.quote(str(x)) for x in args)
    rc, body = terminal.run(command, timeout=timeout)
    if rc:
        reasons = {3:'应用进程已重启或退出，请重新检测', 4:'文件不存在、不可读或目录已变化',
                   5:'远端文件读取或打包失败', 7:'待打包日志超过 10000 个文件或 512 MiB，请缩小日志范围后重试',
                   8:'日志目录没有符合打包条件的日志文件'}
        raise ValueError(reasons.get(rc, '远端文件操作失败（退出码 '+str(rc)+'）'))
    return body


def decode_path(value):
    path = base64.b64decode(value, validate=True).decode('utf-8')
    if not path.startswith('/') or posixpath.normpath(path) != path:
        raise ValueError('远端返回无效文件路径')
    return path


def declared_log_scopes(content, process):
    """Resolve literal Logback paths; shared directories stay restricted by filename."""
    try:
        xml = ET.fromstring(content)
    except ET.ParseError:
        return []
    values = {}
    for element in xml.iter():
        if element.tag.rsplit('}', 1)[-1] == 'property' and element.get('name') and element.get('value'):
            values.setdefault(element.get('name'), set()).add(element.get('value'))
    props = {key: next(iter(value)) for key, value in values.items() if len(value) == 1}
    for arg in shlex.split(process.get('command', '')):
        if arg.startswith('-D') and '=' in arg:
            key, value = arg[2:].split('=', 1)
            props[key] = value
    scopes = []
    for element in xml.iter():
        if element.tag.rsplit('}', 1)[-1] not in ('file', 'fileNamePattern') or not element.text:
            continue
        path = element.text.strip()
        for _ in range(5):
            updated = re.sub(r'\$\{([\w.-]+)\}', lambda m: props.get(m[1], m[0]), path)
            if updated == path: break
            path = updated
        if not path.startswith('/') or '${' in path or '\0' in path:
            continue
        root, basename = posixpath.split(posixpath.normpath(path))
        if len([part for part in root.split('/') if part]) < 3:
            continue
        parts = re.split(r'(%d(?:\{[^}]*\})?|%i)', basename)
        pattern = ''.join('*' if re.fullmatch(r'%d(?:\{[^}]*\})?|%i', part) else glob.escape(part) for part in parts)
        if not pattern or pattern.startswith('*'):
            continue
        scope = {'root':root, 'patterns':[pattern]}
        if scope not in scopes: scopes.append(scope)
    return scopes[:12]


def catalogue(terminal, process, application=None, kind=None):
    if kind not in (None, 'config', 'log'):
        raise ValueError('无效应用文件类型')
    requested_kind = kind
    roots = roots_for(process, application)
    result = {'files': [], 'roots': {'config': [], 'log': []}, 'warnings': [], 'observedAt': check.now(),
              'pid': process['pid'], 'startTicks': process['startTicks'], 'logScopes': []}
    seen = set()
    def add_file(kind, path, root, size, modified, scoped):
        group = log_policy.group_for(path, scoped) if kind == 'log' else None
        if path in seen or (kind == 'log' and group is None):
            return
        seen.add(path)
        entry = {'id':hashlib.sha256((kind+'\0'+path).encode()).hexdigest()[:32],
                 'kind':kind, 'path':path, 'root':root, 'size':int(size), 'modifiedAt':int(float(modified))}
        if group:
            entry['logGroup'] = group
        result['files'].append(entry)
    # Logs still inspect Logback declarations to retain shared-directory scopes.
    requests = [('config', 'list', roots['config'], None)]
    discovered_tomcat = False
    if requested_kind != 'config':
        requests.append(('log', 'list', roots['log'], None))
    while requests:
        kind, mode, paths, pattern = requests.pop(0)
        args = [kind, *([pattern] if pattern is not None else []), *paths]
        body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'], mode, *args)
        if 'FILES_DONE' not in body.splitlines():
            raise ValueError('文件列表不完整，请重试')
        for line in body.splitlines():
            fields = line.split('\t')
            if fields[0] == 'WARNING' and len(fields) == 2:
                result['warnings'].append(fields[1])
            elif fields[0] == 'ROOT' and len(fields) == 3:
                root = decode_path(fields[2])
                if root not in result['roots'][kind]:
                    result['roots'][kind].append(root)
                if kind == 'log':
                    scope = {'root':root, 'patterns':[pattern or '*']}
                    if scope not in result['logScopes']: result['logScopes'].append(scope)
            elif fields[0] == 'FILE' and len(fields) == 6 and fields[1] == kind:
                path, root = decode_path(fields[2]), decode_path(fields[5])
                if root not in result['roots'][kind] or not path.startswith(root+'/'):
                    raise ValueError('文件超出应用目录')
                add_file(kind, path, root, fields[3], fields[4], mode == 'list-match')
        for batch in re.finditer(r'(?m)^FILE_BATCH_BEGIN\t'+kind+r'\t([^\n]+)\n(.*?)^FILE_BATCH_END\s*$', body, re.S):
            root = decode_path(batch[1])
            if root not in result['roots'][kind]:
                raise ValueError('远端返回无效应用目录')
            raw = base64.b64decode(''.join(batch[2].split()), validate=True)
            for record in raw.split(b'\0'):
                if not record:
                    continue
                modified, size, path_bytes = record.split(b'\t', 2)
                path = path_bytes.decode('utf-8')
                if not path.startswith(root+'/') or posixpath.normpath(path) != path:
                    raise ValueError('文件超出应用目录')
                add_file(kind, path, root, size, modified, mode == 'list-match')
        if kind == 'config' and not discovered_tomcat:
            discovered_tomcat = True
            try:
                args = shlex.split(process.get('command', ''))
                base = next((a.split('=', 1)[1] for a in args if a.startswith('-Dcatalina.base=')), '')
                server_path = posixpath.normpath(posixpath.join(base, 'conf/server.xml')) if base.startswith('/') else ''
                server = next((entry for entry in result['files'] if entry['kind'] == 'config' and entry['path'] == server_path), None)
                if server:
                    content = read_file(terminal, process, server)['content']
                    extra = [root for root in check.tomcat_docbase_roots(content, process) if root not in roots['config']]
                    if extra:
                        requests.insert(0, ('config', 'list', extra, None))
            except (ValueError, OSError):
                result['warnings'].append('Tomcat server.xml 读取失败，无法发现其声明的应用目录')
        if kind == 'config' and requested_kind != 'config' and not any(request[0] == 'config' for request in requests):
            log_configs = [entry for entry in result['files'] if entry['kind'] == 'config' and
                posixpath.basename(entry['path']).startswith('logback') and entry['path'].endswith('.xml')]
            scopes = []
            for entry in log_configs[:3]:
                try:
                    # Text remains transient; catalogue/report persist only path metadata.
                    text = read_file(terminal, process, entry)['content']
                    scopes.extend(declared_log_scopes(text, process))
                except Exception:
                    result['warnings'].append('日志配置读取失败，保留进程推导的日志目录')
            for scope in scopes:
                for name in scope['patterns']:
                    request = ('log', 'list-match', [scope['root']], name)
                    if request not in requests: requests.append(request)
            if scopes:
                result['warnings'].append('日志配置声明的共享目录按文件名限定范围，未确认生效 profile')
    result['files'].sort(key=lambda f: (f['kind'], -f['modifiedAt'], f['path']))
    selected = []
    for label, kind, group in (('配置', 'config', None), ('当前日志', 'log', 'current'), ('历史日志', 'log', 'history')):
        files = [entry for entry in result['files'] if entry['kind'] == kind and entry.get('logGroup') == group]
        if len(files) > 1000:
            result['warnings'].append(label+'文件列表超过 1000 项，已显示最近更新的文件')
        selected.extend(files[:1000])
    result['files'] = [entry for entry in selected if requested_kind is None or entry['kind'] == requested_kind]
    if requested_kind == 'config':
        result['roots']['log'] = []
    if requested_kind == 'log':
        result['roots']['config'] = []
    if requested_kind != 'config' and not result['roots']['log']:
        result['warnings'].append('未找到该进程对应的日志目录；仅检查进程推导的目录，不扫描整台服务器')
    if process.get('kind') == 'Tomcat':
        result['warnings'].append('Tomcat JVM 可能承载多个应用，目录根据进程路径与已识别应用推导')
    return result


def redact_config(content, hide_password=True):
    """Mask common properties/YAML/XML secrets and URL authentication."""
    secret = (r'(?:password|passwd|pwd|secret|token|credential|access[-_.]?key|private[-_.]?key|api[-_.]?key)'
              if hide_password else r'(?:secret|token|credential|access[-_.]?key|private[-_.]?key|api[-_.]?key)')
    content = re.sub(r'(?im)^([^\n:=]*'+secret+r'[^\n:=]*\s*[:=])[^\n]*', r'\1 ******', content)
    content = re.sub(r'(?is)(<(?:property|entry)\b[^>]*(?:name|key)=[\"\'][^\"\']*'+secret+r'[^\"\']*[\"\'][^>]*\bvalue=)[\"\'][^\"\']*[\"\']', r'\1"******"', content)
    content = re.sub(r'(?is)(<([\w:.-]*'+secret+r'[\w:.-]*)\b[^>]*>).*?(</\2\s*>)', r'\1******\3', content)
    content = re.sub(r'(?is)(<(?:property|entry)\b[^>]*(?:name|key)=[\"\'][^\"\']*'+secret+r'[^\"\']*[\"\'][^>]*>).*?(</(?:property|entry)>)', r'\1******\2', content)
    def xml_secret(match):
        text = match[0]
        if re.search(r'(?:name|key)=["\'][^"\']*'+secret, text, re.I):
            text = re.sub(r'(\bvalue=)["\'][^"\']*["\']', r'\1"******"', text, flags=re.I)
        return text
    content = re.sub(r'<(?:[\w.-]+:)?(?:property|entry)\b[^>]*>', xml_secret, content, flags=re.I)
    content = re.sub(r'(?i)(\b[\w:.-]*'+secret+r'[\w:.-]*\s*=\s*)(["\'])(.*?)\2', r'\1"******"', content)
    lines = content.splitlines(keepends=True)
    hidden_indent = None
    for i, line in enumerate(lines):
        indent = len(line)-len(line.lstrip())
        if hidden_indent is not None and line.strip() and indent <= hidden_indent:
            hidden_indent = None
        if hidden_indent is not None and indent > hidden_indent:
            lines[i] = ' '*indent+'******\n'
        if re.match(r'\s*[\w.-]*'+secret+r'[\w.-]*\s*:', line, re.I):
            hidden_indent = indent
    content = ''.join(lines)
    content = re.sub(r'(://)[^\s/<>\"\']+@' if hide_password else r'(://)[^\s/:<>\"\']+@', r'\1******@', content)
    content = re.sub(r'(?i)([?&]'+secret+r'=)[^&\s<>\"\']+', r'\1******', content)
    content = re.sub(r'(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[私钥已隐藏]', content)
    return content


def read_file(terminal, process, entry, raw_config=False):
    if entry['kind'] == 'config' and config_ignore.is_ignored(entry['path']):
        raise ValueError('该配置文件已在 ignorelist 中排除，请刷新应用文件列表')
    if entry['kind'] == 'log' and log_policy.group_for(entry['path']) != 'current':
        raise ValueError('仅支持预览 .log、.out、.txt 当前日志；历史日志请打包下载')
    body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'],
                      entry['kind'], entry['path'], entry['root'])
    match = re.search(r'(?m)^CONTENT_BEGIN\n(.*?)^CONTENT_END\s*$', body, re.S)
    if not match or 'FILES_DONE' not in body.splitlines():
        raise ValueError('文件内容传输不完整')
    raw = base64.b64decode(''.join(match[1].split()), validate=True)
    content = raw.decode('utf-8', errors='replace')
    return {'id': entry['id'], 'content': redact_config(content) if entry['kind'] == 'config' and not raw_config else content,
            'lines': 100 if entry['kind'] == 'log' else None, 'observedAt': check.now(),
            'truncated': len(raw) >= 262144, 'redacted': entry['kind'] == 'config'}


def snapshot_configs(terminal, process, catalogue, folder):
    """Read listed files in bounded batches, preserving per-file failures."""
    folder = Path(folder)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    entries = [e for e in catalogue['files'] if e.get('kind') == 'config' and e.get('source') != 'nacos']
    batches, batch, batch_size = [], [], 0
    for entry in entries:
        size = max(0, int(entry.get('size', 0)))
        if size > MAX_CONFIG_SNAPSHOT_FILE:
            entry['snapshotError'] = '文件超过 2 MiB，未采集快照'
            continue
        if batch and (len(batch) >= 6 or batch_size + size > 4 * 1024 * 1024):
            batches.append(batch)
            batch, batch_size = [], 0
        batch.append(entry)
        batch_size += size
    if batch: batches.append(batch)
    saved_size = 0
    for batch in batches:
        args = [value for entry in batch for value in (entry['id'], entry['path'], entry['root'])]
        body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'], 'snapshot', *args)
        if 'FILES_DONE' not in body.splitlines():
            raise ValueError('配置快照读取不完整，请重试')
        pending = {entry['id']: entry for entry in batch}
        for line in body.splitlines():
            fields = line.split('\t')
            if fields[0] not in ('SNAPSHOT', 'SNAPSHOT_ERROR'):
                continue
            if len(fields) != (3 if fields[0] == 'SNAPSHOT' else 2) or fields[1] not in pending:
                raise ValueError('远端返回无效配置快照')
            entry = pending.pop(fields[1])
            if fields[0] == 'SNAPSHOT_ERROR':
                entry['snapshotError'] = '采集时文件不可读或已变化，请刷新配置文件'
                continue
            raw = base64.b64decode(fields[2], validate=True)
            if len(raw) > MAX_CONFIG_SNAPSHOT_FILE + 1:
                raise ValueError('远端配置快照超过单文件限制')
            if len(raw) > MAX_CONFIG_SNAPSHOT_FILE:
                entry['snapshotError'] = '文件超过 2 MiB，未采集快照'
                continue
            if saved_size + len(raw) > MAX_CONFIG_SNAPSHOT_TOTAL:
                entry['snapshotError'] = '配置快照超过 128 MiB 总量限制，未采集该文件'
                continue
            saved_size += len(raw)
            entry['snapshotTruncated'] = len(raw) > 262144
            entry['snapshotName'] = entry['id']
            entry['snapshotObservedAt'] = check.now()
            fd = os.open(folder/entry['id'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
        if pending:
            raise ValueError('配置快照读取不完整，请重试')


def read_snapshot(entry, folder, raw_config=False):
    if config_ignore.is_ignored(entry['path']):
        raise ValueError('该配置文件已在 ignorelist 中排除，请刷新应用文件列表')
    if entry.get('snapshotError'):
        raise ValueError(entry['snapshotError'])
    name = entry.get('snapshotName', '')
    if not re.fullmatch(r'[0-9a-f]{32}', name) or name != entry.get('id'):
        raise ValueError('配置快照不可用，请刷新配置文件')
    path = Path(folder)/name
    if path.is_symlink() or not path.is_file():
        raise ValueError('配置快照不存在，请刷新配置文件')
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_SNAPSHOT_FILE:
        raise ValueError('配置快照超过大小限制')
    content = raw[:262144].decode('utf-8', errors='replace')
    return {'id':entry['id'], 'content':content if raw_config else redact_config(content, hide_password=False),
            **({} if raw_config else {'redactedContent':redact_config(content), 'passwordsVisible':True}),
            'observedAt':entry.get('snapshotObservedAt'), 'lines':None,
            'truncated':bool(entry.get('snapshotTruncated')), 'redacted':not raw_config}


def collect_nacos(terminal, process, catalogue, folder, snapshot_folder=None):
    candidates = [e for e in catalogue['files'] if e.get('kind') == 'config'
                  and re.fullmatch(r'(?:application|bootstrap)(?:-[\w.-]+)?\.ya?ml', posixpath.basename(e.get('path', '')), re.I)]
    if len(candidates) > 20:
        catalogue['warnings'].append('Nacos 声明只检查最近的 20 个应用 YAML 文件')
    seen = set()
    attempted = set()
    for entry in candidates[:20]:
        try:
            raw = (read_snapshot(entry, snapshot_folder, raw_config=True) if snapshot_folder is not None
                   else read_file(terminal, process, entry, raw_config=True))
            if raw['truncated']:
                catalogue['warnings'].append('应用 YAML 文件过大，未解析 Nacos 声明')
                continue
            configs, unresolved = nacos.declarations(raw['content'], process)
            catalogue['warnings'].extend(unresolved)
            if not configs:
                continue
            for config in configs:
                requested = tuple(config[k] for k in ('address', 'namespace', 'group', 'dataId'))
                if requested in attempted: continue
                if len(attempted) >= 20:
                    catalogue['warnings'].append('Nacos 配置超过 20 项，未继续下载')
                    break
                attempted.add(requested)
                try:
                    inferred_group = not config['group']
                    config = nacos.resolve_group(config)
                    identity = tuple(config[k] for k in ('address', 'namespace', 'group', 'dataId'))
                    if identity in seen: continue
                    seen.add(identity)
                    content = nacos.fetch(config)
                    path = nacos.save(folder, config, content)
                    label = 'Nacos / '+config['namespace']+' / '+config['group']+' / '+config['dataId']
                    catalogue['files'].append({'id':hashlib.sha256(('nacos\0'+path.name).encode()).hexdigest()[:32],
                        'kind':'config', 'source':'nacos', 'storageName':path.name, 'path':label,
                        'root':'Nacos', 'size':len(content), 'modifiedAt':int(time.time()), 'snapshotObservedAt':check.now()})
                    catalogue['warnings'].append('Nacos 配置来自 '+entry['path']+' 的声明；未确认该 profile 实际生效')
                    if inferred_group:
                        catalogue['warnings'].append('Nacos Group 在可查询范围内唯一匹配；未确认应用实际使用该 Group：'+config['dataId'])
                except ValueError as error:
                    catalogue['warnings'].append('Nacos 配置读取失败：'+entry['path']+' / '+config['dataId']+'；'+str(error))
                except OSError:
                    catalogue['warnings'].append('Nacos 配置读取或保存失败：'+entry['path']+' / '+config['dataId'])
        except ValueError as error:
            catalogue['warnings'].append('Nacos 配置读取失败：'+entry['path']+'；'+str(error))
        except OSError:
            catalogue['warnings'].append('Nacos 配置读取或保存失败：'+entry['path'])


def read_nacos(entry, folder):
    name = entry.get('storageName', '')
    if not re.fullmatch(r'[0-9a-f]{64}\.(?:yaml|txt)', name):
        raise ValueError('无效 Nacos 文件')
    path = Path(folder)/name
    if path.is_symlink() or not path.is_file():
        raise ValueError('Nacos 文件不存在，请刷新配置列表')
    raw = path.read_bytes()
    if len(raw) > nacos.MAX_CONTENT:
        raise ValueError('Nacos 文件超过大小限制')
    content = raw.decode('utf-8')
    return {'id':entry['id'], 'content':redact_config(content, hide_password=False),
            'redactedContent':redact_config(content), 'passwordsVisible':True, 'lines':None,
            'observedAt':entry.get('snapshotObservedAt') or check.now(), 'truncated':False, 'redacted':True}


def archive_logs(terminal, process, roots, destination, progress=lambda *_:None, cancelled=lambda:False, group='all'):
    if group not in ('all', 'history'):
        raise ValueError('无效日志打包范围')
    scopes = [root if isinstance(root, dict) else {'root':root, 'patterns':['*']} for root in roots]
    unique = []
    for scope in scopes:
        # Keep overlapping scopes: bounded child roots and explicit Logback
        # patterns can include files that a parent wildcard cannot list.
        if scope not in unique: unique.append(scope)
    roots = [value for scope in unique for pattern in scope['patterns'] for value in (scope['root'], pattern)]
    if not roots:
        raise ValueError('未找到可下载的应用日志目录')
    remote = None
    destination = Path(destination)
    try:
        cutoff = 0 if group == 'history' else int(time.time())-30*86400
        progress('正在打包历史日志' if group == 'history' else '正在打包最近 30 天的日志', 0, None)
        body = run_script(terminal, 'application_logs.sh', process['pid'], process['startTicks'], cutoff, group, *roots, timeout=145)
        line = next((line for line in body.splitlines() if line.startswith('ARCHIVE\t')), '')
        fields = line.split('\t')
        if len(fields) != 6 or not re.fullmatch(r'/tmp/envscope-logs\.[A-Za-z0-9]{10}/logs\.tar\.gz', fields[1]):
            raise ValueError('日志压缩包响应不完整')
        remote, size, digest, count, tar_status = fields[1], int(fields[2]), fields[3], int(fields[4]), int(fields[5])
        if not 0 < size <= MAX_ARCHIVE+1048576 or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('日志压缩包超过大小限制或校验信息无效')
        destination.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256()
        with destination.open('wb') as output:
            destination.chmod(0o600)
            for offset in range(0, size, CHUNK):
                if cancelled():
                    raise ValueError('日志下载已取消')
                rc, body = terminal.run('dd if='+shlex.quote(remote)+' bs='+str(CHUNK)+' skip='+str(offset//CHUNK)+' count=1 status=none | base64', timeout=60)
                if rc:
                    raise ValueError('日志分块传输失败')
                chunk = base64.b64decode(''.join(body.split()), validate=True)
                if len(chunk) != min(CHUNK, size-offset):
                    raise ValueError('日志分块大小不符，请重试')
                output.write(chunk); sha.update(chunk)
                progress('正在传输日志压缩包', offset+len(chunk), size)
        if sha.hexdigest() != digest:
            raise ValueError('日志压缩包校验失败，请重试')
        return {'size':size, 'fileCount':count, 'cutoff':cutoff, 'logGroup':group, 'sha256':digest,
                'warnings':['打包期间日志仍在写入，压缩包保留读取时的内容'] if tar_status == 1 else []}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if remote:
            try: terminal.run('rm -rf -- '+shlex.quote(posixpath.dirname(remote)), timeout=15)
            except Exception: pass
