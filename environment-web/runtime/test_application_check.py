import copy
import base64
import gzip
import hashlib
import json
import subprocess
import threading
import unittest
from unittest.mock import patch

import application_check as c


def blob(text, label='sample-0'):
    encoded = base64.b64encode(gzip.compress(text.encode(), mtime=0)).decode()
    lines = '\n'.join(encoded[index:index+512] for index in range(0, len(encoded), 512))
    return 'CHECK_BLOB_BEGIN\t'+label+'\n'+lines+'\nCHECK_BLOB_END\n'


def process(pid=123, command='java -Dappid=orders -jar /var/www/webapps/orders/orders.jar'):
    return {'pid': pid, 'ppid': 1, 'name': 'java', 'kind': 'Java', 'command': command,
            'startedAt': '2026-10-15T01:00:00+00:00', 'startTicks': '54321',
            'cwd': '/var/www/webapps/orders', 'exe': '/usr/bin/java'}


def socket(local='10.0.0.1:8080', peer='10.0.0.2:50000', pid=123, cookie='a', sent=10, received=20, state='ESTAB'):
    return {'local': c.endpoint(local), 'peer': c.endpoint(peer), 'pids': [pid] if pid else [],
            'socketId': cookie, 'state': state, 'bytesSent': sent, 'bytesReceived': received}


def app(identifier, name, host='10.0.0.1', port='8080', env='TEST'):
    return {'id': identifier, 'name': name, 'port': port, 'envs': {env: [{'ip': host, 'port': port}]}}


