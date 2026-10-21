"""跨机器发起方追踪：给定 Server A 上的端口，哪些远端 IP 在建连接，
以及在每个远端 IP 机器上，到底是哪个进程发起的这个连接。

阶段 2。配合 check_inspection.py（阶段 1 单机检查）使用。

典型调用::

    from runtime import server_inventory as si
    from runtime import origin_checker as oc
    import json

    snapshots = oc.load_snapshots("/data/versions/<id>")
    creds = oc.load_credentials("/data/secrets/cookies.json", "/data/secrets/ip-password-map.json")

    # 先拿到 A 机的 asset（从 snapshots 里）
    asset_a = oc.find_asset_by_ip(snapshots, "10.58.9.217")

    # 并发追踪
    report = oc.trace_origins(
        asset=asset_a,
        port=7084,
        snapshots=snapshots,
        creds=creds,
        origin_ips=None,   # None = 先在 A 机上跑 check_established_connections
        max_workers=5,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from server_inventory import (
    Terminal,
    resolve_elevation,
    collect_servers,
)
from check_inspection import check_established_connections


# ---------------------------------------------------------------------------
# 快照 / credentials 加载（CLI 友好：从文件读；service 调用时也可以直接传 dict）
# ---------------------------------------------------------------------------

def load_snapshots(version_dir: str | Path) -> dict[str, Any]:
    """Load jumpserver snapshot + DevOps snapshot for IP→asset lookups."""
    vd = Path(version_dir)
    jumpserver = json.loads((vd / "jumpserver-snapshot.json").read_text())
    devops = {}
    devops_path = vd / "snapshot.json"
    if devops_path.exists():
        devops = json.loads(devops_path.read_text())
    return {"jumpserver": jumpserver, "devops": devops}


def load_credentials(cookies_path: str | Path, ip_map_path: str | Path) -> dict[str, Any]:
    """Load saved cookies + IP password map from /data/secrets/."""
    cookies = json.loads(Path(cookies_path).read_text())
    ip_map = {}
    ip_map_p = Path(ip_map_path)
    if ip_map_p.exists():
        ip_map = json.loads(ip_map_p.read_text())
    return {
        "jumpserver_cookie": cookies.get("jumpserver", {}).get("value", ""),
        "folidev_password": cookies.get("folidev_password", {}).get("value", "") if isinstance(cookies.get("folidev_password"), dict) else cookies.get("folidev_password", ""),
        "root_alt1": cookies.get("root_alt1_password", {}).get("value", "") if isinstance(cookies.get("root_alt1_password"), dict) else cookies.get("root_alt1_password", ""),
        "root_alt2": cookies.get("root_alt2_password", {}).get("value", "") if isinstance(cookies.get("root_alt2_password"), dict) else cookies.get("root_alt2_password", ""),
        "ip_map": ip_map,
    }


# ---------------------------------------------------------------------------
# IP → JumpServer asset 查找
# ---------------------------------------------------------------------------

def find_asset_by_ip(snapshots: dict[str, Any], ip: str) -> dict | None:
    """Find the JumpServer asset whose IP field matches ``ip``.

    JumpServer ``asset.ip`` can be a space/comma separated list — we trim
    and split, then exact-match each piece.  Returns the first match, or None.
    """
    assets = snapshots.get("jumpserver", {}).get("assets", [])
    for asset in assets:
        asset_ip = str(asset.get("ip", ""))
        for piece in re.split(r'[\s,]+', asset_ip):
            if piece.strip() == ip.strip():
                return asset
    return None


# ---------------------------------------------------------------------------
# 单台远端机器上查发起进程
# ---------------------------------------------------------------------------

# ss -tnp: show established where src=target AND dport=目标端口
ORIGIN_CHECK_COMMAND = (
    'target_ip=$1; target_port=$2; '
    'echo "ORIGIN_TARGET\t$target_ip:$target_port"; '
    'ss -H -tnp state established "( src = $target_ip and dport = :$target_port )" 2>/dev/null || '
    'ss -H -tnp state established "( dport = :$target_port )" 2>/dev/null | grep -F "$target_ip:" ; '
    'echo "ORIGIN_DONE\tdone"'
)


def _parse_ss_for_origin(body: str) -> list[dict]:
    """Parse ss output for outbound connections matching our target.

    ss -tnp columns: State Recv-Q Send-Q Local Address:Port Peer Address:Port [Process]
    We want rows where Peer Address contains our target IP.
    """
    results = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("ORIGIN_"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        state = parts[0]
        local = parts[3]
        peer = parts[4]

        local_ip, local_port = _split_addr_port_safe(local)
        peer_ip, peer_port = _split_addr_port_safe(peer)

        pid = None
        comm = None
        cmdline = None
        proc_field = ' '.join(parts[5:]) if len(parts) > 5 else ''
        m = re.search(r'pid=(\d+)', proc_field)
        if m:
            pid = int(m.group(1))
        cm = re.search(r'"([^"]+)"', proc_field)
        if cm:
            comm = cm.group(1)

        results.append({
            "state": state,
            "local_ip": local_ip,
            "local_port": local_port,
            "peer_ip": peer_ip,
            "peer_port": peer_port,
            "pid": pid,
            "comm": comm,
            "cmdline": None,  # filled in later via /proc
        })
    return results


def _split_addr_port_safe(s: str) -> tuple[str | None, str | None]:
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
    idx = s.rfind(':')
    if idx == -1:
        return s, None
    return s[:idx], s[idx + 1:]


# 给定 PID，读 /proc/PID/{cmdline,comm,cwd} —— 在远端机器上执行的 bash 命令
PROC_DETAIL_COMMAND = (
    'pid=$1; '
    'if [ ! -d "/proc/$pid" ]; then echo "PROC_MISSING\t$pid"; exit 0; fi; '
    'comm=$(cat /proc/$pid/comm 2>/dev/null | tr "\\n" " "); '
    'cmd=$(tr "\\0" " " < /proc/$pid/cmdline 2>/dev/null | cut -c1-500); '
    'cwd=$(readlink -f /proc/$pid/cwd 2>/dev/null || echo ""); '
    'echo "PROC_DETAIL\t$pid\t${comm//$"\\t"/ }\t$cwd\t${cmd//$"\\t"/ }"'
)


def _enrich_with_proc_details(terminal, rows: list[dict], timeout=20) -> list[dict]:
    """For each row with a PID, read /proc/<pid> on the remote machine."""
    for row in rows:
        pid = row.get("pid")
        if not pid:
            continue
        cmd = f"bash -c '{PROC_DETAIL_COMMAND}' _ {pid}"
        try:
            rc, body = terminal.run(cmd, timeout=timeout)
            for line in body.splitlines():
                if line.startswith("PROC_DETAIL\t"):
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        row["comm"] = parts[2].strip() or row.get("comm")
                        row["cwd"] = parts[3].strip()
                        row["cmdline"] = parts[4].strip()
                        row["cmdline_truncated"] = len(parts[4]) >= 500
                elif line.startswith("PROC_MISSING\t"):
                    row["pid"] = None  # process exited between ss and /proc read
        except Exception as exc:
            row["proc_lookup_error"] = type(exc).__name__
    return rows


# ---------------------------------------------------------------------------
# 单台远端机器的完整 origin 检查
# ---------------------------------------------------------------------------

def check_origin_on_machine(
    asset: dict,
    target_ip: str,
    target_port: int,
    creds: dict,
    terminal_factory=Terminal,
) -> dict:
    """Open a JumpServer session to ``asset`` and find processes that are
    making outbound connections to ``target_ip:target_port``.

    Returns::

        {
          "asset_id": "...",
          "asset_ip": "10.58.1.5",
          "asset_hostname": "...",
          "loginStatus": "can_login" | "cannot_login",
          "sudoStatus": "root" | ...,
          "connections": [...],
          "warnings": [...]
        }
    """
    asset_id = asset.get("id")
    asset_ip = asset.get("ip", "")
    hostname = asset.get("hostname", "")
    warnings = []

    # 1. 找 SSH 授权账号
    from urllib.parse import urlencode
    import websocket
    from server_inventory import BASE as JUMPSERVER_BASE
    import json as _json
    from http.client import HTTPConnection

    cookie = creds.get("jumpserver_cookie", "")
    if not cookie:
        return _origin_error(asset_id, asset_ip, hostname, "no JumpServer cookie")

    # 用 JumpServer REST API 拿这个 asset 的授权账号
    accounts = _fetch_system_users(asset_id, cookie)
    if not accounts:
        return _origin_error(asset_id, asset_ip, hostname, "no authorized SSH accounts")

    ssh_accounts = [a for a in accounts if a.get("protocol") == "ssh"]
    ssh_accounts.sort(key=lambda a: a.get("username") != "folidev")
    if not ssh_accounts:
        return _origin_error(asset_id, asset_ip, hostname, "no SSH accounts")

    # 2. 登录 + 提权
    terminal = None
    try:
        terminal = terminal_factory(asset, ssh_accounts[0], cookie, creds.get("folidev_password", ""))
        terminal.login()
    except Exception as exc:
        reason = type(exc).__name__
        return _origin_error(asset_id, asset_ip, hostname, f"login failed: {reason}")

    try:
        sudo_status, sudo_reason, _method, _pw, _map_upd = resolve_elevation(
            terminal, asset_ip,
            creds.get("folidev_password", ""),
            creds.get("root_alt1", ""),
            creds.get("root_alt2", ""),
            creds.get("ip_map", {}),
        )
        if sudo_reason:
            warnings.append(sudo_reason)

        # 3. 查发起到 target_ip:target_port 的连接
        cmd = f"bash -c '{ORIGIN_CHECK_COMMAND}' _ {json.dumps(target_ip)} {json.dumps(str(target_port))}"
        rc, body = terminal.run(cmd, timeout=30)

        rows = _parse_ss_for_origin(body)
        if rows and sudo_status in ("root", "passwordless", "password"):
            _enrich_with_proc_details(terminal, rows)

        return {
            "asset_id": asset_id,
            "asset_ip": asset_ip,
            "asset_hostname": hostname,
            "loginStatus": "can_login",
            "sudoStatus": sudo_status,
            "connections": rows,
            "warnings": warnings,
        }
    except Exception as exc:
        return {
            "asset_id": asset_id,
            "asset_ip": asset_ip,
            "asset_hostname": hostname,
            "loginStatus": "can_login",
            "sudoStatus": sudo_status if 'sudo_status' in dir() else "unknown",
            "connections": [],
            "warnings": warnings + [f"check interrupted: {type(exc).__name__}"],
        }
    finally:
        if terminal:
            terminal.close()


def _origin_error(asset_id, asset_ip, hostname, reason):
    return {
        "asset_id": asset_id,
        "asset_ip": asset_ip,
        "asset_hostname": hostname,
        "loginStatus": "cannot_login",
        "sudoStatus": "unknown",
        "connections": [],
        "warnings": [reason],
    }


def _fetch_system_users(asset_id, cookie):
    """Hit JumpServer REST API for SSH accounts authorized on an asset."""
    try:
        host = JUMPSERVER_BASE.replace("http://", "").split("/")[0]
        conn = HTTPConnection(host, timeout=10)
        path = f"/api/v1/perms/users/assets/{asset_id}/system-users/?limit=100"
        conn.request("GET", path, headers={
            "Cookie": cookie,
            "Accept": "application/json",
        })
        resp = conn.getresponse()
        body = _json.loads(resp.read().decode())
        conn.close()
        if isinstance(body, dict):
            results = body.get("results", [])
            while body.get("next"):
                # Pagination — but for our immediate needs, one page is fine.
                break
            return results
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 顶层 orchestrator
# ---------------------------------------------------------------------------

def trace_origins(
    asset: dict,
    port: int,
    snapshots: dict,
    creds: dict,
    origin_ips: list[str] | None = None,
    max_workers: int = 5,
    asset_terminal_factory=Terminal,
) -> dict:
    """Run the full two-phase trace.

    Phase 1 (on ``asset``): if ``origin_ips`` is None, login to the target
    asset first and call check_established_connections to discover who is
    connected.  Otherwise use the caller-supplied IP list directly.

    Phase 2 (on each origin IP): find the JumpServer asset for each IP and
    concurrently inspect the outbound process.

    Returns a report dict suitable for JSON serialization.
    """
    report: dict = {
        "target": {
            "asset_id": asset.get("id"),
            "asset_ip": asset.get("ip"),
            "asset_hostname": asset.get("hostname"),
            "port": port,
        },
        "generatedAt": time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        "phase1_connections": [],
        "phase2_origins": [],
        "unresolved_ips": [],
        "origin_ips_not_in_jumpserver": [],
    }

    # --- Phase 1 ---
    if origin_ips is None:
        cookie = creds.get("jumpserver_cookie", "")
        origin_ips = []
        try:
            # Same as check_origin_on_machine — login to target asset first.
            accounts = _fetch_system_users(asset.get("id"), cookie)
            ssh_accounts = [a for a in accounts if a.get("protocol") == "ssh"]
            ssh_accounts.sort(key=lambda a: a.get("username") != "folidev")
            if ssh_accounts:
                t = asset_terminal_factory(asset, ssh_accounts[0], cookie, creds.get("folidev_password", ""))
                t.login()
                sudo_status, _, _, _, _ = resolve_elevation(
                    t, asset.get("ip", ""),
                    creds.get("folidev_password", ""),
                    creds.get("root_alt1", ""),
                    creds.get("root_alt2", ""),
                    creds.get("ip_map", {}),
                )
                conn_info = check_established_connections(t, port, timeout=30)
                report["phase1_connections"] = conn_info.get("connections", [])
                origin_ips = list({c["remote_ip"] for c in report["phase1_connections"] if c.get("remote_ip")})
                t.close()
        except Exception as exc:
            report["phase1_error"] = f"{type(exc).__name__}: {exc}"

    # Dedupe
    origin_ips = list(dict.fromkeys(origin_ips))

    # --- Phase 2 ---
    # 按 IP 分组：JumpServer 已知 vs 未知
    known = []
    unknown_ips = []
    for ip in origin_ips:
        a = find_asset_by_ip(snapshots, ip)
        if a:
            known.append((ip, a))
        else:
            unknown_ips.append(ip)

    report["origin_ips_not_in_jumpserver"] = unknown_ips

    # 并发查已知资产
    lock = threading.Lock()

    def _worker(ip, asset):
        result = check_origin_on_machine(asset, report["target"]["asset_ip"], port, creds, asset_terminal_factory)
        result["queried_ip"] = ip
        with lock:
            report["phase2_origins"].append(result)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="origin") as pool:
        futures = [pool.submit(_worker, ip, a) for ip, a in known]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                report["warnings"] = report.get("warnings", []) + [f"worker error: {type(exc).__name__}"]

    return report
