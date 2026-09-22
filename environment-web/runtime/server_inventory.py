"""Read-only inventory through the same Koko protocol as ../JumpServer.

Never persist terminal transcripts, passwords, cookies or raw nginx configuration.
Unknown processes are retained; only explicitly recognized OS processes are excluded.
"""
import base64
import fnmatch
import json
import posixpath
import re
import shlex
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

BASE = 'http://10.179.2.146:8080'

_SLOT_NAME = re.compile(r'^(slot:\d+)/')


def inner_prefix():
    """Keep inner pool threads attributable to the slot that spawned them."""
    match = _SLOT_NAME.match(threading.current_thread().name)
    return match.group(1).replace(':', '-') + '-' if match else ''


SYSTEM = set('systemd init kthreadd kworker ksoftirqd migration rcu_sched rcu_preempt watchdog udevd systemd-udevd systemd-journald systemd-logind systemd-resolved systemd-networkd dbus-daemon rsyslogd syslogd auditd audispd sshd cron crond anacron chronyd ntpd agetty getty polkitd irqbalance accounts-daemon cupsd avahi-daemon NetworkManager dhclient rpcbind rpc.statd'.split())
PROCESS_RE = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\w+\s+\w+\s+\d+\s+\d+:\d+:\d+\s+\d+)\s+(\S+)\s+(.*)$')
PS = "LC_ALL=C TZ=UTC ps -eww -o pid=,ppid=,user:32=,etimes=,lstart=,comm=,args="


def clean(text):
    return re.sub(r'[^\n\r\t\x20-\uFFFF]', '', re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)).replace('\r', '')


def redact(text):
    # Arguments can contain application secrets even though collection is read-only.
    return re.sub(r'(?i)((?:password|passwd|pwd|secret|token|access[_-]?key)(?:\s*[=:]\s*|\s+))[^\s;]+', r'\1[REDACTED]', text)