class ParsingTests(unittest.TestCase):
    def test_chunked_probe_argument_preserves_shell_metacharacters_and_empty_values(self):
        values = ['', ('x' * 511 + "'\"; $(exit 91) `exit 92` \\" + '\n') * 4]
        for value in values:
            result = subprocess.run(['bash', '-c', "printf '%s' "+c.quote_probe_argument(value)],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, value)

    def test_read_only_probe_retries_a_single_terminal_timeout(self):
        class Terminal:
            calls = 0
            def run(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1: raise TimeoutError('private-secret')
                return 0, 'CHECK_SS_RC\t0\nCHECK_LISTEN_RC\t0\nCHECK_UID\t0\n'
        terminal = Terminal()
        sample = c.probe(terminal, 'target', 123)
        self.assertEqual(terminal.calls, 2)
        self.assertTrue(any('重试一次后成功' in warning for warning in sample['warnings']))
        self.assertNotIn('private-secret', json.dumps(sample))

    def test_truncated_batch_is_reported_but_keeps_the_frames_it_has(self):
        body = ('CHECK_CLOCK_BEGIN\t1000.0\n'
                'CHECK_SAMPLE_BEGIN\nCHECK_OBSERVED\t1000.5\t1000.6\n'
                'CHECK_SOCKETS_BEGIN\nCHECK_SOCKETS_END\nCHECK_SS_RC\t0\n'
                'CHECK_UID\t0\nCHECK_LISTEN_BEGIN\nCHECK_LISTEN_END\nCHECK_LISTEN_RC\t0\n'
                'CHECK_SAMPLE_END\n'
                'CHECK_TRUNCATED\tsample-budget\t1\t5\n'
                'CHECK_CLOCK_END\t1001.0\n')
        sample = c.parse_observations(body, 'peer_probe', 1000.4, 1002.0)
        self.assertEqual(len(sample['observations']), 1)
        self.assertTrue(any('仅完成 1/5 帧' in warning for warning in sample['warnings']))
        self.assertTrue(any('观测窗口被截断' in warning for warning in sample['warnings']))

    def test_compressed_frames_and_catalog_are_restored_before_parsing(self):
        row = base64.b64encode(b' 123 1 root 60 Tue Sep  1 10:00:00 2026 java 1.0 1.0 1024 2048 '
                               b'java -jar /var/www/webapps/orders/orders.jar').decode()
        catalog = ('CHECK_PROC_VERSION\t1\tCHECK_PROC\t123\t54321\t'+row+'\t'+
                   base64.b64encode(b'/var/www/webapps/orders').decode()+'\t'+
                   base64.b64encode(b'/usr/bin/java').decode()+'\t'+
                   base64.b64encode(b'net:[4026532000]').decode()+'\n')
        frame = ('CHECK_SAMPLE_BEGIN\nCHECK_OBSERVED\t1000.5\t1000.6\n'
                 'CHECK_SOCKETS_BEGIN\n'
                 'ESTAB 0 0 10.0.0.1:8080 10.0.0.2:50000 users:(("java",pid=123,fd=8)) ino:123\n'
                 '  cubic bytes_acked:10 bytes_received:20\n'
                 'CHECK_SOCKETS_END\nCHECK_SS_RC\t0\nCHECK_UID\t0\n'
                 'CHECK_LISTEN_BEGIN\nCHECK_LISTEN_END\nCHECK_LISTEN_RC\t0\n'
                 'CHECK_PROC_REF\t123\t54321\t1\nCHECK_SAMPLE_END\n')
        body = ('CHECK_CLOCK_BEGIN\t1000.0\n'+blob(frame)+blob(catalog, 'catalog')+
                'CHECK_CLOCK_END\t1001.0\n')
        class Terminal:
            def run(self, *_args, **_kwargs):
                return 0, body
        with patch.object(c.time, 'time', side_effect=[1000.2, 1002.0]):
            sample = c.probe(Terminal(), 'target', 123)
        self.assertEqual(len(sample['observations']), 1)
        self.assertEqual(sample['processes'][123]['startedAt'], '2026-09-01T10:00:00+00:00')
        self.assertEqual(sample['sockets'][0]['peer']['port'], 50000)
        self.assertEqual(sample['sockets'][0]['bytesSent'], 10)

    def test_corrupt_compressed_section_is_rejected_without_partial_results(self):
        body = ('CHECK_CLOCK_BEGIN\t1000.0\nCHECK_BLOB_BEGIN\tsample-0\n!!!not-base64!!!\n'
                'CHECK_BLOB_END\nCHECK_CLOCK_END\t1001.0\n')
        with self.assertRaises(c.ProbeError) as error:
            c.expand_transfer(body, 'target_probe')
        self.assertEqual(error.exception.details['code'], 'PROBE_OUTPUT_INCOMPLETE')
        self.assertEqual(error.exception.details['stage'], 'target_probe')

    def test_probe_command_wraps_only_the_uploaded_script(self):
        script = '#!/bin/sh\n'
        terminal = object.__new__(c.ProbeTerminal)
        terminal._socket_probe_path = '/tmp/envscope-socket-probe.x/probe.sh'
        terminal._socket_probe_digest = hashlib.sha256(script.encode()).hexdigest()
        command = terminal.probe_command(script, ['target', '2059', '', '', '', '5', '0.2'],
                                         'timeout 30')
        self.assertEqual(command, "timeout 30 bash /tmp/envscope-socket-probe.x/probe.sh "
                                  "target 2059 '' '' '' 5 0.2")

    def test_container_target_and_listener_modes_preserve_pid_identity_and_batch_arguments(self):
        terminal = object.__new__(c.ProbeTerminal)
        script = (c.PROBES/'application_sockets.sh').read_text()
        terminal._socket_probe_path = '/tmp/envscope-socket-probe.x/probe.sh'
        terminal._socket_probe_digest = hashlib.sha256(script.encode()).hexdigest()
        commands = []
        def run(command, **_):
            commands.append(command)
            return 0, 'CHECK_SS_RC\t0\nCHECK_LISTEN_RC\t0\nCHECK_UID\t0\n'
        terminal.run = run
        c.sampling_probe(terminal, 'container-target', {'pid': 500, 'startTicks': '54321'})
        self.assertTrue(commands[-1].endswith("target 500 '' 54321 '' 5 0.2"))
        c.sampling_probe(terminal, 'container-listeners', {'pid': 400, 'startTicks': '100', 'filter': '( sport = :6379 )'})
        self.assertTrue(commands[-1].endswith("listeners '( sport = :6379 )' 400 100"))

    def test_persistent_terminal_timeout_has_safe_stage_and_error_code(self):
        class Terminal:
            calls = 0
            def run(self, *_args, **_kwargs):
                self.calls += 1
                raise TimeoutError('password=private-secret')
        terminal = Terminal()
        with self.assertRaises(c.ProbeError) as error:
            c.probe(terminal, 'peer', 'sport = :7000')
        self.assertEqual(terminal.calls, 2)
        self.assertEqual(error.exception.details['code'], 'SOCKET_PROBE_TIMEOUT')
        self.assertEqual(error.exception.details['stage'], 'peer_probe')
        self.assertIsNone(error.exception.details['exitCode'])
        self.assertTrue(error.exception.details['diagnostics'])
        self.assertNotIn('private-secret', json.dumps(error.exception.details))

    def test_aborted_terminal_timeout_is_not_retried(self):
        class Terminal:
            _aborted = True
            calls = 0
            def run(self, *_args, **_kwargs):
                self.calls += 1
                raise TimeoutError('password=private-secret')
        terminal = Terminal()
        with self.assertRaises(c.ProbeError) as error:
            c.probe(terminal, 'target', 123)
        self.assertEqual(terminal.calls, 1)
        self.assertEqual(error.exception.details['code'], 'SOCKET_PROBE_TIMEOUT')
        self.assertIn('未在同一会话上重试', error.exception.details['message'])
        self.assertNotIn('private-secret', json.dumps(error.exception.details))

    def test_legacy_ss_headers_do_not_hide_connections_or_listeners(self):
        body = ('State Recv-Q Send-Q Local Address:Port Peer Address:Port\n'
                'ESTAB 0 0 10.0.0.1:8080 10.0.0.2:50000 users:(("java",pid=123,fd=8)) ino:123\n'
                '  cubic bytes_acked:120 bytes_received:240\n'
                'State Recv-Q Send-Q Local Address:Port Peer Address:Port\n'
                'LISTEN 0 128 *:8080 *:* users:(("java",pid=123,fd=7))\n')
        rows = c.parse_sockets(body)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['bytesReceived'], 240)
        self.assertEqual(rows[1]['state'], 'LISTEN')

    def test_probe_exit_code_and_shell_compatibility_diagnostic_are_retained(self):
        class Terminal:
            def run(self, *_args, **_kwargs):
                return 127, '_: line 23: ns[@]: unbound variable\nCookie: private-secret\n'
        with self.assertRaises(c.ProbeError) as error:
            c.probe(Terminal(), 'target', 123)
        details = error.exception.details
        self.assertEqual(details['stage'], 'target_probe')
        self.assertEqual(details['exitCode'], 127)
        self.assertTrue(any('Bash' in x for x in details['diagnostics']))
        self.assertNotIn('private-secret', json.dumps(details))

    def test_failed_ss_exposes_safe_diagnostic_without_raw_stderr(self):
        raw = base64.b64encode(b"ss: invalid option -- H\npassword=private-secret").decode()
        body = 'CHECK_SS_RC\t1\nCHECK_LISTEN_RC\t0\nCHECK_DIAGNOSTIC\tss\t1\t'+raw+'\n'
        with self.assertRaises(c.ProbeError) as error:
            c.parse_probe(body, 'peer_probe')
        self.assertEqual(error.exception.details['exitCode'], 1)
        self.assertEqual(error.exception.details['stage'], 'peer_probe')
        self.assertIn('选项', error.exception.details['diagnostics'][0])
        self.assertNotIn('private-secret', json.dumps(error.exception.details))

    def test_incomplete_probe_has_distinct_error_code(self):
        with self.assertRaises(c.ProbeError) as error:
            c.parse_probe('CHECK_UID\t0\n')
        self.assertEqual(error.exception.details['code'], 'PROBE_OUTPUT_INCOMPLETE')
    def test_ss_state_optional_ipv6_and_multiple_owners(self):
        body = ('ESTAB 0 0 [::ffff:10.0.0.1]:8080 [::ffff:10.0.0.2]:50000 users:(("java",pid=123,fd=8),("java",pid=456,fd=8)) ino:123 sk:aaa\n'
                '\t cubic bytes_acked:120 bytes_received:240\n'
                '0 0 [2001:db8::1]:8080 [2001:db8::2]:51000 users:(("java",pid=42,fd=8)) sk:bbb\n'
                '  cubic bytes_acked:5 bytes_received:6\n')
        rows = c.parse_sockets(body)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['pids'], [123, 456])
        self.assertEqual(rows[0]['local']['ip'], '10.0.0.1')
        self.assertEqual(rows[0]['bytesReceived'], 240)
        self.assertEqual(rows[1]['peer']['ip'], '2001:db8::2')
        self.assertEqual(rows[1]['state'], 'UNKNOWN')

    def test_listen_wildcards_and_missing_counters(self):
        rows = c.parse_sockets('LISTEN 0 128 *:8080 *:* users:(("java",pid=123,fd=8))\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['local']['port'], 8080)
        self.assertIsNone(rows[0]['bytesReceived'])

    def test_reverse_tuple_preserves_source_port_and_ipv6(self):
        flow = socket('[2001:db8::1]:8080', '[2001:db8::2]:50000')
        expression = c.reverse_filter([flow])
        self.assertIn('src [2001:db8::2]', expression)
        self.assertIn('sport = :50000', expression)
        self.assertIn('dst [2001:db8::1]', expression)
        self.assertIn('dport = :8080', expression)
        reverse = socket('[2001:db8::2]:50000', '[2001:db8::1]:8080')
        self.assertTrue(c.same_connection(flow, reverse))
        reverse['local']['port'] += 1
        self.assertFalse(c.same_connection(flow, reverse))

    def test_partial_command_output_is_failure(self):
        with self.assertRaises(RuntimeError):
            c.parse_probe('CHECK_SS_RC\t1\nCHECK_LISTEN_RC\t0\n')

    def test_counts_require_stable_socket_and_two_samples(self):
        flows = {}
        first = socket(sent=5, received=8)
        flow = c.update_flow(flows, first, [socket(state='LISTEN')], 'a')
        self.assertEqual(flow['direction'], 'upstream')
        self.assertEqual(flow['networkActivity'], 'UNKNOWN')
        c.update_flow(flows, socket(sent=15, received=28), [], 'b')
        self.assertEqual(flow['sentDelta'], 10)
        self.assertEqual(flow['receivedDelta'], 20)
        self.assertEqual(flow['bytesSent'], 15)
        self.assertEqual(flow['bytesReceived'], 28)
        self.assertEqual(flow['networkActivity'], 'DATA_OBSERVED')
        c.update_flow(flows, socket(cookie='new', sent=1, received=1), [], 'c')
        self.assertEqual(len(flows), 2)
        self.assertEqual(list(flows.values())[1]['networkActivity'], 'UNKNOWN')

    def test_missing_counters_not_zero(self):
        flows = {}
        value = socket(sent=None, received=None)
        c.update_flow(flows, value, [], 'a')
        result = c.update_flow(flows, value, [], 'b')
        self.assertIsNone(result['receivedDelta'])
        self.assertEqual(result['networkActivity'], 'UNKNOWN')

    def test_application_matching_scopes_environment_and_keeps_ambiguity(self):
        apps = [app('1', 'orders'), app('2', 'other', env='PRODUCT')]
        result = c.match_application(process(), {'10.0.0.1'}, [socket(state='LISTEN')], apps, 'TEST')
        self.assertEqual(result['appId'], '1')
        self.assertEqual(result['confidence'], 'EXACT')
        apps.append(app('3', 'other'))
        result = c.match_application(process(), {'10.0.0.1'}, [socket(state='LISTEN')], apps, 'TEST')
        self.assertEqual(result['confidence'], 'AMBIGUOUS')
        self.assertIsNone(result['appId'])
        self.assertEqual(len(result['candidates']), 2)

    def test_ip_alone_does_not_identify_application(self):
        result = c.match_application(process(command='java -jar unknown.jar'), {'10.0.0.1'}, [], [app('1', 'orders')], 'TEST')
        self.assertEqual(result['confidence'], 'UNKNOWN')

    def test_tomcat_instance_matching_handles_case_separators_and_pid_suffix(self):
        apps = [app('1508', 'phoenix-product', '10.0.0.2', '6001', 'PRODUCT')]
        for directory in ['apache-tomcat_phoenix-product', 'tomcat-Phoenix_Product',
                          'apache-tomcat_phoenix-product_32453']:
            with self.subTest(directory=directory):
                proc = process(32453, f'java -Dcatalina.base=/opt/{directory} org.apache.catalina.startup.Bootstrap')
                proc['kind'] = 'Tomcat'
                result = c.match_application(proc, {'10.0.0.2'}, [], apps, 'PRODUCT')
                self.assertEqual(result['appId'], '1508')
                self.assertEqual(result['appName'], 'phoenix-product')
                self.assertEqual(result['confidence'], 'INFERRED')
                self.assertEqual(c.infer_environment(proc, {'10.0.0.2'}, [], apps), 'PRODUCT')

    def test_business_name_prefers_exact_app_over_substring_candidates(self):
        apps = [app('1', 'orders'), app('2', 'orders-batch')]
        for command in ['java -jar /srv/Orders_Batch.war',
                        'java -Dcatalina.base=/opt/apache-tomcat_orders-batch org.apache.catalina.startup.Bootstrap']:
            with self.subTest(command=command):
                result = c.match_application(process(command=command), {'10.0.0.1'}, [], apps, 'TEST')
                self.assertEqual(result['appId'], '2')
        duplicate = app('3', 'orders-batch')
        result = c.match_application(process(command='java -jar orders-batch.jar'),
                                     {'10.0.0.1'}, [], apps+[duplicate], 'TEST')
        self.assertEqual(result['confidence'], 'AMBIGUOUS')
        self.assertIsNone(result['appId'])
        self.assertEqual({candidate['appId'] for candidate in result['candidates']}, {'2', '3'})

    def test_tomcat_base_takes_precedence_over_shared_home_and_unrelated_arguments(self):
        apps = [app('1', 'orders'), app('2', 'caller')]
        proc = process(command='java -Dcatalina.base=/opt/apache-tomcat_orders '
                       '-Dcatalina.home=/opt/apache-tomcat_caller -Dlog.dir=/logs/caller/ '
                       'org.apache.catalina.startup.Bootstrap')
        result = c.match_application(proc, {'10.0.0.1'}, [], apps, 'TEST')
        self.assertEqual(result['appId'], '1')

    def test_peer_application_is_resolved_when_target_environment_is_unknown(self):
        apps = [app('1508', 'phoenix-product', '10.0.0.2', '6001', 'PRODUCT')]
        proc = process(32453, 'java -Dcatalina.base=/opt/apache-tomcat_phoenix-product '
                       'org.apache.catalina.startup.Bootstrap')
        proc['kind'] = 'Tomcat'
        for direction in ['upstream', 'downstream']:
            with self.subTest(direction=direction):
                target = socket() if direction == 'upstream' else socket('10.0.0.1:50000', '10.0.0.2:6001')
                flow = c.update_flow({}, target, [socket(state='LISTEN')] if direction == 'upstream' else [], 'b')
                reverse = {'local': target['peer'], 'peer': target['local'], 'pids': [32453], 'socketId': 'peer'}
                sample = {'processes': {32453: proc}, 'listeners': []}
                c.attach_peer_observations(flow, [(sample, reverse, 'b')], {'ip': '10.0.0.2'}, apps, None)
                self.assertEqual(flow['direction'], direction)
                self.assertEqual(flow['remoteProcesses'][0]['pid'], 32453)
                self.assertEqual(flow['remoteProcesses'][0]['application']['appId'], '1508')
                self.assertEqual(flow['remoteProcesses'][0]['application']['appName'], 'phoenix-product')

    def test_peer_matching_does_not_override_explicit_environment_or_merge_environments(self):
        proc = process(456, 'java -jar caller.jar')
        target = socket()
        reverse = {'local': target['peer'], 'peer': target['local'], 'pids': [456], 'socketId': 'peer'}
        sample = {'processes': {456: proc}, 'listeners': []}
        apps = [app('1', 'caller', '10.0.0.2', '9000', 'PRODUCT')]
        for environment, inventory in [('TEST', apps), (None, apps+[app('2', 'caller', '10.0.0.2', '9000', 'TEST')])]:
            with self.subTest(environment=environment):
                flow = c.update_flow({}, target, [socket(state='LISTEN')], 'b')
                c.attach_peer_observations(flow, [(sample, reverse, 'b')], {'ip': '10.0.0.2'}, inventory, environment)
                result = flow['remoteProcesses'][0]['application']
                self.assertEqual(result['confidence'], 'UNKNOWN')
                self.assertIsNone(result['appId'])

    def test_port_hint_not_verified_service(self):
        result = c.middleware(port=3306)
        self.assertEqual(result['name'], 'MySQL')
        self.assertEqual(result['confidence'], 'INFERRED')
        self.assertEqual(c.middleware(process(command='/usr/sbin/mysqld') | {'name':'mysqld'}, 3307)['confidence'], 'EXACT')
        self.assertNotEqual(c.middleware(process(command='java -Ddbname=mysqld -jar app.jar'), 3307)['confidence'], 'EXACT')


class ConfigurationTests(unittest.TestCase):
    def test_jdbc_credentials_query_and_custom_port(self):
        raw = ('spring.datasource.url=jdbc:mysql://secret-user:1234@10.1.1.1:3307/orders?password=secret-password\n'
               'spring.datasource.password=secret-password\n')
        entries = c.parse_config(raw, '/app/WEB-INF/classes/db.properties')
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['host'], entries[0]['port'], entries[0]['kind']), ('10.1.1.1', 3307, 'MySQL'))
        self.assertNotIn('secret-user', json.dumps(entries))
        self.assertNotIn('secret-password', json.dumps(entries))
        flow = socket(peer='10.1.1.1:3307')
        self.assertEqual(c.config_matches(flow, {'endpoints': entries}), entries)
        flow['peer']['port'] = 3306
        self.assertEqual(c.config_matches(flow, {'endpoints': entries}), [])

    def test_nested_yaml_redis_and_es_list(self):
        raw = ('spring:\n  redis:\n    host: redis.internal\n    port: 6380\n'
               '  elasticsearch:\n    uris:\n      - http://10.1.2.1:9201\n      - http://10.1.2.2:9201\n')
        entries = c.parse_config(raw, '/app/application.yml')
        self.assertEqual({(e['host'], e['port'], e['kind']) for e in entries},
                         {('redis.internal', 6380, 'Redis'), ('10.1.2.1', 9201, 'Elasticsearch'), ('10.1.2.2', 9201, 'Elasticsearch')})

    def test_xml_and_kafka_multiple_brokers_placeholders(self):
        raw = ('<property name="jdbc.url" value="jdbc:mysql://db.internal:3306/db"/>\n'
               'spring.kafka.bootstrap-servers=kafka-a:9093,kafka-b:9093\n'
               'spring.redis.url=redis://${REDIS_HOST}:6379\n')
        entries = c.parse_config(raw, '/app/WEB-INF/classes/config.xml')
        self.assertEqual({(e['host'], e['port'], e['kind']) for e in entries},
                         {('db.internal', 3306, 'MySQL'), ('kafka-a', 9093, 'Kafka'), ('kafka-b', 9093, 'Kafka')})

    def test_hostname_matching_requires_target_dns_evidence(self):
        flow = socket(peer='10.1.1.1:6379')
        entry = {'host': 'redis.internal', 'port': 6379}
        self.assertEqual(c.config_matches(flow, {'endpoints': [entry]}), [])
        entry['resolvedIps'] = ['10.1.1.1']
        self.assertEqual(c.config_matches(flow, {'endpoints': [entry]}), [entry])

    def test_tomcat_and_jar_roots_are_bounded(self):
        roots = c.config_roots(process(command='java -Dcatalina.base=/opt/tomcat-orders org.apache.catalina.startup.Bootstrap'))
        self.assertEqual(roots, ['/opt/tomcat-orders/conf', '/opt/tomcat-orders/webapps'])
        self.assertEqual(c.config_roots(process(command='java -jar app.jar') | {'cwd': '/'}), [])

    def test_tomcat_context_docbases(self):
        p = process(command='java -Dcatalina.base=/opt/apache-tomcat_dolphin-webhook')
        xml = '''<Server><Service><Engine><Host appBase="/srv/apps">
          <Context path="/dolphin-webhook" docBase="/var/www/webapps/dolphin-webhook"/>
          <Context docBase="orders"/><Context docBase="${unknown}"/>
          <Context docBase="archive.war"/>
        </Host></Engine></Service></Server>'''
        self.assertEqual(c.tomcat_docbase_roots(xml, p),
                         ['/var/www/webapps/dolphin-webhook/WEB-INF', '/srv/apps/orders/WEB-INF'])
        self.assertEqual(c.tomcat_docbase_roots('<Server>', p), [])
        self.assertEqual(c.tomcat_docbase_roots('<!DOCTYPE Server><Server/>', p), [])

    def test_config_probe_follows_server_xml_docbase(self):
        encode = lambda s: base64.b64encode(s.encode()).decode()
        base = '/opt/apache-tomcat_dolphin-webhook'
        xml = '<Server><Service><Engine><Host><Context docBase="/var/www/webapps/dolphin-webhook"/></Host></Engine></Service></Server>'
        first = 'CONFIG_FILE\t'+encode(base+'/conf/server.xml')+'\t'+encode(xml)+'\nCONFIG_DONE'
        second = 'CONFIG_FILE\t'+encode('/var/www/webapps/dolphin-webhook/WEB-INF/classes/db.properties')+'\t'+encode('redis.host=10.0.0.2\nredis.port=6379')+'\nCONFIG_DONE'
        class Terminal:
            def __init__(self): self.responses = iter([(0, first), (0, second)])
            def run(self, *args, **kwargs): return next(self.responses)
        result = c.collect_config(Terminal(), process(command='java -Dcatalina.base='+base))
        self.assertIn('/var/www/webapps/dolphin-webhook/WEB-INF', result['roots'])
        self.assertEqual([(x['host'], x['port']) for x in result['endpoints']], [('10.0.0.2', 6379)])


