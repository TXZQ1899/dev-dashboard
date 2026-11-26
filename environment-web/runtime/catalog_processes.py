"""Attach snapshot process observations to application deployment rows."""
from application_check import ips, match_application
from collections import defaultdict


def build_process_observations(rows, apps, assets):
    """Calculate once per refresh, keeping same-unit siblings together."""
    groups, app_index, asset_index = defaultdict(list), defaultdict(list), defaultdict(list)
    for app in apps:
        for env, deployments in (app.get('envs') or {}).items():
            addresses = {address for deployment in deployments for address in ips(deployment.get('ip'))}
            for address in addresses:
                app_index[(env, address)].append(app)
    for asset in assets:
        for address in ips(asset.get('ip')):
            asset_index[address].append(asset)
    for row in rows:
        groups[(row['unitId'], row['environment'])].append(row)
    result = {}
    for members in groups.values():
        # Preserve sibling comparisons while restricting name matching to each host.
        relevant_apps = {id(app): app for row in members for app in app_index[(row['environment'], row.get('ip'))]}
        relevant_assets = {id(asset): asset for row in members for asset in asset_index[row.get('ip')]}
        attach_processes(members, members[0].get('appId'), list(relevant_apps.values()), list(relevant_assets.values()), True)
        result.update((row['id'], row['processObservation']) for row in members)
    return result


def attach_processes(rows, app_id, apps, assets, matches_catalog):
    for row in rows:
        result = {'status': 'UNKNOWN', 'reason': '采集或关联证据不足'}
        row['processObservation'] = result
        if not matches_catalog or row.get('presenceStatus') != 'PRESENT' or not app_id:
            continue
        address = row.get('ip')
        candidates = [a for a in assets if address and address in ips(a.get('ip'))]
        if len(candidates) != 1:
            continue
        inspection = candidates[0].get('inspection') or {}
        result['observedAt'] = inspection.get('checkedAt')
        if inspection.get('loginStatus') != 'can_login' or inspection.get('processStatus') not in ('complete', 'partial'):
            continue
        found, ambiguous = [], False
        container_pids = {p.get('pid') for p in (inspection.get('kubernetes') or {}).get('processes', [])}
        container_pids.update(c.get('initPid') for c in (inspection.get('docker') or {}).get('containers', []))
        container_pids.discard(None)
        for process in inspection.get('processes') or []:
            # Name evidence is required: a shared port alone does not establish identity.
            match = match_application(process, {address}, [], apps, row.get('environment'))
            if match.get('appId') == app_id:
                if process.get('pid') in container_pids:
                    ambiguous = True
                else:
                    found.append(process)
            elif any(item.get('appId') == app_id for item in match.get('candidates', [])):
                ambiguous = True
        siblings = [r for r in rows if r.get('ip') == address and r.get('environment') == row.get('environment') and r.get('presenceStatus') == 'PRESENT']
        if len(siblings) > 1 and found:
            port = str(row.get('port') or '')
            if not port.isdigit() or sum(str(r.get('port') or '') == port for r in siblings) != 1:
                ambiguous = True
            else:
                found = [p for p in found if int(port) in (p.get('ports') or [])]
                if not found:
                    ambiguous = True
        if len(found) > 1 and str(row.get('port') or '').isdigit():
            narrowed = [p for p in found if int(row['port']) in (p.get('ports') or [])]
            if len(narrowed) == 1:
                found = narrowed
        if len(found) == 1 and not ambiguous:
            process = found[0]
            ports = sorted({p for p in (process.get('ports') or []) if isinstance(p, int) and 0 < p < 65536})
            result.update(status='MATCHED', reason=match_application(process, {address}, [], apps, row.get('environment'))['basis'],
                          pid=process.get('pid'), startedAt=process.get('startedAt'),
                          elapsedSeconds=process.get('elapsedSeconds'), ports=ports,
                          portStatus=inspection.get('portStatus'),
                          command=process.get('command'))
        elif found or ambiguous:
            result.update(status='AMBIGUOUS', reason='存在多个进程或应用候选')
        elif inspection.get('processStatus') == 'complete':
            result.update(status='NOT_FOUND', reason='完整采集中未按应用名称匹配到进程')
