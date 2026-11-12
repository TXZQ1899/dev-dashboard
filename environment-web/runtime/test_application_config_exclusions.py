import json
import tempfile
import unittest
import threading
from pathlib import Path

import application_config_exclusions as exclusions
import service


class ExclusionsTest(unittest.TestCase):
    def test_marks_eligible_apps_and_reverses_without_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            results=[{'appId':'1','name':'missing','status':'failed','error':'Codeup 未找到该仓库，或当前账号无法查看','snapshotVersion':'v1'},
                     {'appId':'2','name':'unsupported','status':'failed','error':'仅支持 Codeup HTTPS 仓库'},
                     {'appId':'3','name':'skip','status':'skipped','reason':'非 WAR/JAR'},
                     {'appId':'4','name':'other','status':'failed','error':'连接失败或超时'},
                     {'appId':'5','name':'unmatched','status':'failed','error':'当前快照中仓库匹配数量为 0'},
                     {'appId':'6','name':'no-url','status':'failed','error':'当前 DevOps 快照未提供应用仓库地址'}]
            saved=exclusions.update_exclusions(data,results,['1','2','3','5','6'],True,service.write)
            self.assertEqual([item['category'] for item in saved],['codeup_not_found','unsupported_repository','skipped','repository_unmatched','repository_missing'])
            self.assertEqual((data/'application-config-exclusions.json').stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError): exclusions.update_exclusions(data,results,['1','4'],True,service.write)
            self.assertEqual(len(exclusions.read_exclusions(data)),5)
            saved=exclusions.update_exclusions(data,results,['2'],False,service.write)
            self.assertEqual([item['appId'] for item in saved],['1','3','5','6'])
            self.assertEqual(saved[0]['snapshotVersion'],'v1')

    def test_corrupt_policy_does_not_silently_allow_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'application-config-exclusions.json'
            path.write_text('{invalid')
            with self.assertRaisesRegex(ValueError,'清单读取失败'): exclusions.read_exclusions(Path(directory))
            path.write_text(json.dumps({'schemaVersion':1,'applications':[{'appId':'7'},{'appId':'7'}]}))
            with self.assertRaisesRegex(ValueError,'清单格式无效'): exclusions.read_exclusions(Path(directory))

    def test_marking_during_job_does_not_modify_frozen_scope(self):
        from unittest.mock import patch
        worker=service.Service.__new__(service.Service)
        worker.lock=threading.RLock()
        worker.config_sync_job={'status':'running','excludedIds':[], 'results':[{'appId':'7','name':'skip','status':'skipped'}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(service,'DATA',Path(directory)):
            result=worker.update_config_exclusions({'appIds':['7'],'excluded':True})
            self.assertEqual(result['applications'][0]['appId'],'7')
            self.assertEqual(worker.config_sync_job['excludedIds'],[])


if __name__ == '__main__': unittest.main()
