"""Read-only Docker metadata and bounded mount browsing for a verified process."""
import base64
import json
import posixpath
import re
import shlex

import application_check as check


def run_probe(terminal, process, container_id, action, *args):
    script = (check.PROBES/'container_info.sh').read_text()
    arguments = [str(process['pid']), str(process['startTicks']), container_id, action, *args]
    if isinstance(terminal, check.ProbeTerminal):
        command = terminal.probe_command(script, arguments, 'timeout 30')
    else:
        encoded = base64.b64encode(script.encode()).decode()
        command = 'timeout 30 bash -c "$(printf %s '+shlex.quote(encoded)+' | base64 -d)" _ '+shlex.join(arguments)
    options = {'timeout':35}
    if isinstance(terminal, check.si.Terminal): options['wait_for_prompt'] = True
    rc, body = terminal.run(command, **options)
    if rc:
        raise ValueError({3:'进程或容器身份已变化，请重新采集', 4:'Docker 信息或挂载目录不可读'}
                         .get(rc, '容器信息读取失败（退出码 '+str(rc)+'）'))
    return body


def inspect(terminal, process, container_id):
    body = run_probe(terminal, process, container_id, 'info')
    match = re.search(r'(?m)^INFO\t([A-Za-z0-9+/=]+)$', body)
    if not match or 'FILES_DONE' not in body.splitlines():
        raise ValueError('容器信息读取不完整')
    try:
        fields = base64.b64decode(match[1], validate=True).decode().split('\t')
        identifier, name, image, image_id, init_pid, mounts = map(json.loads, fields)
        if identifier != container_id or not isinstance(init_pid, int) or not isinstance(mounts, list):
            raise ValueError('invalid container metadata')
        clean = []
        for mount in mounts:
            if not isinstance(mount, dict): raise ValueError('invalid mount')
            kind, source, destination = (mount.get('Type'), mount.get('Source'), mount.get('Destination'))
            if not all(isinstance(x, str) for x in (kind, source, destination)) or not destination.startswith('/'):
                raise ValueError('invalid mount paths')
            clean.append({'type':kind, 'name':mount.get('Name') or '', 'source':source,
                          'destination':destination, 'readWrite':bool(mount.get('RW')),
                          'driver':mount.get('Driver') or '', 'propagation':mount.get('Propagation') or ''})
        clean.sort(key=lambda m: (m['destination'], m['type'], m['source'], m['name']))
        return {'id':identifier, 'name':name.lstrip('/'), 'image':image, 'imageId':image_id,
                'initPid':init_pid, 'mounts':clean}
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError('Docker 返回的容器信息无效') from None


def browse(terminal, process, container_id, before, index, relative, offset=0):
    if type(index) is not int or index < 0 or index >= len(before['mounts']):
        raise ValueError('无效挂载目录')
    mount = before['mounts'][index]
    if mount['type'] not in ('bind', 'volume'):
        raise ValueError('此挂载不是可浏览的持久化目录')
    source = mount['source']
    if not source.startswith('/') or posixpath.normpath(source) != source:
        raise ValueError('挂载源路径不可用于浏览')
    if not isinstance(relative, str) or len(relative) > 4096 or relative.startswith('/') or '\0' in relative or (
            relative and any(p in ('', '.', '..') for p in relative.split('/'))):
        raise ValueError('无效目录路径')
    if type(offset) is not int or not 0 <= offset <= 1000000 or offset % 200:
        raise ValueError('无效目录页码')
    body = run_probe(terminal, process, container_id, 'browse', source, relative, str(offset))
    match = re.search(r'(?ms)^FILES_BEGIN\n(.*?)^FILES_END\s*$', body)
    if not match or 'FILES_DONE' not in body.splitlines(): raise ValueError('目录列表读取不完整')
    try:
        raw = base64.b64decode(''.join(match[1].split()), validate=True)
        entries = []
        for record in raw.split(b'\0'):
            if not record: continue
            kind, size, modified, name = record.split(b'\t', 3)
            name = name.decode('utf-8')
            if not name or name in ('.', '..') or '/' in name: raise ValueError('invalid name')
            entries.append({'name':name, 'type':{'d':'directory', 'f':'file', 'l':'symlink'}.get(kind.decode(), 'other'),
                            'size':int(size), 'modifiedAt':int(float(modified))})
    except (ValueError, UnicodeError):
        raise ValueError('目录列表包含无法识别的文件名或元数据') from None
    if inspect(terminal, process, container_id) != before:
        raise ValueError('容器或挂载已变化，请刷新容器信息')
    return {'entries':entries[:200], 'truncated':len(entries)>200, 'path':relative, 'offset':offset,
            'warnings':[line.split('\t', 1)[1] for line in body.splitlines() if line.startswith('WARNING\t')]}
