import unittest
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from git_address_check import check_snapshot
import service


class GitAddressCheckTests(unittest.TestCase):
    def test_deduplication_deployment_overrides_and_unknowns(self):
        url = 'https://codeup.aliyun.com/org/team/repo.git'
        snapshot = {'apps': [
            {'id': '1', 'repository': url, 'envs': {'TEST': [{'repository': 'git@codeup.aliyun.com:org/team/repo.git'}, {'repository': 'https://other.example/team/repo.git'}]}},
            {'id': '2', 'repository': url, 'envs': {}},
        ]}
        with patch('git_address_check.Checker') as factory:
            factory.return_value.check.return_value = ('否', '仓库不存在或对当前账号不可见', '200', 'NOT_FOUND', False)
            result = check_snapshot(snapshot, {'aliyun': {'value': 'cookie'}})
        self.assertEqual(factory.return_value.check.call_count, 1)
        self.assertEqual([r['status'] for r in result['1']].count('missing'), 2)
        self.assertEqual([r['status'] for r in result['1']].count('unknown'), 1)
        self.assertEqual(result['2'][0]['status'], 'missing')

    def test_stopped_authentication_does_not_mark_later_addresses_missing(self):
        snapshot = {'apps': [{'id': '1', 'repository': 'https://codeup.aliyun.com/org/a.git', 'envs': {'TEST': [{'repository': 'https://codeup.aliyun.com/org/b.git'}]}}]}
        with patch('git_address_check.Checker') as factory:
            factory.return_value.check.return_value = ('无法确认', '登录失效', '401', '', True)
            result = check_snapshot(snapshot, {'aliyun': {'value': 'cookie'}})
        self.assertEqual(factory.return_value.check.call_count, 1)
        self.assertEqual([r['status'] for r in result['1']], ['unknown', 'unknown'])

    def test_accesskey_denial_is_not_missing(self):
        snapshot = {'apps': [{'id': '1', 'repository': 'https://codeup.aliyun.com/org/a.git', 'envs': {}}]}
        saved = {'aliyun_auth_mode': {'value': 'accesskey'}, 'aliyun_accesskey_id': {'value': 'id'}, 'aliyun_accesskey_secret': {'value': 'secret'}}
        with patch('git_address_check._codeup_client'), patch('git_address_check._codeup_path_status', return_value={'access': '暂无权限访问', 'reason': '无权限'}) as lookup:
            result = check_snapshot(snapshot, saved)
        self.assertEqual(result['1'][0]['status'], 'denied')
        self.assertEqual(lookup.call_args.args[1], 'org')

    def test_service_persists_completed_result_and_keeps_snapshot(self):
        class ImmediateThread:
            def __init__(self, target, daemon): self.target = target
            def start(self): self.target()
        with tempfile.TemporaryDirectory() as directory:
            instance = service.Service.__new__(service.Service)
            instance.lock = threading.RLock()
            instance.git_check_job = {'status': 'idle'}
            raw = b'{"collectedAt":"time","apps":[]}'
            instance.catalog_context = lambda: ('version-1', raw)
            saved = {'aliyun': {'value': 'cookie'}}
            with patch.object(service, 'DATA', Path(directory)), patch.object(service, 'credentials', return_value=saved), patch.object(service.threading, 'Thread', ImmediateThread), patch('git_address_check.check_snapshot', return_value={'1': []}):
                instance.start_git_check()
                state = instance.git_check_state()
                self.assertEqual(state['job']['status'], 'complete')
                self.assertEqual(state['result']['version'], 'version-1')
                self.assertEqual(state['result']['snapshotCollectedAt'], 'time')
                self.assertTrue((Path(directory)/'checks/git-address/latest.json').exists())


if __name__ == '__main__':
    unittest.main()
