import base64
import json
import re
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
        body=''' 1 0 root 40000000 Tue Sep  1 10:00:00 2026 systemd /sbin/init
 2 0 root 40000000 Tue Sep  1 10:00:00 2026 kthreadd [kthreadd]
 11 1 root 31536000 Tue Sep  1 10:00:00 2026 java /opt/java -Dpassword=secret org.apache.catalina.Main
 12 1 app 60 Tue Sep  1 10:00:00 2026 custom /opt/custom --token tokenvalue
 invalid
'''
        rows,excluded,malformed=inv.parse_processes(body)
        self.assertEqual((len(rows),excluded,malformed),(2,2,1))
        self.assertEqual(rows[0]['kind'],'Tomcat')
        self.assertEqual(rows[0]['elapsedSeconds'],31536000)
        self.assertEqual(rows[0]['startedAt'],'2026-09-01T10:00:00+00:00')
        self.assertEqual(rows[1]['kind'],'未分类')
        self.assertNotIn('secret',json.dumps(rows));self.assertNotIn('tokenvalue',json.dumps(rows))

    def test_nginx_includes_ipv6_dynamic_static(self):
        routes,warnings=inv.parse_nginx(DUMP)
        self.assertFalse(warnings)
        route=next(r for r in routes if r['uri']=='/api')
        self.assertEqual(route['domains'],['example.com','*.example.com'])
        self.assertEqual([(b['host'],b['port']) for b in route['backends']],[('10.0.0.1','8080'),('::1','9090')])
        self.assertEqual(next(r for r in routes if r['uri']=='/dynamic')['backends'][0]['resolution'],'dynamic')
        self.assertEqual(next(r for r in routes if r['uri'].startswith('~'))['backends'][0]['port'],'443')
        self.assertEqual(next(r for r in routes if r['uri']=='/assets')['backends'],[])

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

    def test_login_success_separate_from_collection_failure(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self):return 'passwordless',''
            def run(self,*a):raise TimeoutError('sensitive output')
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
        self.assertEqual(row['loginStatus'],'can_login')
        self.assertEqual(row['processStatus'],'failed')
        self.assertNotIn('sensitive',json.dumps(row))

    def test_fallback_account_and_partial_visibility(self):
        class Terminal:
            def __init__(self,a,u,*rest): self.user=u['username']
            def login(self):
                if self.user=='folidev':raise RuntimeError('secret')
            def elevate(self):return 'denied','sudo 无权限'
            def run(self,*a):return 0,'10 1 app 60 Tue Sep  1 10:00:00 2026 custom /opt/custom'
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':u} for u in ['other','folidev']],'cookie','password',Terminal)
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

    def test_sudo_login_shell_passwordless_correct_and_wrong(self):
        for mode in ('passwordless','password','password_error'):
            terminal=object.__new__(inv.Terminal)
            terminal.password='private-password';terminal.account={'username':'folidev'}
            sent=[];terminal.send=sent.append
            responses=iter([(0,'1000'),(0,'0')]);terminal.run=lambda *a,**k:next(responses)
            chunks=['SUDOabc_BEGIN\n']
            if mode!='passwordless': chunks+=['SUDOabc_PASSWORD']
            chunks+=['Sorry, try again.\n'] if mode=='password_error' else ['root@host:~# ']
            chunks=iter(chunks);terminal.recv=lambda:next(chunks)
            with patch.object(inv.uuid,'uuid4') as uid:
                uid.return_value.hex='abc'
                status,_=terminal.elevate()
            self.assertEqual(status,mode)
            self.assertIn(' -i;',sent[0]);self.assertNotIn('private-password',sent[0])
            self.assertEqual(sent.count('private-password\r'),0 if mode=='passwordless' else 1)
            if mode=='password_error':self.assertEqual(sent[-1],'\x03')

    def test_wrong_sudo_password_falls_back_to_user_configs(self):
        main_conf = b'http { include /etc/nginx/conf.d/*.conf; }'
        app_conf = b'server { listen 80; server_name app.test; location / { proxy_pass http://10.0.0.1:8080; } }'
        contents={'/etc/nginx/nginx.conf':main_conf,'/etc/nginx/conf.d/app.conf':app_conf}
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self):return 'password_error','sudo 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx nginx: master process /usr/sbin/nginx'
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
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
        self.assertEqual(row['loginStatus'],'can_login')
        self.assertEqual(row['nginxStatus'],'partial')
        self.assertEqual(len(row['nginxConfigurations']),2)
        self.assertEqual({f['path'] for f in row['nginxConfigurations']},{'/etc/nginx/nginx.conf','/etc/nginx/conf.d/app.conf'})
        self.assertTrue(any(r['domains']==['app.test'] for r in row['nginxRoutes']))
        self.assertTrue(any('未获得 sudo 权限' in w for w in row['warnings']))

    def test_wrong_sudo_password_no_nginx_process_stays_not_running(self):
        class Terminal:
            def __init__(self,*a):pass
            def login(self):pass
            def elevate(self):return 'password_error','sudo 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '11 1 app 60 Tue Sep  1 10:00:00 2026 java /opt/java'
                return 0, ''
            def close(self):pass
        row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
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
            def elevate(self):return 'password_error','sudo 密码错误'
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx nginx: master process /usr/sbin/nginx'
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
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
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
            def elevate(self):return 'passwordless',''
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx nginx: master process /usr/sbin/nginx'
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
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
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
            def elevate(self):return 'passwordless',''
            def run(self, command, timeout=60):
                if command == inv.PS:
                    return 0, '10 1 root 60 Tue Sep  1 10:00:00 2026 nginx nginx: master process /usr/sbin/nginx'
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
            row=inv.inspect_asset({'id':'a'},[{'protocol':'ssh','username':'folidev'}],'cookie','password',Terminal)
        self.assertEqual(row['nginxStatus'],'partial')
        self.assertTrue(any('-T 读取失败' in w for w in row['warnings']))
        paths=sorted(f['path'] for f in row['nginxConfigurations'])
        self.assertEqual(paths,['/etc/nginx/conf.d/a.conf','/etc/nginx/conf.d/b.conf','/etc/nginx/nginx.conf'])
        domains=[d for r in row['nginxRoutes'] for d in r['domains']]
        self.assertIn('a.test',domains); self.assertIn('b.test',domains)

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

    def test_empty_accounts(self):
        row=inv.inspect_asset({'id':'a'},[],'cookie','password')
        self.assertEqual(row['loginStatus'],'cannot_login');self.assertTrue(row['reason'])

if __name__=='__main__':unittest.main()
