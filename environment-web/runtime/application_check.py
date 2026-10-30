"""Single-process TCP dependency inspection, executed by the container service.

Snapshot identity is a hint; socket ownership is read live on both assets.
No credentials, terminal transcripts or raw application configuration are saved.
"""
from __future__ import annotations

import base64
import hashlib
import gzip
import ipaddress
import json
import math
import posixpath
import re
import shlex
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler

import server_inventory as si
from application_config import ignore as config_ignore, nacos, zookeeper as zookeeper_config
from application_config.discovery import config_roots, tomcat_docbase_roots
from application_config.endpoints import config_kind, normal_ip, parse_config

ENVIRONMENTS = ('PRODUCT', 'TEST', 'SIMULATION')
PROBES = Path(__file__).with_name('probes')
PORT_HINTS = {3306: 'MySQL', 5432: 'PostgreSQL', 6379: 'Redis', 6380: 'Redis',
              9200: 'Elasticsearch', 9300: 'Elasticsearch', 9092: 'Kafka',
              5672: 'RabbitMQ', 61616: 'ActiveMQ', 9876: 'RocketMQ'}
SAMPLE_PERIOD = 0.2
BATCH_SAMPLES = 5
LISTENER_CHECK_WORKERS = 4
# The remote wrapper ends the probe before the local hard deadline, so an
# unresponsive asset cannot leave a read-only script running after we stop
# waiting (or hold the PTY for the next command).
PROBE_TIMEOUT = 35
PROBE_REMOTE_TIMEOUT = 30


class ProbeError(RuntimeError):
    """Safe structured diagnostics, without persisting arbitrary terminal text."""
    def __init__(self, code, stage, message, exit_code=None, diagnostics=None):
        self.details = {'code': code, 'stage': stage, 'message': message,
                        'exitCode': exit_code, 'diagnostics': diagnostics or []}
        suffix = '；退出码 '+str(exit_code) if exit_code is not None else ''
        super().__init__(message+'（阶段 '+stage+suffix+'）')


def safe_diagnostics(body):
    """Classify known shell/tool failures; never return raw stderr or stdout."""
    result = []
    for pattern, message in (
        (r'unbound variable', '远端 Shell 变量未定义，可能存在 Bash 版本兼容问题'),
        (r'syntax error|unexpected token', '远端命令的参数或脚本解析失败'),
        (r'invalid option|unrecognized option|unknown option', '远端工具不支持探针使用的选项'),
        (r'permission denied|operation not permitted', '权限或系统策略拒绝执行'),
        (r'command not found|No such file or directory', '远端命令或依赖不存在'),
        (r'cannot open netlink|netlink.*not supported', '远端无法读取 socket 的 Netlink 信息')):
        if re.search(pattern, body, re.I):
            result.append(message)
    return result


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def ips(value):
    return {ip for part in re.split(r'[\s,;]+', str(value or '')) if (ip := normal_ip(part))}


def endpoint(address):
    host, sep, port = address.rpartition(':')
    if port == '*':
        port = '0'
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        return None
    host = host.strip('[]')
    return {'ip': normal_ip(host) or host, 'port': int(port), 'protocol': 'tcp'}


def parse_sockets(body, inode_owners=None):
    """Handle ss with/without State/Netid and multi-line internal TCP stats."""
    result, current = [], None
    states = {'ESTAB', 'LISTEN', 'SYN-SENT', 'SYN-RECV', 'TIME-WAIT', 'CLOSE-WAIT',
              'FIN-WAIT-1', 'FIN-WAIT-2', 'LAST-ACK', 'CLOSING', 'UNCONN', 'CLOSED'}
    for raw in body.splitlines():
        # Older ss can attach users:(...) directly to the peer port.
        parts = raw.split('users:(', 1)[0].split()
        if not parts:
            continue
        offset = 1 if parts[0] in ('tcp', 'tcp6') else 0
        state = parts[offset] if len(parts) > offset and parts[offset] in states else 'UNKNOWN'
        if len(parts) > offset and parts[offset] in states:
            offset += 1
        # Address pairs follow Recv-Q / Send-Q; detail rows do not start in numbers.
        if len(parts) >= offset + 4 and parts[offset].isdigit() and parts[offset+1].isdigit():
            local, peer = endpoint(parts[offset+2]), endpoint(parts[offset+3])
            if not local or not peer:
                current = None
                continue
            pids = sorted({int(pid) for pid in re.findall(r'pid=(\d+)', raw)})
            cookie = re.search(r'\bsk:(\S+)', raw)
            inode = re.search(r'\bino:(\d+)', raw)
            if inode and inode_owners:
                pids = sorted(set(pids) | set(inode_owners.get(inode[1], [])))
            current = dict(local=local, peer=peer, state=state, pids=pids,
                           socketId=cookie[1] if cookie else inode[1] if inode else None,
                           bytesSent=None, bytesReceived=None)
            result.append(current)
        if current is not None:
            for field, pattern in (('bytesSent', r'\bbytes_acked:(\d+)'),
                                   ('bytesReceived', r'\bbytes_received:(\d+)')):
                found = re.search(pattern, raw)
                if found:
                    current[field] = int(found[1])
    return result


def decode(value):
    return base64.b64decode(value, validate=True).decode('utf-8', errors='replace').strip()


def section(body, name):
    match = re.search(r'(?m)^CHECK_'+name+r'_BEGIN\n(.*?)^CHECK_'+name+r'_END\s*$', body, re.S)
    return match[1] if match else ''


TRANSFER_BLOB = re.compile(r'(?ms)^CHECK_BLOB_BEGIN\t([\w:.-]+)\n(.*?)^CHECK_BLOB_END\s*$')


def expand_transfer(body, stage='socket_probe'):
    """Restore the compressed frame/catalog sections sent by the remote probe.

    The probe compresses each completed section to bound console transfer. A
    successful exit guarantees complete blocks; a corrupt block means the batch
    cannot be trusted and is rejected rather than parsed partially.
    """
    if 'CHECK_BLOB_BEGIN' not in body:
        return body

    def restore(match):
        try:
            payload = base64.b64decode(re.sub(r'\s+', '', match[2]), validate=True)
            return gzip.decompress(payload).decode('utf-8', 'replace') if payload else ''
        except (ValueError, OSError, UnicodeError):
            raise ProbeError('PROBE_OUTPUT_INCOMPLETE', stage,
                             '连续采样的压缩数据传输不完整，未使用残缺结果') from None

    return TRANSFER_BLOB.sub(restore, body)


def parse_process_record(parts):
    rows, _, malformed = si.parse_processes(decode(parts[3]))
    if malformed or len(rows) != 1 or rows[0]['pid'] != int(parts[1]):
        raise ValueError('无法解析实时进程身份')
    process = rows[0]
    process.update(startTicks=parts[2], cwd=decode(parts[4]), exe=decode(parts[5]), netns=decode(parts[6]))
    return process


def parse_probe(body, stage='socket_probe', catalog=None):
    processes, warnings, statuses = {}, [], {}
    inode_owners = {}
    for line in body.splitlines():
        parts = line.split('\t')
        if parts[0] == 'CHECK_PROC' and len(parts) == 7:
            try:
                process = parse_process_record(parts)
            except ValueError:
                warnings.append('无法解析实时进程身份')
                continue
            # Commands are used only for matching and deriving bounded config roots.
            processes[process['pid']] = process
        elif parts[0] == 'CHECK_PROC_REF' and len(parts) == 4:
            process = (catalog or {}).get(parts[3])
            if process and str(process['pid']) == parts[1] and process['startTicks'] == parts[2]:
                processes[process['pid']] = process
            else:
                warnings.append('实时进程身份引用未通过核验')
        elif parts[0] == 'CHECK_WARNING' and len(parts) > 1:
            warnings.append(parts[1])
        elif parts[0] == 'CHECK_INODE_OWNER' and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            inode_owners.setdefault(parts[1], []).append(int(parts[2]))
        elif parts[0] in ('CHECK_SS_RC', 'CHECK_LISTEN_RC', 'CHECK_UID') and len(parts) == 2:
            statuses[parts[0]] = parts[1]
    if 'CHECK_SS_RC' not in statuses or 'CHECK_LISTEN_RC' not in statuses:
        raise ProbeError('PROBE_OUTPUT_INCOMPLETE', stage, '探针未返回完整的执行状态', diagnostics=safe_diagnostics(body))
    for field, label in (('CHECK_SS_RC', '连接枚举'), ('CHECK_LISTEN_RC', '监听端口枚举')):
        if statuses[field] != '0':
            diagnostics = []
            for line in body.splitlines():
                parts = line.split('\t')
                if len(parts) == 4 and parts[0] == 'CHECK_DIAGNOSTIC':
                    try:
                        diagnostics.extend(safe_diagnostics(decode(parts[3])))
                    except (ValueError, UnicodeError):
                        pass
            raise ProbeError('SOCKET_COMMAND_FAILED', stage, label+'失败',
                             int(statuses[field]) if statuses[field].isdigit() else None,
                             list(dict.fromkeys(diagnostics)))
    if statuses.get('CHECK_UID') != '0':
        warnings.append('未提权至 root，其他用户的 socket/PID 归属可能不可见')
    namespace = re.search(r'(?m)^CHECK_NAMESPACE\t(net:\[\d+\])$', body)
    return {'sockets': parse_sockets(section(body, 'SOCKETS'), inode_owners),
            'listeners': parse_sockets(section(body, 'LISTEN'), inode_owners),
            'processes': processes, 'warnings': warnings,
            'networkNamespace': namespace[1] if namespace else None}


def quote_probe_argument(value):
    # A JumpServer PTY can truncate a pasted physical line in canonical mode.
    # Adjacent quoted chunks joined by shell continuations retain one exact
    # argument while keeping each input line below the terminal's line limit.
    return '\\\n'.join(shlex.quote(value[index:index+512])
                       for index in range(0, len(value), 512)) or "''"


def iso_stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='milliseconds')


