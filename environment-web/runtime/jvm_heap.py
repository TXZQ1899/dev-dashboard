"""JVM 堆转储与离线分析。

流程与 ``jumpserver-server-access`` 一致，全部在目标机经 JumpServer 登录后执行：
jmap 转储 → gzip 压缩 → 目标机临时 HTTP 服务 → 容器拉取并校验 → MAT 离线解析。
校验后的压缩转储归档到数据卷；解压文件与 MAT 索引在分析后清理。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import html
import ipaddress
import math
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from application_check import open_terminal, match_application, ips

MAX_DUMP_BYTES = 8 * 1024 ** 3
MAX_COMPRESS_SOURCE = 8 * 1024 ** 3
DOWNLOAD_RETRIES = 3
MAT_TIMEOUT = 1800
MAT_HEAP = '2500m'
REPORT_DOCS = {'overview': 'System_Overview', 'suspects': 'Leak_Suspects', 'top': 'Top_Components'}


def heap_sizing_recommendation(report):
    """Freeze the candidate and its inputs when comparable sizing evidence is available."""
    runtime = report.get('runtime') or {}
    gc = runtime.get('dumpGc') or {}
    if report.get('status') != 'complete' or gc.get('status') != 'single_full_gc':
        return None
    flags = runtime.get('flags') or {}
    xms = flags.get('InitialHeapSize')
    xmx = flags.get('MaxHeapSize')
    before = (gc.get('before') or {}).get('heapUsedBytes')
    after = (gc.get('after') or {}).get('heapUsedBytes')
    if any(type(value) is not int or value <= 0 for value in (xms, xmx, before, after)):
        return None
    if xms > xmx or after > before or before > xmx:
        return None

    step = 1024 ** 3 if xmx >= 3 * 1024 ** 3 else 256 * 1024 ** 2

    def round_up(value):
        return (value + step - 1) // step * step

    next_xmx = min(xmx, round_up(max(2 * before, 3 * after)))
    next_xms = min(xms, next_xmx, round_up(max(before, 2 * after)))
    return {'ruleVersion': 1, 'taskId': report.get('taskId'), 'finishedAt': report.get('finishedAt'),
            'xmsBytes': next_xms, 'xmxBytes': next_xmx, 'currentXmsBytes': xms, 'currentXmxBytes': xmx,
            'beforeBytes': before, 'afterBytes': after, 'stepBytes': step}


def archive_application(process, asset, apps):
    """Resolve only a uniquely evidenced application across deployment environments."""
    candidates = {}
    host_ips = ips(asset.get('ip', ''))
    for environment in ('PRODUCT', 'TEST', 'SIMULATION'):
        match = match_application(process, host_ips, [], apps, environment)
        for candidate in match.get('candidates', []):
            candidates[candidate['appId']] = candidate
    if len(candidates) != 1:
        return {'appId': None, 'appName': None,
                'confidence': 'AMBIGUOUS' if candidates else 'UNKNOWN'}
    candidate = next(iter(candidates.values()))
    app_id, name = candidate.get('appId', ''), candidate.get('appName', '')
    if not re.fullmatch(r'\d{1,32}', app_id) or not name:
        return {'appId': None, 'appName': None, 'confidence': 'UNKNOWN'}
    return {'appId': app_id, 'appName': name, 'confidence': candidate['confidence']}


def archive_dump(folder, application, server_ip, pid):
    """Atomically retain a verified gzip on the same persistent volume."""
    folder = Path(folder)
    identifier = folder.name
    if not re.fullmatch(r'\d{8}-\d{6}-\d{6}', identifier):
        raise ValueError('invalid task identifier')
    app_id, name = application.get('appId'), application.get('appName')
    if app_id and re.fullmatch(r'\d{1,32}', app_id) and name:
        safe_name = re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('.-')[:120] or 'application'
        group = app_id + '_' + safe_name
    else:
        if type(pid) is not int or pid <= 0:
            raise ValueError('invalid archive process ID')
        address = ipaddress.ip_address(server_ip)
        group = f'{address}_{pid}'
    day = datetime.strptime(identifier[:8], '%Y%m%d').strftime('%Y-%m-%d')
    root = folder.parent / 'dumps'
    root.mkdir(mode=0o700, exist_ok=True)
    group_folder = root / group
    group_folder.mkdir(mode=0o700, exist_ok=True)
    day_folder = group_folder / day
    day_folder.mkdir(mode=0o700, exist_ok=True)
    archive = day_folder / (identifier + '.hprof.gz')
    # Link the verified file without replacing an existing archive. Task cleanup
    # removes the original directory entry without affecting the archived file.
    os.link(folder / 'heap.hprof.gz', archive)
    return archive

# 目标机临时 HTTP 服务：单文件下载成功即自删目录并退出，另设 30 分钟看门狗。
REMOTE_SERVER = r'''import http.server, os, shutil, socketserver, sys, threading, time
root, token, port_file, name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
def stop():
    time.sleep(1)
    try: shutil.rmtree(root)
    except OSError: pass
    os._exit(0)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/' + token + '/' + name:
            self.send_error(404)
            return
        path = os.path.join(root, name)
        try:
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(size))
            self.end_headers()
            with open(path, 'rb') as handle:
                while True:
                    buf = handle.read(262144)
                    if not buf:
                        break
                    self.wfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            return
        threading.Thread(target=stop, daemon=True).start()
    def log_message(self, *args):
        pass
class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
server = S(('0.0.0.0', 0), H)
with open(port_file, 'w') as handle:
    handle.write(str(server.server_address[1]))
threading.Thread(target=lambda: (time.sleep(1800), stop()), daemon=True).start()
server.serve_forever()
'''


class HeapError(RuntimeError):
    """带阶段与错误码的失败，避免把终端原文写入持久数据。"""

    def __init__(self, stage, code, message):
        self.stage, self.code, self.message = stage, code, message
        super().__init__(message)


class Cancelled(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _check_cancel(cancelled):
    if cancelled():
        raise Cancelled('分析已取消')


def _line(body, key):
    match = re.search(r'(?m)^' + re.escape(key) + r'=(.*)$', body)
    return match[1].strip() if match else ''


def _bytes(value, unit):
    scale = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3}.get(unit.upper())
    return int(float(value.replace(',', '')) * scale) if scale else None


def parse_jstat(body):
    """解析 jstat -gc 的单行输出（单位 KiB）。"""
    lines = [line.split() for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    header, values = lines[-2], lines[-1]
    if len(header) != len(values):
        return None
    try:
        parsed = {key: float(value) for key, value in zip(header, values) if value != '-'}
        return {key: value for key, value in parsed.items() if math.isfinite(value) and value >= 0}
    except ValueError:
        return None


def parse_vm_flags(body):
    """解析 jcmd VM.flags 的 -XX 参数（值为原始字节或 KiB）。"""
    flags = {}
    for key, value in re.findall(r'-XX:([A-Za-z][A-Za-z0-9_]*)=(\S+)', body):
        try:
            flags[key] = int(value)
        except ValueError:
            flags[key] = value
    # 新版 JDK 会输出 -XX:+UseG1GC 形式，仅保留布尔位供展示。
    for key in re.findall(r'-XX:\+([A-Za-z][A-Za-z0-9_]*)', body):
        flags.setdefault(key, True)
    return flags


def xss_bytes(flags, cmdline):
    match = re.search(r'-Xss(\d+)([kKmMgG]?)\b', cmdline or '')
    if match:
        scale = {'': 1024, 'k': 1024, 'K': 1024, 'm': 1024 ** 2, 'M': 1024 ** 2,
                 'g': 1024 ** 3, 'G': 1024 ** 3}[match[2]]
        return int(match[1]) * scale
    size = flags.get('ThreadStackSize')
    return int(size) * 1024 if isinstance(size, int) and size > 0 else 1024 * 1024


def rss_composition(rss_kb, jstat, threads, stack_bytes):
    """把 RSS 拆成堆已提交、Metaspace、线程栈和其余 JVM/native。

    堆与 Metaspace 来自 jstat 实测容量；线程栈按线程数 × Xss 估算；
    其余部分是 RSS 减去以上可解释项后的差额（代码缓存、GC 结构、直接内存等）。
    """
    if not rss_kb or not jstat:
        return []
    heap = (jstat.get('S0C', 0) + jstat.get('S1C', 0) + jstat.get('EC', 0) + jstat.get('OC', 0)) * 1024
    metaspace = jstat.get('MC', 0) * 1024
    stacks = threads * stack_bytes
    total = rss_kb * 1024
    known = min(heap + metaspace + stacks, total)
    other = max(0, total - known)

    def item(key, label, value, basis):
        return {'key': key, 'label': label, 'bytes': int(value),
                'percent': round(value * 100 / total, 1) if total else 0, 'basis': basis}
    return [item('heap', 'Java 堆（已提交）', heap, 'measured'),
            item('metaspace', 'Metaspace（已提交）', metaspace, 'measured'),
            item('threads', '线程栈（估算）', stacks, 'estimated'),
            item('native', '其他 JVM / native', other, 'derived')]


class _Text(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        elif tag in ('tr', 'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'li'):
            self.parts.append('\n')
        elif tag in ('td', 'th'):
            self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        elif tag in ('tr', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(markup):
    parser = _Text()
    parser.feed(markup)
    lines = []
    for line in ''.join(parser.parts).split('\n'):
        line = re.sub(r'(\s*\|\s*)+$', '', line)
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if line and line != '|':
            lines.append(line)
    return '\n'.join(lines)


def parse_overview(markup):
    text = html_text(markup)

    def value(label, pattern=r'([\d.,]+)\s*([KMG]?B)?'):
        match = re.search(r'(?i)' + label + r'\s*\|\s*' + pattern, text)
        return match.groups() if match else (None, None)
    used, unit = value('Used heap dump')
    objects, _ = value('Number of objects')
    classes, _ = value('Number of classes')
    loaders, _ = value('Number of class loaders')
    roots, _ = value('Number of GC roots')
    length, _ = value('File length')
    version = re.search(r'JVM version\s*\|\s*(.+)', text)
    return {
        'usedBytes': _bytes(used, unit) if used and unit else None,
        'objects': int(objects.replace(',', '')) if objects else None,
        'classes': int(classes.replace(',', '')) if classes else None,
        'classLoaders': int(loaders.replace(',', '')) if loaders else None,
        'gcRoots': int(roots.replace(',', '')) if roots else None,
        'fileLength': int(length.replace(',', '')) if length else None,
        'jvmVersion': version[1].strip() if version else None,
    }


def parse_top_components(markup):
    result = []
    for match in re.finditer(r'<h2[^>]*>\s*(?:<img[^>]*>\s*)?<a href="pages/[^"]+">([^<]+)</a>', markup):
        label = html.unescape(match[1]).strip()
        found = re.match(r'(.+?) \((\d+)%\)$', label)
        if found:
            result.append({'name': found[1], 'percent': int(found[2])})
    return result[:10]


def parse_suspects(markup):
    text = html_text(markup)
    parts = re.split(r'(?m)^Problem Suspect (\d+)$', text)
    suspects = []
    for index in range(1, len(parts), 2):
        detail = []
        for line in parts[index + 1].split('\n'):
            if line in ('Keywords', 'Table Of Contents', 'Created by Eclipse Memory Analyzer', 'Details »'):
                break
            if line and line not in ('System Overview', 'Leaks', 'Overview') and 'Error rendering chart' not in line:
                detail.append(line)
        if detail:
            text = ' '.join(detail)
            if len(text) > 1200:
                cut = text.rfind('. ', 0, 1200)
                text = text[:cut + 1] if cut > 200 else text[:1200].rstrip() + '…'
            suspects.append({'title': '问题嫌疑 ' + parts[index], 'detail': text})
    return suspects[:6]


def parse_report(folder):
    reports = Path(folder) / 'mat'
    result = {'heap': {}, 'topComponents': [], 'suspects': [], 'reports': {}}
    overview = reports / 'System_Overview' / 'index.html'
    if overview.is_file():
        result['heap'] = parse_overview(overview.read_text(encoding='utf-8', errors='replace'))
    top = reports / 'Top_Components' / 'index.html'
    if top.is_file():
        result['topComponents'] = parse_top_components(top.read_text(encoding='utf-8', errors='replace'))
    suspects = reports / 'Leak_Suspects' / 'index.html'
    if suspects.is_file():
        result['suspects'] = parse_suspects(suspects.read_text(encoding='utf-8', errors='replace'))
    for doc, name in REPORT_DOCS.items():
        candidates = sorted(Path(folder).glob('*_' + name + '.zip'))
        if candidates:
            result['reports'][doc] = {'name': name, 'file': candidates[0].name,
                                      'size': candidates[0].stat().st_size}
    return result


def _upload(terminal, directory, name, payload):
    path = directory + '/' + name
    encoded = base64.b64encode(gzip.compress(payload.encode(), mtime=0)).decode()
    rc, _ = terminal.run(': > ' + shlex.quote(path + '.b64'), timeout=15, wait_for_prompt=True)
    if rc:
        raise HeapError('transfer', 'UPLOAD_FAILED', '无法写入目标机临时文件')
    for index in range(0, len(encoded), 700):
        command = 'printf %s ' + shlex.quote(encoded[index:index + 700]) + ' >> ' + shlex.quote(path + '.b64')
        rc, _ = terminal.run(command, timeout=15, wait_for_prompt=True)
        if rc:
            raise HeapError('transfer', 'UPLOAD_FAILED', '临时脚本上传失败')
    rc, body = terminal.run('base64 -d ' + shlex.quote(path + '.b64') + ' | gzip -dc > ' + shlex.quote(path) +
                            ' && chmod 600 ' + shlex.quote(path) + ' && sha256sum ' + shlex.quote(path),
                            timeout=20, wait_for_prompt=True)
    if rc or body.split()[:1] != [hashlib.sha256(payload.encode()).hexdigest()]:
        raise HeapError('transfer', 'UPLOAD_FAILED', '临时脚本校验失败')
    return path


def _java_home(terminal, pid, started_at=None):
    command = ('p=/proc/' + str(pid) + '; exe=$(readlink -f "$p/exe" 2>/dev/null); '
               'btime=$(awk "/^btime/{print \\$2}" /proc/stat 2>/dev/null); '
               'hz=$(getconf CLK_TCK 2>/dev/null); st=$(awk "{print \\$22}" "$p/stat" 2>/dev/null); '
               'printf "EXE=%s\\n" "$exe"; '
               'printf "START_TICKS=%s\\n" "$st"; '
               'printf "START=%s\\n" "$([ -n "$btime" ] && [ -n "$st" ] && [ -n "$hz" ] && '
               'echo $((btime + st / hz)))"; '
               'tr "\\0" "\\n" < "$p/environ" 2>/dev/null | grep "^JAVA_HOME=" || true')
    rc, body = terminal.run(command, timeout=30)
    if rc:
        raise HeapError('identity', 'PROCESS_READ_FAILED', '无法读取目标进程信息')
    exe, java_home = _line(body, 'EXE'), ''
    match = re.search(r'(?m)^JAVA_HOME=(.*)$', body)
    if match:
        java_home = match[1].strip()
    if not re.search(r'/bin/java$', exe):
        raise HeapError('identity', 'NOT_JAVA', '所选进程不是 JVM 进程（%s）' % (exe or '路径未知'))
    started = _line(body, 'START')
    if started_at and started.isdigit():
        try:
            expected = datetime.fromisoformat(started_at.replace('Z', '+00:00')).timestamp()
            if abs(int(started) - expected) > 300:
                raise HeapError('identity', 'PROCESS_CHANGED', '进程已重启，请刷新快照后重新选择')
        except ValueError:
            pass
    home = java_home or str(Path(exe).parent.parent)
    if not re.fullmatch(r'/[A-Za-z0-9_./+-]+', home):
        raise HeapError('identity', 'INVALID_JAVA_HOME', 'JDK 路径不合法，未执行转储')
    return exe, home, _line(body, 'START_TICKS')


def _runtime_metrics(terminal, pid, java_home, cmdline):
    jstat = java_home + '/bin/jstat'
    jcmd = java_home + '/bin/jcmd'
    command = ('printf "TASKS=%s\\n" "$(ls /proc/' + str(pid) + '/task 2>/dev/null | wc -l)"; '
               'printf "RSS=%s\\n" "$(ps -o rss= -p ' + str(pid) + ' 2>/dev/null | tr -d " ")"; '
               'grep -E "^(VmRSS|VmSize|RssAnon|RssFile|RssShmem):" /proc/' + str(pid) + '/status 2>/dev/null; '
               'echo "---JSTAT"; ' + shlex.quote(jstat) + ' -gc ' + str(pid) + ' 2>&1; '
               'echo "---FLAGS"; ' + shlex.quote(jcmd) + ' ' + str(pid) + ' VM.flags 2>&1; '
               'echo "---END"')
    rc, body = terminal.run(command, timeout=90)
    if rc:
        raise HeapError('metrics', 'JVM_METRICS_FAILED', '读取 JVM 运行指标失败')
    jstat_body = body.split('---JSTAT', 1)[-1].split('---FLAGS', 1)[0]
    flags_body = body.split('---FLAGS', 1)[-1].split('---END', 1)[0]
    values = parse_jstat(jstat_body)
    flags = parse_vm_flags(flags_body)
    tasks = _line(body, 'TASKS')
    rss = _line(body, 'RSS')
    status = dict(re.findall(r'^(VmRSS|VmSize|RssAnon|RssFile|RssShmem):\s*(\d+)\s*kB$', body, re.M))
    return {
        'jstat': values,
        'flags': flags,
        'threads': int(tasks) if tasks.isdigit() else None,
        'rssKB': int(rss) if rss.isdigit() else None,
        'rssAnonKB': int(status['RssAnon']) if 'RssAnon' in status else None,
        'stackBytes': xss_bytes(flags, cmdline),
    }


def gc_metrics(jstat):
    """Convert known jstat fields without substituting zero for missing evidence."""
    fields = {
        'youngUsedBytes': ('S0U', 'S1U', 'EU'), 'oldUsedBytes': ('OU',),
        'heapUsedBytes': ('S0U', 'S1U', 'EU', 'OU'),
        'heapCommittedBytes': ('S0C', 'S1C', 'EC', 'OC'),
        'metaspaceCommittedBytes': ('MC',), 'classSpaceCommittedBytes': ('CCSC',),
    }
    metrics = {key: int(sum(jstat[k] for k in columns) * 1024)
               for key, columns in fields.items() if all(k in jstat for k in columns)}
    for field, output in (('YGC', 'youngCollections'), ('FGC', 'fullCollections')):
        if field in jstat:
            metrics[output] = int(jstat[field])
    for field, output in (('YGCT', 'youngTimeSeconds'), ('FGCT', 'fullTimeSeconds'), ('GCT', 'totalTimeSeconds')):
        if field in jstat:
            metrics[output] = jstat[field]
    return metrics


def compare_dump_gc(body):
    body = '\n' + '\n'.join(body.splitlines()) + '\n'
    def sample(label, end):
        _, marker, rest = body.partition(f'\n---{label}\n')
        section, boundary, _ = rest.partition(f'\n---{end}\n')
        return gc_metrics(parse_jstat(section) or {}) if marker and boundary else {}

    before = sample('BEFORE', 'DUMP')
    after = sample('AFTER', 'END')
    result = {'before': before, 'after': after}
    start = _line(body, 'START_BEFORE')
    if not start.isdigit() or start != _line(body, 'START_AFTER'):
        return {**result, 'status': 'unverified_process'}
    if 'fullCollections' not in before or 'fullCollections' not in after:
        return {**result, 'status': 'unavailable'}
    delta = after['fullCollections'] - before['fullCollections']
    if delta < 0:
        return {**result, 'status': 'unavailable'}
    status = 'single_full_gc' if delta == 1 else 'multiple_full_gc' if delta > 1 else 'no_full_gc'
    result.update(status=status, fullCollectionsDelta=delta)
    if delta > 0 and 'fullTimeSeconds' in before and 'fullTimeSeconds' in after:
        duration = after['fullTimeSeconds'] - before['fullTimeSeconds']
        if duration >= 0:
            result['fullTimeDeltaSeconds'] = round(duration, 3)
    return result


def _dump(terminal, pid, java_home, progress, cancelled, dump_mode='all', start_ticks=''):
    jmap = java_home + '/bin/jmap'
    live = dump_mode == 'live'
    progress('正在执行 live 堆转储（请求 Full GC）' if live else '正在执行 jmap 堆转储（会暂停进程）')
    _check_cancel(cancelled)
    option = 'live,format=b' if live else 'format=b'
    dump = f'{shlex.quote(jmap)} -dump:{option},file="$dir/heap.hprof" {pid}'
    command = 'dir=$(mktemp -d /tmp/envscope-jvm.XXXXXXXXXX) && printf "DIR=%s\\n" "$dir" && '
    if live:
        if not re.fullmatch(r'[0-9]+', start_ticks):
            raise HeapError('identity', 'PROCESS_START_UNKNOWN', '无法确认 JVM 启动标识，未执行 live 转储')
        jstat = f'{shlex.quote(java_home + "/bin/jstat")} -gc {pid} 2>&1'
        start = f"awk '{{sub(/^.*\\) /, \"\"); print $20}}' /proc/{pid}/stat 2>/dev/null"
        command += (
            f'{{ current_start=$({start}); test "$current_start" = {start_ticks} && {{ '
            f'printf "START_BEFORE=%s\\n" "$current_start"; '
            f'printf "%s\\n" ---BEFORE; {jstat}; printf "%s\\n" ---DUMP; '
            f'test "$({start})" = {start_ticks} && {dump} && {{ '
            f'printf "%s\\n" ---AFTER; {jstat}; printf "%s\\n" ---END; '
            f'printf "START_AFTER=%s\\n" "$({start})"; }}; }}; }}')
    else:
        command += dump
    command += ' && printf "SIZE=%s\\n" "$(stat -c %s "$dir/heap.hprof")"'
    rc, body = terminal.run(command, timeout=900)
    directory = _line(body, 'DIR')
    valid_directory = re.fullmatch(r'/tmp/envscope-jvm\.[A-Za-z0-9]{8,20}', directory)
    size_text = _line(body, 'SIZE')
    if not valid_directory:
        raise HeapError('dump', 'DUMP_FAILED', 'jmap 堆转储失败，请确认进程可访问且磁盘空间充足')
    size = int(size_text) if size_text.isdigit() else 0
    if rc or not 0 < size <= MAX_DUMP_BYTES:
        try:
            terminal.run('rm -rf -- ' + shlex.quote(directory), timeout=15)
        except Exception:
            pass
        if size > MAX_DUMP_BYTES:
            raise HeapError('dump', 'DUMP_TOO_LARGE', '堆转储大小超出限制')
        raise HeapError('dump', 'DUMP_FAILED', 'jmap 堆转储失败，请确认进程可访问且磁盘空间充足')
    progress('堆转储完成，正在压缩', dumpBytes=size)
    return directory, size, compare_dump_gc(body) if live else None


def _compress(terminal, directory):
    command = ('gzip -1 -c ' + shlex.quote(directory + '/heap.hprof') + ' > ' + shlex.quote(directory + '/heap.hprof.gz') +
               ' && rm -f ' + shlex.quote(directory + '/heap.hprof') +
               ' && printf "GZ=%s\\n" "$(stat -c %s ' + shlex.quote(directory + '/heap.hprof.gz') + ')"' +
               ' && printf "SHA=%s\\n" "$(sha256sum ' + shlex.quote(directory + '/heap.hprof.gz') + ' | cut -d" " -f1)"')
    rc, body = terminal.run(command, timeout=600)
    size, digest = _line(body, 'GZ'), _line(body, 'SHA')
    if rc or not size.isdigit() or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise HeapError('compress', 'COMPRESS_FAILED', '堆转储压缩失败')
    if int(size) > MAX_COMPRESS_SOURCE:
        raise HeapError('compress', 'ARCHIVE_TOO_LARGE', '压缩后的堆转储超出传输上限')
    return int(size), digest


def _start_server(terminal, directory, token, filename, progress):
    _upload(terminal, directory, 'server.py', REMOTE_SERVER)
    command = ('nohup python3 ' + shlex.quote(directory + '/server.py') + ' ' + shlex.quote(directory) + ' ' +
               shlex.quote(token) + ' ' + shlex.quote(directory + '/port') + ' ' + shlex.quote(filename) +
               ' >' + shlex.quote(directory + '/server.log') + ' 2>&1 & echo $! > ' + shlex.quote(directory + '/pid'))
    rc, _ = terminal.run(command, timeout=30)
    if rc:
        raise HeapError('transfer', 'SERVER_START_FAILED', '目标机临时下载服务启动失败')
    port = ''
    for _ in range(20):
        rc, body = terminal.run('cat ' + shlex.quote(directory + '/port') + ' 2>/dev/null', timeout=15)
        port = body.strip()
        if rc == 0 and port.isdigit():
            return int(port)
        time.sleep(0.5)
    raise HeapError('transfer', 'SERVER_START_FAILED', '目标机临时下载服务未就绪')


def _download(url, destination, size, progress, cancelled):
    destination = Path(destination)
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        _check_cancel(cancelled)
        digest = hashlib.sha256()
        got = 0
        try:
            request = Request(url, headers={'User-Agent': 'envscope-jvm', 'Connection': 'close'})
            with urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise OSError('HTTP %s' % response.status)
                with destination.open('wb') as output:
                    destination.chmod(0o600)
                    while True:
                        _check_cancel(cancelled)
                        buffer = response.read(1 << 20)
                        if not buffer:
                            break
                        output.write(buffer)
                        digest.update(buffer)
                        got += len(buffer)
                        if got // (32 << 20) != (got - len(buffer)) // (32 << 20):
                            progress('正在传输堆转储文件', transferred=got, total=size)
            if got != size:
                raise OSError('接收 %d 字节，预期 %d' % (got, size))
            progress('堆转储传输完成，正在校验', transferred=got, total=size)
            return digest.hexdigest()
        except (OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            if attempt == DOWNLOAD_RETRIES:
                raise HeapError('transfer', 'DOWNLOAD_FAILED', '堆转储传输失败：%s' % str(exc)[:160]) from None
            time.sleep(2)
    raise HeapError('transfer', 'DOWNLOAD_FAILED', '堆转储传输失败')


def _decompress(source, destination, cancelled):
    _check_cancel(cancelled)
    with gzip.open(source, 'rb') as reader, Path(destination).open('wb') as writer:
        shutil.copyfileobj(reader, writer, 1 << 20)
    if Path(destination).stat().st_size <= 0:
        raise HeapError('decompress', 'DECOMPRESS_FAILED', '堆转储解压失败')


def _mat_home():
    for candidate in (os.environ.get('ENVSCOPE_MAT_HOME'), '/data/tools/mat/mat', '/data/tools/mat'):
        if not candidate:
            continue
        home = Path(candidate)
        if (home / 'plugins').is_dir() and (home / 'configuration').is_dir():
            return home
    raise HeapError('analyze', 'MAT_NOT_INSTALLED',
                    '容器内未找到 MAT，请安装到 /data/tools/mat（见 jvm-heap-analysis 技能说明）')


def _jdk21():
    for candidate in (os.environ.get('ENVSCOPE_JDK21_HOME'), '/data/tools/jdk-21.0.13.1+2'):
        if candidate and (Path(candidate) / 'bin' / 'java').is_file():
            return Path(candidate) / 'bin' / 'java'
    raise HeapError('analyze', 'JDK21_NOT_INSTALLED',
                    '容器内未找到 JDK 21（MAT 1.17 要求），请安装到 /data/tools/jdk-21.0.13.1+2')


def _mat_analyze(folder, dump, progress, cancelled):
    mat, java = _mat_home(), _jdk21()
    launchers = sorted((mat / 'plugins').glob('org.eclipse.equinox.launcher_*.jar'))
    if not launchers:
        raise HeapError('analyze', 'MAT_NOT_INSTALLED', 'MAT 启动器缺失，请重新安装 MAT')
    workspace = Path(folder) / 'ws'
    workspace.mkdir(exist_ok=True)
    log_path = Path(folder) / 'mat.log'
    command = [str(java), '--add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED',
               '-Xmx' + MAT_HEAP, '-jar', str(launchers[-1]), '-configuration', str(mat / 'configuration'),
               '-data', str(workspace), '-consolelog', '-nosplash', '-application', 'org.eclipse.mat.api.parse',
               str(dump), 'org.eclipse.mat.api:suspects', 'org.eclipse.mat.api:overview',
               'org.eclipse.mat.api:top_components']
    progress('正在使用 MAT 离线解析堆转储（可能需要数分钟）')
    deadline = time.monotonic() + MAT_TIMEOUT
    with log_path.open('wb') as log:
        process = subprocess.Popen(command, cwd=str(folder), stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if cancelled():
                    process.kill()
                    raise Cancelled('分析已取消')
                if time.monotonic() > deadline:
                    process.kill()
                    raise HeapError('analyze', 'MAT_TIMEOUT', 'MAT 解析超时')
                time.sleep(2)
        finally:
            if process.poll() is None:
                process.kill()
    zips = []
    for name in REPORT_DOCS.values():
        zips.extend(sorted(Path(folder).glob('*_' + name + '.zip')))
    if not zips:
        tail = ''
        try:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-400:]
        except OSError:
            pass
        raise HeapError('analyze', 'MAT_FAILED', 'MAT 未生成报告（退出码 %s）%s' % (process.returncode, tail.strip()[:200]))
    target = Path(folder) / 'mat'
    target.mkdir(exist_ok=True)
    for archive in zips:
        name = REPORT_DOCS.get('top' if 'Top_Components' in archive.name else
                               'suspects' if 'Leak_Suspects' in archive.name else 'overview')
        with zipfile.ZipFile(archive) as source:
            source.extractall(target / name)


def _cleanup(folder):
    folder = Path(folder)
    for pattern in ('*.hprof', '*.hprof.gz', '*.index', '*.threads'):
        for path in folder.glob(pattern):
            path.unlink(missing_ok=True)
    shutil.rmtree(folder / 'ws', ignore_errors=True)


def analyze(folder, asset, process, saved, ip_map, progress=lambda *a, **k: None, cancelled=lambda: False,
            application=None, dump_mode='all'):
    """登录目标机转储堆、拉回容器并离线解析，返回结构化报告。"""
    folder = Path(folder)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    pid = process['pid']
    started = now()
    report = {
        'schemaVersion': 1, 'startedAt': started, 'finishedAt': None, 'status': 'running',
        'target': {'assetId': asset.get('id'), 'serverIp': asset.get('ip'), 'pid': pid,
                   'processName': process.get('name'), 'command': process.get('command')},
        'dump': {'mode': dump_mode}, 'heap': {}, 'runtime': {}, 'rss': [], 'topComponents': [], 'suspects': [],
        'reports': {}, 'warnings': [], 'error': None,
    }
    terminal = None
    remote_directory = None
    try:
        progress('正在通过 JumpServer 登录目标服务器')
        terminal, elevation = open_terminal(asset, saved, ip_map)
        if elevation not in ('root', 'passwordless', 'password'):
            report['warnings'].append('未确认 root 权限，堆转储可能失败')
        progress('正在确认 JVM 进程与 JDK 路径')
        exe, java_home, start_ticks = _java_home(terminal, pid, process.get('startedAt'))
        report['target'].update(exe=exe, javaHome=java_home)
        metrics = _runtime_metrics(terminal, pid, java_home, process.get('command') or '')
        report['runtime'] = {
            'threads': metrics['threads'],
            'rssBytes': metrics['rssKB'] * 1024 if metrics['rssKB'] else None,
            'rssAnonBytes': metrics['rssAnonKB'] * 1024 if metrics['rssAnonKB'] else None,
            'stackBytes': metrics['stackBytes'],
            'flags': {key: metrics['flags'][key] for key in
                      ('InitialHeapSize', 'MaxHeapSize', 'ThreadStackSize', 'UseG1GC', 'UseParallelGC', 'UseConcMarkSweepGC')
                      if key in metrics['flags']},
        }
        jstat = metrics['jstat'] or {}
        if jstat:
            report['runtime']['gc'] = gc_metrics(jstat)
        report['rss'] = rss_composition(metrics['rssKB'], jstat, metrics['threads'] or 0, metrics['stackBytes'])

        _check_cancel(cancelled)
        directory, size, comparison = _dump(terminal, pid, java_home, progress, cancelled, dump_mode, start_ticks)
        remote_directory = directory
        report['dump'].update(dumpBytes=size)
        if comparison is not None:
            report['runtime']['dumpGc'] = comparison
        progress('正在压缩堆转储文件')
        compressed, digest = _compress(terminal, directory)
        report['dump'].update(compressedBytes=compressed, sha256=digest)
        _check_cancel(cancelled)
        token = uuid.uuid4().hex
        port = _start_server(terminal, directory, token, 'heap.hprof.gz', progress)
        host = asset.get('ip')
        url = 'http://%s:%d/%s/heap.hprof.gz' % (host, port, token)
        progress('正在传输堆转储文件到容器')
        received = _download(url, folder / 'heap.hprof.gz', compressed, progress, cancelled)
        if received != digest:
            raise HeapError('transfer', 'CHECKSUM_MISMATCH', '堆转储校验失败，请重试')
        progress('正在归档已校验的堆转储')
        application = application or {'appId': None, 'appName': None, 'confidence': 'UNKNOWN'}
        archive = archive_dump(folder, application, host, pid)
        report['dump'].update(archivePath=str(archive), application=application)
        progress('正在解压堆转储文件')
        _decompress(archive, folder / 'heap.hprof', cancelled)
        _mat_analyze(folder, folder / 'heap.hprof', progress, cancelled)
        progress('正在整理分析结论')
        parsed = parse_report(folder)
        report.update(heap=parsed['heap'], topComponents=parsed['topComponents'],
                      suspects=parsed['suspects'], reports=parsed['reports'])
        report['status'] = 'complete'
        report['summary'] = summarize(report)
        return report
    except Cancelled:
        report['status'] = 'cancelled'
        report['error'] = {'stage': 'cancelled', 'code': 'CANCELLED', 'message': '分析已取消'}
        return report
    except HeapError as exc:
        report['status'] = 'failed'
        report['error'] = {'stage': exc.stage, 'code': exc.code, 'message': exc.message}
        return report
    except Exception as exc:  # 不把终端原文、路径或凭据写入报告
        report['status'] = 'failed'
        report['error'] = {'stage': 'internal', 'code': 'JDK_ANALYSIS_INTERNAL',
                           'message': 'JVM 分析异常：' + type(exc).__name__}
        return report
    finally:
        if terminal:
            if remote_directory:
                try:
                    terminal.run('rm -rf -- ' + shlex.quote(remote_directory), timeout=15)
                except Exception:
                    pass
            try:
                terminal.close()
            except Exception:
                pass
        report['finishedAt'] = now()
        _cleanup(folder)


def summarize(report):
    """面向页面的一句话结论，仅使用已解析的实测数值。"""
    heap, runtime = report.get('heap') or {}, report.get('runtime') or {}
    gc = runtime.get('gc') or {}
    if not heap.get('usedBytes'):
        return ''
    parts = ['MAT 纳入分析的堆对象 {:,} 个，占用 {:.1f} MiB，类加载器 {:,} 个'.format(
        heap.get('objects') or 0, heap['usedBytes'] / 1048576, heap.get('classLoaders') or 0)]
    if gc.get('heapCommittedBytes') and runtime.get('rssBytes'):
        parts.append('进程 RSS %.1f GiB，JVM 已提交堆 %.1f GiB' %
                     (runtime['rssBytes'] / 1073741824, gc['heapCommittedBytes'] / 1073741824))
    if report.get('suspects'):
        parts.append('MAT 给出 %d 条泄漏嫌疑，需结合业务判断' % len(report['suspects']))
    return '；'.join(parts) + '。'
