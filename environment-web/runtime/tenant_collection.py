"""Isolated per-account collection and transactional aggregation of cloud slices."""
from copy import deepcopy
import json

from aliyun import collect_ecs, collect_clb, ecs_snapshot, normalize_clb
from export_nat import collect as collect_nat, normalize_nat
from export_dms import collect as collect_dms
from aliyun_openapi import RpcClient, collect_ecs_ak, collect_clb_ak, collect_nat_ak, collect_dms_ak
from cloud_dns import collect_dns_ak
from aliyun_tenants import CLOUD_SOURCES, LEGACY_TENANT, select, verify, resource_key
from export_devops import AuthenticationError, ExportError


def task_key(tenant_id, source, region=''):
    return f'{tenant_id}/{source}/{region}'


def tenant_error(exc, tenant, source, region=''):
    auth = isinstance(exc, AuthenticationError)
    failure = {'source': source.upper(), 'tenantId': tenant['id'], 'tenantName': tenant['name'],
               'region': region, 'taskKey': task_key(tenant['id'], source, region),
               'credentialVersion': tenant.get('credentialVersion'),
               'type': ('accesskey_invalid' if tenant['auth']['mode'] == 'accesskey' else 'cookie_expired') if auth else type(exc).__name__,
               'message': f"{tenant['name']} / {source.upper()}：" +
                          ('凭据无效或权限不足，请验证账号后重试' if auth else '采集失败，请检查权限、网络或来源完整性')}
    error = ExportError(failure['message'])
    error.failure = failure
    error.failures = [failure]
    return error


def scoped_snapshot(snapshot, tenant, source, region):
    result = deepcopy(snapshot)
    field = 'zones' if source == 'cloud-dns' else 'gateways' if source == 'nat' else 'instances'
    at = result.get('fetchedAt') or result.get('collectedAt')
    meta = {'tenantId': tenant['id'], 'tenantName': tenant['name'], 'accountId': tenant.get('accountId', '')}
    for row in result.get(field, []):
        native = row.get('id') if source != 'dms' else f"{tenant.get('dmsTenantId') or snapshot.get('tenantId') or ''}/{row.get('实例地址', '')}"
        row.update(meta, resourceKey=resource_key(tenant['id'], source, region, native),
                   region=row.get('region') or row.get('regionId') or region, observedAt=at)
        if source == 'cloud-dns':
            row['resourceKey'] = resource_key(tenant['id'], f"dns-zone-{row['visibility']}", '', native)
            for record in row['records']:
                record.update(meta, zoneId=row['id'], observedAt=at,
                              resourceKey=resource_key(tenant['id'], f"dns-record-{row['visibility']}", '', f"{native}/{record['id']}"))
        if source == 'dms':
            row['dmsTenantId'] = tenant.get('dmsTenantId') or snapshot.get('tenantId')
            row.setdefault('cloudInstanceId', row.get('实例ID', '') if tenant['auth']['mode'] == 'cookie' else '')
            row['sourceKeys'] = [resource_key(tenant['id'], 'dms-record', str(row['dmsTenantId'] or ''), record_id)
                                 for record_id in row.get('dmsInstanceIds', [])]
        if source == 'nat':
            for entry in row.get('entries', []):
                entry.update(meta, region=region, observedAt=at,
                             resourceKey=resource_key(tenant['id'], 'dnat', region, f"{row['id']}/{entry['id']}"))
    for project in result.get('projects', []):
        project.update(meta)
    result.pop('tenantId', None)  # Legacy root tenantId is the DMS Tid, not an AppAtlas ID.
    result.update(schemaVersion=2, available=True, complete=True, tenants=[{
        **meta, 'region': region, 'dmsTenantId': tenant.get('dmsTenantId') or snapshot.get('tenantId'),
        'collectedAt': at, 'complete': True, 'count': len(result.get(field, [])),
        'credentialVersion': tenant.get('credentialVersion'), 'scope': 'credential-visible',
    }])
    if source == 'clb':
        warnings = [{**warning, **meta, 'region': region} for warning in result.get('warnings', [])]
        result.update(partial=bool(warnings), warnings=warnings)
        result['tenants'][0].update(partial=result['partial'], warnings=deepcopy(warnings))
    return result


