"""Listener evidence fills service identity gaps without inventing socket owners."""
import json
import threading
import unittest
from unittest.mock import patch

import application_check as c
from test_application_check import app, process, socket


class ListenerTests(unittest.TestCase):
    def test_listener_queries_group_by_host_and_skip_upstream_ambiguous_and_resolved_flows(self):
        asset, peer = {'id': 'target', 'ip': '10.0.0.1'}, {'id': 'peer', 'ip': '10.0.0.2'}
        def flow(ip, port, direction='downstream'):
            return socket('10.0.0.1:51000', f'{ip}:{port}') | {
                'direction': direction, 'remoteProcesses': [], 'warnings': []}
        rows = [flow(peer['ip'], 8080), flow(peer['ip'], 9000),
                flow('10.0.0.3', 8080), flow('10.0.0.4', 8080),
                flow(peer['ip'], 50000, 'upstream'), flow(peer['ip'], 8081)]
        rows[-1]['remoteProcesses'] = [{'pid': 999}]
        calls = []
        class Terminal:
            def close(self): pass
        def sample(_terminal, mode, selector):
            calls.append((mode, selector))
            return {'listeners': [socket('*:8080', '*:*', 456, state='LISTEN')],
                    'processes': {456: process(456)}, 'warnings': []}
        c.inspect_peer_listeners(rows, asset, {
            peer['ip']: {peer['id']: peer}, '10.0.0.3': {'a': {'id': 'a'}, 'b': {'id': 'b'}}},
            [], 'TEST', {}, {}, lambda *_: (Terminal(), 'root'), sample, threading.Event())
        self.assertEqual(calls, [('listeners', '( sport = :8080 or sport = :9000 )')])
        self.assertEqual(rows[0]['peerListeningProcesses'][0]['pid'], 456)
        self.assertFalse(rows[1]['peerListeningProcesses'])
        self.assertTrue(all('peerListeningProcesses' not in row for row in rows[2:]))

    def test_endpoint_matching_retains_multiple_live_candidates_and_excludes_proxies(self):
        flow = socket('10.0.0.1:51000', '10.0.0.2:8080')
        sample = {'listeners': [
            socket('*:8080', '*:*', 456, state='LISTEN'),
            socket('10.0.0.2:8080', '*:*', 457, state='LISTEN'),
            socket('127.0.0.1:8080', '*:*', 458, state='LISTEN'),
            socket('*:8081', '*:*', 459, state='LISTEN'),
            socket('*:8080', '*:*', 460, state='LISTEN'),
            socket('*:8080', '*:*', 461, state='ESTAB'),
            socket('*:8080', '*:*', 462, state='LISTEN'),
        ], 'processes': {pid: process(pid) for pid in range(456, 462)}, 'observedAt': 'later'}
        sample['processes'][460].update(name='docker-proxy', exe='/usr/bin/docker-proxy')
        # A UDP record must never satisfy a TCP endpoint.
        udp = socket('*:8080', '*:*', 459, state='LISTEN')
        udp['local']['protocol'] = 'udp'
        sample['listeners'].append(udp)
        result = c.listener_processes(flow, sample, {'ip': '10.0.0.2'}, [], 'TEST')
        self.assertEqual([p['pid'] for p in result], [456, 457])
        self.assertTrue(all(p['confidence'] == 'INFERRED' and p['observedAt'] == 'later' for p in result))

    def inspect(self, fail=False, nonroot=False, cancel=False):
        asset = {'id': 'target', 'ip': '10.0.0.1'}
        peers = [{'id': f'peer-{i}', 'ip': f'10.0.0.{i+2}'} for i in range(5)]
        rows = [socket(f'10.0.0.1:{51000+i}', f'{peer["ip"]}:8080', cookie=str(i))
                for i, peer in enumerate(peers)]
        opened, calls = [], []
        stop = threading.Event()
        class Terminal:
            def __init__(self, asset):
                self.asset, self.closed = asset, False
            def close(self):
                self.closed = True
        def opener(peer_asset, *_):
            terminal = Terminal(peer_asset)
            opened.append(terminal)
            return terminal, 'root'
        def sample(terminal, mode, selector):
            if mode == 'target':
                if cancel:
                    stop.set()
                return {'sockets': rows, 'listeners': [], 'processes': {123: process()}, 'warnings': []}
            if mode == 'listeners':
                calls.append((terminal.asset['id'], selector))
                if fail:
                    raise RuntimeError('password=private-secret')
                owner = process(456, 'java -Dappid=billing -jar /opt/billing.jar')
                return {'sockets': [], 'listeners': [socket('*:8080', '*:*', 456, state='LISTEN')],
                        'processes': {} if nonroot else {456: owner}, 'observedAt': '2026-10-20T08:00:00+00:00',
                        'warnings': ['未提权至 root，其他用户的 socket/PID 归属可能不可见'] if nonroot else []}
            return {'sockets': [], 'listeners': [], 'processes': {}, 'warnings': []}
        apps = [app('2', 'billing', peer['ip'], '8080') for peer in peers]
        with patch.object(c, 'docker_peer_connections', side_effect=lambda _t, flows, *_a, **_k: {
            c.flow_key(f): {'mappings': [], 'owners': [], 'warnings': []} for f in flows}):
            report = c.inspect_application(asset, process(), apps, peers, {}, {}, 'TEST',
                duration=0, interval=0.001, terminal_opener=opener, sample_probe=sample, cancel=stop,
                config_collector=lambda *_: {'status': 'complete', 'endpoints': [], 'warnings': []})
        self.assertTrue(all(t.closed for t in opened))
        return report, calls

    def test_fifth_remote_gets_listener_pid_without_claiming_a_reverse_connection(self):
        report, calls = self.inspect()
        self.assertEqual({identifier for identifier, _ in calls}, {f'peer-{i}' for i in range(5)})
        self.assertTrue(all(selector == '( sport = :8080 )' for _, selector in calls))
        self.assertEqual(len(report['coverage']['listenerChecks']), 5)
        flow = next(f for f in report['downstream'] if f['peer']['ip'] == '10.0.0.6')
        self.assertEqual(flow['remoteProcesses'], [])
        self.assertEqual(flow['resolution'], 'UNKNOWN')
        candidate = flow['peerListeningProcesses'][0]
        self.assertEqual(candidate['pid'], 456)
        self.assertEqual(candidate['application']['appId'], '2')
        self.assertEqual(candidate['confidence'], 'INFERRED')
        self.assertEqual(candidate['observedAt'], '2026-10-20T08:00:00+00:00')
        self.assertIn('端口监听进程', flow['reason'])

    def test_listener_failure_or_invisible_pid_retains_unknown_without_leaking_errors(self):
        for options in ({'fail': True}, {'nonroot': True}):
            with self.subTest(options=options):
                report, _ = self.inspect(**options)
                for flow in report['downstream']:
                    self.assertFalse(flow.get('peerListeningProcesses'))
                    self.assertEqual(flow['remoteProcesses'], [])
                    self.assertEqual(flow['resolution'], 'UNKNOWN')
                    self.assertTrue(flow['warnings'])
                self.assertNotIn('private-secret', json.dumps(report))

    def test_cancellation_does_not_start_additional_listener_checks(self):
        report, calls = self.inspect(cancel=True)
        self.assertEqual(calls, [])
        self.assertNotIn('listenerChecks', report['coverage'])


if __name__ == '__main__':
    unittest.main()
