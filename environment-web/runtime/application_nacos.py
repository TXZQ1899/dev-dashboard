"""Read explicitly declared Nacos config identities; never infer application ownership."""
import hashlib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_CONTENT = 262144


def documents(content):
    """Split top-level YAML documents without combining profile-specific values."""
    parts, current = [], []
    for line in content.splitlines():
        if re.fullmatch(r'(?:---|\.\.\.)(?:\s+#.*)?\s*', line):
            if current:
                parts.append('\n'.join(current))
                current = []
        else:
            current.append(line)
    if current:
        parts.append('\n'.join(current))
    return parts


def declarations(content, process=None):
    """Collect independent declarations while preserving unresolved documents."""
    found, warnings = [], []
    for document in documents(content):
        config = declaration(document)
        business, issues = business_declarations(document)
        warnings.extend(issues)
        if config:
            found.append(config)
        found.extend(business)
        if not config and not business and re.search(r'(?m)^nacos\s*:', document):
            warnings.append('Nacos 声明不完整或包含未支持的 YAML/变量')
    spring, issues = spring_declarations(content, process or {})
    found.extend(spring)
    warnings.extend(issues)
    return found, warnings


def spring_scalars(document):
    """Extract relevant literal scalar paths, rejecting duplicate/complex values."""
    result, stack = {}, []
    for line in document.splitlines():
        if re.match(r'^ *\t', line):
            raise ValueError('YAML 缩进含制表符')
        match = re.match(r'^( *)([\w.-]+):(?:\s+(.*?))?\s*$', line)
        if not match:
            continue
        level, key, value = len(match[1]), match[2], match[3]
        while stack and stack[-1][0] >= level:
            stack.pop()
        path = '.'.join([item[1] for item in stack]+[key])
        if value is None:
            stack.append((level, key))
            continue
        if path not in ('spring.application.name', 'spring.profiles', 'spring.profiles.active',
                        'spring.config.activate.on-profile') and not path.startswith('spring.cloud.nacos.config.'):
            continue
        value = re.sub(r'\s+#.*$', '', value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if path in result:
            raise ValueError('Spring 配置包含重复字段')
        result[path] = value
    return result


def spring_declarations(content, process):
    """Resolve one explicitly selected Spring profile; never combine alternate profiles."""
    try:
        docs = [spring_scalars(document) for document in documents(content)]
        relevant = [d for d in docs if any(k.startswith('spring.cloud.nacos.config.') for k in d)]
        if not relevant:
            return [], []
        common = [d for d in docs if not (d.get('spring.profiles') or d.get('spring.config.activate.on-profile'))]
        def single(key):
            values = {d[key] for d in common if key in d}
            if len(values) > 1: raise ValueError('多个 Spring 文档声明了不同的 '+key)
            return next(iter(values), '')
        name, profile = single('spring.application.name'), single('spring.profiles.active')
        args = shlex.split(process.get('command', ''))
        for key in ('spring.application.name', 'spring.profiles.active'):
            values = [arg.split('=', 1)[1] for arg in args if arg.startswith(('-D'+key+'=', '--'+key+'='))]
            if len(set(values)) > 1: raise ValueError('进程启动参数包含冲突的 '+key)
            if values:
                if key.endswith('.name'): name = values[0]
                else: profile = values[0]
        if not re.fullmatch(r'[\w.-]+', profile):
            raise ValueError('无法确定唯一 Spring profile')
        selected = [d for d in relevant if not (d.get('spring.profiles') or d.get('spring.config.activate.on-profile'))
                    or d.get('spring.profiles') == profile or d.get('spring.config.activate.on-profile') == profile]
        if len(selected) != 1:
            raise ValueError('当前 profile 的 Nacos 声明不存在或不唯一')
        config = selected[0]
        get = lambda key: config.get('spring.cloud.nacos.config.'+key, '')
        if get('enabled').lower() == 'false': return [], []
        prefix = get('prefix') or get('name') or name
        extension = get('file-extension') or get('fileExtension') or 'properties'
        address = get('server-addr') or get('serverAddr')
        group = get('group') or 'DEFAULT_GROUP'
        namespace, username, password = get('namespace'), get('username'), get('password')
        if (not re.fullmatch(r'[\w.-]+', prefix) or not re.fullmatch(r'[\w-]+', extension) or
                not address or any('${' in value or (value and value[0] in '[{&*!|>')
                                   for value in (address, group, namespace, username, password))):
            raise ValueError('Spring Nacos 配置包含未解析的值')
        base = {'address':address, 'group':group, 'namespace':namespace,
                'username':username, 'password':password}
        ids = [prefix, prefix+'.'+extension, prefix+'-'+profile+'.'+extension]
        return [{**base, 'dataId':data_id} for data_id in ids], [
            'Spring Nacos Data ID 根据声明和 profile '+profile+' 推导，环境变量和其他覆盖未核实']
    except ValueError as error:
        return [], ['Spring Nacos 声明无法解析：'+str(error)]


def business_declarations(content):
    """Extract literal business Data IDs and retain an unknown group as empty."""
    values, indent = {}, None
    for line in content.splitlines():
        if re.match(r'^ *\t', line):
            return [], ['顶层 Nacos 声明使用未支持的 YAML 格式']
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.match(r'^( *)([\w.-]+):(?:\s+(.*?))?\s*$', line)
        if not match:
            continue
        level, key, value = len(match[1]), match[2], match[3]
        if indent is None:
            if level == 0 and key == 'nacos' and value is None:
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
        if re.match(r'^ *\t', line):
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
        if path not in ('nacos.enabled', 'nacos.config.enabled', 'nacos.config.server-addr',
                        'nacos.config.serverAddr', 'nacos.config.data-id', 'nacos.config.dataId',
                        'nacos.config.group', 'nacos.config.namespace', 'nacos.config.username',
                        'nacos.config.password'):
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
