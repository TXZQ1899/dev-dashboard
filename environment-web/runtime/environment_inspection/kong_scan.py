"""Read known environment-scoped Kong Admin APIs; retain failed coverage."""
import json
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

# Kept in step with lib/topology/request/kong.ts; no gateway is inferred.
GATEWAYS = {'PRODUCT':['10.179.4.220', '10.179.4.73'], 'TEST':['10.179.2.144'], 'SIMULATION':[]}


def scan_kong(environment, endpoints, opener=urlopen):
    wanted = {f'{ip}:{port}' for ip, port in endpoints}
    result = {'matches':[], 'queried':0, 'warnings':[]}
    if not wanted:
        result['warnings'].append('应用端点不可用')
        return result
    if not GATEWAYS.get(environment):
        result['warnings'].append('此环境的 Kong Admin 未配置')
        return result
    for host in GATEWAYS[environment]:
        base = f'http://{host}:8001'
        def pages(path):
            seen, result = set(), []
            while path:
                parsed = urlsplit(path)
                if parsed.scheme or parsed.netloc or not path.startswith('/') or path.startswith('//') or path in seen or len(seen) >= 100:
                    raise ValueError('无效或超限的分页链接')
                seen.add(path)
                with opener(base+path, timeout=10) as response:
                    body = json.load(response)
                if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                    raise ValueError('无效响应')
                result.extend(body['data'])
                path = body.get('next')
            return result
        try:
            upstreams, services, routes = pages('/upstreams?size=100'), pages('/services?size=100'), pages('/routes?size=100')
            found = []
            for upstream in upstreams:
                targets = pages('/upstreams/'+quote(str(upstream['id']), safe='')+'/targets?size=100')
                for target in targets:
                    if target.get('target') not in wanted: continue
                    related = [s for s in services if s.get('host') == upstream.get('name')]
                    found.append({'gateway':host, 'upstream':upstream, 'target':target,
                        'services':[{'service':service, 'routes':[r for r in routes if (r.get('service') or {}).get('id') == service.get('id')]} for service in related]})
            result['matches'].extend(found)
            result['queried'] += 1
        except Exception as exc:
            result['warnings'].append(f'{host} 查询失败：{type(exc).__name__}')
    result['warnings'].append('仅按字面 IP:端口匹配；未核验 Target 健康状态')
    return result
