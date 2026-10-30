import base64
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import application_files as f
import application_log_policy as log_policy
from test_application_check import process, app, socket
import application_check as c
from application_config import zookeeper as zk


class FileTests(unittest.TestCase):
    def test_jar_snapshot_feeds_nacos_and_honours_ignorelist(self):
        enc = lambda value: base64.b64encode(value).decode()
        archive = b'/opt/orders/app.jar'
        config = b'nacos:\n  config:\n    server-addr: 192.0.2.10:8848\n    data-id: orders.yaml\n    group: DEFAULT_GROUP\n    namespace: test\n'
        body = ('JAR_CONFIG\t'+enc(archive)+'\t'+enc(b'BOOT-INF/classes/bootstrap.yml')+'\t'+enc(config)+'\n'
                'JAR_CONFIG\t'+enc(archive)+'\t'+enc(b'BOOT-INF/classes/mapper/ignored.xml')+'\t'+enc(b'<x/>')+'\nFILES_DONE')
        result = {'files':[], 'warnings':[]}
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=body), \
             patch.object(f.nacos, 'resolve_group', side_effect=lambda value: value), \
             patch.object(f.nacos, 'fetch', return_value=b'key: value') as fetch:
            snapshots = Path(folder)/'files'; snapshots.mkdir()
            f.snapshot_jar_configs(None, process(), result, snapshots)
            self.assertEqual(len(result['files']), 1)
            entry = result['files'][0]
            self.assertIn('app.jar!/BOOT-INF/classes/bootstrap.yml', entry['path'])
            self.assertEqual(f.read_snapshot(entry, snapshots, raw_config=True)['content'], config.decode())
            f.collect_nacos(None, process(), result, Path(folder)/'nacos', snapshot_folder=snapshots)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result['files'][-1]['source'], 'nacos')

    def test_jar_snapshot_rejects_unsafe_member_and_incomplete_response(self):
        enc = lambda value: base64.b64encode(value).decode()
        body = 'JAR_CONFIG\t'+enc(b'/app/a.jar')+'\t'+enc(b'../application.yml')+'\t'+enc(b'foo')+'\nFILES_DONE'
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=body):
            with self.assertRaisesRegex(ValueError, '无效 JAR'): f.snapshot_jar_configs(None, process(), {'files':[], 'warnings':[]}, folder)
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=''):
            with self.assertRaisesRegex(ValueError, '不完整'): f.snapshot_jar_configs(None, process(), {'files':[], 'warnings':[]}, folder)

    def test_zookeeper_discovery_uses_live_argv_and_exact_file_only(self):
        encode = lambda value: base64.b64encode(value).decode()
        path = '/etc/zookeeper/my zoo.cfg'
        args = b'java\0'+zk.MAIN_CLASS.encode()+b'\0'+path.encode()+b'\0'
        frames = 'ARGV\t'+encode(args)+'\nCWD\t'+encode(b'/opt/zookeeper')+'\nFILES_DONE'
        metadata = 'EXACT_FILE\t'+encode(path.encode())+'\t42\t100\nFILES_DONE'
        with patch.object(f, 'run_script', side_effect=[frames, metadata]) as run:
            result = f.catalogue(None, process(command='java '+zk.MAIN_CLASS+' /untrusted'), kind='config')
        self.assertEqual(result['processType'], 'ZOOKEEPER')
        self.assertEqual(result['files'][0]['path'], path)
        self.assertEqual(result['files'][0]['role'], 'primary')
        self.assertEqual(run.call_args.args[-2:], ('list-file', path))
        self.assertEqual(zk.discover(['java', zk.MAIN_CLASS, 'conf/zoo.cfg'], '/opt/zk')[0], '/opt/zk/conf/zoo.cfg')
        self.assertIsNone(zk.discover(['java', zk.MAIN_CLASS, 'conf/zoo.cfg'], '')[0])
        self.assertIsNone(zk.discover(['java', zk.MAIN_CLASS, '/etc/zoo.cfg', 'extra'], '/')[0])
        self.assertFalse(zk.is_server(process(command='java -jar app.jar --zookeeper=host:2181')))

    def test_zookeeper_unresolved_and_ignored_do_not_scan_directories(self):
        encode = lambda value: base64.b64encode(value).decode()
        args = b'java\0'+zk.MAIN_CLASS.encode()+b'\0relative.cfg\0'
        frames = 'ARGV\t'+encode(args)+'\nCWD\t\nFILES_DONE'
        with patch.object(f, 'run_script', return_value=frames) as run:
            result = f.catalogue(None, process(command='java '+zk.MAIN_CLASS+' relative.cfg'), kind='config')
        self.assertEqual(run.call_count, 1)
        self.assertIn('工作目录不可用', result['warnings'][0])
        self.assertEqual(result['files'], [])
        with patch.object(f, 'run_script', return_value=frames.replace('CWD\t\n', 'CWD\t'+encode(b'/etc')+'\n')) as run, patch.object(f.config_ignore, 'is_ignored', return_value=True):
            result = f.catalogue(None, process(command='java '+zk.MAIN_CLASS+' relative.cfg'), kind='config')
        self.assertEqual(run.call_count, 1)
        self.assertIn('ignorelist', result['warnings'][0])

    def test_zookeeper_does_not_use_business_endpoint_parser(self):
        result = c.collect_config(None, process(command='java '+zk.MAIN_CLASS+' /etc/zoo.cfg'))
        self.assertEqual(result['status'], 'not_collected')
        self.assertEqual(result['endpoints'], [])

    def test_snapshot_reads_bounded_batches_and_marks_unreadable_files(self):
        encode = lambda b: base64.b64encode(b).decode()
        entries = [{'id':'a'*32, 'kind':'config', 'path':'/app/config/application.yml', 'root':'/app/config'},
                   {'id':'b'*32, 'kind':'config', 'path':'/app/config/secret.yml', 'root':'/app/config'}]
        body = 'SNAPSHOT\t'+entries[0]['id']+'\t'+encode(b'password: secret\n')+'\nSNAPSHOT_ERROR\t'+entries[1]['id']+'\nFILES_DONE'
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=body):
            f.snapshot_configs(None, process(), {'files':entries}, folder)
            self.assertEqual((Path(folder)/entries[0]['id']).stat().st_mode & 0o777, 0o600)
            self.assertIn('secret', f.read_snapshot(entries[0], folder)['content'])
            self.assertNotIn('secret', f.read_snapshot(entries[0], folder)['redactedContent'])
            self.assertIn('secret', f.read_snapshot(entries[0], folder, raw_config=True)['content'])
            with self.assertRaisesRegex(ValueError, '不可读'): f.read_snapshot(entries[1], folder)
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value='SNAPSHOT_ERROR\t'+'f'*32+'\nFILES_DONE'):
            with self.assertRaisesRegex(ValueError, '无效配置快照'):
                f.snapshot_configs(None, process(), {'files':entries}, folder)

    def test_snapshot_marks_truncated_content_and_requires_complete_frame(self):
        entry = {'id':'c'*32, 'kind':'config', 'path':'/app/config/a.yml', 'root':'/app/config'}
        body = 'SNAPSHOT\t'+entry['id']+'\t'+base64.b64encode(b'x'*262145).decode()+'\nFILES_DONE'
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=body):
            f.snapshot_configs(None, process(), {'files':[entry]}, folder)
            self.assertTrue(f.read_snapshot(entry, folder)['truncated'])
            self.assertEqual((Path(folder)/entry['id']).stat().st_size, 262145)
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value='FILES_DONE'):
            with self.assertRaisesRegex(ValueError, '不完整'):
                f.snapshot_configs(None, process(), {'files':[entry]}, folder)

    def test_snapshot_rejects_oversized_files_without_discarding_other_files(self):
        large = {'id':'d'*32, 'kind':'config', 'path':'/app/config/large.yml', 'root':'/app/config',
                 'size':f.MAX_CONFIG_SNAPSHOT_FILE+1}
        small = {'id':'e'*32, 'kind':'config', 'path':'/app/config/small.yml', 'root':'/app/config', 'size':10}
        body = 'SNAPSHOT\t'+small['id']+'\t'+base64.b64encode(b'small').decode()+'\nFILES_DONE'
        with tempfile.TemporaryDirectory() as folder, patch.object(f, 'run_script', return_value=body) as run:
            f.snapshot_configs(None, process(), {'files':[large, small]}, folder)
            self.assertIn('超过 2 MiB', large['snapshotError'])
            self.assertEqual(f.read_snapshot(small, folder)['content'], 'small')
            self.assertNotIn(large['path'], run.call_args.args)

    def test_snapshot_honours_updated_ignore_rules(self):
        entry = {'id':'a'*32, 'kind':'config', 'path':'/app/config/private.yml', 'snapshotName':'a'*32}
        with tempfile.TemporaryDirectory() as folder, patch.object(f.config_ignore, 'is_ignored', return_value=True):
            with self.assertRaisesRegex(ValueError, 'ignorelist'):
                f.read_snapshot(entry, folder)

    def test_catalogue_follows_server_xml_docbase(self):
        encode = lambda value: base64.b64encode(value.encode()).decode()
        base = '/opt/apache-tomcat_dolphin-webhook'
        conf, webinf = base+'/conf', '/var/www/webapps/dolphin-webhook/WEB-INF'
        server = conf+'/server.xml'
        first = 'ROOT\tconfig\t'+encode(conf)+'\nFILE\tconfig\t'+encode(server)+'\t100\t100\t'+encode(conf)+'\nFILES_DONE'
        second = 'ROOT\tconfig\t'+encode(webinf)+'\nFILE\tconfig\t'+encode(webinf+'/classes/db.properties')+'\t10\t100\t'+encode(webinf)+'\nFILES_DONE'
        xml = '<Server><Service><Engine><Host><Context docBase="/var/www/webapps/dolphin-webhook"/></Host></Engine></Service></Server>'
        with patch.object(f, 'run_script', side_effect=[first, second]), patch.object(f, 'read_file', return_value={'content': xml}):
            result = f.catalogue(None, process(command='java -Dcatalina.base='+base), kind='config')
        self.assertEqual({entry['path'] for entry in result['files']}, {server, webinf+'/classes/db.properties'})

    def test_config_catalogue_does_not_list_or_read_logs(self):
        with patch.object(f, 'run_script', return_value='FILES_DONE') as run:
            result = f.catalogue(None, process(), kind='config')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[5], 'config')
        self.assertEqual(result['roots']['log'], [])
        self.assertFalse(any('日志目录' in warning for warning in result['warnings']))

    def test_log_catalogue_keeps_logback_scopes_without_returning_configs(self):
        encode = lambda value: base64.b64encode(value.encode()).decode()
        config_root, log_root = '/var/www/webapps/orders/WEB-INF', '/var/log/shared/orders'
        config = ('ROOT\tconfig\t'+encode(config_root)+'\nFILE\tconfig\t'+encode(config_root+'/logback.xml')+
                  '\t100\t100\t'+encode(config_root)+'\nFILES_DONE')
        log = ('ROOT\tlog\t'+encode(log_root)+'\nFILE\tlog\t'+encode(log_root+'/orders.log')+
               '\t100\t100\t'+encode(log_root)+'\nFILES_DONE')
        xml = '<configuration><appender><file>'+log_root+'/orders.log</file></appender></configuration>'
        with patch.object(f, 'run_script', side_effect=[config, 'FILES_DONE', log]) as run, \
             patch.object(f, 'read_file', return_value={'content':xml}):
            result = f.catalogue(None, process(), kind='log')
        self.assertEqual(run.call_count, 3)
        self.assertEqual([entry['kind'] for entry in result['files']], ['log'])
        self.assertEqual(result['roots']['config'], [])
        self.assertEqual(result['logScopes'], [{'root':log_root, 'patterns':['orders.log']}])

    def test_log_groups_exclude_unrelated_files_and_flume_directories(self):
        for name in ('app.log', 'catalina.out', 'debug.txt', 'line\nfeed.log'):
            self.assertEqual(log_policy.group_for('/app/logs/'+name), 'current', name)
        for name in ('app.log.gz', 'app.log.tar.gz', 'catalina.out.gz', 'debug.txt.zip',
                     'app.log.1', 'app.log.2026-10-15.1.gz'):
            self.assertEqual(log_policy.group_for('/app/logs/'+name), 'history', name)
        for name in ('app.jar', 'README', 'backup.tar.gz', 'app.log.1.exe', 'app.xml'):
            self.assertIsNone(log_policy.group_for('/app/logs/'+name), name)
        self.assertIsNone(log_policy.group_for('/app/logs/apache-flume-1.6.0-bin/logs/flume.log'))
        self.assertIsNone(log_policy.group_for('/app/logs/orders.2026-10-15.1.gz'))
        self.assertEqual(log_policy.group_for('/app/logs/orders.2026-10-15.1.gz', scoped=True), 'history')

    def test_current_and_history_catalogue_limits_are_independent(self):
        b64 = lambda data: base64.b64encode(data).decode()
        root = '/var/www/webapps/orders/logs'
        records = ''.join(f'{i}.0\t8\t{root}/app-{i}.log.gz\0' for i in range(1001))
        records += f'1.0\t8\t{root}/current.log\0' + f'9999.0\t8\t{root}/invalid.jar\0'
        body = ('ROOT\tlog\t'+b64(root.encode())+'\nFILE_BATCH_BEGIN\tlog\t'+b64(root.encode())+
                '\n'+b64(records.encode())+'\nFILE_BATCH_END\nFILES_DONE')
        with patch.object(f, 'run_script', side_effect=['FILES_DONE', body]):
            result = f.catalogue(None, process())
        self.assertEqual(sum(e.get('logGroup') == 'current' for e in result['files']), 1)
        self.assertEqual(sum(e.get('logGroup') == 'history' for e in result['files']), 1000)
        self.assertTrue(any('历史日志' in warning for warning in result['warnings']))

    def test_old_catalogue_cannot_preview_archives_or_excluded_logs(self):
        for name in ('a.log.gz', 'a.log.tar.gz', 'a.jar', 'apache-flume-1.6.0-bin/flume.log'):
            with patch.object(f, 'run_script') as run:
                with self.assertRaises(ValueError):
                    f.read_file(None, process(), {'id':'x','kind':'log','path':'/app/logs/'+name,'root':'/app/logs'})
                run.assert_not_called()

    def test_roots_are_specific_to_process_and_application(self):
        roots = f.roots_for(process())
        self.assertIn('/var/www/webapps/orders/WEB-INF', roots['config'])
        self.assertEqual(roots['log'], ['/var/www/webapps/orders/logs'])
        self.assertNotIn('/var/www/webapps', roots['config'])
        roots = f.roots_for(process(command='java -Dcatalina.base=/opt/tomcat/orders'), {'appName':'orders'})
        self.assertIn('/opt/tomcat/orders/webapps/ROOT/WEB-INF', roots['config'])
        self.assertIn('/var/www/webapps/orders/logs', roots['log'])
        self.assertEqual(f.roots_for(process(command='java -jar x.jar',))['log'], ['/var/www/webapps/orders/logs'])

    def test_environment_inference_preserves_ambiguity(self):
        p = process(); listeners = [socket(state='LISTEN')]
        self.assertEqual(c.infer_environment(p, {'10.0.0.1'}, listeners, [app('1','orders')]), 'TEST')
        apps = [app('1','orders'), app('1','orders',env='PRODUCT')]
        self.assertIsNone(c.infer_environment(p, {'10.0.0.1'}, listeners, apps))
        self.assertEqual(c.match_application(p, {'10.0.0.1'}, listeners, apps, None)['confidence'], 'UNKNOWN')

    def test_config_redacts_properties_xml_urls_yaml_and_keys(self):
        text = '''db.password=secret-one
redis:
  password: secret-two
<property name="password" value="secret-three"/>
<property name="token"><value>secret-four</value></property>
<password>secret-five</password>
<user username="tomcat" password="secret-nine" roles="manager"/>
url=jdbc:mysql://name:secret-six@db:3306/orders?password=secret-seven&x=1
-----BEGIN RSA PRIVATE KEY-----
secret-eight
-----END RSA PRIVATE KEY-----
spring.application.name=orders
'''
        result = f.redact_config(text)
        self.assertNotIn('secret-', result)
        self.assertIn('spring.application.name=orders', result)
        self.assertIn('db:3306', result)

    def test_config_preview_reveals_passwords_but_masks_other_secrets(self):
        text = ('db.password=pass-one\nredis:\n  passwd: pass-two\n'
                '<property name="password" value="pass-three"/>\n<password>pass-four</password>\n'
                '<user password="pass-five"/>\nurl=jdbc:mysql://name:pass-six@db:3306/db?password=pass-seven&token=token-one\n'
                'api.token=token-two\n-----BEGIN PRIVATE KEY-----\nprivate-key\n-----END PRIVATE KEY-----\n')
        preview = f.redact_config(text, hide_password=False)
        for password in ('pass-one', 'pass-two', 'pass-three', 'pass-four', 'pass-five', 'pass-six', 'pass-seven'):
            self.assertIn(password, preview)
            self.assertNotIn(password, f.redact_config(text))
        self.assertNotIn('token-one', preview)
        self.assertNotIn('token-two', preview)
        self.assertNotIn('private-key', preview)

    def test_catalogue_deduplicates_and_rejects_escaped_paths(self):
        b64 = lambda s: base64.b64encode(s.encode()).decode()
        root = '/var/www/webapps/orders/logs'
        body = 'ROOT\tlog\t'+b64(root)+'\nFILE\tlog\t'+b64(root+'/a.log')+'\t8\t100\t'+b64(root)+'\nFILES_DONE'
        with patch.object(f, 'run_script', side_effect=['FILES_DONE', body]):
            result = f.catalogue(None, process())
        self.assertEqual(result['files'][0]['kind'], 'log')
        with patch.object(f, 'run_script', side_effect=['FILES_DONE', body.replace(b64(root+'/a.log'), b64('/etc/passwd'))]):
            with self.assertRaises(ValueError): f.catalogue(None, process())

    def test_batched_catalogue_supports_unusual_names_and_limits_newest(self):
        b64 = lambda data: base64.b64encode(data).decode()
        root='/var/www/webapps/orders/logs'
        names=['app with spaces.log','line\nfeed.log','tab\tfile.log']
        records=''.join('100.0\t8\t'+root+'/'+name+'\0' for name in names).encode()
        body='ROOT\tlog\t'+b64(root.encode())+'\nFILE_BATCH_BEGIN\tlog\t'+b64(root.encode())+'\n'+b64(records)+'\nFILE_BATCH_END\nFILES_DONE'
        with patch.object(f,'run_script',side_effect=['FILES_DONE',body]):
            result=f.catalogue(None,process())
        self.assertEqual({Path(x['path']).name for x in result['files']},set(names))
        records=''.join(f'{i}.0\t8\t{root}/{i}.log\0' for i in range(1001)).encode()
        body='ROOT\tlog\t'+b64(root.encode())+'\nFILE_BATCH_BEGIN\tlog\t'+b64(root.encode())+'\n'+b64(records)+'\nFILE_BATCH_END\nFILES_DONE'
        with patch.object(f,'run_script',side_effect=['FILES_DONE',body]):
            result=f.catalogue(None,process())
        self.assertEqual(len(result['files']),1000)
        self.assertEqual(result['files'][0]['modifiedAt'],1000)
        self.assertTrue(result['warnings'])


    def test_read_requires_complete_frame_and_redacts_content(self):
        entry = {'id':'x','kind':'config','path':'/a/b/c.xml','root':'/a/b'}
        body = 'CONTENT_BEGIN\n'+base64.b64encode(b'password=private').decode()+'\nCONTENT_END\nFILES_DONE'
        with patch.object(f,'run_script',return_value=body):
            result = f.read_file(None, process(), entry)
        self.assertNotIn('private', result['content'])
        with patch.object(f,'run_script',return_value=body.replace('FILES_DONE','')):
            with self.assertRaises(ValueError):f.read_file(None, process(), entry)

    def test_logback_shared_directory_scopes_are_not_broadened(self):
        xml='<configuration><property name="LOG_PATH" value="/var/log/sys"/><appender><file>${LOG_PATH}/orders.log</file><fileNamePattern>${LOG_PATH}/orders.%d{yyyy-MM-dd}.%i.gz</fileNamePattern></appender></configuration>'
        self.assertEqual(f.declared_log_scopes(xml,process()),[
            {'root':'/var/log/sys','patterns':['orders.log']},
            {'root':'/var/log/sys','patterns':['orders.*.*.gz']}])
        self.assertEqual(f.declared_log_scopes(xml.replace('${LOG_PATH}','${UNRESOLVED}'),process()),[])
        self.assertEqual(f.declared_log_scopes('<bad xml',process()),[])

    def test_chunk_transfer_checks_digest_and_cleans_remote(self):
        data = b'test archive'*100000
        remote = '/tmp/envscope-logs.abcdefghij/logs.tar.gz'
        response = '\t'.join(['ARCHIVE',remote,str(len(data)),hashlib.sha256(data).hexdigest(),'3','1'])
        class Terminal:
            def __init__(self): self.commands=[]; self.offset=0
            def run(self, command, **_):
                self.commands.append(command)
                if command.startswith('rm '): return 0, ''
                chunk = data[self.offset:self.offset+f.CHUNK];self.offset += len(chunk)
                return 0, base64.b64encode(chunk).decode()
        terminal = Terminal()
        with tempfile.TemporaryDirectory() as tmp, patch.object(f,'run_script',return_value=response):
            dest = Path(tmp)/'logs.part'
            result = f.archive_logs(terminal, process(), ['/a/b/logs'], dest)
            self.assertEqual(dest.read_bytes(),data)
            self.assertTrue(result['warnings'])
            self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        self.assertTrue(terminal.commands[-1].startswith('rm -rf -- /tmp/envscope-logs.'))
        terminal = Terminal()
        with tempfile.TemporaryDirectory() as tmp, patch.object(f,'run_script',return_value=response.replace(hashlib.sha256(data).hexdigest(),'0'*64)):
            dest = Path(tmp)/'logs.part'
            with self.assertRaises(ValueError): f.archive_logs(terminal, process(), ['/a/b/logs'], dest)
            self.assertFalse(dest.exists())