def parse_processes(body):
    rows, excluded, malformed = [], 0, 0
    for line in body.splitlines():
        if not line.strip(): continue
        m = PROCESS_RE.match(line)
        if not m:
            malformed += 1
            continue
        pid, ppid, user, age, start, comm, command = m.groups()
        if command.startswith('[') or comm in SYSTEM:
            excluded += 1
            continue
        lower = command.lower()
        kind = next((name for name, pattern in [('Tomcat',r'org\.apache\.catalina'), ('Kafka',r'kafka\.'), ('Nginx',r'nginx'), ('Java',r'\bjava\b'), ('Redis',r'redis-server'), ('MySQL',r'mysqld'), ('PostgreSQL',r'postgres'), ('Node.js',r'\bnode\b'), ('Python',r'\bpython[23]?\b')] if re.search(pattern, lower)), '未分类')
        try:
            stamp = datetime.strptime(start, '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            malformed += 1
            continue
        rows.append(dict(pid=int(pid), ppid=int(ppid), user=user, name=comm, kind=kind,
                         startedAt=stamp, elapsedSeconds=int(age), command=redact(command)))
    return rows, excluded, malformed


def skip_lua_block(text, start):
    """Lua bodies use Lua grammar, not Nginx's semicolon-terminated directives."""
    i=start+1; depth=1
    while i<len(text):
        comment=text.startswith('--',i)
        if comment:i+=2
        long=re.match(r'\[(=*)\[',text[i:])
        if long:
            end=text.find(']'+long[1]+']',i+len(long[0]))
            if end<0: raise ValueError('未闭合 Lua 长字符串')
            i=end+len(long[1])+2;continue
        if comment:
            end=text.find('\n',i);i=len(text) if end<0 else end+1;continue
        if text[i] in ('"',"'"):
            quote=text[i];i+=1
            while i<len(text):
                if text[i]=='\\':i+=2;continue
                if text[i]==quote:i+=1;break
                i+=1
            continue
        if text[i]=='{':depth+=1
        if text[i]=='}':
            depth-=1
            if depth==0:return i
        i+=1
    raise ValueError('未闭合 Lua 配置块')


def tokenize(text):
    """Nginx punctuation, comments, escaped characters, quotes and ${variables}."""
    tokens, buf, quote, i = [], '', None, 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            buf += text[i:i+2]; i += 2; continue
        if quote:
            if c == quote: quote = None
            else: buf += c
        elif c in '\"\'': quote = c
        elif c == '$' and i+1 < len(text) and text[i+1] == '{':
            end = text.find('}', i+2)
            if end < 0: raise ValueError('未闭合变量')
            buf += text[i:end+1]; i = end
        elif c == '#' and not buf:
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        elif c == '{' and (buf or (tokens[-1] if tokens else '')).endswith('_by_lua_block'):
            if buf: tokens.append(buf);buf=''
            tokens.extend(['{','}']);i=skip_lua_block(text,i)
        elif c.isspace() or c in '{};':
            if buf: tokens.append(buf); buf = ''
            if c in '{};': tokens.append(c)
        else: buf += c
        i += 1
    if quote: raise ValueError('未闭合引号')
    if buf: tokens.append(buf)
    return tokens


def parse_tree(text):
    tokens = iter(tokenize(text))
    def block(nested=False):
        result, args = [], []
        for token in tokens:
            if token == '}':
                if not nested or args: raise ValueError('配置块不完整')
                return result
            if token in (';', '{'):
                if not args: raise ValueError('空指令')
                result.append((args, block(True) if token == '{' else [])); args = []
            else: args.append(token)
        if nested or args: raise ValueError('配置不完整')
        return result
    return block()


def parse_nginx(dump):
    sections = re.split(r'(?m)^# configuration file (.+):\s*\n', dump)
    files = {sections[i]: sections[i+1] for i in range(1, len(sections)-1, 2)}
    if not files: raise ValueError('nginx -T 未返回配置文件')
    root = next(iter(files)); prefix = posixpath.dirname(root); warnings = []
    def expand(path, stack=()):
        if path in stack: raise ValueError('循环 include')
        def walk(nodes):
            out = []
            for args, children in nodes:
                if args[0] == 'include' and len(args) == 2:
                    pattern = args[1] if args[1].startswith('/') else posixpath.join(prefix,args[1])
                    matches = [p for p in files if fnmatch.fnmatchcase(p, pattern)]
                    if not matches and not any(c in args[1] for c in '*?['): warnings.append('include 未展开：'+args[1])
                    for p in matches: out.extend(expand(p, stack+(path,)))
                else:
                    if '_by_lua' in args[0]: warnings.append('包含 Lua 动态逻辑；静态配置不能确定运行时域名、URI 或后端')
                    out.append((args, walk(children)))
            return out
        return walk(parse_tree(files[path]))
    tree = expand(root)
    routes = []
    def values(nodes, key): return [a[1:] for a,c in nodes if a[0] == key]
    def endpoint(value, default='80'):
        if value.startswith('unix:'): return {'host':value, 'port':None, 'resolution':'unix'}
        if '$' in value: return {'host':value, 'port':None, 'resolution':'dynamic'}
        parsed = urlsplit('//'+value)
        try: port = str(parsed.port) if parsed.port else default
        except ValueError: port = None
        host = parsed.hostname or value
        import ipaddress
        try: ipaddress.ip_address(host); resolution='ip'
        except ValueError: resolution='hostname'
        return {'host':host,'port':port,'resolution':resolution}
    for args, context in tree:
        if args[0] not in ('http','stream'): continue
        upstreams = {a[1]: [endpoint(v[0]) for v in values(c,'server') if v] for a,c in context if a[0]=='upstream' and len(a)>1}
        def visit(nodes, domains, listeners, uri, inherited=None):
            proxies = [(a[0],a[1]) for a,c in nodes if a[0] in ('proxy_pass','fastcgi_pass','grpc_pass','uwsgi_pass','scgi_pass') and len(a)>1]
            effective = proxies or inherited or []
            for directive, target in effective:
                parsed=urlsplit(target if '://' in target else '//'+target)
                name=parsed.netloc if '://' in target else target
                backends=upstreams.get(name)
                if backends is None:
                    backends=[endpoint(name, '443' if parsed.scheme in ('https','grpcs') else '80')]
                routes.append(dict(domains=domains, listen=listeners, uri=uri, directive=directive,
                                   target=target, upstream=name, backends=backends, context=args[0]))
            if not effective:
                routes.append(dict(domains=domains,listen=listeners,uri=uri,directive='static/other',target='',upstream='',backends=[],context=args[0]))
            for a,c in nodes:
                if a[0]=='location': visit(c,domains,listeners,' '.join(a[1:]),effective)
                elif a[0] in ('if','limit_except'): visit(c,domains,listeners,uri,effective)
        for a,c in context:
            if a[0]=='server':
                domains=[x for v in values(c,'server_name') for x in v]
                visit(c,domains,[' '.join(v) for v in values(c,'listen')], '/' if args[0]=='http' else '(TCP/UDP)')
    return routes, sorted(set(warnings))


class Terminal:
    def __init__(self, asset, account, cookie, password):
        import websocket
        self.wsmod=websocket; self.password=password; self.account=account; self.tid=None
        self.ws=websocket.create_connection(BASE.replace('http:','ws:')+'/koko/ws/terminal/?'+urlencode({'target_id':asset['id'],'type':'asset','system_user_id':account['id']}), cookie=cookie, subprotocols=['JMS-KOKO'], origin=BASE, timeout=5)

    def send(self, text): self.ws.send(json.dumps({'id':self.tid,'type':'TERMINAL_DATA','data':text}))

    def recv(self):
        try: raw=self.ws.recv()
        except self.wsmod.WebSocketTimeoutException: return ''
        if not raw: raise ConnectionError('连接已关闭')
        msg=json.loads(raw)
        if msg['type']=='CONNECT':
            self.tid=msg['id'];self.ws.send(json.dumps({'id':self.tid,'type':'TERMINAL_INIT','data':json.dumps({'cols':1000,'rows':40})}))
        if msg['type'] in ('CLOSE','ERROR'): raise ConnectionError('堡垒机关闭连接或拒绝访问')
        return msg.get('data','') if msg['type']=='TERMINAL_DATA' else ''

    def login(self):
        deadline=time.monotonic()+45; buf=''; supplied=False
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())[-20000:]
            if re.search(r'authentication failed|permission denied|认证失败|发生错误|无法连接|connection refused',buf,re.I):
                reason = ('SSH 认证失败或账号无权限' if re.search(r'authentication failed|permission denied|认证失败',buf,re.I) else '目标 SSH 端口拒绝连接' if re.search(r'connection refused',buf,re.I) else '堡垒机连接资产失败')
                raise ConnectionError(reason)
            if re.search(r'(?:password|密码)[：:]\s*$',buf,re.I):
                if self.account.get('username') != 'folidev' or not self.password or supplied:
                    raise ConnectionError('资产需要密码，未配置 folidev 密码或密码被拒绝')
                self.send(self.password+'\r'); supplied=True;buf=''
            elif self.tid and re.search(r'[^\n]*[#$>]\s*$',buf):
                # Prove a command can run; a banner ending in # is not login success.
                rc, output=self.run('id -u', timeout=15)
                if rc == 0 and re.search(r'(?m)^\d+$',output): return
                raise ConnectionError('终端未能执行登录验证命令')
        raise TimeoutError('登录超时（45 秒）')

    def elevate(self):
        """Enter the authorized sudo login shell once; never retry a wrong password."""
        rc, body = self.run('id -u', timeout=15)
        if rc == 0 and body.strip() == '0': return 'root', ''
        token = 'SUDO'+uuid.uuid4().hex
        prompt = token+'_PASSWORD'
        has_password = bool(self.password and self.account.get('username') == 'folidev')
        command = 'sudo -p '+shlex.quote(prompt)+' -i' if has_password else 'sudo -n -i'
        self.send("printf '\\n%s%s\\n' "+token+" _BEGIN; "+command+"; r=$?; printf '\\n%s%s:%s\\n' "+token+" _EXIT \"$r\"\r")
        deadline=time.monotonic()+45; buf=''; supplied=False
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())[-30000:]
            marker=token+'_BEGIN\n'
            if marker not in buf: continue
            body=buf.split(marker,1)[1]
            wrong=re.search(r'Sorry, try again|incorrect password|authentication failure|对不起.*重试|抱歉.*重试|密码.*错误',body,re.I)
            if supplied and (wrong or body.rstrip().endswith(prompt) and body.count(prompt)>1):
                self.send('\x03')
                return 'password_error','sudo 密码错误'
            if body.rstrip().endswith(prompt) and not supplied:
                self.send(self.password+'\r'); supplied=True
                continue
            if re.search(r'(?m)^'+token+r'_EXIT:\d+',body):
                if re.search(r'password.*required|需要密码',body,re.I): return 'password_required','sudo 需要 folidev 密码'
                return 'denied','sudo -i 不可用或无权限'
            if re.search(r'[^\n]*[#$>]\s*$',body):
                rc, identity=self.run('id -u',timeout=15)
                if rc==0 and identity.strip()=='0': return ('password' if supplied else 'passwordless'),''
                return 'denied','sudo -i 未获得 root 权限'
        self.send('\x03')
        return 'timeout','sudo -i 超时'

    def run(self, command, timeout=60):
        token='INV'+uuid.uuid4().hex
        # Split markers ensure echoed commands cannot be interpreted as output.
        self.send("printf '\\n%s%s\\n' "+token+" _BEGIN; "+command+"; r=$?; printf '\\n%s%s:%s\\n' "+token+" _END \"$r\"\r")
        deadline=time.monotonic()+timeout;buf=''
        while time.monotonic()<deadline:
            buf=clean(buf+self.recv())
            if len(buf)>24_000_000: raise ValueError('终端输出超过 24 MB 限制')
            match=re.search(r'(?m)^'+token+r'_END:(\d+)\s*$',buf)
            if match:
                marker=token+'_BEGIN\n'
                if marker not in buf: raise ValueError('终端输出帧缺失')
                return int(match[1]),buf.split(marker,1)[1].split(token+'_END:',1)[0].strip()
        self.send('\x03')
        raise TimeoutError('只读命令执行超时')

    def close(self):
        try: self.ws.close(timeout=1)
        except Exception: pass


