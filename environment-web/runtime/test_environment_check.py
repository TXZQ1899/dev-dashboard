import io, json, os, subprocess, sys, tempfile, threading, unittest, zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
import service as s

class EnvironmentCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name); p = patch.object(s, 'DATA', self.data); p.start(); self.addCleanup(p.stop)
        self.service = object.__new__(s.Service)
        self.service.lock = threading.RLock(); self.service.environment_check_job = None; self.service.check_job = None
        self.service.files_lock = threading.Lock(); self.service.archive_job = None; self.service.job = None; self.service.extra_jobs = []
        self.source = 'ready-1'; folder = self.data/'versions'/self.source
        self.process = {'pid':123, 'startedAt':'2026-01-01T00:00:00Z', 'command':'java -jar sample.jar'}
        self.asset = {'id':'asset-1', 'ip':'192.0.2.1', 'inspection':{'processes':[self.process]}}
        s.write(folder/'snapshot.json', {'apps':[]}); s.write(folder/'jumpserver-snapshot.json', {'assets':[self.asset]})
        self.payload = {'unitId':'12345678-1234-1234-1234-123456789012', 'environment':'PRODUCT'}
        self.service.catalog_query = Mock(return_value={'unitType':'APPLICATION', 'unitName':'sample', 'appId':'1', 'activeSnapshot':{'version':self.source, 'matchesCatalog':True}, 'instances':[
            {'id':'i1', 'ip':'192.0.2.1', 'processObservation':{'status':'MATCHED', 'pid':123, 'startedAt':self.process['startedAt']}},
            {'id':'i2', 'ip':'192.0.2.2', 'processObservation':{'status':'NOT_FOUND', 'reason':'无进程'}}]})

    def start(self):
        with patch.object(s.threading.Thread, 'start'): return self.service.start_environment_check(self.payload)['job']

    def test_scope_progress_and_report_persistence(self):
        job = self.start(); self.assertEqual([i['status'] for i in job['nodes']], ['pending', 'unresolved']); self.assertEqual(len(job['items']), 11)
        with self.assertRaises(ValueError): self.start()
        targets = s.read(self.data/'checks'/job['id']/'inspection-targets.json')
        self.assertEqual(targets[0]['pid'], 123); self.assertIsNone(targets[1]['pid'])
        folder = self.data/'checks'/job['id']/'inspection'; folder.mkdir(); (folder/'巡检结果.xlsx').write_bytes(b'fixture')
        s.write(folder/'inspection-status.json', {'status':[['TCP 192.0.2.2','UNKNOWN','无进程']]})
        events = [{'event':'stage','name':'Kong'}, {'event':'log','message':'开始检查'}, {'event':'stage_end','name':'Kong','rows':[['Kong','PARTIAL','缺失网关']]}, {'event':'node','instanceId':'i1','status':'complete'}, {'event':'stage','name':'生成 Excel 报告'}]
        child = Mock(stdout=io.StringIO(''.join(json.dumps(e)+'\n' for e in events))); child.wait.return_value = 0
        with patch.object(s.subprocess, 'Popen', return_value=child): self.service.work_environment_check(job['id'])
        state = self.service.environment_check_state(job['id'])
        self.assertEqual(state['job']['status'], 'partial'); self.assertEqual(state['job']['items'][2]['status'], 'partial')
        self.assertTrue(state['reportReady']); self.assertEqual(self.service.environment_check_report(job['id']).read_bytes(), b'fixture')
        self.assertEqual(state['job']['events'][0]['message'], '开始检查')
        self.assertEqual(state['job']['nodes'][0]['status'], 'complete')
        self.assertEqual((self.data/'checks'/job['id']/'inspection-targets.json').stat().st_mode & 0o777, 0o600)

    def test_shared_pid_not_reused_and_conflicts_block(self):
        rows = self.service.catalog_query.return_value['instances']; rows[1] = {**rows[0], 'id':'i2'}
        self.assertEqual([i['status'] for i in self.start()['nodes']], ['unresolved', 'unresolved'])
        with self.assertRaises(ValueError): self.service.start_application_check({'pid':123})
        with self.assertRaises(ValueError): self.service.environment_check_state('../bad')

    def test_process_identity_must_match_snapshot(self):
        self.service.catalog_query.return_value['instances'][0]['processObservation']['startedAt'] = 'changed'
        self.assertEqual(self.start()['nodes'][0]['status'], 'unresolved')

    def test_runner_generates_excel_with_unknown_evidence(self):
        folder = self.data/'versions'/self.source
        s.write(folder/'snapshot.json', {'apps':[{'id':'1','name':'demo','envs':{'SIMULATION':[{'ip':'192.0.2.1','port':'8080'}]}}]})
        s.write(folder/'jumpserver-snapshot.json', {'assets':[]}); targets = self.data/'targets.json'
        s.write(targets, [{'ip':'192.0.2.1','assetId':'missing','pid':123,'startedAt':'time','ports':[9090]}]); output = self.data/'report'; runtime = Path(__file__).parent
        env = dict(os.environ, ENVSCOPE_DATA=str(self.data), PYTHONPATH=str(runtime))
        result = subprocess.run([sys.executable, str(runtime/'environment_inspection'/'run.py'), '--app-id','1', '--environment','SIMULATION','--version',self.source,'--targets',str(targets),'--output',str(output)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertIn('300 秒 TCP 检查', [event.get('name') for event in stages])
        self.assertTrue(any(row[0].startswith('TCP ') and row[1] == 'UNKNOWN' for row in s.read(output/'inspection-status.json')['status']))
        with zipfile.ZipFile(output/'巡检结果.xlsx') as book:
            self.assertIn('检查状态', book.read('xl/workbook.xml').decode())
            sheets = ''.join(book.read(name).decode() for name in book.namelist() if name.startswith('xl/worksheets/'))
            self.assertIn('9090', sheets); self.assertIn('JumpServer 进程监听观察', sheets)

    def test_tcp_runs_each_of_four_nodes_for_300_seconds(self):
        runtime = Path(__file__).parent
        sys.path.insert(0, str(runtime/'environment_inspection'))
        self.addCleanup(sys.path.remove, str(runtime/'environment_inspection'))
        import tcp_run
        assets, targets = [], []
        for n in range(1, 5):
            process = {**self.process, 'pid':100+n}
            asset = {'id':f'asset-{n}', 'ip':f'192.0.2.{n}', 'inspection':{'processes':[process]}}
            assets.append(asset)
            targets.append({'instanceId':f'i{n}', 'ip':asset['ip'], 'assetId':asset['id'], 'pid':process['pid'], 'startedAt':process['startedAt']})
        folder = self.data/'versions'/self.source
        s.write(folder/'jumpserver-snapshot.json', {'assets':assets})
        s.write(self.data/'secrets'/'cookies.json', {'jumpserver':{'value':'private'}})
        target_path = self.data/'targets.json'; s.write(target_path, targets)
        env = {'ENVSCOPE_DATA':str(self.data), 'ENVSCOPE_INSPECTION_VERSION':self.source, 'ENVSCOPE_INSPECTION_TARGETS':str(target_path)}
        def inspect(asset, process, apps, assets, saved, ip_map, environment, duration, progress):
            if process['pid'] == 103: raise ValueError('private')
            return {'status':'complete', 'target':{'serverIp':asset['ip'], 'pid':process['pid']}, 'environment':environment, 'coverage':{'requestedSeconds':duration}}
        events = io.StringIO()
        with patch.dict(os.environ, env), patch.object(tcp_run, 'inspect_application', side_effect=inspect) as check, redirect_stdout(events):
            paths, errors = tcp_run.collect('1', 'PRODUCT', self.data/'tcp', lambda message: None)
        self.assertEqual(check.call_count, 4)
        self.assertTrue(all(call.args[7] == 300 for call in check.call_args_list))
        self.assertEqual(len(paths), 3); self.assertEqual(errors, [('192.0.2.3','TCP 检查失败：ValueError')])
        self.assertNotIn('private', str(errors))
        self.assertEqual([event['status'] for event in map(json.loads, events.getvalue().splitlines()) if event['instanceId'] == 'i3'], ['running', 'failed'])

    def test_kong_uses_configured_environment_and_retains_failed_gateway(self):
        runtime = Path(__file__).parent
        sys.path.insert(0, str(runtime/'environment_inspection'))
        self.addCleanup(sys.path.remove, str(runtime/'environment_inspection'))
        import kong_scan
        def request(url, timeout):
            if 'bad:' in url: raise OSError('unavailable')
            if '/upstreams?' in url: rows = [{'id':'u', 'name':'upstream'}]
            elif '/services?' in url: rows = [{'id':'s', 'name':'svc', 'host':'upstream'}]
            elif '/routes?' in url: rows = [{'name':'route', 'service':{'id':'s'}}]
            else: rows = [{'target':'192.0.2.1:8080'}]
            class Response:
                def __enter__(self): return io.BytesIO(json.dumps({'data':rows, 'next':None}).encode())
                def __exit__(self, *args): pass
            return Response()
        with patch.dict(kong_scan.GATEWAYS, {'TEST':['good','bad']}):
            result = kong_scan.scan_kong('TEST', [('192.0.2.1','8080')], request)
        self.assertEqual(result['queried'], 1)
        self.assertEqual(result['matches'][0]['services'][0]['routes'][0]['name'], 'route')
        self.assertTrue(any('bad' in warning for warning in result['warnings']))

    def test_live_bridge_keeps_elevation_diagnostics_out_of_json_events(self):
        runtime = Path(__file__).parent
        sys.path.insert(0, str(runtime/'environment_inspection'))
        self.addCleanup(sys.path.remove, str(runtime/'environment_inspection'))
        import live_scan
        terminal = Mock()
        terminal.run.return_value = (0, '-bash: rintf: command not found\nENVSCOPE_INSPECTION_RESULT\t{"observedAt":"now","listenerStatus":"complete","processes":[]}')
        def opener(*args):
            print('elevation diagnostic')
            return terminal, 'password_error'
        def load(path):
            if str(path).endswith('jumpserver-snapshot.json'): return {'assets':[{'id':'a','ip':'192.0.2.1'}]}
            if str(path) == 'targets': return [{'ip':'192.0.2.1','assetId':'a','pid':123,'startedAt':'time'}]
            return {}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['live_scan.py','192.0.2.1','sample','v1','encoded']), patch.dict(os.environ, {'ENVSCOPE_INSPECTION_TARGETS':'targets'}), patch.object(live_scan, 'load', side_effect=load), patch.object(live_scan.application_check, 'open_terminal', side_effect=opener), redirect_stdout(stdout), patch.object(sys, 'stderr', stderr):
            live_scan.main()
        self.assertEqual(json.loads(stdout.getvalue())['event'], 'host')
        self.assertIn('elevation diagnostic', stderr.getvalue())
        terminal.close.assert_called_once()
        with self.assertRaises(ValueError): live_scan.parse_probe_output('no result')
        with self.assertRaises(ValueError): live_scan.parse_probe_output('ENVSCOPE_INSPECTION_RESULT\t{}\nENVSCOPE_INSPECTION_RESULT\t{}')

if __name__ == '__main__': unittest.main()
