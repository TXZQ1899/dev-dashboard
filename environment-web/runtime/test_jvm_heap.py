import unittest
import tempfile
import gzip
import hashlib
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import jvm_heap as j


OVERVIEW = '''<html><body><table>
<tr><td>Used heap dump</td><td>114.4 MB</td></tr>
<tr><td>Number of objects</td><td>1,928,768</td></tr>
<tr><td>Number of classes</td><td>26,639</td></tr>
<tr><td>Number of class loaders</td><td>3,389</td></tr>
<tr><td>Number of GC roots</td><td>4,046</td></tr>
<tr><td>JVM version</td><td>java version "1.8.0_101"</td></tr>
<tr><td>File length</td><td>1,777,000,000</td></tr>
</table></body></html>'''

TOP = '''<html><body>
<h2 id="i2"><img src="img/warning.gif" alt="Status: warning."> <a href="pages/a.html">org.springframework.boot.loader.LaunchedURLClassLoader @ 0x6c (71%)</a></h2>
<h2 id="i3"> <a href="pages/b.html">&lt;system class loader&gt; (24%)</a></h2>
</body></html>'''

SUSPECTS = '''<html><body>
<h1>Leak Suspects</h1>
<h2>Problem Suspect 1</h2>
<p>The classloader occupies 13,059,408 (10.89%) bytes.</p>
<h4>Keywords</h4>
<p>ignored</p>
</body></html>'''