def nginx_commands(processes):
    """Use each running master executable and its original -p / -c options."""
    commands=[]
    for p in processes:
        if 'nginx: master process ' not in p['command']: continue
        args=shlex.split(p['command'].split('nginx: master process ',1)[1])
        if not args: continue
        executable=f"/proc/{p['pid']}/exe"
        opts=[];i=1
        while i<len(args):
            if args[i] in ('-p','-c','-g') and i+1<len(args): opts.extend(args[i:i+2]);i+=2
            elif args[i][:2] in ('-p','-c','-g') and len(args[i])>2: opts.append(args[i]);i+=1
            else:i+=1
        commands.append(' '.join(shlex.quote(a) for a in [executable,'-T',*opts]))
    return list(dict.fromkeys(commands))


def collect_config_files(terminal, paths, instance):
    # Base64 protects exact file bytes from PTY CR/LF conversion and escape stripping.
    marker='CONFIG'+uuid.uuid4().hex
    commands=[]
    for i,path in enumerate(paths):
        commands.append("printf '\\n%s%s\\n' "+marker+' _FILE_'+str(i)+"; base64 -- "+shlex.quote(path)+" 2>/dev/null; r=$?; printf '\\n%s%s:%s\\n' "+marker+' _DONE_'+str(i)+' \"$r\"')
    if not commands: return [], ['Nginx 未返回配置文件清单']
    _, output=terminal.run('; '.join(commands),timeout=90)
    files=[];errors=[]
    for i,path in enumerate(paths):
        match=re.search(re.escape(marker+'_FILE_'+str(i))+r'\n(.*?)\n'+re.escape(marker+'_DONE_'+str(i))+r':(\d+)',output,re.S)
        if not match or match[2]!='0':
            errors.append('原始配置读取失败：'+path);continue
        try:
            encoded=''.join(match[1].split()); raw=base64.b64decode(encoded,validate=True)
            files.append({'path':path,'instance':instance,'content':raw.decode('utf-8',errors='replace'),'base64':encoded,'bytes':len(raw)})
        except ValueError: errors.append('原始配置传输校验失败：'+path)
    return files,errors


