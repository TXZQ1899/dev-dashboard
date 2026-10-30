"""Snapshot configuration files inside the live application JAR."""
import base64
import hashlib
import os
import re
import shlex
from pathlib import Path

import application_check as check
from application_config import ignore as config_ignore


def snapshot_jar_configs(terminal, process, catalogue, folder):
    """Capture classpath configs from the live process's JAR into the same snapshot."""
    from application_files import run_script, decode_path, MAX_CONFIG_SNAPSHOT_FILE
    try:
        args = shlex.split(process.get('command', ''))
    except ValueError:
        return
    if '-jar' not in args:
        return
    body = run_script(terminal, 'application_jar_configs.sh', process['pid'], process['startTicks'], timeout=120)
    if 'FILES_DONE' not in body.splitlines():
        raise ValueError('JAR 配置读取不完整，请重试')
    seen = set()
    for line in body.splitlines():
        fields = line.split('\t')
        if fields[0] == 'WARNING' and len(fields) == 2:
            catalogue['warnings'].append(fields[1])
        elif fields[0] == 'JAR_CONFIG' and len(fields) == 4:
            archive = decode_path(fields[1])
            member = base64.b64decode(fields[2], validate=True).decode('utf-8')
            if not re.fullmatch(r'(?:BOOT-INF/classes/|classes/)?[A-Za-z0-9_./-]+\.(?:yml|yaml|properties|xml|conf)', member) or any(p in ('', '.', '..') for p in member.split('/')):
                raise ValueError('远端返回无效 JAR 配置路径')
            path = archive+'!/'+member
            if path in seen or config_ignore.is_ignored('/'+member):
                continue
            seen.add(path)
            raw = base64.b64decode(fields[3], validate=True)
            if len(raw) > MAX_CONFIG_SNAPSHOT_FILE:
                raise ValueError('JAR 配置超过单文件限制')
            identifier = hashlib.sha256(('config\0'+path).encode()).hexdigest()[:32]
            fd = os.open(Path(folder)/identifier, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
            catalogue['files'].append({'id':identifier, 'kind':'config', 'source':'jar', 'path':path,
                'root':archive+'!/', 'size':len(raw), 'modifiedAt':0, 'snapshotName':identifier,
                'snapshotTruncated':len(raw) > 262144, 'snapshotObservedAt':check.now()})
    if seen:
        catalogue['warnings'].append('JAR 内配置来自运行进程的包；未确认配置或 profile 实际生效')


