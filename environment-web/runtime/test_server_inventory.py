import base64
import json
import re
import time
import unittest
from unittest.mock import patch
import server_inventory as inv

def base64_reply(command, contents):
    """Path-aware base64 response mimicking collect_config_files markers."""
    marker = 'CONFIGabc'
    paths = re.findall(r'base64 -- (\S+)', command)
    out = []
    for i, path in enumerate(paths):
        out.append(marker+'_FILE_'+str(i)+'\n'+base64.b64encode(contents[path]).decode()+'\n'+marker+'_DONE_'+str(i)+':0')
    return 0, '\n'.join(out)

DUMP = '''# configuration file /etc/nginx/nginx.conf:
events {} http { include conf.d/*.conf; }
# configuration file /etc/nginx/conf.d/app.conf:
upstream app { server 10.0.0.1:8080 weight=2; server [::1]:9090; }
server { listen 443 ssl; server_name example.com *.example.com;
 location /api { proxy_pass http://app; }
 location ~ ^/v[0-9]+/ { proxy_pass https://backend.example.org; }
 location /dynamic { proxy_pass http://${backend}:80; }
 location /assets { root /srv/assets; }
}
'''

class InventoryTests(unittest.TestCase):
    def test_processes_retain_unknown_and_root_app(self):
        body=''' 1 0 root 40000000 Tue Sep  1 10:00:00 2026 systemd 0.0 0.0 0 0 /sbin/init
 2 0 root 40000000 Tue Sep  1 10:00:00 2026 kthreadd 0.0 0.0 0 0 [kthreadd]
 11 1 root 31536000 Tue Sep  1 10:00:00 2026 java 3.2 2.5 1048576 4194304 /opt/java -Dpassword=secret org.apache.catalina.Main
 12 1 app 60 Tue Sep  1 10:00:00 2026 custom 1.5 0.1 51200 204800 /opt/custom --token tokenvalue
 invalid
'''
        rows,excluded,malformed=inv.parse_processes(body)
        self.assertEqual((len(rows),excluded,malformed),(4,2,1))
        self.assertTrue(rows[0]['system'])  # systemd — retained with system flag
        self.assertTrue(rows[1]['system'])  # kthreadd — retained with system flag
        self.assertEqual(rows[2]['kind'],'Tomcat')
        self.assertEqual(rows[2]['elapsedSeconds'],31536000)
        self.assertEqual(rows[2]['startedAt'],'2026-09-01T10:00:00+00:00')
        self.assertEqual((rows[2]['memPercent'],rows[2]['rssKB'],rows[2]['vszKB']),(2.5,1048576,4194304))
        self.assertEqual((rows[3]['memPercent'],rows[3]['rssKB'],rows[3]['vszKB']),(0.1,51200,204800))
        self.assertEqual(rows[3]['kind'],'未分类')
        self.assertNotIn('secret',json.dumps(rows));self.assertNotIn('tokenvalue',json.dumps(rows))

    def test_process_with_spaces_in_comm_is_not_dropped(self):
        # Nuxt rewrites its process title; /proc/pid/comm becomes the 15-byte
        # "node /var/www/w" which contains spaces. The whole ps line used to
        # fail PROCESS_RE and the process (and its ss-owned port 3000) vanished.
        body=('24895 21522 root                              739668 Wed Sep 30 01:49:58 2026 node /var/www/w  1.2 0.4 243496 1103304 node /var/www/webapps/atlantis_nuxt/node_modules/nuxt/bin/nuxt.js\n'
              ' 12 1 app 60 Tue Sep  1 10:00:00 2026 custom 1.5 0.1 51200 204800 /opt/custom\n')
        rows,excluded,malformed=inv.parse_processes(body)
        self.assertEqual((len(rows),excluded,malformed),(2,0,0))
        proc=next(p for p in rows if p['pid']==24895)
        self.assertEqual(proc['name'],'node /var/www/w')
        self.assertEqual(proc['kind'],'Node.js')
        self.assertEqual(proc['user'],'root')
        self.assertEqual(proc['ppid'],21522)
        self.assertEqual((proc['memPercent'],proc['rssKB'],proc['vszKB']),(0.4,243496,1103304))
        self.assertEqual(proc['startedAt'],'2026-09-30T01:49:58+00:00')
        self.assertEqual(proc['command'],'node /var/www/webapps/atlantis_nuxt/node_modules/nuxt/bin/nuxt.js')
        # ss still attributes the socket to the renamed process; once the ps
        # row survives, port 3000 is attributed to the Node.js application.
        listeners=inv.parse_listening_ports(
            'LISTEN 0 511 *:3000 *:* users:(("node /var/www/w",pid=24895,fd=19))')
        inv.attach_process_ports(rows,listeners)
        self.assertEqual(proc['ports'],[3000])
        app_ports=inv.build_app_ports(rows,listeners)
        self.assertEqual([(a['app'],a['port']) for a in app_ports],[('Node.js',3000)])

    def test_nginx_includes_ipv6_dynamic_static(self):
        routes,warnings=inv.parse_nginx(DUMP)
        self.assertFalse(warnings)
        route=next(r for r in routes if r['uri']=='/api')
        self.assertEqual(route['domains'],['example.com','*.example.com'])
        self.assertEqual(route['file'],'/etc/nginx/conf.d/app.conf')
        self.assertEqual([(b['host'],b['port']) for b in route['backends']],[('10.0.0.1','8080'),('::1','9090')])
        self.assertEqual(next(r for r in routes if r['uri']=='/dynamic')['backends'][0]['resolution'],'dynamic')
        self.assertEqual(next(r for r in routes if r['uri'].startswith('~'))['backends'][0]['port'],'443')
        self.assertEqual(next(r for r in routes if r['uri']=='/assets')['backends'],[])
        self.assertEqual(next(r for r in routes if r['uri']=='/assets')['file'],'/etc/nginx/conf.d/app.conf')

    def test_openresty_lua_blocks_are_opaque(self):
        dump = """# configuration file /etc/nginx/nginx.conf:
http { upstream kong { server 10.0.0.1:8000;
 balancer_by_lua_block {
  local t = {name = "}"} -- ignored }
  local s = [=[ braces } { ]=]
  require('kong').balancer()
 }
}
server { server_name kong.test; location / { proxy_pass http://kong; } }
}
"""
        routes,warnings=inv.parse_nginx(dump)
        self.assertTrue(routes)
        self.assertEqual(routes[-1]['domains'],['kong.test'])
        self.assertTrue(any('Lua' in w for w in warnings))

    def test_hash_inside_uri_is_not_a_comment_and_empty_glob_is_valid(self):
        routes,warnings=inv.parse_nginx('# configuration file /etc/nginx/nginx.conf:\nhttp { include conf.d/*.conf; server { location / { proxy_pass http://10.0.0.1/path#fragment; } } }')
        self.assertFalse(warnings)
        self.assertEqual(routes[-1]['target'],'http://10.0.0.1/path#fragment')

    def test_nginx_invalid_and_missing_include(self):
        with self.assertRaises(ValueError):inv.parse_nginx('# configuration file /etc/nginx/nginx.conf:\nhttp {')
        _,warnings=inv.parse_nginx('# configuration file /etc/nginx/nginx.conf:\nhttp { include missing.conf; }')
        self.assertTrue(warnings)

    def test_nginx_preserves_runtime_config_and_safe_quoting(self):
        commands=inv.nginx_commands([{'pid':42,'command':'nginx: master process /opt/nginx/sbin/nginx -c /tmp/custom.conf -p /srv/nginx/' }])
        self.assertEqual(commands,['/proc/42/exe -T -c /tmp/custom.conf -p /srv/nginx/'])

    def test_nginx_rejoins_g_directive_string(self):
        # nginx 把 argv 用空格拼进进程标题，引号丢失：`-g 'daemon off;'` 变成
        # `-g daemon off;`。只取第一个词会得到非法的 `-g daemon`，导致 -T 失败。
        commands=inv.nginx_commands([{'pid':326384,'command':'nginx: master process nginx -g daemon off;'}])
        self.assertEqual(commands,["/proc/326384/exe -T -g 'daemon off;'"])

    def test_login_success_separate_from_collection_failure(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'passwordless',''
            def run(self,*a):raise TimeoutError('sensitive output')
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['loginStatus'],'can_login')
        self.assertEqual(row['processStatus'],'failed')
        self.assertNotIn('sensitive',json.dumps(row))

    def test_fallback_account_and_partial_visibility(self):
        class Terminal:
            def __init__(self,a,u,*rest): self.user=u['username']
            def login(self):
                if self.user=='folidev':raise RuntimeError('secret')
            def elevate(self, password=None):return 'denied','sudo 无权限'
            def elevate_su(self, pw):return 'denied','su 无权限'
            def run(self,*a):return 0,'10 1 app 60 Tue Sep  1 10:00:00 2026 custom 1.5 0.1 51200 204800 /opt/custom'
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':u} for u in ['other','folidev']],'cookie','password','','',{},Terminal)
        self.assertEqual(row['account'],'other');self.assertEqual(row['processStatus'],'partial')
        self.assertEqual(row['attempts'][0]['account'],'folidev');self.assertNotIn('secret',json.dumps(row))

    def test_terminal_frames(self):
        terminal=object.__new__(inv.Terminal)
        sent=[];terminal.send=sent.append
        terminal.recv=lambda:next(chunks)
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            chunks=iter(['echo INVabc _BEGIN\nINVabc_BEGIN\n','0\nINVabc_END:0\n'])
            self.assertEqual(terminal.run('id -u'),(0,'0'))

    def test_run_rejects_multiline_commands(self):
        terminal=object.__new__(inv.Terminal)
        with self.assertRaises(ValueError):
            terminal.run('echo A\necho B')

    def test_run_timeout_is_bounded_and_aborts_the_terminal(self):
        class Socket:
            def __init__(self):
                self.timeouts=[]; self.closed=False; self.sent=[]
            def settimeout(self, value): self.timeouts.append(value)
            def send(self, text): self.sent.append(text)
            def close(self): self.closed=True
        terminal=object.__new__(inv.Terminal)
        terminal.ws=Socket(); terminal.tid=None
        terminal._prompt_ready=True; terminal._prompt_tail=''
        terminal.recv=lambda:''
        started=time.monotonic()
        with self.assertRaises(TimeoutError):
            terminal.run('sleep 1', timeout=0.3)
        self.assertLess(time.monotonic()-started, 2.0)
        self.assertTrue(terminal._timed_out)
        self.assertTrue(terminal._aborted)
        self.assertTrue(terminal.ws.closed)
        self.assertTrue(terminal.ws.timeouts and terminal.ws.timeouts[0] <= 0.3)

    def test_run_refuses_a_terminal_aborted_by_timeout(self):
        terminal=object.__new__(inv.Terminal)
        terminal._aborted=True
        with self.assertRaises(ConnectionError):
            terminal.run('id -u')

    def test_probe_command_waits_until_the_interactive_prompt_is_ready(self):
        terminal=object.__new__(inv.Terminal)
        sent=[]
        terminal.send=sent.append
        terminal._prompt_ready=False
        terminal._prompt_tail=''
        chunks=iter(['[root@test ~]# ', 'INVabc_BEGIN\n0\nINVabc_END:0\n', '[root@test ~]# '])
        received=[0]
        def receive():
            value=next(chunks)
            if received[0]==0:
                self.assertEqual(sent, [])
            received[0]+=1
            return value
        terminal.recv=receive
        with patch.object(inv.uuid,'uuid4',return_value=type('U',(),{'hex':'abc'})()):
            self.assertEqual(terminal.run('id -u',wait_for_prompt=True),(0,'0'))
        self.assertTrue(terminal._prompt_ready)
        self.assertEqual(len(sent),1)

    def test_sudo_login_shell_passwordless_correct_and_wrong(self):
        for mode in ('passwordless','password','password_error'):
            terminal=object.__new__(inv.Terminal)
            terminal.password='private-password';terminal.account={'username':'folidev'}
            sent=[];terminal.send=sent.append
            responses=iter([(0,'1000'),(0,'0')]);terminal.run=lambda *a,**k:next(responses)
            chunks=['SUDOabc_BEGIN\n']
            if mode!='passwordless': chunks+=['SUDOabc_PASSWORD']
            if mode=='password_error':
                # After "Sorry, try again", elevate sends Ctrl+C + empty Enters
                # and waits for the _EXIT marker (shell prints it once sudo exits)
                # before returning. Without the _EXIT chunk the wait loop would
                # exhaust the mock recv() iterator.
                chunks+=['Sorry, try again.\n', 'SUDOabc_EXIT:1\n']
            else:
                chunks+=['root@host:~# ']
            chunks=iter(chunks);terminal.recv=lambda:next(chunks)
            with patch.object(inv.uuid,'uuid4') as uid:
                uid.return_value.hex='abc'
                status,_=terminal.elevate()
            self.assertEqual(status,mode)
            self.assertIn(' -i;',sent[0]);self.assertNotIn('private-password',sent[0])
            self.assertEqual(sent.count('private-password\r'),0 if mode=='passwordless' else 1)
            if mode=='password_error':self.assertTrue(sent[-1].startswith('\x03') and '\r' in sent[-1])

    def test_wrong_sudo_password_falls_back_to_user_configs(self):
        main_conf = b'http { include /etc/nginx/conf.d/*.conf; }'
        app_conf = b'server { listen 80; server_name app.test; location / { proxy_pass http://10.0.0.1:8080; } }'
        contents={'/etc/nginx/nginx.conf':main_conf,'/etc/nginx/conf.d/app.conf':app_conf}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'password_error','sudo 密码错误'
            def elevate_su(self, pw):return 'password_error','su 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx'
                if '/proc/' in command and ' -T' in command:
                    return 1, 'nginx: permission denied'
                if command.startswith('cat '):
                    return 0, main_conf.decode()
                if command.startswith('ls '):
                    return 0, '/etc/nginx/conf.d/app.conf'
                if 'base64' in command:
                    return base64_reply(command, contents)
                return 0, ''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['loginStatus'],'can_login')
        self.assertEqual(row['nginxStatus'],'partial')
        self.assertEqual(len(row['nginxConfigurations']),2)
        self.assertEqual({f['path'] for f in row['nginxConfigurations']},{'/etc/nginx/nginx.conf','/etc/nginx/conf.d/app.conf'})
        self.assertTrue(any(r['domains']==['app.test'] for r in row['nginxRoutes']))
        self.assertTrue(any('未获得 sudo 权限' in w for w in row['warnings']))

    def test_elevate_waits_for_sudo_exit_before_returning_password_error(self):
        # Regression: elevate() used to return 'password_error' immediately
        # after sending Ctrl+C, leaving the PTY inside sudo's password retry
        # loop. The next run() command was read as another password attempt
        # and timed out. elevate() must now wait for the _EXIT marker that
        # confirms sudo has exited and the folidev shell has taken over.
        # Here sudo treats the first Ctrl+C as another wrong attempt (common
        # when tgetpass has ISIG disabled); only after the empty Enters
        # exhaust the retry limit does sudo exit and print _EXIT.
        terminal=object.__new__(inv.Terminal)
        terminal.password='private-password';terminal.account={'username':'folidev'}
        sent=[];terminal.send=sent.append
        terminal.run=lambda *a,**k:(0,'1000')
        chunks=iter([
            'SUDOabc_BEGIN\n',
            'SUDOabc_PASSWORD',                              # sudo asks for password
            'Sorry, try again.\nSUDOabc_PASSWORD',           # wrong; sudo asks again (Ctrl+C counted as wrong)
            'Sorry, try again.\nSUDOabc_PASSWORD',           # empty Enter counted as wrong
            'sudo: 3 incorrect password attempts\nSUDOabc_EXIT:1\n',  # sudo exits; shell prints _EXIT
        ])
        terminal.recv=lambda:next(chunks)
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            status,reason=terminal.elevate()
        self.assertEqual(status,'password_error')
        self.assertEqual(reason,'sudo 密码错误')
        # Must have sent Ctrl+C + empty Enters, NOT just Ctrl+C and returned.
        self.assertTrue(sent[-1].startswith('\x03'))
        self.assertIn('\r',sent[-1])
        # Must not have sent the password more than once.
        self.assertEqual(sent.count('private-password\r'),1)

    def test_wrong_sudo_password_no_nginx_process_stays_not_running(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'password_error','sudo 密码错误'
            def elevate_su(self, pw):return 'password_error','su 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '11 1 app 60 Tue Sep  1 10:00:00 2026 java 0.0 0.0 0 0 /opt/java'
                return 0, ''
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['nginxStatus'],'not_running')
        self.assertEqual(row['nginxConfigurations'],[])

    def test_conf_d_glob_reaches_the_shell_unquoted(self):
        # 回归测试：glob 必须裸露发给 shell；被 shlex.quote 包进引号后永不展开（导致 conf.d 采集为空）。
        sent=[]
        class Terminal:
            def run(self, command, timeout=60):
                sent.append(command)
                return 2, ''
        self.assertEqual(inv.list_conf_d(Terminal(),'/etc/nginx'),[])
        self.assertEqual(sent,['ls -1 -- /etc/nginx/conf.d/*.conf 2>/dev/null'])
        # 含空格/特殊字符的 include 模式必须拒绝执行，不能裸露注入 shell
        self.assertEqual(inv.ls_glob(Terminal(),'/etc/nginx/conf.d/x;rm -rf /'),[])
        self.assertEqual(inv.ls_glob(Terminal(),'/etc/nginx/a b.conf'),[])
        self.assertEqual(len(sent),1)

    def test_fallback_collects_conf_d_even_without_include(self):
        # 主配置未 include conf.d 时，也必须采集 conf.d 下全部 .conf 文件。
        contents={'/etc/nginx/nginx.conf':b'http { include sites-enabled/*.conf; }',
                  '/etc/nginx/conf.d/a.conf':b'server { server_name a.test; }',
                  '/etc/nginx/conf.d/b.conf':b'server { server_name b.test; }'}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'password_error','sudo 密码错误'
            def elevate_su(self, pw):return 'password_error','su 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx'
                if ' -T' in command:
                    return 1, 'permission denied'
                if command.startswith('cat '):
                    return 0, contents['/etc/nginx/nginx.conf'].decode()
                if command.startswith('ls ') and 'sites-enabled' in command:
                    return 1, ''
                if command.startswith('ls '):
                    return 0, '/etc/nginx/conf.d/a.conf\n/etc/nginx/conf.d/b.conf'
                if 'base64' in command:
                    return base64_reply(command, contents)
                return 0, ''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['nginxStatus'],'partial')
        paths=sorted(f['path'] for f in row['nginxConfigurations'])
        self.assertEqual(paths,['/etc/nginx/conf.d/a.conf','/etc/nginx/conf.d/b.conf','/etc/nginx/nginx.conf'])
        self.assertTrue(any('未获得 sudo 权限' in w for w in row['warnings']))

    def test_elevated_collects_conf_d_orphans_missing_from_nginx_t(self):
        # nginx -T 只列出已加载文件；conf.d 下未被加载的 .conf 也要一并采集。
        contents={'/etc/nginx/nginx.conf':b'http { server { server_name main.test; } }',
                  '/etc/nginx/conf.d/orphan.conf':b'server { server_name orphan.test; }'}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'passwordless',''
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx'
                if ' -T' in command:
                    return 0, ('# configuration file /etc/nginx/nginx.conf:\n'
                               'http { server { server_name main.test; } }\n')
                if command.startswith('ls '):
                    return 0, '/etc/nginx/conf.d/orphan.conf'
                if 'base64' in command:
                    return base64_reply(command, contents)
                return 0, ''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['nginxStatus'],'complete')
        paths=[f['path'] for f in row['nginxConfigurations']]
        self.assertEqual(paths,['/etc/nginx/nginx.conf','/etc/nginx/conf.d/orphan.conf'])
        self.assertTrue(any('未被加载' in w for w in row['warnings']))
        self.assertTrue(any(r['domains']==['main.test'] for r in row['nginxRoutes']))
        self.assertFalse(any(r['domains']==['orphan.test'] for r in row['nginxRoutes']))

    def test_elevated_nginx_t_failure_still_reads_files_directly(self):
        # sudo 可用但 nginx -T 失败时，必须直接读取主配置与 conf.d 全部 .conf 并解析路由。
        contents={'/etc/nginx/nginx.conf':b'http { include /etc/nginx/conf.d/*.conf; }',
                  '/etc/nginx/conf.d/a.conf':b'server { listen 80; server_name a.test; }',
                  '/etc/nginx/conf.d/b.conf':b'server { listen 81; server_name b.test; }'}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'passwordless',''
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx'
                if ' -T' in command:
                    return 1, 'nginx: [emerg] unknown directive'
                if command.startswith('cat '):
                    return 0, contents['/etc/nginx/nginx.conf'].decode()
                if command.startswith('ls '):
                    return 0, '/etc/nginx/conf.d/a.conf\n/etc/nginx/conf.d/b.conf'
                if 'base64' in command:
                    return base64_reply(command, contents)
                return 0, ''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['nginxStatus'],'partial')
        self.assertTrue(any('-T 读取失败' in w for w in row['warnings']))
        paths=sorted(f['path'] for f in row['nginxConfigurations'])
        self.assertEqual(paths,['/etc/nginx/conf.d/a.conf','/etc/nginx/conf.d/b.conf','/etc/nginx/nginx.conf'])
        domains=[d for r in row['nginxRoutes'] for d in r['domains']]
        self.assertIn('a.test',domains); self.assertIn('b.test',domains)

    def test_containerized_nginx_reads_config_through_proc_root(self):
        # nginx 跑在容器中（官方镜像 `nginx -g daemon off;`）：宿主机没有 /etc/nginx，
        # 必须经 /proc/<pid>/root 读取容器内配置，而不是报 "-T 读取失败"。
        contents={'/etc/nginx/nginx.conf':b'http { include /etc/nginx/conf.d/*.conf; }',
                  '/etc/nginx/conf.d/default.conf':b'server { listen 80; server_name wcms.test; location / { proxy_pass http://wcms:9000; } }'}
        root='/proc/326384/root'
        physical={root+p: v for p,v in contents.items()}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'password',''
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '326384 1 root 60 Tue Sep  1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process nginx -g daemon off;'
                if command.startswith('stat -L'):
                    return 0, '5:14070241\n64768:128'   # 容器根与宿主机根不同
                if command.startswith('cat '):
                    return 0, contents['/etc/nginx/nginx.conf'].decode()
                if command.startswith('ls '):
                    return 0, root+'/etc/nginx/conf.d/default.conf'
                if 'base64' in command:
                    return base64_reply(command, physical)
                return 0, ''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertFalse(any('-T 读取失败' in w for w in row['warnings']))
        self.assertFalse(any('无法读取 Nginx 配置文件' in w for w in row['warnings']))
        self.assertTrue(any('已通过 /proc/326384/root 读取容器内配置' in w for w in row['warnings']))
        paths=sorted(f['path'] for f in row['nginxConfigurations'])
        self.assertEqual(paths,['/etc/nginx/conf.d/default.conf','/etc/nginx/nginx.conf'])
        domains=[d for r in row['nginxRoutes'] for d in r['domains']]
        self.assertIn('wcms.test',domains)

    def test_original_files_preserve_bytes(self):
        raw=b'# original\r\nserver { }\n\xff'
        encoded=base64.b64encode(raw).decode()
        class Terminal:
            def run(self,*a,**kw):return 0,'CONFIGabc_FILE_0\n'+encoded+'\nCONFIGabc_DONE_0:0'
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            files,errors=inv.collect_config_files(Terminal(),['/etc/nginx/test.conf'],'/proc/10/exe')
        self.assertFalse(errors)
        self.assertEqual(base64.b64decode(files[0]['base64']),raw)
        self.assertEqual(files[0]['path'],'/etc/nginx/test.conf')

    def test_many_config_files_are_read_in_bounded_batches(self):
        # A single shell line overflows the PTY canon buffer (Linux MAX_CANON
        # 4096); hosts with ~52 config files hung until run() timed out. Each
        # sent line must stay under the batch limit and every file be collected.
        paths=['/etc/nginx/conf.d/%02d.conf'%i for i in range(52)]
        contents={p:b'upstream u%d { server 10.0.0.1:80; }\n'%i for i,p in enumerate(paths)}
        index={p:i for i,p in enumerate(paths)}
        sent=[]
        class Terminal:
            def run(self, command, timeout=60):
                sent.append(command)
                lines=[]
                for path in re.findall(r'base64 -- (\S+)', command):
                    i=index[path]
                    lines.append('CONFIGabc_FILE_'+str(i)+'\n'+base64.b64encode(contents[path]).decode()+'\nCONFIGabc_DONE_'+str(i)+':0')
                return 0,'\n'.join(lines)
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            files,errors=inv.collect_config_files(Terminal(),paths,'x')
        self.assertFalse(errors)
        self.assertEqual([f['path'] for f in files],paths)
        self.assertGreater(len(sent),1)
        self.assertTrue(all(len(c)<=inv.CONFIG_BATCH_BYTES for c in sent))

    def test_empty_accounts(self):
        row=inv.inspect_asset({'id':'a'},[],'cookie','password','','',{})
        self.assertEqual(row['loginStatus'],'cannot_login');self.assertTrue(row['reason'])

    def test_parse_listening_ports_keeps_pidless_as_none(self):
        body=('Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
              'tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=1234,fd=6))\n'
              'tcp LISTEN 0 50 [::]:7084 [::]:* users:((\"java\",pid=384,fd=42))\n'
              'tcp LISTEN 0 128 127.0.0.1:6379 0.0.0.0:* users:((\"redis-server\",pid=7,fd=4))\n'
              'tcp LISTEN 0 128 0.0.0.0:443 0.0.0.0:* -\n'   # no sudo: pid hidden
              'tcp LISTEN 0 128 0.0.0.0:3306 0.0.0.0:* \n')   # trailing spaces, no process col
        listeners=inv.parse_listening_ports(body)
        ports={(l['pid'],l['port'],l['address']) for l in listeners}
        self.assertEqual(ports,{(1234,80,'0.0.0.0'),(384,7084,'::'),(7,6379,'127.0.0.1'),
                                 (None,443,'0.0.0.0'),(None,3306,'0.0.0.0')})
        self.assertEqual(len(listeners),5)   # header dropped, pid-less kept

    def test_parse_listening_ports_handles_ss_without_netid_column(self):
        # `ss -t` (TCP only) omits the Netid column on some iproute2 versions;
        # the State becomes the first token, shifting every column left by one.
        body=('State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
              'LISTEN 0 65535 *:9091 *:* users:((\"docker-proxy\",pid=20156,fd=4))\n'
              'LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=1234,fd=6))\n'
              'LISTEN 0 50 [::]:7084 [::]:* users:((\"java\",pid=384,fd=42))\n'
              'LISTEN 0 128 0.0.0.0:9200 0.0.0.0:* \n')   # no sudo, pid hidden
        listeners=inv.parse_listening_ports(body)
        ports={(l['pid'],l['port'],l['address']) for l in listeners}
        self.assertEqual(ports,{(20156,9091,'*'),(1234,80,'0.0.0.0'),(384,7084,'::'),
                                 (None,9200,'0.0.0.0')})

    def test_infer_port_pids_matches_via_command_line(self):
        # When sudo is unavailable, ss hides PIDs for other users' sockets.
        # infer_port_pids fills them in by scanning command-line port args.
        processes=[{'pid':384,'kind':'Java','command':'java -Dappid=fosun-cashier --server.port=7084 --spring.profiles.active=test'},
                   {'pid':2721,'kind':'Node.js','command':'node app.js --port=3000'}]
        listeners=[{'pid':None,'port':7084,'address':'0.0.0.0'},
                   {'pid':None,'port':3000,'address':'0.0.0.0'},
                   {'pid':None,'port':3306,'address':'0.0.0.0'}]   # no cmd-line match → stays None
        inv.infer_port_pids(listeners,processes)
        self.assertEqual(listeners[0]['pid'],384)   # matched via --server.port=7084
        self.assertEqual(listeners[1]['pid'],2721)  # matched via --port=3000
        self.assertIsNone(listeners[2]['pid'])      # no match → still None

    def test_infer_port_pids_skips_ambiguous_matches(self):
        # Two processes mention the same port → can't decide, stays None.
        processes=[{'pid':100,'kind':'Java','command':'java --server.port=8080'},
                   {'pid':200,'kind':'Java','command':'java --server.port=8080'}]
        listeners=[{'pid':None,'port':8080,'address':'0.0.0.0'}]
        inv.infer_port_pids(listeners,processes)
        self.assertIsNone(listeners[0]['pid'])

    def test_app_name_resolves_java_appid_and_jar(self):
        self.assertEqual(inv.app_name({'kind':'Java','command':'/opt/java -Dappid=fosun-cashier --server.port=7084'}),'fosun-cashier')
        self.assertEqual(inv.app_name({'kind':'Java','command':'java -Dspring.application.name=order-svc -jar app.jar'}),'order-svc')
        self.assertEqual(inv.app_name({'kind':'Java','command':'java -jar /opt/apps/cashier.jar'}),'cashier.jar')
        self.assertEqual(inv.app_name({'kind':'Java','command':'java -version'}),'Java')
        self.assertEqual(inv.app_name({'kind':'Nginx','command':'nginx: master process /usr/sbin/nginx'}),'Nginx')
        self.assertEqual(inv.app_name({'kind':'未分类','name':'myd','command':'./myd'}),'myd')

    def test_build_app_ports_groups_and_reports_unknown_pids(self):
        processes=[{'pid':1234,'kind':'Nginx','command':'nginx: master process /usr/sbin/nginx'},
                   {'pid':384,'kind':'Java','command':'java -Dappid=fosun_cashier --server.port=7084'}]
        listeners=[{'pid':1234,'port':80,'address':'0.0.0.0'},
                   {'pid':1234,'port':8080,'address':'0.0.0.0'},
                   {'pid':384,'port':7084,'address':'::'},
                   {'pid':9999,'port':22,'address':'0.0.0.0'},   # not in ps → 未知进程
                   {'pid':None,'port':3306,'address':'0.0.0.0'}]   # sudo unavailable → 未知进程
        ports=inv.build_app_ports(processes,listeners)
        self.assertEqual([a['app'] for a in ports],['Nginx','Nginx','fosun_cashier','未知进程','未知进程'])
        self.assertEqual([a['port'] for a in ports],[80,8080,7084,22,3306])
        cashier=next(a for a in ports if a['app']=='fosun_cashier')
        self.assertEqual(cashier['kind'],'Java');self.assertEqual(cashier['pids'],[384]);self.assertEqual(cashier['addresses'],['::'])
        unknown=next(a for a in ports if a['port']==3306)
        self.assertEqual(unknown['app'],'未知进程');self.assertEqual(unknown['kind'],'未知');self.assertEqual(unknown['pids'],[])

    def test_attach_process_ports_assigns_sorted_ports_by_pid(self):
        processes=[{'pid':384,'kind':'Java','command':'java -Dappid=a'},
                   {'pid':1234,'kind':'Nginx','command':'nginx: master process'},
                   {'pid':7,'kind':'Redis','command':'redis-server'}]
        listeners=[{'pid':384,'port':7084,'address':'::'},
                   {'pid':1234,'port':8080,'address':'0.0.0.0'},
                   {'pid':1234,'port':80,'address':'0.0.0.0'},
                   {'pid':9999,'port':22,'address':'0.0.0.0'},   # not in ps, ignored
                   {'pid':None,'port':3306,'address':'0.0.0.0'}]   # sudo unavailable, skipped
        inv.attach_process_ports(processes,listeners)
        self.assertEqual(processes[0]['ports'],[7084])
        self.assertEqual(processes[1]['ports'],[80,8080])   # 排序去重
        self.assertNotIn('ports',processes[2])   # 无监听不带字段

    def test_parse_proc_net_tcp_extracts_port_uids(self):
        body=('  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
              '   0: 0100007F:0CEA 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000\n'
              '   1: 00000000:01F6 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 67890 1 0000000000000000\n'
              '   2: 00000000:0050 00000000:0000 06 00000000:00000000 00:00000000 00000000     0        0 11111 1 0000000000000000\n')
        port_uids=inv.parse_proc_net_tcp(body)
        # 0CEA=3306 uid=0, 01F6=502 uid=1000, 0050=80 state=06(not LISTEN) skipped
        self.assertEqual(port_uids,{3306:{0},502:{1000}})

    def test_parse_passwd_extracts_user_uids(self):
        body=('root:x:0:0:root:/root:/bin/bash\n'
              'nobody:x:65534:65534:nobody:/:/sbin/nologin\n'
              'app:x:1000:1000::/home/app:/bin/bash\n'
              'bad:x:notanumber:1000::/home/bad:/bin/bash\n')
        mapping=inv.parse_passwd(body)
        self.assertEqual(mapping,{'root':0,'nobody':65534,'app':1000})

    def test_infer_port_pids_uid_matching(self):
        # Port 3306 has uid=0 in /proc/net/tcp; only mysqld runs as root → match
        # Port 9200 has uid=1000; only java runs as app(uid=1000) → match
        processes=[{'pid':1693,'user':'root','name':'mysqld','kind':'MySQL','command':'mysqld --daemonize'},
                   {'pid':1544,'user':'app','name':'java','kind':'Java','command':'java -Xms8g'}]
        listeners=[{'pid':None,'port':3306,'address':'0.0.0.0'},
                   {'pid':None,'port':9200,'address':'0.0.0.0'}]
        port_uids={3306:{0},9200:{1000}}
        user_to_uid={'root':0,'app':1000}
        inv.infer_port_pids(listeners,processes,port_uids,user_to_uid)
        self.assertEqual(listeners[0]['pid'],1693)   # UID 0 → only mysqld
        self.assertEqual(listeners[1]['pid'],1544)   # UID 1000 → only java

    def test_infer_port_pids_uid_skips_multi_candidates(self):
        # Two processes run as root → can't decide by UID alone
        processes=[{'pid':100,'user':'root','name':'sshd','kind':'SSH','command':'sshd'},
                   {'pid':200,'user':'root','name':'mysqld','kind':'MySQL','command':'mysqld'}]
        listeners=[{'pid':None,'port':3306,'address':'0.0.0.0'}]
        port_uids={3306:{0}}
        user_to_uid={'root':0}
        inv.infer_port_pids(listeners,processes,port_uids,user_to_uid)
        # UID matching: 2 root processes → skip; well-known 3306 → 2 candidates (sshd not MySQL kind) → 1 match
        self.assertEqual(listeners[0]['pid'],200)   # well-known port matches mysqld, not sshd

    def test_infer_port_pids_well_known_port_matching(self):
        # No /proc/net/tcp data → falls to level 3 (well-known port)
        processes=[{'pid':1693,'user':'root','name':'mysqld','kind':'MySQL','command':'mysqld'},
                   {'pid':1544,'user':'app','name':'java','kind':'Java','command':'/opt/elasticsearch/bin/java -Des.path.home=/opt/elasticsearch'}]
        listeners=[{'pid':None,'port':3306,'address':'0.0.0.0'},
                   {'pid':None,'port':9200,'address':'0.0.0.0'},
                   {'pid':None,'port':5601,'address':'0.0.0.0'},
                   {'pid':None,'port':8080,'address':'0.0.0.0'}]
        inv.infer_port_pids(listeners,processes)   # no port_uids/user_to_uid
        self.assertEqual(listeners[0]['pid'],1693)   # 3306 → mysqld
        self.assertEqual(listeners[1]['pid'],1544)   # 9200 → java with elasticsearch in cmd
        self.assertIsNone(listeners[2]['pid'])       # 5601 → no node process → no match
        self.assertIsNone(listeners[3]['pid'])       # 8080 → not well-known → no match

    def test_match_well_known_port_keyword_filter(self):
        # Two Java processes, only one has 'elasticsearch' in command
        processes=[{'pid':100,'name':'java','kind':'Java','command':'java -Xms8g -Xmx8g'},
                   {'pid':200,'name':'java','kind':'Java','command':'java -Des.path.home=/opt/elasticsearch'}]
        self.assertEqual(inv.match_well_known_port(9200,processes),200)
        self.assertEqual(inv.match_well_known_port(9300,processes),200)  # same keyword filter

    def test_match_well_known_port_no_match(self):
        processes=[{'pid':1,'name':'sshd','kind':'SSH','command':'sshd'}]
        self.assertIsNone(inv.match_well_known_port(3306,processes))
        self.assertIsNone(inv.match_well_known_port(9999,processes))  # not well-known

    def test_inspect_asset_collects_app_listening_ports(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'passwordless',''
            def run(self, command, timeout=60):
                if command==inv.PS:
                    return 0,('10 1 root 60 Tue Sep 1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx\n'
                              '384 1 app 60 Tue Sep 1 10:00:00 2026 java 0.0 0.0 0 0 /opt/java -Dappid=fosun_cashier --server.port=7084')
                if command==inv.PORTS_COMMAND:
                    return 0,('tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=10,fd=6))\n'
                              'tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:((\"nginx\",pid=10,fd=7))\n'
                              'tcp LISTEN 0 50 [::]:7084 [::]:* users:((\"java\",pid=384,fd=42))')
                return 0,''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['appPorts'][0],{'app':'Nginx','kind':'Nginx','port':80,'addresses':['0.0.0.0'],'pids':[10]})
        self.assertEqual(row['appPorts'][1],{'app':'Nginx','kind':'Nginx','port':8080,'addresses':['0.0.0.0'],'pids':[10]})
        cashier=row['appPorts'][2]
        self.assertEqual(cashier['app'],'fosun_cashier');self.assertEqual(cashier['port'],7084);self.assertEqual(cashier['addresses'],['::'])
        procs={p['pid']:p for p in row['processes']}
        self.assertEqual(procs[10]['ports'],[80,8080])
        self.assertEqual(procs[384]['ports'],[7084])

    def test_inspect_asset_warns_when_not_elevated(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'denied','sudo 无权限'
            def elevate_su(self, pw):return 'denied','su 无权限'
            def run(self, command, timeout=60):
                if command==inv.PS:
                    return 0,'10 1 root 60 Tue Sep 1 10:00:00 2026 nginx 0.0 0.0 0 0 nginx: master process /usr/sbin/nginx'
                if command==inv.PORTS_COMMAND:
                    return 0,'tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=10,fd=6))'
                return 0,''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        self.assertEqual(row['appPorts'][0]['app'],'Nginx')
        self.assertTrue(any('应用监听端口' in w for w in row['warnings']))

    def test_inspect_asset_captures_ports_without_sudo(self):
        # Simulates the 10.58.1.221 scenario: sudo denied, ps sees all
        # processes, but ss -ltnp hides PIDs for other users' sockets.
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self, password=None):return 'denied','sudo 无权限'
            def elevate_su(self, pw):return 'denied','su 无权限'
            def run(self, command, timeout=60):
                if command==inv.PS:
                    return 0,('1693 1 root 60 Tue Sep 1 10:00:00 2026 mysqld 0.0 0 0 0 /usr/sbin/mysqld --daemonize\n'
                              '1544 1 root 60 Tue Sep 1 10:00:00 2026 java 0.0 0.0 0 0 /bin/java -Xms8g -Xmx8g')
                if command==inv.PORTS_COMMAND:
                    # ss shows ports but Process column is empty (no pid=)
                    return 0,('State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
                              'LISTEN 0 128 0.0.0.0:3306 0.0.0.0:* \n'
                              'LISTEN 0 128 0.0.0.0:9200 0.0.0.0:* \n'
                              'LISTEN 0 50 [::]:9300 [::]:* \n')
                return 0,''
            def close(self):pass
        with patch.object(inv.uuid,'uuid4') as uid:
            uid.return_value.hex='abc'
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password','','',{},Terminal)
        # appPorts should NOT be empty — ports are captured even without PIDs
        self.assertTrue(row['appPorts'])
        apps={a['port']:a for a in row['appPorts']}
        self.assertIn(3306,apps);self.assertIn(9200,apps);self.assertIn(9300,apps)
        # Port 3306 matched to mysqld via well-known port inference (level 3)
        self.assertEqual(apps[3306]['pids'],[1693])
        # Ports 9200/9300 not matched (java cmd lacks 'elasticsearch' keyword)
        self.assertEqual(apps[9200]['app'],'未知进程')
        self.assertEqual(apps[9200]['pids'],[])
        # Warning about unmatched ports should be present
        self.assertTrue(any('无法关联到进程' in w for w in row['warnings']))

    def test_resolve_elevation_mapped_sudo_i_uses_saved_method(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'password',''
            def elevate_su(self, pw): self.calls.append(('su',pw)); return 'denied',''
        ip_map={'10.0.0.1':{'method':'sudo-i','password':'folidev_pw','status':'normal'}}
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.1','folidev_pw','root1','root2',ip_map)
        self.assertEqual(status,'password'); self.assertEqual(method,'sudo-i')
        self.assertEqual(pw,'folidev_pw'); self.assertFalse(updated)
        self.assertEqual(t.calls,[('elevate','folidev_pw')])

    def test_resolve_elevation_mapped_su_uses_saved_method(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'denied',''
            def elevate_su(self, pw): self.calls.append(('su',pw)); return 'password',''
        ip_map={'10.0.0.2':{'method':'su','password':'root1','status':'normal'}}
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.2','folidev_pw','root1','root2',ip_map)
        self.assertEqual(status,'password'); self.assertEqual(method,'su')
        self.assertEqual(pw,'root1'); self.assertFalse(updated)
        self.assertEqual(t.calls,[('su','root1')])

    def test_resolve_elevation_password_error_skips_probing(self):
        class T:
            def elevate(self, password=None): raise AssertionError('should not be called')
            def elevate_su(self, pw): raise AssertionError('should not be called')
        ip_map={'10.0.0.3':{'method':None,'password':None,'status':'password_error'}}
        status,reason,method,pw,updated=inv.resolve_elevation(T(),'10.0.0.3','folidev_pw','root1','root2',ip_map)
        self.assertEqual(status,'password_error'); self.assertIsNone(method)
        self.assertIsNone(pw); self.assertFalse(updated)

    def test_resolve_elevation_unmapped_probes_all_three(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'denied',''
            def elevate_su(self, pw): self.calls.append(('su',pw)); return 'denied',''
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.4','folidev_pw','root1','root2',{})
        self.assertEqual(status,'password_error'); self.assertIsNone(method)
        self.assertIsNone(pw); self.assertTrue(updated)
        # sudo-i with folidev pw, then su with root1, then su with root2.
        self.assertEqual(t.calls,[('elevate','folidev_pw'),('su','root1'),('su','root2')])

    def test_resolve_elevation_unmapped_succeeds_on_su_alt2(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'password_error','sudo wrong'
            def elevate_su(self, pw):
                self.calls.append(('su',pw))
                if pw=='root2': return 'password',''
                return 'password_error','su wrong'
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.5','folidev_pw','root1','root2',{})
        self.assertEqual(status,'password'); self.assertEqual(method,'su')
        self.assertEqual(pw,'root2'); self.assertTrue(updated)

    def test_resolve_elevation_mapped_fails_then_probes(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'password_error','sudo wrong'
            def elevate_su(self, pw): self.calls.append(('su',pw)); return 'password',''
        ip_map={'10.0.0.6':{'method':'sudo-i','password':'old_pw','status':'normal'}}
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.6','folidev_pw','root1','root2',ip_map)
        self.assertEqual(status,'password'); self.assertEqual(method,'su')
        self.assertEqual(pw,'root1'); self.assertTrue(updated)
        # First tried mapped sudo-i (old_pw), then probed: sudo-i (folidev), su (root1).
        self.assertEqual(t.calls,[('elevate','old_pw'),('elevate','folidev_pw'),('su','root1')])

    def test_resolve_elevation_no_alt_passwords_skips_su(self):
        class T:
            def __init__(self): self.calls=[]
            def elevate(self, password=None): self.calls.append(('elevate',password)); return 'password_error',''
            def elevate_su(self, pw): raise AssertionError('should not be called')
        t=T()
        status,reason,method,pw,updated=inv.resolve_elevation(t,'10.0.0.7','folidev_pw','','',{})
        self.assertEqual(status,'password_error'); self.assertTrue(updated)
        self.assertEqual(t.calls,[('elevate','folidev_pw')])


if __name__=='__main__':unittest.main()