def ls_glob(terminal, pattern):
    """Expand an absolute ls pattern that may contain shell globs.

    Globs must stay outside quotes or the shell passes them to ls literally and
    nothing matches. Patterns come from nginx include directives and our own
    conf.d sweep, so restrict them to safe filename characters and send them
    unquoted; -1 keeps ls one-per-line even though stdout is a PTY.
    """
    if not re.fullmatch(r'[A-Za-z0-9_./?*\[\]-]+', pattern) or '..' in pattern:
        return []
    rc, listing = terminal.run('ls -1 -- '+pattern+' 2>/dev/null', timeout=15)
    if rc != 0: return []
    return [line.strip() for line in listing.splitlines() if line.strip()]


def list_conf_d(terminal, prefix):
    """All .conf files under <prefix>/conf.d that the current account can list."""
    return ls_glob(terminal, prefix.rstrip('/')+'/conf.d/*.conf')


def nginx_process_config(processes):
    """Return (prefix, conf_path) from the first nginx master process, or defaults."""
    for p in processes:
        if 'nginx: master process ' not in p['command']: continue
        args=shlex.split(p['command'].split('nginx: master process ',1)[1])
        if not args: continue
        prefix='/etc/nginx';conf=None;i=1
        while i<len(args):
            if args[i]=='-p' and i+1<len(args): prefix=args[i+1];i+=2
            elif args[i].startswith('-p') and len(args[i])>2: prefix=args[i][2:];i+=1
            elif args[i]=='-c' and i+1<len(args): conf=args[i+1];i+=2
            elif args[i].startswith('-c') and len(args[i])>2: conf=args[i][2:];i+=1
            else:i+=1
        if not conf: conf=prefix.rstrip('/')+'/nginx.conf'
        return prefix,conf
    return '/etc/nginx','/etc/nginx/nginx.conf'