def merge_snapshots(previous, slices, source, replace_all=False, selected_ids=()):
    """Replace only selected tenants, including their now-empty or removed regions."""
    field = 'zones' if source == 'cloud-dns' else 'gateways' if source == 'nat' else 'instances'
    old = deepcopy(previous or {})
    if old and old.get('schemaVersion') != 2 and (source == 'ecs' or old.get('available')):
        # The existing collector had exactly one account. Attribute only this legacy
        # cloud snapshot while deriving a new version; never rewrite historical files.
        legacy = {'id': LEGACY_TENANT, 'name': '现有租户（历史数据）', 'accountId': '',
                  'auth': {'mode': 'cookie'}, 'credentialVersion': 'legacy'}
        old = scoped_snapshot(old, legacy, source, old.get('region') or '')
        for scope in old['tenants']: scope['legacy'] = True
    selected_ids = set(selected_ids)
    keep = lambda row: not replace_all and (row.get('tenantId') or LEGACY_TENANT) not in selected_ids
    rows = [r for r in old.get(field, []) if keep(r)]
    scopes = [r for r in old.get('tenants', []) if keep(r)]
    projects = [r for r in old.get('projects', []) if keep(r)]
    for item in slices:
        rows.extend(item.get(field, [])); scopes.extend(item.get('tenants', []))
        projects.extend(item.get('projects', []))
    result = {**old, 'schemaVersion': 2, 'available': True, 'complete': True, field: rows, 'tenants': scopes}
    result.pop('tenantId', None)
    result['regions'] = sorted({s['region'] for s in scopes if s.get('region')})
    result['region'] = result['regions'][0] if len(result['regions']) == 1 else ''
    # The root timestamp is publication freshness; individual slices retain observation times.
    times = [s.get('collectedAt') for s in scopes if s.get('collectedAt')]
    result['fetchedAt' if source == 'ecs' else 'collectedAt'] = max(times, default=None)
    if source == 'ecs':
        result['projects'] = list({(p.get('tenantId', LEGACY_TENANT), p['id']): p for p in projects}.values())
    if source == 'dms':
        result['totalCount'] = len(rows)
        result['rawRecordCount'] = sum(1 + r.get('重复记录', 0) for r in rows)
    if source == 'cloud-dns':
        result['zoneCount'] = len(rows)
        result['recordCount'] = sum(z['recordCount'] for z in rows)
    if source == 'clb':
        result['warnings'] = [warning for scope in scopes for warning in scope.get('warnings', [])]
        result['partial'] = any(scope.get('partial', False) for scope in scopes)
    return result


def build_tasks(saved, folder, source=None, tenant_id=None):
    selected = select(saved, source, tenant_id)
    tasks, contexts = {}, {}
    for tenant in selected:
        try:
            verify(tenant)
        except Exception as exc:
            raise tenant_error(exc, tenant, source or 'ecs') from None
        for kind in ((source,) if source else CLOUD_SOURCES):
            for region in ([''] if kind in ('dms', 'cloud-dns') else tenant['regions']):
                key = task_key(tenant['id'], kind, region)
                contexts[key] = {'tenantId': tenant['id'], 'tenantName': tenant['name'],
                                 'source': kind.upper(), 'region': region,
                                 'label': f"{tenant['name']} · {kind.upper()}" + (f' · {region}' if region else '')}
                def run(tenant=deepcopy(tenant), kind=kind, region=region):
                    try:
                        auth = tenant['auth']
                        out = folder/'sources'/'aliyun'/tenant['id']/kind/(region or 'all')
                        out.mkdir(parents=True, exist_ok=True)
                        if auth['mode'] == 'accesskey':
                            args = (auth['accessKeyId'], auth['accessKeySecret'])
                            if kind == 'cloud-dns':
                                raw = collect_dns_ak(*args, out=out)
                            elif kind == 'dms':
                                tid = tenant.get('dmsTenantId')
                                if not tid:
                                    data = RpcClient(*args, 'dms-enterprise.aliyuncs.com', '2018-11-01').call('GetUserActiveTenant')
                                    tid = str((data.get('Tenant') or {}).get('Tid') or '')
                                    if data.get('Success') is not True or not tid.isdigit():
                                        raise ExportError('无法确定 DMS Tid')
                                tenant['dmsTenantId'] = str(tid)
                                raw = collect_dms_ak(*args, out=out, tid=tid)
                            else:
                                raw = {'ecs': collect_ecs_ak, 'clb': collect_clb_ak, 'nat': collect_nat_ak}[kind](*args, out=out, region=region)
                        else:
                            if kind == 'cloud-dns':
                                raise ExportError('DNS 采集需要租户的 AccessKey，请在租户管理中配置')
                            if region and region != 'cn-shanghai':
                                raise ExportError('Cookie 采集仅支持上海地域，请使用 AccessKey')
                            raw = {'ecs': collect_ecs, 'clb': collect_clb, 'nat': collect_nat, 'dms': collect_dms}[kind](auth['cookie'], out)
                        if kind == 'ecs':
                            project_file = folder/'ecs-snapshot.json'
                            if not project_file.exists(): project_file = folder/'base'/'ecs-snapshot.json'
                            if not project_file.exists(): project_file = folder/'ecs-projects.json'
                            previous = json.loads(project_file.read_text()) if project_file.exists() else {}
                            projects = [p for p in previous.get('projects', []) if (p.get('tenantId') or LEGACY_TENANT) == tenant['id']]
                            raw = ecs_snapshot(raw, projects)
                        elif kind == 'clb': raw = normalize_clb(raw)
                        elif kind == 'nat': raw = normalize_nat(raw)
                        return scoped_snapshot(raw, tenant, kind, region)
                    except Exception as exc:
                        raise tenant_error(exc, tenant, kind, region) from None
                tasks[key] = run
    return tasks, contexts, selected


def publish(results, contexts, selected, folder, write, read, replace_all=False):
    for source in {context['source'].lower() for context in contexts.values()}:
        path = folder/f'{source}-snapshot.json'
        slices = [results[key] for key, context in contexts.items() if context['source'].lower() == source]
        for snapshot in slices:
            for scope in snapshot['tenants']: scope['sourceVersion'] = folder.name
        write(path, merge_snapshots(read(path, read(folder/'base'/path.name, {})), slices, source, replace_all,
                                   [t['id'] for t in selected]))


