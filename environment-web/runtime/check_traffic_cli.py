#!/usr/bin/env python3
"""主动核验 CLI：两阶段跨机器追踪。

在容器内直接跑::

    python3 runtime/check_traffic_cli.py trace --ip 10.58.9.217 --port 7084 --version <id>
    python3 runtime/check_traffic_cli.py connections --ip 10.58.9.217 --port 7084 --version <id>
    python3 runtime/check_traffic_cli.py traffic --ip 10.58.9.217 --port 7084 --version <id> --duration 10
    python3 runtime/check_traffic_cli.py logs --ip 10.58.9.217 --app fosun-cashier --version <id>
    python3 runtime/check_traffic_cli.py usage --ip 10.58.9.217 --path /var/www/webapps/fosun-cashier --version <id>

--version 可以省略；省略时读 $ENVSCOPE_DATA/current.json 的当前版本。
--cookies 和 --ip-map 也省略时默认读 /data/secrets/cookies.json 和 /data/secrets/ip-password-map.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure runtime/ is on sys.path so sibling modules import correctly
_RUNTIME_DIR = Path(__file__).resolve().parent
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))

import server_inventory as si  # noqa: E402
from check_inspection import (  # noqa: E402
    check_established_connections,
    check_traffic_snapshot,
    check_log_activity,
    check_directory_usage,
)
from origin_checker import (  # noqa: E402
    load_snapshots,
    load_credentials,
    find_asset_by_ip,
    trace_origins,
)


DEFAULT_DATA = os.environ.get("ENVSCOPE_DATA", "/data")


# ---------------------------------------------------------------------------
# 找到版本目录 + asset
# ---------------------------------------------------------------------------

def _resolve_version(version_arg: str | None) -> Path:
    if version_arg:
        return Path(DEFAULT_DATA) / "versions" / version_arg
    current = Path(DEFAULT_DATA) / "current.json"
    if not current.exists():
        print("ERROR: no --version and no current.json", file=sys.stderr)
        sys.exit(2)
    data = json.loads(current.read_text())
    return Path(DEFAULT_DATA) / "versions" / data["id"]


def _find_asset(snapshots: dict, ip: str):
    asset = find_asset_by_ip(snapshots, ip)
    if not asset:
        print(f"ERROR: no JumpServer asset matches IP {ip}", file=sys.stderr)
        sys.exit(2)
    return asset


def _open_terminal(asset, creds, elevate=True):
    """Open a JumpServer terminal to ``asset`` and optionally elevate to root."""
    cookie = creds["jumpserver_cookie"]
    accounts = _fetch_system_users(asset["id"], cookie)
    ssh = [a for a in accounts if a.get("protocol") == "ssh"]
    ssh.sort(key=lambda a: a.get("username") != "folidev")
    if not ssh:
        print(f"ERROR: asset {asset.get('ip')} has no SSH accounts", file=sys.stderr)
        sys.exit(2)

    t = si.Terminal(asset, ssh[0], cookie, creds.get("folidev_password", ""))
    try:
        t.login()
    except Exception as exc:
        print(f"ERROR: login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)

    if elevate:
        sudo_status, reason, _, _, _ = si.resolve_elevation(
            t, asset.get("ip", ""),
            creds.get("folidev_password", ""),
            creds.get("root_alt1", ""),
            creds.get("root_alt2", ""),
            creds.get("ip_map", {}),
        )
        if sudo_status not in ("root", "passwordless", "password"):
            print(f"WARN: elevation incomplete ({sudo_status}: {reason}) — some checks may fail", file=sys.stderr)

    return t


def _fetch_system_users(asset_id: str, cookie: str):
    from http.client import HTTPConnection
    host = si.BASE.replace("http://", "").split("/")[0]
    conn = HTTPConnection(host, timeout=10)
    conn.request("GET", f"/api/v1/perms/users/assets/{asset_id}/system-users/?limit=100",
                 headers={"Cookie": cookie, "Accept": "application/json"})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    if isinstance(body, dict):
        return body.get("results", [])
    return []


# ---------------------------------------------------------------------------
# 各子命令
# ---------------------------------------------------------------------------

def cmd_connections(args):
    snapshots = load_snapshots(_resolve_version(args.version))
    creds = load_credentials(Path(DEFAULT_DATA) / "secrets" / "cookies.json",
                             Path(DEFAULT_DATA) / "secrets" / "ip-password-map.json")
    asset = _find_asset(snapshots, args.ip)
    t = _open_terminal(asset, creds)
    try:
        result = check_established_connections(t, args.port)
    finally:
        t.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_traffic(args):
    snapshots = load_snapshots(_resolve_version(args.version))
    creds = load_credentials(Path(DEFAULT_DATA) / "secrets" / "cookies.json",
                             Path(DEFAULT_DATA) / "secrets" / "ip-password-map.json")
    asset = _find_asset(snapshots, args.ip)
    t = _open_terminal(asset, creds)
    try:
        result = check_traffic_snapshot(t, args.port, duration=args.duration,
                                        timeout=max(args.duration + 30, 60))
    finally:
        t.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_logs(args):
    snapshots = load_snapshots(_resolve_version(args.version))
    creds = load_credentials(Path(DEFAULT_DATA) / "secrets" / "cookies.json",
                             Path(DEFAULT_DATA) / "secrets" / "ip-password-map.json")
    asset = _find_asset(snapshots, args.ip)
    t = _open_terminal(asset, creds, elevate=False)  # 日志通常普通用户可读
    try:
        result = check_log_activity(t, args.app)
    finally:
        t.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_usage(args):
    snapshots = load_snapshots(_resolve_version(args.version))
    creds = load_credentials(Path(DEFAULT_DATA) / "secrets" / "cookies.json",
                             Path(DEFAULT_DATA) / "secrets" / "ip-password-map.json")
    asset = _find_asset(snapshots, args.ip)
    t = _open_terminal(asset, creds)
    try:
        result = check_directory_usage(t, args.path)
    finally:
        t.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_trace(args):
    version_dir = _resolve_version(args.version)
    snapshots = load_snapshots(version_dir)
    creds = load_credentials(Path(DEFAULT_DATA) / "secrets" / "cookies.json",
                             Path(DEFAULT_DATA) / "secrets" / "ip-password-map.json")
    asset = _find_asset(snapshots, args.ip)
    origin_ips = None
    if args.origins:
        origin_ips = [x.strip() for x in args.origins.split(",") if x.strip()]
    report = trace_origins(asset, args.port, snapshots, creds,
                           origin_ips=origin_ips, max_workers=args.workers)
    print(json.dumps(report, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="check-traffic", description="两阶段跨机器主动核验 CLI")
    p.add_argument("--version", help="版本目录 ID；省略则读 current.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("connections", help="查入站连接（谁连到了我的端口）")
    c.add_argument("--ip", required=True, help="被检查服务器 IP")
    c.add_argument("--port", required=True, type=int)
    c.set_defaults(func=cmd_connections)

    t = sub.add_parser("traffic", help="流量快照（tcpdump 抓包或 ss 采样）")
    t.add_argument("--ip", required=True)
    t.add_argument("--port", required=True, type=int)
    t.add_argument("--duration", type=int, default=10, help="采样秒数（默认 10）")
    t.set_defaults(func=cmd_traffic)

    l = sub.add_parser("logs", help="日志活跃度检查")
    l.add_argument("--ip", required=True)
    l.add_argument("--app", required=True, help="应用名（/var/www/webapps/{app}/logs）")
    l.set_defaults(func=cmd_logs)

    u = sub.add_parser("usage", help="目录占用检查")
    u.add_argument("--ip", required=True)
    u.add_argument("--path", required=True, help="要检查的目录")
    u.set_defaults(func=cmd_usage)

    tr = sub.add_parser("trace", help="两阶段完整追踪（A 机入站连接 → 各 B 机发起进程）")
    tr.add_argument("--ip", required=True, help="被检查服务器 A 的 IP")
    tr.add_argument("--port", required=True, type=int, help="A 机上的应用端口")
    tr.add_argument("--origins", help="逗号分隔的远端 IP 列表（可选，省略则自动在 A 机上查）")
    tr.add_argument("--workers", type=int, default=5, help="并发远端会话数（默认 5）")
    tr.set_defaults(func=cmd_trace)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