def collect_nginx_files(terminal, rows):
    """Collect every reachable nginx config file.

    Strategy: run nginx -T per running instance, then always sweep
    <prefix>/conf.d/*.conf and base64-read every conf.d file that -T did not
    yield (unloaded orphans or read failures). When no -T succeeded, the main
    config and its include targets are read directly and routes are parsed from
    the raw files on a best-effort basis.

    Returns (files, routes, warnings, notes, via_nginx_t); `warnings` are
    integrity issues that lower the collection status, `notes` are informational.
    """
    prefix, main_conf = nginx_process_config(rows)
    commands = nginx_commands(rows)
    all_files, all_routes, warnings, notes = [], [], [], []
    collected = set()
    via_nginx_t = False
    for command in commands:
        rc, dump = terminal.run(command, timeout=60)
        if rc != 0 or not dump:
            warnings.append('Nginx -T 读取失败：检查配置权限、证书权限或 nginx 版本')
            continue
        via_nginx_t = True
        paths = list(dict.fromkeys(re.findall(r'(?m)^# configuration file (.+):\s*$', dump)))
        files, file_errors = collect_config_files(terminal, paths, command.split()[0])
        all_files.extend(files); warnings.extend(file_errors)
        collected.update(f['path'] for f in files)
        try:
            routes, parse_warnings = parse_nginx(dump)
            for route in routes: route['instance'] = command.split()[0]
            all_routes.extend(routes); warnings.extend(parse_warnings)
        except ValueError:
            warnings.append('Nginx 配置解析不完整')
    # conf.d 全量补采：-T 未列出（未加载）或读取失败的 .conf 一律直接读取
    missing = [p for p in list_conf_d(terminal, prefix) if p not in collected]
    if missing:
        files, errors = collect_config_files(terminal, missing, 'folidev')
        all_files.extend(files); warnings.extend(errors)
        collected.update(f['path'] for f in files)
        if via_nginx_t and files:
            notes.append('conf.d 中存在未被加载的配置文件，已一并采集：'+', '.join(f['path'] for f in files))
    if not via_nginx_t:
        warnings.extend(read_nginx_without_t(terminal, prefix, main_conf, collected, all_files, all_routes))
    return all_files, all_routes, warnings, notes, via_nginx_t


