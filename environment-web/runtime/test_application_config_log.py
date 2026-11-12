import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

import application_config_log as log
import application_config_sync as sync


class ConfigLogTest(unittest.TestCase):
    def test_allowlist_and_path_redaction_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log.reset(root)
            log.append(root, 'tree_start', appId='7', path='src/main/resources', page=2,
                       cookie='private-cookie', content='secret-config', url='https://example.com/?token=x')
            log.append(root, 'file_start', path='https://host/path?token=secret')
            data = (root/'application-config-job.log').read_text()
            self.assertNotIn('private-cookie', data)
            self.assertNotIn('secret-config', data)
            self.assertNotIn('token=secret', data)
            self.assertEqual((root/'application-config-job.log').stat().st_mode & 0o777, 0o600)
            events = log.tail(root)['events']
            self.assertEqual(events[0]['path'], 'src/main/resources')
            self.assertEqual(events[1]['path'], '[路径无法安全显示]')

    def test_rotation_and_tail_limits(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(log, 'MAX_BYTES', 350), patch.object(log, 'MAX_LINES', 2):
            root = Path(directory); log.reset(root)
            for n in range(15): log.append(root, 'phase', appId=str(n), phase='发现资源文件')
            self.assertLessEqual((root/'application-config-job.log').stat().st_size, 350)
            self.assertTrue((root/'application-config-job.log.1').exists())
            tail = log.tail(root)
            self.assertTrue(tail['truncated'])
            self.assertLessEqual(len(tail['events']), 2)
            self.assertEqual(tail['events'][-1]['appId'], '14')
            log.reset(root)
            self.assertFalse((root/'application-config-job.log.1').exists())
            self.assertEqual(log.tail(root)['events'], [])

    def test_tree_reports_started_then_completed_or_failed_without_url(self):
        client = sync.Codeup({'url':'https://codeup.aliyun.com/org/repo','codeupId':'42'}, 'cookie')
        events=[]; client.trace=lambda event, **fields: events.append((event,fields))
        with patch.object(client, 'get', return_value=json.dumps([{'type':'blob','path':'resources/a.xml'}]).encode()):
            client.tree('resources')
        self.assertEqual([e[0] for e in events], ['tree_start','tree_done'])
        self.assertEqual(events[1][1]['count'],1)
        events.clear()
        with patch.object(client, 'get', side_effect=ValueError('sensitive')):
            with self.assertRaises(ValueError): client.tree('resources')
        self.assertEqual([e[0] for e in events], ['tree_start','tree_error'])
        self.assertNotIn('sensitive', str(events))
        self.assertEqual(events[-1][1]['errorKind'], '请求失败')
        self.assertEqual(sync.log_error_kind(ValueError('Codeup HTTP 403')), 'Codeup HTTP 403')

    def test_log_failure_does_not_interrupt_batch(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock()
        worker.catalog_store = object()
        worker.config_sync_job = {'status':'queued', 'results':[]}
        def run(_data, _store, _version, _apps, _repos, _cookie, update, activity, _previous):
            activity({'appId':'7','name':'orders','phase':'发现资源文件','completed':0,'total':0})
            activity.trace('tree_start', appId='7', path='src/main/resources', page=1)
            counts={'completed':1,'total':1,'ready':1,'partial':0,'failed':0,'skipped':0}
            update(counts, {'appId':'7','name':'orders','status':'ready'})
            return counts
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), \
             patch.object(log,'append',side_effect=OSError('disk failure')), patch.object(sync,'run',side_effect=run):
            worker._run_config_sync('v1', [], [], 'cookie')
        self.assertEqual(worker.config_sync_job['status'], 'ready')
        self.assertIn('详细日志写入失败', worker.config_sync_job['logError'])


if __name__ == '__main__': unittest.main()
