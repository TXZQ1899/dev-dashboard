import copy
import json
import unittest
from unittest.mock import patch

import application_check as c
from test_application_check import app, process, socket


def container(identifier='a', address='172.18.0.6', target=6379, namespace='net:[42]'):
    return {'id': identifier * 64, 'name': 'redis-custom', 'initPid': 400, 'startTicks': '100',
            'networkNamespace': namespace, 'ips': [address],
            'ports': {str(target) + '/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '7000'}], '6378/tcp': None}}


def redis_sample(value=None, peer='10.0.0.1:33580'):
    value = value or container()
    address = value['ips'][0] + ':' + next(iter(value['ports'])).split('/')[0]
    owner = process(500, 'redis-server *:6379 [cluster]') | {
        'name': 'redis-server', 'kind': 'Redis', 'exe': '/usr/bin/redis-server', 'netns': value['networkNamespace']}
    return {'sockets': [socket(address, peer, 500, 'backend')],
            'listeners': [socket('*:' + address.rsplit(':', 1)[1], '*:*', 500, state='LISTEN')],
            'processes': {500: owner}, 'warnings': [], 'networkNamespace': value['networkNamespace']}


class DockerTests(unittest.TestCase):
    def flow(self):
        return socket('10.0.0.1:33580', '10.0.0.2:7000') | {'direction': 'downstream'}

    def inspect(self, sample=None, containers=None, current=None):
        values = containers if containers is not None else [container()]
        flow = self.flow()
        calls = []
        def discovery(_terminal, identifier=None):
            rows = values if identifier is None else [v for v in values if v['id'] == identifier]
            return copy.deepcopy(current if identifier and current is not None else rows), []
        def probe(_terminal, mode, selector):
            calls.append((mode, selector))
            return sample if sample is not None else redis_sample()
        with patch.object(c, 'docker_containers', side_effect=discovery):
            result = c.docker_peer_connections(None, [flow], probe)[c.flow_key(flow)]
        return result, calls

    def test_metadata_is_minimal_and_malformed_or_failed_results_remain_unknown(self):
        value = container()
        row = '\t'.join(['CHECK_DOCKER', value['id'], json.dumps('/redis-custom'), '400', '100',
                         'net:[42]', json.dumps(value['ports']), '172.18.0.6'])
        class Terminal:
            body = row + '\nCHECK_DOCKER_STATUS\tcomplete\n'
            def run(self, command, **_):
                self.command = command
                return 0, self.body
        terminal = Terminal()
        rows, warnings = c.docker_containers(terminal)
        self.assertEqual(rows, [value])
        self.assertEqual(warnings, [])
        self.assertNotIn('.Config', terminal.command)
        self.assertNotIn('.Env', terminal.command)
        for body in ['CHECK_DOCKER_STATUS\tfailed\nprivate-secret',
                     row.replace('net:[42]', 'bad') + '\nCHECK_DOCKER_STATUS\tcomplete',
                     'CHECK_DOCKER_STATUS\tlimit']:
            terminal.body = body
            rows, warnings = c.docker_containers(terminal)
            self.assertEqual(rows, [])
            self.assertTrue(warnings)
            self.assertNotIn('private-secret', json.dumps(warnings))
        terminal.body = 'CHECK_DOCKER_STATUS\tnot_available'
        self.assertEqual(c.docker_containers(terminal), ([], []))

    def test_mapping_preserves_rewritten_port_protocol_and_host_binding(self):
        value, flow = container(), self.flow()
        mapping = c.docker_mappings([value], flow)[0]
        self.assertEqual(mapping['target'], c.endpoint('172.18.0.6:6379'))
        self.assertEqual(mapping['published'], c.endpoint('10.0.0.2:7000'))
        for host in ['127.0.0.1', '10.0.0.3', '::']:
            value['ports']['6379/tcp'][0]['HostIp'] = host
            self.assertEqual(c.docker_mappings([value], flow), [])
        value['ports'] = {'7000/udp': [{'HostIp': '0.0.0.0', 'HostPort': '7000'}], '6379/tcp': None}
        self.assertEqual(c.docker_mappings([value], flow), [])
        flow['peer']['protocol'] = 'udp'
        self.assertEqual(c.docker_mappings([container()], flow), [])

    def test_ipv6_mapping_normalizes_mapped_client_and_keeps_source_port(self):
        value = container(address='2001:db8::6')
        value['ports']['6379/tcp'][0]['HostIp'] = '::'
        flow = socket('[2001:db8::1]:33580', '[2001:db8::2]:7000')
        self.assertEqual(c.docker_mappings([value], flow)[0]['target']['ip'], '2001:db8::6')
        parsed = c.parse_sockets('ESTAB 0 0 172.18.0.6:6379 [::ffff:10.0.0.1]:33580users:(("redis-server",pid=500,fd=23)) sk:backend\n')
        self.assertEqual(parsed[0]['peer'], self.flow()['local'])

    def test_real_reverse_socket_identifies_custom_port_redis_without_app_assignment(self):
        result, calls = self.inspect()
        self.assertEqual(len(result['owners']), 1)
        owner = result['owners'][0]
        self.assertEqual(owner['pid'], 500)
        self.assertEqual(owner['service']['name'], 'Redis')
        self.assertEqual(owner['service']['confidence'], 'EXACT')
        self.assertEqual(owner['application']['confidence'], 'UNKNOWN')
        self.assertEqual(owner['container']['id'], container()['id'])
        self.assertEqual(owner['connection']['peer'], self.flow()['local'])
        self.assertIn('sport = :6379', calls[0][1]['filter'])
        self.assertIn('dport = :33580', calls[0][1]['filter'])
        self.assertEqual(calls[0][1]['startTicks'], '100')

    def test_mq_type_comes_from_actual_process_even_on_a_custom_port(self):
        sample = redis_sample()
        sample['processes'][500].update(name='beam.smp', kind='未分类', exe='/usr/bin/beam.smp',
                                       command='/usr/bin/beam.smp -s rabbit boot')
        owner = self.inspect(sample)[0]['owners'][0]
        self.assertEqual(owner['service']['name'], 'RabbitMQ')
        self.assertEqual(owner['service']['confidence'], 'EXACT')
        self.assertIsNone(owner['application']['appId'])

    def test_mapping_or_listener_alone_never_becomes_connection_owner(self):
        sample = redis_sample(peer='172.18.0.1:50000')
        result, _ = self.inspect(sample)
        self.assertEqual(result['owners'], [])
        self.assertEqual(result['mappings'][0]['service']['confidence'], 'INFERRED')
        sample['sockets'] = [socket('172.18.0.6:6379', '10.0.0.1:33581', 500)]
        self.assertEqual(self.inspect(sample)[0]['owners'], [])
        sample['sockets'] = [socket('172.18.0.9:6379', '10.0.0.1:33580', 500)]
        self.assertEqual(self.inspect(sample)[0]['owners'], [])
        sample['sockets'] = []
        self.assertEqual(self.inspect(sample)[0]['owners'], [])
        sample['listeners'][0]['local']['ip'] = '127.0.0.1'
        self.assertNotIn('service', self.inspect(sample)[0]['mappings'][0])

    def test_incomplete_docker_inventory_does_not_claim_unique_connection(self):
        flow = self.flow()
        with patch.object(c, 'docker_containers', return_value=([container()], ['容器网络空间不可读'])):
            result = c.docker_peer_connections(None, [flow], lambda *_: self.fail('must not probe'))[c.flow_key(flow)]
        self.assertEqual(result['owners'], [])
        self.assertTrue(result['warnings'])

    def test_container_restart_or_namespace_change_cannot_confirm_old_mapping(self):
        for changes in [{'initPid': 401}, {'startTicks': '101'}, {'ips': ['172.18.0.7']},
                        {'ports': {'6380/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '7000'}]}}]:
            result, _ = self.inspect(current=[container() | changes])
            self.assertEqual(result['owners'], [])
            self.assertTrue(result['warnings'])
        sample = redis_sample() | {'networkNamespace': 'net:[43]'}
        self.assertEqual(self.inspect(sample)[0]['owners'], [])
        sample = redis_sample()
        sample['processes'][500]['netns'] = 'net:[43]'
        self.assertEqual(self.inspect(sample)[0]['owners'], [])

    def test_multiple_containers_remain_candidates_and_probe_limit_does_not_pick_one(self):
        result, _ = self.inspect(containers=[container(), container('b', '172.18.0.7', namespace='net:[43]')])
        self.assertTrue(result['ambiguous'])
        self.assertEqual(len(result['mappings']), 2)
        values = [container(hex(i)[2:], '172.18.0.' + str(i), namespace='net:[' + str(i) + ']') for i in range(1, 10)]
        result, calls = self.inspect(containers=values)
        self.assertEqual(calls, [])
        self.assertEqual(result['owners'], [])
        self.assertTrue(result['warnings'])

    def test_container_probe_failure_is_safe_and_retains_mapping(self):
        with patch.object(c, 'docker_containers', return_value=([container()], [])):
            def failed(*_):
                raise RuntimeError('password=private-secret')
            flow = self.flow()
            result = c.docker_peer_connections(None, [flow], failed)[c.flow_key(flow)]
        self.assertEqual(result['owners'], [])
        self.assertEqual(len(result['mappings']), 1)
        self.assertNotIn('private-secret', json.dumps(result))

    def test_docker_proxy_detection_uses_executable_identity_not_command_substrings(self):
        self.assertTrue(c.is_docker_proxy(process() | {'name': 'docker-proxy-cu'}))
        self.assertTrue(c.is_docker_proxy(process() | {'exe': '/usr/bin/docker-proxy'}))
        self.assertFalse(c.is_docker_proxy(process(command='java -jar docker-proxy.jar')))


class DockerOrchestrationTests(unittest.TestCase):
    def run_report(self, backend=True, host_proxy=False, ambiguous=False, socket_identity=True):
        class Terminal:
            def __init__(self, asset): self.asset = asset
            def close(self): pass
        values = [container()]
        if ambiguous: values.append(container('b', '172.18.0.7', namespace='net:[43]'))
        def discovery(_terminal, identifier=None):
            return copy.deepcopy(values if identifier is None else [v for v in values if v['id'] == identifier]), []
        def probe(_terminal, mode, selector):
            if mode == 'target':
                return {'sockets': [socket('10.0.0.1:33580', '10.0.0.2:7000')],
                        'listeners': [], 'processes': {123: process()}, 'warnings': []}
            if mode == 'peer':
                proxy = process(600) | {'name': 'docker-proxy-cu', 'exe': '/usr/bin/docker-proxy'}
                return {'sockets': [socket('10.0.0.2:7000', '10.0.0.1:33580', 600)] if host_proxy else [],
                        'listeners': [], 'processes': {600: proxy}, 'warnings': []}
            sample = redis_sample()
            if not backend: sample['sockets'] = []
            if not socket_identity:
                for value in sample['sockets']: value['socketId'] = None
            return sample
        with patch.object(c, 'docker_containers', side_effect=discovery):
            report = c.inspect_application({'id': 'target', 'ip': '10.0.0.1'}, process(),
                [app('redis-id', 'unrelated-business', '10.0.0.2', '6379')],
                [{'id': 'peer', 'ip': '10.0.0.2'}], {}, {}, 'TEST', duration=0.06, interval=0.003,
                terminal_opener=lambda asset, *_: (Terminal(asset['id']), 'root'), sample_probe=probe,
                config_collector=lambda *_: {'endpoints': [], 'status': 'complete', 'warnings': []})
        return report['downstream'][0]

    def test_end_to_end_container_connection_is_exact_and_app_is_unknown(self):
        flow = self.run_report(host_proxy=True)
        self.assertEqual(flow['resolution'], 'EXACT')
        self.assertEqual(flow['service']['name'], 'Redis')
        self.assertEqual(flow['remoteProcesses'][0]['pid'], 500)
        self.assertEqual(flow['remoteProcesses'][0]['application']['confidence'], 'UNKNOWN')
        self.assertEqual(flow['proxyProcesses'][0]['pid'], 600)
        self.assertEqual(flow['peer'], c.endpoint('10.0.0.2:7000'))
        self.assertEqual(flow['sentDelta'], 0)
        self.assertEqual(flow['receivedDelta'], 0)

    def test_proxy_rebuilt_connection_keeps_service_inferred_and_owner_unresolved(self):
        flow = self.run_report(backend=False, host_proxy=True)
        self.assertEqual(flow['resolution'], 'UNKNOWN')
        self.assertEqual(flow['remoteProcesses'], [])
        self.assertEqual(flow['service']['name'], 'Redis')
        self.assertEqual(flow['service']['confidence'], 'INFERRED')

    def test_ambiguous_publication_does_not_become_exact_with_only_one_observed_owner(self):
        flow = self.run_report(ambiguous=True)
        self.assertEqual(flow['resolution'], 'AMBIGUOUS')
        self.assertNotEqual(flow['service']['confidence'], 'EXACT')

    def test_missing_backend_socket_identity_keeps_connection_inferred(self):
        self.assertEqual(self.run_report(socket_identity=False)['resolution'], 'INFERRED')

    def test_container_observation_outside_target_window_is_not_exact(self):
        inspect = c.docker_peer_connections
        def outside(*args, **kwargs):
            results = inspect(*args, **kwargs)
            for result in results.values():
                for owner in result['owners']:
                    for field in ('observedAt', '_observationStart', '_observationEnd'):
                        owner[field] = '2000-01-01T00:00:00+00:00'
            return results
        with patch.object(c, 'docker_peer_connections', side_effect=outside):
            self.assertEqual(self.run_report()['resolution'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