def read_nginx_without_t(terminal, prefix, main_conf, collected, all_files, all_routes):
    """No nginx -T available: read the main config plus its include targets
    directly, then parse routes from the raw files. Returns integrity warnings."""
    warnings = []
    rc, main_text = terminal.run('cat '+shlex.quote(main_conf), timeout=30)
    include_patterns = [m.group(1).strip() for m in re.finditer(r'(?m)^\s*include\s+([^;]+);', main_text)] if rc == 0 else []
    read_paths = [main_conf] if main_conf not in collected else []
    for pattern in include_patterns:
        absolute = pattern if pattern.startswith('/') else prefix.rstrip('/')+'/'+pattern
        read_paths.extend(p for p in ls_glob(terminal, absolute) if p not in collected and p not in read_paths)
    if read_paths:
        files, errors = collect_config_files(terminal, read_paths, 'folidev')
        all_files.extend(files); warnings.extend(errors)
        collected.update(f['path'] for f in files)
    if not all_files:
        warnings.append('当前账号无法读取 Nginx 配置文件')
        return warnings
    main_file = next((f for f in all_files if f['path'] == main_conf), None)
    if main_file:
        dump_parts = ['# configuration file '+main_file['path']+':\n'+main_file['content']+'\n']
    else:
        abs_include = [p if p.startswith('/') else prefix.rstrip('/')+'/'+p for p in include_patterns] \
            or [prefix.rstrip('/')+'/conf.d/*.conf']
        dump_parts = ['# configuration file '+main_conf+':\nhttp { '+'; '.join('include '+p for p in abs_include)+'; }\n']
    for f in all_files:
        if f is main_file: continue
        dump_parts.append('# configuration file '+f['path']+':\n'+f['content']+'\n')
    try:
        routes, parse_warnings = parse_nginx('\n'.join(dump_parts))
        for route in routes: route['instance'] = 'folidev'
        all_routes.extend(routes); warnings.extend(parse_warnings)
    except ValueError:
        warnings.append('Nginx 配置解析不完整')
    return warnings


