"""Read-only inventory through the same Koko protocol as ../JumpServer.

Never persist terminal transcripts, passwords, cookies or raw nginx configuration.
Unknown processes are retained; only explicitly recognized OS processes are excluded.
"""
import base64
import fnmatch
import json
import posixpath
import re
import shlex
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

BASE = 'http://10.179.2.146:8080'

_SLOT_NAME = re.compile(r'^(slot:\d+)/')


def inner_prefix():
    """Keep inner pool threads attributable to the slot that spawned them."""
    match = _SLOT_NAME.match(threading.current_thread().name)
    return match.group(1).replace(':', '-') + '-' if match else ''


SYSTEM = set('systemd init kthreadd kworker ksoftirqd migration rcu_sched rcu_preempt watchdog udevd systemd-udevd systemd-journald systemd-logind systemd-resolved systemd-networkd dbus-daemon rsyslogd syslogd auditd audispd sshd cron crond anacron chronyd ntpd agetty getty polkitd irqbalance accounts-daemon cupsd avahi-daemon NetworkManager dhclient rpcbind rpc.statd'.split())
# comm comes from /proc/<pid>/comm (prctl PR_SET_NAME): it may contain spaces
# when a runtime rewrites its process title (e.g. Nuxt sets "node /var/www/w",
# kernel-truncated to 15 bytes). It is bounded to 15 chars and matched
# non-greedily so the following numeric columns (%cpu/%mem/rss/vsz) still align.
PROCESS_RE = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\w+\s+\w+\s+\d+\s+\d+:\d+:\d+\s+\d+)\s+(.{1,15}?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+(.*)$')
# %cpu / %mem / rss / vsz 供单机查询结果展示 CPU 与内存占用率及实际内存（旧快照数据缺这些字段，前端按 - 展示）。
PS = "LC_ALL=C TZ=UTC ps -eww -o pid=,ppid=,user:32=,etimes=,lstart=,comm=,%cpu=,%mem=,rss=,vsz=,args="

# Specs (CPU / memory / OS) are only collected for the 10.58 on-premises segment.
SPECS_COMMAND = (
    "cpu=$(nproc 2>/dev/null || echo 0); "
    "mem=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0); "
    "os=$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2- | tr -d '\"' || true); "
    "test -z \"$os\" && os=$(uname -sr 2>/dev/null || echo unknown); "
    "printf 'SPECS\\t%s\\t%s\\t%s\\n' \"$cpu\" \"$mem\" \"$os\""
)
SPECS_RE = re.compile(r'SPECS\t(\d+)\t(\d+)\t(.+)')


def is_10_58(ip):
    """True when any IP in the asset's ip field belongs to the 10.58.0.0/16 segment."""
    if not ip:
        return False
    for part in re.split(r'[\s,]+', str(ip)):
        part = part.strip()
        if part.startswith('10.58.'):
            return True
    return False


# Deployed web applications live as first-level directories of /var/www/webapps.
# The find output is "<epoch mtime>\t<dirname>" per entry; WEBAPPS_DONE marks the
# outcome (find's exit code, or "absent" when the directory does not exist).
WEBAPPS_COMMAND = (
    'dir=/var/www/webapps; '
    'if [ -d "$dir" ]; then '
    'find "$dir" -maxdepth 1 -mindepth 1 -type d -printf \'%T@\\t%f\\n\' 2>/dev/null; '
    'printf \'WEBAPPS_DONE\\t%s\\n\' "$?"; '
    'else printf \'WEBAPPS_DONE\\tabsent\\n\'; fi'
)
WEBAPPS_DONE_RE = re.compile(r'WEBAPPS_DONE\t(\S+)')
WEBAPPS_EPOCH_RE = re.compile(r'\d+(?:\.\d+)?')
# Release copies end with "_YYYYMMDD-<time>" (also tolerated: _ / . separators
# and 4-6 digit times); "__" prefixes and "_buff" suffixes are scratch/backup.
WEBAPPS_DATED_RE = re.compile(r'_\d{8}[-_.]\d{4,6}$')


def is_webapp_dir(name):
    """True for an application directory; False for backup/release copies."""
    if name.startswith(('.', '__')):
        return False
    if name.endswith('_buff'):
        return False
    if WEBAPPS_DATED_RE.search(name):
        return False
    return True


def collect_webapps(terminal):
    """List deployed web applications under /var/www/webapps.

    Each first-level subdirectory is one application; its mtime is the last
    update time. Status: complete / partial (find errors but some entries
    were listed) / absent (no such directory) / failed.
    """
    result = {'status': 'not_collected', 'apps': []}
    try:
        rc, body = terminal.run(WEBAPPS_COMMAND, timeout=20)
    except Exception:
        result['status'] = 'failed'
        return result
    match = WEBAPPS_DONE_RE.search(body)
    if not match:
        result['status'] = 'failed'
        return result
    outcome = match.group(1)
    if outcome == 'absent':
        result['status'] = 'absent'
        return result
    apps, seen = [], set()
    for line in body.splitlines():
        fields = line.split('\t')
        if len(fields) != 2 or not WEBAPPS_EPOCH_RE.fullmatch(fields[0]):
            continue
        name = fields[1].strip()
        if not name or name in seen or not is_webapp_dir(name):
            continue
        seen.add(name)
        try:
            mtime = datetime.fromtimestamp(float(fields[0]), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            continue
        apps.append({'name': name, 'mtime': mtime})
    apps.sort(key=lambda app: app['name'])
    result['apps'] = apps
    result['status'] = 'complete' if outcome == '0' else ('partial' if apps else 'failed')
    return result


def collect_specs(terminal):
    """Read CPU count, total memory (MB) and OS pretty name through the active terminal."""
    try:
        rc, body = terminal.run(SPECS_COMMAND, timeout=15)
        if rc != 0:
            return None
        match = SPECS_RE.search(body)
        if not match:
            return None
        cpu = int(match.group(1))
        memory_kb = int(match.group(2))
        os_name = match.group(3).strip()
        return {'cpu': cpu, 'memoryMB': memory_kb // 1024, 'os': os_name}
    except Exception:
        return None


# Server-level CPU/memory usage: sample /proc/stat twice with a 1-second
# interval to compute actual CPU utilisation; /proc/meminfo gives total vs
# available memory so the usage rate reflects real pressure (buffers/cache
# excluded). MemAvailable exists on Linux 3.2+ (CentOS 7+ has it); older
# kernels fall back to MemFree + Buffers + Cached.
# All values are printed on a single tab-separated line for reliable parsing.
USAGE_COMMAND = (
    "cpu1=$(head -1 /proc/stat); "
    "sleep 1; "
    "cpu2=$(head -1 /proc/stat); "
    "mt=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null); "
    "ma=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null); "
    "mf=$(awk '/^MemFree:/{print $2}' /proc/meminfo 2>/dev/null); "
    "bu=$(awk '/^Buffers:/{print $2}' /proc/meminfo 2>/dev/null); "
    "ca=$(awk '/^Cached:/{print $2}' /proc/meminfo 2>/dev/null); "
    "printf 'USAGE\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$cpu1\" \"$cpu2\" \"$mt\" \"$ma\" \"$mf\" \"$bu\" \"$ca\""
)


def _parse_cpu_stat(line):
    """Parse the first line of /proc/stat into a list of integer jiffies.

    Line format: 'cpu  user nice system idle iowait irq softirq steal ...'
    """
    tokens = line.split()
    values = []
    for t in tokens[1:]:
        try:
            values.append(int(t))
        except ValueError:
            pass
    return values


def _parse_mem_val(s):
    """Parse a memory value (kB) from a possibly empty string."""
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def collect_usage(terminal):
    """Read server-level CPU usage (%) and memory usage (%) through the active terminal.

    CPU usage is computed by sampling /proc/stat twice with a 1-second
    interval: usage% = (total_delta - idle_delta) / total_delta * 100.
    Memory usage uses MemAvailable when present (Linux 3.2+), otherwise
    falls back to MemTotal - MemFree - Buffers - Cached.
    """
    try:
        rc, body = terminal.run(USAGE_COMMAND, timeout=20)
        if rc != 0:
            return None
        match = re.search(r'USAGE\t([^\n]+)', body)
        if not match:
            return None
        fields = match.group(1).split('\t')
        if len(fields) < 7:
            return None
        cpu1 = _parse_cpu_stat(fields[0].strip())
        cpu2 = _parse_cpu_stat(fields[1].strip())
        if not cpu1 or not cpu2:
            return None
        total_delta = sum(cpu2) - sum(cpu1)
        # idle is index 3, iowait is index 4 (may be absent on very old kernels)
        idle1 = cpu1[3] + (cpu1[4] if len(cpu1) > 4 else 0)
        idle2 = cpu2[3] + (cpu2[4] if len(cpu2) > 4 else 0)
        idle_delta = idle2 - idle1
        if total_delta <= 0:
            cpu_percent = 0.0
        else:
            cpu_percent = round((total_delta - idle_delta) * 100 / total_delta, 1)
        mem_total = _parse_mem_val(fields[2]) or 0
        mem_available = _parse_mem_val(fields[3])
        mem_free = _parse_mem_val(fields[4])
        buffers = _parse_mem_val(fields[5])
        cached = _parse_mem_val(fields[6])
        if mem_available is not None:
            mem_used = mem_total - mem_available
        elif mem_free is not None:
            mem_used = mem_total - mem_free - (buffers or 0) - (cached or 0)
        else:
            return None
        mem_percent = round(mem_used * 100 / mem_total, 1) if mem_total > 0 else 0.0
        return {'cpuPercent': cpu_percent, 'memPercent': mem_percent,
                'memTotalMB': mem_total // 1024, 'memUsedMB': max(0, mem_used) // 1024}
    except Exception:
        return None


# Extended output exposes socket UIDs even when another user's PID is hidden.
# Keep non-TTY formatting and propagate ss failures through the pipe.
PORTS_COMMAND = "bash -o pipefail -c 'ss -ltnpe 2>/dev/null | cat'"

# Read /proc/net/tcp + /proc/net/tcp6 (world-readable, includes socket UID) and
# /etc/passwd (username→UID mapping) for PID inference without sudo.
PORT_UIDS_COMMAND = "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null; echo ===PASSWD===; cat /etc/passwd 2>/dev/null"


def parse_proc_net_tcp(body):
    """Parse /proc/net/tcp and /proc/net/tcp6 into {port: set(uids)}.

    Only LISTEN sockets (state 0A) are included. /proc/net/tcp is world-readable
    and includes the owning UID for each socket — this allows PID inference even
    when ss -p hides PIDs for other users' sockets (no sudo).
    """
    port_uids = {}
    for line in body.splitlines():
        tokens = line.split()
        if len(tokens) < 10:
            continue
        # Skip header: first token is "sl" or "sl:"
        if not tokens[0].rstrip(':').isdigit():
            continue
        state = tokens[3]
        if state != '0A':  # 0A = LISTEN
            continue
        local = tokens[1]  # e.g. "0100007F:0CEA"
        port_hex = local.rsplit(':', 1)[-1]
        try:
            port = int(port_hex, 16)
            uid = int(tokens[7])
        except ValueError:
            continue
        port_uids.setdefault(port, set()).add(uid)
    return port_uids


def parse_passwd(body):
    """Parse /etc/passwd into {username: uid}."""
    user_to_uid = {}
    for line in body.splitlines():
        parts = line.split(':')
        if len(parts) < 3:
            continue
        try:
            user_to_uid[parts[0]] = int(parts[2])
        except ValueError:
            continue
    return user_to_uid


def collect_port_uids(terminal):
    """Read /proc/net/tcp + /etc/passwd for UID-based port→process matching.

    Returns (port_uids, user_to_uid) where port_uids maps port→set(uids)
    and user_to_uid maps username→uid. Returns ({}, {}) on failure.
    """
    try:
        rc, body = terminal.run(PORT_UIDS_COMMAND, timeout=20)
    except Exception:
        return {}, {}
    if rc != 0:
        return {}, {}
    parts = body.split('===PASSWD===', 1)
    return parse_proc_net_tcp(parts[0]), parse_passwd(parts[1] if len(parts) > 1 else '')


# Well-known port → process matching rules for level-3 inference.
# match_names: process comm names; match_kinds: process kind labels;
# cmd_keyword: optional substring required in the command line (case-insensitive).
WELL_KNOWN_PORTS = {
    5672: {'match_names': {'beam.smp'}, 'cmd_pattern': r'(?:^|\s)-s\s+rabbit\s+boot(?:\s|$)', 'requires_uid': True, 'label': 'RabbitMQ'},
    15672: {'match_names': {'beam.smp'}, 'cmd_pattern': r'(?:^|\s)-s\s+rabbit\s+boot(?:\s|$)', 'requires_uid': True, 'label': 'RabbitMQ'},
    25672: {'match_names': {'beam.smp'}, 'cmd_pattern': r'(?:^|\s)-s\s+rabbit\s+boot(?:\s|$)', 'requires_uid': True, 'label': 'RabbitMQ'},
    3306: {'match_names': {'mysqld', 'mariadbd'}, 'match_kinds': {'MySQL'}, 'label': 'MySQL'},
    5432: {'match_names': {'postgres'}, 'match_kinds': {'PostgreSQL'}, 'label': 'PostgreSQL'},
    6379: {'match_names': {'redis-server', 'redis'}, 'match_kinds': {'Redis'}, 'label': 'Redis'},
    27017: {'match_names': {'mongod'}, 'match_kinds': {'MongoDB'}, 'label': 'MongoDB'},
    9200: {'match_kinds': {'Java'}, 'cmd_keyword': 'elasticsearch', 'label': 'Elasticsearch'},
    9300: {'match_kinds': {'Java'}, 'cmd_keyword': 'elasticsearch', 'label': 'Elasticsearch'},
    5601: {'match_kinds': {'Node.js'}, 'cmd_keyword': 'kibana', 'label': 'Kibana'},
    9600: {'match_kinds': {'Java'}, 'cmd_keyword': 'logstash', 'label': 'Logstash'},
    9092: {'match_kinds': {'Java'}, 'cmd_keyword': 'kafka', 'label': 'Kafka'},
    2181: {'match_kinds': {'Java'}, 'cmd_keyword': 'zookeeper', 'label': 'ZooKeeper'},
}


def well_known_port_candidates(port, processes):
    """Return all service-role candidates, never choose between competing PIDs."""
    info = WELL_KNOWN_PORTS.get(port)
    if not info:
        return []
    match_names = info.get('match_names', set())
    match_kinds = info.get('match_kinds', set())
    keyword = info.get('cmd_keyword')
    candidates = []
    for proc in processes:
        if proc.get('name', '') in match_names or proc.get('kind', '') in match_kinds:
            if keyword and keyword not in proc.get('command', '').lower():
                continue
            if info.get('cmd_pattern') and not re.search(info['cmd_pattern'], proc.get('command', ''), re.I):
                continue
            candidates.append(proc['pid'])
    return sorted(set(candidates))


def match_well_known_port(port, processes):
    candidates = well_known_port_candidates(port, processes)
    return candidates[0] if len(candidates) == 1 else None


def parse_listening_ports(body):
    """Parse `ss -ltnp` output into a list of {pid, port, address}.

    Sockets whose Process column carries `pid=N` are matched to their owning
    PIDs. Sockets whose Process column is empty or `-` (common when the
    collection account lacks sudo — ports and addresses are still visible but
    the owning PID is hidden for other users' sockets) are kept with pid=None
    so they are not silently lost.

    The port is the part after the last colon of the Local Address field and
    IPv6 brackets are stripped from the bind address.

    `ss -t` (TCP only) omits the Netid column on some iproute2 versions, so the
    State column becomes the first token — the Local Address index shifts by one
    depending on whether Netid is present.
    """
    listeners = []
    for line in body.splitlines():
        tokens = line.split()
        if len(tokens) < 5:
            continue
        # ss -t omits the Netid column when every row is TCP; detect by checking
        # whether the first token is a State value rather than a protocol name.
        offset = 0 if tokens[0] == 'LISTEN' else 1
        if tokens[offset] != 'LISTEN':
            continue
        local = tokens[3 + offset]
        port_text = local.rsplit(':', 1)[-1]
        if not port_text.isdigit() or not 0 < int(port_text) <= 65535:
            continue
        address = local.rsplit(':', 1)[0]
        if address.startswith('[') and address.endswith(']'):
            address = address[1:-1]
        pids = re.findall(r'pid=(\d+)', line)
        uid = re.search(r'\buid:(\d+)\b', line)
        metadata = {'uid': int(uid[1])} if uid else {}
        if pids:
            for pid in pids:
                listeners.append({'pid': int(pid), 'port': int(port_text), 'address': address, **metadata})
        else:
            listeners.append({'pid': None, 'port': int(port_text), 'address': address, **metadata})
    return listeners


# Command-line port argument patterns for inferring PID when ss -p can't show it.
PORT_ARG_RE = re.compile(
    r'(?:--server\.port|-Dserver\.port|--http\.port|--jetty\.port|--port)[=:](\d{2,5})'
)


def infer_port_pids(listeners, processes, port_uids=None, user_to_uid=None):
    """Keep inferred/ambiguous candidates separate from ss-observed PID owners.

    Socket UID constrains every candidate when available. A service-role hint
    can narrow a UID shared with auxiliary processes, but is never FD evidence.
    """
    user_to_uid = user_to_uid or {}
    port_to_pids = {}
    for proc in processes:
        cmd = proc.get('command', '')
        for m in PORT_ARG_RE.finditer(cmd):
            port = int(m.group(1))
            port_to_pids.setdefault(port, set()).add(proc['pid'])
    for listener in listeners:
        if listener['pid'] is not None:
            continue
        socket_uid = listener.get('uid')
        if socket_uid is None:
            uids = (port_uids or {}).get(listener['port'], set())
            if len(uids) == 1:
                socket_uid = next(iter(uids))
        pool = processes
        reasons = []
        if socket_uid is not None:
            def process_uid(proc):
                user = proc.get('user', '')
                return int(user) if str(user).isdigit() else user_to_uid.get(user)
            pool = [p for p in processes if process_uid(p) == socket_uid]
            reasons.append('socket UID '+str(socket_uid)+'，仅考虑同 UID 进程')
        pool_ids = {p['pid'] for p in pool}
        declared = port_to_pids.get(listener['port'], set()) & pool_ids
        rule = WELL_KNOWN_PORTS.get(listener['port'], {})
        service = set(well_known_port_candidates(listener['port'], pool)) if socket_uid is not None or not rule.get('requires_uid') else set()
        if declared:
            reasons.append('启动参数声明相同端口')
        if service:
            reasons.append('监听端口与 '+rule['label']+' 主进程角色匹配')
        # Conflicting declarations and role hints retain every candidate.
        candidates = declared | service
        # A shared UID alone cannot identify a service role. Root is especially
        # unsafe here: filtered/partial ps output may hide its actual owner.
        if not candidates and socket_uid not in (None, 0) and len(pool_ids) == 1 and not rule.get('requires_uid'):
            candidates = pool_ids
        listener.update(candidatePids=sorted(candidates),
                        ownershipConfidence='INFERRED' if len(candidates) == 1 else 'AMBIGUOUS' if candidates else 'UNKNOWN',
                        basis='；'.join(reasons) or '监听端口可见，进程归属未确认')


def collect_listening_ports(terminal):
    """Read TCP listeners; None means collection failed, [] means none observed."""
    try:
        rc, body = terminal.run(PORTS_COMMAND, timeout=20)
    except Exception:
        return None
    if rc != 0:
        return None
    return parse_listening_ports(body)


def jar_name(command):
    """Return the .jar basename when the command runs `java -jar <path>`."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'[^\']*\'|[^\s]+', command)
    tokens = [re.sub(r'^([\'"])(.*)\1$', r'\2', t) for t in tokens]
    try:
        i = tokens.index('-jar')
    except ValueError:
        return None
    if i + 1 < len(tokens):
        return tokens[i + 1].split('/')[-1]
    return None


def app_name(process):
    """Best-effort application name for a process.

    Java apps resolve to -Dappid / -Dspring.application.name / the .jar name;
    recognized service kinds (Nginx, Redis, ...) use the kind; others fall
    back to the process comm so a custom daemon still has a label.
    """
    kind = process.get('kind', '')
    command = process.get('command', '')
    if kind == 'Java':
        m = re.search(r'(?:-D|--)appid=([A-Za-z0-9_.-]+)', command)
        if m:
            return m.group(1)
        m = re.search(r'-Dspring\.application\.name=([A-Za-z0-9_.-]+)', command)
        if m:
            return m.group(1)
        jar = jar_name(command)
        if jar:
            return jar
        return 'Java'
    if kind in ('Tomcat', 'Kafka', 'Nginx', 'Redis', 'MySQL', 'PostgreSQL', 'Node.js', 'Python'):
        return kind
    return process.get('name') or '未分类'


def attach_process_ports(processes, listeners):
    """Attach observed owners to ports and unique candidates to inferredPorts."""
    by_pid = {}
    inferred = {}
    for listener in listeners:
        if listener['pid'] is None:
            candidates = listener.get('candidatePids', [])
            if listener.get('ownershipConfidence') == 'INFERRED' and len(candidates) == 1:
                inferred.setdefault(candidates[0], []).append({
                    'port': listener['port'], 'address': listener['address'], 'protocol': 'tcp',
                    'confidence': 'INFERRED', 'basis': listener['basis']})
            continue
        by_pid.setdefault(listener['pid'], set()).add(listener['port'])
    for proc in processes:
        ports = sorted(by_pid.get(proc['pid'], ()))
        if ports:
            proc['ports'] = ports
        candidates = inferred.get(proc['pid'])
        if candidates:
            proc['inferredPorts'] = sorted(candidates, key=lambda p: (p['port'], p['address']))


def build_app_ports(processes, listeners):
    """Group listening sockets by application name and port.

    Each entry is one (app, port) pair with the bind addresses and PIDs that
    own it. Sockets whose PID matches a collected process are attributed to
    that application. Sockets without a PID (sudo unavailable — owning PID
    hidden by ss) or whose PID is not in the process list are reported under
    the placeholder app name ``未知进程`` so the port count is not silently
    zeroed out.
    """
    by_pid = {p['pid']: p for p in processes}
    grouped = {}
    for listener in listeners:
        proc = by_pid.get(listener['pid']) if listener['pid'] is not None else None
        if proc:
            name = app_name(proc)
            kind = proc.get('kind', '未分类')
        else:
            name = '未知进程'
            kind = '未知'
        candidates = tuple(listener.get('candidatePids', []))
        confidence = listener.get('ownershipConfidence', 'UNKNOWN')
        basis = listener.get('basis', '')
        key = (name, listener['port'], candidates, confidence, basis)
        entry = grouped.get(key)
        if entry is None:
            entry = {'kind': kind, 'addresses': set(), 'pids': set()}
            grouped[key] = entry
        entry['addresses'].add(listener['address'])
        if listener['pid'] is not None:
            entry['pids'].add(listener['pid'])
    result = []
    for (app, port, candidates, confidence, basis), entry in grouped.items():
        item = {'app': app, 'kind': entry['kind'], 'port': port,
                'addresses': sorted(entry['addresses']), 'pids': sorted(entry['pids'])}
        if candidates or basis:
            item.update(candidatePids=list(candidates), ownershipConfidence=confidence, basis=basis)
        result.append(item)
    result.sort(key=lambda r: (r['app'], r['port']))
    return result


def clean(text):
    return re.sub(r'[^\n\r\t\x20-\uFFFF]', '', re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)).replace('\r', '')


def redact(text):
    # Arguments can contain application secrets even though collection is read-only.
    return re.sub(r'(?i)((?:password|passwd|pwd|secret|token|access[_-]?key)(?:\s*[=:]\s*|\s+))[^\s;]+', r'\1[REDACTED]', text)


def parse_processes(body):
    rows, excluded, malformed = [], 0, 0
    for line in body.splitlines():
        if not line.strip(): continue
        m = PROCESS_RE.match(line)
        if not m:
            malformed += 1
            continue
        pid, ppid, user, age, start, comm, cpu, mem, rss, vsz, command = m.groups()
        is_system = command.startswith('[') or comm in SYSTEM
        if is_system:
            excluded += 1
        lower = command.lower()
        kind = next((name for name, pattern in [('Tomcat',r'org\.apache\.catalina'), ('Kafka',r'kafka\.'), ('Nginx',r'nginx'), ('Java',r'\bjava\b'), ('Redis',r'redis-server'), ('MySQL',r'mysqld'), ('PostgreSQL',r'postgres'), ('Node.js',r'\bnode\b'), ('Python',r'\bpython[23]?\b')] if re.search(pattern, lower)), '未分类')
        try:
            stamp = datetime.strptime(start, '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            malformed += 1
            continue
        rows.append(dict(pid=int(pid), ppid=int(ppid), user=user, name=comm, kind=kind,
                         startedAt=stamp, elapsedSeconds=int(age),
                         cpuPercent=float(cpu), memPercent=float(mem), rssKB=int(rss), vszKB=int(vsz),
                         command=redact(command), system=is_system))
    return rows, excluded, malformed


def skip_lua_block(text, start):
    """Lua bodies use Lua grammar, not Nginx's semicolon-terminated directives."""
    i=start+1; depth=1
    while i<len(text):
        comment=text.startswith('--',i)
        if comment:i+=2
        long=re.match(r'\[(=*)\[',text[i:])
        if long:
            end=text.find(']'+long[1]+']',i+len(long[0]))
            if end<0: raise ValueError('未闭合 Lua 长字符串')
            i=end+len(long[1])+2;continue
        if comment:
            end=text.find('\n',i);i=len(text) if end<0 else end+1;continue
        if text[i] in ('"',"'"):
            quote=text[i];i+=1
            while i<len(text):
                if text[i]=='\\':i+=2;continue
                if text[i]==quote:i+=1;break
                i+=1
            continue
        if text[i]=='{':depth+=1
        if text[i]=='}':
            depth-=1
            if depth==0:return i
        i+=1
    raise ValueError('未闭合 Lua 配置块')


def tokenize(text):
    """Nginx punctuation, comments, escaped characters, quotes and ${variables}."""
    tokens, buf, quote, i = [], '', None, 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            buf += text[i:i+2]; i += 2; continue
        if quote:
            if c == quote: quote = None
            else: buf += c
        elif c in '\"\'': quote = c
        elif c == '$' and i+1 < len(text) and text[i+1] == '{':
            end = text.find('}', i+2)
            if end < 0: raise ValueError('未闭合变量')
            buf += text[i:end+1]; i = end
        elif c == '#' and not buf:
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        elif c == '{' and (buf or (tokens[-1] if tokens else '')).endswith('_by_lua_block'):
            if buf: tokens.append(buf);buf=''
            tokens.extend(['{','}']);i=skip_lua_block(text,i)
        elif c.isspace() or c in '{};':
            if buf: tokens.append(buf); buf = ''
            if c in '{};': tokens.append(c)
        else: buf += c
        i += 1
    if quote: raise ValueError('未闭合引号')
    if buf: tokens.append(buf)
    return tokens


def parse_tree(text):
    tokens = iter(tokenize(text))
    def block(nested=False):
        result, args = [], []
        for token in tokens:
            if token == '}':
                if not nested or args: raise ValueError('配置块不完整')
                return result
            if token in (';', '{'):
                if not args: raise ValueError('空指令')
                result.append((args, block(True) if token == '{' else [])); args = []
            else: args.append(token)
        if nested or args: raise ValueError('配置不完整')
        return result
    return block()


def static_target(alias, root, uri):
    """Resolve the on-disk directory a static location serves, from alias/root.

    `alias X` maps the location prefix onto X, so the directory is X itself.
    `root X` appends the request path, so for a plain prefix/exact location the
    directory is X + location path; for regex/named locations the path cannot
    be derived and the raw root is returned. Values keep variables verbatim.
    """
    if alias: return alias
    if not root: return ''
    parts = uri.split(' ')
    if len(parts) == 2 and parts[0] in ('=','^~'): path = parts[1]
    elif len(parts) == 1 and parts[0].startswith('/'): path = parts[0]
    else: return root
    return root.rstrip('/') + path


def parse_nginx(dump):
    sections = re.split(r'(?m)^# configuration file (.+):\s*\n', dump)
    files = {sections[i]: sections[i+1] for i in range(1, len(sections)-1, 2)}
    if not files: raise ValueError('nginx -T 未返回配置文件')
    root = next(iter(files)); prefix = posixpath.dirname(root); warnings = []
    def expand(path, stack=()):
        if path in stack: raise ValueError('循环 include')
        def walk(nodes):
            out = []
            for args, children in nodes:
                if args[0] == 'include' and len(args) == 2:
                    pattern = args[1] if args[1].startswith('/') else posixpath.join(prefix,args[1])
                    matches = [p for p in files if fnmatch.fnmatchcase(p, pattern)]
                    if not matches and not any(c in args[1] for c in '*?['): warnings.append('include 未展开：'+args[1])
                    for p in matches: out.extend(expand(p, stack+(path,)))
                else:
                    if '_by_lua' in args[0]: warnings.append('包含 Lua 动态逻辑；静态配置不能确定运行时域名、URI 或后端')
                    out.append((args, walk(children), path))
            return out
        return walk(parse_tree(files[path]))
    tree = expand(root)
    routes = []
    def values(nodes, key): return [a[1:] for a,c,_ in nodes if a[0] == key]
    def endpoint(value, default='80'):
        if value.startswith('unix:'): return {'host':value, 'port':None, 'resolution':'unix'}
        if '$' in value: return {'host':value, 'port':None, 'resolution':'dynamic'}
        parsed = urlsplit('//'+value)
        try: port = str(parsed.port) if parsed.port else default
        except ValueError: port = None
        host = parsed.hostname or value
        import ipaddress
        try: ipaddress.ip_address(host); resolution='ip'
        except ValueError: resolution='hostname'
        return {'host':host,'port':port,'resolution':resolution}
    for args, context, _ in tree:
        if args[0] not in ('http','stream'): continue
        upstreams = {a[1]: [endpoint(v[0]) for v in values(c,'server') if v] for a,c,_ in context if a[0]=='upstream' and len(a)>1}
        def visit(nodes, domains, listeners, uri, inherited=None, src=None, inherited_root=None, static_fallback=True):
            proxies = [(a[0],a[1],p) for a,c,p in nodes if a[0] in ('proxy_pass','fastcgi_pass','grpc_pass','uwsgi_pass','scgi_pass') and len(a)>1]
            effective = proxies or inherited or []
            alias = next((a[1] for a,c,p in nodes if a[0]=='alias' and len(a)==2), None)
            own_root = next((a[1] for a,c,p in nodes if a[0]=='root' and len(a)==2), None)
            effective_root = own_root if own_root is not None else inherited_root
            for directive, target, source in effective:
                parsed=urlsplit(target if '://' in target else '//'+target)
                name=parsed.netloc if '://' in target else target
                backends=upstreams.get(name)
                if backends is None:
                    backends=[endpoint(name, '443' if parsed.scheme in ('https','grpcs') else '80')]
                routes.append(dict(domains=domains, listen=listeners, uri=uri, directive=directive,
                                   target=target, upstream=name, backends=backends, file=source, context=args[0]))
            if not effective and static_fallback:
                routes.append(dict(domains=domains,listen=listeners,uri=uri,directive='static/other',target=static_target(alias,effective_root,uri),upstream='',backends=[],file=src,context=args[0]))
            for a,c,p in nodes:
                if a[0]=='location': visit(c,domains,listeners,' '.join(a[1:]),effective,p,effective_root)
                # if/limit_except are not routing contexts: capture only their own
                # proxy directives; the parent block already emitted this uri's route.
                elif a[0] in ('if','limit_except'): visit(c,domains,listeners,uri,None,p,effective_root,static_fallback=False)
        for a,c,src in context:
            if a[0]=='server':
                domains=[x for v in values(c,'server_name') for x in v]
                # An explicit `location /` already covers the server-level fallback.
                has_root_location = any(x[0][0]=='location' and x[0][1:]==['/'] for x in c)
                visit(c,domains,[' '.join(v) for v in values(c,'listen')], '/' if args[0]=='http' else '(TCP/UDP)', src=src, static_fallback=not has_root_location)
    seen, unique = set(), []
    for r in routes:
        key = (tuple(r['listen']),tuple(r['domains']),r['uri'],r['directive'],r['target'],r['upstream'],
               tuple((b['host'],b['port'],b['resolution']) for b in r['backends']),r['context'],r.get('file'))
        if key in seen: continue
        seen.add(key); unique.append(r)
    return unique, sorted(set(warnings))


class Terminal:
    def __init__(self, asset, account, cookie, password):
        import websocket
        self._prompt_ready = False
        self._prompt_tail = ''
        self._timed_out = False
        self._aborted = False
        self.wsmod=websocket; self.password=password; self.account=account; self.tid=None
        self.ws=websocket.create_connection(BASE.replace('http:','ws:')+'/koko/ws/terminal/?'+urlencode({'target_id':asset['id'],'type':'asset','system_user_id':account['id']}), cookie=cookie, subprotocols=['JMS-KOKO'], origin=BASE, timeout=5)

    def send(self, text): self.ws.send(json.dumps({'id':self.tid,'type':'TERMINAL_DATA','data':text}))

    def recv(self):
        try: raw=self.ws.recv()
        except self.wsmod.WebSocketTimeoutException: return ''
        if not raw: raise ConnectionError('连接已关闭')
        msg=json.loads(raw)
        if msg['type']=='CONNECT':
            self.tid=msg['id'];self.ws.send(json.dumps({'id':self.tid,'type':'TERMINAL_INIT','data':json.dumps({'cols':1000,'rows':40})}))
        if msg['type'] in ('CLOSE','ERROR'): raise ConnectionError('堡垒机关闭连接或拒绝访问')
        return msg.get('data','') if msg['type']=='TERMINAL_DATA' else ''

    def login(self):
        deadline=time.monotonic()+45; buf=''; supplied=False
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())[-20000:]
            if re.search(r'authentication failed|permission denied|认证失败|发生错误|无法连接|connection refused',buf,re.I):
                reason = ('SSH 认证失败或账号无权限' if re.search(r'authentication failed|permission denied|认证失败',buf,re.I) else '目标 SSH 端口拒绝连接' if re.search(r'connection refused',buf,re.I) else '堡垒机连接资产失败')
                raise ConnectionError(reason)
            if re.search(r'(?:password|密码)[：:]\s*$',buf,re.I):
                if self.account.get('username') != 'folidev' or not self.password or supplied:
                    raise ConnectionError('资产需要密码，未配置 folidev 密码或密码被拒绝')
                self.send(self.password+'\r'); supplied=True;buf=''
            elif self.tid and re.search(r'[^\n]*[#$>]\s*$',buf):
                # Prove a command can run; a banner ending in # is not login success.
                rc, output=self.run('id -u', timeout=15)
                if rc == 0 and re.search(r'(?m)^\d+$',output): return
                raise ConnectionError('终端未能执行登录验证命令')
        raise TimeoutError('登录超时（45 秒）')

    def elevate(self, password=None):
        """Enter the authorized sudo login shell once; never retry a wrong password."""
        pw = password if password is not None else self.password
        rc, body = self.run('id -u', timeout=15)
        if rc == 0 and body.strip() == '0': return 'root', ''
        token = 'SUDO'+uuid.uuid4().hex
        prompt = token+'_PASSWORD'
        has_password = bool(pw)
        command = 'sudo -p '+shlex.quote(prompt)+' -i' if has_password else 'sudo -n -i'
        self.send("printf '\\n%s%s\\n' "+token+" _BEGIN; "+command+"; r=$?; printf '\\n%s%s:%s\\n' "+token+" _EXIT \"$r\"\r")
        deadline=time.monotonic()+45; buf=''; supplied=False
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())[-30000:]
            marker=token+'_BEGIN\n'
            if marker not in buf: continue
            body=buf.split(marker,1)[1]
            wrong=re.search(r'Sorry, try again|incorrect password|authentication failure|对不起.*重试|抱歉.*重试|密码.*错误',body,re.I)
            if supplied and (wrong or body.rstrip().endswith(prompt) and body.count(prompt)>1):
                # Sudo rejected the password. Send Ctrl+C plus enough empty
                # Enters to exhaust sudo's default 3-attempt retry limit, then
                # WAIT for the _EXIT marker (printed by the folidev shell
                # after sudo exits). Returning earlier leaves the PTY inside
                # sudo's password retry loop, so the next command (ps) is read
                # as another password attempt and times out in run().
                self.send('\x03\r\r\r\r\r\r\r\r\r\r')
                wait=time.monotonic()+10
                while time.monotonic()<wait:
                    buf=clean(buf+self.recv())[-30000:]
                    if marker in buf and re.search(r'(?m)^'+token+r'_EXIT:\d+', buf.split(marker,1)[1]):
                        return 'password_error','sudo 密码错误'
                self.send('\x03')
                return 'timeout','sudo -i 超时'
            if body.rstrip().endswith(prompt) and not supplied:
                self.send(pw+'\r'); supplied=True
                continue
            if re.search(r'(?m)^'+token+r'_EXIT:\d+',body):
                if re.search(r'password.*required|需要密码',body,re.I): return 'password_required','sudo 需要 folidev 密码'
                return 'denied','sudo -i 不可用或无权限'
            if re.search(r'[^\n]*[#$>]\s*$',body):
                # sudo -i started a fresh login shell; it may still be running
                # .bash_profile / MOTD output after the prompt first appears.
                # Wait for the shell to fully stabilize before sending run()
                # commands — otherwise the printf _BEGIN marker can be lost.
                time.sleep(0.5)
                rc, identity=self.run('id -u',timeout=15)
                if rc==0 and identity.strip()=='0': return ('password' if supplied else 'passwordless'),''
                return 'denied','sudo -i 未获得 root 权限'
        self.send('\x03')
        return 'timeout','sudo -i 超时'

    def elevate_su(self, root_password):
        """Switch to root via `su -l` with the given root password; never retry."""
        rc, body = self.run('id -u', timeout=15)
        if rc == 0 and body.strip() == '0': return 'root', ''
        if not root_password:
            return 'denied', 'su 需要 root 密码，未配置'
        token = 'SU'+uuid.uuid4().hex
        self.send("printf '\\n%s%s\\n' "+token+" _BEGIN; su -l\r")
        deadline=time.monotonic()+30; buf=''; supplied=False
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())[-30000:]
            marker=token+'_BEGIN\n'
            if marker not in buf: continue
            body=buf.split(marker,1)[1]
            if not supplied and re.search(r'[Pp]assword[：:]\s*$', body):
                self.send(root_password+'\r'); supplied=True
                # Keep marker in buf so subsequent loop iterations can still
                # parse body; only discard what came after it so far.
                buf = marker
                continue
            if supplied:
                wrong=re.search(r'su:.*(incorrect|invalid|authentication|失败|错误)|incorrect password|authentication failure|密码.*错误',body,re.I)
                if wrong:
                    self.send('\x03')
                    return 'password_error','su 密码错误'
                if re.search(r'[^\n]*[#$>]\s*$',body):
                    time.sleep(0.5)
                    rc, identity=self.run('id -u',timeout=15)
                    if rc==0 and identity.strip()=='0': return 'password',''
                    self.send('\x03')
                    return 'denied','su 未获得 root 权限'
            # If no password prompt appeared but a root prompt did, su may have
            # succeeded without authentication (PAM config).  Check the prompt
            # text for "root@" — calling run('id -u') this early can race with
            # login-shell initialization and time out.
            if not supplied and re.search(r'[^\n]*[#$>]\s*$', body):
                if re.search(r'root@', body):
                    time.sleep(1.0)
                    return 'passwordless','su 无需密码直接获得 root'
        # Timeout: diagnose what happened (last 300 chars of buf, safe for logs).
        tail = buf[-300:].replace('\r','\\r').replace('\n','\\n') if buf else '(empty)'
        self.send('\x03')
        return 'timeout', f'su 超时（buf 末尾: {tail[:200]}）'

    def _wait_prompt(self, deadline):
        tail = getattr(self, '_prompt_tail', '')
        while not re.search(r'[^\n]*[#$>]\s*$', tail):
            if time.monotonic() >= deadline:
                raise TimeoutError('等待远端命令提示符超时')
            tail = clean(tail+self.recv())[-20000:]
        self._prompt_tail, self._prompt_ready = tail, True

    def _set_read_timeout(self, seconds):
        """Bound every blocking read so run() can re-check its deadline."""
        ws = getattr(self, 'ws', None)
        for target in (ws, getattr(ws, 'sock', None) if ws is not None else None):
            if target is None:
                continue
            try:
                target.settimeout(seconds)
                return
            except Exception:
                continue

    def _abort_after_timeout(self):
        """A timed-out PTY is no longer in sync; close it instead of reusing it."""
        self._timed_out = True
        self._aborted = True
        try:
            self.send('\x03')
        except Exception:
            pass
        ws = getattr(self, 'ws', None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def run(self, command, timeout=60, wait_for_prompt=False):
        # The PTY line discipline splits on newlines before the shell parses
        # quotes, so only one physical command line is safe here. Existing
        # script probes keep their explicit bash -c wrapper.
        if ('\n' in command or '\r' in command) and not re.match(
                r'^[ \t]*(?:timeout[ \t]+\S+[ \t]+)?bash[ \t]+-c(?:[ \t]|$)', command):
            raise ValueError('run() 仅支持单行命令；多行脚本请使用 bash -c 形式或上传后执行')
        if getattr(self, '_aborted', False):
            raise ConnectionError('终端已在超时后关闭，需要重新建立会话')
        deadline=time.monotonic()+timeout
        try:
            self._set_read_timeout(min(1.0, max(0.05, timeout)))
            if wait_for_prompt and not getattr(self, '_prompt_ready', True):
                self._wait_prompt(deadline)
        except TimeoutError:
            self._abort_after_timeout()
            raise
        self._prompt_ready=False
        token='INV'+uuid.uuid4().hex
        # Split markers ensure echoed commands cannot be interpreted as output.
        self.send("printf '\\n%s%s\\n' "+token+" _BEGIN; "+command+"; r=$?; printf '\\n%s%s:%s\\n' "+token+" _END \"$r\"\r")
        buf=''
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())
            if len(buf)>24_000_000: raise ValueError('终端输出超过 24 MB 限制')
            match=re.search(r'(?m)^'+token+r'_END:(\d+)\s*$',buf)
            if match:
                self._prompt_tail=buf[match.end():]
                self._prompt_ready=bool(re.search(r'[^\n]*[#$>]\s*$',self._prompt_tail))
                end_pos = match.start()
                # Strategy 1: find BEGIN marker (most reliable).
                marker=token+'_BEGIN\n'
                alt_marker='\n'+marker
                begin_pos = buf.find(marker)
                if begin_pos == -1: begin_pos = buf.find(alt_marker)
                if begin_pos != -1:
                    out = buf[begin_pos + len(marker):end_pos].strip()
                else:
                    # Strategy 2 (fallback): BEGIN marker was lost — this can
                    # happen after `sudo -i` or `su -` starts a fresh login
                    # shell whose initialization races with the next command.
                    # Walk backwards from the END marker to find the line that
                    # echoed our printf invocation; command output sits between
                    # that echo and the END marker.
                    echo_pos = buf.rfind("printf '\\n"+token, 0, end_pos)
                    if echo_pos == -1:
                        echo_pos = buf.rfind(token+' _BEGIN', 0, end_pos)
                    if echo_pos != -1:
                        # Skip past the echoed line entirely (up to next \n)
                        nl = buf.find('\n', echo_pos)
                        out = buf[nl+1:end_pos].strip() if nl != -1 else buf[echo_pos:end_pos].strip()
                    else:
                        # Last resort: take everything after the last shell
                        # prompt before the END marker.
                        tail = buf[:end_pos]
                        prompt_hits = list(re.finditer(r'[#$>]\s*$', tail, re.M))
                        out = tail[prompt_hits[-1].end():].strip() if prompt_hits else tail.strip()
                if wait_for_prompt and not self._prompt_ready:
                    try:
                        self._wait_prompt(deadline)
                    except TimeoutError:
                        self._abort_after_timeout()
                        raise
                return int(match[1]), out
        self._abort_after_timeout()
        raise TimeoutError('只读命令执行超时')

    def close(self):
        try: self.ws.close(timeout=1)
        except Exception: pass


# nginx short options that terminate a preceding -g directive string.
NGINX_SHORT_OPTS=('-?','-h','-v','-V','-t','-T','-q','-s','-p','-c','-g','-e')


def nginx_options(args):
    """Reconstruct -p / -c / -g options from an nginx master process title.

    nginx rewrites its title by joining argv with single spaces, so a quoted
    `-g 'daemon off;'` reaches us as `-g daemon off;`. A -g value is a directive
    string terminated by ';', so those words must be rejoined; passing only the
    first word (`-g daemon`) makes nginx fail with "invalid number of arguments
    in \\"daemon\\" directive". -p / -c values are single paths.
    """
    opts=[];i=1
    while i<len(args):
        arg=args[i]
        if arg=='-g':
            words=[];j=i+1
            while j<len(args):
                if words and args[j] in NGINX_SHORT_OPTS: break
                words.append(args[j])
                if args[j].endswith(';'): j+=1; break
                j+=1
            if words: opts.extend(['-g',' '.join(words)])
            i=j
        elif arg in ('-p','-c'):
            if i+1<len(args): opts.extend([arg,args[i+1]]);i+=2
            else: i+=1
        elif len(arg)>2 and arg[:2] in ('-p','-c','-g'):
            opts.append(arg);i+=1
        else:
            i+=1
    return opts


def nginx_commands(processes):
    """Use each running master executable and its original -p / -c options."""
    commands=[]
    for p in processes:
        if 'nginx: master process ' not in p['command']: continue
        args=shlex.split(p['command'].split('nginx: master process ',1)[1])
        if not args: continue
        executable=f"/proc/{p['pid']}/exe"
        opts=nginx_options(args)
        commands.append(' '.join(shlex.quote(a) for a in [executable,'-T',*opts]))
    return list(dict.fromkeys(commands))


def nginx_mount_root(terminal, processes):
    """Return '/proc/<pid>/root' when the first nginx master runs in a different
    mount namespace (container), else ''.

    A containerized nginx keeps its config inside the container's own filesystem,
    so host-side paths like /etc/nginx/nginx.conf do not exist and `nginx -T`
    cannot see them. /proc/<pid>/root exposes that filesystem to root, and the
    device:inode of the two roots tells the namespaces apart.
    """
    for p in processes:
        if 'nginx: master process ' not in p['command']: continue
        rc, out = terminal.run('stat -L -c %%d:%%i /proc/%s/root /' % p['pid'], timeout=15)
        if rc != 0: return ''
        values=out.split()
        return '/proc/%s/root' % p['pid'] if len(values)>=2 and values[0]!=values[1] else ''
    return ''


# A PTY in canonical mode caps one input line at MAX_CANON (4096 bytes on Linux).
# Joining every file read into a single shell line overflows that buffer: the line
# is truncated, the shell is left waiting on the broken remainder and run() times
# out, so a host with many config files (e.g. 52) never finishes. Batch the
# sub-commands so each sent line stays comfortably under the limit.
CONFIG_BATCH_BYTES=3000


def config_batches(commands, limit=CONFIG_BATCH_BYTES):
    """Group sub-commands so each joined shell line stays under the PTY limit."""
    batches=[];current=[];size=0
    for command in commands:
        if current and size+len(command)+2>limit:
            batches.append(current);current=[];size=0
        current.append(command);size+=len(command)+2
    if current: batches.append(current)
    return batches


def collect_config_files(terminal, paths, instance, fs_root=''):
    # Base64 protects exact file bytes from PTY CR/LF conversion and escape stripping.
    # `fs_root` (e.g. /proc/<pid>/root) makes the same logical paths readable when
    # nginx runs inside a container with its own mount namespace; stored paths stay
    # logical so the data model matches what `nginx -T` would report.
    marker='CONFIG'+uuid.uuid4().hex
    commands=[]
    for i,path in enumerate(paths):
        physical=fs_root+path if fs_root and path.startswith('/') else path
        commands.append("printf '\\n%s%s\\n' "+marker+' _FILE_'+str(i)+"; base64 -- "+shlex.quote(physical)+" 2>/dev/null; r=$?; printf '\\n%s%s:%s\\n' "+marker+' _DONE_'+str(i)+' "$r"')
    if not commands: return [], ['Nginx 未返回配置文件清单']
    outputs=[]
    for batch in config_batches(commands):
        _, output=terminal.run('; '.join(batch),timeout=90)
        outputs.append(output)
    output='\n'.join(outputs)
    files=[];errors=[]
    for i,path in enumerate(paths):
        match=re.search(re.escape(marker+'_FILE_'+str(i))+r'\n(.*?)\n'+re.escape(marker+'_DONE_'+str(i))+r':(\d+)',output,re.S)
        if not match or match[2]!='0':
            errors.append('原始配置读取失败：'+path);continue
        try:
            encoded=''.join(match[1].split()); raw=base64.b64decode(encoded,validate=True)
            files.append({'path':path,'instance':instance,'content':raw.decode('utf-8',errors='replace'),'base64':encoded,'bytes':len(raw)})
        except ValueError: errors.append('原始配置传输校验失败：'+path)
    return files,errors


def ls_glob(terminal, pattern, fs_root=''):
    """Expand an absolute ls pattern that may contain shell globs.

    Globs must stay outside quotes or the shell passes them to ls literally and
    nothing matches. Patterns come from nginx include directives and our own
    conf.d sweep, so restrict them to safe filename characters and send them
    unquoted; -1 keeps ls one-per-line even though stdout is a PTY. Results are
    returned without the `fs_root` prefix so callers keep logical paths.
    """
    if not re.fullmatch(r'[A-Za-z0-9_./?*\[\]-]+', pattern) or '..' in pattern:
        return []
    physical=fs_root+pattern if fs_root and pattern.startswith('/') else pattern
    rc, listing = terminal.run('ls -1 -- '+physical+' 2>/dev/null', timeout=15)
    if rc != 0: return []
    found=[]
    for line in listing.splitlines():
        path=line.strip()
        if not path: continue
        found.append(path[len(fs_root):] if fs_root and path.startswith(fs_root) else path)
    return found


def list_conf_d(terminal, prefix, fs_root=''):
    """All .conf files under <prefix>/conf.d that the current account can list."""
    return ls_glob(terminal, prefix.rstrip('/')+'/conf.d/*.conf', fs_root)


def nginx_process_config(processes):
    """Return (prefix, conf_path) from the first nginx master process, or defaults."""
    for p in processes:
        if 'nginx: master process ' not in p['command']: continue
        args=shlex.split(p['command'].split('nginx: master process ',1)[1])
        if not args: continue
        prefix='/etc/nginx';conf=None;i=1
        while i<len(args):
            if args[i]=='-p' and i+1<len(args): prefix=args[i+1];i+=2
            elif args[i].startswith('-p') and len(args[i])>2: prefix=args[i][2:];i+=1
            elif args[i]=='-c' and i+1<len(args): conf=args[i+1];i+=2
            elif args[i].startswith('-c') and len(args[i])>2: conf=args[i][2:];i+=1
            else:i+=1
        if not conf: conf=prefix.rstrip('/')+'/nginx.conf'
        return prefix,conf
    return '/etc/nginx','/etc/nginx/nginx.conf'


def collect_nginx_files(terminal, rows):
    """Collect every reachable nginx config file.

    Strategy: run nginx -T per running instance, then always sweep
    <prefix>/conf.d/*.conf and base64-read every conf.d file that -T did not
    yield (unloaded orphans or read failures). When no -T succeeded, the main
    config and its include targets are read directly and routes are parsed from
    the raw files on a best-effort basis.

    Returns (files, routes, warnings, notes, via_nginx_t); `warnings` are
    integrity issues that lower the collection status, `notes` are informational.
    """
    prefix, main_conf = nginx_process_config(rows)
    # Containerized nginx lives in its own mount namespace: host-side `nginx -T`
    # cannot see its config, so read the container files through /proc/<pid>/root.
    fs_root = nginx_mount_root(terminal, rows)
    commands = [] if fs_root else nginx_commands(rows)
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
        files, file_errors = collect_config_files(terminal, paths, command.split()[0])
        all_files.extend(files); warnings.extend(file_errors)
        collected.update(f['path'] for f in files)
        try:
            routes, parse_warnings = parse_nginx(dump)
            for route in routes: route['instance'] = command.split()[0]
            all_routes.extend(routes); warnings.extend(parse_warnings)
        except ValueError:
            warnings.append('Nginx 配置解析不完整')
    # conf.d 全量补采：-T 未列出（未加载）或读取失败的 .conf 一律直接读取
    missing = [p for p in list_conf_d(terminal, prefix, fs_root) if p not in collected]
    if missing:
        files, errors = collect_config_files(terminal, missing, 'folidev', fs_root)
        all_files.extend(files); warnings.extend(errors)
        collected.update(f['path'] for f in files)
        if via_nginx_t and files:
            notes.append('conf.d 中存在未被加载的配置文件，已一并采集：'+', '.join(f['path'] for f in files))
    if fs_root:
        notes.append('Nginx 运行于容器（独立挂载命名空间），已通过 '+fs_root+' 读取容器内配置')
    if not via_nginx_t:
        warnings.extend(read_nginx_without_t(terminal, prefix, main_conf, collected, all_files, all_routes, fs_root))
    return all_files, all_routes, warnings, notes, via_nginx_t


def read_nginx_without_t(terminal, prefix, main_conf, collected, all_files, all_routes, fs_root=''):
    """No nginx -T available: read the main config plus its include targets
    directly, then parse routes from the raw files. Returns integrity warnings."""
    warnings = []
    physical=fs_root+main_conf if fs_root and main_conf.startswith('/') else main_conf
    rc, main_text = terminal.run('cat '+shlex.quote(physical), timeout=30)
    include_patterns = [m.group(1).strip() for m in re.finditer(r'(?m)^\s*include\s+([^;]+);', main_text)] if rc == 0 else []
    read_paths = [main_conf] if main_conf not in collected else []
    for pattern in include_patterns:
        absolute = pattern if pattern.startswith('/') else prefix.rstrip('/')+'/'+pattern
        read_paths.extend(p for p in ls_glob(terminal, absolute, fs_root) if p not in collected and p not in read_paths)
    if read_paths:
        files, errors = collect_config_files(terminal, read_paths, 'folidev', fs_root)
        all_files.extend(files); warnings.extend(errors)
        collected.update(f['path'] for f in files)
    if not all_files:
        warnings.append('当前账号无法读取 Nginx 配置文件')
        return warnings
    main_file = next((f for f in all_files if f['path'] == main_conf), None)
    if main_file:
        dump_parts = ['# configuration file '+main_file['path']+':\n'+main_file['content']+'\n']
    else:
        abs_include = [p if p.startswith('/') else prefix.rstrip('/')+'/'+p for p in include_patterns] \
            or [prefix.rstrip('/')+'/conf.d/*.conf']
        dump_parts = ['# configuration file '+main_conf+':\nhttp { '+'; '.join('include '+p for p in abs_include)+'; }\n']
    for f in all_files:
        if f is main_file: continue
        dump_parts.append('# configuration file '+f['path']+':\n'+f['content']+'\n')
    try:
        routes, parse_warnings = parse_nginx('\n'.join(dump_parts))
        for route in routes: route['instance'] = 'folidev'
        all_routes.extend(routes); warnings.extend(parse_warnings)
    except ValueError:
        warnings.append('Nginx 配置解析不完整')
    return warnings


def resolve_elevation(terminal, ip, folidev_pw, root_alt1, root_alt2, ip_map):
    """Determine and perform elevation per the IP password map.

    Returns (sudo_status, sudo_reason, method, password, map_updated).
    """
    entry = ip_map.get(ip) if ip_map else None

    # 1. Map says password_error — skip all attempts.
    if entry and entry.get('status') == 'password_error':
        return 'password_error', '映射表标记密码错误，跳过 root 提权', None, None, False

    # 2. Map says normal — try the mapped method first.
    if entry and entry.get('status') == 'normal':
        method = entry.get('method')
        saved_pw = entry.get('password')
        if method == 'sudo-i' and saved_pw:
            status, reason = terminal.elevate(saved_pw)
        elif method == 'su' and saved_pw:
            status, reason = terminal.elevate_su(saved_pw)
        else:
            status, reason = 'denied', '映射表方法未知或密码缺失'
        if status in ('root', 'passwordless', 'password'):
            return status, reason, method, saved_pw, False
        # Mapped method failed — fall through to probe.

    # 3. Probe: sudo-i with folidev password first, then su -l with root passwords.
    #   sudo-i validates the *current user's* password (folidev), not root's.
    #   su -l validates the *root* password.
    #   So we never cross-try: folidev pw only for sudo-i, root pw only for su.
    if folidev_pw:
        status, reason = terminal.elevate(folidev_pw)
        print(f'  [{ip}] sudo -i 尝试 folidev 密码：{status} — {reason}', flush=True)
        if status in ('root', 'passwordless', 'password'):
            return status, reason, 'sudo-i', folidev_pw, True

    for root_pw in (root_alt1, root_alt2):
        if not root_pw:
            continue
        status, reason = terminal.elevate_su(root_pw)
        print(f'  [{ip}] su -l 尝试 root 密码：{status} — {reason}', flush=True)
        if status in ('root', 'passwordless', 'password'):
            return status, reason, 'su', root_pw, True

    return 'password_error', '提权失败，root 密码登录错误', None, None, True


def inspect_asset(asset, accounts, cookie, folidev_pw, root_alt1, root_alt2, ip_map, terminal_factory=Terminal):
    result={'checkedAt':datetime.now(timezone.utc).isoformat(), 'loginStatus':'cannot_login', 'reason':'没有授权 SSH 账号', 'processStatus':'not_collected','processes':[], 'appPorts':[], 'portStatus':'not_collected', 'webapps':{'status':'not_collected','apps':[]}, 'nginxStatus':'not_collected', 'nginxRoutes':[], 'nginxConfigurations':[], 'warnings':[], 'attempts':[]}
    ip = asset.get('ip', '')
    host = asset.get('hostname', '')
    tag = f'JumpServer [{ip}] {host}' if host else f'JumpServer [{ip}]'
    ssh=[a for a in accounts if a.get('protocol')=='ssh']
    ssh.sort(key=lambda a:a.get('username')!='folidev')
    for account in ssh:
        terminal=None
        try:
            terminal=terminal_factory(asset,account,cookie,folidev_pw);terminal.login()
        except Exception as exc:
            # Known safe messages only, never exception bodies / terminal output.
            reason=str(exc) if type(exc) in (ConnectionError,TimeoutError) else type(exc).__name__
            result['attempts'].append({'account':account.get('username',''), 'reason':reason})
            result['reason']=reason
            print(f'{tag} 登录失败（账号 {account.get("username","")}）：{reason}', flush=True)
            if terminal: terminal.close()
            continue
        result.update(loginStatus='can_login',reason='',account=account.get('username',''))
        print(f'{tag} 登录成功（账号 {account.get("username","")}）', flush=True)
        try:
            sudo_status, sudo_reason, method, used_pw, map_updated = resolve_elevation(
                terminal, ip, folidev_pw, root_alt1, root_alt2, ip_map)
            result['sudoStatus'] = sudo_status
            elevated = sudo_status in ('root','passwordless','password')
            sudo_label = {'root':'已是 root', 'passwordless':'sudo 免密', 'password':'sudo 密码成功',
                          'sudo_password_error':'sudo 密码错误', 'password_error':'无法提权',
                          'denied':'sudo 被拒', 'timeout':'sudo 超时'}.get(sudo_status, sudo_status)
            print(f'{tag} sudo：{sudo_label}', flush=True)
            if sudo_reason:
                result['warnings'].append(sudo_reason)
                result['nginxStatus']='sudo_password_error' if sudo_status=='password_error' else 'sudo_unavailable'
            if map_updated:
                result['elevationInfo'] = {'ip': ip, 'method': method, 'password': used_pw,
                    'status': 'normal' if method else 'password_error',
                    'verifiedAt': datetime.now(timezone.utc).isoformat()}
            rc, body=terminal.run(PS)
            rows, excluded, malformed=parse_processes(body) if rc==0 else ([],0,0)
            result.update(processes=rows,excludedSystemProcesses=excluded,processStatus='complete' if rc==0 and not malformed and elevated else 'partial' if rc==0 else 'failed')
            proc_label = {'complete':'完整', 'partial':'部分', 'failed':'失败'}.get(result['processStatus'], result['processStatus'])
            print(f'{tag} 进程：{proc_label}（{len(rows)} 条，排除系统进程 {excluded}）', flush=True)
            if not elevated: result['warnings'].append('sudo 不可用，仅采集当前账号可见进程，完整性未确认')
            if malformed: result['warnings'].append(f'{malformed} 行进程无法解析')
            usage = collect_usage(terminal)
            if usage: result['serverUsage'] = usage
            # Host listeners remain useful even when ps returns no visible rows.
            listeners = collect_listening_ports(terminal)
            result['portStatus'] = 'failed' if listeners is None else 'complete'
            if listeners is not None:
                port_uids, user_to_uid = collect_port_uids(terminal)
                infer_port_pids(listeners, rows, port_uids, user_to_uid)
                attach_process_ports(rows, listeners)
                result['appPorts'] = build_app_ports(rows, listeners)
                print(f'{tag} 监听端口：{len(listeners)} 个，应用×端口 {len(result["appPorts"])} 条', flush=True)
                if not elevated and listeners:
                    result['warnings'].append('sudo 不可用，主机应用监听端口已保留；其他用户的 PID 可能不可见，推断归属不代表已核验 socket 所有权')
                    unmatched = sum(1 for l in listeners if l['pid'] is None)
                    if unmatched:
                        result['warnings'].append(f'无 sudo 权限，{unmatched} 条监听记录无法关联到进程的直接所有权（已保留端口及推断候选）')
            else:
                result['warnings'].append('TCP 监听端口采集失败，不能据此判断没有监听')
                print(f'{tag} 监听端口：采集失败', flush=True)
            if is_10_58(asset.get('ip','')):
                specs = collect_specs(terminal)
                if specs:
                    result['specs'] = specs
                    print(f'{tag} 规格（10.58 网段）：{specs["cpu"]}C {specs["memoryMB"]//1024}G {specs["os"]}', flush=True)
                else:
                    print(f'{tag} 规格（10.58 网段）：未采集到', flush=True)
            result['webapps'] = collect_webapps(terminal)
            webapps = result['webapps']
            webapps_label = {'complete':'完整', 'partial':'部分', 'absent':'目录不存在', 'failed':'失败', 'not_collected':'未采集'}.get(webapps['status'], webapps['status'])
            print(f'{tag} Webapps：{webapps_label}（{len(webapps["apps"])} 个应用）', flush=True)
            commands=nginx_commands(rows)
            if not commands:
                result['nginxStatus']='not_running' if result['processStatus']!='failed' and not any(p['kind']=='Nginx' for p in rows) else 'unknown'
                nginx_label = {'not_running':'未运行', 'unknown':'无法判断'}.get(result['nginxStatus'], result['nginxStatus'])
                print(f'{tag} Nginx：{nginx_label}', flush=True)
            else:
                files, routes, warnings, notes, via_nginx_t = collect_nginx_files(terminal, rows)
                result['nginxConfigurations'].extend(files)
                result['nginxRoutes'].extend(routes)
                result['warnings'].extend(warnings); result['warnings'].extend(notes)
                if not elevated:
                    result['nginxStatus']='sudo_password_error' if sudo_status=='password_error' else 'sudo_unavailable'
                    if files:
                        result['nginxStatus']='partial'
                        result['warnings'].append('未获得 sudo 权限，nginx -T 以当前账号执行成功，完整性未确认' if via_nginx_t
                                                   else '未获得 sudo 权限，仅采集当前账号可见的 Nginx 配置文件，完整性未确认')
                elif via_nginx_t:
                    result['nginxStatus']='partial' if warnings else 'complete'
                else:
                    # -T 全部失败，但 conf.d/主配置直接读取仍有产出或全空
                    result['nginxStatus']='partial'
                nginx_label = {'complete':'完整', 'partial':'部分', 'sudo_password_error':'sudo 密码错误',
                               'sudo_unavailable':'sudo 不可用', 'not_running':'未运行', 'unknown':'无法判断',
                               'failed':'失败', 'not_collected':'未采集'}.get(result['nginxStatus'], result['nginxStatus'])
                print(f'{tag} Nginx：{nginx_label}（{len(routes)} 条路由，{len(files)} 份配置）', flush=True)
        except Exception as exc:
            msg = str(exc)
            # 敏感异常（含终端/原始输出）不应完整记录，ValueError 可能来自解析。
            safe_msg = msg if type(exc) in (ValueError, KeyError, IndexError,
                                            AttributeError, TypeError, OverflowError) else type(exc).__name__
            detail = safe_msg.replace('\n', ' ')[:200] if safe_msg else type(exc).__name__
            result['warnings'].append('采集中断：'+type(exc).__name__ + (f' — {detail}' if detail else ''))
            if result['processStatus']=='not_collected': result['processStatus']='failed'
            if result['nginxStatus']=='not_collected': result['nginxStatus']='failed'
            print(f'{tag} 采集中断：{type(exc).__name__} — {detail}', flush=True)
        finally: terminal.close()
        return result
    print(f'{tag} 没有授权的 SSH 账号，跳过', flush=True)
    return result


def collect_servers(assets, get, cookie, folidev_pw, root_alt1, root_alt2, ip_map, progress=None):
    def run(asset):
        try:
            path='/api/v1/perms/users/assets/'+asset['id']+'/system-users/'
            accounts=get(path)
            if isinstance(accounts,dict):
                rows=accounts.get('results',[])
                expected=accounts.get('count',len(rows))
                while len(rows)<expected:
                    page=get(path+'?limit=100&offset='+str(len(rows)))
                    if page.get('count')!=expected or not page.get('results'): raise ValueError('授权账号分页不完整')
                    rows.extend(page['results'])
                if len(rows)!=expected or len({a['id'] for a in rows})!=len(rows): raise ValueError('授权账号分页不完整')
                accounts=rows
            return inspect_asset(asset,accounts,cookie,folidev_pw,root_alt1,root_alt2,ip_map)
        except Exception as exc:
            return {'checkedAt':datetime.now(timezone.utc).isoformat(),'loginStatus':'cannot_login','reason':'读取授权账号失败：'+type(exc).__name__,'processStatus':'not_collected','processes':[],'appPorts':[],'nginxStatus':'not_collected','nginxRoutes':[],'warnings':[]}
    results={}; map_updates={}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix=inner_prefix()) as pool:
        futures={pool.submit(run,a):a for a in assets}
        for future in as_completed(futures):
            a=futures[future]; r=future.result(); results[a['id']]=r
            info=r.get('elevationInfo')
            if info: map_updates[info['ip']] = {k:v for k,v in info.items() if k!='ip'}
            # Per-asset completion summary — one line that closes the detailed trace above.
            login = '✓' if r.get('loginStatus') == 'can_login' else '✗'
            proc_count = len(r.get('processes') or [])
            nginx_st = r.get('nginxStatus', 'not_collected')
            webapps_count = len((r.get('webapps') or {}).get('apps') or [])
            webapps_st = (r.get('webapps') or {}).get('status', 'not_collected')
            print(f"JumpServer [{a.get('ip','')}] {a.get('hostname','')} — 完成（登录 {login}，进程 {proc_count}，Nginx {nginx_st}，Webapps {webapps_st} {webapps_count} 个）", flush=True)
            if progress: progress(len(results),len(assets))
    return results, map_updates
