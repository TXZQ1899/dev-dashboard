"""Read-only export of Aliyun DMS instances using console cookie login (no OpenAPI AK).

Console APIs used (same calls the DMS web console performs):
  POST /dms/metastore/instances/list   -> all instances the account can see
Instance IP is resolved from the instance address via AliDNS DoH, because the
local fake-IP DNS resolver cannot return the real address.
"""
from export_devops import AuthenticationError
import argparse
import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
HOST = 'https://dms.aliyun.com'
LIST_API = '/dms/metastore/instances/list'
DOH_API = 'https://223.5.5.5/resolve'
REGIONS = {
    'cn-shanghai': '华东2（上海）', 'cn-hangzhou': '华东1（杭州）',
    'cn-shenzhen': '华南1（深圳）', 'cn-beijing': '华北2（北京）',
    'cn-zhangjiakou': '华北3（张家口）', 'cn-hongkong': '中国香港',
    'cn-guangzhou': '华南1（广州）', 'cn-chengdu': '西南1（成都）',
    'ap-southeast-1': '新加坡', 'ap-southeast-5': '马来西亚（吉隆坡）',
    'ap-northeast-1': '日本（东京）',
}
DB_TYPES = {'mysql': 'MySQL', 'redis': 'Redis', 'mongo': 'MongoDB', 'postgresql': 'PostgreSQL',
            'drds': 'DRDS', 'clickhouse': 'ClickHouse', 'sqlserver': 'SQL Server',
            'polar_mysql': 'PolarDB MySQL', 'polardb': 'PolarDB', 'adb': 'ADB', 'dts': 'DTS'}
ENV_TYPES = {'product': '生产', 'test': '测试', 'pre': '预发', 'simulation': '仿真'}
CSV_FIELDS = ['实例名称', '实例地址', '类型', '实例地区', '实例IP', '登录状态', '实例ID']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuthenticationError('DMS 接口发生重定向，登会话可能已失效，请更新 Cookie')


class Client:
    def __init__(self, cookie=None, timeout=60):
        self.cookie = (cookie if cookie is not None else (BASE / 'codeup-cookie.txt').read_text()).strip()
        if self.cookie.lower().startswith('cookie:'):
            self.cookie = self.cookie.split(':', 1)[1].strip()
        if not self.cookie or '\n' in self.cookie or '\r' in self.cookie:
            raise ValueError('Cookie 必须是非空的单行请求头值')
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def headers(self, json_type=True):
        headers = {'Cookie': self.cookie, 'Referer': HOST + '/', 'User-Agent': 'Mozilla/5.0',
                   'Accept': 'application/json', 'X-Requested-With': 'FETCH'}
        if json_type:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        return headers

    def list_instances(self):
        body = json.dumps({'nodeType': [0, 1]}).encode()
        req = urllib.request.Request(HOST + LIST_API, data=body, headers=self.headers())
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode('utf-8'))
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308, 401, 403):
                    raise AuthenticationError('DMS 登录失效或无权限，请更新 Cookie') from None
                if attempt == 2:
                    raise RuntimeError('DMS 接口返回 HTTP ' + str(exc.code)) from None
            except urllib.error.URLError:
                if attempt == 2:
                    raise RuntimeError('DMS 接口连接失败或超时，请检查网络/VPN') from None
            time.sleep(1 + attempt)
        if not isinstance(payload, dict):
            raise RuntimeError('DMS 接口响应不是对象')
        if str(payload.get('errorCode')) == '401' or payload.get('isLogin') is False:
            raise AuthenticationError('DMS 登录失效，请更新 Cookie')
        if payload.get('success') is not True:
            raise RuntimeError('DMS 接口失败：' + str(payload.get('errorCode')) + ' / ' + str(payload.get('message'))[:180])
        rows = payload.get('root')
        if not isinstance(rows, list):
            raise RuntimeError('DMS 接口响应缺少 root 数组')
        return payload


def resolve_ip(host, cache):
    """Resolve instance address to real IP through AliDNS DoH (local DNS returns fake IPs)."""
    if host in cache:
        return cache[host]
    if host and all(part.isdigit() for part in host.split('.')) and len(host.split('.')) == 4:
        cache[host] = host
        return host
    url = DOH_API + '?' + urllib.parse.urlencode({'name': host, 'type': 'A'})
    answer = ''
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/dns-json'})
        with urllib.request.build_opener().open(req, timeout=20) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if payload.get('Status') == 0:
            ips = [a['data'] for a in payload.get('Answer', []) if a.get('type') == 1]
            answer = ','.join(dict.fromkeys(ips))
    except Exception:
        answer = ''
    cache[host] = answer
    return answer


