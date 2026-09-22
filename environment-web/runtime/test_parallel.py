import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collect import parallel_collect, collection_failure, install_slot_logs
from export_devops import ExportError, AuthenticationError, CollectionError
from urllib.error import HTTPError

class ParallelTests(unittest.TestCase):
    def test_safe_collector_diagnostic_survives_parallel_wrapper(self):
        failure=collection_failure(CollectionError('Local GitLab 项目 179 / branches 第 2 页重复'),'Local GitLab')
        self.assertIn('第 2 页重复',failure['message'])
        unsafe=collection_failure(ExportError('private=secret'),'Local GitLab')
        self.assertNotIn('secret',unsafe['message'])

    def test_auth_platform_mapping_and_non_auth_failures(self):
        for source,key in [('ECS','aliyun'),('CLB','aliyun'),('NAT','aliyun'),('Codeup','aliyun'),('DevOps','devops'),('JumpServer','jumpserver'),('Local GitLab','local_gitlab')]:
            failure=collection_failure(AuthenticationError('private=secret'),source)
            self.assertEqual(failure['credential'],key)
            self.assertIn('Cookie 已失效',failure['message'])
            self.assertNotIn('secret',str(failure))
        for status in (302,401):
            self.assertEqual(collection_failure(HTTPError('url',status,'private',{},None),'JumpServer')['type'],'cookie_expired')
        for exc in (TimeoutError('secret'),HTTPError('url',500,'private',{},None),HTTPError('url',403,'private',{},None)):
            self.assertNotEqual(collection_failure(exc,'ECS')['type'],'cookie_expired')

    def test_parallel_auth_keeps_structured_platform(self):
        def fail(): raise AuthenticationError('secret')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExportError) as error:
                parallel_collect({'ECS':fail,'NAT':fail},Path(tmp)/'progress.json')
            self.assertEqual({f['credential'] for f in error.exception.failures},{'aliyun'})
            self.assertNotIn('secret',str(error.exception))

    def test_sources_overlap_and_respect_limit(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        running = peak = 0
        def task():
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            barrier.wait(timeout=5)
            with lock: running -= 1
            return 42
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'progress.json'
            result = parallel_collect({str(i):task for i in range(6)}, path)
            self.assertEqual(len(result),6)
            self.assertEqual(peak,3)
            progress = json.loads(path.read_text())
            self.assertEqual(progress['completed'],6)
            self.assertEqual(set(progress['sources'].values()),{'完成'})

    def test_failure_drains_other_sources_and_redacts_details(self):
        completed = threading.Event()
        def fail(): raise RuntimeError('secret-cookie-value')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'progress.json'
            with self.assertRaises(ExportError) as error:
                parallel_collect({'broken':fail, 'healthy':completed.set},path)
            self.assertTrue(completed.is_set())
            self.assertEqual(str(error.exception),'broken：RuntimeError')
            progress=json.loads(path.read_text())
            self.assertEqual(progress['sources'],{'broken':'失败','healthy':'完成'})
            self.assertNotIn('secret',path.read_text())

    def test_each_slot_reports_its_own_source_and_log(self):
        # Three concurrent slots reuse a worker per slot; the UI needs the ordered
        # history and a per-slot log so a slow source can be watched on its own.
        import time
        names=['ECS','CLB','NAT','JumpServer','Codeup','Local GitLab','DevOps']
        def task(name):
            def run():
                print(f'{name} running', flush=True)
                time.sleep(0.05)
            return run
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            install_slot_logs(tmp/'slots')
            parallel_collect({n:task(n) for n in names}, tmp/'progress.json')
            progress=json.loads((tmp/'progress.json').read_text())
            self.assertEqual(len(progress['slots']),3)
            self.assertEqual(set(progress['slots']),{'1','2','3'})
            # Every source ran in exactly one slot, and the order is preserved.
            seen=[entry['source'] for slot in sorted(progress['slotHistory']) for entry in progress['slotHistory'][slot]]
            self.assertEqual(sorted(seen),sorted(names))
            self.assertEqual(set(progress['slotHistory']),{'1','2','3'})
            # Each slot log names only the sources that actually ran in that slot.
            for slot, entries in progress['slotHistory'].items():
                text=(tmp/'slots'/(slot+'.log')).read_text()
                for entry in entries:
                    self.assertIn(f"{entry['source']} running",text)
                others={n for n in names if n not in [e['source'] for e in entries]}
                for other in others:
                    self.assertNotIn(f'{other} running',text)

    def test_slot_logs_do_not_leak_between_reused_slots(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            install_slot_logs(tmp/'slots')
            def make(name):
                def run():
                    print(f'{name} line', flush=True)
                    time.sleep(0.08)
                return run
            # One slot must handle two sequential sources without mixing them.
            parallel_collect({n:make(n) for n in ['Alpha','Beta']}, tmp/'progress.json', workers=1)
            progress=json.loads((tmp/'progress.json').read_text())
            self.assertEqual(list(progress['slotHistory'].keys()),['1'])
            text=(tmp/'slots'/'1.log').read_text()
            self.assertIn('Alpha line',text); self.assertIn('Beta line',text)
            # slot2 never ran, so it must not exist at all.
            self.assertFalse((tmp/'slots'/'2.log').exists())

if __name__=='__main__': unittest.main()
