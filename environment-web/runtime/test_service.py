import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('service',Path(__file__).with_name('service.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)/'data';self.app=Path(self.tmp.name)/'app'
        (self.app/'lib').mkdir(parents=True)
        fixtures={'snapshot.json':{'apps':[{'id':'app'}]},'repositories.json':{'repos':[{'id':'repo'}]},
                  'jumpserver-snapshot.json':{'assets':[{'id':'server'}],'groups':[{'id':'group','assetIds':['server']}]},
                  'ecs-snapshot.json':{'instances':[{'id':'ecs'}]}}
        for name,body in fixtures.items():s.write(self.app/'lib'/name,body)
        for name,body in {'dns-snapshot.json':{'records':[]},'eip-snapshot.json':{'records':[]}}.items():s.write(self.app/'lib'/name,body)
        for name,value in [('APP',self.app),('DATA',self.data)]:
            p=patch.object(s,name,value);p.start();self.addCleanup(p.stop)
        self.service=s.Service()

    def test_folidev_password_preserved_private_and_not_trimmed(self):
        password = ' 密码 with spaces '
        self.service.save_settings({'folidevPassword':password})
        self.assertEqual(s.credentials()['folidev']['value'], password)
        self.assertTrue(self.service.status()['folidev']['configured'])
        self.assertNotIn(password, json.dumps(self.service.status(),ensure_ascii=False))
        self.service.save_settings({'folidevPassword':''})
        self.assertEqual(s.credentials()['folidev']['value'], password)
        self.assertEqual((self.data/'secrets/cookies.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): self.service.save_settings({'folidevPassword':'bad\npassword'})

    def test_cookie_alert_persists_until_corresponding_cookie_updated(self):
        self.service.save_settings({'cookies':{'aliyun':'secret=old','devops':'secret=other'}})
        folder=self.data/'versions'/'failure'
        stamp=s.credentials()['aliyun']['updatedAt']
        report={'message':'阿里云 Cookie 已失效，请更新','failures':[{'type':'cookie_expired','credential':'aliyun','credentialUpdatedAt':stamp,'message':'阿里云 Cookie 已失效，请更新'}]}
        s.write(folder/'collection-error.json',report)
        self.service.collection_error(folder)
        self.assertIn('已失效',self.service.status()['cookies']['aliyun']['alert']['message'])
        self.assertIsNotNone(s.Service().status()['cookies']['aliyun']['alert'])
        self.service.save_settings({'cookies':{'aliyun':'','devops':'secret=new-other'}})
        self.assertIsNotNone(self.service.status()['cookies']['aliyun']['alert'])
        self.service.save_settings({'cookies':{'aliyun':'secret=new'}})
        self.assertIsNone(self.service.status()['cookies']['aliyun']['alert'])
        self.assertNotIn('secret',json.dumps(self.service.status()))
        report['failures'][0]['credentialUpdatedAt']='older-credential'
        s.write(folder/'collection-error.json',report)
        self.service.collection_error(folder)
        self.assertIsNone(self.service.status()['cookies']['aliyun']['alert'])

    def test_nginx_original_files_are_version_scoped(self):
        identifier = self.service.status()['current']['id']
        folder = self.service.version(identifier)
        name = s.hashlib.sha256(b'server').hexdigest()+'.json'
        original = {'files':[{'path':'/etc/nginx/nginx.conf','content':'private config'}]}
        s.write(folder/'nginx-configs'/name, original)
        self.assertEqual(self.service.nginx_configs(identifier,'server'), original)
        self.assertNotIn('private config', json.dumps(self.service.status()))
        with self.assertRaises(ValueError):self.service.nginx_configs('../other','server')
        with self.assertRaises(ValueError):self.service.nginx_configs(identifier,'../server')

    def test_seed_and_restart_preserve_current(self):
        current=s.read(self.data/'current.json')
        self.assertEqual(len(self.service.status()['versions']),1)
        self.assertEqual(s.Service().status()['current'],current)

    def test_topology_generation_uses_current_version_and_persists_new_ids(self):
        current=self.service.status()['current']
        topology={'generatedAt':'2026-09-20T12:00:00+08:00','nodes':[{'id':'n'}],'edges':[{'id':'e'}],
                  'stats':{'nodeCount':1,'edgeCount':1,'ambiguousEdges':0,'unresolvedNodes':0}}
        def generate(args,**kwargs):
            root=Path(args[2]);s.write(root/'outputs/topology/topology.json',topology)
            result=s.subprocess.CompletedProcess(args,0)
            return result
        with patch.object(s.subprocess,'run',side_effect=generate):
            first=self.service.generate_topology()
            second=self.service.generate_topology()
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(second['sourceVersion'],current['id'])
        self.assertEqual(self.service.topology_status()['id'],second['id'])
        self.assertTrue(self.service.topology_file(second['id']).is_file())
        self.assertEqual(s.read(self.data/'topology/latest.json')['id'],second['id'])
        self.assertEqual(self.service.status()['topology']['id'],second['id'])
        with self.assertRaises(ValueError):self.service.topology_file('../escape')

    def topology_ready(self):
        topology={'generatedAt':'2026-09-20T12:00:00+08:00','nodes':[{'id':'n'}],'edges':[{'id':'e'}],
                  'stats':{'nodeCount':1,'edgeCount':1,'ambiguousEdges':0,'unresolvedNodes':0}}
        def generate(args,**kwargs):
            root=Path(args[2]);s.write(root/'outputs/topology/topology.json',topology)
            return s.subprocess.CompletedProcess(args,0)
        with patch.object(s.subprocess,'run',side_effect=generate):
            self.service.generate_topology()

    def test_path_query_uses_active_topology_and_forwards_options(self):
        self.topology_ready()
        payload={'query':{'kind':'domain','query':'api.example.com','to':['APPLICATION'],'environment':'TEST','maxDepth':6},
                 'resolution':{'status':'found','candidates':[],'matchedBy':['name']},'status':'resolved','paths':[],
                 'gaps':[],'reachableTypes':[]}
        calls=[];options=[]
        def run(command,**kwargs):
            calls.append(command);options.append(kwargs)
            return s.subprocess.CompletedProcess(command,0,json.dumps(payload),'')
        with patch.object(s.subprocess,'run',side_effect=run):
            result=self.service.path_query({'kind':['domain'],'q':[' api.example.com '],'to':['APPLICATION'],'env':['test'],'maxDepth':['6']})
        self.assertEqual(result['topology']['id'],self.service.topology_status()['id'])
        self.assertEqual(result['result'],payload)
        command=calls[0]
        self.assertEqual(command[:4],['node',str(self.app/'scripts'/'topology-path.mjs'),'domain','api.example.com'])
        self.assertIn('--json',command)
        self.assertEqual(command[command.index('--file')+1],str(self.service.topology_file(result['topology']['id'])))
        self.assertEqual(command[command.index('--to')+1],'APPLICATION')
        self.assertEqual(command[command.index('--env')+1],'TEST')
        self.assertEqual(command[command.index('--max-depth')+1],'6')
        self.assertEqual(command[command.index('--max-paths')+1],'50')
        self.assertEqual(options[0].get('cwd'),self.app)
        self.assertEqual(options[0].get('timeout'),60)

    def test_path_query_rejects_invalid_input_and_missing_topology(self):
        with self.assertRaisesRegex(ValueError,'尚未生成 Topology'):
            self.service.path_query({'kind':['domain'],'q':['api.example.com']})
        self.topology_ready()
        with patch.object(s.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'kind'): self.service.path_query({'kind':['cluster'],'q':['x']})
            with self.assertRaisesRegex(ValueError,'q 必须是'): self.service.path_query({'kind':['domain'],'q':['  ']})
            with self.assertRaisesRegex(ValueError,'节点类型'): self.service.path_query({'kind':['domain'],'q':['x'],'to':['NOPE']})
            with self.assertRaisesRegex(ValueError,'env'): self.service.path_query({'kind':['domain'],'q':['x'],'env':['STAGING']})
            with self.assertRaisesRegex(ValueError,'maxDepth'): self.service.path_query({'kind':['domain'],'q':['x'],'maxDepth':['99']})
            self.assertEqual(run.call_count,0)

    def test_path_query_surfaces_cli_failure_without_dumping_stdout(self):
        self.topology_ready()
        def run(command,**kwargs):
            return s.subprocess.CompletedProcess(command,1,'','boom: 路径查询失败')
        with patch.object(s.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'boom'):
                self.service.path_query({'kind':['host'],'q':['10.0.0.1']})

    def test_every_script_the_runtime_runs_is_shipped_into_the_image(self):
        """采集与路径查询都在容器内执行：service.py 调用的脚本不能被 .dockerignore 排除。"""
        root=Path(__file__).resolve().parent.parent
        source=(root/'runtime'/'service.py').read_text(encoding='utf-8')
        referenced=set(re.findall(r"APP/'scripts'?/?'([A-Za-z0-9._-]+\.mjs)'",source))
        self.assertTrue(referenced,'service.py 应当通过 subprocess 调用至少一个脚本')
        ignored={line.strip() for line in (root/'.dockerignore').read_text(encoding='utf-8').splitlines()
                 if line.strip() and not line.strip().startswith('#')}
        for name in sorted(referenced):
            self.assertIn(f'!scripts/{name}',ignored,f'scripts/{name} 被 .dockerignore 排除，容器内不存在')

    def test_failed_topology_generation_does_not_become_latest(self):
        with patch.object(s.subprocess,'run') as run:
            run.return_value.returncode=1
            with self.assertRaisesRegex(ValueError,'Topology 生成失败'):
                self.service.generate_topology()
        self.assertIsNone(self.service.topology_status())
        failed=[p for p in (self.data/'topology').glob('*/version.json') if s.read(p).get('status')=='failed']
        self.assertEqual(len(failed),1)

    def test_unified_cookie_migrates_and_updates_all_cloud_consumers(self):
        s.write(self.data/'secrets/cookies.json', {'codeup':{'value':'old=private','updatedAt':'yesterday'}})
        restored=s.Service()
        saved=s.read(self.data/'secrets/cookies.json')
        self.assertNotIn('codeup',saved)
        self.assertEqual(saved['aliyun']['value'],'old=private')
        restored.save_settings({'cookies':{'aliyun':'new=private'}})
        self.assertEqual(s.credentials()['aliyun']['value'],'new=private')
        self.assertNotIn('private',json.dumps(restored.status()))

    def test_legacy_versions_have_no_fabricated_clb(self):
        folder=self.service.version(self.service.status()['current']['id'])
        (folder/'clb-snapshot.json').unlink()
        self.assertIsNone(s.validate(folder)['clb'])
        self.assertFalse(s.read(folder/'clb-snapshot.json',s.EMPTY_CLB)['available'])

    def test_legacy_nat_and_invalid_rules(self):
        folder=self.service.version(self.service.status()['current']['id'])
        (folder/'nat-snapshot.json').unlink()
        self.assertIsNone(s.validate(folder)['nat'])
        s.write(folder/'nat-snapshot.json', {'available':True,'gateways':[{'id':'ngw','entries':[{'id':'fwd'}]}]})
        with self.assertRaises(ValueError): s.validate(folder)

    def test_nat_sync_preserves_other_snapshots_and_requires_fresh_nat(self):
        previous=self.service.version(self.service.status()['current']['id'])
        clb={'available':True,'collectedAt':'old','instances':[{'id':'lb'}]}
        s.write(previous/'clb-snapshot.json',clb)
        s.write(previous/'nat-snapshot.json',{'available':True,'gateways':[]})
        for success in (False,True):
            identifier='nat-success' if success else 'nat-missing'
            folder=self.data/'versions'/identifier
            s.write(folder/'version.json',{'id':identifier,'status':'collecting'})
            self.service.job={'id':identifier,'kind':'nat','status':'running'}
            def run(*args,**kwargs):
                self.assertEqual(s.read(folder/'clb-snapshot.json'),clb)
                self.assertFalse((folder/'nat-snapshot.json').exists())
                if success:s.write(folder/'nat-snapshot.json',{'available':True,'collectedAt':'new','gateways':[]})
                return type('Result',(),{'returncode':0})()
            with patch.object(s.subprocess,'run',side_effect=run),patch.object(self.service,'activate') as activate:
                self.service.work('nat',identifier)
                self.assertEqual(activate.called,success)
                self.assertEqual(self.service.job['status'],'succeeded' if success else 'failed')

    def test_clb_sync_defers_ready_status_until_backend_swapped(self):
        # Bug: Settings advertised CLB counts before the new backend could serve
        # them. version.json must stay 'collecting' while activate() rebuilds,
        # and only flip to 'ready' with counts after the swap succeeds.
        previous=self.service.version(self.service.status()['current']['id'])
        s.write(previous/'clb-snapshot.json',{'available':True,'collectedAt':'old','instances':[]})
        s.write(previous/'nat-snapshot.json',{'available':True,'gateways':[]})
        identifier='clb-defer'
        folder=self.data/'versions'/identifier
        s.write(folder/'version.json',{'id':identifier,'status':'collecting'})
        self.service.job={'id':identifier,'kind':'clb','status':'running'}
        def run(*args,**kwargs):
            s.write(folder/'clb-snapshot.json',{'available':True,'collectedAt':'new','instances':[{'id':'lb'}]})
            return type('Result',(),{'returncode':0})()
        captured={}
        def activate_snapshot(identifier):
            # At this point the new backend hasn't started; version must still
            # be collecting so the Settings version row doesn't leak counts.
            captured['status']=s.read(folder/'version.json').get('status')
            captured['counts']=s.read(folder/'version.json').get('counts')
        with patch.object(s.subprocess,'run',side_effect=run),patch.object(self.service,'activate',side_effect=activate_snapshot):
            self.service.work('clb',identifier)
        self.assertEqual(captured['status'],'collecting')
        self.assertIsNone(captured['counts'])
        report=s.read(folder/'version.json')
        self.assertEqual(report['status'],'ready')
        self.assertEqual(report['counts']['clb'],1)

    def test_cookie_not_returned_and_blank_preserves(self):
        self.service.save_settings({'cookies':{'devops':'Cookie: private=secret'}})
        self.service.save_settings({'cookies':{'devops':''}})
        self.assertNotIn('secret',json.dumps(self.service.status()))
        self.assertEqual(s.read(self.data/'secrets/cookies.json')['devops']['value'],'private=secret')
        self.assertEqual((self.data/'secrets/cookies.json').stat().st_mode & 0o777,0o600)

    def test_reject_header_injection_and_invalid_schedule(self):
        for body in [{'cookies':{'devops':'a=b\nInjected: true'}},{'schedule':{'enabled':True,'time':'27:00'}}]:
            with self.assertRaises(ValueError):self.service.save_settings(body)

    def test_missing_credentials_cannot_sync_or_schedule(self):
        self.service.backend={'port':3101}
        with self.assertRaises(ValueError):self.service.start('sync')
        with self.assertRaises(ValueError):self.service.save_settings({'schedule':{'enabled':True,'time':'18:00'}})

    def test_jumpserver_asset_inspect_validates_input(self):
        self.service.backend={'port':3101}
        with self.assertRaisesRegex(ValueError,'Cookie'):
            self.service.start('jumpserver_asset', asset_id='00000000-0000-0000-0000-000000000000')
        self.service.save_settings({'cookies':{'jumpserver':'cookie=private'}})
        with self.assertRaisesRegex(ValueError,'资产'):
            self.service.start('jumpserver_asset', asset_id='not-a-uuid')
        with self.assertRaisesRegex(ValueError,'资产'):
            self.service.start('jumpserver_asset', asset_id=None)

    def test_update_asset_root_password_updates_only_that_ip_entry(self):
        identifier=self.service.status()['current']['id']
        folder=self.data/'versions'/identifier
        snapshot=s.read(folder/'jumpserver-snapshot.json')
        snapshot['assets'].append({'id':'00000000-0000-0000-0000-000000000002','ip':'10.58.9.5'})
        s.write(folder/'jumpserver-snapshot.json',snapshot)
        s.write(self.data/'secrets/ip-password-map.json',
                {'10.58.9.5':{'status':'password_error'},'10.58.9.6':{'status':'password_error'}})
        self.service.update_asset_root_password('00000000-0000-0000-0000-000000000002','new-root-pass')
        ip_map=s.read(self.data/'secrets/ip-password-map.json')
        self.assertEqual(ip_map['10.58.9.5'],{'method':'su','password':'new-root-pass','status':'normal',
                                              'updatedAt':ip_map['10.58.9.5']['updatedAt']})
        self.assertEqual(ip_map['10.58.9.6'],{'status':'password_error'})
        self.assertEqual((self.data/'secrets/ip-password-map.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError,'资产'):
            self.service.update_asset_root_password('00000000-0000-0000-0000-000000000003','pw')
        with self.assertRaisesRegex(ValueError,'资产 ID'):
            self.service.update_asset_root_password('server2','pw')
        for bad in ('', None, 'bad\npassword', 123):
            with self.assertRaises(ValueError): self.service.update_asset_root_password('00000000-0000-0000-0000-000000000002',bad)
        self.assertEqual(s.read(self.data/'secrets/ip-password-map.json')['10.58.9.5']['password'],'new-root-pass')

    def _seed_ip_login_asset(self, ip='10.58.1.74', hostname='host-74', asset_id='00000000-0000-0000-0000-000000000074', inspection=None):
        identifier=self.service.status()['current']['id']
        folder=self.data/'versions'/identifier
        snapshot=s.read(folder/'jumpserver-snapshot.json')
        asset={'id':asset_id,'ip':ip,'hostname':hostname}
        if inspection is not None: asset['inspection']=inspection
        snapshot['assets'].append(asset)
        s.write(folder/'jumpserver-snapshot.json',snapshot)

    def test_ip_login_info_returns_method_and_password_when_map_has_normal_entry(self):
        self._seed_ip_login_asset(inspection={'loginStatus':'can_login','sudoStatus':'root','checkedAt':'2026-10-13T10:00:00+08:00'})
        s.write(self.data/'secrets/ip-password-map.json',
                {'10.58.1.74':{'method':'su','password':'root-pw','status':'normal','updatedAt':'2026-10-12T18:00:00+08:00'}})
        info=self.service.ip_login_info('10.58.1.74')
        self.assertEqual(info['ip'],'10.58.1.74')
        self.assertEqual(info['hostname'],'host-74')
        self.assertEqual(info['assetId'],'00000000-0000-0000-0000-000000000074')
        self.assertEqual(info['loginStatus'],'can_login')
        self.assertEqual(info['sudoStatus'],'root')
        self.assertEqual(info['inspectionCheckedAt'],'2026-10-13T10:00:00+08:00')
        self.assertEqual(info['elevation'],{'method':'su','status':'normal','password':'root-pw',
                                            'updatedAt':'2026-10-12T18:00:00+08:00','source':'ip-password-map'})
        self.assertIn('su 提权',info['note'])

    def test_ip_login_info_hides_password_when_status_is_password_error(self):
        self._seed_ip_login_asset(ip='10.58.9.7',inspection={'loginStatus':'can_login','sudoStatus':'password_error','checkedAt':'2026-10-12T10:00:00+08:00'})
        s.write(self.data/'secrets/ip-password-map.json',
                {'10.58.9.7':{'method':'su','password':'stale-pw','status':'password_error','updatedAt':'2026-10-12T09:00:00+08:00'}})
        info=self.service.ip_login_info('10.58.9.7')
        self.assertEqual(info['elevation']['status'],'password_error')
        self.assertIsNone(info['elevation']['password'])  # never surface a stale password alongside an error marker
        self.assertIn('密码错误',info['note'])

    def test_ip_login_info_reports_default_probe_chain_when_ip_not_in_map(self):
        self._seed_ip_login_asset(ip='10.58.9.8',inspection={'loginStatus':'cannot_login','reason':'连接超时','sudoStatus':None,'checkedAt':'2026-10-12T10:00:00+08:00'})
        s.write(self.data/'secrets/ip-password-map.json',{})
        info=self.service.ip_login_info('10.58.9.8')
        self.assertIsNone(info['elevation'])
        self.assertEqual(info['loginStatus'],'cannot_login')
        self.assertIsNone(info['sudoStatus'])
        self.assertIn('探测顺序',info['note'])
        self.assertIn('sudo -i',info['note'])
        self.assertIn('su -l',info['note'])

    def test_ip_login_info_handles_asset_without_inspection(self):
        # Older snapshots may have assets with no inspection field at all.
        self._seed_ip_login_asset(ip='10.58.9.9',inspection=None)
        s.write(self.data/'secrets/ip-password-map.json',
                {'10.58.9.9':{'method':'sudo-i','password':'folidev-pw','status':'normal','updatedAt':'2026-10-12T08:00:00+08:00'}})
        info=self.service.ip_login_info('10.58.9.9')
        self.assertIsNone(info['loginStatus'])
        self.assertIsNone(info['sudoStatus'])
        self.assertIsNone(info['inspectionCheckedAt'])
        self.assertEqual(info['elevation']['method'],'sudo-i')
        self.assertEqual(info['elevation']['password'],'folidev-pw')

    def test_ip_login_info_rejects_invalid_ip_format(self):
        for bad in (None, '', 'not-an-ip', '10.58.1', '10.58.1.74.5', 123, '10.58.1.74/24', '../../etc/passwd'):
            with self.assertRaisesRegex(ValueError,'IP 格式无效'):
                self.service.ip_login_info(bad)

    def test_ip_login_info_404s_when_ip_not_in_jumpserver_snapshot(self):
        # Snapshot has no asset with this IP.
        with self.assertRaisesRegex(ValueError,'未收录此 IP'):
            self.service.ip_login_info('10.99.99.99')

    def test_ip_login_info_404s_when_no_current_version(self):
        # Drop the current pointer entirely.
        (self.data/'current.json').unlink()
        with self.assertRaisesRegex(ValueError,'当前数据版本'):
            self.service.ip_login_info('10.58.1.74')

    def test_jumpserver_asset_work_passes_asset_id_to_collector(self):
        identifier='js-asset'
        folder=self.data/'versions'/identifier
        s.write(folder/'version.json',{'id':identifier,'status':'collecting'})
        self.service.job={'id':identifier,'kind':'jumpserver_asset','status':'running'}
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            return type('Result',(),{'returncode':0})()
        with patch.object(s.subprocess,'run',side_effect=run),patch.object(self.service,'activate'):
            self.service.work('jumpserver_asset',identifier,'00000000-0000-0000-0000-000000000000')
        self.assertEqual(calls[0][-2],'jumpserver_asset')
        self.assertEqual(calls[0][-1],'00000000-0000-0000-0000-000000000000')
        self.assertEqual(self.service.job['status'],'succeeded')

    def test_traversal_and_failed_versions_cannot_activate(self):
        for identifier in ('../../secrets','unknown',None):
            with self.assertRaises(ValueError):self.service.version(identifier)
        s.write(self.data/'versions/failed/version.json',{'id':'failed','status':'failed'})
        with self.assertRaises(ValueError):self.service.version('failed')

    def test_concurrent_jobs_rejected(self):
        self.service.backend={'port':3101}
        self.service.job={'status':'running','kind':'switch'}
        with self.assertRaises(ValueError):self.service.start('switch',self.service.status()['current']['id'])

    def test_incomplete_group_coverage_rejected(self):
        s.write(self.app/'lib/jumpserver-snapshot.json',{'assets':[{'id':'server'}],'groups':[{'id':'g','assetIds':[]}]})
        with self.assertRaises(ValueError):s.validate(self.app/'lib')

    def test_build_failure_leaves_current_unchanged(self):
        current=s.read(self.data/'current.json')
        (self.app/'node_modules').mkdir()
        with patch.object(s.subprocess,'run') as run:
            run.return_value.returncode=1
            with self.assertRaises(ValueError):self.service.activate(current['id'])
        self.assertEqual(s.read(self.data/'current.json'),current)
        self.assertIsNone(self.service.backend)

    def test_restart_marks_interrupted_sync_failed(self):
        s.write(self.data/'job.json',{'id':'interrupted','status':'running','kind':'sync'})
        s.write(self.data/'versions/interrupted/version.json',{'id':'interrupted','status':'collecting','createdAt':s.now()})
        restored=s.Service()
        self.assertEqual(restored.job['status'],'failed')
        self.assertEqual(s.read(self.data/'versions/interrupted/version.json')['status'],'failed')

    def test_activation_commits_only_after_healthy_launch(self):
        identifier=self.service.status()['current']['id']
        (self.app/'node_modules').mkdir()
        with patch.object(s.subprocess,'run') as run,patch.object(self.service,'launch') as launch:
            run.return_value.returncode=0
            def fail(*args):
                self.assertIsNone(self.service.backend)
                raise ValueError('health failed')
            launch.side_effect=fail
            with self.assertRaises(ValueError):self.service.activate(identifier)
        self.assertIsNone(self.service.backend)

    def test_credentials_can_be_saved_while_a_job_is_running(self):
        # A failed or long sync must not block updating an expired Cookie:
        # collection snapshots credentials at start, so in-flight jobs are unaffected.
        self.service.backend={'port':3101}
        self.service.job={'id':'running-job','status':'running','kind':'sync','phase':'正在采集数据'}
        self.service.save_settings({'cookies':{'aliyun':'session=token-value'}})
        saved=s.credentials()
        self.assertEqual(saved['aliyun']['value'],'session=token-value')
        self.assertNotIn('token-value',json.dumps(self.service.status()))
        # Sync and switch still reject a concurrent job; only config saving is allowed.
        with self.assertRaises(ValueError):self.service.start('switch',self.service.status()['current']['id'])

    def test_running_job_exposes_every_source_and_slot_state(self):
        # JumpServer's counter used to overwrite job['progress'], hiding the other
        # sources and every slot. All seven states and three slots must survive.
        folder=self.data/'versions'/'running-sync'
        s.write(folder/'version.json',{'id':'running-sync','createdAt':s.now(),'status':'collecting','kind':'sync'})
        sources={'ECS':'完成','CLB':'完成','NAT':'采集中','JumpServer':'采集中','Codeup':'等待','Local GitLab':'等待','DevOps':'等待'}
        s.write(folder/'progress.json',{'phase':'并行采集：NAT、JumpServer','completed':2,'total':7,'sources':sources,
                'slots':{'1':{'source':'JumpServer','status':'采集中'},'2':{'source':'NAT','status':'采集中'},'3':{'source':None,'status':'空闲'}}})
        s.write(folder/'server-progress.json',{'phase':'正在通过 JumpServer 检查服务器、进程与 Nginx','completed':5,'total':421})
        (folder/'slots').mkdir(parents=True,exist_ok=True)
        (folder/'slots'/'1.log').write_text('JumpServer line')
        (folder/'slots'/'2.log').write_text('NAT line')
        self.service.backend={'port':3101}
        self.service.job={'id':'running-sync','status':'running','kind':'sync','phase':'正在采集数据'}
        status=self.service.status()
        progress=status['job']['progress']
        self.assertEqual(progress['sources'],sources)
        self.assertEqual(len(progress['slots']),3)
        self.assertEqual(progress['jumpServer']['completed'],5)
        self.assertEqual(status['job']['slotLogs'],{'1':'JumpServer line','2':'NAT line'})
        # Without a source list the JumpServer phase is still shown.
        self.service.job['phase']='正在采集数据'
        self.assertEqual(self.service.status()['job']['progress']['sources'],sources)

    def test_single_source_sync_exposes_log_tail_and_progress(self):
        # status() used to only read collection.log / progress.json for sync,
        # local_gitlab, jumpserver and jumpserver_asset, so a standalone DevOps
        # (or Codeup / ECS / CLB / NAT) sync hid the log panel even while running.
        for kind,phase in [('devops','正在独立采集 DevOps 应用、环境和发布记录'),
                            ('codeup','正在独立采集 Codeup 代码组和仓库'),
                            ('ecs','正在独立采集阿里云 ECS 实例')]:
            folder=self.data/'versions'/f'running-{kind}'
            s.write(folder/'version.json',{'id':f'running-{kind}','createdAt':s.now(),'status':'collecting','kind':kind})
            s.write(folder/'progress.json',{'phase':phase})
            (folder/'collection.log').write_text(f'{kind} collection line')
            self.service.backend={'port':3101}
            self.service.job={'id':f'running-{kind}','status':'running','kind':kind,'phase':'正在采集数据'}
            status=self.service.status()
            self.assertEqual(status['job']['logTail'],'{kind} collection line'.format(kind=kind),
                             f'{kind} logTail must be exposed so the Settings log panel renders')
            self.assertEqual(status['job']['progress']['phase'],phase)

    def test_switch_job_does_not_look_for_collection_log(self):
        # switch re-activates an already-ready version and writes no collection.log;
        # status() must not raise trying to read per-source files for it.
        self.service.backend={'port':3101}
        self.service.job={'id':'switch-job','status':'running','kind':'switch','phase':'正在切换版本'}
        status=self.service.status()
        self.assertNotIn('logTail',status['job'])
        self.assertNotIn('progress',status['job'])

    def test_failed_job_exposes_structured_failures_and_log(self):
        # A failed collection job must surface collection-error.json failures
        # and the full collection.log so the user can see which source failed
        # and why — not just a generic "采集失败" phase string.
        folder=self.data/'versions'/'failed-sync'
        s.write(folder/'version.json',{'id':'failed-sync','createdAt':s.now(),
              'status':'failed','kind':'sync','error':'采集失败：ECS：…；当前版本未改变'})
        (folder/'collection.log').write_text('ECS: connection refused\nCLB: timeout\n')
        s.write(folder/'collection-error.json',{'type':'ExportError',
              'message':'ECS：…；CLB：…',
              'failures':[
                  {'source':'ECS','type':'ConnectionError','message':'ECS：连接被拒绝'},
                  {'source':'CLB','type':'HTTPError','credential':'aliyun','message':'CLB：请求超时'},
              ]})
        self.service.backend={'port':3101}
        self.service.job={'id':'failed-sync','status':'failed','kind':'sync',
                          'phase':'采集失败：ECS：…；当前版本未改变','finishedAt':s.now()}
        status=self.service.status()
        # logTail must be populated for failed jobs so the log panel renders.
        self.assertIn('connection refused',status['job']['logTail'])
        # failures array must be forwarded verbatim from collection-error.json.
        self.assertEqual(len(status['job']['failures']),2)
        self.assertEqual(status['job']['failures'][0]['source'],'ECS')
        self.assertEqual(status['job']['failures'][1]['credential'],'aliyun')
        # switch jobs must still be exempt from this path.
        self.service.job={'id':'switch-job','status':'failed','kind':'switch','phase':'切换失败'}
        status=self.service.status()
        self.assertNotIn('failures',status['job'])

    def test_startup_cannot_race_activation(self):
        with self.assertRaisesRegex(ValueError,'恢复'):
            self.service.start('switch',self.service.status()['current']['id'])


if __name__=='__main__':unittest.main()
