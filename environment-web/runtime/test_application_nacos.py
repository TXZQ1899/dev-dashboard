import os
import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

import application_files as files
import application_nacos as nacos
import application_check as check

YAML = '''nacos:
  config:
    bootstrap:
      enable: true
    data-id: dolphin-webhook-prod.yaml
    group: DEFAULT_GROUP
    server-addr: 192.0.2.10:8848
    namespace: test-namespace
  enabled: true
  dataId: "unrelated:one,unrelated:two"
'''


class NacosTests(unittest.TestCase):
    def test_declaration_uses_config_identity_separately_from_business_dataids(self):
        result = nacos.declaration(YAML)
        self.assertEqual(result['dataId'], 'dolphin-webhook-prod.yaml')
        self.assertEqual(result['namespace'], 'test-namespace')
        self.assertIsNone(nacos.declaration(YAML.replace('dolphin-webhook-prod.yaml', '${data.id}')))
        self.assertIsNone(nacos.declaration(YAML.replace('  enabled: true', '  enabled: false')))
        self.assertIsNone(nacos.declaration(YAML.replace('    group:', '    data-id: other.yaml\n    group:')))

    def test_business_references_require_explicit_coordinates(self):
        resolved, warnings = nacos.business_declarations(YAML)
        self.assertEqual(resolved, [])
        self.assertEqual(len(warnings), 2)
        sample = YAML.replace('  dataId: "unrelated:one,unrelated:two"',
                              '  nacosUrl: 192.0.2.20:8848\n  namespace: business\n  group: BIZ\n  dataId: "one,two,ambiguous:thing"')
        resolved, warnings = nacos.business_declarations(sample)
        self.assertEqual([(x['dataId'], x['group'], x['namespace']) for x in resolved],
                         [('one', 'BIZ', 'business'), ('two', 'BIZ', 'business'), ('ambiguous:thing', 'BIZ', 'business')])
        self.assertEqual(warnings, [])

    def test_group_lookup_keeps_colon_and_requires_unique_complete_result(self):
        config = {'address':'192.0.2.1:8848', 'dataId':'service:variant', 'group':'',
                  'namespace':'ns', 'username':'', 'password':''}
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return self.data
        class Opener:
            def __init__(self, data): self.data = data
            def open(self, url, timeout): self.url = url; return Response(self.data)
        import json
        rows = [{'dataId':'service:variant', 'group':'BIZ', 'tenant':'ns'}]
        opener = Opener(json.dumps({'totalCount':1, 'pageItems':rows}).encode())
        with patch.object(nacos, 'build_opener', return_value=opener):
            resolved = nacos.resolve_group(config)
        self.assertEqual(resolved['group'], 'BIZ')
        self.assertIn('dataId=service%3Avariant', opener.url)
        rows.append({'dataId':'service:variant', 'group':'OTHER', 'tenant':'ns'})
        for result in ({'totalCount':2, 'pageItems':rows}, {'totalCount':3, 'pageItems':rows}):
            with patch.object(nacos, 'build_opener', return_value=Opener(json.dumps(result).encode())):
                with self.assertRaises(ValueError): nacos.resolve_group(config)

    def test_business_declarations_keep_all_literal_ids_without_group(self):
        sample = YAML.replace('  dataId:', '  nacosUrl: 192.0.2.20:8848\n  namespace: business\n  dataId:')
        resolved, warnings = nacos.business_declarations(sample)
        self.assertEqual([(x['dataId'], x['group']) for x in resolved], [('unrelated:one', ''), ('unrelated:two', '')])
        self.assertEqual(warnings, [])

    def test_business_fetches_continue_after_one_failure(self):
        sample = YAML.replace('  dataId: "unrelated:one,unrelated:two"',
                              '  nacosUrl: 192.0.2.20:8848\n  namespace: business\n  group: BIZ\n  dataId: "one,two"')
        result = {'files':[{'id':'a','kind':'config','path':'/a/b/application.yml'}], 'warnings':[]}
        def fetch(config):
            if config['dataId'] == 'one': raise ValueError('HTTP 404')
            return b'data'
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(files, 'read_file', return_value={'content':sample, 'truncated':False}), \
             patch.object(nacos, 'fetch', side_effect=fetch):
            files.collect_nacos(None, {}, result, directory)
        self.assertEqual(len(result['files']), 3)
        self.assertTrue(any('one；HTTP 404' in w for w in result['warnings']))

    def test_business_group_lookup_and_catalogue(self):
        sample = YAML.replace('  dataId:', '  nacosUrl: 192.0.2.20:8848\n  namespace: business\n  dataId:')
        result = {'files':[{'id':'a','kind':'config','path':'/a/b/application.yml'}], 'warnings':[]}
        def resolve(config): return config | {'group':'BIZ'} if not config['group'] else config
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(files, 'read_file', return_value={'content':sample, 'truncated':False}), \
             patch.object(nacos, 'resolve_group', side_effect=resolve), \
             patch.object(nacos, 'fetch', return_value=b'key: value'):
            files.collect_nacos(None, {}, result, directory)
        paths = [e['path'] for e in result['files']]
        self.assertTrue(any('BIZ / unrelated:one' in p for p in paths))
        self.assertTrue(any('BIZ / unrelated:two' in p for p in paths))
        self.assertTrue(any('唯一匹配' in w for w in result['warnings']))

    def test_fetch_builds_bounded_api_request(self):
        config = nacos.declaration(YAML)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'redis.host: 10.1.2.3\n'
        class Opener:
            def open(self, url, timeout):
                self.url = url
                self.timeout = timeout
                return Response()
        opener = Opener()
        with patch.object(nacos, 'build_opener', return_value=opener):
            self.assertIn(b'redis', nacos.fetch(config))
        self.assertIn('/nacos/v1/cs/configs?', opener.url)
        self.assertIn('dataId=dolphin-webhook-prod.yaml', opener.url)
        self.assertIn('tenant=test-namespace', opener.url)
        self.assertEqual(opener.timeout, 5)
        with self.assertRaises(ValueError): nacos.fetch(config | {'address':'http://host:8848/other'})

    def test_catalogue_persists_and_previews_redacted(self):
        entry = {'id':'a', 'kind':'config', 'path':'/app/WEB-INF/classes/application-prod.yml'}
        result = {'files':[entry], 'warnings':[]}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(files, 'read_file', return_value={'content':YAML, 'truncated':False}), \
             patch.object(nacos, 'fetch', return_value=b'password: secret\nredis.host: 10.1.2.3\n'):
            files.collect_nacos(None, {}, result, directory)
            item = result['files'][1]
            saved = Path(directory)/item['storageName']
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertIn('secret', saved.read_text())
            preview = files.read_nacos(item, directory)
            self.assertNotIn('secret', preview['content'])
            self.assertTrue(preview['redacted'])
            with self.assertRaises(ValueError): files.read_nacos(item | {'storageName':'../secret'}, directory)

    def test_detection_extracts_nacos_endpoints_without_persisting_content(self):
        encode = lambda s: base64.b64encode(s.encode()).decode()
        path = '/var/www/webapps/orders/WEB-INF/classes/application-prod.yml'
        class Terminal:
            def run(self, *args, **kwargs):
                return 0, 'CONFIG_FILE\t'+encode(path)+'\t'+encode(YAML)+'\nCONFIG_DONE'
        with patch.object(check, 'config_roots', return_value=['/var/www/webapps/orders/WEB-INF']), \
             patch.object(nacos, 'fetch', return_value=b'redis:\n  host: 192.0.2.25\n  port: 6379\npassword: private\n'):
            result = check.collect_config(Terminal(), {'command':'java'})
        self.assertIn(('192.0.2.25', 6379), [(entry['host'], entry['port']) for entry in result['endpoints']])
        self.assertNotIn('private', str(result))
        self.assertTrue(any('Nacos /' in entry['file'] for entry in result['endpoints']))

    def test_failed_fetch_reports_and_does_not_save(self):
        result = {'files':[{'id':'a','kind':'config','path':'/a/b/application.yml'}], 'warnings':[]}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(files, 'read_file', return_value={'content':YAML, 'truncated':False}), \
             patch.object(nacos, 'fetch', side_effect=ValueError('Nacos 配置读取失败（HTTP 403）')):
            files.collect_nacos(None, {}, result, directory)
            self.assertEqual(len(result['files']), 1)
            self.assertTrue(any('HTTP 403' in warning for warning in result['warnings']))
            self.assertEqual(os.listdir(directory), [])


if __name__ == '__main__': unittest.main()
