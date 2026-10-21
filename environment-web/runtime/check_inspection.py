"""主动核验：连接、流量、日志、目录占用。

每个函数接受一个 Terminal（JumpServer Koko 会话）和参数，
返回结构化的 dict。函数本身不处理会话生命周期——调用方负责
登录 / 提权 / close。

这是阶段 1：在被检查方机器（Server A）上本地采集。
跨机器追踪（阶段 2）在 origin_checker.py 里。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 1. 已建立的入站连接（谁连过来了）
# ---------------------------------------------------------------------------

def check_established_connections(terminal, port, timeout=30):
    """List currently established inbound TCP connections to ``port``.

    Uses ``ss -tnp state established "( sport = :PORT )"`` — the ``-p`` flag
    needs root to show ``pid=`` ownership.  When it is missing we still get
    remote IPs but cannot attribute each connection to a local process.

    Returns::

        {
          "port": 7084,
          "has_pid": True,
          "connections": [
            {"remote_ip": "10.58.1.5", "remote_port": 54321, "state": "ESTAB",
             "pid": 1234, "comm": "java"}
          ]
        }
    """
    if not str(port).isdigit() or not (1 <= int(port) <= 65535):
        return {"port": port, "error": "invalid port", "connections": []}

    # ss filter needs shell-level quotes — pass sport in () form so IPv6
    # wildcard listens also match.
    cmd = f'ss -H -tnp state established "( sport = :{port} )"'
    rc, body = terminal.run(cmd, timeout=timeout)

    conns = []
    has_pid = False
    # ss -tnp columns (variable): State Recv-Q Send-Q Local Address:Port Peer Address:Port [Process]
    # e.g. "ESTAB 0 0 10.58.9.217:7084 10.58.1.5:54321 users:(("java",pid=384,fd=220))"
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('State'):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        state = parts[0]
        local = parts[3]
        peer = parts[4]

        # Parse peer IP:port — works for IPv4; IPv6 peer comes as [::1]:port
        peer_ip, peer_port = _split_addr_port(peer)
        if not peer_ip:
            continue

        pid = None
        comm = None
        proc_field = ' '.join(parts[5:]) if len(parts) > 5 else ''
        m = re.search(r'pid=(\d+)', proc_field)
        if m:
            has_pid = True
            pid = int(m.group(1))
        cm = re.search(r'"([^"]+)"', proc_field)
        if cm:
            comm = cm.group(1)

        conns.append({
            "remote_ip": peer_ip,
            "remote_port": int(peer_port) if peer_port and peer_port.isdigit() else None,
            "state": state,
            "pid": pid,
            "comm": comm,
        })

    return {"port": int(port), "has_pid": has_pid, "connections": conns}


# ---------------------------------------------------------------------------
# 2. 流量快照（tcpdump 抓包 或 ss 两次采样对比）
# ---------------------------------------------------------------------------

def check_traffic_snapshot(terminal, port, duration=10, timeout=None):
    """Capture inbound TCP traffic on ``port`` for ``duration`` seconds.

    Prefers tcpdump when available (requires root).  Falls back to two
    ``ss`` samples separated by ``duration`` seconds — works without root
    but only sees **currently established** connections, not short-lived
    requests or data volume.

    Returns::

        {
          "mode": "tcpdump" | "ss-polling",
          "duration": 10,
          "total_packets": 0,
          "data_packets": 0,
          "data_bytes": 0,
          "top_sources": [{"ip": "10.58.1.5", "packets": 120, "bytes": 54321}],
          "warning": None | "tcpdump not installed; falling back to ss polling"
        }
    """
    if timeout is None:
        timeout = max(duration + 30, 60)

    # --- tcpdump path ---
    rc, have_tcpdump = terminal.run("command -v tcpdump >/dev/null 2>&1 && echo yes || echo no", timeout=10)
    if rc == 0 and 'yes' in have_tcpdump:
        # tcpdump -nn -tttt -q -s 96 -c MAX -i any "tcp dst port PORT"
        # Use -c with a large cap to auto-stop, plus timeout safety.
        cap = 20000
        cmd = (
            f"timeout {duration} tcpdump -i any -nn -tttt -q -l -s 96 -c {cap} "
            f"'tcp dst port {port}' 2>/dev/null"
        )
        rc, body = terminal.run(cmd, timeout=timeout)

        packets = 0
        data_packets = 0
        data_bytes = 0
        sources: dict[str, dict] = {}

        for line in body.splitlines():
            if ' IP ' not in line and 'IP6 ' not in line:
                continue
            packets += 1
            m = re.search(r'tcp (\d+)', line)
            if m:
                b = int(m.group(1))
                if b > 0:
                    data_packets += 1
                    data_bytes += b
                    # source IP is before the last ">"
                    idx = line.rfind('>')
                    if idx > 0:
                        src_part = line[:idx].split()[-1]
                        src_ip = re.sub(r'\.[0-9]+$', '', src_part)
                        if src_ip not in sources:
                            sources[src_ip] = {"ip": src_ip, "packets": 0, "bytes": 0}
                        sources[src_ip]["packets"] += 1
                        sources[src_ip]["bytes"] += b

        top = sorted(sources.values(), key=lambda s: s["bytes"], reverse=True)[:10]
        return {
            "mode": "tcpdump",
            "duration": duration,
            "total_packets": packets,
            "data_packets": data_packets,
            "data_bytes": data_bytes,
            "top_sources": top,
            "warning": None,
        }

    # --- ss polling fallback (no root / no tcpdump) ---
    cmd = f"ss -H -tn state established '( sport = :{port} )' | awk '{{print $4, $5}}'"
    rc1, snap1 = terminal.run(cmd, timeout=15)
    time.sleep(duration)
    rc2, snap2 = terminal.run(cmd, timeout=15)

    def _parse(body):
        pairs = []
        for line in body.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                pairs.append((parts[0].strip(), parts[1].strip()))
        return set(pairs)

    s1 = _parse(snap1) if rc1 == 0 else set()
    s2 = _parse(snap2) if rc2 == 0 else set()
    new = s2 - s1
    closed = s1 - s2

    return {
        "mode": "ss-polling",
        "duration": duration,
        "total_packets": 0,
        "data_packets": 0,
        "data_bytes": 0,
        "top_sources": [{"ip": _split_addr_port(peer)[0], "packets": 0, "bytes": 0}
                        for _, peer in sorted(new)],
        "new_connections": len(new),
        "closed_connections": len(closed),
        "warning": "tcpdump not installed; falling back to ss polling (no byte counts)",
    }


# ---------------------------------------------------------------------------
# 3. 日志活跃度（/var/www/webapps/{app}/logs）
# ---------------------------------------------------------------------------

LOG_CHECK_COMMAND = (
    'app=$1; '
    'dir=/var/www/webapps/$app/logs; '
    'if [ ! -d "$dir" ]; then echo "LOG_STATUS\tno_dir"; exit 0; fi; '
    'echo "LOG_STATUS\texists"; '
    # Top 10 most recent files with mtime epoch + size
    'ls -lt --time-style=+%s "$dir" 2>/dev/null | head -11 | tail -10 | while read perms links owner group size mtime name; do '
    '  case "$perms" in -*);; *) continue;; esac; '
    '  if [ "$mtime" != "" ]; then echo "LOG_FILE\t$mtime\t$size\t$name"; fi; '
    'done; '
    # Total lines in *.log files (gzip-friendly: just plain .log)
    'total=0; errors=0; requests=0; '
    'for f in "$dir"/*.log; do '
    '  [ -f "$f" ] || continue; '
    '  lines=$(wc -l < "$f" 2>/dev/null || echo 0); '
    '  errs=$(grep -c -iE "error|exception|fail" "$f" 2>/dev/null || echo 0); '
    '  reqs=$(grep -c -iE "GET |POST |PUT |DELETE " "$f" 2>/dev/null || echo 0); '
    '  total=$((total + lines)); errors=$((errors + errs)); requests=$((requests + reqs)); '
    'done; '
    'echo "LOG_COUNTS\t$total\t$errors\t$requests"'
)


def check_log_activity(terminal, app_name, timeout=30):
    """Inspect ``/var/www/webapps/{app_name}/logs`` for activity signals.

    Runs LOG_CHECK_COMMAND with the app name as ``$1``.  Parses the
    prefixed output lines so stderr noise doesn't break things.

    Returns::

        {
          "app": "fosun-cashier",
          "directory": "/var/www/webapps/fosun-cashier/logs",
          "exists": True,
          "files": [{"name": "access.log", "mtime_epoch": 1728600000, "size": 1234567}],
          "total_lines": 98000,
          "error_lines": 42,
          "request_lines": 54000
        }
    """
    cmd = f"bash -c {json.dumps(LOG_CHECK_COMMAND)} _ {json.dumps(app_name)}"
    # Simpler bash -c form — avoid JSON quoting mess:
    cmd = f"bash -c '{LOG_CHECK_COMMAND}' _ {json.dumps(app_name)}"
    rc, body = terminal.run(cmd, timeout=timeout)

    result = {
        "app": app_name,
        "directory": f"/var/www/webapps/{app_name}/logs",
        "exists": False,
        "files": [],
        "total_lines": 0,
        "error_lines": 0,
        "request_lines": 0,
    }

    for line in body.splitlines():
        if line.startswith("LOG_STATUS\t"):
            status = line.split('\t', 1)[1].strip()
            result["exists"] = (status == "exists")
        elif line.startswith("LOG_FILE\t"):
            parts = line.split('\t')
            if len(parts) >= 4:
                try:
                    result["files"].append({
                        "name": parts[3].strip(),
                        "mtime_epoch": int(parts[1]),
                        "size": int(parts[2]),
                    })
                except ValueError:
                    pass
        elif line.startswith("LOG_COUNTS\t"):
            parts = line.split('\t')
            if len(parts) >= 4:
                try:
                    result["total_lines"] = int(parts[1])
                    result["error_lines"] = int(parts[2])
                    result["request_lines"] = int(parts[3])
                except ValueError:
                    pass

    return result


# ---------------------------------------------------------------------------
# 4. 目录占用（精简的 check_usage.sh — 只保留"是否被进程/配置引用"）
# ---------------------------------------------------------------------------

# 4a. lsof +D (needs root or matching user) — only return PIDs found
# 4b. Nginx config grep
# 4c. /var/www/webapps/{app} itself — does it exist + mtime
USAGE_COMMAND = (
    'dir=$1; '
    'echo "USAGE_DIR\t$dir"; '
    'if command -v lsof >/dev/null 2>&1; then '
    '  out=$(lsof -t +D "$dir" 2>/dev/null | sort -u); '
    '  if [ -n "$out" ]; then echo "USAGE_LSOF\t$out"; fi; '
    'else echo "USAGE_LSOF\tunavailable"; fi; '
    'if command -v nginx >/dev/null 2>&1; then '
    '  t=$(mktemp); nginx -T > "$t" 2>/dev/null || true; '
    '  n=$(grep -cF -- "$dir" "$t" 2>/dev/null || echo 0); '
    '  echo "USAGE_NGINX\t$n"; rm -f "$t"; '
    'else echo "USAGE_NGINX\t0"; fi; '
    'if [ -d "$dir" ]; then '
    '  echo -n "USAGE_DIR_MTIME\t"; stat -c %Y "$dir" 2>/dev/null || echo 0; '
    '  echo -n "USAGE_DIR_SIZE\t"; du -sb "$dir" 2>/dev/null | cut -f1 || echo 0; '
    'else echo "USAGE_DIR_MTIME\tmissing"; echo "USAGE_DIR_SIZE\tmissing"; fi; '
    'echo "USAGE_DONE\tdone"'
)


def check_directory_usage(terminal, path, timeout=30):
    """Check whether ``path`` is referenced by running processes or configs.

    Returns::

        {
          "path": "/var/www/webapps/fosun-cashier",
          "exists": True,
          "mtime_epoch": 1728600000,
          "size_bytes": 123456789,
          "lsof_pids": [1234, 5678],
          "lsof_unavailable": False,
          "nginx_refs": 0
        }
    """
    cmd = f"bash -c '{USAGE_COMMAND}' _ {json.dumps(path)}"
    rc, body = terminal.run(cmd, timeout=timeout)

    result = {
        "path": path,
        "exists": False,
        "mtime_epoch": None,
        "size_bytes": None,
        "lsof_pids": [],
        "lsof_unavailable": False,
        "nginx_refs": 0,
    }

    for line in body.splitlines():
        if line.startswith("USAGE_DIR\t"):
            pass  # echoed $1 for confirmation
        elif line.startswith("USAGE_LSOF\t"):
            val = line.split('\t', 1)[1].strip()
            if val == "unavailable":
                result["lsof_unavailable"] = True
            elif val:
                result["lsof_pids"] = [int(p) for p in val.split() if p.isdigit()]
        elif line.startswith("USAGE_NGINX\t"):
            val = line.split('\t', 1)[1].strip()
            try:
                result["nginx_refs"] = int(val)
            except ValueError:
                pass
        elif line.startswith("USAGE_DIR_MTIME\t"):
            val = line.split('\t', 1)[1].strip()
            if val.isdigit():
                result["exists"] = True
                result["mtime_epoch"] = int(val)
            elif val == "missing":
                result["exists"] = False
        elif line.startswith("USAGE_DIR_SIZE\t"):
            val = line.split('\t', 1)[1].strip()
            if val.isdigit():
                result["size_bytes"] = int(val)

    return result


# ---------------------------------------------------------------------------
# 辅助：IP:port 解析
# ---------------------------------------------------------------------------

def _split_addr_port(s: str):
    """Split an ``IP:PORT`` string.  Handles bare IP and ``[v6]:port``."""
    s = s.strip()
    if not s:
        return None, None
    if s.startswith('['):
        end = s.find(']')
        if end == -1:
            return None, None
        ip = s[1:end]
        rest = s[end + 1:]
        if rest.startswith(':'):
            return ip, rest[1:]
        return ip, None
    # IPv4 / bare IP / host — split on last colon
    idx = s.rfind(':')
    if idx == -1:
        return s, None
    return s[:idx], s[idx + 1:]
