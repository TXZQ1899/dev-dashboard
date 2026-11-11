"""Manual, bounded repository configuration snapshots for catalog applications."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from application_config import nacos
from deployment_catalog import decode

LIMIT = 2 * 1024 * 1024
GROUPS = ('dev', 'test', 'uat', 'prod', 'other')
MAVEN_TYPES = {'app', 'war', 'war8', 'jar', 'springboot'}
EXT = re.compile(r'\.(?:properties|ya?ml|xml|conf|ini|json|toml)$', re.I)


class RepositoryCache:
    """Small, short-lived per-job cache of POM and tree responses only."""
    def __init__(self, limit=16*1024*1024, ttl=300):
        self.limit, self.ttl, self.size = limit, ttl, 0
        self.entries = OrderedDict()

    def get(self, key):
        item = self.entries.pop(key, None)
        if item is None: return None
        timestamp, data = item
        if time.monotonic() - timestamp > self.ttl:
            self.size -= len(data)
            return None
        self.entries[key] = item
        return data

    def put(self, key, data):
        if len(data) > self.limit: return
        old = self.entries.pop(key, None)
        if old: self.size -= len(old[1])
        while self.entries and self.size + len(data) > self.limit:
            _, (_, removed) = self.entries.popitem(last=False)
            self.size -= len(removed)
        self.entries[key] = (time.monotonic(), data)
        self.size += len(data)


class CodeupAuthenticationError(ValueError):
    """The session cannot authenticate; retry only after credentials change."""


class CodeupPermissionDenied(ValueError):
    """Codeup explicitly reports that the current account lacks access."""


def safe_relative(path):
    if not isinstance(path, str) or not path or '\\' in path or '\0' in path:
        raise ValueError('无效仓库路径')
    p = PurePosixPath(path)
    if p.is_absolute() or any(x in ('.', '..') for x in path.split('/')):
        raise ValueError('无效仓库路径')
    return str(p)


def folder_name(name, app_id):
    if not re.fullmatch(r'[0-9]+', str(app_id)):
        raise ValueError('应用 ID 无效')
    name = unicodedata.normalize('NFC', name or '')
    name = re.sub(r'[^\w.\-]+', '_', name, flags=re.UNICODE).strip('._-')[:120]
    if not name: raise ValueError('应用名称无法用于目录')
    return name + '-' + str(app_id)


def catalog_app_ids(store):
    with store.connection() as conn:
        rows = store.execute(conn, "SELECT s.native_key_json FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='DEVOPS_APPLICATION' AND s.presence_status='PRESENT' AND u.unit_type='APPLICATION'", (store.source_key,))
    ids = [str(decode(row['native_key_json'])['appId']) for row in rows]
    if len(ids) != len(set(ids)): raise ValueError('部署单元存在重复应用绑定')
    return sorted(ids, key=lambda x: int(x) if x.isdigit() else -1)


class Codeup:
    def __init__(self, repo, cookie, cache=None):
        url = urlsplit(repo['url'])
        path = url.path.strip('/')
        if url.scheme != 'https' or url.hostname != 'codeup.aliyun.com' or url.port or url.username or url.password or url.query or url.fragment:
            raise ValueError('仅支持 Codeup HTTPS 仓库')
        if path.endswith('.git'): path = path[:-4]
        safe_relative(path)
        self.url = 'https://codeup.aliyun.com/' + quote(path, safe='/')
        self.cookie = cookie
        self.cache = cache
        self.warnings = []
        self.opener = build_opener(ProxyHandler({}), nacos.NoRedirect())
        self.id = str(repo.get('codeupId') or '')
        if not re.fullmatch(r'\d+', self.id):
            self.id = self.resolve_id(path)

    def resolve_id(self, path):
        """Resolve the exact declared path from Codeup; do not match by name."""
        url = 'https://codeup.aliyun.com/portal/path_info?'+urlencode({'path':'/'+path})
        try: data = json.loads(self.get(url))
        except (json.JSONDecodeError, UnicodeError): raise ValueError('仓库身份查询响应无效') from None
        if not isinstance(data, dict): raise ValueError('仓库身份查询响应无效')
        code = str(data.get('errorCode') or '').upper()
        if code in ('UNAUTHORIZED', 'NOT_LOGIN', 'LOGIN_REQUIRED', '401') or 'login' in str(data.get('errorAction') or '').lower():
            raise CodeupAuthenticationError('Codeup 登录失效，请在 Settings 更新阿里云 Cookie 后重试')
        if code in ('FORBIDDEN', 'ACCESS_DENIED', '403'):
            raise CodeupPermissionDenied('Codeup 当前账号无权访问该仓库')
        if code in ('NOT_FOUND', 'PROJECT_NOT_FOUND', '404'):
            raise ValueError('Codeup 未找到该仓库，或当前账号无法查看')
        result = data.get('result')
        resource = result.get('pathResource') if isinstance(result, dict) else None
        if (data.get('success') is not True or not isinstance(resource, dict) or
                str(resource.get('type', '')).upper() != 'PROJECT' or result.get('assetsName') == 'error' or
                not re.fullmatch(r'\d+', str(resource.get('id') or ''))):
            raise ValueError('Codeup 仓库身份无法唯一确认')
        return str(resource['id'])

    def get(self, url, limit=LIMIT, cacheable=False):
        if cacheable and self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None: return cached
        try:
            with self.opener.open(Request(url, headers={'Cookie':self.cookie, 'User-Agent':'Mozilla/5.0'}), timeout=15) as response:
                if response.headers.get_content_type() == 'text/html': raise ValueError('Codeup 返回 HTML，可能登录失效')
                data = response.read(limit + 1)
                if len(data) > limit: raise ValueError('文件超过 2 MiB')
                if cacheable and self.cache is not None: self.cache.put(url, data)
                return data
        except HTTPError as e:
            location = urlsplit(e.headers.get('Location', ''))
            code = e.code
            e.close()
            if code == 401 or (code in (301, 302, 303, 307, 308) and
                    location.hostname == 'account-devops.aliyun.com' and
                    location.path.rstrip('/') in ('/login', '/logout')):
                raise CodeupAuthenticationError('Codeup 登录失效，请在 Settings 更新阿里云 Cookie 后重试') from None
            raise ValueError('Codeup HTTP '+str(code)) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('Codeup 连接失败或超时') from None

    def raw(self, path):
        path = safe_relative(path)
        return self.get(self.url+'/raw/master/'+quote(path, safe='/'), cacheable=PurePosixPath(path).name == 'pom.xml')

    def tree(self, path):
        rows, seen = [], set()
        for page in range(1, 51):
            url = 'https://codeup.aliyun.com/api/v4/projects/'+self.id+'/repository/tree?'+urlencode({'ref':'master','path':path,'per_page':100,'page':page})
            try: batch = json.loads(self.get(url, cacheable=True))
            except (json.JSONDecodeError, UnicodeDecodeError): raise ValueError('Codeup 文件树响应无效') from None
            if not isinstance(batch, list): raise ValueError('Codeup 文件树响应无效')
            if (page > 1 and batch and all(isinstance(row, dict) and row.get('type') in ('tree', 'blob') for row in batch)
                    and len(batch) == len(seen) and {row.get('path') for row in batch} == seen):
                self.warnings.append('Codeup 目录分页返回重复结果，已保留已读文件；列表完整性未确认：'+path)
                return rows
            for row in batch:
                if not isinstance(row, dict) or row.get('type') not in ('tree','blob'): raise ValueError('Codeup 文件树条目无效')
                item = safe_relative(row.get('path'))
                if (path and not item.startswith(path.rstrip('/')+'/')) or item in seen: raise ValueError('Codeup 文件树路径异常或分页重复')
                seen.add(item); rows.append(row)
            if len(batch) < 100: return rows
        raise ValueError('Codeup 文件树分页超过上限')


def maven_modules(client, app_name):
    pending, visited, matches = ['pom.xml'], set(), []
    root_fallback = False
    while pending:
        path = pending.pop()
        if path in visited: continue
        if len(visited) >= 250: raise ValueError('Maven 模块超过上限')
        visited.add(path)
        try:
            content = client.raw(path)
        except CodeupAuthenticationError:
            raise
        except ValueError as e:
            # Some multi-project repositories have no aggregator at the root.
            # Enumerate POMs in repository directories without scanning source
            # trees, but never reinterpret permission or connectivity failures.
            if (path == 'pom.xml' and not root_fallback and
                    str(e) in ('Codeup HTTP 404', 'Codeup 返回 HTML，可能登录失效')):
                root_fallback = True
                directories, discovered, searched = [('', 0)], [], 0
                while directories:
                    directory, depth = directories.pop()
                    searched += 1
                    if searched > 250 or len(discovered) + len(directories) > 250:
                        raise ValueError('仓库 POM 搜索超过上限') from None
                    for row in client.tree(directory):
                        candidate = row['path']
                        leaf = PurePosixPath(candidate).name
                        if row['type'] == 'blob' and leaf == 'pom.xml': discovered.append(candidate)
                        elif row['type'] == 'tree' and depth < 4 and leaf not in ('src', 'target', 'node_modules', '.git'):
                            directories.append((candidate, depth+1))
                if not discovered: raise ValueError('仓库根目录和限定搜索范围内均未找到 POM') from None
                pending.extend(discovered)
                continue
            raise ValueError(('根 POM 读取失败：' if path == 'pom.xml' else '模块 POM 读取失败：'+path+'；')+str(e)) from None
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            raise ValueError('POM XML 解析失败：'+path) from None
        # Only literal declarations in build, not unrelated XML fields.
        direct = lambda e,n: [x for x in e if x.tag.rsplit('}',1)[-1] == n]
        names = [x.text.strip() for build in direct(root,'build') for x in direct(build,'finalName') if x.text]
        artifacts = [x.text.strip() for x in direct(root,'artifactId') if x.text]
        parents = [x.text.strip() for parent in direct(root, 'parent') for x in direct(parent, 'artifactId') if x.text]
        replacements = {}
        if len(artifacts) == 1 and re.fullmatch(r'[\w.-]+', artifacts[0]):
            replacements.update({'${project.artifactId}':artifacts[0], '${artifactId}':artifacts[0]})
        if len(parents) == 1 and re.fullmatch(r'[\w.-]+', parents[0]):
            replacements.update({'${parent.artifactId}':parents[0], '${project.parent.artifactId}':parents[0]})
        resolved = []
        for raw in names:
            value = raw
            for key, replacement in replacements.items(): value = value.replace(key, replacement)
            if '${' not in value: resolved.append(value)
        if app_name in resolved or app_name+'-exec' in resolved:
            packaging = next((x.text.strip() for x in direct(root,'packaging') if x.text), 'jar')
            basis = ('finalName' if app_name in names else 'finalName resolves to artifactId' if app_name in resolved
                     else 'explicit finalName matches application + -exec')
            matches.append((str(PurePosixPath(path).parent), packaging,
                            basis))
        for modules in direct(root,'modules'):
            for module in direct(modules,'module'):
                if module.text:
                    value = safe_relative(module.text.strip())
                    pending.append(safe_relative(str(PurePosixPath(path).parent / value / 'pom.xml').removeprefix('./')))
    return matches


def group_for(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    m = re.match(r'^(?:application|bootstrap)[-_](dev|test|uat|prod)(?:\.|$)', name)
    if m: return m[1]
    for part in parts[:-1]:
        if part.lower() in GROUPS[:4]: return part.lower()
    return 'other'


def ant_match(path, pattern):
    """Match literal Maven resource patterns, including zero-depth **/."""
    if not pattern or pattern.startswith('/') or '..' in pattern.split('/') or '\\' in pattern or '[' in pattern:
        raise ValueError('Maven 资源匹配规则不受支持')
    expression = ''
    i = 0
    while i < len(pattern):
        if pattern.startswith('**/', i): expression += '(?:.*/)?'; i += 3
        elif pattern.startswith('**', i): expression += '.*'; i += 2
        elif pattern[i] == '*': expression += '[^/]*'; i += 1
        elif pattern[i] == '?': expression += '[^/]'; i += 1
        else: expression += re.escape(pattern[i]); i += 1
    return re.fullmatch(expression, path) is not None


def resource_rules(client, module):
    pom_path = str(PurePosixPath(module)/'pom.xml').removeprefix('./')
    try: root = ET.fromstring(client.raw(pom_path))
    except ET.ParseError: raise ValueError('模块 POM XML 解析失败：'+pom_path) from None
    direct = lambda e,n: [x for x in e if x.tag.rsplit('}',1)[-1] == n]
    resources = [r for build in direct(root,'build') for block in direct(build,'resources') for r in direct(block,'resource')]
    if not resources:
        return [(str(PurePosixPath(module)/'src/main/resources').removeprefix('./'), ['**/*'], [], False)]
    if len(resources) > 32: raise ValueError('Maven 资源目录超过上限')
    rules = []
    for resource in resources:
        dirs = [x.text.strip() for x in direct(resource,'directory') if x.text and x.text.strip()]
        if len(dirs) != 1 or '${' in dirs[0]: raise ValueError('Maven 资源目录无法确定')
        directory = safe_relative(dirs[0])
        source = safe_relative(str(PurePosixPath(module)/directory).removeprefix('./'))
        includes = [x.text.strip() for block in direct(resource,'includes') for x in direct(block,'include') if x.text and x.text.strip()] or ['**/*']
        excludes = [x.text.strip() for block in direct(resource,'excludes') for x in direct(block,'exclude') if x.text and x.text.strip()]
        for pattern in includes+excludes: ant_match('validation',pattern)
        filtering = [x.text.strip().lower() for x in direct(resource,'filtering') if x.text and x.text.strip()]
        if len(filtering) > 1 or (filtering and filtering[0] not in ('true', 'false')):
            raise ValueError('Maven 资源过滤设置无法确定')
        rules.append((source, includes, excludes, filtering == ['true']))
    return rules


def put(root, relative, data):
    path = root / safe_relative(relative)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists():
        if path.read_bytes() == data: return
        raise ValueError('配置路径冲突：'+relative)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(data)


def discover_profiles(content):
    profiles = set()
    for doc in nacos.documents(content):
        try: d = nacos.spring_scalars(doc)
        except ValueError: continue
        for key in ('spring.profiles','spring.config.activate.on-profile','spring.profiles.active'):
            value = d.get(key,'')
            if re.fullmatch(r'[\w.-]+', value): profiles.add(value)
    return sorted(profiles)


def properties_as_yaml(content):
    """Handle literal single-line properties; leave escapes and continuations unresolved."""
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '!')): continue
        relevant = line.startswith(('nacos.', 'spring.cloud.nacos.', 'spring.application.', 'spring.profiles.'))
        if '\\' in line:
            if relevant: raise ValueError('Nacos properties 包含转义或续行，无法安全解析')
            continue
        match = re.fullmatch(r'([\w.\[\]-]+)\s*[=:]\s*(.*)', line)
        if not match:
            if relevant: raise ValueError('Nacos properties 包含不支持的语法')
            continue
        rows.append(match[1]+': '+match[2])
    return '\n'.join(rows)


def properties_nacos_extensions(content):
    """Extract literal indexed Nacos Boot extension declarations from one file."""
    values, duplicates = {}, set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(('#','!')): continue
        match = re.fullmatch(r'(nacos\.config\.[\w.\[\]-]+)\s*[=:]\s*(.*)', line)
        if not match: continue
        key, value = match[1], match[2].strip()
        if key in values: duplicates.add(key)
        values[key] = value
    indices = sorted({int(m[1]) for key in values if (m := re.fullmatch(r'nacos\.config\.ext-config\[(\d+)\]\.data-id',key))})
    if not indices: return [], []
    base_keys = ('server-addr','namespace','username','password')
    if any('nacos.config.'+key in duplicates for key in base_keys):
        return [], ['扩展 Nacos 连接声明包含重复字段']
    if values.get('nacos.config.enabled','').lower() == 'false' or values.get('nacos.config.bootstrap.enable','').lower() == 'false':
        return [], []
    found, warnings = [], []
    for index in indices:
        prefix = 'nacos.config.ext-config['+str(index)+'].'
        keys = ('data-id','group','server-addr','namespace','username','password')
        if any(prefix+key in duplicates for key in keys):
            warnings.append('扩展 Nacos 配置包含重复字段：索引 '+str(index)); continue
        data_id = values.get(prefix+'data-id','')
        get = lambda key: values.get(prefix+key, values.get('nacos.config.'+key,''))
        address, namespace = get('server-addr'), get('namespace')
        username, password = get('username'), get('password')
        # Extension group defaults independently to DEFAULT_GROUP.
        group = values.get(prefix+'group') or 'DEFAULT_GROUP'
        if (not re.fullmatch(r'[\w.:-]+',data_id) or not address or
                any('\\' in v or '${' in v or (v and v[0] in '[{&*!|>') for v in (address,namespace,username,password,group))):
            warnings.append('扩展 Nacos 配置的连接或 Data ID 无法确定：索引 '+str(index)); continue
        found.append({'address':address,'namespace':namespace,'username':username,'password':password,'group':group,'dataId':data_id})
    return found, warnings


def resolve_local_placeholders(content):
    """Resolve unique simple literal scalars within each YAML document only."""
    resolved = []
    for document in nacos.documents(content):
        values, duplicates, seen, stack = {}, set(), set(), []
        for line in document.splitlines():
            m = re.match(r'^( *)([\w.-]+):(?:\s+(.*?))?\s*$', line)
            if not m: continue
            level, key, value = len(m[1]), m[2], m[3]
            while stack and stack[-1][0] >= level: stack.pop()
            path = '.'.join([item[1] for item in stack]+[key])
            if path in seen: duplicates.add(path)
            seen.add(path)
            if value is None:
                stack.append((level,key))
                continue
            value = re.sub(r'\s+#.*$', '', value).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'": value = value[1:-1]
            values[path] = value
        def replace(match):
            key = match[1]
            value = values.get(key, '')
            # Restrict interpolation to unambiguous address-like literals;
            # complex YAML and nested placeholders remain unresolved.
            if any(key == d or key.startswith(d+'.') for d in duplicates) or not re.fullmatch(r'[A-Za-z0-9_.:/-]+', value): return match[0]
            return value
        resolved.append(re.sub(r'\$\{([\w.-]+)\}', replace, document))
    return '\n---\n'.join(resolved)


def shared_dataids(fields, profile):
    """Resolve explicitly declared legacy Spring shared Data IDs in one profile."""
    prefix = 'spring.cloud.nacos.config.'
    values = [fields[prefix+k] for k in ('shared-dataids','sharedDataids','shared-data-ids') if fields.get(prefix+k)]
    if not values: return [], []
    if len(set(values)) != 1: return [], ['共享 Nacos Data ID 声明冲突']
    if fields.get(prefix+'enabled', '').lower() == 'false': return [], []
    address = fields.get(prefix+'server-addr') or fields.get(prefix+'serverAddr') or ''
    namespace = fields.get(prefix+'namespace', '')
    username, password = fields.get(prefix+'username',''), fields.get(prefix+'password','')
    if not address or any('${' in x or (x and x[0] in '[{&*!|>') for x in (address,namespace,username,password)):
        return [], ['共享 Nacos 地址、命名空间或凭据无法确定']
    resolved, issues = [], []
    for raw in values[0].split(','):
        item = re.sub(r'\$\{spring\.profiles\.active(?::[^}]*)?\}', profile, raw.strip()) if profile else raw.strip()
        if not re.fullmatch(r'[\w.:-]+', item) or '.' not in item:
            issues.append('共享 Nacos Data ID 无法确定')
            continue
        resolved.append({'address':address, 'namespace':namespace, 'group':'DEFAULT_GROUP',
                         'dataId':item, 'username':username, 'password':password})
    return resolved, issues


def sync_one(app, repo, cookie, output, progress=lambda *args: None, cache=None):
    progress('匹配 Maven 模块', 0, 0)
    client = Codeup(repo, cookie, cache=cache) if cache is not None else Codeup(repo, cookie)
    result = {'appId':str(app['id']), 'name':app['name'], 'branch':'master', 'status':'partial', 'files':[], 'warnings':[], 'notes':[], 'missingCandidates':[]}
    matches = maven_modules(client, app['name'])
    deployable = [m for m in matches if m[1] in ('war', 'jar')]
    if deployable: matches = deployable
    if len(matches) > 1 and app.get('appType') in ('war','war8'):
        war = [m for m in matches if m[1] == 'war']
        if len(war) == 1: matches = war
    if len(matches) != 1: raise ValueError('finalName 匹配模块数量为 '+str(len(matches))+'，无法唯一定位')
    module, packaging, match_basis = matches[0]
    if packaging not in ('war', 'jar'): raise ValueError('匹配模块的 Maven packaging 不是 WAR/JAR：'+packaging)
    result['module'], result['packaging'], result['moduleMatchBasis'] = module, packaging, match_basis
    found = {}
    for resources, includes, excludes, filtering in resource_rules(client, module):
        progress('发现资源文件', 0, 0)
        if filtering:
            result['notes'].append('Maven 资源目录 '+resources+' 声明了 filtering；已保存仓库原文，未执行构建时变量替换')
        queue = [resources]
        while queue:
            path = queue.pop()
            for row in client.tree(path):
                if row['type'] == 'tree': queue.append(row['path'])
                elif EXT.search(row['path']):
                    rel = str(PurePosixPath(row['path']).relative_to(resources))
                    if any(ant_match(rel, pat) for pat in includes) and not any(ant_match(rel, pat) for pat in excludes):
                        found.setdefault(row['path'], rel)
            if len(queue) + len(found) > 1000: raise ValueError('资源文件超过上限')
    result['warnings'].extend(getattr(client, 'warnings', []))
    if not found: raise ValueError('匹配模块没有发现支持的 resources 配置文件')
    entries = []
    for index, (path, rel) in enumerate(sorted(found.items()), 1):
        progress('下载仓库配置', index-1, len(found))
        saved = False
        try:
            data = client.raw(path)
            dest = group_for(rel)+'/resources/'+rel
            put(output, dest, data)
            saved = True
            result['files'].append({'source':'repository','path':path,'file':dest})
            if re.search(r'(?:^|/)(?:application|bootstrap)(?:-[\w.-]+)?\.(?:ya?ml|properties)$',path,re.I):
                content = data.decode('utf-8')
                extensions, issues = properties_nacos_extensions(content) if path.lower().endswith('.properties') else ([], [])
                result['warnings'].extend(path+'：'+issue for issue in issues)
                if path.lower().endswith('.properties'): content = properties_as_yaml(content)
                entries.append((path,resolve_local_placeholders(content),extensions))
        except CodeupAuthenticationError:
            raise
        except (ValueError, UnicodeError) as e:
            result['warnings'].append(('配置解析失败（原文件已保存）：' if saved else '资源文件失败：')+path+'；'+str(e))
    seen, downloaded = set(), set()
    for entry_index, (path, content, extensions) in enumerate(entries, 1):
        progress('读取 Nacos 配置声明', entry_index-1, len(entries))
        profiles = discover_profiles(content)
        filename = PurePosixPath(path).name
        named = re.match(r'^(?:application|bootstrap)-([\w.-]+)\.(?:ya?ml|properties)$', filename, re.I)
        if named: profiles = sorted(set(profiles+[named[1]]))
        if not profiles: profiles = ['']
        for profile in profiles:
            # Spring profile documents are resolved separately; top-level Nacos
            # documents are parsed only when their own profile matches.
            spring_configs, warnings = nacos.spring_declarations(content, {'command':'java -Dspring.profiles.active='+profile} if profile else {})
            for warning in warnings:
                target = result['notes'] if warning.startswith('Spring Nacos Data ID 根据声明和 profile ') else result['warnings']
                target.append(path+'：'+warning)
            configs = list(spring_configs)
            explicit = {tuple(c[k] for k in ('address','namespace','group','dataId')) for c in extensions}
            configs.extend(extensions)
            for doc in nacos.documents(content):
                try: fields = nacos.spring_scalars(doc)
                except ValueError: continue
                selected = fields.get('spring.profiles') or fields.get('spring.config.activate.on-profile')
                if selected and selected != profile: continue
                direct = nacos.declaration(doc)
                business, issues = nacos.business_declarations(doc)
                declared = ([direct] if direct else [])+business
                shared, shared_issues = shared_dataids(fields, profile)
                declared.extend(shared)
                result['warnings'].extend(path+'：'+issue for issue in shared_issues)
                explicit_data_id = fields.get('spring.cloud.nacos.config.data-id') or fields.get('spring.cloud.nacos.config.dataId')
                if explicit_data_id and (not selected or selected == profile):
                    data_id = re.sub(r'\$\{spring\.profiles\.active(?::[^}]*)?\}', profile, explicit_data_id)
                    if (spring_configs and profile and data_id and '${' not in data_id and
                            re.fullmatch(r'[\w.:-]+', data_id)):
                        declared.append({**spring_configs[0], 'dataId':data_id})
                    else:
                        result['warnings'].append(path+'：显式 Spring Nacos Data ID 或连接信息无法确定')
                configs.extend(declared)
                explicit.update(tuple(c[k] for k in ('address','namespace','group','dataId')) for c in declared)
                result['warnings'].extend(path+'：'+x for x in issues)
            generated = {tuple(c[k] for k in ('address','namespace','group','dataId')) for c in spring_configs}
            generated_missing, profile_ok = 0, 0
            for config in configs:
                coordinate = tuple(config[k] for k in ('address','namespace','group','dataId'))
                inferred = coordinate in generated and coordinate not in explicit
                identity = coordinate+(profile,)
                if identity in seen:
                    if identity in downloaded: profile_ok += 1
                    continue
                seen.add(identity)
                if len(seen) > 100: result['warnings'].append('Nacos 候选超过上限'); break
                try:
                    progress('下载 Nacos 配置', len(seen)-1, 0)
                    config = nacos.resolve_group(config) if not config['group'] else config
                    data = nacos.fetch(config)
                    group = profile if profile in GROUPS[:4] else 'other'
                    key = hashlib.sha256('\0'.join(config[k] for k in ('address','namespace','group','dataId')).encode()).hexdigest()[:16]
                    basename = re.sub(r'[^\w.\-]', '_', config['dataId'])[:120] or 'config'
                    dest = group+'/nacos/'+(profile or 'unidentified')+'/'+key+'-'+basename
                    put(output, dest, data)
                    result['files'].append({'source':'nacos','declaration':path,'profile':profile,'namespace':config['namespace'],'group':config['group'],'dataId':config['dataId'],'file':dest})
                    downloaded.add(identity)
                    profile_ok += 1
                except (ValueError, OSError) as e:
                    if inferred and str(e) == 'Nacos 配置读取失败（HTTP 404）':
                        generated_missing += 1
                        result['missingCandidates'].append({'declaration':path,'profile':profile,'dataId':config['dataId'],'reason':'HTTP 404'})
                    else:
                        result['warnings'].append('Nacos '+profile+' / '+config['dataId']+'：'+str(e))
            if generated_missing and not profile_ok:
                result['warnings'].append('Nacos '+profile+'：推导的 Data ID 未找到配置（HTTP 404），请核对声明和权限')
    if not result['files']: raise ValueError('未能下载配置文件')
    result['status'] = 'partial' if result['warnings'] else 'ready'
    return result


def run(data, store, version, apps, repos, cookie, update, activity=lambda *args: None, previous_results=None):
    base = data/'application-config'; base.mkdir(mode=0o700, parents=True, exist_ok=True)
    ids = catalog_app_ids(store)
    by_id = {str(a.get('id')):a for a in apps}
    by_url = {}
    for repo in repos:
        url = repo.get('url','').removesuffix('.git').rstrip('/')
        by_url.setdefault(url, []).append(repo)
    counts = {'total':len(ids),'completed':0,'ready':0,'partial':0,'failed':0,'skipped':0}
    cache = RepositoryCache()
    previous_results = previous_results or []
    completed_ids = set()
    for previous in previous_results:
        app_id, status = previous.get('appId'), previous.get('status')
        if app_id not in ids or app_id in completed_ids or status not in ('ready','partial','failed','skipped'):
            raise ValueError('无法验证续传任务的已完成应用')
        completed_ids.add(app_id)
        counts[status] += 1
        counts['completed'] += 1
    for app_id in ids:
        if app_id in completed_ids: continue
        app = by_id.get(app_id); stage = None
        def progress(phase, completed=0, total=0):
            activity({'appId':app_id, 'name':app.get('name','') if app else '',
                      'phase':phase, 'completed':completed, 'total':total})
        progress('准备应用')
        try:
            if not app: raise ValueError('当前快照中没有此应用')
            if not app.get('appType'): raise ValueError('应用类型未知，无法判断是否应跳过')
            if app.get('appType', '').lower() not in MAVEN_TYPES:
                counts['skipped'] += 1
                result = {'appId':app_id, 'name':app.get('name',''), 'status':'skipped',
                          'reason':'非 WAR/JAR 应用类型：'+str(app.get('appType') or '未知'), 'snapshotVersion':version}
                counts['completed'] += 1
                update(dict(counts), result)
                continue
            name = folder_name(app.get('name',''), app_id)
            target = base/name; backup = base/('.previous-'+name)
            if backup.exists() and not target.exists(): os.replace(backup, target)
            (base/name).mkdir(mode=0o700, exist_ok=True)
            for group in GROUPS: (base/name/group).mkdir(mode=0o700,exist_ok=True)
            url = app.get('repository','').removesuffix('.git').rstrip('/')
            if not url: raise ValueError('当前 DevOps 快照未提供应用仓库地址')
            candidates = by_url.get(url,[])
            if len(candidates) != 1: raise ValueError('当前快照中仓库匹配数量为 '+str(len(candidates)))
            if candidates[0].get('access') == '暂无权限访问' and candidates[0].get('inCodeup') is False:
                raise CodeupPermissionDenied('当前仓库快照记录为无权限，按要求跳过')
            stage = Path(tempfile.mkdtemp(prefix='.sync-', dir=base))
            for group in GROUPS: (stage/group).mkdir(mode=0o700)
            result = sync_one(app, candidates[0], cookie, stage, progress, cache)
            result['snapshotVersion'] = version
            put(stage, 'other/manifest.json', json.dumps(result,ensure_ascii=False,indent=2).encode())
            # Replace only after a bounded per-app snapshot has completed. A backup
            # survives interrupted swaps and is never treated as current output.
            if backup.exists(): shutil.rmtree(backup)
            os.replace(target,backup)
            try: os.replace(stage,target); stage=None
            except Exception: os.replace(backup,target); raise
            shutil.rmtree(backup)
            counts[result['status']] += 1
        except CodeupAuthenticationError:
            raise
        except CodeupPermissionDenied as e:
            counts['skipped'] += 1
            result = {'appId':app_id, 'name':app.get('name','') if app else '', 'status':'skipped',
                      'reason':str(e), 'skipReason':'REPOSITORY_FORBIDDEN', 'snapshotVersion':version}
        except Exception as e:
            counts['failed'] += 1
            result = {'appId':app_id, 'name':app.get('name','') if app else '', 'status':'failed', 'error':str(e)[:300], 'snapshotVersion':version}
        finally:
            if stage: shutil.rmtree(stage,ignore_errors=True)
        counts['completed'] += 1
        update(dict(counts), result)
    return counts