def parse_observations(body, stage, sent_at, received_at):
    """Bound remote clock offset by the request/response, not by assuming NTP.

    A capture's bounds include ss enumeration and transport uncertainty. EXACT
    attribution later requires the entire peer bound inside target evidence.
    """
    blocks = re.findall(r'(?m)^CHECK_SAMPLE_BEGIN\n(.*?)^CHECK_SAMPLE_END\s*$', body, re.S)
    if not blocks:
        return parse_probe(body, stage)
    begin = re.search(r'(?m)^CHECK_CLOCK_BEGIN\t([\d.]+)$', body)
    end = re.search(r'(?m)^CHECK_CLOCK_END\t([\d.]+)$', body)
    if not begin or not end:
        raise ProbeError('PROBE_OUTPUT_INCOMPLETE', stage, '连续采样未返回完整的时钟边界')
    remote_begin, remote_end = float(begin[1]), float(end[1])
    low, high = sent_at-remote_begin, received_at-remote_end
    if remote_end < remote_begin or low > high:
        raise ProbeError('PROBE_CLOCK_CHANGED', stage, '采样期间时钟发生变化，无法确认观察时间')
    frames = []
    catalog = {}
    for version, record in re.findall(r'(?m)^CHECK_PROC_VERSION\t(\d+)\t(CHECK_PROC\t[^\n]+)$', body):
        try:
            catalog[version] = parse_process_record(record.split('\t'))
        except (ValueError, IndexError):
            raise ProbeError('PROBE_OUTPUT_INCOMPLETE', stage, '连续采样的进程身份目录不完整') from None
    for block in blocks:
        frame = parse_probe(block, stage, catalog)
        capture = re.search(r'(?m)^CHECK_OBSERVED\t([\d.]+)\t([\d.]+)$', block)
        if not capture or not remote_begin <= float(capture[1]) <= float(capture[2]) <= remote_end:
            raise ProbeError('PROBE_OUTPUT_INCOMPLETE', stage, '连续采样未返回有效的 socket 观察时间')
        first, last = float(capture[1])+low, float(capture[2])+high
        frame.update(observedAt=iso_stamp((first+last)/2),
                     observationStart=iso_stamp(first), observationEnd=iso_stamp(math.ceil(last*1000)/1000))
        frames.append(frame)
    truncation = re.search(r'(?m)^CHECK_TRUNCATED\t([\w-]+)\t(\d+)\t(\d+)$', body)
    if truncation:
        labels = {'sample-budget': '采样预算', 'transfer-budget': '传输预算'}
        reason, done, requested = truncation[1], truncation[2], truncation[3]
        frames[-1]['warnings'].append(
            '连续采样因'+labels.get(reason, '时间')+'用尽提前结束，仅完成 '+done+'/'+requested+
            ' 帧，对端观测窗口被截断')
    return {**frames[-1], 'observations': frames,
            'warnings': list(dict.fromkeys(w for frame in frames for w in frame['warnings']))}


def observation_frames(sample):
    return sample.get('observations', [sample])


def probe(terminal, mode, selector, batch=False):
    script = (PROBES/'application_sockets.sh').read_text()
    if mode == 'container-target':
        args = ['target', str(selector['pid']), '', selector['startTicks'], '',
                str(BATCH_SAMPLES if batch else 1), str(SAMPLE_PERIOD)]
    elif mode == 'container-listeners':
        args = ['listeners', selector['filter'], str(selector['pid']), selector['startTicks']]
    elif mode == 'container-peer':
        args = ['peer', selector['filter'], str(selector['pid']), selector['startTicks'], selector['listenFilter']]
    else:
        args = [mode, str(selector)]
        if batch:
            args.extend(['', '', '', str(BATCH_SAMPLES), str(SAMPLE_PERIOD)])
    # Koko's interactive PTY must not parse the probe's nested quotes/newlines.
    # Bound script input lines too, not just a potentially long ss filter.
    if isinstance(terminal, ProbeTerminal):
        command = terminal.probe_command(script, args, 'timeout '+str(PROBE_REMOTE_TIMEOUT))
    else:
        encoded_script = quote_probe_argument(base64.b64encode(script.encode()).decode())
        command = 'timeout '+str(PROBE_REMOTE_TIMEOUT)+' bash -c "$(printf %s '+encoded_script+' | base64 -d)" _ '+\
                  ' '.join(quote_probe_argument(arg) for arg in args)
    retried = False
    for attempt in range(2):
        try:
            sent_at = time.time()
            options = {'timeout': PROBE_TIMEOUT}
            if isinstance(terminal, si.Terminal):
                options['wait_for_prompt'] = True
            rc, body = terminal.run(command, **options)
            received_at = time.time()
            break
        except TimeoutError:
            aborted = bool(getattr(terminal, '_aborted', False))
            if attempt or aborted:
                if aborted:
                    detail = '等待远端 socket 探针返回超时，终端已关闭，未在同一会话上重试'
                else:
                    detail = '等待远端 socket 探针返回超时，单次等待上限 35 秒，重试一次后仍未完成'
                raise ProbeError('SOCKET_PROBE_TIMEOUT', mode+'_probe', detail,
                    diagnostics=['通过 JumpServer 等待只读探针结束标记超时；无法仅据此区分命令执行与终端传输延迟']) from None
            # A non-aborted terminal (for example a test double) may still be in
            # sync. Real Terminal.run() marks the session aborted on timeout.
            retried = True
    if rc:
        reasons = {3: '进程已退出或身份发生变化，请重新采集', 4: '无法读取进程身份',
                   5: '应用处于其他网络命名空间，远端缺少 nsenter', 6: '远端缺少 ss 或 timeout',
                   7: '无法读取所选进程的 socket 文件描述符归属',
                   8: '对端 socket 结果处理失败，未将错误视为空连接',
                   124: '远端探针超过 '+str(PROBE_REMOTE_TIMEOUT)+' 秒被终止，未返回完整样本',
                   137: '远端探针超过 '+str(PROBE_REMOTE_TIMEOUT)+' 秒被终止，未返回完整样本'}
        raise ProbeError('PROBE_COMMAND_FAILED', mode+'_probe', reasons.get(rc, '远端连接检查失败'),
                         rc, safe_diagnostics(body))
    sample = parse_observations(expand_transfer(body, mode+'_probe'), mode+'_probe', sent_at, received_at)
    if retried:
        sample['warnings'].append('socket 探针等待终端返回超时，重试一次后成功')
    return sample


def sampling_probe(terminal, mode, selector):
    return probe(terminal, mode, selector, batch=mode in ('target', 'peer', 'container-target'))


def is_docker_proxy(process):
    executable = posixpath.basename(process.get('exe', ''))
    return any(re.fullmatch(r'docker-proxy(?:[-\w.]*)', value)
               for value in (process.get('name', ''), executable))


def docker_containers(terminal, container_id=None):
    """Only networking metadata; never fetch container Env, mounts or credentials."""
    script = (PROBES/'application_docker.sh').read_text()
    args = ['_', container_id] if container_id else []
    rc, body = terminal.run('bash -c '+shlex.quote(script)+(' '+shlex.join(args) if args else ''), timeout=22)
    status = re.search(r'(?m)^CHECK_DOCKER_STATUS\t(\w+)$', body)
    if rc or not status:
        return [], ['Docker 端口映射检查未完成，未确认容器归属']
    if status[1] == 'not_available':
        return [], []
    if status[1] != 'complete':
        return [], ['Docker 运行容器超过 64 个，未继续核验' if status[1] == 'limit' else
                    'Docker 端口映射不可读或检查失败，未确认容器归属']
    containers = []
    try:
        for line in body.splitlines():
            if not line.startswith('CHECK_DOCKER\t'):
                continue
            fields = line.split('\t')
            if len(fields) != 8:
                raise ValueError('incomplete Docker metadata')
            _, identifier, name, pid, ticks, namespace, ports, addresses = fields
            if (not re.fullmatch(r'[0-9a-f]{64}', identifier) or not pid.isdigit() or int(pid) <= 0 or
                    not ticks.isdigit() or not re.fullmatch(r'net:\[\d+\]', namespace)):
                raise ValueError('invalid Docker identity')
            ports, name = json.loads(ports), json.loads(name)
            if not isinstance(ports, (dict, type(None))) or not isinstance(name, str):
                raise ValueError('invalid Docker metadata')
            containers.append({'id': identifier, 'name': name.lstrip('/'), 'initPid': int(pid),
                'startTicks': ticks, 'networkNamespace': namespace, 'ports': ports or {},
                'ips': sorted(ips(addresses))})
        if len(containers) > 64:
            raise ValueError('too many containers')
    except (ValueError, TypeError):
        return [], ['Docker 端口映射数据不完整，未确认容器归属']
    return containers, [line.split('\t', 1)[1] for line in body.splitlines() if line.startswith('CHECK_WARNING\t')]


def docker_mappings(containers, flow):
    peer = flow['peer']
    if peer['protocol'] != 'tcp' or flow['local']['protocol'] != 'tcp':
        return []
    mappings = []
    for container in containers:
        for endpoint_key, bindings in container['ports'].items():
            match = re.fullmatch(r'(\d{1,5})/tcp', endpoint_key)
            if not match or not 0 < int(match[1]) <= 65535 or not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict) or str(binding.get('HostPort')) != str(peer['port']):
                    continue
                host = normal_ip(binding.get('HostIp'))
                if host != peer['ip'] and not (host in ('0.0.0.0', '::') and
                        ipaddress.ip_address(host).version == ipaddress.ip_address(peer['ip']).version):
                    continue
                for address in container['ips']:
                    mapping = {'containerId': container['id'], 'containerName': container['name'],
                        'initPid': container['initPid'], 'published': dict(peer),
                        'target': {'ip': address, 'port': int(match[1]), 'protocol': 'tcp'}}
                    if mapping not in mappings:
                        mappings.append(mapping)
    return mappings


