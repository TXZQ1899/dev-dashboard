"""Read DNS records in the container and correlate only evidenced names and IPs."""
import base64
import json
import os
import sys
from pathlib import Path


def normal(value):
    return str(value or '').strip().rstrip('.').lower()


def scan(data, version, names, ips, static_path=Path('/app/lib/dns-snapshot.json')):
    sources = []; warnings = []; records = []
    def read(path):
        with path.open(encoding='utf-8') as f: return json.load(f)
    try:
        static = read(static_path)
        sources.append('静态 DNS')
        for r in static.get('records', []): records.append((('static','',''),r))
    except (OSError, ValueError, TypeError) as exc: warnings.append('静态 DNS 读取失败：'+type(exc).__name__)
    try:
        cloud = read(data/'versions'/version/'cloud-dns-snapshot.json')
        if not cloud.get('available'): warnings.append('云 DNS 快照不可用')
        else: sources.append('云 DNS')
        if not cloud.get('complete'): warnings.append('云 DNS 快照不完整')
        for zone in cloud.get('zones', []):
            scope = ('cloud',str(zone.get('tenantId','')),str(zone.get('visibility','')))
            for r in zone.get('records', []): records.append((scope,r))
    except (OSError, ValueError, TypeError) as exc: warnings.append('云 DNS 读取失败：'+type(exc).__name__)
    names = {normal(n) for n in names if n and '*' not in n and '$' not in n and not n.startswith('~')}
    ips = set(ips)
    by_scope = {}
    for scope,r in records: by_scope.setdefault(scope,[]).append(r)
    result = []
    for scope, group in by_scope.items():
        known = set(names)
        for r in group:
            if str(r.get('type','')).upper() in ('A','AAAA') and str(r.get('value','')) in ips:
                known.add(normal(r.get('name')))
        # CNAME relationships stay inside one source/tenant/visibility scope.
        for _ in range(32):
            added = set()
            for r in group:
                if str(r.get('type','')).upper() != 'CNAME' or r.get('status') != '启用': continue
                a,b = normal(r.get('name')),normal(r.get('value'))
                if a in known and b: added.add(b)
                if b in known and a: added.add(a)
            if added <= known: break
            known.update(added)
        for r in group:
            name = normal(r.get('name')); value = str(r.get('value',''))
            if name not in known: continue
            reason = '入口域名' if name in names else '服务器 IP 候选' if value in ips else '同来源域名或 CNAME 关联'
            result.append({'name':r.get('name',''), 'type':r.get('type',''), 'value':value, 'status':r.get('status',''), 'line':r.get('line',''), 'source':'静态 DNS' if scope[0]=='static' else '云 DNS', 'tenant':r.get('tenantName','') or scope[1], 'visibility':scope[2], 'observedAt':r.get('observedAt',''), 'reason':reason})
    return {'records':result,'sources':sources,'warnings':warnings}


if __name__=='__main__':
    try:
        args=json.loads(base64.b64decode(sys.argv[2]).decode())
        print(json.dumps(scan(Path(os.environ.get('ENVSCOPE_DATA','/data')),sys.argv[1],args['names'],args['ips']),ensure_ascii=False))
    except Exception as exc:
        print('DNS 查询失败：'+type(exc).__name__,file=sys.stderr);sys.exit(1)