def control_mode(row):
    if row.get('sellCommon'):
        return 'COMMON'
    return 'STABLE' if row.get('sellStable') else 'NONE_CONTROL'


def login_status(row):
    """Mirror the console instance-list logic: auto-login (免登录) instances carry no login icon."""
    return '免登录' if (control_mode(row) == 'COMMON' or row.get('sellTrust')) else '未登录'


def normalize(row, ip):
    host, port = row.get('host') or '', row.get('port')
    return {
        '实例名称': row.get('alias') or '',
        '实例ID': row.get('instanceId') or '',
        'dmsInstanceId': row.get('id') or '',
        '实例地址': (host + ':' + str(port)) if port else host,
        'host': host,
        'port': port,
        '类型': DB_TYPES.get(row.get('dbType'), row.get('dbType') or ''),
        'dbType': row.get('dbType') or '',
        '实例地区': REGIONS.get(row.get('regionId'), row.get('regionId') or ''),
        'regionId': row.get('regionId') or '',
        '实例IP': ip,
        '登录状态': login_status(row),
        'controlMode': control_mode(row),
        'sellTrust': bool(row.get('sellTrust')),
        '实例来源': row.get('instanceSource') or '',
        '环境': ENV_TYPES.get(row.get('envType'), row.get('envType') or ''),
        'envType': row.get('envType') or '',
        '实例状态': row.get('instanceStatus') or '',
        '登录方式': row.get('loginType') or '',
        '版本': row.get('version') or '',
    }


def merge_duplicates(replacements):
    """DMS keeps several records for one instance address; merge them into one row."""
    merged = []
    for item in replacements:
        key = item['实例地址']
        existing = next((row for row in merged if row['实例地址'] == key), None)
        if existing is None:
            item['dmsInstanceIds'] = [item.pop('dmsInstanceId')]
            item['重复记录'] = 0
            merged.append(item)
            continue
        existing['dmsInstanceIds'].append(item.pop('dmsInstanceId'))
        existing['重复记录'] += 1
        if item['登录状态'] == '免登录':
            existing['登录状态'] = '免登录'
    return merged


def collect(cookie=None, out=None):
    out = Path(out) if out is not None else BASE / 'dms-export'
    out.mkdir(parents=True, exist_ok=True)
    client = Client(cookie)
    payload = client.list_instances()
    rows = payload['root']
    if payload.get('totalCount') is not None and int(payload['totalCount']) != len(rows):
        raise RuntimeError('返回实例数与 totalCount 不一致，采集结果不完整')
    (out / 'dms-instances-raw.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    cache, normalized = {}, []
    for index, row in enumerate(rows, 1):
        ip = resolve_ip(row.get('host') or '', cache)
        normalized.append(normalize(row, ip))
        print('解析 IP {0}/{1} {2} -> {3}'.format(index, len(rows), row.get('host'), ip or '未解析'), flush=True)
    instances = merge_duplicates(normalized)
    result = {'available': True, 'source': HOST + LIST_API,
              'collectedAt': datetime.now(timezone.utc).isoformat(),
              'tenantId': payload.get('tenantId'), 'rawRecordCount': len(normalized),
              'totalCount': len(instances), 'instances': instances}
    (out / 'dms-instances.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (out / 'dms-instances.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in instances:
            writer.writerow({key: item[key] for key in CSV_FIELDS})
    free = sum(1 for i in instances if i['登录状态'] == '免登录')
    unresolved = [i['实例地址'] for i in instances if not i['实例IP']]
    print(json.dumps({'output': str(out), '记录数': len(normalized), '实例数': len(instances),
                      '免登录': free, '未登录': len(instances) - free, 'IP未解析': unresolved}, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description='导出阿里云 DMS 实例清单（免登录/未登录），只读接口')
    parser.add_argument('--cookie-file', type=Path, default=BASE / 'codeup-cookie.txt')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        cookie = args.cookie_file.read_text(encoding='utf-8') if args.cookie_file.exists() else None
        collect(cookie, args.output)
        return 0
    except (AuthenticationError, RuntimeError, OSError, ValueError) as exc:
        print('DMS 导出未完成：' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