def resolve_docker_target(terminal, proxy, sample_probe, evidence):
    """Resolve a proxy entry to one live listener, never to a guessed container PID."""
    def fail(message, ambiguous=False):
        evidence.update(resolution='AMBIGUOUS' if ambiguous else 'UNKNOWN', reason=message)
        raise ProbeError('DOCKER_TARGET_AMBIGUOUS' if ambiguous else 'DOCKER_TARGET_UNRESOLVED',
                         'docker_target', message)

    try:
        tokens = shlex.split(proxy.get('command', ''))
        fields = {}
        for flag in ('-proto', '-host-ip', '-host-port', '-container-ip', '-container-port'):
            if tokens.count(flag) != 1 or tokens.index(flag)+1 >= len(tokens):
                raise ValueError('missing or repeated proxy argument')
            fields[flag] = tokens[tokens.index(flag)+1]
        host, address = normal_ip(fields['-host-ip']), normal_ip(fields['-container-ip'])
        host_port, port = int(fields['-host-port']), int(fields['-container-port'])
        if fields['-proto'] != 'tcp' or not host or not address or not 0 < host_port <= 65535 or not 0 < port <= 65535:
            raise ValueError('invalid proxy endpoint')
    except (ValueError, IndexError):
        fail('无法从 Docker 代理中确认完整 TCP 发布端口与容器 Endpoint')

    containers, warnings = docker_containers(terminal)
    candidates = []
    for container in containers:
        if address not in container['ips']:
            continue
        bindings = container['ports'].get(str(port)+'/tcp')
        if isinstance(bindings, list) and any(isinstance(binding, dict) and
                normal_ip(binding.get('HostIp')) == host and str(binding.get('HostPort')) == str(host_port)
                for binding in bindings):
            candidates.append(container)
    evidence['mappings'] = [{'containerId': value['id'], 'containerName': value['name'],
        'initPid': value['initPid'], 'published': {'ip': host, 'port': host_port, 'protocol': 'tcp'},
        'target': {'ip': address, 'port': port, 'protocol': 'tcp'}} for value in candidates]
    if warnings:
        fail('Docker 网络元数据不完整，未能唯一确认代理对应的容器')
    if len(candidates) != 1:
        fail('Docker 代理对应多个容器候选，未选择检测目标' if candidates else
             '未找到与 Docker 代理完整发布端口及容器 Endpoint 一致的运行容器', len(candidates) > 1)
    container = candidates[0]
    sample = sample_probe(terminal, 'container-listeners', {'pid': container['initPid'],
        'startTicks': container['startTicks'], 'filter': '( sport = :'+str(port)+' )'})
    current, warnings = docker_containers(terminal, container['id'])
    if warnings or current != [container] or sample.get('networkNamespace') != container['networkNamespace']:
        fail('容器身份、网络空间或发布端口已变化，请重新检测')
    pids = {pid for listener in sample['listeners'] if listener['state'] == 'LISTEN' and
            listener['local']['protocol'] == 'tcp' and listener['local']['port'] == port and
            listener['local']['ip'] in ('*', '0.0.0.0', '::', address) for pid in listener['pids']}
    evidence['processes'] = [{'pid': pid, 'processName': si.app_name(sample['processes'][pid])
        if pid in sample['processes'] else None} for pid in sorted(pids)]
    if not pids:
        fail('容器发布端口 '+str(port)+' 未发现可核验的监听进程；不能据此判断整个容器没有连接')
    if len(pids) != 1:
        fail('容器发布端口存在多个监听 PID，未选择检测目标', True)
    process = sample['processes'].get(next(iter(pids)))
    if not process or process.get('netns') != container['networkNamespace'] or is_docker_proxy(process):
        fail('容器监听 PID 的实时身份未通过核验，未选择检测目标')
    if sample['warnings']:
        fail('容器监听检查不完整，未能唯一确认服务进程')
    evidence.update(resolution='INFERRED', reason='由 Docker 发布端口与实时监听定位服务进程；本次检测该进程的全部 TCP 连接',
        pid=process['pid'], processName=si.app_name(process), startedAt=process['startedAt'],
        startTicks=process['startTicks'], networkNamespace=container['networkNamespace'],
        observedAt=sample.get('observedAt', now()))
    return container, process


def docker_peer_connections(terminal, flows, sample_probe, should_stop=lambda: False):
    """A publication is a candidate; only an unchanged reverse client tuple is an owner."""
    results = {flow_key(flow): {'mappings': [], 'owners': [], 'warnings': []} for flow in flows}
    containers, warnings = docker_containers(terminal)
    groups = {}
    for flow in flows:
        result = results[flow_key(flow)]
        result['warnings'].extend(warnings)
        result['mappings'] = docker_mappings(containers, flow)
        result['ambiguous'] = len({m['containerId'] for m in result['mappings']}) > 1
        for mapping in result['mappings']:
            groups.setdefault(mapping['containerId'], []).append((flow, mapping))
    if warnings:
        # An unreadable container can hide another publication candidate.
        return results
    if len(groups) > 8:
        for result in results.values():
            result['warnings'].append('Docker 端口映射涉及超过 8 个容器，未继续核验')
        return results
    for container in containers:
        pending = groups.get(container['id'])
        if not pending or should_stop():
            continue
        reverse_flows = [{**flow, 'peer': mapping['target']} for flow, mapping in pending]
        listen_filter = '( '+' or '.join('sport = :'+str(port) for port in
                         sorted({m['target']['port'] for _, m in pending}))+' )'
        try:
            sample = sample_probe(terminal, 'container-peer', {
                'pid': container['initPid'], 'startTicks': container['startTicks'],
                'filter': reverse_filter(reverse_flows), 'listenFilter': listen_filter})
            stamp = sample.get('observedAt', now())
            if sample.get('networkNamespace') != container['networkNamespace']:
                raise RuntimeError('container namespace changed')
            current, check_warnings = docker_containers(terminal, container['id'])
            if current != [container] or check_warnings:
                raise RuntimeError('container identity or publication changed')
            for flow, mapping in pending:
                result = results[flow_key(flow)]
                result['warnings'].extend(sample['warnings'])
                mapping['observedAt'] = stamp
                # Listener identity can classify the candidate service, never the connection.
                services = []
                for listener in sample['listeners']:
                    if (listener['local']['port'] != mapping['target']['port'] or
                            listener['local']['ip'] not in ('*', '0.0.0.0', '::', mapping['target']['ip'])):
                        continue
                    for pid in listener['pids']:
                        process = sample['processes'].get(pid, {})
                        if process.get('netns') == container['networkNamespace']:
                            service = middleware(process, mapping['target']['port'])
                            if service['confidence'] == 'EXACT' and not is_docker_proxy(process):
                                services.append(service)
                if services and len({s['name'] for s in services}) == 1:
                    mapping['service'] = {'name': services[0]['name'], 'confidence': 'INFERRED',
                                          'basis': 'Docker 发布端口映射及容器监听进程，尚未确认连接归属'}
                for remote_socket in sample['sockets']:
                    if (remote_socket['state'] == 'LISTEN' or remote_socket['local'] != mapping['target'] or
                            remote_socket['peer'] != flow['local']):
                        continue
                    for pid in remote_socket['pids']:
                        process = sample['processes'].get(pid, {})
                        if process.get('netns') != container['networkNamespace'] or is_docker_proxy(process):
                            continue
                        service = middleware(process, mapping['target']['port'])
                        result['owners'].append({'serverIp': flow['peer']['ip'], 'pid': pid,
                            'startedAt': process['startedAt'], 'processName': si.app_name(process),
                            'application': {'appId': None, 'appName': None, 'confidence': 'UNKNOWN',
                                'basis': '容器服务归属独立于 DevOps 应用，未按宿主机部署端口关联', 'candidates': []},
                            'service': service, 'observedAt': stamp, 'confidence': 'INFERRED',
                            '_observationStart': sample.get('observationStart', stamp),
                            '_observationEnd': sample.get('observationEnd', stamp),
                            'container': {'id': container['id'], 'name': container['name'], 'initPid': container['initPid']},
                            'connection': {'published': mapping['published'], 'local': remote_socket['local'],
                                           'peer': remote_socket['peer'], 'socketId': remote_socket.get('socketId')}})
        except Exception as exc:
            for flow, _ in pending:
                result = results[flow_key(flow)]
                result['warnings'].append('容器网络空间、进程身份或 socket 检查失败，未确认容器连接归属')
                if isinstance(exc, ProbeError):
                    result['warnings'].append(str(exc))
                    result['warnings'].extend(exc.details['diagnostics'])
    return results


class ProbeTerminal(si.Terminal):
    """Stage a verified script once per session; subsequent commands stay short.

    Long interactive input is not a reliable upload protocol. Each write below
    is a complete command below the PTY line limit and waits for its prompt.
    """
    def _upload(self, payload, name):
        directory = getattr(self, '_socket_probe_dir', None)
        if directory is None:
            rc, body = self.run('umask 077; mktemp -d /tmp/envscope-socket-probe.XXXXXXXXXX',
                                timeout=15, wait_for_prompt=True)
            directory = body.strip()
            if rc or not re.fullmatch(r'/tmp/envscope-socket-probe\.[A-Za-z0-9]{6,20}', directory):
                raise ProbeError('PROBE_UPLOAD_FAILED', 'socket_probe', '无法创建私有探针临时目录')
            self._socket_probe_dir = directory
        path = directory+'/'+name
        encoded = base64.b64encode(gzip.compress(payload.encode(), mtime=0)).decode()
        rc, _ = self.run(': > '+shlex.quote(path+'.b64'), timeout=15, wait_for_prompt=True)
        if rc:
            raise ProbeError('PROBE_UPLOAD_FAILED', 'socket_probe', '无法写入私有探针文件')
        for index in range(0, len(encoded), 768):
            command = 'printf %s '+shlex.quote(encoded[index:index+768])+' >> '+shlex.quote(path+'.b64')
            rc, _ = self.run(command, timeout=15, wait_for_prompt=True)
            if rc:
                raise ProbeError('PROBE_UPLOAD_FAILED', 'socket_probe', '探针分段传输失败')
        rc, body = self.run('base64 -d '+shlex.quote(path+'.b64')+' | gzip -dc > '+shlex.quote(path)+
            ' && chmod 600 '+shlex.quote(path)+' && sha256sum '+shlex.quote(path),
            timeout=15, wait_for_prompt=True)
        if rc or not body.split() or body.split()[0] != hashlib.sha256(payload.encode()).hexdigest():
            raise ProbeError('PROBE_UPLOAD_FAILED', 'socket_probe', '探针文件校验失败，未执行不完整内容')
        return path

    def prepare_probe(self, script):
        digest = hashlib.sha256(script.encode()).hexdigest()
        if getattr(self, '_socket_probe_digest', None) != digest:
            self._socket_probe_digest = None
            self._socket_probe_path = self._upload(script, 'probe.sh')
            self._socket_probe_digest = digest
        return self._socket_probe_path

    def probe_command(self, script, args, wrapper=None):
        path = self.prepare_probe(script)
        arguments = []
        assignments = []
        for index, value in enumerate(args):
            if len(value) <= 512:
                arguments.append(shlex.quote(value))
            else:
                cache = getattr(self, '_socket_probe_arguments', {})
                if cache.get(index, (None,))[0] != value:
                    cache.pop(index, None)
                    self._socket_probe_arguments = cache
                    cache[index] = (value, self._upload(value, 'argument-'+str(index)))
                    self._socket_probe_arguments = cache
                variable = '_envscope_probe_arg_'+str(index)
                assignments.append(variable+'="$(cat '+shlex.quote(cache[index][1])+'; printf .)"')
                arguments.append('"${'+variable+'%.}"')
        invocation = 'bash '+shlex.quote(path)+' '+' '.join(arguments)
        # Only the script itself is wrapped: argument assignments must run
        # outside `timeout`, or they would be parsed as its command name.
        return '; '.join(assignments+[wrapper+' '+invocation if wrapper else invocation])

    def close(self):
        directory = getattr(self, '_socket_probe_dir', None)
        try:
            if directory:
                self.run('rm -rf -- '+shlex.quote(directory), timeout=5, wait_for_prompt=True)
        except Exception:
            pass
        finally:
            super().close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def authorized_accounts(asset_id, cookie):
    # Reuse the deployed asset authorization contract, including full pagination.
    path = '/api/v1/perms/users/assets/'+asset_id+'/system-users/'
    opener = build_opener(NoRedirect)
    rows, expected = [], None
    for _ in range(20):
        request = Request(si.BASE+path+'?limit=100&offset='+str(len(rows)),
                          headers={'Cookie': cookie, 'Accept': 'application/json'})
        with opener.open(request, timeout=15) as response:
            page = json.load(response)
        if isinstance(page, list):
            return page
        batch = page.get('results', [])
        if expected is None:
            expected = page.get('count', len(batch))
        if page.get('count', expected) != expected or (not batch and len(rows) < expected):
            raise RuntimeError('授权账号分页不完整')
        rows.extend(batch)
        if len(rows) == expected:
            return rows
    raise RuntimeError('授权账号数量超出检查范围')


