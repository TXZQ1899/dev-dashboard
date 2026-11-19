import importlib.util
import ast
import json
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
import zipfile
import datetime as dt
import io
import tarfile
import socket
import struct
import threading
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / 'environment-web' / 'runtime'))
sys.modules.setdefault('application_files', types.SimpleNamespace(redact_config=lambda text: text))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


scanner = module('scale_snapshot', 'snapshot_scan.py')
runner = module('scale_run', 'run.py')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class InspectionTest(unittest.TestCase):
    def test_endpoint_matching_and_missing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save(root / 'current.json', {'id': 'v1'})
            version = root / 'versions' / 'v1'
            save(version / 'devops.json', {'apps': [{'id': '1', 'name': 'demo', 'envs': {'PRODUCT': [{'ip': '10.0.0.1', 'port': '8761'}]}}]})
            routes = [{'uri': '/x', 'backends': [{'host': '10.0.0.1', 'port': '8761'}]}, {'uri': '/other', 'backends': [{'host': '10.0.0.1', 'port': '8091'}]}]
            save(version / 'jumpserver-snapshot.json', {'assets': [{'ip': '10.0.0.2', 'inspection': {'nginxStatus': 'partial', 'nginxRoutes': routes}}]})
            save(version / 'nginx-configs' / 'sample.json', {'assetId':'missing', 'files':[{'path':'/etc/nginx/nginx.conf','content':'http { server { server_name app.example;\nlocation /x { proxy_pass http://10.0.0.1:8761; }\n} }'}]})
            app = root / 'application-config' / 'demo-2'
            save(app / 'other' / 'manifest.json', {'appId': '2', 'name': 'caller', 'status': 'ready', 'files': [{'file': 'prod/resources/app.yml'}, {'file': 'prod/resources/missing'}]})
            config = app / 'prod' / 'resources' / 'app.yml'
            config.parent.mkdir(parents=True)
            config.write_text('one: 10.0.0.1:8761\ntwo: 10.0.0.1:87610\n', encoding='utf-8')
            extra = root / 'application-config' / 'orphan-3' / 'prod' / 'app.yml'
            extra.parent.mkdir(parents=True)
            extra.write_text('url: http://10.0.0.1:8761\n', encoding='utf-8')
            (root/'application-config'/'empty-4'/'prod').mkdir(parents=True)
            result = scanner.scan(root, '1', 'PRODUCT')
            self.assertEqual([row['uri'] for row in result['nginx']], ['/x'])
            self.assertEqual([row['line'] for row in result['references']], [1, 1])
            self.assertEqual(result['configErrors'], [])
            self.assertEqual(result['configFilesScanned'], 2)
            self.assertEqual(result['nginxFilesScanned'], 1)
            self.assertEqual(result['nginx'][0]['evidence'], '解析路由')

    def test_dns_cname_scope_and_ip_candidates(self):
        dns = module('scale_dns', 'dns_scan.py')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            static = root/'static.json'
            save(static, {'records':[{'name':'example.com','type':'CNAME','value':'backend.example.com','status':'启用'},{'name':'backend.example.com','type':'A','value':'10.0.0.1','status':'启用'}]})
            save(root/'versions'/'v1'/'cloud-dns-snapshot.json', {'available':True,'complete':True,'zones':[{'tenantId':'other','visibility':'private','records':[{'name':'unrelated.example','type':'CNAME','value':'backend.example.com','status':'启用'}]}]})
            result = dns.scan(root,'v1',['example.com'],['10.0.0.1'],static)
            self.assertEqual({r['name'] for r in result['records']}, {'example.com','backend.example.com'})
            self.assertFalse(result['warnings'])

    def test_downloaded_nested_archive_is_analyzed_locally(self):
        from analyze_logs import analyze
        with tempfile.TemporaryDirectory() as temp:
            inner = io.BytesIO()
            content = b'10.0.0.1 - - [18/Nov/2026:10:02:00 +0800] "GET /orders?id=1 HTTP/1.1" 200 12\n2026-11-18 10:03:00.001 ERROR example\n'
            with tarfile.open(fileobj=inner, mode='w:gz') as tar:
                item = tarfile.TarInfo('access.log'); item.size = len(content); tar.addfile(item, io.BytesIO(content))
            output = Path(temp)/'logs.tar.gz'; data = inner.getvalue()
            with tarfile.open(output, mode='w:gz') as tar:
                item = tarfile.TarInfo('rotated.tar.gz'); item.size = len(data); tar.addfile(item, io.BytesIO(data))
            tz = dt.timezone(dt.timedelta(hours=8))
            result = analyze(output, dt.datetime(2026,11,17,tzinfo=tz), dt.datetime(2026,11,19,tzinfo=tz))
            self.assertEqual(result['apis'][0]['uri'], '/orders')
            self.assertEqual(result['apis'][0]['clients'], ['10.0.0.1'])
            self.assertEqual(result['hours'][0]['count'], 1)
            self.assertEqual(result['errors'][0]['count'], 1)

    def test_status_without_access_log_and_failed_analysis(self):
        state, note = runner.log_status(['archive'], 1, [], True, ['未找到目录'])
        self.assertEqual(state, 'COMPLETE')
        self.assertIn('无 ACCESS LOG', note)
        self.assertEqual(runner.log_status(['archive'], 1, ['读取失败'], False, [])[0], 'PARTIAL')
        self.assertEqual(runner.log_status([], 0, [], True, [])[0], 'UNKNOWN')

    def test_jvm_rss_and_xmx(self):
        tree = ast.parse((ROOT/'remote_probe.py').read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('memory_size','jvm_details')]
        import os, re
        env = {'os':os, 're':re}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<jvm>', 'exec'), env)
        info = env['jvm_details'](['java','-Xms512m','-Xmx1g','-Dappid=demo','-jar','demo.jar'], 'VmRSS:\t1048576 kB\n')
        self.assertEqual(info['rssMiB'], 1024)
        self.assertEqual(info['rssXmxPercent'], 100)
        self.assertEqual(info['jvmArgs'], ['-Xms512m','-Xmx1g'])
        self.assertIsNone(env['jvm_details'](['java','-jar','demo.jar'], 'VmRSS: 1024 kB\n')['rssXmxPercent'])
        self.assertIsNone(env['jvm_details'](['java','-Xmx1g'], '')['rssMiB'])

    def test_summary_counts_distinct_other_applications_and_proxies(self):
        snap = {'appId':'1','endpoints':[['10.0.0.5','80']],'references':[{'appId':'1'},{'appId':'2'},{'appId':'2'},{'appId':'3'}],
                'nginx':[{'evidence':'解析路由','serverIp':'10.0.0.1','matchedEndpoints':[['10.0.0.5','80']]}, {'evidence':'解析路由','serverIp':'10.0.0.1','matchedEndpoints':[['10.0.0.5','80']]}]}
        self.assertEqual(runner.short_status(['配置引用','COMPLETE',''],snap,None,None,{})[2], '被 2 个其他应用引用')
        self.assertIn('1 个 Nginx', runner.short_status(['Nginx','COMPLETE',''],snap,None,None,{})[2])
        self.assertEqual(runner.short_status(['当前进程 10.0.0.1','COMPLETE',''],snap,None,None,{'10.0.0.1':[{'pid':42}]})[2], '匹配进程 PID：42')

    def test_kong_rows_preserve_unresolved_associations(self):
        kong = {'matches':[{'upstream':{'name':'up'}, 'target':{'target':'10.0.0.1:80'}, 'services':[{'service':{'name':'svc'}, 'routes':[{'name':'route'}]}]}, {'upstream':{'name':'up2'},'target':{'target':'10.0.0.2:80'},'services':[]}]}
        rows = runner.kong_rows(kong)
        self.assertEqual(rows[1], ['svc','route','up','10.0.0.1:80'])
        self.assertEqual(rows[2], ['','','up2','10.0.0.2:80'])

    def test_config_environment_scope_and_url_redaction(self):
        config = module('scale_config', 'config_bridge.py')
        self.assertTrue(config.select_file('other/resources/application-prd.yml','PRODUCT',{'prd'}))
        self.assertFalse(config.select_file('other/resources/application-test.yml','PRODUCT',{'prd'}))
        self.assertTrue(config.select_file('other/resources/application.yml','PRODUCT',{'prd'}))
        self.assertFalse(config.select_file('uat/resources/application-uat.yml','SIMULATION',set()))
        self.assertTrue(config.select_file('uat/resources/application-uat.yml','SIMULATION',{'uat'}))
        self.assertEqual(config.clean_url('http://user:password@host:8080/a?token=secret'), 'http://host:8080/a')

    def test_config_scan_extracts_components_and_dubbo_xml(self):
        config = module('scale_config_scan', 'config_bridge.py')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); folder=root/'application-config'/'demo-7'
            save(folder/'other'/'manifest.json', {'appId':'7','name':'demo','snapshotVersion':'v1','files':[]})
            f=folder/'prod'/'application-prod.yml';f.parent.mkdir(parents=True)
            f.write_text('eureka:\n  client:\n    service-url:\n      defaultZone: http://user:supersecret@10.0.0.9:8761/eureka/?token=private\n<dubbo:registry address="zookeeper://10.0.0.8:2181"/>\n',encoding='utf-8')
            result=config.scan(root,'7','PRODUCT',{'prod'})
            self.assertEqual({c['kind'] for c in result['components']},{'Eureka 注册中心','Dubbo 注册中心'})
            self.assertEqual(result['urls'][0]['url'],'http://10.0.0.9:8761/eureka/')
            self.assertEqual(next(c for c in result['components'] if c['kind']=='Dubbo 注册中心')['registryProtocol'],'zookeeper')
            self.assertNotIn('supersecret',json.dumps(result))
            self.assertNotIn('private',json.dumps(result))
            self.assertNotIn('content',result['files'][0])

    def test_dubbo_registry_preserves_consumer_ambiguity(self):
        dubbo = module('scale_dubbo','dubbo_bridge.py')
        from urllib.parse import quote
        class FakeZooKeeper:
            def __init__(self,*_): pass
            def close(self): pass
            def children(self,path):
                if path=='/dubbo': return ['com.example.Api']
                if path.endswith('/providers'): return [quote('dubbo://10.0.0.1:20880/com.example.Api?application=provider',safe='')]
                if path.endswith('/consumers'): return [quote('consumer://10.0.0.2/com.example.Api?application=consumer',safe='')]
                return []
        apps=[{'id':'2','name':'consumer','envs':{'PRODUCT':[{'ip':'10.0.0.2','port':'8080'}]}}]
        rows,warnings=dubbo.scan_registry('zk',2181,'provider',{('10.0.0.1','20880')},apps,'PRODUCT',client_factory=FakeZooKeeper)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['consumerAppId'],'2')
        self.assertIsNone(rows[0]['consumerPort'])
        self.assertEqual(rows[0]['consumerConfidence'],'INFERRED')
        self.assertEqual(warnings,[])
        duplicate = {'id':'3','name':'consumer','envs':{'PRODUCT':[{'ip':'10.0.0.2','port':'9090'}]}}
        ambiguous,_=dubbo.scan_registry('zk',2181,'provider',{('10.0.0.1','20880')},apps+[duplicate],'PRODUCT',client_factory=FakeZooKeeper)
        self.assertEqual(ambiguous[0]['consumerConfidence'],'AMBIGUOUS')
        self.assertEqual(ambiguous[0]['consumerAppId'],'')

    def test_zookeeper_read_only_wire_request(self):
        dubbo = module('scale_dubbo_wire','dubbo_bridge.py')
        client,server=socket.socketpair()
        def receive_frame(sock):
            length=struct.unpack('!i',dubbo.recv_exact(sock,4))[0]
            return dubbo.recv_exact(sock,length)
        def reply(sock,data): sock.sendall(struct.pack('!i',len(data))+data)
        def serve():
            try:
                receive_frame(server)
                reply(server,struct.pack('!iiqi',0,5000,42,16)+b'\0'*16+b'\0')
                request=receive_frame(server);xid,op=struct.unpack_from('!ii',request)
                if op!=12: return
                value=b'com.example.Api'
                reply(server,struct.pack('!iqii',xid,0,0,1)+struct.pack('!i',len(value))+value)
            finally: server.close()
        thread=threading.Thread(target=serve);thread.start()
        with patch.object(dubbo.socket,'create_connection',return_value=client):
            zk=dubbo.Zookeeper('unused',2181)
            self.assertEqual(zk.children('/dubbo'),['com.example.Api'])
            zk.close()
        thread.join(timeout=2); self.assertFalse(thread.is_alive())

    def test_tcp_aggregation_keeps_unresolved_separate(self):
        from dependencies import aggregate_tcp
        flow={'peer':{'ip':'10.0.0.2','port':80},'service':{'name':'HTTP'},'resolution':'UNKNOWN','remoteProcesses':[]}
        owner={'pid':3,'processName':'app','application':{'appId':'4','confidence':'EXACT'}}
        known={**flow,'remoteProcesses':[owner],'resolution':'EXACT'}
        rows=aggregate_tcp({'upstream':[flow,known],'downstream':[]})
        self.assertEqual(len(rows),2)
        self.assertEqual({r['evidence'] for r in rows},{'未解析','连接进程'})

    def test_component_urls_are_removed_without_conflating_other_lines(self):
        from dependencies import unlisted_urls
        component={'file':'a.yml','line':10,'host':'10.0.0.1','port':8761}
        urls=[{'url':'http://10.0.0.1:8761/eureka/','file':'a.yml','line':10},
              {'url':'http://10.0.0.1:8761/third-party','file':'a.yml','line':11},
              {'url':'https://api.example/pay','file':'b.yml','line':5}]
        self.assertEqual(unlisted_urls(urls,[component]),urls[1:])

    def test_tcp_display_hides_confidence_but_retains_candidates(self):
        from dependencies import tcp_display_rows
        owner={'pid':3,'processName':'app','confidence':'INFERRED','application':{'confidence':'AMBIGUOUS','candidates':[{'appId':'4','appName':'one','confidence':'INFERRED'},{'appId':'5','appName':'two','confidence':'INFERRED'}]}}
        report={'upstream':[{'peer':{'ip':'10.0.0.2','port':80},'remoteProcesses':[owner],'resolution':'AMBIGUOUS'}]}
        rows=tcp_display_rows(report)
        self.assertEqual(rows[0],['方向','对端IP','对端端口集合','对端进程 PID/名称','进程证据','应用ID/名称'])
        self.assertEqual(rows[1][-1][0]['状态'],'待确认')
        self.assertEqual(rows[1][-1][0]['候选'],[{'appId':'4','appName':'one'},{'appId':'5','appName':'two'}])
        self.assertNotIn('confidence',json.dumps(rows))

    def test_status_counts_unique_peer_ip_and_port(self):
        report={'status':'partial','warnings':['连接数量超过 100，已限制本次检查范围'],
                'upstream':[{'peer':{'ip':'10.0.0.1','port':80}},{'peer':{'ip':'10.0.0.1','port':80}},{'peer':{'ip':'10.0.0.1','port':443}}],
                'downstream':[{'peer':{'ip':'10.0.0.2','port':9000}},{'peer':{'ip':'10.0.0.2','port':9001}}]}
        result=runner.requested_status(['TCP 10.0.0.5','PARTIAL',''],None,None,{'10.0.0.5':report})
        self.assertEqual(result[1],'COMPLETE')
        self.assertIn('识别上游 2 个，下游 2 个',result[2])
        self.assertIn('结果不完整',result[2])

    def test_config_and_dubbo_summary_do_not_guess_roles(self):
        config={'files':[{}], 'components':[{'host':'10.0.0.1','port':2181,'confidence':'EXACT'}], 'urls':[], 'warnings':['生产配置原文未导出；仅保存脱敏后的端点、URL 和文件摘要'], 'dubboEvidence':[]}
        result=runner.requested_status(['应用配置与关联组件','PARTIAL',''],config,None,{})
        self.assertEqual(result,['应用配置与关联组件','COMPLETE','识别 1 个关联组件，0 个关联系统'])
        result=runner.requested_status(['Dubbo','UNKNOWN',''],config,{'rows':[],'warnings':['注册中心未知']},{})
        self.assertEqual(result,['Dubbo','COMPLETE','未识别 Dubbo 角色'])

    def test_xlsx_is_valid_xml_and_strings_are_not_formulas(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'report.xlsx'
            runner.workbook(output, {'状态': [['=1+1', 'a&b', 0]]})
            with zipfile.ZipFile(output) as archive:
                for name in archive.namelist():
                    ET.fromstring(archive.read(name))
                sheet = archive.read('xl/worksheets/sheet1.xml').decode()
                self.assertIn('=1+1', sheet)
                self.assertNotIn('<f>', sheet)
                self.assertIn('a&amp;b', sheet)
                self.assertIn('>0<', sheet)


if __name__ == '__main__':
    unittest.main()
