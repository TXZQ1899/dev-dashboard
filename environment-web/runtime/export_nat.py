"""Read-only, complete Shanghai NAT gateway and DNAT export using the console session."""
from export_devops import AuthenticationError
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from export_slb import NoRedirect, save

HOST = 'https://vpc.console.aliyun.com'
REGION = 'cn-shanghai'
SOURCE = HOST + '/nat/' + REGION + '/nats'


class NatClient:
    def __init__(self, cookie):
        self.cookie = cookie.strip()
        if self.cookie.lower().startswith('cookie:'):
            self.cookie = self.cookie.split(':', 1)[1].strip()
        if not self.cookie or '\n' in self.cookie or '\r' in self.cookie:
            raise ValueError('阿里云 Cookie 必须为单行')
        self.opener = build_opener(NoRedirect)
        try:
            with self.opener.open(Request(SOURCE, headers=self.headers()), timeout=40) as response:
                html = response.read().decode()
        except HTTPError as error:
            if error.code in (301, 302, 303, 307, 308, 401):
                raise AuthenticationError('NAT Cookie expired') from None
            if error.code == 403: raise
            raise ValueError(f'NAT 控制台 HTTP {error.code}') from None
        token = re.search(r'SEC_TOKEN\s*:\s*["\x27]([^"\x27]+)', html)
        if not token:
            raise ValueError('NAT 控制台未返回会话令牌，请在 Settings 更新统一阿里云 Cookie')
        self.token = token.group(1)

    def headers(self):
        return {'Cookie': self.cookie, 'Referer': SOURCE, 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

    def call(self, action, params=None):
        if action not in ('DescribeNatGateways', 'DescribeForwardTableEntries'):
            raise ValueError('Only NAT inventory and DNAT read actions are allowed')
        data = dict(action=action, product='vpc', region=REGION, sec_token=self.token,
                    params=json.dumps({'RegionId': REGION, **(params or {})}))
        request = Request(HOST + '/data/api.json?' + urlencode({'action': action}),
                          data=urlencode(data).encode(),
                          headers={**self.headers(), 'Content-Type': 'application/x-www-form-urlencoded'})
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=45) as response:
                    result = json.load(response)
                if str(result.get('code')).upper() in ('401','NOT_LOGIN','UNAUTHORIZED','LOGIN_REQUIRED') or result.get('isLogin') is False:
                    raise AuthenticationError('NAT Cookie expired')
                if str(result.get('code')) != '200' or not isinstance(result.get('data'), dict):
                    raise ValueError(f'{action} 请求失败，请检查阿里云登录态与 NAT 读取权限')
                value = result['data']
                if value.get('Code') and value['Code'] not in ('200', 'Success'):
                    raise ValueError(f'{action} 返回错误，请检查 NAT 读取权限')
                return value
            except HTTPError as error:
                if error.code in (301,302,303,307,308,401): raise AuthenticationError('NAT Cookie expired') from None
                if error.code == 403: raise
                if error.code < 500:
                    raise ValueError(f'{action} HTTP {error.code}，请检查登录态与 NAT 读取权限') from None
            except OSError:
                pass
            if attempt < 2:
                time.sleep(attempt + 1)
        raise ValueError(f'{action} 服务暂不可用或网络超时')


def paged(client, action, outer, inner, key, params=None):
    rows, expected = [], None
    for page in range(1, 10001):
        result = client.call(action, {**(params or {}), 'PageNumber': page, 'PageSize': 50})
        if type(result.get('TotalCount')) is not int or result['TotalCount'] < 0:
            raise ValueError(f'{action} 缺少有效总数')
        if expected is None:
            expected = result['TotalCount']
        if expected != result['TotalCount']:
            raise ValueError(f'{action} 采集期间总数变化')
        batch = result.get(outer, {}).get(inner)
        if not isinstance(batch, list):
            raise ValueError(f'{action} 返回不完整清单')
        rows.extend(batch)
        if len(rows) >= expected:
            if len(rows) != expected or any(not row.get(key) for row in rows) or len({row[key] for row in rows}) != expected:
                raise ValueError(f'{action} 总数或唯一 ID 核对失败')
            return rows
        if not batch:
            raise ValueError(f'{action} 分页提前结束')
    raise ValueError(f'{action} 超出分页上限')


def collect(cookie, out, client=None):
    client = client or NatClient(cookie)
    def gateways():
        return paged(client, 'DescribeNatGateways', 'NatGateways', 'NatGateway', 'NatGatewayId')
    rows = gateways()
    collected = []
    for gateway in rows:
        table_ids = gateway.get('ForwardTableIds', {}).get('ForwardTableId')
        if not isinstance(table_ids, list) or len(set(table_ids)) != len(table_ids):
            raise ValueError('NAT 网关 DNAT 表清单缺失或重复')
        entries = []
        for table in table_ids:
            entries.extend({**entry, 'ForwardTableId': table} for entry in paged(
                client, 'DescribeForwardTableEntries', 'ForwardTableEntries', 'ForwardTableEntry',
                'ForwardEntryId', {'ForwardTableId': table}))
        if len({entry['ForwardEntryId'] for entry in entries}) != len(entries):
            raise ValueError('NAT 网关 DNAT 条目 ID 重复')
        collected.append({'attributes': gateway, 'entries': entries})
    # Table membership is also checked: a newly created table must not be silently omitted.
    def membership(items):
        return {row['NatGatewayId']: sorted(row.get('ForwardTableIds', {}).get('ForwardTableId', [])) for row in items}
    if membership(rows) != membership(gateways()):
        raise ValueError('NAT 网关或 DNAT 表清单在采集期间变化')
    result = dict(complete=True, region=REGION, source=SOURCE,
                  collectedAt=datetime.now().astimezone().isoformat(), gateways=collected)
    save(Path(out) / 'nat-all.json', result)
    return result


def normalize_nat(raw):
    if not raw.get('complete'):
        raise ValueError('NAT collection incomplete')
    gateways = []
    for item in raw['gateways']:
        gateway = item['attributes']
        entries = []
        for entry in item['entries']:
            required = ('ForwardEntryId', 'ForwardTableId', 'ExternalIp', 'ExternalPort', 'InternalIp', 'InternalPort', 'IpProtocol', 'Status')
            if any(entry.get(key) is None or str(entry[key]).strip() == '' for key in required):
                raise ValueError('DNAT 条目缺少 IP、端口、协议或状态')
            entries.append(dict(id=entry['ForwardEntryId'], tableId=entry['ForwardTableId'],
                                name=entry.get('ForwardEntryName', ''), externalIp=entry['ExternalIp'],
                                externalPort=str(entry['ExternalPort']), internalIp=entry['InternalIp'],
                                internalPort=str(entry['InternalPort']), protocol=entry['IpProtocol'], status=entry['Status']))
        gateways.append(dict(id=gateway['NatGatewayId'], name=gateway.get('Name', ''),
                             status=gateway.get('Status', ''), vpcId=gateway.get('VpcId', ''),
                             entries=entries))
    return dict(available=True, collectedAt=raw['collectedAt'], region=raw['region'], gateways=gateways)