def open_terminal(asset, saved, ip_map, terminal_factory=ProbeTerminal, account_reader=authorized_accounts):
    cookie = saved.get('jumpserver', {}).get('value', '')
    accounts = account_reader(asset['id'], cookie)
    accounts = sorted((a for a in accounts if a.get('protocol') == 'ssh'),
                      key=lambda a: a.get('username') != 'folidev')
    for account in accounts:
        terminal = None
        try:
            password = saved.get('folidev', {}).get('value', '')
            terminal = terminal_factory(asset, account, cookie, password)
            terminal.login()
            status, _, _, _, _ = si.resolve_elevation(terminal, asset.get('ip', ''), password,
                saved.get('rootAlt1', {}).get('value', ''), saved.get('rootAlt2', {}).get('value', ''), ip_map)
            return terminal, status
        except Exception:
            if terminal:
                terminal.close()
    raise RuntimeError('无法通过 JumpServer 登录资产，请检查授权、登录态与账号')


def middleware(process=None, port=None):
    command = (process or {}).get('command', '').lower()
    name = (process or {}).get('name', '').lower()
    executable = posixpath.basename((process or {}).get('exe', '')).lower()
    for kind, names, pattern in (
        ('MySQL', ('mysqld', 'mariadbd'), None), ('Redis', ('redis-server',), None),
        ('PostgreSQL', ('postgres',), None),
        ('Elasticsearch', ('elasticsearch',), r'\sorg\.elasticsearch\.bootstrap\.elasticsearch(?:\s|$)'),
        ('Kafka', (), r'\skafka\.kafka(?:\s|$)'),
        ('RabbitMQ', (), r'(?:^|\s)(?:-s\s+)?rabbit\s+boot(?:\s|$)'),
        ('RocketMQ', (), r'\sorg\.apache\.rocketmq\.(?:broker\.brokerstartup|namesrv\.namesrvstartup)(?:\s|$)'),
        ('ActiveMQ', (), r'\sorg\.apache\.activemq\.console\.main(?:\s|$)'),
        ('Nginx', ('nginx',), None)):
        if name in names or executable in names or (pattern and re.search(pattern, command)):
            return {'name': kind, 'confidence': 'EXACT', 'basis': '远端进程命令'}
    if port in PORT_HINTS:
        return {'name': PORT_HINTS[port], 'confidence': 'INFERRED', 'basis': '常用端口，仅为类型候选'}
    return {'name': '应用/未知服务', 'confidence': 'UNKNOWN', 'basis': '尚无服务类型证据'}


def normalized_app_name(value):
    return re.sub(r'[-_.]', '', value.lower())


def process_business_name(process):
    """Use the same Java package / Tomcat instance conventions as the process page."""
    command = process.get('command', '')
    jar = si.jar_name(command)
    if jar:
        return re.sub(r'\.(jar|war)$', '', jar, flags=re.I)
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    base = next((arg.split('=', 1)[1] for key in ('-Dcatalina.base=', '-Dcatalina.home=')
                 for arg in args if arg.startswith(key)), None)
    if not base:
        match = re.search(r'(/[\w./~-]*apache-tomcat[\w.-]*)', command, re.I)
        base = match[1] if match else None
    if base:
        match = re.match(r'^(?:apache-)?tomcat[_-](.+)$', posixpath.basename(base.rstrip('/')), re.I)
        if match:
            return match[1].strip()
    return None


def match_application(process, host_ips, listeners, apps, environment):
    """Name hints stay inferred; conflicting declarations/ports remain candidates."""
    if environment not in ENVIRONMENTS:
        return {'appId': None, 'appName': None, 'confidence': 'UNKNOWN',
                'basis': '未能唯一识别部署环境，未跨环境关联应用', 'candidates': []}
    command = process.get('command', '')
    declared = re.findall(r'(?:-Dappid=|-Dspring\.application\.name=|--spring\.application\.name=)([^\s]+)', command)
    declared = {x.strip('\"\'') for x in declared}
    business_name = process_business_name(process)
    business_name = normalized_app_name(business_name) if business_name else None
    ports = {s['local']['port'] for s in listeners if process['pid'] in s['pids']}
    candidates, preferred, independently_supported = {}, set(), set()
    for app in apps:
        deployments = [d for d in app.get('envs', {}).get(environment, []) if ips(d.get('ip')) & host_ips]
        if not deployments:
            continue
        name, app_id = str(app.get('name', '')), str(app.get('id', ''))
        explicit = name in declared or app_id in declared
        normalized = normalized_app_name(name)
        exact_business = bool(normalized and normalized == business_name)
        path = bool(name and re.search(r'(?:/|tomcat[_/-])'+re.escape(name)+r'(?:[/_.\s]|$)', command, re.I))
        name_match = explicit or exact_business or path
        # Deployment-level port is authoritative; app default is only a fallback hint.
        port_match = any(str(d.get('port') or app.get('port') or '').isdigit() and
                         int(d.get('port') or app.get('port')) in ports for d in deployments)
        if name_match or port_match:
            candidates[app_id] = {'appId': app_id, 'appName': name,
                'confidence': 'EXACT' if explicit and port_match else 'INFERRED',
                'basis': '进程声明与部署端口' if explicit and port_match else '进程名称/目录或部署端口匹配'}
            if exact_business:
                preferred.add(app_id)
            if explicit or port_match:
                independently_supported.add(app_id)
    # An exact business name outranks substring/path-only hints. Keep conflicting
    # declarations or shared listening ports as candidates rather than choosing one.
    if preferred:
        candidates = {key: value for key, value in candidates.items()
                      if key in preferred or key in independently_supported}
    values = sorted(candidates.values(), key=lambda a: a['appId'])
    if len(values) == 1:
        return {**values[0], 'candidates': values}
    return {'appId': None, 'appName': None, 'confidence': 'AMBIGUOUS' if values else 'UNKNOWN',
            'basis': '存在多个应用候选' if values else '未找到该环境中的应用部署匹配', 'candidates': values}


def infer_environment(process, host_ips, listeners, apps):
    matches = [env for env in ENVIRONMENTS
               if match_application(process, host_ips, listeners, apps, env)['confidence'] != 'UNKNOWN']
    return matches[0] if len(matches) == 1 else None


def collect_config(terminal, process):
    if zookeeper_config.is_server(process):
        return {'status': 'not_collected', 'roots': [], 'endpoints': [],
                'warnings': ['ZooKeeper 配置的上下游解析尚不支持；配置文件可在配置文件页查看']}
    roots = config_roots(process)
    warnings = ['配置为声明信息，未确认生效 profile；占位符和 jar 内配置未展开']
    if not roots:
        return {'status': 'not_collected', 'roots': [], 'endpoints': [], 'warnings': warnings+['无法确定应用配置目录']}
    script = config_ignore.probe_script('application_configs.sh')
    config_rc, body = terminal.run('timeout 20 bash -c '+shlex.quote(script)+' _ '+shlex.join(roots), timeout=25)
    bodies = [body]
    server = next((decode(line.split('\t')[2]) for line in body.splitlines()
                   if line.startswith('CONFIG_FILE\t') and len(line.split('\t')) == 3
                   and posixpath.basename(decode(line.split('\t')[1])) == 'server.xml'
                   and posixpath.dirname(decode(line.split('\t')[1])) in roots), None)
    extra = [root for root in tomcat_docbase_roots(server, process) if root not in roots] if server else []
    if extra:
        roots = extra + roots
        rc, extra_body = terminal.run('timeout 20 bash -c '+shlex.quote(script)+' _ '+shlex.join(roots), timeout=25)
        config_rc = config_rc or rc
        bodies = [extra_body]
    endpoints = []
    nacos_seen = set()
    nacos_attempted = set()
    for line in '\n'.join(bodies).splitlines():
        fields = line.split('\t')
        if fields[0] == 'CONFIG_FILE' and len(fields) == 3:
            path = decode(fields[1])
            # Ignore unrelated XML files under Tomcat webapps; WEB-INF is in scope.
            if '/webapps/' in path and '/WEB-INF/' not in path and not posixpath.basename(path).startswith('application'):
                continue
            content = decode(fields[2])
            endpoints.extend(parse_config(content, path))
            if re.fullmatch(r'(?:application|bootstrap)(?:-[\w.-]+)?\.ya?ml', posixpath.basename(path), re.I):
                configs, unresolved = nacos.declarations(content, process)
                warnings.extend(unresolved)
                if configs:
                    for declaration in configs:
                        requested = tuple(declaration[k] for k in ('address', 'namespace', 'group', 'dataId'))
                        if requested in nacos_attempted: continue
                        if len(nacos_attempted) >= 20:
                            warnings.append('Nacos 配置超过 20 项，未继续读取')
                            break
                        nacos_attempted.add(requested)
                        try:
                            inferred_group = not declaration['group']
                            declaration = nacos.resolve_group(declaration)
                            identity = tuple(declaration[k] for k in ('address', 'namespace', 'group', 'dataId'))
                            if identity not in nacos_seen and len(nacos_seen) < 20:
                                nacos_seen.add(identity)
                                remote = nacos.fetch(declaration).decode('utf-8')
                                source = 'Nacos / '+declaration['namespace']+' / '+declaration['group']+' / '+declaration['dataId']
                                endpoints.extend(parse_config(remote, source))
                                warnings.append('Nacos 配置来自 '+path+' 的声明，未确认该 profile 生效')
                                if inferred_group:
                                    warnings.append('Nacos Group 在可查询范围内唯一匹配；未确认应用实际使用该 Group：'+declaration['dataId'])
                            elif identity not in nacos_seen:
                                warnings.append('Nacos 配置超过 20 项，未继续读取')
                        except ValueError as error:
                            warnings.append('Nacos 配置读取失败：'+path+' / '+declaration['dataId']+'；'+str(error))
        elif fields[0] == 'CONFIG_WARNING':
            warnings.append(fields[1])
    if config_rc or any('CONFIG_DONE' not in item for item in bodies):
        warnings.append('配置读取未完成')
    # Resolve names on the application host, not using the container's DNS view.
    names = sorted({x['host'] for x in endpoints if not normal_ip(x['host'])})
    if len(names) > 8:
        warnings.append('最多解析 8 个配置域名，其余保留声明')
    for host in names[:8]:
        rc, output = terminal.run('timeout 3 getent ahosts '+shlex.quote(host), timeout=5)
        resolved = sorted({ip for line in output.splitlines() if line.split() and (ip := normal_ip(line.split()[0]))}) if rc == 0 else []
        if not resolved:
            warnings.append('目标机未能解析配置域名：'+host)
        for entry in endpoints:
            if entry['host'] == host:
                entry['resolvedIps'] = resolved
    return {'status': 'partial' if config_rc or len(warnings) > 1 else 'complete',
            'roots': roots, 'endpoints': endpoints[:200], 'warnings': warnings}


