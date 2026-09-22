import io
import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import collect
from collect import jumpserver, install_slot_logs


ASSETS = [{'id': f'a{i}', 'hostname': f'h{i}', 'ip': f'10.0.0.{i}', 'os': 'linux', 'platform': 'Linux'} for i in range(1, 31)]


def make_fetcher(assets=ASSETS, inspection=None):
    """Minimal JumpServer API stand-in: assets plus one covering group."""
    def get(path):
        # Query strings are part of the path here, so match on the base route.
        base = path.split('?', 1)[0]
        if base.endswith('/system-users/'):
            return {'count': 1, 'results': [{'id': 'u1', 'username': 'root'}]}
        if base == '/api/v1/perms/users/assets/':
            return {'count': len(assets), 'results': assets}
        if base == '/api/v1/perms/users/nodes/':
            return [{'id': 'n1', 'key': '1', 'name': '默认'}]
        if base.startswith('/api/v1/perms/users/nodes/') and base.endswith('/assets/'):
            return {'count': len(assets), 'results': assets}
        raise AssertionError('unexpected path ' + path)
    return get


def fake_collect_servers(assets, get, cookie, password, progress=None):
    """Report progress like the real collector, without opening any terminal."""
    result = {}
    for index, asset in enumerate(assets, 1):
        result[asset['id']] = {'checkedAt': '2026-09-20T00:00:00+00:00', 'loginStatus': 'can_login' if index <= 20 else 'cannot_login',
                               'processStatus': 'complete', 'processes': [{'pid': 1, 'name': 'java'}],
                               'nginxStatus': 'complete' if index <= 10 else 'not_running', 'nginxRoutes': [],
                               'nginxConfigurations': ([{'path': '/etc/nginx/nginx.conf', 'content': 'x'}] if index <= 5 else []),
                               'warnings': []}
        if progress:
            progress(index, len(assets))
    return result


class JumpServerLogTests(unittest.TestCase):
    def test_jumpserver_writes_stage_progress_and_summary_logs(self):
        # The slot tab for JumpServer was empty because the collector never printed.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            install_slot_logs(tmp / 'slots')
            try:
                import threading
                threading.current_thread().name = 'slot:1'
                with patch.object(collect, 'collect_servers', fake_collect_servers):
                    result = jumpserver('cookie', tmp, '', fetcher=make_fetcher())
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(len(result['assets']), 30)

            # The combined slot log must carry the real stages, not just a summary.
            text = (tmp / 'slots' / '1.log').read_text()
            self.assertIn('JumpServer 授权资产：30 台', text)
            self.assertIn('JumpServer 授权分组：1 个', text)
            self.assertIn('开始检查服务器、进程与 Nginx', text)
            self.assertIn('JumpServer 检查：25/30 台', text)   # cadence marker
            self.assertIn('JumpServer 检查：30/30 台', text)   # final asset always logged
            self.assertIn('可登录 20 台，不能登录 10 台', text)
            self.assertIn('Nginx 读取完整 10 台', text)
            self.assertIn('原始配置 5 份', text)
            self.assertIn('采集完成：30 台资产，1 个分组', text)

    def test_progress_records_a_snapshot_file_for_the_ui(self):
        # The UI also reads server-progress.json; it must stay written.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with patch.object(collect, 'collect_servers', fake_collect_servers):
                jumpserver('cookie', tmp, '', fetcher=make_fetcher())
            progress = json.loads((tmp / 'server-progress.json').read_text())
            self.assertEqual(progress['total'], 30)
            self.assertEqual(progress['completed'], 30)
            self.assertIn('JumpServer', progress['phase'])

    def test_logs_never_leak_the_cookie_or_password(self):
        # Collection logs are surfaced in the browser; secrets must not appear.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            install_slot_logs(tmp / 'slots')
            try:
                import threading
                threading.current_thread().name = 'slot:1'
                with patch.object(collect, 'collect_servers', fake_collect_servers):
                    jumpserver('super-secret-cookie', tmp, 'secret-password', fetcher=make_fetcher())
            finally:
                sys.stdout = sys.__stdout__
            text = (tmp / 'slots' / '1.log').read_text()
            self.assertNotIn('super-secret-cookie', text)
            self.assertNotIn('secret-password', text)


SINGLE_ASSET_SNAPSHOT = {'collectedAt': 'old', 'groups': [{'id': 'g', 'assetIds': ['a1']}],
                         'assets': [{'id': 'a1', 'hostname': 'h1', 'ip': '10.0.0.1', 'os': 'linux', 'platform': 'Linux',
                                     'inspection': {'checkedAt': 'old', 'processes': [{'pid': 9, 'name': 'java'}]}}]}


def single_inspection(configs):
    def inspect(assets, get, cookie, password, progress=None):
        assert [a['id'] for a in assets] == ['a1']
        return {'a1': {'checkedAt': 'new', 'loginStatus': 'can_login', 'processStatus': 'complete',
                       'processes': [{'pid': 1, 'name': 'nginx'}], 'nginxStatus': 'complete',
                       'nginxRoutes': [], 'nginxConfigurations': configs, 'warnings': []}}
    return inspect


class JumpServerAssetTests(unittest.TestCase):
    def test_single_asset_inspection_merges_into_copied_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'jumpserver-snapshot.json').write_text(json.dumps(SINGLE_ASSET_SNAPSHOT))
            configs = [{'path': '/etc/nginx/nginx.conf', 'content': 'x'}]
            with patch.object(collect, 'collect_servers', single_inspection(configs)):
                result = collect.jumpserver_asset('cookie', tmp, 'pw', 'a1', fetcher=make_fetcher())
            asset = result['assets'][0]
            self.assertEqual(asset['inspection']['checkedAt'], 'new')
            self.assertEqual(asset['inspection']['configurationCount'], 1)
            self.assertEqual(asset['inspection']['configurationVersion'], tmp.name)
            saved = json.loads((tmp / 'nginx-configs' / (hashlib.sha256(b'a1').hexdigest() + '.json')).read_text())
            self.assertEqual(saved['assetId'], 'a1')
            self.assertEqual(saved['files'], configs)

    def test_single_asset_inspection_without_configs_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'jumpserver-snapshot.json').write_text(json.dumps(SINGLE_ASSET_SNAPSHOT))
            stale = tmp / 'nginx-configs' / (hashlib.sha256(b'a1').hexdigest() + '.json')
            stale.parent.mkdir(); stale.write_text('{"assetId":"a1"}')
            with patch.object(collect, 'collect_servers', single_inspection([])):
                result = collect.jumpserver_asset('cookie', tmp, 'pw', 'a1', fetcher=make_fetcher())
            self.assertEqual(result['assets'][0]['inspection']['configurationCount'], 0)
            self.assertFalse(stale.exists())

    def test_single_asset_inspection_rejects_unknown_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'jumpserver-snapshot.json').write_text(json.dumps(SINGLE_ASSET_SNAPSHOT))
            with self.assertRaises(Exception):
                collect.jumpserver_asset('cookie', tmp, 'pw', 'missing', fetcher=make_fetcher())


if __name__ == '__main__':
    unittest.main()