class OrchestrationTests(unittest.TestCase):
    def test_docker_target_matches_published_client_tuple_as_inferred(self):
        asset = {'id': 'a', 'ip': '10.0.0.1'}
        assets = [asset, {'id': 'b', 'ip': '10.0.0.2'}]
        proxy = process(123, 'docker-proxy') | {'name': 'docker-proxy'}
        container = {'id': 'a'*64, 'name': 'redis', 'initPid': 123, 'startTicks': '54321',
                     'networkNamespace': 'net:[1]', 'ports': {}, 'ips': ['172.17.0.2']}
        target = process(123) | {'netns': 'net:[1]'}
        mapping = {'published': c.endpoint('0.0.0.0:6379'), 'target': c.endpoint('172.17.0.2:6379')}
        class Terminal:
            def __init__(self, identifier): self.identifier = identifier
            def close(self): pass
        def resolve(_terminal, _proxy, _probe, evidence):
            evidence.update(resolution='INFERRED', mappings=[mapping])
            return container, target
        def sample(terminal, mode, selector):
            if mode == 'peer':
                self.assertIn('dst 10.0.0.1 and dport = :6379', selector)
                return {'sockets': [socket('10.0.0.2:50000', '10.0.0.1:6379', 456)],
                        'listeners': [], 'processes': {456: process(456)}, 'warnings': []}
            if mode == 'target':
                return {'sockets': [], 'listeners': [], 'processes': {123: proxy}, 'warnings': []}
            return {'sockets': [socket('172.17.0.2:6379', '10.0.0.2:50000')],
                    'listeners': [socket('172.17.0.2:6379', '*:*', state='LISTEN')],
                    'processes': {123: target}, 'warnings': [], 'networkNamespace': 'net:[1]'}
        with patch.object(c, 'resolve_docker_target', side_effect=resolve), patch.object(c, 'docker_containers', return_value=([container], [])):
            report = c.inspect_application(asset, proxy, [], assets, {}, {}, 'TEST', duration=.04, interval=.005,
                terminal_opener=lambda a, *_: (Terminal(a['id']), 'root'), sample_probe=sample)
        flow = report['upstream'][0]
        self.assertEqual(flow['resolution'], 'INFERRED')
        self.assertEqual(flow['remoteProcesses'][0]['pid'], 456)
        evidence = flow['remoteProcesses'][0]['dockerTargetConnection']
        self.assertEqual(evidence['published'], c.endpoint('10.0.0.1:6379'))
        self.assertEqual(evidence['container'], c.endpoint('172.17.0.2:6379'))

    def test_published_candidate_requires_full_client_tuple_and_window(self):
        flow = c.update_flow({}, socket('172.17.0.2:6379', '10.0.0.2:50000'),
                             [socket('172.17.0.2:6379', '*:*', state='LISTEN')], '2026-01-01T00:00:10+00:00')
        flow['_publishedEndpoints'] = [c.endpoint('10.0.0.1:6379')]
        flow['_possibleEnd'] = '2026-01-01T00:00:20+00:00'
        sample = {'processes': {456: process(456)}, 'listeners': []}
        wrong = socket('10.0.0.2:50001', '10.0.0.1:6379', 456)
        correct = socket('10.0.0.2:50000', '10.0.0.1:6379', 456)
        c.attach_peer_observations(flow, [(sample, wrong, '2026-01-01T00:00:15+00:00'),
            (sample, correct, '2026-01-01T00:00:30+00:00')], {'ip': '10.0.0.2'}, [], 'TEST')
        self.assertEqual(flow['remoteProcesses'], [])

    def test_multiple_published_endpoints_remain_ambiguous(self):
        flow = c.update_flow({}, socket('172.17.0.2:6379', '10.0.0.2:50000'),
                             [socket('172.17.0.2:6379', '*:*', state='LISTEN')], '2026-01-01T00:00:10+00:00')
        flow['_publishedEndpoints'] = [c.endpoint('10.0.0.1:6379'), c.endpoint('10.0.0.3:6379')]
        sample = {'processes': {456: process(456)}, 'listeners': []}
        observations = [(sample, socket('10.0.0.2:50000', ip+':6379', 456), '2026-01-01T00:00:10+00:00')
                        for ip in ('10.0.0.1', '10.0.0.3')]
        c.attach_peer_observations(flow, observations, {'ip': '10.0.0.2'}, [], 'TEST')
        self.assertTrue(flow['_dockerAmbiguous'])

    def test_partial_failure_retains_observed_duration_and_last_valid_process_identity(self):
        class Terminal:
            def close(self): pass
        samples = iter([1, 2, 3])
        def sample(*_):
            number = next(samples)
            if number == 3:
                raise c.ProbeError('SOCKET_PROBE_TIMEOUT', 'target_probe', '只读探针超时')
            return {'sockets': [socket()], 'listeners': [socket(state='LISTEN')],
                    'processes': {123: process()}, 'warnings': [],
                    'observedAt': c.iso_stamp(1000+(5 if number == 1 else 12))}
        with patch.object(c.time, 'monotonic', side_effect=[0, 5, 5, 12, 12, 47]):
            report = c.inspect_application({'id': 'a', 'ip': '10.0.0.1'}, process(), [], [], {}, {}, 'TEST',
                duration=60, interval=0, terminal_opener=lambda *_: (Terminal(), 'root'), sample_probe=sample)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['coverage']['actualSeconds'], 7)
        self.assertEqual(report['coverage']['samplingElapsedSeconds'], 47)
        self.assertEqual(report['upstream'][0]['samples'], 2)
        self.assertEqual(report['target']['startTicks'], process()['startTicks'])
        self.assertEqual(report['target']['startedAt'], process()['startedAt'])
        self.assertEqual(report['error']['code'], 'SOCKET_PROBE_TIMEOUT')

    def test_probe_failure_is_exposed_in_report_with_stage_and_exit_code(self):
        class Terminal:
            closed = False
            def close(self): self.closed = True
        terminal = Terminal()
        def failure(*_):
            raise c.ProbeError('PROBE_COMMAND_FAILED', 'target_probe', '远端连接检查失败', 127,
                               ['远端 Shell 变量未定义，可能存在 Bash 版本兼容问题'])
        report = c.inspect_application({'id':'a','ip':'10.0.0.1'}, process(), [], [], {}, {}, 'TEST',
            duration=0, terminal_opener=lambda *_:(terminal,'root'), sample_probe=failure)
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['error']['exitCode'],127)
        self.assertEqual(report['error']['stage'],'target_probe')
        self.assertEqual(report['coverage']['actualSeconds'], 0)
        self.assertTrue(terminal.closed)

    def test_unmanaged_database_keeps_endpoint_and_configuration_evidence(self):
        class Terminal:
            def close(self): pass
        connection = socket('10.0.0.1:51000', '10.1.1.1:3307')
        def sample(*_):
            return {'sockets':[connection], 'listeners':[socket(state='LISTEN')],
                    'processes':{123:process()}, 'warnings':[]}
        config = {'status':'complete', 'endpoints':c.parse_config('jdbc.url=jdbc:mysql://10.1.1.1:3307/db', '/app/WEB-INF/classes/db.properties')}
        report = c.inspect_application({'id':'a','ip':'10.0.0.1'}, process(), [app('1','orders')],
            [{'id':'a','ip':'10.0.0.1'}], {}, {}, 'TEST', duration=0, terminal_opener=lambda *_:(Terminal(),'root'),
            sample_probe=sample, config_collector=lambda *_:config)
        flow = report['downstream'][0]
        self.assertEqual(flow['peer']['ip'],'10.1.1.1')
        self.assertEqual(flow['peer']['port'],3307)
        self.assertEqual(flow['resolution'],'UNKNOWN')
        self.assertEqual(flow['service']['name'],'MySQL')
        self.assertEqual(flow['service']['confidence'],'INFERRED')
        self.assertEqual(flow['configurationMatches'][0]['file'],'/app/WEB-INF/classes/db.properties')

    def run_inspection(self, duplicate=False, fail_peer=False, changed=False, cancel=False, unmanaged_target=False):
        assets = [{'id': 'a', 'ip': '10.0.0.1'}, {'id': 'b', 'ip': '10.0.0.2'}]
        if duplicate: assets.append({'id': 'c', 'ip': '10.0.0.2'})
        events, terminals = [], []
        class Terminal:
            def __init__(self, identifier): self.identifier = identifier; self.closed = False
            def close(self): self.closed = True
        def opener(asset, *_):
            terminal = Terminal(asset['id']); terminals.append(terminal)
            return terminal, 'root'
        def sample(terminal, mode, selector):
            events.append(mode)
            if mode == 'peer':
                if fail_peer: raise RuntimeError('private secret from transport')
                value = socket('10.0.0.2:50000', '10.0.0.1:8080', pid=456, cookie='remote')
                return {'sockets': [value], 'listeners': [socket('10.0.0.2:9000', '*:*', pid=456, state='LISTEN')],
                        'processes': {456: process(456, 'java -Dappid=caller -jar caller.jar')}, 'warnings': []}
            proc = process()
            if changed: proc['startedAt'] = '2026-10-15T02:00:00+00:00'
            return {'sockets': [socket(sent=10+events.count('target'))], 'listeners': [socket(state='LISTEN')],
                    'processes': {123: proc}, 'warnings': []}
        stop = threading.Event()
        if cancel: stop.set()
        apps = [app('2', 'caller', '10.0.0.2', '9000', 'PRODUCT' if unmanaged_target else 'TEST')]
        if not unmanaged_target:
            apps.append(app('1', 'orders'))
        report = c.inspect_application(assets[0], process(), apps,
            assets, {}, {}, None if unmanaged_target else 'TEST', duration=0.04, interval=0.005, cancel=stop, terminal_opener=opener,
            sample_probe=sample, config_collector=lambda *_: {'endpoints': [], 'status': 'complete', 'warnings': []})
        self.assertTrue(all(t.closed for t in terminals))
        return report, events

    def test_peers_sample_while_target_still_sampling(self):
        report, events = self.run_inspection()
        self.assertIn('peer', events)
        self.assertIn('target', events[events.index('peer')+1:])
        flow = report['upstream'][0]
        self.assertEqual(flow['remoteProcesses'][0]['pid'], 456)
        self.assertEqual(flow['remoteProcesses'][0]['application']['appId'], '2')
        self.assertIn(flow['resolution'], ('EXACT', 'INFERRED'))

    def test_duplicate_asset_remains_ambiguous(self):
        report, events = self.run_inspection(duplicate=True)
        self.assertNotIn('peer', events)
        self.assertEqual(report['upstream'][0]['resolution'], 'AMBIGUOUS')
        self.assertEqual(report['upstream'][0]['remoteProcesses'], [])

    def test_unmanaged_target_report_retains_resolved_peer_application(self):
        report, _ = self.run_inspection(unmanaged_target=True)
        self.assertIsNone(report['environment'])
        self.assertEqual(report['target']['application']['confidence'], 'UNKNOWN')
        owner = report['upstream'][0]['remoteProcesses'][0]
        self.assertEqual(owner['application']['appId'], '2')
        self.assertEqual(owner['application']['appName'], 'caller')
        self.assertEqual(owner['application']['confidence'], 'EXACT')

    def test_peer_failure_is_unknown_and_does_not_leak_exception(self):
        report, _ = self.run_inspection(fail_peer=True)
        self.assertEqual(report['upstream'][0]['resolution'], 'UNKNOWN')
        self.assertNotIn('private secret', json.dumps(report))

    def test_reused_pid_fails_before_assigning_flows(self):
        report, _ = self.run_inspection(changed=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['upstream'], [])

    def test_cancel_before_sampling_closes_target(self):
        report, events = self.run_inspection(cancel=True)
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
