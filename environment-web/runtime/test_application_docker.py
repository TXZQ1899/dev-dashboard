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


class DockerTargetTests(unittest.TestCase):
    def proxy(self, **changes):
        return process(600, '/usr/bin/docker-proxy-current -proto tcp -host-ip 0.0.0.0 '
            '-host-port 7000 -container-ip 172.18.0.6 -container-port 6379') | {
            'name': 'docker-proxy-cu', 'kind': '未分类', 'exe': '/usr/bin/docker-proxy-current', **changes}

    def resolve(self, sample=None, values=None, warnings=None, current=None, proxy=None):
        evidence = {'resolution': 'UNKNOWN', 'mappings': [], 'processes': []}
        value = container()
        values = [value] if values is None else values
        sample = redis_sample() if sample is None else sample
        def discovery(_terminal, identifier=None):
            rows = values if identifier is None else (current if current is not None else [value])
            return copy.deepcopy(rows), warnings or []
        with patch.object(c, 'docker_containers', side_effect=discovery):
            try:
                resolved = c.resolve_docker_target(None, proxy or self.proxy(), lambda *_: sample, evidence)
                return evidence, resolved, None
            except c.ProbeError as error:
                return evidence, None, error.details

    def test_proxy_endpoint_and_live_listener_select_actual_pid_without_claiming_application(self):
        evidence, (value, owner), error = self.resolve()
        self.assertIsNone(error)
        self.assertEqual(value, container())
        self.assertEqual(owner['pid'], 500)
        self.assertEqual(evidence['resolution'], 'INFERRED')
        self.assertEqual(evidence['mappings'][0]['published'], c.endpoint('0.0.0.0:7000'))
        self.assertEqual(evidence['mappings'][0]['target'], c.endpoint('172.18.0.6:6379'))
        self.assertNotIn('command', json.dumps(evidence))

    def test_missing_or_inconsistent_endpoint_metadata_never_selects_an_arbitrary_container(self):
        wrong_binding = container()
        wrong_binding['ports']['6379/tcp'][0]['HostIp'] = '127.0.0.1'
        for changes in [dict(values=[]), dict(values=[wrong_binding]), dict(warnings=['unreadable']),
                        dict(proxy=self.proxy(command=self.proxy()['command'].replace('-proto tcp', '-proto udp'))),
                        dict(proxy=self.proxy(command=self.proxy()['command']+' -host-port 7001'))]:
            evidence, resolved, error = self.resolve(**changes)
            self.assertIsNone(resolved)
            self.assertEqual(error['code'], 'DOCKER_TARGET_UNRESOLVED')
            self.assertEqual(evidence['resolution'], 'UNKNOWN')

    def test_multiple_containers_or_listener_pids_remain_ambiguous(self):
        second = container('b')
        evidence, resolved, error = self.resolve(values=[container(), second])
        self.assertEqual(evidence['resolution'], 'AMBIGUOUS')
        self.assertEqual(len(evidence['mappings']), 2)
        self.assertIsNone(resolved)
        sample = redis_sample()
        # A PID with unreadable identity must not disappear from the candidate set.
        sample['listeners'][0]['pids'].append(501)
        evidence, resolved, error = self.resolve(sample=sample)
        self.assertIsNone(resolved)
        self.assertEqual(error['code'], 'DOCKER_TARGET_AMBIGUOUS')
        self.assertEqual([p['pid'] for p in evidence['processes']], [500, 501])

    def test_published_but_unlistened_port_is_explicitly_unresolved(self):
        sample = redis_sample() | {'listeners': []}
        evidence, resolved, error = self.resolve(sample=sample)
        self.assertIsNone(resolved)
        self.assertEqual(error['code'], 'DOCKER_TARGET_UNRESOLVED')
        self.assertIn('未发现可核验的监听进程', error['message'])
        self.assertEqual(len(evidence['mappings']), 1)

    def test_restarted_container_changed_namespace_and_unverified_listener_are_rejected(self):
        wrong_pid = redis_sample() | {'processes': {}}
        wrong_netns = redis_sample() | {'networkNamespace': 'net:[99]'}
        warnings = redis_sample() | {'warnings': ['未提权']}
        wrong_process = redis_sample()
        wrong_process['processes'][500]['netns'] = 'net:[99]'
        for changes in [dict(current=[container() | {'startTicks': '101'}]), dict(sample=wrong_pid),
                        dict(sample=wrong_netns), dict(sample=warnings), dict(sample=wrong_process)]:
            evidence, resolved, error = self.resolve(**changes)
            self.assertIsNone(resolved)
            self.assertEqual(evidence['resolution'], 'UNKNOWN')
            self.assertEqual(error['code'], 'DOCKER_TARGET_UNRESOLVED')

    def run_report(self, change=None):
        calls, discovery_calls = [], []
        proxy, service = self.proxy(), redis_sample()['processes'][500]
        class Terminal:
            def close(self): pass
        def discovery(_terminal, identifier=None):
            discovery_calls.append(identifier)
            changed = identifier and len(discovery_calls) >= 4 and change == 'container'
            return [container() | ({'startTicks': '101'} if changed else {})], []
        def probe(_terminal, mode, selector):
            calls.append((mode, selector))
            if mode == 'target':
                return {'sockets': [], 'listeners': [], 'processes': {600: proxy}, 'warnings': []}
            if mode == 'container-listeners':
                return redis_sample()
            self.assertEqual(mode, 'container-target')
            self.assertEqual(selector, {'pid': 500, 'startTicks': service['startTicks']})
            sample = redis_sample(peer='192.0.2.10:33580')
            # Include another listening port and an outgoing socket: the scope
            # is the resolved process, not just the proxy's published port.
            sample['sockets'].append(socket('172.18.0.6:46000', '192.0.2.20:3306', 500, 'out'))
            sample['listeners'].append(socket('*:6380', '*:*', 500, state='LISTEN'))
            sample['sockets'].append(socket('172.18.0.6:6380', '192.0.2.30:33581', 500, 'other'))
            if change == 'pid': sample['processes'][500]['startTicks'] = 'reused'
            if change == 'namespace': sample['networkNamespace'] = 'net:[99]'
            return sample
        with patch.object(c, 'docker_containers', side_effect=discovery):
            report = c.inspect_application({'id': 'target', 'ip': '10.0.0.2'}, proxy,
                [app('wrong', 'redis', '10.0.0.2', '6379')], [], {}, {}, 'TEST',
                duration=0.015, interval=0.002, terminal_opener=lambda *_: (Terminal(), 'root'),
                sample_probe=probe, config_collector=lambda *_: self.fail('must not read container paths on host'))
        return report, calls

    def test_proxy_entry_samples_container_service_connections_and_preserves_actual_endpoints(self):
        report, calls = self.run_report()
        self.assertEqual(report['target']['pid'], 600)
        self.assertEqual(report['target']['processName'], 'docker-proxy-cu')
        self.assertEqual(report['target']['dockerTarget']['pid'], 500)
        self.assertEqual(report['target']['application']['confidence'], 'UNKNOWN')
        self.assertEqual(len(report['upstream']), 2)
        self.assertEqual(len(report['downstream']), 1)
        self.assertEqual(report['upstream'][0]['local'], c.endpoint('172.18.0.6:6379'))
        self.assertTrue(all(flow['resolution'] == 'UNKNOWN' for flow in report['upstream']))
        self.assertGreaterEqual(report['coverage']['samples'], 1)
        self.assertEqual(report['configuration']['status'], 'not_collected')
        self.assertEqual([mode for mode, _ in calls[:3]], ['target', 'container-listeners', 'container-target'])

    def test_changed_container_retains_prior_evidence_and_never_uses_the_new_identity(self):
        report, _ = self.run_report('container')
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['error']['code'], 'DOCKER_TARGET_CHANGED')
        self.assertEqual(report['coverage']['samples'], 1)
        self.assertEqual(len(report['upstream']), 2)

    def test_changed_service_identity_or_namespace_stops_before_accepting_flows(self):
        for change in ('pid', 'namespace'):
            report, _ = self.run_report(change)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['coverage']['samples'], 0)
            self.assertEqual(report['upstream'], [])
            self.assertEqual(report['downstream'], [])


if __name__ == '__main__':
    unittest.main()
