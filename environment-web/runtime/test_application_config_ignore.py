import base64
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import application_check as check
from application_config import ignore
import application_files as files


class IgnorelistFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        env = patch.dict(os.environ, {'ENVSCOPE_DATA': str(self.data)})
        env.start()
        self.addCleanup(env.stop)


class IgnorelistTests(IgnorelistFixture):
    def test_default_rules_match_requested_paths_without_excluding_business_configs(self):
        base = '/opt/apache-tomcat_dolphin-switches'
        ignored = ['conf/catalina.properties', 'conf/jaspic-providers.xml',
                   'conf/logging.properties', 'conf/tomcat-users.xml', 'conf/web.xml',
                   'conf/yum/yum.conf', 'webapps/ROOT/WEB-INF/web.xml',
                   'webapps/ROOT/WEB-INF/classes/mybatis-config.xml',
                   'webapps/ROOT/WEB-INF/classes/mybatis-config',
                   'webapps/ROOT/WEB-INF/classes/mapper/nested/order.xml']
        kept = ['conf/server.xml', 'conf/context.xml', 'conf/web.xml.custom',
                'webapps/ROOT/WEB-INF/classes/application.properties',
                'webapps/ROOT/WEB-INF/classes/spring-datasource.xml',
                'webapps/ROOT/WEB-INF/classes/logback.xml',
                'webapps/ROOT/WEB-INF/classes/logging.properties',
                'webapps/ROOT/WEB-INF/classes/mapper-extra/order.xml']
        for path in ignored:
            with self.subTest(path=path):
                self.assertTrue(ignore.is_ignored(base+'/'+path))
        for path in kept:
            with self.subTest(path=path):
                self.assertFalse(ignore.is_ignored(base+'/'+path))
        # Resolved ROOT deployments can live outside catalina.base.
        self.assertTrue(ignore.is_ignored('/var/www/webapps/orders/WEB-INF/web.xml'))

    def test_volume_rules_are_additive_deduplicated_and_reloaded(self):
        custom = self.data/'application-config.ignorelist'
        custom.write_text('# operator rules\n\n*/private-config\n*/conf/web.xml\n')
        self.assertEqual(ignore.ignore_patterns().count('*/conf/web.xml'), 1)
        self.assertTrue(ignore.is_ignored('/opt/app/private-config/nested/application.yml'))
        self.assertTrue(ignore.is_ignored('/opt/app/conf/web.xml'))
        custom.write_text('*/replacement.properties\n')
        self.assertFalse(ignore.is_ignored('/opt/app/private-config/application.yml'))
        self.assertTrue(ignore.is_ignored('/opt/app/replacement.properties'))

    def test_ignored_configuration_in_saved_catalogue_is_rejected_before_reading(self):
        entry = {'kind': 'config', 'path': '/opt/app/WEB-INF/classes/mapper/order.xml'}
        with patch.object(files, 'run_script') as run:
            with self.assertRaisesRegex(ValueError, 'ignorelist'):
                files.read_file(None, {}, entry)
        run.assert_not_called()


