"""Run the existing read-only TCP detector in the application container."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application_check import inspect_application


def node_event(instance, status, reason=None):
    print(json.dumps({'event':'node', 'instanceId':instance, 'status':status, 'reason':reason}, ensure_ascii=False), flush=True)


def collect(app_id, environment, output, progress=print):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    version = os.environ['ENVSCOPE_INSPECTION_VERSION']
    targets = json.loads(Path(os.environ['ENVSCOPE_INSPECTION_TARGETS']).read_text())
    folder = data/'versions'/version
    snapshot = json.loads((folder/'jumpserver-snapshot.json').read_text())
    inventory = json.loads((folder/'snapshot.json').read_text())
    paths, errors = [], []
    try:
        saved = json.loads((data/'secrets'/'cookies.json').read_text())
        mapping_file = data/'secrets'/'ip-password-map.json'
        ip_map = json.loads(mapping_file.read_text()) if mapping_file.exists() else {}
    except (OSError, ValueError):
        for target in targets:
            if target.get('pid'): node_event(target.get('instanceId'), 'unknown', 'JumpServer 凭据不可读取')
        return [], [(target.get('ip'), 'JumpServer 凭据不可读取') for target in targets]
    for index, target in enumerate(targets):
        ip, pid = target.get('ip'), target.get('pid')
        if not target.get('assetId') or not pid:
            errors.append((ip, target.get('reason') or '资产或进程未确认'))
            progress(f'{ip or "未知服务器"}：资产或进程未确认')
            continue
        try:
            node_event(target.get('instanceId'), 'running')
            assets = [a for a in snapshot.get('assets', []) if a.get('id') == target['assetId']]
            processes = [p for p in (assets[0].get('inspection') or {}).get('processes', []) if p.get('pid') == pid] if len(assets) == 1 else []
            if len(processes) != 1 or processes[0].get('startedAt') != target.get('startedAt'):
                raise ValueError('进程身份无法确认')
            progress(f'{ip} PID {pid}：开始 300 秒 TCP 检测')
            last = [0, '']
            def notify(message, **details):
                tick = time.monotonic()
                if message != last[1] or tick-last[0] >= 20:
                    progress(f'{ip} PID {pid}：{message}')
                    last[:] = [tick, message]
            report = inspect_application(assets[0], processes[0], inventory.get('apps', []), snapshot.get('assets', []),
                                         saved, ip_map, environment, 300, progress=notify)
            report['sourceVersion'] = version
            path = output/f'tcp-{index}.json'
            path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            path.chmod(0o600)
            paths.append(path)
            node_event(target.get('instanceId'), report.get('status', 'unknown'))
            progress(f'{ip} PID {pid}：TCP 检测 {report.get("status", "unknown")}')
        except Exception as exc:
            errors.append((ip, 'TCP 检查失败：'+type(exc).__name__))
            node_event(target.get('instanceId'), 'failed', 'TCP 检查失败：'+type(exc).__name__)
            progress(f'{ip} PID {pid}：TCP 检查失败：{type(exc).__name__}')
    return paths, errors
