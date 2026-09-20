import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from batch_analyze_deployment_logs import analyze_deployment, run


class BatchLogAnalysisTests(unittest.TestCase):
    def make_export(self, root, suffix='one', secret='do-not-publish-credential'):
        app=root/('devops-details-'+suffix)/'sample'
        dep=app/'测试环境'/'logs'/'server_deploy-1'
        dep.mkdir(parents=True)
        summary={'app_name':'sample','environments':{'TEST':{'name':'测试环境','server_ips':['10.0.0.1'],'deployments':[{'deploy_id':1,'ip':'10.0.0.1'}]}}}
        (app/'summary.json').write_text(json.dumps(summary))
        log=('-----START-----\npropNameValues:\n----------------------------------------\n'
             '{cur_app_name=sample, _vmIP=10.0.0.1, _vmPort=22, http1.1_port=7007, BATCH_JOB_TOKEN='+secret+', dp_params= -p branch=main -p set_url= -p goal_options=clean install -Dmaven.test.skip=true}\n'
             '----------------------------------------\n>>>>>> start to executing script:run >>>>>>\n---------------SCRIPT--------------------\n'
             '#curl http://10.0.0.2/file\njava -auth '+secret+' build\n---------------RESULT-------------------\n'
             'curl: no URL specified!\n<<<<<<< script finished. <<<<<<<\n')
        (dep/'log.txt').write_text(log)
        index={'deployment':{'deploy_id':1,'ip':'10.0.0.1','log_status':'exported'},'steps':[{'operation':'Deploy','status':'SUCCESS','start_time':'2026-01-01 12:00:00','log_file':'log.txt'}]}
        (dep/'index.json').write_text(json.dumps(index))
        return dep

    def test_redaction_network_scope_and_nonzero_success_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep=self.make_export(Path(tmp))
            result,_=analyze_deployment(dep/'index.json','sample')
            self.assertNotIn('do-not-publish-credential',json.dumps(result))
            b=result['code_and_build']['build_parameters'][0]
            self.assertEqual(b['set_url'],'')
            self.assertEqual(b['goal_options'],'clean install -Dmaven.test.skip=true')
            self.assertTrue(any(r['ip']=='10.0.0.1' and r['port']==7007 for r in result['network_endpoints']))
            comment=next(r for r in result['network_endpoints'] if r['ip']=='10.0.0.2')
            self.assertIsNone(comment['port'])
            self.assertIn('注释引用',comment['scopes'])
            self.assertTrue(any(f['type']=='curl 错误' for f in result['review_findings']))

    def test_duplicate_export_and_missing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=self.make_export(root,'one');second=self.make_export(root,'two')
            with contextlib.redirect_stdout(io.StringIO()):run(root,root/'output')
            data=json.loads((root/'output'/'sample.json').read_text())
            self.assertEqual(data['coverage']['deployment_snapshots'],1)
            self.assertEqual(data['coverage']['merged_duplicate_indexes'],1)
            self.assertNotIn('"evidence"',json.dumps(data))
            (second/'log.txt').write_text('')
            with contextlib.redirect_stdout(io.StringIO()):run(root,root/'output')
            data=json.loads((root/'output'/'sample.json').read_text())
            self.assertEqual(data['coverage']['deployment_snapshots'],2)
            self.assertEqual(data['coverage']['snapshots_without_log_content'],1)

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep=self.make_export(Path(tmp));(dep/'log.txt').unlink()
            result,_=analyze_deployment(dep/'index.json','sample')
            self.assertFalse(result['operations'][0]['log_available'])
            self.assertTrue(result['data_issues'])

    def test_reject_log_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep=self.make_export(Path(tmp))
            p=dep/'index.json';data=json.loads(p.read_text());data['steps'][0]['log_file']='../outside.txt';p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):analyze_deployment(p,'sample')


if __name__=='__main__':unittest.main()
