"""Conservative Zookeeper snapshot discovery; never creates catalog records."""
import re
import shlex
from collections import Counter

try:
    import application_check as check
except ModuleNotFoundError:
    from runtime import application_check as check

MAIN_CLASS = 'org.apache.zookeeper.server.quorum.QuorumPeerMain'
ENVIRONMENTS = ('TEST', 'SIMULATION', 'PRODUCT')


def config_argument(command):
    """Return the server's sole explicit config argument, if recognizable."""
    try:
        args = shlex.split(command)
    except (TypeError, ValueError):
        return None
    if args.count(MAIN_CLASS) != 1:
        return None
    index = args.index(MAIN_CLASS)
    return args[index + 1] if index + 2 == len(args) and args[index + 1].startswith('/') else None


def discover(jumpserver, devops, source_version):
    """Return snapshot candidates and evidence; no candidate is a confirmed unit.

    Application heuristics are evaluated in every environment and all candidate
    IDs retained, because the snapshot does not directly bind a PID to an app.
    """
    assets = jumpserver.get('assets', [])
    apps = devops.get('apps', [])
    if not isinstance(assets, list) or not isinstance(apps, list):
        raise ValueError('资产或应用快照格式无效')
    coverage = Counter()
    candidates = []
    for asset in assets:
        inspection = asset.get('inspection') or {}
        coverage[inspection.get('processStatus') or 'not_collected'] += 1
        for process in inspection.get('processes') or []:
            command = process.get('command') or ''
            try:
                args = shlex.split(command)
            except ValueError:
                continue
            if MAIN_CLASS not in args:
                continue
            pid = process.get('pid')
            listeners = [{'local': {'port': port}, 'pids': [pid]} for port in process.get('ports') or []
                         if isinstance(port, int) and not isinstance(port, bool)]
            matches = {}
            for environment in ENVIRONMENTS:
                result = check.match_application(process, check.ips(asset.get('ip')), listeners, apps, environment)
                for app in result['candidates']:
                    item = matches.setdefault(app['appId'], {'appId': app['appId'], 'appName': app['appName'], 'evidence': []})
                    item['evidence'].append({'environment': environment, 'confidence': app['confidence'], 'basis': app['basis']})
            candidates.append({
                'assetId': asset.get('id'), 'ip': asset.get('ip'), 'pid': pid,
                'startedAt': process.get('startedAt'), 'observedAt': inspection.get('checkedAt'),
                'processStatus': inspection.get('processStatus'), 'directPorts': process.get('ports') or [],
                'inferredPorts': process.get('inferredPorts') or [], 'configPathCandidate': config_argument(command),
                'typeConfidence': 'EXACT', 'applicationCandidates': sorted(matches.values(), key=lambda m: m['appId']),
                'applicationStatus': 'AMBIGUOUS' if len(matches) > 1 else 'INFERRED' if matches else 'UNKNOWN',
                'clusterStatus': 'UNKNOWN',
                'reason': '应用关联候选待核实' if matches else '集群与独立部署关系尚未核实',
            })
    candidates.sort(key=lambda x: (str(x['ip']), x['pid'] or 0))
    return {'sourceVersion': source_version, 'coverage': dict(coverage), 'candidates': candidates}


def main():
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description='只读筛选 Zookeeper 服务候选，不创建部署单元')
    parser.add_argument('--jumpserver', required=True, help='JumpServer 快照文件')
    parser.add_argument('--devops', required=True, help='DevOps 应用快照文件')
    parser.add_argument('--source-version', required=True, help='已完成的来源版本 ID')
    args = parser.parse_args()
    with Path(args.jumpserver).open() as handle:
        jumpserver = json.load(handle)
    with Path(args.devops).open() as handle:
        devops = json.load(handle)
    print(json.dumps(discover(jumpserver, devops, args.source_version), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