def config_matches(flow, config):
    peer = flow['peer']
    return [entry for entry in config.get('endpoints', []) if entry['port'] == peer['port'] and
            (entry['host'] == peer['ip'] or peer['ip'] in entry.get('resolvedIps', []))]


def flow_key(socket):
    return (socket['local']['ip'], socket['local']['port'], socket['peer']['ip'], socket['peer']['port'],
            socket.get('socketId'))


def update_flow(flows, socket, listeners, stamp):
    key = flow_key(socket)
    is_inbound = any(s['local']['port'] == socket['local']['port'] and set(s['pids']) & set(socket['pids']) for s in listeners)
    if key not in flows:
        flows[key] = {**socket, 'direction': 'upstream' if is_inbound else 'downstream',
            'directionConfidence': 'INFERRED', 'directionBasis': '根据本进程监听端口推断连接方向；工具未记录连接建立过程',
            'firstSeenAt': stamp, 'lastSeenAt': stamp, 'samples': 0, 'sentDelta': None, 'receivedDelta': None,
            'networkActivity': 'UNKNOWN', 'remoteProcesses': [], 'warnings': [],
            '_firstSent': socket['bytesSent'], '_firstReceived': socket['bytesReceived']}
        if socket['state'] == 'SYN-SENT':
            flows[key].update(direction='downstream', directionConfidence='EXACT',
                              directionBasis='观察到 SYN-SENT，选中进程正在主动发起连接')
    flow = flows[key]
    flow['state'] = socket['state']
    # Counters and state refer to the same latest observation.
    flow['bytesSent'], flow['bytesReceived'] = socket['bytesSent'], socket['bytesReceived']
    if is_inbound and flow['directionConfidence'] != 'EXACT':
        flow['direction'] = 'upstream'
    flow['samples'] += 1
    flow['lastSeenAt'] = stamp
    for counter, first, delta in (('bytesSent', '_firstSent', 'sentDelta'), ('bytesReceived', '_firstReceived', 'receivedDelta')):
        if flow['samples'] >= 2 and flow.get('socketId') and flow[first] is not None and socket[counter] is not None:
            diff = socket[counter]-flow[first]
            flow[delta] = diff if diff >= 0 else None
            if diff < 0:
                flow['warnings'].append('socket 计数发生回退，流量无法确认')
    if any((flow[k] or 0) > 0 for k in ('sentDelta', 'receivedDelta')):
        flow['networkActivity'] = 'DATA_OBSERVED'
    elif flow['sentDelta'] == 0 and flow['receivedDelta'] == 0:
        flow['networkActivity'] = 'NO_DATA_OBSERVED'
    return flow


def reverse_filter(flows):
    conditions = []
    for flow in flows:
        for local in [flow['local']] + flow.get('_publishedEndpoints', []):
            peer = flow['peer']
            # Validate all addresses before interpolating the ss expression.
            for e in (local, peer):
                if not normal_ip(e['ip']):
                    raise ValueError('连接地址不是有效 IP')
            source = '['+peer['ip']+']' if ':' in peer['ip'] else peer['ip']
            destination = '['+local['ip']+']' if ':' in local['ip'] else local['ip']
            # Old iproute2 (including ss170501) accepts bare host predicates but
            # rejects `src = IP` / `dst = IP` with a filter parser error (exit 255).
            conditions.append('( src '+source+' and sport = :'+str(peer['port'])+
                              ' and dst '+destination+' and dport = :'+str(local['port'])+' )')
    return '( '+' or '.join(dict.fromkeys(conditions))+' )'

def same_connection(flow, remote):
    return flow['local'] == remote['peer'] and flow['peer'] == remote['local']


def peer_sampling_filter(flows):
    """Pre-sample the known service, retaining client ports in the evidence.

    Discovery may omit an ephemeral port; attribution never does. This lets a
    peer batch capture a short connection before its target batch is returned.
    """
    conditions = []
    for flow in flows:
        for local in [flow['local']] + flow.get('_publishedEndpoints', []):
            peer = flow['peer']
            if not normal_ip(local['ip']) or not normal_ip(peer['ip']):
                raise ValueError('连接地址不是有效 IP')
            source = '['+peer['ip']+']' if ':' in peer['ip'] else peer['ip']
            destination = '['+local['ip']+']' if ':' in local['ip'] else local['ip']
            service = ('sport = :'+str(peer['port']) if flow['direction'] == 'downstream' else
                       'dport = :'+str(local['port']))
            conditions.append('( src '+source+' and dst '+destination+' and '+service+' )')
    return '( '+' or '.join(dict.fromkeys(conditions))+' )'

def observation_bounds(sample, fallback):
    return sample.get('observationStart', fallback), sample.get('observationEnd', fallback)


class PeerObservationBuffer:
    """Bound discovery memory and index by the complete reversed TCP tuple."""
    def __init__(self, limit=2000):
        self.limit, self.records, self.by_tuple = limit, deque(), {}
        self.sequence = 0

    @staticmethod
    def key(local, peer):
        return (local['ip'], local['port'], peer['ip'], peer['port'], local['protocol'])

    def extend(self, observations):
        for observation in observations:
            socket = observation[1]
            key = self.key(socket['local'], socket['peer'])
            if len(self.records) >= self.limit:
                old_key, _ = self.records.popleft()
                self.by_tuple[old_key].popleft()
                if not self.by_tuple[old_key]:
                    del self.by_tuple[old_key]
            self.sequence += 1
            record = (self.sequence, observation)
            self.records.append((key, record))
            self.by_tuple.setdefault(key, deque()).append(record)

    def for_flow(self, flow):
        return (observation for _, observation in self.by_tuple.get(self.key(flow['peer'], flow['local']), ()))

    def new_for_flow(self, flow, apps, environment):
        # Every target frame used to replay the entire peer history under the
        # shared lock. Revisit history only if the attribution context changes;
        # final confidence is still evaluated against the latest target bounds.
        context = (id(self), id(apps), environment)
        previous, sequence = flow.get('_peerCursor', (None, 0))
        if previous != context:
            sequence = 0
        flow['_peerCursor'] = (context, self.sequence)
        return (observation for local in [flow['local']] + flow.get('_publishedEndpoints', [])
                for number, observation in self.by_tuple.get(self.key(flow['peer'], local), ()) if number > sequence)


def possible_observation(flow, first, last):
    return (last >= flow.get('_possibleStart', flow['firstSeenAt']) and
            (not flow.get('_possibleEnd') or first <= flow['_possibleEnd']))


def confirmed_observation(flow, first, last):
    return (flow['samples'] >= 2 and flow.get('socketId') and not flow.get('_interrupted') and
            flow.get('_firstObservationEnd', flow['firstSeenAt']) <= first <= last <=
            flow.get('_lastObservationStart', flow['lastSeenAt']))


def attach_peer_observations(flow, observations, peer_asset, apps, environment):
    if isinstance(observations, PeerObservationBuffer):
        observations = observations.new_for_flow(flow, apps, environment)
    for sample, socket, stamp in observations:
        published = socket['peer'] in flow.get('_publishedEndpoints', []) and socket['local'] == flow['peer']
        if not same_connection(flow, socket) and not published:
            continue
        first, last = observation_bounds(sample, stamp)
        if not possible_observation(flow, first, last):
            continue
        if published:
            endpoints = flow.setdefault('_publishedMatches', set())
            endpoints.add((socket['peer']['ip'], socket['peer']['port']))
            if len(endpoints) > 1:
                flow['_dockerAmbiguous'] = True
        flow['_peerSocketSeen'] = True
        owners = []
        for pid in socket['pids']:
            process = sample['processes'].get(pid)
            if not process:
                continue
            if is_docker_proxy(process):
                proxies = flow.setdefault('proxyProcesses', [])
                if not any(p['pid'] == pid and p['startedAt'] == process['startedAt'] for p in proxies):
                    proxies.append({'pid': pid, 'startedAt': process['startedAt'],
                                    'processName': process['name'], 'observedAt': stamp})
                continue
            peer_ips = ips(peer_asset.get('ip')) | {flow['peer']['ip']}
            # A frame is immutable evidence. All sockets owned by this PID in
            # that frame share its host, process and listener evidence; compute
            # application matching once, without sharing connection ownership.
            cache = sample.setdefault('_applicationMatches', {})
            cache_key = (id(apps), environment, frozenset(peer_ips), pid)
            if cache_key not in cache:
                peer_environment = environment
                if peer_environment is None:
                    peer_environment = infer_environment(process, peer_ips, sample['listeners'], apps)
                cache[cache_key] = match_application(process, peer_ips, sample['listeners'], apps, peer_environment)
            app = cache[cache_key]
            owners.append({'serverIp': flow['peer']['ip'], 'pid': pid,
                'startedAt': process['startedAt'], 'processName': si.app_name(process),
                'application': app, 'service': middleware(process, flow['peer']['port']),
                'observedAt': stamp, 'confidence': 'INFERRED',
                '_observationStart': first, '_observationEnd': last,
                '_socketId': socket.get('socketId')})
            if published:
                owners[-1]['dockerTargetConnection'] = {'published': socket['peer'], 'client': socket['local'],
                    'container': flow['local'], 'socketId': socket.get('socketId')}
        for owner in owners:
            existing = next((p for p in flow['remoteProcesses'] if p['pid'] == owner['pid'] and
                p['startedAt'] == owner['startedAt'] and not p.get('container')), None)
            # Retain a bracketed observation instead of always keeping the first
            # hit (which may precede the target's first batch).
            if existing is None:
                owner['_observations'] = [dict(owner)]
                flow['remoteProcesses'].append(owner)
            else:
                evidence = existing.setdefault('_observations', [dict(existing)])
                if not evidence or evidence[-1]['observedAt'] != owner['observedAt']:
                    evidence.append(owner)
                    evidence[:] = evidence[:2]+evidence[-8:] if len(evidence) > 10 else evidence
                if not confirmed_observation(flow, existing['_observationStart'], existing['_observationEnd']):
                    existing.update(owner)


