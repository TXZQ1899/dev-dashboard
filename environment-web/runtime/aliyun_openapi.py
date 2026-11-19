"""阿里云 OpenAPI（AccessKey）采集器。

与原有 Cookie / 控制台采集并行存在：本模块只新增读取路径，不修改
`export_slb.py`、`export_nat.py`、`export_dms.py`、`fetch_ecs_inventory.py`
和 `daily_collection.py` 的现有行为。输出结构与现有 normalize 函数保持一致，
因此 `aliyun.py` 中的 `normalize_clb` / `ecs_snapshot` 及 `export_nat.normalize_nat`
可以直接复用。

签名实现只依赖 Python 标准库：
- RPC 风格接口（ECS / SLB / VPC / DMS）使用 SignatureVersion 1.0（HMAC-SHA1）；
- ROA 风格接口（云效 Codeup 2021-06-25）使用 ACS3-HMAC-SHA256。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from export_devops import AuthenticationError, ExportError
from export_dms import DB_TYPES, ENV_TYPES, REGIONS, merge_duplicates, resolve_ip
from check_codeup_access import normalize_url

DEFAULT_REGION = 'cn-shanghai'
CODEUP_HOST = 'devops.cn-hangzhou.aliyuncs.com'
CODEUP_VERSION = '2021-06-25'
CODEUP_WORKERS = 5
TZ_SHANGHAI = ZoneInfo('Asia/Shanghai')

# OpenAPI 鉴权/权限类错误统一转成 AuthenticationError，便于采集层提示更新 AccessKey。
_AUTH_CODES = {
    'InvalidAccessKeyId', 'InvalidAccessKeyId.NotFound', 'InvalidAccessKeySecret',
    'SignatureDoesNotMatch', 'SignatureNonceUsed', 'Forbidden.RAM',
    'NoPermission', 'NoPermission.User', 'UnauthorizedOperation',
    'InvalidSecurityToken.Expired', 'InvalidSecurityToken.Mismatch',
}


def _enc(value):
    return urllib.parse.quote(str(value), safe='~')


def _http_json(method, url, headers, body=None, timeout=45):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class OpenApiError(ExportError):
    def __init__(self, action, status, code, message):
        super().__init__(f'{action} 调用失败（{code or status}）：{str(message)[:180]}')
        self.action, self.status, self.code = action, status, code


class RpcClient:
    """阿里云 RPC 风格 OpenAPI 客户端（SignatureVersion 1.0）。"""

    def __init__(self, access_key_id, access_key_secret, endpoint, version, timeout=45, send=None):
        if not access_key_id or not access_key_secret:
            raise ExportError('AccessKey ID 和 Secret 不能为空')
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.endpoint = endpoint
        self.version = version
        self.timeout = timeout
        self.send = send or _http_json

    def call(self, action, params=None):
        values = {
            'Format': 'JSON',
            'Version': self.version,
            'AccessKeyId': self.access_key_id,
            'SignatureMethod': 'HMAC-SHA1',
            'SignatureVersion': '1.0',
            'SignatureNonce': str(uuid.uuid4()),
            'Timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'Action': action,
        }
        for key, value in (params or {}).items():
            if value is not None:
                values[key] = value
        query = '&'.join(_enc(key) + '=' + _enc(value) for key, value in sorted(values.items()))
        string_to_sign = 'GET&%2F&' + _enc(query)
        digest = hmac.new((self.access_key_secret + '&').encode('utf-8'),
                          string_to_sign.encode('utf-8'), hashlib.sha1).digest()
        signature = urllib.parse.quote(base64.b64encode(digest).decode(), safe='')
        url = f'https://{self.endpoint}/?{query}&Signature={signature}'
        status, body = self.send('GET', url, {'Accept': 'application/json'}, None, self.timeout)
        try:
            payload = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeError, AttributeError):
            raise ExportError(f'{action} 返回了无法解析的数据（HTTP {status}）') from None
        if status >= 400:
            code = str(payload.get('Code') or '')
            if status == 401 or code in _AUTH_CODES:
                raise AuthenticationError(f'{action} AccessKey 无效或无权调用（{code or status}）')
            raise OpenApiError(action, status, code, payload.get('Message') or payload)
        return payload


class RoaClient:
    """阿里云 ROA 风格 OpenAPI 客户端（ACS3-HMAC-SHA256）。"""

    def __init__(self, access_key_id, access_key_secret, endpoint, version, timeout=45, send=None):
        if not access_key_id or not access_key_secret:
            raise ExportError('AccessKey ID 和 Secret 不能为空')
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.endpoint = endpoint
        self.version = version
        self.timeout = timeout
        self.send = send or _http_json

    def call(self, action, path, params=None):
        values = {key: value for key, value in (params or {}).items() if value is not None}
        query = '&'.join(_enc(key) + '=' + _enc(value) for key, value in sorted(values.items()))
        body = b''
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {
            'host': self.endpoint,
            'x-acs-action': action,
            'x-acs-version': self.version,
            'x-acs-date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'x-acs-content-sha256': payload_hash,
            'x-acs-signature-nonce': str(uuid.uuid4()),
            'accept': 'application/json',
        }
        names = sorted(headers)
        canonical_headers = ''.join(name + ':' + str(headers[name]).strip() + '\n' for name in names)
        canonical_request = '\n'.join([
            'GET', urllib.parse.quote(path, safe='/-_.~'), query,
            canonical_headers, ';'.join(names), payload_hash,
        ])
        string_to_sign = 'ACS3-HMAC-SHA256\n' + hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
        signature = hmac.new(self.access_key_secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                             hashlib.sha256).hexdigest()
        headers['Authorization'] = ('ACS3-HMAC-SHA256 Credential=' + self.access_key_id +
                                    ',SignedHeaders=' + ';'.join(names) + ',Signature=' + signature)
        url = f'https://{self.endpoint}{path}' + ('?' + query if query else '')
        status, raw = self.send('GET', url, headers, None, self.timeout)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError, AttributeError):
            raise ExportError(f'{action} 返回了无法解析的数据（HTTP {status}）') from None
        if status >= 400:
            code = str(payload.get('errorCode') or payload.get('Code') or '')
            if status == 401 or code in _AUTH_CODES:
                raise AuthenticationError(f'{action} AccessKey 无效或无权调用（{code or status}）')
            raise OpenApiError(action, status, code, payload.get('errorMessage') or payload.get('Message') or payload)
        if payload.get('success') is False:
            raise OpenApiError(action, status, payload.get('errorCode'), payload.get('errorMessage'))
        return payload


def _items(value, outer, inner):
    return ((value or {}).get(outer) or {}).get(inner) or []


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat()


def _page_guard(total, rows, key, action):
    if type(total) is not int or total < 0:
        raise ExportError(f'{action} 缺少有效总数，无法确认采集完整性')
    if len(rows) != total or len({row.get(key) for row in rows}) != total:
        raise ExportError(f'{action} 总数或唯一 {key} 核对失败，采集结果不完整')


# --------------------------------------------------------------------------- ECS

def collect_ecs_ak(access_key_id, access_key_secret, out=None, region=DEFAULT_REGION):
    """通过 ECS OpenAPI 采集实例，输出结构与 fetch_ecs_inventory.collect 一致。"""
    client = RpcClient(access_key_id, access_key_secret, f'ecs.{region}.aliyuncs.com', '2014-05-26')
    rows, expected = [], None
    page = 1
    while True:
        data = client.call('DescribeInstances', {'RegionId': region, 'PageNumber': page, 'PageSize': 100})
        batch = _items(data, 'Instances', 'Instance')
        if expected is None:
            expected = data.get('TotalCount')
        elif expected != data.get('TotalCount'):
            raise ExportError('ECS 采集期间实例总数发生变化')
        rows.extend(batch)
        print(f'ECS OpenAPI：第 {page} 页，已读取 {len(rows)} 台', flush=True)
        if expected is not None and len(rows) >= expected:
            break
        if not batch:
            raise ExportError('ECS 分页提前为空，采集结果不完整')
        page += 1
        if page > 1000:
            raise ExportError('ECS 分页超过安全上限')
    _page_guard(expected, rows, 'InstanceId', 'ECS DescribeInstances')
    if any(row.get('RegionId') != region for row in rows):
        raise ExportError('ECS 返回了非预期地域的实例')

    # 控制台快照把标签放在 extraProperties.tags，OpenAPI 需单独调用 ListTagResources。
    tags = {}
    ids = [row['InstanceId'] for row in rows]
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        params = {'RegionId': region, 'ResourceType': 'instance'}
        for index, instance_id in enumerate(batch, 1):
            params[f'ResourceId.{index}'] = instance_id
        while True:
            value = client.call('ListTagResources', params)
            for item in _items(value, 'TagResources', 'TagResource'):
                tags.setdefault(item.get('ResourceId'), []).append(
                    {'TagKey': item.get('TagKey', ''), 'TagValue': item.get('TagValue', '')})
            token = value.get('NextToken')
            if not token:
                break
            params['NextToken'] = token
    for row in rows:
        extra = row.setdefault('extraProperties', {})
        if isinstance(extra, dict):
            extra['tags'] = tags.get(row['InstanceId'], [])
    result = {'FetchedAt': _now(), 'RegionId': region, 'TotalCount': expected,
              'Instances': {'Instance': rows}}
    if out is not None:
        _save(Path(out) / 'ecs-raw.json', result)
    return result


# --------------------------------------------------------------------------- CLB

def collect_clb_ak(access_key_id, access_key_secret, out=None, region=DEFAULT_REGION):
    """通过 SLB OpenAPI 采集 CLB，输出结构与 export_slb.collect 一致。"""
    client = RpcClient(access_key_id, access_key_secret, f'slb.{region}.aliyuncs.com', '2014-05-15')
    ecs = RpcClient(access_key_id, access_key_secret, f'ecs.{region}.aliyuncs.com', '2014-05-26')
    out = Path(out) if out is not None else Path('slb-export') / datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    result = {'region': region, 'source': f'https://slb.{region}.aliyuncs.com (AccessKey)',
              'collectedAt': _now(), 'instances': [], 'errors': [], 'warnings': []}
    last_request = ('DescribeLoadBalancers', {})

    def call(api, action, params=None):
        # Retry the whole signed call so every attempt gets a fresh nonce.
        for attempt in range(3):
            try:
                return api.call(action, params)
            except (OSError, OpenApiError) as exc:
                status = (exc.status if isinstance(exc, OpenApiError) else
                          exc.code if isinstance(exc, urllib.error.HTTPError) else None)
                if status is not None and status not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt == 2:
                    raise
                delay = 2 ** attempt + random.uniform(0, 0.5)
                reason = f'HTTP {status}' if status is not None else '网络超时或连接异常'
                print(f'CLB OpenAPI：{action} {reason}，{delay:.1f} 秒后重试'
                      f'（第 {attempt + 2}/3 次请求）', flush=True)
                time.sleep(delay)

    def read(action, params=None, api=None):
        nonlocal last_request
        last_request = (action, params or {})
        return call(api or client, action, {'RegionId': region, **(params or {})})

    def warn(action, params, row=None, exc=None, skipped=False, reason='request_failed'):
        # Never log raw transport exceptions: they can contain signed URLs.
        if exc is None:
            message = '实例已停止或锁定，跳过详情采集'
        elif isinstance(exc, AuthenticationError):
            message = '接口认证或权限异常'
        elif isinstance(exc, OpenApiError):
            message = f'接口错误（HTTP {exc.status}，{exc.code}）'
        elif isinstance(exc, OSError):
            message = '网络超时或连接异常'
        else:
            message = f'响应数据异常（{type(exc).__name__}）'
        warning = {'action': action, 'params': params, 'reason': reason,
                   'error': message, 'skipped': skipped}
        if row:
            warning.update(loadBalancerId=row['LoadBalancerId'],
                           status=row.get('LoadBalancerStatus', ''))
        result['warnings'].append(warning)
        print('Warning: CLB OpenAPI ' + json.dumps(warning, ensure_ascii=False), flush=True)

    def listing():
        rows, page, total = [], 1, None
        while True:
            data = call(client, 'DescribeLoadBalancers', {'RegionId': region, 'PageNumber': page, 'PageSize': 100})
            total = data.get('TotalCount') if total is None else total
            if data.get('TotalCount') != total:
                raise ExportError('CLB 采集期间实例总数发生变化')
            batch = _items(data, 'LoadBalancers', 'LoadBalancer')
            rows.extend(batch)
            if len(rows) >= total:
                break
            if not batch:
                raise ExportError('CLB 分页提前为空')
            page += 1
            if page > 1000:
                raise ExportError('CLB 分页超过安全上限')
        _page_guard(total, rows, 'LoadBalancerId', 'CLB DescribeLoadBalancers')
        return rows

    def collect_instance(row, detail):
        def check_backends(attributes):
            backends = (_items(attributes, 'BackendServers', 'BackendServer') +
                        _items(attributes, 'MasterSlaveBackendServers', 'MasterSlaveBackendServer'))
            if any(not isinstance(server, dict) for server in backends):
                raise ExportError('CLB 后端记录格式异常')

        lb = row['LoadBalancerId']
        params = {'LoadBalancerId': lb}
        if (detail.get('LoadBalancerId') != lb or any(key not in detail for key in
                ('LoadBalancerName', 'Address', 'AddressType', 'LoadBalancerStatus'))):
            raise ExportError('CLB 实例详情缺失或身份不匹配')
        check_backends(detail)
        entry = {'listAttributes': row, 'attributes': detail, 'listeners': [],
                 'health': read('DescribeHealthStatus', params), 'serverGroups': []}
        check_backends(entry['health'])
        for listener in _items(detail, 'ListenerPortsAndProtocol', 'ListenerPortAndProtocol'):
            protocol = listener['ListenerProtocol'].upper()
            lp = {**params, 'ListenerPort': listener['ListenerPort']}
            value = {'protocol': protocol,
                     'attributes': read('DescribeLoadBalancer' + protocol + 'ListenerAttribute', lp)}
            if value['attributes'].get('ListenerPort') != listener['ListenerPort']:
                raise ExportError('CLB 监听器详情缺失或端口不匹配')
            if protocol in ('HTTP', 'HTTPS'):
                value['rules'] = read('DescribeRules', lp)
                for rule in _items(value['rules'], 'Rules', 'Rule'):
                    if not rule.get('RuleId') or not rule.get('VServerGroupId'):
                        raise ExportError('CLB 转发规则缺少身份或服务器组')
            entry['listeners'].append(value)
        for kind, action, outer, inner, key, attr in [
            ('virtual', 'DescribeVServerGroups', 'VServerGroups', 'VServerGroup',
             'VServerGroupId', 'DescribeVServerGroupAttribute'),
            ('masterSlave', 'DescribeMasterSlaveServerGroups', 'MasterSlaveServerGroups',
             'MasterSlaveServerGroup', 'MasterSlaveServerGroupId', 'DescribeMasterSlaveServerGroupAttribute')]:
            groups = _items(read(action, params), outer, inner)
            ids = {group[key] for group in groups}
            for listener in entry['listeners']:
                refs = [listener['attributes']] + _items(listener.get('rules', {}), 'Rules', 'Rule')
                for ref in refs:
                    if ref.get(key) and ref[key] not in ids:
                        groups.append({key: ref[key]})
                        ids.add(ref[key])
            for group in groups:
                attributes = read(attr, {key: group[key]})
                if attributes.get(key) != group[key]:
                    raise ExportError('CLB 服务器组详情缺失或身份不匹配')
                check_backends(attributes)
                entry['serverGroups'].append({'kind': kind, 'listAttributes': group,
                                              'attributes': attributes})
        return entry

    rows = listing()
    for field, action in [('serverCertificates', 'DescribeServerCertificates'),
                          ('caCertificates', 'DescribeCACertificates')]:
        result[field] = {}
        try:
            result[field] = read(action)
        except Exception as exc:
            warn(action, {}, exc=exc)
    for index, row in enumerate(rows):
        params = {'LoadBalancerId': row['LoadBalancerId']}
        context = row
        if str(row.get('LoadBalancerStatus', '')).lower() in ('inactive', 'locked'):
            warn('DescribeLoadBalancers', params, row, skipped=True, reason='non_active_status')
        else:
            try:
                detail = read('DescribeLoadBalancerAttribute', params)
                context = {**row, 'LoadBalancerStatus': detail.get('LoadBalancerStatus') or row.get('LoadBalancerStatus', '')}
                if str(context['LoadBalancerStatus']).lower() in ('inactive', 'locked'):
                    warn('DescribeLoadBalancerAttribute', params, context, skipped=True, reason='non_active_status')
                else:
                    result['instances'].append(collect_instance(row, detail))
            except Exception as exc:
                warn(*last_request, row=context, exc=exc, skipped=True)
        _save(out / 'slb-all.json', result)
        print(f'CLB OpenAPI：{index + 1}/{len(rows)} {row["LoadBalancerId"]} '
              f'{row.get("LoadBalancerName", "")}', flush=True)

    # 补全默认组后端 IP（与控制台采集相同的两条证据链）。
    missing = set()
    for entry in result['instances']:
        health = _items(entry['health'], 'BackendServers', 'BackendServer')
        for attrs in [entry['attributes']] + [g['attributes'] for g in entry['serverGroups']]:
            for server in _items(attrs, 'BackendServers', 'BackendServer'):
                if server.get('ServerIp'):
                    continue
                ips = {h['ServerIp'] for h in health
                       if h.get('ServerIp') and h.get('ServerId') == server.get('ServerId')}
                if len(ips) == 1:
                    server['ResolvedServerIp'] = ips.pop()
                    server['ResolvedIpSource'] = 'DescribeHealthStatus'
                elif server.get('Type') == 'ecs' and server.get('ServerId'):
                    missing.add(server['ServerId'])
    result['backendEcsInstances'] = []
    for start in range(0, len(missing), 100):
        params = {'InstanceIds': json.dumps(sorted(missing)[start:start + 100]), 'PageSize': 100}
        try:
            value = read('DescribeInstances', params, api=ecs)
            instances = _items(value, 'Instances', 'Instance')
            if any(not isinstance(instance, dict) or not instance.get('InstanceId') for instance in instances):
                raise ExportError('CLB 后端 ECS 记录缺少身份')
            result['backendEcsInstances'].extend(instances)
        except Exception as exc:
            warn('DescribeInstances', params, exc=exc)
    for entry in result['instances']:
        for attrs in [entry['attributes']] + [g['attributes'] for g in entry['serverGroups']]:
            for server in _items(attrs, 'BackendServers', 'BackendServer'):
                match = next((e for e in result['backendEcsInstances']
                              if e['InstanceId'] == server.get('ServerId')), None)
                if match and not server.get('ServerIp') and not server.get('ResolvedServerIp'):
                    ips = (match.get('VpcAttributes', {}).get('PrivateIpAddress', {}).get('IpAddress', [])
                           or _items(match, 'InnerIpAddress', 'IpAddress'))
                    if len(ips) == 1:
                        server['ResolvedServerIp'] = ips[0]
                        server['ResolvedIpSource'] = 'DescribeInstances'

    result['instanceSetStable'] = {x['LoadBalancerId'] for x in listing()} == {x['LoadBalancerId'] for x in rows}
    result['completedAt'] = _now()
    result['complete'] = not result['errors'] and result['instanceSetStable']
    result['partial'] = bool(result['warnings'])
    _save(out / 'slb-all.json', result)
    print(json.dumps({'output': str(out), 'complete': result['complete'],
                      'partial': result['partial'], 'errors': result['errors'],
                      'warnings': result['warnings']}, ensure_ascii=False), flush=True)
    if not result['complete']:
        reasons = []
        if result['errors']:
            actions = '、'.join(sorted({error['action'] for error in result['errors']}))
            reasons.append(f'{len(result["errors"])} 项接口请求失败（{actions}），详见采集日志')
        if not result['instanceSetStable']:
            reasons.append('采集期间实例清单发生变化')
        raise ExportError('CLB 采集不完整：' + '；'.join(reasons))
    return result


# --------------------------------------------------------------------------- NAT

def collect_nat_ak(access_key_id, access_key_secret, out=None, region=DEFAULT_REGION):
    """通过 VPC OpenAPI 采集 NAT 网关与 DNAT 条目，输出与 export_nat.collect 一致。"""
    client = RpcClient(access_key_id, access_key_secret, f'vpc.{region}.aliyuncs.com', '2016-04-28')
    out = Path(out) if out is not None else Path('nat-export')

    def paged(action, outer, inner, key, params=None):
        rows, expected, page = [], None, 1
        while True:
            value = client.call(action, {'RegionId': region, **(params or {}),
                                         'PageNumber': page, 'PageSize': 50})
            total = value.get('TotalCount')
            if type(total) is not int or total < 0:
                raise ExportError(f'{action} 缺少有效总数')
            if expected is None:
                expected = total
            elif expected != total:
                raise ExportError(f'{action} 采集期间总数变化')
            batch = _items(value, outer, inner)
            rows.extend(batch)
            if len(rows) >= expected:
                _page_guard(expected, rows, key, action)
                return rows
            if not batch:
                raise ExportError(f'{action} 分页提前结束')
            page += 1
            if page > 1000:
                raise ExportError(f'{action} 分页超过安全上限')

    def gateways():
        return paged('DescribeNatGateways', 'NatGateways', 'NatGateway', 'NatGatewayId')

    rows = gateways()
    collected = []
    for gateway in rows:
        table_ids = (gateway.get('ForwardTableIds') or {}).get('ForwardTableId') or []
        if not isinstance(table_ids, list) or len(set(table_ids)) != len(table_ids):
            raise ExportError('NAT 网关 DNAT 表清单缺失或重复')
        entries = []
        for table in table_ids:
            entries.extend({**entry, 'ForwardTableId': table} for entry in paged(
                'DescribeForwardTableEntries', 'ForwardTableEntries', 'ForwardTableEntry',
                'ForwardEntryId', {'ForwardTableId': table}))
        if len({entry['ForwardEntryId'] for entry in entries}) != len(entries):
            raise ExportError('NAT 网关 DNAT 条目 ID 重复')
        collected.append({'attributes': gateway, 'entries': entries})
        print(f'NAT OpenAPI：{gateway.get("NatGatewayId")} 已采集 {len(entries)} 条 DNAT', flush=True)

    def membership(items):
        return {row['NatGatewayId']: sorted((row.get('ForwardTableIds') or {}).get('ForwardTableId') or [])
                for row in items}

    if membership(rows) != membership(gateways()):
        raise ExportError('NAT 网关或 DNAT 表清单在采集期间变化')
    result = {'complete': True, 'region': region,
              'source': f'https://vpc.{region}.aliyuncs.com (AccessKey)',
              'collectedAt': _now(), 'gateways': collected}
    out.mkdir(parents=True, exist_ok=True)
    _save(out / 'nat-all.json', result)
    return result


# --------------------------------------------------------------------------- DMS

def _dms_login_status(row):
    mode = ((row.get('StandardGroup') or {}).get('GroupMode') or '').upper()
    return '免登录' if (mode == 'COMMON' or str(row.get('SellTrust', '')).lower() == 'true') else '未登录'


def _normalize_dms(row, ip):
    host = row.get('Host') or ''
    port = row.get('Port')
    region = row.get('EcsRegion') or ''
    kind = (row.get('InstanceType') or '').lower()
    mode = ((row.get('StandardGroup') or {}).get('GroupMode') or '').upper()
    return {
        '实例名称': row.get('InstanceAlias') or '',
        '实例ID': str(row.get('InstanceId') or ''),
        'dmsInstanceId': str(row.get('InstanceId') or ''),
        '实例地址': (host + ':' + str(port)) if port else host,
        'host': host,
        'port': port,
        '类型': DB_TYPES.get(kind, row.get('InstanceType') or ''),
        'dbType': kind,
        '实例地区': REGIONS.get(region, region),
        'regionId': region,
        '实例IP': ip,
        '登录状态': _dms_login_status(row),
        'controlMode': mode or 'NONE_CONTROL',
        'sellTrust': str(row.get('SellTrust', '')).lower() == 'true',
        'cloudInstanceId': '', 'vpcId': row.get('VpcId') or '',
        '实例来源': row.get('InstanceSource') or '',
        '环境': ENV_TYPES.get(row.get('EnvType'), row.get('EnvType') or ''),
        'envType': row.get('EnvType') or '',
        '实例状态': row.get('State') or '',
        '登录方式': '',
        '版本': '',
    }


def collect_dms_ak(access_key_id, access_key_secret, out=None, tid=None):
    """通过 DMS Enterprise OpenAPI 采集数据库实例，输出与 export_dms.collect 一致。"""
    client = RpcClient(access_key_id, access_key_secret, 'dms-enterprise.aliyuncs.com', '2018-11-01')
    rows, expected, page = [], None, 1
    while True:
        value = client.call('ListInstances', {'PageNumber': page, 'PageSize': 100, **({'Tid': tid} if tid is not None else {})})
        if value.get('Success') is False:
            raise ExportError('DMS ListInstances 返回失败')
        total = value.get('TotalCount')
        if type(total) is not int or total < 0:
            raise ExportError('DMS ListInstances 缺少有效总数')
        if expected is None:
            expected = total
        elif expected != total:
            raise ExportError('DMS 采集期间实例总数发生变化')
        batch = _items(value, 'InstanceList', 'Instance')
        rows.extend(batch)
        print(f'DMS OpenAPI：第 {page} 页，已读取 {len(rows)} 条', flush=True)
        if len(rows) >= expected:
            break
        if not batch:
            raise ExportError('DMS 分页提前为空')
        page += 1
        if page > 1000:
            raise ExportError('DMS 分页超过安全上限')
    _page_guard(expected, rows, 'InstanceId', 'DMS ListInstances')

    out = Path(out) if out is not None else Path('dms-export')
    out.mkdir(parents=True, exist_ok=True)
    _save(out / 'dms-instances-raw.json', {'TotalCount': expected, 'InstanceList': {'Instance': rows}})
    cache, normalized = {}, []
    for index, row in enumerate(rows, 1):
        ip = resolve_ip(row.get('Host') or '', cache)
        normalized.append(_normalize_dms(row, ip))
        print(f'DMS 解析 IP {index}/{len(rows)} {row.get("Host")} -> {ip or "未解析"}', flush=True)
    instances = merge_duplicates(normalized)
    result = {'available': True, 'source': 'https://dms-enterprise.aliyuncs.com (AccessKey)',
              'collectedAt': _now(), 'tenantId': tid, 'rawRecordCount': len(normalized),
              'totalCount': len(instances), 'instances': instances}
    _save(out / 'dms-instances.json', result)
    fields = ['实例名称', '实例地址', '类型', '实例地区', '实例IP', '登录状态', '实例ID']
    with (out / 'dms-instances.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in instances:
            writer.writerow({key: item[key] for key in fields})
    free = sum(1 for item in instances if item['登录状态'] == '免登录')
    print(json.dumps({'记录数': len(normalized), '实例数': len(instances), '免登录': free,
                      '未登录': len(instances) - free,
                      'IP未解析': [i['实例地址'] for i in instances if not i['实例IP']]},
                     ensure_ascii=False), flush=True)
    return result


# -------------------------------------------------------------------------- Codeup

def _codeup_client(access_key_id, access_key_secret):
    return RoaClient(access_key_id, access_key_secret, CODEUP_HOST, CODEUP_VERSION)


def _codeup_time(value):
    text = str(value or '').strip()
    if not text:
        raise ExportError('Codeup 提交记录缺少时间')
    text = text.replace('Z', '+00:00')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', text):
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ_SHANGHAI)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ExportError('Codeup 提交时间缺少时区')
    return parsed


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return default


def resolve_codeup_organization(folder, access_key_id, access_key_secret, explicit=''):
    """确定 Codeup 企业 ID：显式配置 > 当前/历史快照 > ListOrganizations。"""
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    folder = Path(folder)
    for candidate in [folder / 'repositories.json'] + sorted(
            (folder.parent).glob('*/repositories.json'), key=lambda path: path.stat().st_mtime, reverse=True):
        data = _read_json(candidate, {}) or {}
        for group in data.get('groups') or []:
            path = str(group.get('path') or '')
            if '/' in path and path.split('/')[0]:
                return path.split('/')[0]
    value = _codeup_client(access_key_id, access_key_secret).call('ListOrganizations', '/organizations/list')
    organizations = value.get('result') or []
    if organizations and organizations[0].get('id'):
        return str(organizations[0]['id'])
    raise ExportError('无法确定 Codeup 企业 ID：请在 Settings 填写「Codeup 企业 ID」，或先保留一份已采集的仓库快照')


def _codeup_groups(client, organization, repos):
    details, groups = {}, {}
    def load(group_id):
        key = str(group_id)
        if key in details:
            return details[key]
        try:
            value = client.call('GetGroupDetail', '/repository/groups/get_detail',
                                {'organizationId': organization, 'groupId': key})
            details[key] = value.get('result') or None
        except AuthenticationError:
            raise
        except Exception:
            details[key] = None
        return details[key]

    for repo in repos:
        group_id = repo.get('namespaceId')
        while group_id and str(group_id) not in groups:
            detail = load(group_id)
            if not detail:
                break
            path = detail.get('pathWithNamespace') or ''
            groups[str(detail.get('id'))] = {
                'id': str(detail.get('id')),
                'group_name': detail.get('name') or path.rsplit('/', 1)[-1],
                'english_name': detail.get('path') or path.rsplit('/', 1)[-1],
                'chinese_name': detail.get('description') or '',
                'updated_at': None,
                'repository_count': 0,
                'full_path': path,
                'url': detail.get('webUrl') or '',
                'subgroup_count': None,
                'date_source_field': None,
            }
            parent = detail.get('parentId')
            if not parent or str(parent) == str(detail.get('id')):
                break
            group_id = parent
    result = list(groups.values())
    for group in result:
        prefix = group['full_path'] + '/'
        group['repository_count'] = sum(1 for repo in repos if str(repo.get('path', '')).startswith(prefix))
    # 父组可能只通过子组的 parentId 发现；按路径长度排序，方便上层选择最近父组。
    result.sort(key=lambda item: len(item['full_path']), reverse=True)
    return result


def _codeup_repos(client, organization):
    rows, total, page = [], None, 1
    while True:
        value = client.call('ListRepositories', '/repository/list',
                            {'organizationId': organization, 'page': page, 'perPage': 100})
        batch = value.get('result') or []
        if total is None:
            total = value.get('total')
        elif total != value.get('total'):
            raise ExportError('Codeup 采集期间仓库总数发生变化')
        rows.extend(batch)
        print(f'Codeup OpenAPI：第 {page} 页，已读取 {len(rows)} 个仓库', flush=True)
        if type(total) is not int or total < 0:
            raise ExportError('Codeup 返回的仓库总数无效')
        if len(rows) >= total:
            break
        if not batch:
            raise ExportError('Codeup 仓库分页提前为空')
        page += 1
        if page > 1000:
            raise ExportError('Codeup 仓库分页超过安全上限')
    _page_guard(total, rows, 'Id', 'Codeup ListRepositories')
    after = client.call('ListRepositories', '/repository/list',
                        {'organizationId': organization, 'page': 1, 'perPage': 1})
    if after.get('total') != total:
        raise ExportError('Codeup 可访问仓库总数在采集期间发生变化')
    return rows


def _codeup_branches(client, organization, repository_id):
    rows, total, page = [], None, 1
    while True:
        value = client.call('ListRepositoryBranches', f'/repository/{repository_id}/branches',
                            {'organizationId': organization, 'page': page, 'pageSize': 100})
        batch = value.get('result') or []
        if total is None:
            total = value.get('total')
        elif total != value.get('total'):
            raise ExportError('Codeup 采集期间分支总数发生变化')
        rows.extend(batch)
        if type(total) is not int or total < 0:
            raise ExportError('Codeup 返回的分支总数无效')
        if len(rows) >= total:
            break
        if not batch:
            raise ExportError('Codeup 分支分页提前为空')
        page += 1
        if page > 1000:
            raise ExportError('Codeup 分支分页超过安全上限')
    names = {row.get('name') for row in rows}
    if len(names) != len(rows) or any(not name for name in names):
        raise ExportError('Codeup 分支分页重复或缺少名称')
    return rows


def _codeup_history(client, organization, repository_id, heads):
    known = {}
    reported = None
    pending = dict.fromkeys(reversed(heads))
    while pending:
        head, _ = pending.popitem()
        if head in known:
            continue
        value = client.call('ListRepositoryCommits', f'/repository/{repository_id}/commits',
                            {'organizationId': organization, 'refName': head,
                             'page': 1, 'pageSize': 100})
        rows = value.get('result') or []
        reported = value.get('total') if reported is None else reported
        if not any(row.get('id') == head for row in rows):
            raise ExportError('Codeup 提交历史不完整，请求的提交未返回')
        for commit in rows:
            commit_id = commit.get('id')
            parents = commit.get('parentIds')
            if not commit_id or not isinstance(parents, list):
                raise ExportError('Codeup 提交记录缺少 ID 或父提交')
            stamp = _codeup_time(commit.get('committedDate'))
            pending.pop(commit_id, None)
            if commit_id in known:
                continue
            known[commit_id] = stamp.isoformat()
            for parent in parents:
                if parent and parent not in known:
                    pending[parent] = None
    daily = Counter(datetime.fromisoformat(stamp).astimezone(TZ_SHANGHAI).date().isoformat()
                    for stamp in known.values())
    latest = max(known.items(), key=lambda pair: datetime.fromisoformat(pair[1]), default=None)
    return known, daily, latest, reported


def codeup_repo_ak(client, organization, row):
    path = row.get('pathWithNamespace') or ''
    web_url = row.get('webUrl') or ('https://codeup.aliyun.com/' + path)
    try:
        host, path, url = normalize_url(web_url)
    except ValueError as exc:
        raise ExportError(f'Codeup 返回的仓库地址无法识别：{exc}') from None
    if host != 'codeup.aliyun.com':
        raise ExportError('Codeup 返回非预期仓库地址')
    name = row.get('name') or path.rsplit('/', 1)[-1]
    repository_id = row.get('Id')
    if not repository_id or not path:
        raise ExportError('Codeup 仓库记录缺少 ID 或完整路径')
    branches = _codeup_branches(client, organization, repository_id)
    heads = []
    for branch in branches:
        commit = branch.get('commit') or {}
        stamp = _codeup_time(commit.get('committedDate'))
        if not commit.get('id'):
            raise ExportError('Codeup 分支缺少最近提交 ID')
        heads.append((stamp, stamp.isoformat(), commit['id'], branch.get('name')))
    latest_head = max(heads, key=lambda item: item[0], default=None)
    repo = {
        'codeupId': str(repository_id),
        'name': name,
        'path': path,
        'url': url,
        'namespaceId': str(row.get('namespaceId') or ''),
        'groupName': path.split('/')[-2] if path.count('/') >= 1 else '',
        'description': row.get('description') or '',
        'branches': len(branches),
        'reportedBranchCount': len(branches),
        'mergeRequests': None,
        'commits': None,
        'lastCommittedAt': latest_head[1] if latest_head else '',
        'lastCommitId': latest_head[2] if latest_head else '',
        'lastCommitBranch': latest_head[3] if latest_head else '',
        'commitTimeStatus': 'available' if latest_head else 'empty',
        'commitTimeScope': 'current_branch_heads',
        'reportedCommits': None,
        'commitHistoryComplete': False,
        'commitHistoryScope': 'all_current_branches_unique_sha',
        'commitDailyCounts': {},
    }
    known, daily, latest, reported = _codeup_history(
        client, organization, repository_id, [item[2] for item in sorted(heads, reverse=True)])
    repo.update(reportedCommits=reported, commits=len(known), commitHistoryComplete=True,
                commitTimeScope='all_current_branches_unique_sha',
                commitDailyCounts=dict(sorted(daily.items())))
    if latest:
        repo.update(lastCommittedAt=latest[1], lastCommitId=latest[0])
        if latest[0] not in {item[2] for item in heads}:
            repo['lastCommitBranch'] = ''
    return repo


def _codeup_prefix():
    match = re.match(r'^(slot:\d+)', threading.current_thread().name)
    return (match.group(1).replace(':', '-') + '-codeup-') if match else ''


def collect_codeup_ak(folder, access_key_id, access_key_secret, organization_id=''):
    """通过云效 OpenAPI 采集 Codeup，输出结构与 daily_collection.collect_codeup 一致。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    client = _codeup_client(access_key_id, access_key_secret)
    organization = resolve_codeup_organization(folder, access_key_id, access_key_secret, organization_id)
    print(f'Codeup OpenAPI：企业 {organization}', flush=True)
    raw_repos = _codeup_repos(client, organization)
    groups = _codeup_groups(client, organization, [
        {'namespaceId': row.get('namespaceId'), 'path': row.get('pathWithNamespace') or ''}
        for row in raw_repos])
    _save(folder / 'codeup-groups-raw.json', {'groups': groups})
    _save(folder / 'progress.json', {'phase': '正在采集 Codeup 仓库分支和提交历史',
                                     'completed': 0, 'total': len(raw_repos)})
    (folder / 'commit-times').mkdir(parents=True, exist_ok=True)
    repos = []
    with ThreadPoolExecutor(max_workers=CODEUP_WORKERS, thread_name_prefix=_codeup_prefix()) as pool:
        tasks = [pool.submit(codeup_repo_ak, client, organization, row) for row in raw_repos]
        for index, task in enumerate(as_completed(tasks)):
            try:
                repo = task.result()
            except Exception:
                for pending in tasks:
                    pending.cancel()
                raise
            repos.append(repo)
            _save(folder / 'commit-times' / (repo['codeupId'] + '.json'), repo)
            _save(folder / 'progress.json', {'phase': '正在采集 Codeup 仓库分支和提交历史',
                                             'completed': index + 1, 'total': len(raw_repos),
                                             'current': repo['name']})
            print(f'Codeup OpenAPI 仓库完成：{index + 1}/{len(raw_repos)} {repo["name"]}', flush=True)
    if len({repo['url'] for repo in repos}) != len(repos):
        raise ExportError('Codeup 仓库完整地址重复')
    repos.sort(key=lambda item: item['url'])
    result = {'complete': True, 'collectedAt': _now(), 'namespaceId': organization,
              'scope': 'mine', 'groups': groups, 'repos': repos}
    _save(folder / 'codeup.json', result)
    fields = ['代码组描述', '代码组名', '代码库数', '完整路径']
    with (folder / 'codeup-groups.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for group in groups:
            writer.writerow({'代码组描述': group['chinese_name'], '代码组名': group['group_name'],
                             '代码库数': group['repository_count'], '完整路径': group['full_path']})
    fields = ['组名', '库名', 'Git地址', '分支数', '合并请求数', '提交数', '最近代码提交时间']
    with (folder / 'codeup-repositories.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for repo in repos:
            writer.writerow({'组名': repo['groupName'], '库名': repo['name'], 'Git地址': repo['url'],
                             '分支数': repo['branches'], '合并请求数': '',
                             '提交数': repo['commits'], '最近代码提交时间': repo['lastCommittedAt']})
    return result


def _codeup_path_status(client, organization, path):
    """GetRepository 按路径探测单个仓库：区分存在 / 不存在 / 无权限 / 无法确认。"""
    try:
        value = client.call('GetRepository', '/repository/get',
                            {'organizationId': organization, 'identity': path})
    except OpenApiError as exc:
        code = str(exc.code or '').upper()
        if exc.status == 404 or code == 'SYSTEM_NOT_FOUND_ERROR':
            return {'access': '地址不存在', 'reason': 'Codeup 返回 SYSTEM_NOT_FOUND_ERROR：地址不存在'}
        if exc.status == 403 or 'FORBIDDEN' in code:
            return {'access': '暂无权限访问', 'reason': 'Codeup 返回 FORBIDDEN：暂无权限访问'}
        return {'access': '无法确认', 'reason': 'Codeup 接口未提供明确访问状态'}
    if value.get('success') is True and isinstance(value.get('repository'), dict):
        return {'access': '是', 'reason': '仓库查询成功（OpenAPI 读取权限）'}
    return {'access': '无法确认', 'reason': 'Codeup 接口未提供明确访问状态'}


def codeup_reference_status(devops, codeup, access_key_id, access_key_secret, organization_id=''):
    """DevOps 引用但未出现在 Codeup 列表中的仓库，用 GetRepository 逐路径核对访问状态。

    与 daily_collection.codeup_reference_status 的输出结构一致（url → {access, reason}），
    区别仅在于走 OpenAPI，不依赖网页 Cookie。
    """
    organization = str(organization_id or '').strip() or str((codeup or {}).get('namespaceId') or '')
    if not organization:
        raise ExportError('无法确定 Codeup 企业 ID，无法核对引用仓库状态')
    client = _codeup_client(access_key_id, access_key_secret)
    listed = {repo.get('url') for repo in (codeup or {}).get('repos') or []}
    references = set()
    for app in (devops or {}).get('apps') or []:
        references.add(app.get('repository') or '')
        for rows in (app.get('envs') or {}).values():
            references.update(row.get('repository') or '' for row in rows or [])
    result = {}
    for raw in sorted(references):
        if not raw:
            continue
        try:
            host, path, url = normalize_url(raw)
        except ValueError:
            continue
        if host != 'codeup.aliyun.com' or url in listed:
            continue
        result[url] = _codeup_path_status(client, organization, path)
    return result
