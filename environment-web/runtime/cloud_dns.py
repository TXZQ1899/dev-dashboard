"""Read-only, two-level AliDNS / PrivateZone collection; no DNS lookups."""
from datetime import datetime, timezone
import json

from aliyun_openapi import RpcClient
from export_devops import ExportError


def pages(client, action, container, member, total_key, identity, params=None):
    rows, total, number = [], None, 1
    while True:
        data = client.call(action, {**(params or {}), 'PageNumber': number, 'PageSize': 100})
        block = data.get(container)
        batch = block.get(member) if isinstance(block, dict) else block
        count = data.get(total_key)
        if not isinstance(batch, list) or not isinstance(count, int) or count < 0:
            raise ExportError(f'{action} 清单或总数缺失')
        if total is not None and count != total:
            raise ExportError(f'{action} 分页期间总数变化')
        total = count
        rows.extend(batch)
        ids = [str(row.get(identity) or '') for row in rows]
        if any(not key for key in ids) or len(set(ids)) != len(ids) or len(rows) > total:
            raise ExportError(f'{action} 存在缺失或重复 ID')
        if len(rows) == total:
            return rows
        if not batch:
            raise ExportError(f'{action} 分页提前结束')
        number += 1


def record(row, zone, visibility):
    host = str(row.get('RR', row.get('Rr', ''))).strip().lower().rstrip('.')
    name = zone if host == '@' else host if host == zone or host.endswith('.' + zone) else f'{host}.{zone}'
    if not host or not row.get('Type') or row.get('Value') is None:
        raise ExportError('DNS 解析记录缺少必要字段')
    status = str(row.get('Status') or '')
    return {
        'id': str(row['RecordId']), 'zone': zone, 'name': name,
        'type': str(row['Type']).upper(), 'value': str(row['Value']),
        'line': str(row.get('Line') or ''), 'status': {'ENABLE': '启用', 'DISABLE': '暂停'}.get(status, '未知'),
        'providerStatus': status, 'ttl': row.get('TTL', row.get('Ttl')), 'weight': row.get('Weight'),
        'priority': row.get('Priority'), 'policy': '', 'remark': str(row.get('Remark') or ''),
        'source': 'alidns' if visibility == 'public' else 'pvtz', 'visibility': visibility,
        'raw': row,
    }


def collect_dns_ak(key, secret, out, client_factory=RpcClient):
    zones = []
    configs = (
        ('public', 'alidns.aliyuncs.com', '2015-01-09', 'DescribeDomains', 'Domains', 'Domain', 'TotalCount', 'DomainId',
         'DomainName', 'DescribeDomainRecords', 'DomainRecords', 'Record', 'TotalCount', 'DomainName'),
        ('private', 'pvtz.aliyuncs.com', '2018-01-01', 'DescribeZones', 'Zones', 'Zone', 'TotalItems', 'ZoneId',
         'ZoneName', 'DescribeZoneRecords', 'Records', 'Record', 'TotalItems', 'ZoneId'),
    )
    for visibility, endpoint, version, action, container, member, total, identity, name_key, details, rc, rm, rt, param in configs:
        client = client_factory(key, secret, endpoint, version)
        listing = pages(client, action, container, member, total, identity)
        for raw in listing:
            count = raw.get('RecordCount')
            name = str(raw.get(name_key) or '').strip().lower().rstrip('.')
            if not name or not isinstance(count, int) or count < 0:
                raise ExportError('DNS 域名列表缺少域名或解析记录数')
            # An explicit zero is authoritative: never call the record API for it.
            records = [] if count == 0 else pages(client, details, rc, rm, rt, 'RecordId', {param: raw[name_key if visibility == 'public' else identity]})
            if len(records) != count:
                raise ExportError('DNS 域名列表与解析记录数不一致，请重新采集')
            zones.append({'id': str(raw[identity]), 'name': name, 'visibility': visibility,
                          'recordCount': count, 'recordsCollected': count > 0,
                          'skipReason': 'zero-records' if count == 0 else '',
                          'records': [record(r, name, visibility) for r in records], 'raw': raw})
        # Detect added/removed zones and count changes without any extra detail calls.
        after = pages(client, action, container, member, total, identity)
        signature = lambda items: sorted((str(z[identity]), z.get(name_key), z.get('RecordCount')) for z in items)
        if signature(listing) != signature(after):
            raise ExportError('DNS 域名清单在采集期间变化，请重新采集')
    result = {'available': True, 'complete': True, 'collectedAt': datetime.now(timezone.utc).isoformat(),
              'zones': zones, 'zoneCount': len(zones), 'recordCount': sum(z['recordCount'] for z in zones),
              'scope': 'credential-visible', 'realtimeDns': False}
    path = out/'cloud-dns-raw.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    return result
