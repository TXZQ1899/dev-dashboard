"""Identify Kibana servers and their explicitly declared configuration."""
import posixpath
import re
import shlex

SERVER = re.compile(r'(?:^|\s)\S*/kibana/(?:\S*/)?(?:src/cli|bin/kibana)(?:\s|$)', re.IGNORECASE)


def is_server(process):
    return bool(SERVER.search(process.get('command') or ''))


def discover(argv, cwd):
    values = []
    for index, arg in enumerate(argv):
        if arg in ('-c', '--config') and index + 1 < len(argv):
            values.append(argv[index + 1])
        elif arg.startswith('--config='):
            values.append(arg.split('=', 1)[1])
    if len(values) != 1:
        return None, '实时启动参数无法唯一确认 Kibana 主配置文件'
    path = values[0]
    if not path or '\0' in path or '$' in path or '\n' in path or '\r' in path or path.startswith('-'):
        return None, 'Kibana 主配置路径无法解析'
    if not path.startswith('/'):
        if not cwd or not cwd.startswith('/'):
            return None, '相对配置路径无法解析：进程工作目录不可用'
        path = posixpath.join(cwd, path)
    path = posixpath.normpath(path)
    if path == '/' or path.startswith('//'):
        return None, 'Kibana 主配置路径无效'
    return path, None


def config_root(process):
    """Return the declared configuration directory for endpoint collection."""
    try:
        argv = shlex.split(process.get('command') or '')
    except ValueError:
        return None
    path, _warning = discover(argv, process.get('cwd', ''))
    return posixpath.dirname(path) if path else None


def kibana_catalogue(terminal, process):
    from application_files import run_script, decode_path
    import application_check as check
    from application_config import ignore as config_ignore
    import base64
    import hashlib

    result = {'files': [], 'roots': {'config': [], 'log': []}, 'warnings': [],
              'observedAt': check.now(), 'pid': process['pid'], 'startTicks': process['startTicks'],
              'logScopes': [], 'processType': 'KIBANA', 'typeConfidence': 'EXACT',
              'typeEvidence': '实时进程命令为 Kibana CLI', 'discoveryVersion': 'kibana-v1'}
    body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'], 'process-args')
    lines = body.splitlines()
    argv_lines = [line[5:] for line in lines if line.startswith('ARGV\t')]
    cwd_lines = [line[4:] for line in lines if line.startswith('CWD\t')]
    if 'FILES_DONE' not in lines or len(argv_lines) != 1 or len(cwd_lines) != 1:
        raise ValueError('实时进程参数读取不完整')
    try:
        raw = base64.b64decode(argv_lines[0], validate=True)
        if not raw.endswith(b'\0'):
            raise ValueError('启动参数不完整')
        argv = [part.decode('utf-8') for part in raw[:-1].split(b'\0')]
        cwd = base64.b64decode(cwd_lines[0], validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        raise ValueError('实时进程参数无法解析') from None
    if not SERVER.search(' '.join(argv)):
        result['typeConfidence'] = 'UNKNOWN'
        result['typeEvidence'] = '实时启动参数未能确认 Kibana CLI'
        result['warnings'].append('实时启动参数未能确认 Kibana CLI')
        return result
    path, warning = discover(argv, cwd)
    if warning:
        result['warnings'].append(warning)
        return result
    if config_ignore.is_ignored(path):
        result['warnings'].append('Kibana 主配置文件已在 ignorelist 中排除')
        return result
    body = run_script(terminal, 'application_files.sh', process['pid'], process['startTicks'], 'list-file', path)
    lines = body.splitlines()
    if 'FILES_DONE' not in lines:
        raise ValueError('Kibana 配置文件检查不完整')
    records = [line.split('\t') for line in lines if line.startswith('EXACT_FILE\t')]
    result['warnings'].extend(line.split('\t', 1)[1] for line in lines if line.startswith('WARNING\t'))
    if not records:
        return result
    if len(records) != 1 or len(records[0]) != 4 or decode_path(records[0][1]) != path:
        raise ValueError('远端返回无效 Kibana 配置文件')
    try:
        size, modified = int(records[0][2]), int(records[0][3])
        if size < 0: raise ValueError()
    except ValueError:
        raise ValueError('远端返回无效 Kibana 配置文件元数据') from None
    root = posixpath.dirname(path)
    result['roots']['config'].append(root)
    result['files'].append({'id': hashlib.sha256(('config\0'+path).encode()).hexdigest()[:32],
        'kind': 'config', 'path': path, 'root': root, 'size': size, 'modifiedAt': modified,
        'role': 'primary', 'discoveryEvidence': 'Kibana 启动参数指定'})
    result['warnings'].append('仅发现启动参数指定的主配置；关联配置尚未采集，不能确认配置已生效')
    return result