def sampling_coverage(stamps):
    times = [datetime.fromisoformat(stamp).timestamp() for stamp in stamps]
    gaps = [later-earlier for earlier, later in zip(times, times[1:]) if later >= earlier]
    return {'samples': len(times), 'firstObservedAt': stamps[0] if stamps else None,
            'lastObservedAt': stamps[-1] if stamps else None,
            'averageIntervalSeconds': round(sum(gaps)/len(gaps), 3) if gaps else None,
            'maxIntervalSeconds': round(max(gaps), 3) if gaps else None}


def listener_processes(flow, sample, peer_asset, apps, environment):
    """Live service candidates, kept separate from reverse-connection owners."""
    candidates = {}
    for listener in sample['listeners']:
        local = listener['local']
        if (listener.get('state') != 'LISTEN' or local['protocol'] != flow['peer']['protocol'] or
                local['port'] != flow['peer']['port'] or
                (local['ip'] == '0.0.0.0' and ipaddress.ip_address(flow['peer']['ip']).version != 4) or
                local['ip'] not in ('*', '0.0.0.0', '::', flow['peer']['ip'])):
            continue
        for pid in listener['pids']:
            process = sample['processes'].get(pid)
            if not process or is_docker_proxy(process):
                continue
            peer_ips = ips(peer_asset.get('ip')) | {flow['peer']['ip']}
            peer_environment = environment if environment is not None else infer_environment(
                process, peer_ips, sample['listeners'], apps)
            application = match_application(process, peer_ips, sample['listeners'], apps, peer_environment)
            candidates[pid, process['startedAt']] = {
                'serverIp': flow['peer']['ip'], 'pid': pid, 'startedAt': process['startedAt'],
                'processName': si.app_name(process), 'application': application,
                'service': middleware(process, local['port']), 'confidence': 'INFERRED',
                'observedAt': sample.get('observedAt', now()), 'endpoint': local,
                'basis': '实时匹配对端 TCP 监听 Endpoint；未确认该进程持有本条连接'}
    return list(candidates.values())


def inspect_peer_listeners(flows, asset, by_ip, apps, environment, saved, ip_map,
                           terminal_opener, sample_probe, cancel, progress=None):
    """One bounded query per unresolved downstream host; all hosts are visited."""
    pending = {}
    for flow in flows:
        if flow['direction'] != 'downstream' or flow['remoteProcesses']:
            continue
        peer_ip = flow['peer']['ip']
        candidates = {asset['id']: asset} if ipaddress.ip_address(peer_ip).is_loopback else by_ip.get(peer_ip, {})
        if len(candidates) == 1:
            peer_asset = next(iter(candidates.values()))
            pending.setdefault(peer_asset['id'], (peer_asset, []))[1].append(flow)

    def check(item):
        peer_asset, entries = item
        terminal, stamps, warnings = None, [], []
        status = 'failed'
        def notify(message, stage):
            if progress:
                progress(message, scope='peer', assetId=peer_asset['id'],
                         serverIp=peer_asset.get('ip'), stage=stage)
        try:
            if cancel.is_set():
                return None
            notify('正在登录并核验下游端口监听进程', 'listeners')
            terminal, _sudo = terminal_opener(peer_asset, saved, ip_map)
            if cancel.is_set():
                return None
            ports = sorted({entry['peer']['port'] for entry in entries})
            selector = '( '+' or '.join('sport = :'+str(port) for port in ports)+' )'
            notify('正在等待下游端口监听探针返回', 'listeners')
            sample = sample_probe(terminal, 'listeners', selector)
            warnings.extend(sample['warnings'])
            for frame in observation_frames(sample):
                stamps.append(frame.get('observedAt', now()))
                for flow in entries:
                    flow['peerListeningProcesses'] = listener_processes(flow, frame, peer_asset, apps, environment)
            status = 'partial' if warnings else 'complete'
        except ProbeError as exc:
            warnings.extend([str(exc), *exc.details['diagnostics']])
        except Exception:
            warnings.append('对端监听进程核验失败，未使用快照推定 PID')
        finally:
            if terminal:
                terminal.close()
            notify('监听核验已取消' if cancel.is_set() else
                   ('下游端口监听核验完成' if status != 'failed' else '下游端口监听核验失败'),
                   'cancelled' if cancel.is_set() else status)
        for flow in entries:
            flow['warnings'].extend(warnings)
        return {'serverIp': peer_asset.get('ip'), 'status': status, **sampling_coverage(stamps)}

    with ThreadPoolExecutor(max_workers=LISTENER_CHECK_WORKERS, thread_name_prefix='app-check-listen') as pool:
        return [result for result in pool.map(check, pending.values()) if result is not None]


