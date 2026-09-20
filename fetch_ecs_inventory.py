from export_devops import AuthenticationError
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(__file__).resolve().parent
OUT = BASE / 'ecs-export-cn-shanghai'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuthenticationError('API redirected; session may have expired (Cookie not forwarded).')


def collect(cookie, out):
    cookie = cookie.strip()
    OUT = Path(out)
    if cookie.lower().startswith('cookie:'):
        cookie = cookie.split(':', 1)[1].strip()
    if '\n' in cookie or '\r' in cookie:
        raise RuntimeError('Expected a single-line Cookie header.')
    opener = urllib.request.build_opener(NoRedirect())
    OUT.mkdir(exist_ok=True)
    instances, seen, counts = [], set(), []
    total = None
    page = 1
    while total is None or page <= math.ceil(total / 100):
        params = urllib.parse.urlencode({
            '__preventCache': str(time.time_ns()),
            'pageNumber': page, 'pageSize': 100,
            'additionalAttributes': '["SECURE_BOOT","NETWORK_PRIMARY_ENI_IP"]',
            'regionId': 'cn-shanghai',
        })
        req = urllib.request.Request(
            'https://ecsnew.console.aliyun.com/instance/instance/list.json?' + params,
            headers={'Cookie': cookie, 'Accept': 'application/json',
                     'Referer': 'https://ecs.console.aliyun.com/',
                     'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=60) as response:
            raw = response.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            raise RuntimeError('API did not return JSON; check session validity.') from None
        if str(payload.get('code')).upper() in ('401','NOT_LOGIN','UNAUTHORIZED','LOGIN_REQUIRED') or payload.get('isLogin') is False:
            raise AuthenticationError('ECS Cookie expired')
        if str(payload.get('code')) != '200' or payload.get('successResponse') is not True:
            raise RuntimeError('API reported failure; code=' + str(payload.get('code')))
        data = payload['data']
        current_total = int(data['TotalCount'])
        if total is None:
            total = current_total
        if current_total != total:
            raise RuntimeError('Instance total changed during pagination; rerun for a consistent export.')
        if int(data['PageNumber']) != page or int(data['PageSize']) != 100:
            raise RuntimeError('Unexpected pagination metadata.')
        rows = data['Instances']['Instance']
        expected = min(100, total - (page - 1) * 100)
        if len(rows) != expected:
            raise RuntimeError('Unexpected page length.')
        for row in rows:
            instance_id = row['InstanceId']
            if instance_id in seen:
                raise RuntimeError('Duplicate instance ID across pages.')
            if row.get('RegionId') != 'cn-shanghai':
                raise RuntimeError('Unexpected region.')
            seen.add(instance_id)
        instances.extend(rows)
        counts.append(len(rows))
        (OUT / f'ecs-list-page-{page}.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        print(f'Page {page}: {len(rows)} instances; total={total}', flush=True)
        page += 1
    if len(instances) != total:
        raise RuntimeError('Export count does not match API total.')
    result = {'RegionId': 'cn-shanghai',
              'FetchedAt': datetime.now(timezone.utc).isoformat(),
              'TotalCount': total, 'PageCounts': counts,
              'Instances': {'Instance': instances}}
    target = OUT / 'ecs-list-all.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'Validated {len(seen)} unique instances. Saved: {target}', flush=True)
    return result


def main():
    collect((BASE / 'aliyun-cookie.txt').read_text(), OUT)


if __name__ == '__main__':
    main()
