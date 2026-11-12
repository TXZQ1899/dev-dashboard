"""Bounded, metadata-only diagnostic log for the application config batch."""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_BYTES = 1024 * 1024
READ_BYTES = 192 * 1024
MAX_LINES = 250
EVENTS = {'task_start', 'task_finish', 'task_error', 'phase', 'app_finish',
          'file_start', 'file_done', 'file_error', 'tree_start', 'tree_done', 'tree_error',
          'nacos_start', 'nacos_done', 'nacos_error', 'mapper_skip', 'resource_skip'}


def reset(folder):
    path = folder/'application-config-job.log'
    with path.open('w', encoding='utf-8') as output:
        os.chmod(path, 0o600)
    (folder/'application-config-job.log.1').unlink(missing_ok=True)


def append(folder, event, **values):
    if event not in EVENTS: return
    record = {'time':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'), 'event':event}
    for key in ('appId', 'name', 'phase', 'status'):
        value = values.get(key)
        if isinstance(value, str): record[key] = ''.join(c for c in value if c.isprintable())[:120]
    path = values.get('path')
    if isinstance(path, str):
        record['path'] = path[:300] if re.fullmatch(r'[\w./-]{1,300}', path) and '..' not in path.split('/') else '[路径无法安全显示]'
    kind = values.get('errorKind')
    if isinstance(kind, str) and (kind in ('请求失败', 'Codeup 连接失败或超时', 'Codeup 文件树响应无效', 'Codeup 登录失效') or re.fullmatch(r'(?:Codeup|Nacos) HTTP \d{3}', kind)):
        record['errorKind'] = kind
    for key in ('page', 'count', 'elapsedMs', 'completed', 'total'):
        value = values.get(key)
        if isinstance(value, int) and 0 <= value <= 100000000: record[key] = value
    raw = (json.dumps(record, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
    path = folder/'application-config-job.log'
    if path.exists() and path.stat().st_size + len(raw) > MAX_BYTES:
        os.replace(path, folder/'application-config-job.log.1')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'wb') as output: output.write(raw)


def tail(folder):
    path = folder/'application-config-job.log'
    try:
        with path.open('rb') as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            start = max(0, size-READ_BYTES)
            source.seek(start)
            raw = source.read(READ_BYTES)
        lines = raw.splitlines()
        if start and lines: lines = lines[1:]
        events = []
        for line in lines[-MAX_LINES:]:
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('event') in EVENTS: events.append(event)
            except (ValueError, UnicodeError): continue
        return {'events':events, 'truncated': start > 0 or len(lines) > MAX_LINES or (folder/'application-config-job.log.1').exists()}
    except FileNotFoundError:
        return {'events':[], 'truncated':False}