def inspect_application(asset, selected, apps, assets, saved, ip_map, environment, duration=30,
                        cancel=None, progress=None, terminal_opener=open_terminal, sample_probe=sampling_probe,
                        config_collector=collect_config, interval=0):
    cancel = cancel or threading.Event()
    done = threading.Event()
    lock = threading.Lock()
    flows, requests, peer_results, warnings = {}, {}, {}, []
    peer_observations, target_stamps = {}, []
    terminal = None
    report = {'schemaVersion': 1, 'startedAt': now(), 'environment': environment,
              'target': {'assetId': asset['id'], 'serverIp': asset.get('ip'), 'pid': selected['pid']},
              'upstream': [], 'downstream': [], 'warnings': warnings,
              'coverage': {'method': 'ss-batched-polling', 'requestedSeconds': duration,
                           'intervalSeconds': interval, 'batchPauseSeconds': interval,
                           'requestedSamplePeriodSeconds': SAMPLE_PERIOD,
                           'samplesPerBatch': BATCH_SAMPLES,
                           'targetOwnershipMethod': 'proc-fd-inode',
                           'limitations': ['短连接可能在采样间隙消失', '字节增长不等于业务请求',
                                          '代理/NAT 前的调用方未还原', '仅覆盖 TCP，未覆盖 UDP/Unix Socket',
                                          '未对连接发起握手进行取证']}}
    # A peer IP must resolve to exactly one managed asset; otherwise keep the gap.
    by_ip = {}
    for peer_asset in assets:
        for ip in ips(peer_asset.get('ip')):
            by_ip.setdefault(ip, {})[peer_asset['id']] = peer_asset

    def check_peer(peer_asset):
        remote = None
        result = {'status': 'partial', 'processes': {}, 'sockets': {}, 'listeners': [], 'warnings': []}
        stamps = []
        first_observation_start = last_observation_end = None
        def notify(message, stage):
            if progress:
                progress(message, scope='peer', assetId=peer_asset['id'],
                         serverIp=peer_asset.get('ip'), stage=stage, samples=len(stamps))
        try:
            if cancel.is_set():
                return
            notify('正在通过 JumpServer 登录对端服务器', 'login')
            remote, sudo = terminal_opener(peer_asset, saved, ip_map)
            result['sudoStatus'] = sudo
            sampled = False
            # Even a peer discovered in the last target batch gets a query.
            # Late evidence still has to pass the original lifetime checks.
            while not cancel.is_set() and (not sampled or not done.is_set()):
                with lock:
                    pending = list(requests[peer_asset['id']].values())[:100]
                # Same-host client IP cannot distinguish this JVM from every
                # other local application; retain the precise query scope.
                selector = reverse_filter(pending) if peer_asset['id'] == asset['id'] else peer_sampling_filter(pending)
                notify('正在等待对端连接采样返回', 'sampling')
                batch = sample_probe(remote, 'peer', selector)
                sampled = True
                result['warnings'] = list(dict.fromkeys(result['warnings']+batch['warnings']))
                with lock:
                    for sample in observation_frames(batch):
                        stamp = sample.get('observedAt', now())
                        stamps.append(stamp)
                        first, last = observation_bounds(sample, stamp)
                        first_observation_start = min(first_observation_start or first, first)
                        last_observation_end = max(last_observation_end or last, last)
                        # Bound the discovery buffer independently of the 100
                        # target-connection limit. Full tuples are kept intact.
                        if len(sample['sockets']) > 400:
                            result['warnings'].append('对端单次采样超过 400 条连接，已限制发现缓冲区')
                        peer_observations[peer_asset['id']].extend(
                            (sample, socket, stamp) for socket in sample['sockets'][:400])
                    pending = list(requests[peer_asset['id']].values())[:100]
                    for flow in pending:
                        attach_peer_observations(flow, peer_observations[peer_asset['id']],
                                                 peer_asset, apps, environment)
                notify(f'已收到对端有效采样 {len(stamps)} 帧', 'sampling')
                docker_flows = [flow for flow in pending if flow['direction'] == 'downstream' and
                                not flow.get('_possibleEnd') and
                                (not flow['remoteProcesses'] or any(p.get('container') for p in flow['remoteProcesses']))]
                if docker_flows and not done.is_set() and not cancel.is_set():
                    notify('正在核验 Docker 发布端口与容器连接', 'docker')
                    checked = docker_peer_connections(remote, docker_flows, sample_probe,
                        should_stop=lambda: done.is_set() or cancel.is_set())
                    with lock:
                        for flow in docker_flows:
                            docker_result = checked[flow_key(flow)]
                            flow['dockerMappings'] = docker_result['mappings']
                            flow['_dockerAmbiguous'] = flow.get('_dockerAmbiguous', False) or docker_result.get('ambiguous', False)
                            flow['warnings'].extend(docker_result['warnings'])
                            existing = {(p['pid'], p['startedAt'], p.get('container', {}).get('id')): p
                                        for p in flow['remoteProcesses']}
                            for owner in docker_result['owners']:
                                existing.setdefault((owner['pid'], owner['startedAt'], owner['container']['id']), owner)
                            flow['remoteProcesses'] = list(existing.values())
                if done.wait(interval):
                    break
            result['status'] = 'partial' if result['warnings'] else 'complete'
        except ProbeError as exc:
            result['warnings'].append(str(exc))
            result['warnings'].extend(exc.details['diagnostics'])
        except Exception:
            result['warnings'].append('对端登录或 socket 检查失败；未将快照进程视为实时连接归属')
        finally:
            if remote:
                remote.close()
            with lock:
                result['coverage'] = {'serverIp': peer_asset.get('ip'), **sampling_coverage(stamps)}
                result['coverage'].update(observationStart=first_observation_start,
                                          observationEnd=last_observation_end)
                peer_results[peer_asset['id']] = result
            notify(f'对端采样结束，共 {len(stamps)} 帧' if not cancel.is_set() else '对端采样已取消',
                   'cancelled' if cancel.is_set() else result['status'])

    # Each discovered asset needs its own sampling loop for the observation
    # window. A small fixed pool would queue peers until that window ends.
    # Threads are created lazily, only for assets reached by target connections.
    peer_workers = len({peer_asset['id'] for peer_asset in assets} | {asset['id']})
    pool = ThreadPoolExecutor(max_workers=peer_workers, thread_name_prefix='app-check-peer')
    stage = 'target_login'
    target_process = None
    sampling_selected = selected
    target_container = None
    listeners = []
    started = None
    last_observation_end = None
    def notify(message, stage, **stats):
        if progress:
            progress(message, stage=stage, serverIp=asset.get('ip'), **stats)
    try:
        notify('正在通过 JumpServer 登录目标服务器', 'login')
        terminal, sudo = terminal_opener(asset, saved, ip_map)
        report['target']['sudoStatus'] = sudo
        if isinstance(terminal, ProbeTerminal):
            stage = 'target_probe'
            notify('正在准备并校验只读连接探针', 'prepare')
            terminal.prepare_probe((PROBES/'application_sockets.sh').read_text())
        if is_docker_proxy(selected) and not cancel.is_set():
            stage = 'docker_target'
            notify('正在核验 Docker 代理与容器监听进程', 'docker_target')
            entry = sample_probe(terminal, 'target', selected['pid'])
            proxy, proxy_ticks = None, None
            for frame in observation_frames(entry):
                proxy = frame['processes'].get(selected['pid'])
                if (not proxy or not is_docker_proxy(proxy) or
                        selected.get('startedAt') and proxy['startedAt'] != selected['startedAt']):
                    raise RuntimeError('所选代理进程已退出或 PID 已复用，请重新采集服务器')
                if proxy_ticks is None:
                    proxy_ticks = proxy['startTicks']
                if proxy['startTicks'] != proxy_ticks:
                    raise RuntimeError('检查期间代理进程已重启，请重新采集服务器')
            if proxy is None:
                raise ProbeError('DOCKER_TARGET_UNRESOLVED', 'docker_target', '未取得代理进程实时身份')
            report['target'].update(processName=si.app_name(proxy), startedAt=proxy['startedAt'],
                                    startTicks=proxy['startTicks'])
            evidence = {'resolution': 'UNKNOWN', 'reason': '尚未确认容器服务进程', 'mappings': [], 'processes': []}
            report['target']['dockerTarget'] = evidence
            target_container, sampling_selected = resolve_docker_target(terminal, proxy, sample_probe, evidence)
            report['coverage']['limitations'].append('连接与字节计数来自容器服务进程；发布映射与客户端连接匹配仅为推断，未核验 NAT 转换')
            notify('已定位容器 '+target_container['name']+'，正在检测服务 PID '+str(sampling_selected['pid']), 'docker_target')
        started = time.monotonic()
        notify('目标探针已就绪，开始观察 TCP 连接', 'sampling', samplingStartedAt=now())
        initial_ticks = sampling_selected.get('startTicks') if target_container else None
        while not cancel.is_set():
            if target_stamps and time.monotonic()-started >= duration:
                break
            stage = 'target_probe'
            notify('正在等待目标连接采样返回', 'sampling')
            if target_container:
                batch = sample_probe(terminal, 'container-target', {
                    'pid': sampling_selected['pid'], 'startTicks': initial_ticks})
                current, check_warnings = docker_containers(terminal, target_container['id'])
                if check_warnings or current != [target_container]:
                    raise ProbeError('DOCKER_TARGET_CHANGED', 'target_probe',
                                     '检测期间容器身份或发布端口已变化，已停止并保留先前观察')
            else:
                batch = sample_probe(terminal, 'target', sampling_selected['pid'])
            warnings[:] = list(dict.fromkeys(warnings+batch['warnings']))
            for sample in observation_frames(batch):
                process = sample['processes'].get(sampling_selected['pid'])
                if not process or (sampling_selected.get('startedAt') and process['startedAt'] != sampling_selected['startedAt']):
                    raise RuntimeError('所选进程已退出或 PID 已复用，请重新采集服务器')
                if target_container and (sample.get('networkNamespace') != target_container['networkNamespace'] or
                                         process.get('netns') != target_container['networkNamespace']):
                    raise ProbeError('DOCKER_TARGET_CHANGED', 'target_probe', '容器服务进程的网络空间已变化，已停止检测')
                if initial_ticks is not None and initial_ticks != process['startTicks']:
                    raise RuntimeError('检查期间进程已重启，结果仅保留已观察证据')
                initial_ticks = process['startTicks']
                target_process = process
                listeners = sample['listeners']
                if environment is None and not target_container:
                    environment = infer_environment(process, ips(asset.get('ip')), listeners, apps)
                    report['environment'] = environment
                stamp = sample.get('observedAt', now())
                first, last = observation_bounds(sample, stamp)
                last_observation_end = last
                with lock:
                    present = {flow_key(s) for s in sample['sockets'] if sampling_selected['pid'] in s['pids']}
                    for key, flow in flows.items():
                        if key not in present:
                            flow.setdefault('_possibleEnd', last)
                    for socket in sample['sockets']:
                        if sampling_selected['pid'] not in socket['pids'] or socket['state'] == 'LISTEN':
                            continue
                        if not normal_ip(socket['local']['ip']) or not normal_ip(socket['peer']['ip']) or not socket['peer']['port']:
                            continue
                        if flow_key(socket) not in flows and len(flows) >= 100:
                            if '连接数量超过 100，已限制本次检查范围' not in warnings:
                                warnings.append('连接数量超过 100，已限制本次检查范围')
                            continue
                        flow = update_flow(flows, socket, listeners, stamp)
                        if target_container and flow['direction'] == 'upstream':
                            mapping = report['target']['dockerTarget']['mappings'][0]
                            if flow['local'] == mapping['target']:
                                binding = mapping['published']
                                addresses = (ips(asset.get('ip')) if binding['ip'] in ('0.0.0.0', '::')
                                             else {binding['ip']})
                                flow['_publishedEndpoints'] = [dict(binding, ip=ip) for ip in sorted(addresses)
                                    if ipaddress.ip_address(ip).version == ipaddress.ip_address(binding['ip']).version]
                        if flow.get('_possibleEnd'):
                            flow['_interrupted'] = True
                            flow['sentDelta'], flow['receivedDelta'], flow['networkActivity'] = None, None, 'UNKNOWN'
                            warning = 'socket 标识在缺失后再次出现，连续性未确认，流量增量保留未知'
                            if warning not in flow['warnings']:
                                flow['warnings'].append(warning)
                        flow.setdefault('_possibleStart', target_stamps[-1] if target_stamps else first)
                        flow.setdefault('_firstObservationEnd', last)
                        flow['_lastObservationStart'] = first
                        peer_ip = socket['peer']['ip']
                        candidates = {asset['id']: asset} if ipaddress.ip_address(peer_ip).is_loopback else by_ip.get(peer_ip, {})
                        if len(candidates) != 1:
                            flow['resolution'] = 'AMBIGUOUS' if candidates else 'UNKNOWN'
                            flow['reason'] = '来源 IP 对应多个资产' if candidates else '对端 IP 未匹配到 JumpServer 资产；可能为托管服务/代理/NAT'
                            continue
                        peer_asset = next(iter(candidates.values()))
                        peer_id = peer_asset['id']
                        if peer_id not in requests:
                            requests[peer_id] = {}
                            peer_observations[peer_id] = PeerObservationBuffer()
                            requests[peer_id][flow_key(socket)] = flow
                            pool.submit(check_peer, peer_asset)
                        else:
                            requests[peer_id][flow_key(socket)] = flow
                        attach_peer_observations(flow, peer_observations[peer_id], peer_asset, apps, environment)
                        flow['_peerAssetId'] = peer_id
                    target_stamps.append(stamp)
            elapsed = time.monotonic()-started
            with lock:
                counts = {direction: sum(flow['direction'] == direction for flow in flows.values())
                          for direction in ('upstream', 'downstream')}
            notify(f'已收到目标有效采样 {len(target_stamps)} 帧', 'sampling',
                   samples=len(target_stamps), elapsedSeconds=round(elapsed, 1),
                   lastObservedAt=target_stamps[-1] if target_stamps else None,
                   environment=environment, **counts)
            if elapsed >= duration:
                break
            cancel.wait(min(interval, max(0, duration-elapsed)))
        report['coverage']['samplingElapsedSeconds'] = round(time.monotonic()-started, 1)
        done.set()
        notify('目标连接采样结束', 'sampling_complete', samplingFinishedAt=now())
        if target_process and target_container:
            report['configuration'] = {'status': 'not_collected', 'endpoints': [],
                'warnings': ['容器服务配置未核对；未将容器内目录作为宿主机目录读取']}
        elif target_process:
            if not cancel.is_set():
                notify('正在核对应用配置中的数据库、Redis、ES 与 MQ Endpoint', 'configuration')
                try:
                    stage = 'configuration'
                    report['configuration'] = config_collector(terminal, target_process)
                except Exception:
                    report['configuration'] = {'status': 'failed', 'endpoints': [], 'warnings': ['配置读取失败']}
        report['status'] = 'cancelled' if cancel.is_set() else 'complete'
    except Exception as exc:
        # Only locally generated messages are allowed into persisted output.
        report['status'] = 'partial' if flows else 'failed'
        if isinstance(exc, ProbeError):
            report['error'] = exc.details
            warnings.append(str(exc))
            warnings.extend(exc.details['diagnostics'])
        else:
            message = str(exc) if type(exc) is RuntimeError else '目标检查失败：'+type(exc).__name__
            report['error'] = {'code': 'TARGET_CHECK_FAILED', 'stage': stage, 'message': message,
                               'exitCode': None, 'diagnostics': []}
            warnings.append(message)
    finally:
        done.set()
        if last_observation_end:
            with lock:
                for flow in flows.values():
                    flow.setdefault('_possibleEnd', last_observation_end)
        if 'samplingElapsedSeconds' not in report['coverage']:
            report['coverage']['samplingElapsedSeconds'] = round(time.monotonic()-started, 1) if started is not None else 0
        if target_process and target_container:
            report['target']['application'] = {'appId': None, 'appName': None, 'confidence': 'UNKNOWN',
                'basis': '容器服务归属独立于 DevOps 应用，未按宿主机部署端口关联', 'candidates': []}
        elif target_process:
            host_ips = ips(asset.get('ip')) | {x['local']['ip'] for x in flows.values()}
            report['target'].update(processName=si.app_name(target_process), startedAt=target_process['startedAt'],
                startTicks=target_process['startTicks'],
                application=match_application(target_process, host_ips, listeners, apps, environment))
            if target_process.get('kind') == 'Tomcat':
                warnings.append('Tomcat 的同一 JVM 可能承载多个 Web 应用；本次连接只能归属到进程，应用多候选会保留')
        if terminal:
            terminal.close()
        if len(peer_results) < len(requests):
            notify('正在等待对端采集结束并保留连接证据', 'waiting_peers')
        pool.shutdown(wait=True)
    config = report.get('configuration', {})
    report['coverage'].update(sampling_coverage(target_stamps))
    # Observation coverage ends at the last effective frame. Time spent
    # matching, waiting for a lock or transporting results is not observation.
    report['coverage']['actualSeconds'] = (round(max(0, (
        datetime.fromisoformat(target_stamps[-1])-datetime.fromisoformat(target_stamps[0])
    ).total_seconds()), 1) if target_stamps else 0)
    report['coverage']['peers'] = [result['coverage'] for result in peer_results.values()]
    for flow in flows.values():
        retained = []
        for owner in flow['remoteProcesses']:
            evidence = [item for item in owner.get('_observations', [owner]) if possible_observation(
                flow, item.get('_observationStart', item['observedAt']), item.get('_observationEnd', item['observedAt']))]
            if evidence:
                selected_evidence = next((item for item in evidence if confirmed_observation(
                    flow, item.get('_observationStart', item['observedAt']), item.get('_observationEnd', item['observedAt']))), evidence[0])
                owner.update(selected_evidence)
                retained.append(owner)
        flow['remoteProcesses'] = retained
    if target_process and not cancel.is_set():
        notify('正在核验未解析下游 Endpoint 的监听进程', 'listeners')
        report['coverage']['listenerChecks'] = inspect_peer_listeners(
            list(flows.values()), asset, by_ip, apps, environment, saved, ip_map,
            terminal_opener, sample_probe, cancel, progress)
    notify('正在整理并保存上下游检测报告', 'saving')
    # Resolve temporal candidates before removing any internal lifetime bounds.
    for flow in flows.values():
        for owner in flow['remoteProcesses']:
            if any(other is not flow and other['local'] == flow['local'] and other['peer'] == flow['peer'] and
                   possible_observation(other, owner.get('_observationStart', owner['observedAt']),
                                        owner.get('_observationEnd', owner['observedAt'])) for other in flows.values()):
                flow['_temporalAmbiguous'] = True
    for flow in flows.values():
        flow['configurationMatches'] = config_matches(flow, config) if flow['direction'] == 'downstream' else []
        flow['service'] = middleware(port=flow['peer']['port'])
        kinds = {x['kind'] for x in flow['configurationMatches'] if x['kind'] != '未知服务'}
        if len(kinds) == 1:
            flow['service'] = {'name': next(iter(kinds)), 'confidence': 'INFERRED', 'basis': '应用配置声明的匹配 Endpoint，未确认生效 profile'}
        if flow['remoteProcesses']:
            for owner in flow['remoteProcesses']:
                # Full tuple reversal alone can be a later reuse. A remote reading
                # bracketed by this target socket's observations gives overlap.
                first = owner.get('_observationStart', owner['observedAt'])
                last = owner.get('_observationEnd', owner['observedAt'])
                socket_id = owner['connection'].get('socketId') if owner.get('container') else owner.get('_socketId')
                owner['confidence'] = ('EXACT' if not owner.get('dockerTargetConnection') and socket_id and not flow.get('_temporalAmbiguous') and
                    confirmed_observation(flow, first, last) else 'INFERRED')
                owner['observationStart'], owner['observationEnd'] = first, last
            flow['resolution'] = (flow['remoteProcesses'][0]['confidence'] if
                len(flow['remoteProcesses']) == 1 and not flow.get('_dockerAmbiguous') and
                not flow.get('_temporalAmbiguous') else 'AMBIGUOUS')
            flow['reason'] = ('在重叠观察窗口内匹配对端连接及进程' if flow['resolution'] == 'EXACT' else
                              '匹配到反向五元组，但 socket 身份或观察时间重叠证据不足') if len(flow['remoteProcesses']) == 1 else '对端连接存在多个进程所有者'
            if flow.get('_dockerAmbiguous'):
                flow['reason'] = ('多个发布 Endpoint 存在匹配的客户端连接，未唯一确认转发归属' if flow.get('_publishedMatches') else
                                  '同一发布 Endpoint 对应多个容器候选，未唯一确认转发归属')
            elif flow.get('_temporalAmbiguous'):
                flow['reason'] = '同一五元组在时间边界内对应多个本地 socket 候选，未唯一确认连接归属'
            elif len(flow['remoteProcesses']) == 1 and flow['remoteProcesses'][0].get('dockerTargetConnection'):
                flow['reason'] = '客户端连接与 Docker 发布端口、容器连接的客户端 Endpoint 在观察窗口内匹配；转发关系为推断，未核验 NAT 转换'
            elif len(flow['remoteProcesses']) == 1 and flow['remoteProcesses'][0].get('container'):
                flow['reason'] = ('Docker 发布端口映射与容器内反向连接、进程在重叠观察窗口内匹配' if
                                  flow['resolution'] == 'EXACT' else '匹配到 Docker 容器内反向连接，但 socket 身份或观察时间重叠未充分确认')
            if (len(flow['remoteProcesses']) == 1 and flow['resolution'] != 'AMBIGUOUS' and
                    flow['remoteProcesses'][0]['service']['confidence'] == 'EXACT'):
                flow['service'] = flow['remoteProcesses'][0]['service']
        else:
            flow.setdefault('resolution', 'UNKNOWN')
            flow.setdefault('reason', '本次未匹配到对端连接的进程归属，具体原因未确认；短连接可能在两端采样间隙结束')
            if flow.get('_peerSocketSeen'):
                flow['reason'] = '观察到相同反向五元组，但进程身份或时间对应关系未充分确认'
            mappings = flow.get('dockerMappings', [])
            if mappings:
                flow['resolution'] = 'AMBIGUOUS' if flow.get('_dockerAmbiguous') else 'UNKNOWN'
                flow['reason'] = ('同一发布 Endpoint 对应多个容器候选，未唯一确认转发归属' if
                    flow.get('_dockerAmbiguous') else '已找到 Docker 发布端口映射，未匹配容器内反向连接；代理/SNAT 可能改写客户端地址或端口')
                services = [m['service'] for m in mappings if m.get('service')]
                if len(services) == len(mappings) and len({s['name'] for s in services}) == 1:
                    flow['service'] = services[0]
            elif flow.get('proxyProcesses'):
                flow['reason'] = '匹配到 Docker 代理进程，但未确认其容器内服务连接'
        peer_result = peer_results.get(flow.get('_peerAssetId'), {})
        if not flow['remoteProcesses'] and flow.get('_peerAssetId'):
            peer_coverage = peer_result.get('coverage', {})
            if any('未提权至 root' in w for w in peer_result.get('warnings', [])):
                flow['reason'] = '对端未取得 root 权限，连接的进程归属可能不可见'
            elif peer_result.get('status') == 'partial' and not peer_result.get('coverage', {}).get('samples'):
                flow['reason'] = '对端未完成有效 socket 采样，无法确认连接的进程归属'
            elif (peer_coverage.get('observationStart') and flow.get('_possibleEnd') and
                  peer_coverage['observationStart'] > flow['_possibleEnd']) or (
                  peer_coverage.get('observationEnd') and
                  peer_coverage['observationEnd'] < flow.get('_possibleStart', flow['firstSeenAt'])):
                flow['reason'] = '对端有效采样与目标连接的可能存续窗口未重叠，未将窗口外 PID 归属到本连接'
        flow['warnings'].extend(peer_result.get('warnings', []))
        if flow.get('peerListeningProcesses') and not flow['remoteProcesses']:
            flow['reason'] += '；已获取对端端口监听进程，作为服务候选展示，连接所有者仍未确认'
            services = [p['service'] for p in flow['peerListeningProcesses']]
            if len({s['name'] for s in services}) == 1:
                flow['service'] = {**services[0], 'confidence': 'INFERRED', 'basis': '对端端口监听进程，仅作为服务类型候选'}
        flow['warnings'] = list(dict.fromkeys(flow['warnings']))
        for owner in flow['remoteProcesses']:
            for key in list(owner):
                if key.startswith('_'):
                    del owner[key]
        for key in list(flow):
            if key.startswith('_'):
                del flow[key]
        report[flow['direction']].append(flow)
    for direction in ('upstream', 'downstream'):
        report[direction].sort(key=lambda x: (x['peer']['ip'], x['peer']['port'], x['local']['port']))
    if report.get('status') == 'complete' and (warnings or config.get('status') in ('failed', 'partial', 'not_collected') or any(x['resolution'] != 'EXACT' or x['warnings'] for x in flows.values())):
        report['status'] = 'partial'
    report['finishedAt'] = now()
    return report
