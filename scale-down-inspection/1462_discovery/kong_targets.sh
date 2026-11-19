#!/bin/sh
# Read-only Kong Admin export. Run where the Admin API is reachable.
set -eu
umask 077
command -v curl >/dev/null || { echo 'curl is required' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'python3 is required' >&2; exit 1; }
exec python3 - "$@" <<'PY'
import json, subprocess, sys, urllib.parse

base = 'http://10.179.4.220:8001'
endpoints = {'10.179.4.251:8761', '10.179.4.108:8761'}

def pages(path):
    seen, result = set(), []
    while path:
        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith('/') or path.startswith('//') or path in seen:
            raise ValueError('invalid or repeated pagination link')
        seen.add(path)
        raw = subprocess.check_output(['curl', '--fail', '--silent', '--show-error',
                                       '--connect-timeout', '5', '--max-time', '30', base + path])
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError('unexpected Kong response')
        result.extend(data['data'])
        path = data.get('next')
    return result

try:
    upstreams = pages('/upstreams?size=100')
    services = pages('/services?size=100')
    routes = pages('/routes?size=100')
    matches = []
    for upstream in upstreams:
        uid, name = upstream['id'], upstream.get('name', '')
        targets = pages('/upstreams/' + urllib.parse.quote(uid, safe='') + '/targets?size=100')
        selected = [t for t in targets if t.get('target') in endpoints]
        if not selected:
            continue
        related = [s for s in services if s.get('host') == name]
        for target in selected:
            matches.append({'upstream': upstream, 'target': target,
                            'services': [{'service': s, 'routes': [r for r in routes
                                if (r.get('service') or {}).get('id') == s.get('id')]}
                                for s in related]})
    json.dump({'admin': base, 'matches': matches,
              'limitations': ['配置证据；仅匹配字面 IP:端口',
                               '包含零权重 target，未核实健康状态']},
              sys.stdout, ensure_ascii=False, indent=2)
    print()
except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
    print('Kong 查询不完整: ' + type(exc).__name__, file=sys.stderr)
    sys.exit(1)
PY