@unittest.skipUnless(platform.system() == 'Linux', 'GNU Linux file probes')
class LinuxFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.logs=self.root/'logs';self.logs.mkdir()
        self.config=self.root/'WEB-INF';self.config.mkdir()
        self.pid=os.getpid()
        self.ticks=Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19]
        self.process={'pid':self.pid,'startTicks':self.ticks}
        class Terminal:
            def run(self, command, timeout=60):
                r=subprocess.run(command,shell=True,capture_output=True,text=True,timeout=timeout)
                return r.returncode,r.stdout
        self.terminal=Terminal()

    @unittest.skipUnless(shutil.which('java') and shutil.which('javac') and shutil.which('jar'), 'JDK required')
    def test_live_jar_config_snapshot(self):
        source = self.root/'Main.java'
        source.write_text('public class Main { public static void main(String[] a) throws Exception { Thread.sleep(60000); } }')
        subprocess.run(['javac', str(source)], check=True, capture_output=True)
        config = self.root/'BOOT-INF/classes/bootstrap.yml'
        config.parent.mkdir(parents=True)
        config.write_text('nacos:\n  config:\n    data-id: orders.yaml\n')
        subprocess.run(['jar', 'cfe', 'app.jar', 'Main', 'Main.class', 'BOOT-INF'], cwd=self.root, check=True, capture_output=True)
        child = subprocess.Popen(['java', '-jar', 'app.jar'], cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (child.terminate(), child.wait()))
        ticks = Path(f'/proc/{child.pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]
        proc = {'pid':child.pid, 'startTicks':ticks, 'command':'java -jar app.jar'}
        folder = self.root/'snapshot'; folder.mkdir()
        result = {'files':[], 'warnings':[]}
        f.snapshot_jar_configs(self.terminal, proc, result, folder)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(f.read_snapshot(result['files'][0], folder, raw_config=True)['content'], config.read_text())

    def test_exact_config_probe_rejects_symlinks_and_snapshots_only_listed_file(self):
        path = self.root/'zoo config.cfg'
        path.write_text('clientPort=2181\n')
        alias = self.root/'alias.cfg'
        alias.symlink_to(path)
        body = f.run_script(self.terminal, 'application_files.sh', self.pid, self.ticks, 'list-file', str(path))
        self.assertIn('EXACT_FILE\t', body)
        denied = f.run_script(self.terminal, 'application_files.sh', self.pid, self.ticks, 'list-file', str(alias))
        self.assertIn('WARNING\t', denied)
        entry = {'id':'a'*32, 'kind':'config', 'path':str(path), 'root':str(self.root), 'size':path.stat().st_size}
        with tempfile.TemporaryDirectory() as folder:
            f.snapshot_configs(self.terminal, self.process, {'files':[entry]}, folder)
            self.assertEqual(f.read_snapshot(entry, folder)['content'], 'clientPort=2181\n')
            path.unlink()
            path.symlink_to('/etc/passwd')
            entry.pop('snapshotName')
            f.snapshot_configs(self.terminal, self.process, {'files':[entry]}, Path(folder)/'again')
            self.assertIn('不可读或已变化', entry['snapshotError'])

    def test_list_preview_archive_and_symlink_boundaries(self):
        log=self.logs/'app with spaces.log';log.write_text(''.join(f'line {i}\n' for i in range(250)))
        old=self.logs/'old.log';old.write_text('old');os.utime(old,(time.time()-31*86400,)*2)
        (self.logs/'escape.log').symlink_to('/etc/passwd')
        (self.config/'spring-application.xml').write_text('<password>private</password>')
        with patch.object(f,'roots_for',return_value={'config':[str(self.config)],'log':[str(self.logs)]}):
            catalog=f.catalogue(self.terminal,self.process)
        self.assertEqual(len(catalog['files']),3)
        entry=next(e for e in catalog['files'] if e['path']==str(log))
        preview=f.read_file(self.terminal,self.process,entry)
        self.assertEqual(preview['content'].splitlines()[0],'line 150')
        self.assertEqual(len(preview['content'].splitlines()),100)
        result=f.archive_logs(self.terminal,self.process,catalog['roots']['log'],self.root/'download.tar.gz')
        self.assertEqual(result['fileCount'],1)
        with tarfile.open(self.root/'download.tar.gz') as archive:
            self.assertEqual(len(archive.getnames()),1)
            self.assertTrue(archive.getnames()[0].endswith('app with spaces.log'))
            self.assertFalse(archive.getnames()[0].startswith('/'))
            self.assertEqual(archive.extractfile(archive.getmembers()[0]).read(),log.read_bytes())
        log.unlink();log.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):f.read_file(self.terminal,self.process,entry)

    def test_shared_log_directory_download_excludes_other_applications(self):
        (self.logs/'orders.log').write_text('orders')
        (self.logs/'orders.2026-10-15.1.gz').write_bytes(b'compressed fixture')
        (self.logs/'other-app.log').write_text('private other app')
        scopes=[{'root':str(self.logs),'patterns':['orders.log','orders.*.*.gz','orders.log']}]
        result=f.archive_logs(self.terminal,self.process,scopes,self.root/'scoped.tar.gz')
        self.assertEqual(result['fileCount'],2)
        with tarfile.open(self.root/'scoped.tar.gz') as archive:
            self.assertTrue(all('other-app' not in name for name in archive.getnames()))
        body=f.run_script(self.terminal,'application_files.sh',self.pid,self.ticks,'list-match','log','orders.log',str(self.logs))
        self.assertIn('FILES_DONE',body)

    def test_log_listing_and_history_archive_use_the_same_filters(self):
        current = ['app.log', 'catalina.out', 'debug.txt', 'empty.log', 'nested/worker.log']
        history = ['app.log.gz', 'app.log.tar.gz', 'catalina.out.1', 'debug.txt.2026-10-15.gz', 'old.log.gz']
        invalid = ['app.jar', 'app.xml', 'README', 'backup.tar.gz', 'app.log.1.exe',
                   'apache-flume-1.6.0-bin/logs/flume.log', 'apache-flume-1.6.0-bin/logs/flume.log.gz']
        for name in current+history+invalid:
            path = self.logs/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        (self.logs/'empty.log').write_text('')
        (self.logs/'binary.log').write_bytes(b'\x00binary\n')
        (self.logs/'fake-directory.log').mkdir()
        (self.logs/'symlink.log.gz').symlink_to(self.logs/'app.log.gz')
        old_time = time.time()-90*86400
        os.utime(self.logs/'old.log.gz', (old_time, old_time))
        with patch.object(f, 'roots_for', return_value={'config':[], 'log':[str(self.logs)]}):
            catalog = f.catalogue(self.terminal, self.process)
        by_group = lambda group: {str(Path(e['path']).relative_to(self.logs)) for e in catalog['files'] if e['logGroup'] == group}
        self.assertEqual(by_group('current'), set(current))
        self.assertEqual(by_group('history'), set(history))
        result = f.archive_logs(self.terminal, self.process, catalog['logScopes'], self.root/'history.tar.gz', group='history')
        self.assertEqual(result['fileCount'], len(history))
        self.assertEqual(result['cutoff'], 0)
        self.assertEqual(result['logGroup'], 'history')
        with tarfile.open(self.root/'history.tar.gz') as archive:
            names = {name.removeprefix(str(self.logs).lstrip('/')+'/') for name in archive.getnames()}
            self.assertEqual(names, set(history))
        # The legacy recent-log bundle also filters out junk and Flume contents.
        f.archive_logs(self.terminal, self.process, catalog['logScopes'], self.root/'recent.tar.gz')
        with tarfile.open(self.root/'recent.tar.gz') as archive:
            names = {name.removeprefix(str(self.logs).lstrip('/')+'/') for name in archive.getnames()}
            self.assertEqual(names, set(current+history)-{'old.log.gz'})

    def test_explicit_rolling_scope_under_application_root_is_preserved(self):
        (self.logs/'orders.2026-10-15.1.gz').write_bytes(b'fixture')
        (self.logs/'other.2026-10-15.1.gz').write_bytes(b'other')
        scopes = [{'root':str(self.logs), 'patterns':['*']},
                  {'root':str(self.logs), 'patterns':['orders.*.*.gz']}]
        result = f.archive_logs(self.terminal, self.process, scopes, self.root/'history.tar.gz', group='history')
        self.assertEqual(result['fileCount'], 1)
        with tarfile.open(self.root/'history.tar.gz') as archive:
            self.assertTrue(archive.getnames()[0].endswith('/orders.2026-10-15.1.gz'))

    def test_history_and_junk_do_not_evict_current_logs_from_remote_list(self):
        for i in range(1001):
            (self.logs/f'{i}.log.gz').touch()
            (self.logs/f'{i}.jar').touch()
        current = self.logs/'old-current.out'
        current.write_text('still active')
        os.utime(current, (1, 1))
        with patch.object(f, 'roots_for', return_value={'config':[], 'log':[str(self.logs)]}):
            catalog = f.catalogue(self.terminal, self.process)
        self.assertEqual([e['path'] for e in catalog['files'] if e['logGroup'] == 'current'], [str(current)])
        self.assertEqual(sum(e['logGroup'] == 'history' for e in catalog['files']), 1000)
        self.assertTrue(any('历史日志' in warning for warning in catalog['warnings']))

    def test_pid_reuse_empty_archive_and_config_root_symlink(self):
        with self.assertRaises(ValueError):f.archive_logs(self.terminal,self.process,[str(self.logs)],self.root/'empty.gz')
        p={**self.process,'startTicks':'0'}
        with self.assertRaises(ValueError):f.archive_logs(self.terminal,p,[str(self.logs)],self.root/'bad.gz')
        alias=self.root/'ROOT';alias.symlink_to(self.config, target_is_directory=True)
        (self.config/'spring.xml').write_text('x=1')
        with patch.object(f,'roots_for',return_value={'config':[str(alias)],'log':[]}):
            catalog=f.catalogue(self.terminal,self.process)
        self.assertEqual(catalog['roots']['config'],[str(self.config)])