class ParseTest(unittest.TestCase):
    def test_sizing_recommendation_uses_verified_samples_and_keeps_existing_limits(self):
        mib = 1024 ** 2
        report = {'taskId': 'stored', 'status': 'complete', 'runtime': {
            'flags': {'InitialHeapSize': 2048 * mib, 'MaxHeapSize': 3072 * mib},
            'dumpGc': {'status': 'single_full_gc',
                       'before': {'heapUsedBytes': int(734.3 * mib)},
                       'after': {'heapUsedBytes': int(157.7 * mib)}},
        }}
        recommendation = j.heap_sizing_recommendation(report)
        self.assertEqual((recommendation['xmsBytes'], recommendation['xmxBytes']), (1024 * mib, 2048 * mib))
        self.assertEqual(recommendation['taskId'], 'stored')
        report['runtime']['flags'] = {'InitialHeapSize': 256 * mib, 'MaxHeapSize': 2048 * mib}
        report['runtime']['dumpGc']['before']['heapUsedBytes'] = 300 * mib
        report['runtime']['dumpGc']['after']['heapUsedBytes'] = 100 * mib
        recommendation = j.heap_sizing_recommendation(report)
        self.assertEqual((recommendation['xmsBytes'], recommendation['xmxBytes']), (256 * mib, 768 * mib))
        report['runtime']['dumpGc']['status'] = 'no_full_gc'
        self.assertIsNone(j.heap_sizing_recommendation(report))

    def test_archive_application_requires_unique_matching_deployment(self):
        process = {'pid': 12, 'command': 'java -Dappid=marketing-center-offline -jar marketing-center-offline.jar'}
        app = {'id': 1380, 'name': 'marketing-center-offline', 'envs': {'PRODUCT': [{'ip': '10.0.0.1'}]}}
        resolved = j.archive_application(process, {'ip': '10.0.0.1'}, [app])
        self.assertEqual(resolved['appId'], '1380')
        self.assertEqual(resolved['confidence'], 'INFERRED')
        duplicate = {**app, 'id': 1381}
        self.assertEqual(j.archive_application(process, {'ip': '10.0.0.1'}, [app, duplicate])['confidence'], 'AMBIGUOUS')
        self.assertEqual(j.archive_application(process, {'ip': '10.0.0.2'}, [app])['confidence'], 'UNKNOWN')

    def test_verified_archive_survives_working_cleanup_and_is_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'jvm'
            folder = root / '20261125-165111-600251'
            folder.mkdir(parents=True)
            (folder / 'heap.hprof.gz').write_bytes(b'verified')
            (folder / 'heap.hprof').write_bytes(b'temporary')
            app = {'appId': '1380', 'appName': 'marketing-center-offline'}
            path = j.archive_dump(folder, app, '10.179.5.150', 29021)
            self.assertEqual(path.relative_to(root).as_posix(), 'dumps/1380_marketing-center-offline/2026-11-25/20261125-165111-600251.hprof.gz')
            j._cleanup(folder)
            self.assertEqual(path.read_bytes(), b'verified')
            self.assertFalse((folder / 'heap.hprof').exists())
            (folder / 'heap.hprof.gz').write_bytes(b'other')
            with self.assertRaises(FileExistsError):
                j.archive_dump(folder, app, '10.179.5.150', 29021)
            self.assertEqual(path.read_bytes(), b'verified')

    def test_unmatched_archive_uses_ip_and_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'jvm' / '20261125-165111-600251'
            folder.mkdir(parents=True)
            (folder / 'heap.hprof.gz').write_bytes(b'data')
            path = j.archive_dump(folder, {}, '10.179.5.150', 29021)
            self.assertEqual(path.parent.parent.name, '10.179.5.150_29021')
            self.assertEqual(path.parent.name, '2026-11-25')

    def test_analysis_failure_keeps_only_verified_dump(self):
        data = gzip.compress(b'example heap')
        digest = hashlib.sha256(data).hexdigest()

        def download(url, destination, size, progress, cancelled):
            destination.write_bytes(data)
            return digest

        for expected_digest, should_archive in ((digest, True), ('0' * 64, False)):
            with self.subTest(verified=should_archive), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / 'jvm' / '20261125-165111-600251'
                with patch.multiple(j,
                    open_terminal=Mock(return_value=(Mock(), 'root')),
                    _java_home=Mock(return_value=('/java/bin/java', '/java', '123')),
                    _runtime_metrics=Mock(return_value={'jstat': {}, 'flags': {}, 'threads': 1,
                                                        'rssKB': None, 'rssAnonKB': None, 'stackBytes': 1024}),
                    _dump=Mock(return_value=('/tmp/envscope-jvm.example', 12, None)),
                    _compress=Mock(return_value=(len(data), expected_digest)),
                    _start_server=Mock(return_value=8000), _download=download,
                    _mat_analyze=Mock(side_effect=j.HeapError('analyze', 'MAT_FAILED', '解析失败')),
                ):
                    report = j.analyze(folder, {'ip': '10.179.5.150'}, {'pid': 29021}, {}, {})
                self.assertEqual(report['error']['code'], 'MAT_FAILED' if should_archive else 'CHECKSUM_MISMATCH')
                self.assertFalse((folder / 'heap.hprof.gz').exists())
                self.assertFalse((folder / 'heap.hprof').exists())
                if should_archive:
                    self.assertEqual(gzip.decompress(Path(report['dump']['archivePath']).read_bytes()), b'example heap')
                else:
                    self.assertFalse((folder.parent / 'dumps').exists())

    def test_jstat(self):
        body = (' S0C S1C S0U S1U EC EU OC OU MC MU CCSC CCSU YGC YGCT FGC FGCT GCT\n'
                ' 5120.0 5632.0 0.0 5632.0 1386496.0 543.2 2796544.0 249.0 171432.0 164480.0 20480.0 19800.0 147945 3132.1 10 3.14 3135.2')
        values = j.parse_jstat(body)
        self.assertEqual(values['YGC'], 147945)
        self.assertEqual(values['OC'], 2796544.0)
        self.assertIsNone(j.parse_jstat('garbage'))

    def test_live_dump_collects_around_jmap_and_converts_gc_evidence(self):
        body = '''DIR=/tmp/envscope-jvm.12345678
START_BEFORE=123
---BEFORE
S0U S1U EU OU FGC FGCT
0 0 100 900 6 1.555
---DUMP
Heap dump file created
---AFTER
S0U S1U EU OU FGC FGCT
0 8 10 70 7 2.234
---END
START_AFTER=123
SIZE=100'''
        terminal = Mock()
        terminal.run.return_value = (0, body)
        _, _, comparison = j._dump(terminal, 1, '/java', Mock(), lambda: False, 'live', '123')
        command = terminal.run.call_args.args[0]
        subprocess.run(['sh', '-n', '-c', command], check=True)
        self.assertEqual(command.count('/java/bin/jstat -gc 1'), 2)
        first_sample, dump, second_sample = command.index('/java/bin/jstat'), command.index('-dump:live'), command.rindex('/java/bin/jstat')
        self.assertLess(first_sample, dump)
        self.assertLess(dump, second_sample)
        self.assertEqual(comparison['status'], 'single_full_gc')
        self.assertEqual(comparison['before']['oldUsedBytes'], 900 * 1024)
        self.assertEqual(comparison['after']['heapUsedBytes'], 88 * 1024)
        self.assertEqual(comparison['fullTimeDeltaSeconds'], 0.679)
        self.assertEqual(j.compare_dump_gc(body.replace('7 2.234', '6 1.555'))['status'], 'no_full_gc')
        self.assertEqual(j.compare_dump_gc(body.replace('7 2.234', '8 3.000'))['status'], 'multiple_full_gc')
        self.assertEqual(j.compare_dump_gc(body.replace('7 2.234', '- -'))['status'], 'unavailable')
        self.assertEqual(j.compare_dump_gc(body.replace('START_AFTER=123', 'START_AFTER=456'))['status'], 'unverified_process')

    def test_incomplete_jstat_does_not_invent_heap_or_gc_values(self):
        result = j.gc_metrics(j.parse_jstat('S0U S1U EU OU FGC\n- - 100 900 -'))
        self.assertEqual(result, {'oldUsedBytes': 900 * 1024})

    def test_flags_and_stack(self):
        flags = j.parse_vm_flags(' -XX:InitialHeapSize=4294967296 -XX:MaxHeapSize=4294967296 '
                                 '-XX:+UseParallelGC -XX:ThreadStackSize=512')
        self.assertEqual(flags['MaxHeapSize'], 4294967296)
        self.assertTrue(flags['UseParallelGC'])
        self.assertEqual(j.xss_bytes(flags, ''), 512 * 1024)
        self.assertEqual(j.xss_bytes({}, '-Xss256k'), 256 * 1024)
        self.assertEqual(j.xss_bytes({}, ''), 1024 * 1024)

    def test_rss_composition(self):
        jstat = {'S0C': 5120, 'S1C': 5632, 'EC': 1386496, 'OC': 2796544, 'MC': 171432}
        parts = j.rss_composition(5310428, jstat, 291, 1024 * 1024)
        by_key = {part['key']: part for part in parts}
        self.assertEqual(by_key['heap']['bytes'], (5120 + 5632 + 1386496 + 2796544) * 1024)
        self.assertEqual(by_key['threads']['bytes'], 291 * 1024 * 1024)
        self.assertEqual(by_key['threads']['basis'], 'estimated')
        self.assertGreater(by_key['native']['bytes'], 0)
        self.assertAlmostEqual(sum(part['percent'] for part in parts), 100.0, delta=0.3)
        self.assertEqual(j.rss_composition(None, jstat, 1, 1), [])

    def test_overview(self):
        heap = j.parse_overview(OVERVIEW)
        self.assertEqual(heap['objects'], 1928768)
        self.assertEqual(heap['classes'], 26639)
        self.assertEqual(heap['classLoaders'], 3389)
        self.assertEqual(heap['gcRoots'], 4046)
        self.assertEqual(heap['usedBytes'], int(114.4 * 1024 * 1024))
        self.assertEqual(heap['fileLength'], 1777000000)
        self.assertIn('1.8.0_101', heap['jvmVersion'])

    def test_top_and_suspects(self):
        top = j.parse_top_components(TOP)
        self.assertEqual(top[0], {'name': 'org.springframework.boot.loader.LaunchedURLClassLoader @ 0x6c', 'percent': 71})
        self.assertEqual(top[1], {'name': '<system class loader>', 'percent': 24})
        suspects = j.parse_suspects(SUSPECTS)
        self.assertEqual(suspects[0]['title'], '问题嫌疑 1')
        self.assertIn('10.89%', suspects[0]['detail'])
        self.assertNotIn('ignored', suspects[0]['detail'])

    def test_summary(self):
        report = {
            'heap': {'objects': 1928768, 'usedBytes': 114 * 1024 * 1024, 'classLoaders': 3389},
            'runtime': {'rssBytes': 5 * 1024 ** 3, 'gc': {'heapCommittedBytes': 4 * 1024 ** 3}},
            'rss': [{'key': 'heap', 'bytes': 4 * 1024 ** 3}],
            'suspects': [{'title': '问题嫌疑 1'}],
        }
        summary = j.summarize(report)
        self.assertIn('1,928,768', summary)
        self.assertIn('进程 RSS 5.0 GiB，JVM 已提交堆 4.0 GiB', summary)
        self.assertIn('1 条泄漏嫌疑', summary)


if __name__ == '__main__':
    unittest.main()
