"""Read a known Kong asset's local Admin API over an authorized terminal."""
import base64
from contextlib import redirect_stdout
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlsplit
import zlib

from application_check import open_terminal
from service import credentials, read

# The local reader uses HTTPConnection, which never follows redirects.
REMOTE_READ = """
import base64, http.client, sys, zlib
conn = http.client.HTTPConnection('127.0.0.1', 8001, timeout=5)
conn.request('GET', sys.argv[1])
response = conn.getresponse()
body = response.read(4_000_001)
if len(body) > 4_000_000:
    raise ValueError('Admin API response too large')
print(str(response.status) + ':' + base64.b64encode(zlib.compress(body)).decode('ascii'))
conn.close()
"""


def allowed_path(path):
    if not isinstance(path, str) or len(path) > 2000 or re.search(r'[\s\x00-\x1f]', path):
        return False
    parsed = urlsplit(path)
    return not (parsed.netloc or parsed.scheme or parsed.fragment) and bool(re.fullmatch(
        r'/routes|/services/[A-Za-z0-9-]+|/upstreams/[A-Za-z0-9._-]+/targets', parsed.path))


def asset_for_gateway(data, address, version):
    if not ipaddress.ip_address(address).is_private or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', version):
        raise ValueError('无效的 Kong 资产或快照版本')
    snapshot = read(data/'versions'/version/'jumpserver-snapshot.json', {})
    matches = [asset for asset in snapshot.get('assets', []) if asset.get('ip') == address]
    if len(matches) != 1:
        raise ValueError('当前快照无法唯一确认 Kong 资产')
    routes = (matches[0].get('inspection') or {}).get('nginxRoutes', [])
    listeners = {port for route in routes if re.search(r'(?:^|/)kong(?:/|$)', route.get('file') or '')
                 for listen in route.get('listen', []) for port in (8000, 8001)
                 if re.match(rf'^(?:\S+:)?{port}(?:\s|$)', listen)}
    if listeners != {8000, 8001}:
        raise ValueError('当前快照缺少 Kong 的代理和 Admin 监听证据')
    return matches[0]


def request(terminal, path):
    if not allowed_path(path):
        return {'error': '不支持的 Kong Admin 查询'}
    command = shlex.join(['python3', '-c', f'exec({REMOTE_READ!r})', path])
    rc, output = terminal.run(command, timeout=10)
    if rc:
        return {'error': '网关本机 Admin 请求失败'}
    frames = re.findall(r'(?m)^(\d{3}):([A-Za-z0-9+/=]+)\s*$', output)
    if len(frames) != 1:
        return {'error': '网关本机 Admin 响应不完整'}
    status, data = frames[0]
    if not 200 <= int(status) < 300:
        return {'error': f'Admin API HTTP {status}'}
    return {'data': json.loads(zlib.decompress(base64.b64decode(data)))}


def main():
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    address, version = sys.argv[1:]
    terminal = None
    try:
        version = version or read(data/'current.json', {}).get('id', '')
        asset = asset_for_gateway(data, address, version)
        with redirect_stdout(sys.stderr):
            terminal, _ = open_terminal(asset, credentials(), read(data/'secrets/ip-password-map.json', {}))
        for line in sys.stdin:
            try:
                with redirect_stdout(sys.stderr):
                    result = request(terminal, json.loads(line))
            except Exception:
                result = {'error': '读取网关本机 Admin 响应失败'}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
    except Exception:
        print(json.dumps({'error': '无法通过 JumpServer 查询 Kong Admin'}, ensure_ascii=False), flush=True)
    finally:
        if terminal is not None:
            terminal.close()


if __name__ == '__main__':
    main()
