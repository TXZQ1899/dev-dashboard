import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message
import application_config_sync as sync

ROOT = b'<project><modules><module>service</module><module>web</module></modules></project>'
SERVICE = b'<project><build><finalName>orders</finalName></build></project>'
WEB = b'<project><packaging>war</packaging><build><finalName>orders</finalName></build></project>'
BOOTSTRAP = b'''spring:\n  application:\n    name: orders\n  profiles:\n    active: test\n---\nspring:\n  profiles: test\n  cloud:\n    nacos:\n      config:\n        server-addr: 192.0.2.1:8848\n        namespace: test-ns\n        file-extension: yaml\n---\nspring:\n  profiles: prod\n  cloud:\n    nacos:\n      config:\n        server-addr: 192.0.2.2:8848\n        namespace: prod-ns\n        file-extension: yaml\n'''
class Client:
    def __init__(self, repo, cookie): pass
    def raw(self, path):
        return {'pom.xml':ROOT, 'service/pom.xml':SERVICE, 'web/pom.xml':WEB,
                'web/src/main/resources/bootstrap.yml':BOOTSTRAP,
                'web/src/main/resources/application-test.yml':b'key: value'}[path]
    def tree(self, path):
        return [{'type':'blob','path':path+'/bootstrap.yml'}, {'type':'blob','path':path+'/application-test.yml'}]