@unittest.skipUnless(platform.system() == 'Linux', 'GNU Linux configuration probes')
class LinuxIgnorelistTests(IgnorelistFixture):
    def setUp(self):
        super().setUp()
        self.base = self.data/'apache-tomcat_orders'
        self.webinf = self.base/'webapps/ROOT/WEB-INF'
        self.config = self.webinf/'classes'
        self.pid = os.getpid()
        self.ticks = Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19]
        self.process = {'pid': self.pid, 'startTicks': self.ticks,
                        'command': 'java -Dcatalina.base='+str(self.base), 'cwd': str(self.base)}
        class Terminal:
            def run(self, command, timeout=45):
                result = subprocess.run(['bash', '-c', command], capture_output=True, text=True, timeout=timeout)
                return result.returncode, result.stdout
        self.terminal = Terminal()

    def write(self, relative, content='redis.address=10.99.0.9:6379\n'):
        path = self.base/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)

    def test_catalogue_and_endpoint_probe_skip_ignored_files_before_limits(self):
        # More than the endpoint probe's 20-file limit, created before kept files.
        for i in range(21):
            self.write(f'webapps/ROOT/WEB-INF/classes/mapper/nested/order-{i}.xml')
        for relative in ('conf/catalina.properties', 'conf/jaspic-providers.xml',
                         'conf/logging.properties', 'conf/tomcat-users.xml', 'conf/web.xml',
                         'conf/yum/yum.conf', 'webapps/ROOT/WEB-INF/web.xml',
                         'webapps/ROOT/WEB-INF/classes/mybatis-config.xml'):
            self.write(relative)
        kept = {self.write('conf/server.xml', '<Server/>'),
                self.write('conf/context.xml', '<Context/>'),
                self.write('webapps/ROOT/WEB-INF/classes/logback.xml', '<configuration/>'),
                self.write('webapps/ROOT/WEB-INF/classes/spring-datasource.xml', '<beans/>'),
                self.write('webapps/ROOT/WEB-INF/classes/application.properties',
                           'spring.datasource.url=jdbc:mysql://10.1.1.2:3307/orders\n')}
        roots = [str(self.base/'conf'), str(self.webinf)]
        with patch.object(files, 'roots_for', return_value={'config': roots, 'log': []}):
            catalog = files.catalogue(self.terminal, self.process)
        self.assertEqual({entry['path'] for entry in catalog['files']}, kept)
        # Use the same process-derived roots as the catalogue so nested mapper
        # files are reachable within the endpoint probe's bounded depth.
        with patch.object(check, 'config_roots', return_value=roots):
            result = check.collect_config(self.terminal, self.process)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([(x['host'], x['port']) for x in result['endpoints']], [('10.1.1.2', 3307)])
        self.assertFalse(any('limit' in warning for warning in result['warnings']))

    def test_ignored_files_do_not_fill_catalogue_limit(self):
        mapper = self.config/'mapper'
        mapper.mkdir(parents=True)
        for i in range(1001):
            (mapper/f'{i}.xml').touch()
        kept = self.write('webapps/ROOT/WEB-INF/classes/application.properties')
        os.utime(kept, (1, 1))
        with patch.object(files, 'roots_for', return_value={'config': [str(self.webinf)], 'log': []}):
            catalog = files.catalogue(self.terminal, self.process)
        self.assertEqual([entry['path'] for entry in catalog['files']], [kept])
        self.assertFalse(any('1000' in warning for warning in catalog['warnings']))

    def test_custom_patterns_are_literal_shell_arguments_and_logs_are_unaffected(self):
        injected = self.data/'should-not-exist'
        custom = self.data/'application-config.ignorelist'
        custom.write_text("*/private dir\n*/quote'$(touch "+str(injected)+")*.properties\n*/conf/server.xml\n")
        self.write('conf/server.xml')
        self.write('conf/private dir/application.properties')
        kept = self.write('conf/application.properties')
        # Log filename filtering still applies; config ignore rules must not
        # prune an otherwise valid log below the ignored "private dir".
        self.write('logs/WEB-INF/web.xml')
        logs = {self.write('logs/WEB-INF/web.log'), self.write('logs/private dir/app.log'),
                self.write('logs/app.log')}
        with patch.object(files, 'roots_for', return_value={
                'config': [str(self.base/'conf')], 'log': [str(self.base/'logs')]}):
            catalog = files.catalogue(self.terminal, self.process)
        self.assertFalse(injected.exists())
        self.assertEqual([entry['path'] for entry in catalog['files'] if entry['kind'] == 'config'], [kept])
        self.assertEqual({entry['path'] for entry in catalog['files'] if entry['kind'] == 'log'}, logs)
        body = files.run_script(self.terminal, 'application_files.sh', self.pid, self.ticks,
                                'list', 'config', str(self.base/'conf/private dir'))
        payload = body.split('FILE_BATCH_BEGIN', 1)[1].split('\n', 1)[1].split('FILE_BATCH_END', 1)[0]
        self.assertEqual(base64.b64decode(''.join(payload.split())), b'')


if __name__ == '__main__':
    unittest.main()
