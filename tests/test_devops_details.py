import json
import tempfile
import unittest
from pathlib import Path

from export_devops import ExportError
from export_devops_details import export_application, log_text, parse_steps, safe_name

PAGE = '''<table><tr><td>resource</td><td>Deploy</td><td>10.0.0.1</td>
<td><span class="buildResourceClass" id="1_2_3"></span>
<script>triggerAction(2,3,1)</script></td></tr></table>'''


class FakeClient:
    base_url = 'http://example'
    def get(self, path, params):
        return {'deployList': [{'deployId': 4, 'sceneId': 5, 'ip': '10.0.0.1'},
                               {'deployId': 6, 'sceneId': 5, 'ip': '10.0.0.2'}]}

    def read(self, path, params=None, json_response=False):
        if path.endswith('searchPlandetail'):
            return PAGE
        if path.endswith('actionRun'):
            return {'status': 'SUCCESS', 'startTime': '2026-09-02 12:00:00'}
        if path.endswith('getLogContentFromFile'):
            return 'a<br/><b>ok</b> &lt;config&gt;'
        raise AssertionError(path)


class DetailTests(unittest.TestCase):
    def test_parse_and_no_execution(self):
        steps = parse_steps(PAGE)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['operation'], 'Deploy')
        self.assertEqual(steps[0]['build_id'], '1')
        with self.assertRaises(ExportError):
            parse_steps('<html>Login</html>')

    def test_log_preserves_escaped_markup(self):
        self.assertEqual(log_text('a<br/><b>b</b>&lt;b&gt;literal&lt;/b&gt; &amp;'), 'a\nb<b>literal</b> &')

    def test_safe_names(self):
        self.assertNotIn('/', safe_name('../../bad/name'))
        self.assertNotIn(':', safe_name('2026-09-02 12:00:00'))
        self.assertLess(len(safe_name('中文' * 150).encode()), 120)

    def test_multiple_servers_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'app'
            result = export_application(FakeClient(), {'id': 1, 'appName': 'app', 'jdkVersion': 'JDK18'}, folder)
            self.assertEqual(result['export_status'], 'complete')
            self.assertEqual(len(list(folder.rglob('*_log.txt'))), 6)
            data = json.loads((folder / 'summary.json').read_text())
            self.assertEqual(data['environments']['PRODUCT']['server_ips'], ['10.0.0.1', '10.0.0.2'])
            self.assertEqual(data['jdk_version'], 'JDK18')

    def test_failed_logs_are_reported(self):
        class FailingClient(FakeClient):
            def read(self, path, params=None, json_response=False):
                if path.endswith('getLogContentFromFile'):
                    raise ExportError('mock failure')
                return super().read(path, params, json_response)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'app'
            result = export_application(FailingClient(), {'id': 1, 'appName': 'app'}, folder)
            self.assertEqual(result['export_status'], 'partial')
            self.assertEqual(len(result['errors']), 6)
            self.assertFalse(list(folder.rglob('*_log.txt')))


if __name__ == '__main__':
    unittest.main()