class ConfigSyncTest(unittest.TestCase):
    def test_war_match_profiles_and_paths(self):
        calls=[]
        def fetch(config):
            calls.append((config['dataId'], config['address'], config['namespace']))
            if config['dataId'] in ('orders', 'orders.yaml'): raise ValueError('Nacos 配置读取失败（HTTP 404）')
            return b'password: secret'
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',Client), patch.object(sync.nacos,'fetch',side_effect=fetch):
            result=sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
            self.assertEqual(result['module'],'web')
            self.assertTrue((Path(d)/'test/resources/application-test.yml').is_file())
            self.assertTrue((Path(d)/'other/resources/bootstrap.yml').is_file())
            self.assertEqual({(x[0],x[1]) for x in calls if x[0].endswith(('-test.yaml','-prod.yaml'))}, {('orders-test.yaml','192.0.2.1:8848'),('orders-prod.yaml','192.0.2.2:8848')})
            self.assertEqual(result['status'],'ready')
            self.assertEqual(len(result['missingCandidates']),4)
            for file in Path(d).rglob('*'):
                if file.is_file(): self.assertEqual(file.stat().st_mode & 0o777,0o600)

    def test_all_inferred_nacos_candidates_missing_is_partial(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',Client), \
             patch.object(sync.nacos,'fetch',side_effect=ValueError('Nacos 配置读取失败（HTTP 404）')):
            result=sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
            self.assertEqual(result['status'],'partial')
            self.assertTrue(any('推导的 Data ID 未找到' in w for w in result['warnings']))

    def test_explicit_missing_nacos_identity_stays_partial(self):
        class ExplicitClient(Client):
            def raw(self, path):
                if path.endswith('bootstrap.yml'):
                    return b'nacos:\n  config:\n    server-addr: 192.0.2.1:8848\n    data-id: explicit.yaml\n'
                return super().raw(path)
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',ExplicitClient), \
             patch.object(sync.nacos,'fetch',side_effect=ValueError('Nacos 配置读取失败（HTTP 404）')):
            result=sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
            self.assertEqual(result['status'],'partial')
            self.assertTrue(any('explicit.yaml' in w for w in result['warnings']))

    def test_explicit_spring_data_id_uses_matching_profile(self):
        class NamedClient(Client):
            def raw(self, path):
                if path.endswith('bootstrap.yml'):
                    return BOOTSTRAP.replace(b'        file-extension: yaml', b'        data-id: common-${spring.profiles.active:test}.yaml\n        file-extension: yaml')
                return super().raw(path)
        calls=[]
        def fetch(config):
            calls.append((config['dataId'],config['address']))
            return b'value: ok'
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',NamedClient), patch.object(sync.nacos,'fetch',side_effect=fetch):
            sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
        self.assertIn(('common-test.yaml','192.0.2.1:8848'),calls)
        self.assertIn(('common-prod.yaml','192.0.2.2:8848'),calls)
        self.assertNotIn(('common-test.yaml','192.0.2.2:8848'),calls)

    def test_shared_dataids_are_explicit_and_profile_scoped(self):
        class SharedClient(Client):
            def raw(self, path):
                if path.endswith('bootstrap.yml'):
                    return BOOTSTRAP.replace(b'        file-extension: yaml', b'        group: APP_GROUP\n        shared-dataids: common-${spring.profiles.active:test}.properties\n        refreshable-dataids: common-${spring.profiles.active:test}.properties,not-declared.properties\n        file-extension: yaml')
                return super().raw(path)
        calls=[]
        def fetch(config):
            calls.append((config['dataId'],config['address'],config['group']))
            if not config['dataId'].startswith('common-'):
                raise ValueError('Nacos 配置读取失败（HTTP 404）')
            return b'value: ok'
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',SharedClient), patch.object(sync.nacos,'fetch',side_effect=fetch):
            result=sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
        self.assertEqual(result['status'],'ready')
        self.assertIn(('common-test.properties','192.0.2.1:8848','DEFAULT_GROUP'),calls)
        self.assertIn(('common-prod.properties','192.0.2.2:8848','DEFAULT_GROUP'),calls)
        self.assertFalse(any(c[0]=='not-declared.properties' for c in calls))

    def test_progress_reports_current_application_and_phase(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params): return [{'native_key_json':'{"appId":"7"}'}]
        app={'id':'7','name':'orders','appType':'app','repository':'https://codeup.aliyun.com/org/repo.git'}
        activity=[]
        def simulate(app, repo, cookie, output, progress, cache):
            progress('下载仓库配置', 2, 5)
            return {'status':'ready'}
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'sync_one', side_effect=simulate):
            sync.run(Path(d), Store(), 'v1', [app], [{'url':app['repository']}], 'cookie', lambda *args: None, activity.append)
        self.assertEqual(activity[-1], {'appId':'7','name':'orders','phase':'下载仓库配置','completed':2,'total':5})

    def test_resume_retains_completed_and_processes_only_remaining(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params):
                return [{'native_key_json':'{"appId":"7"}'},{'native_key_json':'{"appId":"8"}'}]
        apps=[{'id':'7','name':'done','appType':'app','repository':'https://codeup.aliyun.com/org/repo.git'},
              {'id':'8','name':'front','appType':'node'}]
        old=[{'appId':'7','name':'done','status':'ready'}]; updates=[]
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'sync_one', side_effect=AssertionError('must not repeat completed')):
            counts=sync.run(Path(d),Store(),'v1',apps,[], 'cookie',lambda c,r: updates.append(r),previous_results=old)
        self.assertEqual(counts,{'total':2,'completed':2,'ready':1,'partial':0,'failed':0,'skipped':1})
        self.assertEqual([r['appId'] for r in updates], ['8'])

    def test_repository_cache_is_bounded_and_expires(self):
        cache = sync.RepositoryCache(limit=5, ttl=10)
        with patch.object(sync.time, 'monotonic', return_value=100):
            cache.put('first', b'123')
            cache.put('second', b'45')
            self.assertEqual(cache.get('first'), b'123')
            cache.put('third', b'67')
            self.assertIsNone(cache.get('second'))
        with patch.object(sync.time, 'monotonic', return_value=111):
            self.assertIsNone(cache.get('first'))

    def test_ambiguous_match_not_guessed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',Client):
            with self.assertRaisesRegex(ValueError,'无法唯一定位'):
                sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d))

    def test_non_deployable_aggregator_does_not_mask_unique_jar(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',Client), \
             patch.object(sync,'maven_modules',return_value=[('.', 'pom', 'finalName'), ('web','jar','finalName')]), \
             patch.object(sync.nacos,'fetch',return_value=b'config'):
            result=sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d))
            self.assertEqual(result['module'],'web')

    def test_explicit_artifact_exec_name_is_supported(self):
        class ExecClient:
            def __init__(self, final_name): self.final_name = final_name
            def raw(self, path):
                return ('<project><artifactId>orders</artifactId><build><finalName>'+
                        self.final_name+'</finalName></build></project>').encode()
        matches = sync.maven_modules(ExecClient('${project.artifactId}-exec'), 'orders')
        self.assertEqual(matches, [('.', 'jar', 'explicit finalName matches application + -exec')])
        self.assertEqual(sync.maven_modules(ExecClient('${project.artifactId}'), 'orders'), [('.', 'jar', 'finalName resolves to artifactId')])
        self.assertEqual(sync.maven_modules(ExecClient('${unknown}-exec'), 'orders'), [])
        class DifferentArtifact:
            def raw(self, path): return b'<project><artifactId>host</artifactId><build><finalName>orders-exec</finalName></build></project>'
        self.assertEqual(sync.maven_modules(DifferentArtifact(), 'orders'), [('.', 'jar', 'explicit finalName matches application + -exec')])
        class ParentArtifact:
            def raw(self, path): return b'<project><parent><artifactId>orders</artifactId></parent><artifactId>host</artifactId><build><finalName>app-${parent.artifactId}-exec</finalName></build></project>'
        self.assertEqual(sync.maven_modules(ParentArtifact(), 'app-orders'), [('.', 'jar', 'explicit finalName matches application + -exec')])

    def test_paths_and_host_are_restricted(self):
        for path in ('../x', '/etc/passwd', 'a/../b', 'a\\b'):
            with self.assertRaises(ValueError): sync.safe_relative(path)
        with self.assertRaises(ValueError): sync.Codeup({'url':'https://example.com/repo','codeupId':'1'},'secret')
        self.assertEqual(sync.group_for('application-test_uk.yml'),'other')
        self.assertEqual(sync.group_for('test/application.yml'),'test')
        self.assertTrue(sync.ant_match('bootstrap.yml', '**/*'))
        self.assertTrue(sync.ant_match('a/b/bootstrap.yml', '**/*'))
        self.assertFalse(sync.ant_match('a/private.yml', '**/public*.yml'))

    def test_declared_maven_resource_directories_and_filters(self):
        class MavenResources:
            def raw(self, path):
                return b'<project><build><resources><resource><directory>src</directory><includes><include>**/*.properties</include></includes><excludes><exclude>**/private.properties</exclude></excludes></resource></resources></build></project>'
        rules=sync.resource_rules(MavenResources(),'web')
        self.assertEqual(rules,[('web/src',['**/*.properties'],['**/private.properties'],False)])
        self.assertTrue(sync.ant_match('prod/application.properties',rules[0][1][0]))
        self.assertTrue(sync.ant_match('private.properties',rules[0][2][0]))

    def test_non_java_is_skipped_without_directory_or_network(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params): return [{'native_key_json':'{"appId":"7"}'}]
        results=[]
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',side_effect=AssertionError('unexpected network')):
            counts=sync.run(Path(d), Store(), 'v1', [{'id':'7','name':'front','appType':'node'}], [], 'cookie', lambda c,r: results.append(r))
            self.assertEqual(counts['skipped'],1)
            self.assertEqual(results[0]['status'],'skipped')
            self.assertFalse((Path(d)/'application-config/front-7').exists())

    def test_repository_forbidden_is_skipped_but_not_found_is_failure(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params): return [{'native_key_json':'{"appId":"7"}'}]
        app={'id':'7','name':'orders','appType':'app','repository':'https://codeup.aliyun.com/org/repo.git'}
        repo={'url':app['repository']}
        for error, status in ((sync.CodeupPermissionDenied('无权限'),'skipped'),(ValueError('未找到或不可见'),'failed')):
            results=[]
            with tempfile.TemporaryDirectory() as d, patch.object(sync,'sync_one',side_effect=error):
                counts=sync.run(Path(d), Store(), 'v1', [app], [repo], 'cookie', lambda c,r: results.append(r))
            self.assertEqual(results[0]['status'],status)
            self.assertEqual(counts[status],1)
        results=[]
        denied_repo={**repo,'access':'暂无权限访问','inCodeup':False}
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'sync_one',side_effect=AssertionError('unexpected network')):
            counts=sync.run(Path(d), Store(), 'v1', [app], [denied_repo], 'cookie', lambda c,r: results.append(r))
        self.assertEqual(counts['skipped'],1)
        self.assertEqual(results[0]['skipReason'],'REPOSITORY_FORBIDDEN')

    def test_properties_rejects_continuation(self):
        self.assertIn('spring.application.name: orders', sync.properties_as_yaml('spring.application.name=orders'))
        with self.assertRaises(ValueError): sync.properties_as_yaml('nacos.config.server-addr=value\\\ncontinued')

    def test_properties_indexed_nacos_extensions_are_profile_specific(self):
        def content(address, namespace):
            return ('nacos.config.server-addr='+address+'\n'
                    'nacos.config.namespace='+namespace+'\n'
                    'nacos.config.data-id=app.properties\n'
                    'nacos.config.group=DEFAULT_GROUP\n'
                    'nacos.config.ext-config[0].data-id=extra.properties\n'
                    'nacos.config.ext-config[0].auto-refresh=true\n'
                    'nacos.config.ext-config[1].data-id=other.properties\n'
                    'nacos.config.ext-config[1].group=OTHER\n').encode()
        class PropertiesClient(Client):
            def tree(self, path):
                return [{'type':'blob','path':path+'/application-test.properties'}, {'type':'blob','path':path+'/application-prod.properties'}]
            def raw(self, path):
                if path.endswith('application-test.properties'): return content('192.0.2.1:8848','test-ns')
                if path.endswith('application-prod.properties'): return content('192.0.2.2:8848','prod-ns')
                return super().raw(path)
        calls=[]
        def fetch(config):
            calls.append((config['address'],config['namespace'],config['group'],config['dataId']))
            return b'value: ok'
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',PropertiesClient), patch.object(sync.nacos,'fetch',side_effect=fetch):
            result=sync.sync_one({'id':'1','name':'orders','appType':'war8'}, {}, 'cookie', Path(d))
        self.assertEqual(result['status'],'ready')
        self.assertIn(('192.0.2.1:8848','test-ns','DEFAULT_GROUP','extra.properties'), calls)
        self.assertIn(('192.0.2.2:8848','prod-ns','OTHER','other.properties'), calls)
        self.assertIn(('192.0.2.2:8848','prod-ns','DEFAULT_GROUP','app.properties'), calls)

    def test_local_placeholders_never_cross_profiles_or_resolve_duplicates(self):
        text = '''spring:\n  profiles: test\n  cloud:\n    nacos:\n      config:\n        server-addr: ${mc.nacos.server-addr}\nmc:\n  nacos:\n    server-addr: test.example:8848\n---\nspring:\n  profiles: prod\n  cloud:\n    nacos:\n      config:\n        server-addr: ${mc.nacos.server-addr}\nmc:\n  nacos:\n    server-addr: prod.example:8848\n'''
        result = sync.resolve_local_placeholders(text)
        docs = result.split('\n---\n')
        self.assertIn('server-addr: test.example:8848', docs[0])
        self.assertNotIn('prod.example', docs[0])
        self.assertIn('server-addr: prod.example:8848', docs[1])
        duplicate = text.replace('    server-addr: test.example:8848', '    server-addr: test.example:8848\n    server-addr: other.example:8848')
        self.assertIn('${mc.nacos.server-addr}', sync.resolve_local_placeholders(duplicate).split('\n---\n')[0])

    def test_expired_login_is_not_reported_as_bad_pom(self):
        class LoginClient:
            def raw(self, path): raise sync.CodeupAuthenticationError('登录失效')
        with self.assertRaises(sync.CodeupAuthenticationError):
            sync.maven_modules(LoginClient(), 'orders')
        headers = Message()
        headers['Location'] = 'https://account-devops.aliyun.com/logout?secret=not-logged'
        with patch.object(sync, 'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://codeup.aliyun.com/x',302,'',headers,None)
            client = sync.Codeup({'url':'https://codeup.aliyun.com/org/repo.git','codeupId':'1'}, 'cookie')
            with self.assertRaisesRegex(sync.CodeupAuthenticationError, '更新阿里云 Cookie') as error:
                client.raw('pom.xml')
            self.assertNotIn('not-logged', str(error.exception))

    def test_missing_id_reports_snapshot_access_state(self):
        repo = {'url':'https://codeup.aliyun.com/org/repo.git', 'codeupId':'', 'access':'暂无权限访问'}
        with patch.object(sync.Codeup, 'get', return_value=b'{"success":false,"errorCode":"FORBIDDEN"}'):
            with self.assertRaisesRegex(ValueError, '当前账号无权访问'):
                sync.Codeup(repo, 'cookie')
        with patch.object(sync.Codeup, 'get', return_value=b'{"success":false,"errorCode":"NOT_FOUND"}'):
            with self.assertRaisesRegex(ValueError, '未找到该仓库，或当前账号无法查看'):
                sync.Codeup(repo, 'cookie')
        with patch.object(sync.Codeup, 'get', return_value=b'{"success":true,"result":{"pathResource":{"type":"PROJECT","id":42}}}') as get:
            self.assertEqual(sync.Codeup(repo, 'cookie').id, '42')
            self.assertIn('path=%2Forg%2Frepo', get.call_args.args[0])

    def test_root_pom_read_error_preserves_cause(self):
        class MissingClient:
            def raw(self, path): raise ValueError('Codeup HTTP 403')
        with self.assertRaisesRegex(ValueError, '根 POM 读取失败：Codeup HTTP 403'):
            sync.maven_modules(MissingClient(), 'orders')

    def test_repository_without_root_pom_discovers_nested_modules(self):
        class NoRoot:
            def raw(self, path):
                if path == 'pom.xml': raise ValueError('Codeup HTTP 404')
                return b'<project><packaging>war</packaging><build><finalName>orders</finalName></build></project>'
            def tree(self, path):
                return {'':[{'path':'web','type':'tree'}, {'path':'src','type':'tree'}],
                        'web':[{'path':'web/pom.xml','type':'blob'}]}[path]
        self.assertEqual(sync.maven_modules(NoRoot(), 'orders'), [('web','war','finalName')])

    def test_unpaginated_tree_retains_files_and_warns(self):
        import json
        client = sync.Codeup({'url':'https://codeup.aliyun.com/org/repo.git','codeupId':'1'}, 'cookie')
        rows = [{'path':'config/'+str(i)+'.yml','type':'blob'} for i in range(149)]
        with patch.object(client, 'get', return_value=json.dumps(rows).encode()) as fetch:
            self.assertEqual(client.tree('config'), rows)
            self.assertEqual(fetch.call_count, 2)
        self.assertIn('完整性未确认', client.warnings[0])

    def test_job_reports_all_failed_and_expired_session(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock()
        worker.catalog_store = object()
        with tempfile.TemporaryDirectory() as d, patch.object(service, 'DATA', Path(d)):
            worker.config_sync_job = {'status':'running', 'results':[]}
            with patch.object(sync, 'run', return_value={'ready':0,'partial':0,'failed':2}):
                worker._run_config_sync('v1', [], [], 'cookie')
            self.assertEqual(worker.config_sync_job['status'], 'failed')
            worker.config_sync_job = {'status':'running', 'results':[]}
            with patch.object(sync, 'run', side_effect=sync.CodeupAuthenticationError('需要更新 Cookie')):
                worker._run_config_sync('v1', [], [], 'cookie')
            self.assertEqual(worker.config_sync_job['status'], 'failed')
            self.assertEqual(worker.config_sync_job['error'], '需要更新 Cookie')

    def test_resume_service_retains_prior_results_and_selective_retry(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object()
        worker.catalog_context = lambda: ('v1', b'{"apps":[]}')
        worker.config_sync_job = {'status':'failed','version':'v1','startedAt':'before',
            'progress':{'total':3}, 'results':[{'appId':'7','status':'ready'},{'appId':'8','status':'partial'}]}
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), \
             patch.object(service,'credentials',return_value={'aliyun':{'value':'cookie'}}), \
             patch.object(sync,'catalog_app_ids',return_value=['7','8','9']), \
             patch.object(service.threading,'Thread') as thread:
            service.write(Path(d)/'versions/v1/repositories.json', {'repos':[]})
            result=worker.start_config_sync({'resume':True,'retryAppIds':['8']})['job']
            self.assertEqual([r['appId'] for r in result['results']],['7'])
            self.assertEqual(result['progress']['completed'],1)
            self.assertEqual(result['progress']['total'],3)
            thread.return_value.start.assert_called_once()

if __name__ == '__main__': unittest.main()
