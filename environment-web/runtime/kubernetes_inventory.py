"""Read-only Kubernetes process inventory collected through JumpServer.

Cgroup identity is direct evidence; CRI metadata and namespace listeners remain
separate from host listeners. No environment variables or full inspect output.
"""
import base64
import json
import re
import shlex
from datetime import datetime
from pathlib import Path

PROBE = Path(__file__).parent / 'probes' / 'kubernetes_inventory.sh'
CGROUP = re.compile(r'(?:^|[/.-])pod([0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12})(?:\.slice|/|$)', re.I)
CONTAINER = re.compile(r'(?:cri-containerd-|crio-|docker-)?([0-9a-f]{64})(?:\.scope|/|$)', re.I)


def parse_cgroup(value):
    if 'kubepods' not in value:
        return None
    pod = CGROUP.search(value)
    container = CONTAINER.search(value)
    if not pod:
        return None
    return {'podUid': pod.group(1).replace('_', '-').lower(),
            'containerId': container.group(1).lower() if container else None}


def parse_output(body, processes):
    expected = {p['pid']: p for p in processes}
    result = {'status': 'partial', 'processes': [], 'warnings': []}
    rows, metadata, ended = {}, {}, False
    for line in body.splitlines():
        parts = line.split('\t')
        if parts[0] == 'K8S_END':
            ended = True
        elif parts[0] == 'K8S_META' and len(parts) == 2:
            try:
                value = json.loads(base64.b64decode(parts[1], validate=True))
                metadata = {x['id']: x for x in value if re.fullmatch(r'[0-9a-f]{64}', x.get('id', ''))}
            except (ValueError, TypeError, KeyError):
                result['warnings'].append('CRI 元数据无法解析')
        elif parts[0] == 'K8S_MISSING' and len(parts) == 2 and parts[1] != '0':
            result['warnings'].append('部分进程已退出或 cgroup 不可读')
        elif parts[0] == 'K8S_PROC' and len(parts) == 7:
            try:
                pid = int(parts[1]); start = int(parts[2]); end = int(parts[3])
                cgroup = base64.b64decode(parts[4], validate=True).decode()
                ns = parts[5]
                if pid not in expected or start <= 0 or start != end or not re.fullmatch(r'net:\[\d+\]', ns):
                    continue
                try:
                    observed = datetime.fromisoformat(expected[pid]['startedAt'].replace('Z', '+00:00')).timestamp()
                    if abs(observed - int(parts[6])) > 2:
                        result['warnings'].append('进程启动时间变化或不可核验')
                        continue
                except (KeyError, ValueError, TypeError):
                    result['warnings'].append('进程启动时间变化或不可核验')
                    continue
                identity = parse_cgroup(cgroup)
                if identity:
                    rows[pid] = {'pid': pid, 'startTicks': str(start), 'networkNamespace': ns,
                                 **identity, 'confidence': 'EXACT', 'basis': '/proc/PID/cgroup',
                                 'listeners': [], 'listenerStatus': 'not_collected'}
            except (ValueError, UnicodeError):
                result['warnings'].append('进程身份读取不完整')
        elif parts[0] == 'K8S_LISTENER' and len(parts) == 4:
            try:
                pid, port = int(parts[1]), int(parts[3]); addr = parts[2]
                if pid in rows and 0 < port < 65536 and re.fullmatch(r'[0-9a-fA-F:.\[*\]%_-]+', addr):
                    rows[pid]['listeners'].append({'address': addr, 'port': port, 'protocol': 'tcp', 'confidence': 'EXACT'})
            except ValueError:
                pass
        elif parts[0] == 'K8S_LISTENER_STATUS' and len(parts) == 3:
            try:
                pid = int(parts[1])
                if pid in rows and parts[2] in ('observed', 'unknown'):
                    rows[pid]['listenerStatus'] = parts[2]
            except ValueError:
                pass
    for row in rows.values():
        meta = metadata.get(row.get('containerId'))
        if meta and meta.get('podUid') == row['podUid']:
            for key in ('podName', 'namespace', 'containerName', 'image'):
                if isinstance(meta.get(key), str) and len(meta[key]) <= 512:
                    row[key] = meta[key]
    result['processes'] = sorted(rows.values(), key=lambda x: x['pid'])
    if rows and not metadata:
        result['warnings'].append('CRI 元数据不可用，Pod 与容器名称未确认')
    if any(r['listenerStatus'] != 'observed' for r in rows.values()):
        result['warnings'].append('部分 Pod 进程监听未完成采集')
    if not ended:
        result['warnings'].append('探针未完整结束')
    elif len(processes) > 512:
        result['warnings'].append('进程数超过 512，仅检查前 512 个')
    elif not result['warnings']:
        result['status'] = 'complete'
    return result


def collect_kubernetes_inventory(terminal, processes):
    pids = [p['pid'] for p in processes if isinstance(p.get('pid'), int) and p['pid'] > 0 and not p.get('system')]
    if not pids:
        return {'status': 'partial', 'processes': [], 'warnings': ['没有可检查的进程']}
    script = base64.b64encode(PROBE.read_bytes()).decode()
    command = 'bash -c ' + shlex.quote('printf %s ' + shlex.quote(script) + ' | base64 -d | timeout 75 bash -s -- ' + ' '.join(map(str, pids[:512])))
    try:
        rc, body = terminal.run(command, timeout=100)
        if rc == 124:
            return parse_output(body, processes)
        if rc != 0:
            return {'status': 'failed', 'processes': [], 'warnings': ['Kubernetes 探针执行失败']}
        return parse_output(body, processes)
    except Exception:
        return {'status': 'failed', 'processes': [], 'warnings': ['Kubernetes 探针不可用']}
