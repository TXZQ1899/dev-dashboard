"""Read explicitly declared Nacos config identities; never infer application ownership."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_CONTENT = 262144


def business_declarations(content):
    """Extract literal business Data IDs and retain an unknown group as empty."""
    values, indent = {}, None
    for line in content.splitlines():
        if '\t' in line or line.strip() in ('---', '...'):
            return [], ['顶层 Nacos 声明使用未支持的 YAML 格式']
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.match(r'^( *)([\w.-]+):(?:\s+(.*?))?\s*$', line)
        if not match:
            continue
        level, key, value = len(match[1]), match[2], match[3]
        if indent is None:
            if key == 'nacos' and value is None:
                indent = level
            continue
        if level <= indent:
            break
        if value is None or key not in ('dataId', 'nacosUrl', 'namespace', 'group', 'enabled', 'username', 'password'):
            continue
        # Only direct children; nested `nacos.config` fields have deeper indentation.
        if level != indent + 2:
            continue
        value = re.sub(r'\s+#.*$', '', value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            return [], ['顶层 Nacos 声明包含重复字段，未下载业务配置']
        values[key] = value
    if values.get('enabled', '').lower() == 'false' or 'dataId' not in values:
        return [], []
    address, group, namespace = values.get('nacosUrl', ''), values.get('group', ''), values.get('namespace', '')
    ids = [item.strip() for item in values['dataId'].split(',')]
    resolved, warnings = [], []
    for data_id in ids:
        if (not data_id or not address or not namespace or
                any('${' in x or (x and x[0] in '[{&*!|>') for x in (data_id, group, address, namespace))):
            warnings.append('顶层 Nacos 业务配置的 Data ID / Group 无法确定：'+data_id[:160])
            continue
        resolved.append({'address': address, 'dataId': data_id, 'group': group,
                         'namespace': values.get('namespace', ''),
                         'username': values.get('username', ''), 'password': values.get('password', '')})
    return resolved, warnings


def declaration(content):
    """Read literal scalars in the nacos.config mapping of a simple YAML document.

    Reject complex YAML, duplicate keys and unresolved values rather than guessing.
    """
    values, stack = {}, []
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '\t' in line or line.strip() in ('---', '...'):
            return None
        m = re.match(r'^( *)([\w.-]+):(?:\s+(.*?))?\s*$', line)
        if not m:
            continue
        indent, key, value = len(m[1]), m[2], m[3]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = '.'.join([x[1] for x in stack]+[key])
        if value is None:
            stack.append((indent, key))
            continue
        if not path.startswith('nacos.config.') and path != 'nacos.enabled':
            continue
        value = re.sub(r'\s+#.*$', '', value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or '${' in value or value[0] in '[{&*!|>' or path in values:
            return None
        values[path] = value
    get = lambda key: values.get('nacos.config.'+key, '')
    if values.get('nacos.enabled', '').lower() == 'false' or get('enabled').lower() == 'false':
        return None
    address, data_id = get('server-addr') or get('serverAddr'), get('data-id') or get('dataId')
    if not address or not data_id:
        return None
    return {'address': address, 'dataId': data_id, 'group': get('group') or 'DEFAULT_GROUP',
            'namespace': get('namespace'), 'username': get('username'), 'password': get('password')}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def client(config):
    address = config['address']
    if ',' in address or any(c in address for c in '\r\n\t '):
        raise ValueError('Nacos 地址不是单个明确的服务器')
    url = urlsplit(address if '://' in address else 'http://'+address)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('Nacos 服务器地址无效')
    try:
        port = url.port
    except ValueError:
        raise ValueError('Nacos 服务器端口无效') from None
    if port is None:
        raise ValueError('Nacos 服务器端口未声明')
    base = url.scheme+'://'+url.netloc+'/nacos'
    opener = build_opener(ProxyHandler({}), NoRedirect())
    if bool(config['username']) != bool(config['password']):
        raise ValueError('Nacos 登录信息不完整')
    try:
        token = ''
        if config['username']:
            body = urlencode({'username':config['username'], 'password':config['password']}).encode()
            with opener.open(Request(base+'/v1/auth/login', data=body), timeout=5) as response:
                token = json.loads(response.read(16385))['accessToken']
        return opener, base, token
    except HTTPError as error:
        raise ValueError('Nacos 认证失败（HTTP '+str(error.code)+'）') from None
    except (URLError, TimeoutError, OSError, KeyError, ValueError):
        raise ValueError('Nacos 连接或认证失败') from None


def resolve_group(config):
    if config['group']:
        return config
    if '*' in config['dataId']:
        raise ValueError('Nacos Data ID 含通配符，无法安全查询 Group')
    opener, base, token = client(config)
    params = {'search':'blur', 'dataId':config['dataId'], 'group':'*',
              'tenant':config['namespace'], 'pageNo':1, 'pageSize':100}
    if token: params['accessToken'] = token
    try:
        with opener.open(base+'/v1/cs/configs?'+urlencode(params), timeout=5) as response:
            raw = response.read(MAX_CONTENT+1)
        if len(raw) > MAX_CONTENT: raise ValueError('响应过大')
        result = json.loads(raw)
        items, total = result['pageItems'], result['totalCount']
        if not isinstance(items, list) or not isinstance(total, int) or total != len(items) or total > 100:
            raise ValueError('查询结果不完整')
        groups = {item.get('group') for item in items if isinstance(item, dict)
                  and item.get('dataId') == config['dataId']
                  and item.get('tenant', config['namespace']) == config['namespace']
                  and isinstance(item.get('group'), str) and item['group']}
        if len(groups) != 1:
            raise ValueError('未找到唯一 Group（匹配 '+str(len(groups))+' 个）')
        return {**config, 'group':next(iter(groups))}
    except HTTPError as error:
        raise ValueError('Nacos Group 查询失败（HTTP '+str(error.code)+'）') from None
    except (URLError, TimeoutError, OSError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith('未找到唯一 Group'):
            raise
        raise ValueError('Nacos Group 查询失败或结果不完整') from None


def fetch(config):
    opener, base, token = client(config)
    params = {'dataId': config['dataId'], 'group': config['group']}
    if config['namespace']:
        params['tenant'] = config['namespace']
    if token: params['accessToken'] = token
    try:
        with opener.open(base+'/v1/cs/configs?'+urlencode(params), timeout=5) as response:
            data = response.read(MAX_CONTENT+1)
        if len(data) > MAX_CONTENT:
            raise ValueError('Nacos 配置超过 256 KiB，未保存')
        data.decode('utf-8')
        return data
    except HTTPError as error:
        raise ValueError('Nacos 配置读取失败（HTTP '+str(error.code)+'）') from None
    except (URLError, TimeoutError, OSError, UnicodeError, KeyError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == 'Nacos 配置超过 256 KiB，未保存':
            raise
        raise ValueError('Nacos 连接、认证或响应解析失败') from None


def save(folder, config, content):
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = '\0'.join(config[k] for k in ('address', 'namespace', 'group', 'dataId'))
    digest = hashlib.sha256(identity.encode()).hexdigest()
    extension = '.yaml' if config['dataId'].lower().endswith(('.yaml', '.yml')) else '.txt'
    path = folder/(digest+extension)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.nacos-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return path