def inspect_asset(asset, accounts, cookie, password, terminal_factory=Terminal):
    result={'checkedAt':datetime.now(timezone.utc).isoformat(), 'loginStatus':'cannot_login', 'reason':'没有授权 SSH 账号', 'processStatus':'not_collected','processes':[], 'nginxStatus':'not_collected', 'nginxRoutes':[], 'nginxConfigurations':[], 'warnings':[], 'attempts':[]}
    ssh=[a for a in accounts if a.get('protocol')=='ssh']
    ssh.sort(key=lambda a:a.get('username')!='folidev')
    for account in ssh:
        terminal=None
        try:
            terminal=terminal_factory(asset,account,cookie,password);terminal.login()
        except Exception as exc:
            # Known safe messages only, never exception bodies / terminal output.
            reason=str(exc) if type(exc) in (ConnectionError,TimeoutError) else type(exc).__name__
            result['attempts'].append({'account':account.get('username',''), 'reason':reason})
            result['reason']=reason
            if terminal: terminal.close()
            continue
        result.update(loginStatus='can_login',reason='',account=account.get('username',''))
        try:
            sudo_status, sudo_reason = terminal.elevate()
            result['sudoStatus'] = sudo_status
            elevated = sudo_status in ('root','passwordless','password')
            if sudo_reason:
                result['warnings'].append(sudo_reason)
                result['nginxStatus']='sudo_password_error' if sudo_status=='password_error' else 'sudo_unavailable'
            rc, body=terminal.run(PS)
            rows, excluded, malformed=parse_processes(body) if rc==0 else ([],0,0)
            result.update(processes=rows,excludedSystemProcesses=excluded,processStatus='complete' if rc==0 and not malformed and elevated else 'partial' if rc==0 else 'failed')
            if not elevated: result['warnings'].append('sudo 不可用，仅采集当前账号可见进程，完整性未确认')
            if malformed: result['warnings'].append(f'{malformed} 行进程无法解析')
            commands=nginx_commands(rows)
            if not commands:
                result['nginxStatus']='not_running' if result['processStatus']!='failed' and not any(p['kind']=='Nginx' for p in rows) else 'unknown'
            else:
                files, routes, warnings, notes, via_nginx_t = collect_nginx_files(terminal, rows)
                result['nginxConfigurations'].extend(files)
                result['nginxRoutes'].extend(routes)
                result['warnings'].extend(warnings); result['warnings'].extend(notes)
                if not elevated:
                    result['nginxStatus']='sudo_password_error' if sudo_status=='password_error' else 'sudo_unavailable'
                    if files:
                        result['nginxStatus']='partial'
                        result['warnings'].append('未获得 sudo 权限，nginx -T 以当前账号执行成功，完整性未确认' if via_nginx_t
                                                   else '未获得 sudo 权限，仅采集当前账号可见的 Nginx 配置文件，完整性未确认')
                elif via_nginx_t:
                    result['nginxStatus']='partial' if warnings else 'complete'
                else:
                    # -T 全部失败，但 conf.d/主配置直接读取仍有产出或全空
                    result['nginxStatus']='partial'
        except Exception as exc:
            result['warnings'].append('采集中断：'+type(exc).__name__)
            if result['processStatus']=='not_collected': result['processStatus']='failed'
            if result['nginxStatus']=='not_collected': result['nginxStatus']='failed'
        finally: terminal.close()
        return result
    return result


def collect_servers(assets, get, cookie, password, progress=None):
    def run(asset):
        try:
            path='/api/v1/perms/users/assets/'+asset['id']+'/system-users/'
            accounts=get(path)
            if isinstance(accounts,dict):
                rows=accounts.get('results',[])
                expected=accounts.get('count',len(rows))
                while len(rows)<expected:
                    page=get(path+'?limit=100&offset='+str(len(rows)))
                    if page.get('count')!=expected or not page.get('results'): raise ValueError('授权账号分页不完整')
                    rows.extend(page['results'])
                if len(rows)!=expected or len({a['id'] for a in rows})!=len(rows): raise ValueError('授权账号分页不完整')
                accounts=rows
            return inspect_asset(asset,accounts,cookie,password)
        except Exception as exc:
            return {'checkedAt':datetime.now(timezone.utc).isoformat(),'loginStatus':'cannot_login','reason':'读取授权账号失败：'+type(exc).__name__,'processStatus':'not_collected','processes':[],'nginxStatus':'not_collected','nginxRoutes':[],'warnings':[]}
    results={}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix=inner_prefix()) as pool:
        futures={pool.submit(run,a):a for a in assets}
        for future in as_completed(futures):
            a=futures[future];results[a['id']]=future.result()
            if progress: progress(len(results),len(assets))
    return results
