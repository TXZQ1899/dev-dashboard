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

    def test_single_run_processes_only_requested_app_and_keeps_previous_on_failure(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params): return [{'native_key_json':'{"appId":"7"}'}, {'native_key_json':'{"appId":"8"}'}]
        apps=[{'id':str(i),'name':'app'+str(i),'appType':'app','repository':'https://codeup.aliyun.com/org/repo.git'} for i in (7,8)]
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'sync_one', side_effect=ValueError('读取失败')) as call:
            target=Path(d)/'application-config/app7-7/other'
            target.mkdir(parents=True); (target/'existing.yml').write_text('old')
            results=[]
            counts=sync.run(Path(d),Store(),'v1',apps,[{'url':apps[0]['repository']}], 'cookie',lambda c,r: results.append(r),app_ids=['7'])
            self.assertEqual((counts['total'],counts['failed']), (1,1))
            self.assertEqual([x['appId'] for x in results], ['7'])
            self.assertEqual(call.call_args.args[0]['id'], '7')
            self.assertEqual((target/'existing.yml').read_text(), 'old')

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

    def test_directory_name_fallback_downloads_nested_resources(self):
        class Modules:
            def __init__(self, repo, cookie): pass
            def raw(self, path):
                return {'pom.xml': b'<project><packaging>pom</packaging><modules><module>tims-middle-producer</module><module>other</module></modules></project>',
                        'tims-middle-producer/pom.xml': b'<project><artifactId>producer</artifactId></project>',
                        'other/pom.xml': b'<project><artifactId>other</artifactId></project>',
                        'tims-middle-producer/src/main/resources/config/application-test.yml': b'key: value'}[path]
            def tree(self, path):
                return {'tims-middle-producer/src/main/resources': [{'type':'tree','path':path+'/config'}],
                        'tims-middle-producer/src/main/resources/config': [{'type':'blob','path':path+'/application-test.yml'}]}[path]
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'Codeup', Modules):
            result = sync.sync_one({'id':'1','name':'tims-middle-producer（废弃）','appType':'app'}, {}, 'cookie', Path(d))
            self.assertEqual(result['module'], 'tims-middle-producer')
            self.assertIn('deprecated suffix', result['moduleMatchBasis'])
            self.assertTrue((Path(d)/'test/resources/config/application-test.yml').is_file())

    def test_mapper_xml_is_excluded_but_mybatis_configuration_is_saved(self):
        class MapperClient:
            def __init__(self, repo, cookie): self.reads = []; self.trees = []
            def raw(self, path):
                self.reads.append(path)
                return {'pom.xml':b'<project><build><finalName>orders</finalName></build></project>',
                        'src/main/resources/mybatis-config.xml':b'<configuration><mappers/></configuration>',
                        'src/main/resources/queries.xml':b'<?xml version="1.0"?><mapper namespace="example.User"><select id="a"/></mapper>'}[path]
            def tree(self, path):
                self.trees.append(path)
                if path == 'src/main/resources':
                    return [{'type':'tree','path':path+'/mapper'}, {'type':'tree','path':path+'/mybatis'},
                            *[{'type':'blob','path':path+'/'+name} for name in ('UserMapper.xml','mybatis-config.xml','queries.xml')]]
                if path == 'src/main/resources/mybatis':
                    return [{'type':'tree','path':path+'/mappers'}]
                return [{'type':'blob','path':path+'/User.xml'}]
        client = MapperClient({}, '')
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'Codeup', return_value=client):
            result = sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d))
            self.assertEqual([f['path'] for f in result['files']], ['src/main/resources/mybatis-config.xml'])
            self.assertNotIn('src/main/resources/mapper/User.xml', client.reads)
            self.assertNotIn('src/main/resources/UserMapper.xml', client.reads)
            self.assertNotIn('src/main/resources/mapper', client.trees)
            self.assertNotIn('src/main/resources/mybatis/mappers', client.trees)
            self.assertTrue((Path(d)/'other/resources/mybatis-config.xml').exists())
            self.assertFalse((Path(d)/'other/resources/queries.xml').exists())

    def test_mapper_resource_root_is_skipped_but_module_named_mapper_is_not(self):
        class Client:
            def __init__(self, *args): self.trees = []
            def tree(self, path):
                self.trees.append(path)
                if path.endswith('/mybatis/mappers'): raise AssertionError('must not enumerate mapper resource root')
                return [{'type':'blob','path':path+'/mybatis-config.xml'}]
            def raw(self, path): return b'<configuration/>'
        client=Client()
        rules=[('mapper/src/main/resources/mybatis/mappers',['**/*'],[],False), ('mapper/src/main/resources',['**/*'],[],False)]
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',return_value=client), \
             patch.object(sync,'maven_modules',return_value=[('mapper','jar','finalName')]), patch.object(sync,'resource_rules',return_value=rules):
            result=sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d))
        self.assertEqual(client.trees,['mapper/src/main/resources'])
        self.assertEqual(len(result['files']),1)

    def test_static_and_template_directories_are_never_enumerated(self):
        class Client:
            def __init__(self): self.trees=[]; self.reads=[]
            def tree(self, path):
                self.trees.append(path)
                if path == 'static/src/main/resources':
                    return [{'type':'tree','path':path+'/'+name} for name in ('static','Template','templates','static-assets','config')] + [
                        {'type':'blob','path':path+'/static/nested/unexpected.xml'}]
                if path.endswith(('/static-assets','/config')):
                    return [{'type':'blob','path':path+'/application.yml'}]
                raise AssertionError('must not enumerate an excluded directory: '+path)
            def raw(self, path): self.reads.append(path); return b'key: value'
        client=Client(); events=[]
        def progress(*args): pass
        progress.trace=lambda event, **kwargs: events.append((event,kwargs))
        rules=[('static/src/main/resources/templates',['**/*'],[],False), ('static/src/main/resources',['**/*'],[],False)]
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'Codeup',return_value=client), \
             patch.object(sync,'maven_modules',return_value=[('static','jar','finalName')]), patch.object(sync,'resource_rules',return_value=rules):
            result=sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d), progress)
        self.assertEqual(set(client.trees), {'static/src/main/resources','static/src/main/resources/static-assets','static/src/main/resources/config'})
        self.assertEqual(len(result['files']),2)
        self.assertFalse(any('/static/nested/' in path for path in client.reads))
        self.assertEqual(sum(event == 'resource_skip' for event, _ in events),4)

    def test_directory_fallback_requires_uniqueness_and_yields_to_final_name(self):
        class Modules:
            def __init__(self, final=False): self.final = final
            def raw(self, path):
                if path == 'pom.xml':
                    return b'<project><modules><module>a/orders</module><module>b/orders</module><module>chosen</module></modules></project>'
                if path == 'chosen/pom.xml' and self.final:
                    return b'<project><build><finalName>orders</finalName></build></project>'
                return b'<project/>'
        candidates = sync.maven_modules(Modules(), 'orders')
        self.assertEqual({x[0] for x in candidates}, {'a/orders', 'b/orders'})
        with tempfile.TemporaryDirectory() as d, patch.object(sync, 'Codeup', return_value=Modules()):
            with self.assertRaisesRegex(ValueError, '无法唯一定位'):
                sync.sync_one({'id':'1','name':'orders','appType':'app'}, {}, 'cookie', Path(d))
        self.assertEqual(sync.maven_modules(Modules(True), 'orders'), [('chosen', 'jar', 'finalName')])

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

    def test_single_refresh_is_scoped_and_excludes_concurrent_batch(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object()
        worker.catalog_context = lambda: ('v1', b'{"apps":[{"id":"7","name":"orders"}]}')
        worker.config_sync_job = {'status':'ready', 'results':[{'appId':'old'}]}
        worker.config_single_job = {'status':'idle'}
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), \
             patch.object(service,'credentials',return_value={'aliyun':{'value':'cookie'}}), \
             patch.object(sync,'catalog_app_ids',return_value=['7','8']), \
             patch.object(service.threading,'Thread') as thread:
            service.write(Path(d)/'versions/v1/repositories.json', {'repos':[]})
            job = worker.start_single_config_sync({'appId':'7'})['job']
            self.assertEqual((job['appId'], job['status']), ('7','queued'))
            self.assertEqual(worker.config_sync_job['results'], [{'appId':'old'}])
            self.assertEqual(thread.call_args.kwargs['args'][0], '7')
            with self.assertRaisesRegex(ValueError, '同步正在进行'): worker.start_config_sync()
            with self.assertRaisesRegex(ValueError, '同步正在进行'): worker.start_single_config_sync({'appId':'7'})

    def test_single_runner_preserves_batch_job_and_reports_failure(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object()
        worker.config_single_job = {'status':'queued','appId':'7'}
        worker.config_sync_job = {'status':'partial','results':[{'appId':'8'}]}
        def run(*args, **kwargs):
            self.assertEqual(kwargs['app_ids'], ['7'])
            args[6]({}, {'appId':'7','status':'failed','error':'仓库不可见'})
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), patch.object(sync,'run',side_effect=run):
            worker._run_single_config_sync('7','v1',[],[],'cookie')
            self.assertEqual(worker.config_single_job['status'], 'failed')
            self.assertEqual(worker.config_single_job['result']['error'], '仓库不可见')
            self.assertEqual(worker.config_sync_job['results'], [{'appId':'8'}])

    def test_retry_keeps_ready_and_excluded_retries_other_results_and_pending(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object()
        worker.catalog_context = lambda: ('v1', b'{"apps":[]}')
        worker.config_sync_job = {'status':'failed','version':'v1','startedAt':'before',
            'scopeAppIds':['1','2','3','4','5','6'], 'progress':{'total':6,'completed':5}, 'results':[
                {'appId':'1','name':'ready','status':'ready','snapshotVersion':'old'},
                {'appId':'2','name':'partial','status':'partial','warnings':['warning']},
                {'appId':'3','name':'failed','status':'failed','error':'unavailable'},
                {'appId':'4','name':'skipped','status':'skipped','reason':'non-java'},
                {'appId':'5','name':'excluded','status':'failed','error':'仅支持 Codeup HTTPS 仓库'}]}
        worker.config_single_job = {'status':'idle'}
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), \
             patch.object(service,'credentials',return_value={'aliyun':{'value':'cookie'}}), \
             patch.object(sync,'catalog_app_ids',return_value=['1','2','3','4','5','6']), \
             patch.object(service.threading,'Thread') as thread:
            service.write(Path(d)/'versions/v1/repositories.json', {'repos':[]})
            service.write(Path(d)/'application-config-exclusions.json', {'schemaVersion':1,'applications':[{'appId':'5'}]})
            job = worker.start_config_sync({'retryFailed':True})['job']
            self.assertEqual([(r['appId'],r['status']) for r in job['results']], [('1','ready'),('5','skipped')])
            self.assertEqual(job['results'][0]['snapshotVersion'],'old')
            self.assertEqual(job['results'][1]['skipReason'],'USER_EXCLUDED')
            self.assertEqual(job['scopeAppIds'],['1','2','3','4','5','6'])
            self.assertEqual((job['progress']['total'],job['progress']['completed']), (6,2))
            self.assertEqual(thread.call_args.kwargs['args'][-1], ['5'])

    def test_batch_skips_excluded_and_retries_partial_failed_skipped_and_pending(self):
        class Store:
            source_key='source'
            def connection(self):
                class Context:
                    def __enter__(self): return None
                    def __exit__(self, *args): pass
                return Context()
            def execute(self, conn, sql, params):
                return [{'native_key_json':'{"appId":"'+str(i)+'"}'} for i in range(1,7)]
        apps=[{'id':str(i),'name':'app'+str(i),'appType':'app','repository':'https://codeup.aliyun.com/org/repo.git'} for i in range(1,7)]
        old=[{'appId':'1','name':'app1','status':'ready'}]
        calls=[]; results=[]
        def sync_one(app, repo, cookie, output, progress, cache):
            calls.append(app['id']); return {'appId':app['id'],'name':app['name'],'status':'ready'}
        with tempfile.TemporaryDirectory() as d, patch.object(sync,'sync_one',side_effect=sync_one):
            counts=sync.run(Path(d),Store(),'v1',apps,[{'url':apps[0]['repository']}], 'cookie',lambda c,r:results.append(r),
                            previous_results=old, scope_ids=['1','2','3','4','5','6'], excluded_ids=['5'])
        self.assertEqual(calls,['2','3','4','6'])
        self.assertEqual([r['appId'] for r in results if r['status']=='skipped'], ['5'])
        self.assertEqual((counts['completed'],counts['ready'],counts['skipped']), (6,5,1))

    def test_retry_completed_task_can_use_new_snapshot_without_repeating_ready(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object(); worker.config_single_job = {'status':'idle'}
        worker.catalog_context = lambda: ('v2', b'{"apps":[]}')
        worker.config_sync_job = {'status':'partial','version':'v1','progress':{'total':3,'completed':3},'results':[
            {'appId':'1','name':'ready','status':'ready','snapshotVersion':'v1'},
            {'appId':'2','name':'partial','status':'partial'}, {'appId':'3','name':'skipped','status':'skipped'}]}
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), \
             patch.object(service,'credentials',return_value={'aliyun':{'value':'cookie'}}), patch.object(service.threading,'Thread'):
            service.write(Path(d)/'versions/v2/repositories.json',{'repos':[]})
            job=worker.start_config_sync({'retryFailed':True})['job']
            self.assertEqual(job['version'],'v2')
            self.assertEqual([(r['appId'],r['snapshotVersion']) for r in job['results']],[('1','v1')])
            self.assertEqual(job['scopeAppIds'],['1','2','3'])

    def test_retry_interrupted_task_rejects_unknown_old_scope_after_snapshot_change(self):
        import service
        worker = service.Service.__new__(service.Service)
        worker.lock = threading.RLock(); worker.catalog_store = object(); worker.config_single_job = {'status':'idle'}
        worker.catalog_context = lambda: ('v2', b'{"apps":[]}')
        worker.config_sync_job = {'status':'failed','version':'v1','progress':{'total':2,'completed':1},'results':[{'appId':'1','status':'ready'}]}
        with tempfile.TemporaryDirectory() as d, patch.object(service,'DATA',Path(d)), patch.object(service,'credentials',return_value={'aliyun':{'value':'cookie'}}):
            service.write(Path(d)/'versions/v2/repositories.json',{'repos':[]})
            with self.assertRaisesRegex(ValueError,'未记录完整范围'): worker.start_config_sync({'retryFailed':True})

    def test_full_sync_exclusion_skips_before_reading_repository(self):
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
            counts=sync.run(Path(d),Store(),'v1',[{'id':'7','name':'app','appType':'app'}],[],'cookie',lambda c,r:results.append(r),excluded_ids=['7'])
        self.assertEqual(counts['skipped'],1)
        self.assertEqual(results[0]['skipReason'],'USER_EXCLUDED')

if __name__ == '__main__': unittest.main()
