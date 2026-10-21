"""Small fixtures for buffered, clock-bounded application socket sampling."""
import threading
import unittest
from unittest.mock import patch

import application_check as c
from test_application_check import app, process, socket


def stamp(seconds):
    return c.iso_stamp(1_800_000_000+seconds)


def frame(seconds, rows, pid=123):
    return {'sockets': rows, 'listeners': [], 'processes': {pid: process(pid)}, 'warnings': [],
            'observedAt': stamp(seconds), 'observationStart': stamp(seconds-0.01),
            'observationEnd': stamp(seconds+0.01)}


class SamplingTests(unittest.TestCase):
    def test_live_activity_precedes_remote_wait_and_reports_only_returned_frames(self):
        events = []
        class Terminal:
            def close(self): pass
        def progress(message, **details):
            events.append({'message':message, **details})
        def opener(asset, *_):
            expected = 'target' if asset['id'] == 'target' else 'peer'
            self.assertTrue(any(event.get('scope', 'target') == expected and event['stage'] == 'login'
                                for event in events))
            return Terminal(), 'root'
        def sample(_terminal, mode, _selector):
            expected = 'target' if mode == 'target' else 'peer'
            self.assertTrue(any(event.get('scope', 'target') == expected and event['stage'] == 'sampling'
                                and '等待' in event['message'] for event in events))
            if mode == 'target':
                self.assertFalse(any('samples' in event and event.get('scope', 'target') == 'target'
                                     for event in events))
                return frame(1, [socket()])
            return frame(1, [socket('10.0.0.2:50000', '10.0.0.1:8080', 456)], 456)
        with patch.object(c, 'docker_peer_connections', return_value={}):
            report = c.inspect_application({'id':'target', 'ip':'10.0.0.1'}, process(), [],
                [{'id':'peer', 'ip':'10.0.0.2'}], {}, {}, 'TEST', duration=0,
                terminal_opener=opener, sample_probe=sample, progress=progress,
                config_collector=lambda *_: {'status':'complete', 'endpoints':[], 'warnings':[]})
        counters = next(event for event in events if event.get('scope', 'target') == 'target' and 'samples' in event)
        self.assertEqual((counters['samples'], counters['upstream'], counters['downstream']), (1, 0, 1))
        self.assertEqual(counters['environment'], 'TEST')
        self.assertEqual(report['coverage']['samples'], 1)
        self.assertTrue(any(event.get('scope') == 'peer' and event.get('samples') == 1 for event in events))
        self.assertEqual(events[-1]['stage'], 'saving')
        for stage in ('configuration', 'listeners', 'sampling_complete'):
            self.assertTrue(any(event['stage'] == stage for event in events), stage)

    def test_selected_pid_is_attached_only_to_its_verified_socket_inode(self):
        body = ('CHECK_INODE_OWNER\t1234\t123\nCHECK_SOCKETS_BEGIN\n'
                'ESTAB 0 0 10.0.0.1:51000 10.0.0.2:3306 ino:1234 sk:target\n'
                'ESTAB 0 0 10.0.0.1:51002 10.0.0.2:3306 ino:5678 sk:other\n'
                'CHECK_SOCKETS_END\nCHECK_SS_RC\t0\nCHECK_LISTEN_RC\t0\nCHECK_UID\t0\n')
        rows = c.parse_probe(body)['sockets']
        self.assertEqual(rows[0]['pids'], [123])
        self.assertEqual(rows[1]['pids'], [])

    def test_remote_clock_offset_and_transport_are_bounded_for_each_frame(self):
        body = ('CHECK_CLOCK_BEGIN\t1000\nCHECK_SAMPLE_BEGIN\n'
                'CHECK_OBSERVED\t1000.2\t1000.3\nCHECK_SS_RC\t0\n'
                'CHECK_LISTEN_RC\t0\nCHECK_UID\t0\nCHECK_SAMPLE_END\nCHECK_CLOCK_END\t1001\n')
        parsed = c.parse_observations(body, 'target_probe', 10, 12)['observations'][0]
        self.assertEqual(parsed['observationStart'], c.iso_stamp(10.2))
        self.assertEqual(parsed['observationEnd'], c.iso_stamp(11.3))
        self.assertLess(parsed['observedAt'], c.iso_stamp(12))
        with self.assertRaises(c.ProbeError):
            c.parse_observations(body.replace('CHECK_CLOCK_END\t1001', ''), 'target_probe', 10, 12)
        with self.assertRaises(c.ProbeError):
            c.parse_observations(body, 'target_probe', 10, 10.5)

    def test_clock_uncertainty_cannot_be_promoted_to_exact_from_its_midpoint(self):
        flow = c.update_flow({}, socket(), [], stamp(2))
        flow.update(samples=2, lastSeenAt=stamp(4), _firstObservationEnd=stamp(2.1),
                    _lastObservationStart=stamp(3.9))
        self.assertTrue(c.confirmed_observation(flow, stamp(3), stamp(3.1)))
        self.assertFalse(c.confirmed_observation(flow, stamp(3), stamp(4.1)))
        flow['_interrupted'] = True
        self.assertFalse(c.confirmed_observation(flow, stamp(3), stamp(3.1)))

    def test_discovery_broadens_service_scope_but_attribution_keeps_client_port(self):
        flow = c.update_flow({}, socket('10.0.0.1:51000', '10.0.0.2:3306'), [], stamp(3))
        flow['_possibleStart'] = stamp(2)
        expression = c.peer_sampling_filter([flow])
        self.assertIn('sport = :3306', expression)
        self.assertNotIn('51000', expression)
        wrong = socket('10.0.0.2:3306', '10.0.0.1:51001', 456)
        correct = socket('10.0.0.2:3306', '10.0.0.1:51000', 456)
        sample = frame(2.9, [wrong, correct], 456)
        observations = [(sample, value, sample['observedAt']) for value in sample['sockets']]
        c.attach_peer_observations(flow, observations[:1], {'ip': '10.0.0.2'}, [], 'TEST')
        self.assertEqual(flow['remoteProcesses'], [])
        c.attach_peer_observations(flow, observations, {'ip': '10.0.0.2'}, [], 'TEST')
        self.assertEqual([p['pid'] for p in flow['remoteProcesses']], [456])

    def test_tuple_reuse_outside_possible_lifetime_cannot_attach_an_owner(self):
        flow = c.update_flow({}, socket(), [], stamp(1))
        flow['_possibleEnd'] = stamp(2)
        reverse = socket('10.0.0.2:50000', '10.0.0.1:8080', 456)
        sample = frame(3, [reverse], 456)
        c.attach_peer_observations(flow, [(sample, reverse, sample['observedAt'])], {'ip':'10.0.0.2'}, [], 'TEST')
        self.assertEqual(flow['remoteProcesses'], [])

    def test_reappearing_socket_identity_does_not_bridge_byte_counters(self):
        stop = threading.Event()
        class Terminal:
            def close(self): pass
        def sample(*_):
            stop.set()
            frames = [frame(1, [socket(sent=10)]), frame(2, []), frame(3, [socket(sent=100)])]
            return {**frames[-1], 'observations': frames}
        report = c.inspect_application({'id':'target', 'ip':'10.0.0.1'}, process(), [], [], {}, {}, 'TEST',
            duration=1, cancel=stop, terminal_opener=lambda *_: (Terminal(), 'root'), sample_probe=sample)
        flow = report['downstream'][0]
        self.assertIsNone(flow['sentDelta'])
        self.assertEqual(flow['networkActivity'], 'UNKNOWN')
        self.assertTrue(any('连续性未确认' in warning for warning in flow['warnings']))

    def test_peer_buffer_resolves_short_socket_discovered_after_peer_batch(self):
        ready, cancel = threading.Event(), threading.Event()
        stable = socket('10.0.0.1:51000', '10.0.0.2:3306')
        short = socket('10.0.0.1:51002', '10.0.0.2:3306', cookie='short')
        reverse = socket('10.0.0.2:3306', '10.0.0.1:51002', 456, cookie='remote-short')
        calls = []
        class Terminal:
            def __init__(self, asset): self.asset = asset
            def close(self): pass
        def sample(terminal, mode, selector):
            if mode == 'peer':
                self.assertNotIn('51000', selector)
                peer = frame(2.05, [reverse], 456)
                ready.set()
                return {**peer, 'observations': [peer]}
            calls.append(mode)
            if len(calls) == 1:
                return frame(1, [stable])
            self.assertTrue(ready.wait(1))
            cancel.set()
            frames = [frame(2, [stable, short]), frame(2.1, [stable, short]), frame(2.2, [stable])]
            return {**frames[-1], 'observations': frames}
        with patch.object(c, 'docker_peer_connections', return_value={}):
            report = c.inspect_application({'id':'target', 'ip':'10.0.0.1'}, process(), [],
                [{'id':'peer','ip':'10.0.0.2'}], {}, {}, 'TEST', duration=1, interval=0.001,
                terminal_opener=lambda asset, *_: (Terminal(asset['id']), 'root'),
                sample_probe=sample, cancel=cancel)
        flow = next(f for f in report['downstream'] if f['local']['port'] == 51002)
        self.assertEqual(flow['samples'], 2)
        self.assertEqual(flow['resolution'], 'EXACT')
        self.assertEqual(flow['remoteProcesses'][0]['pid'], 456)
        self.assertEqual(report['coverage']['samples'], 4)
        self.assertEqual(flow['firstSeenAt'], stamp(2))
        self.assertFalse(any(key.startswith('_') for key in flow['remoteProcesses'][0]))

    def test_same_asset_keeps_full_reverse_tuple_query_scope(self):
        stop = threading.Event()
        selectors = []
        class Terminal:
            def close(self): pass
        def sample(_terminal, mode, selector):
            if mode == 'peer':
                selectors.append(selector)
                stop.set()
                return frame(2, [])
            return frame(1, [socket('10.0.0.1:51000', '10.0.0.1:3306')])
        asset = {'id':'same', 'ip':'10.0.0.1'}
        c.inspect_application(asset, process(), [], [asset], {}, {}, 'TEST', duration=1, interval=0.001,
            cancel=stop, terminal_opener=lambda *_: (Terminal(), 'root'), sample_probe=sample)
        self.assertTrue(selectors)
        self.assertIn('sport = :3306', selectors[0])
        self.assertIn('dport = :51000', selectors[0])

    def test_all_fifteen_upstream_hosts_and_downstream_hosts_get_live_pid_checks(self):
        stop = threading.Event()
        asset = {'id': 'target', 'ip': '10.0.0.1'}
        remotes = [{'id': f'peer-{i}', 'ip': f'10.0.0.{i+2}'} for i in range(18)]
        rows = [socket('10.0.0.1:8080', f'{peer["ip"]}:{50000+i}', cookie=f'up-{i}')
                if i < 15 else socket(f'10.0.0.1:{51000+i}', f'{peer["ip"]}:9000', cookie=f'down-{i}')
                for i, peer in enumerate(remotes)]
        ready = threading.Barrier(len(remotes)+1)
        opened, queried, target_calls = [], set(), []

        class Terminal:
            def __init__(self, identifier):
                self.identifier, self.closed = identifier, False
            def close(self):
                self.closed = True

        def opener(peer_asset, *_):
            terminal = Terminal(peer_asset['id'])
            opened.append(terminal)
            return terminal, 'root'

        def sample(terminal, mode, selector):
            if mode == 'target':
                target_calls.append(mode)
                if len(target_calls) > 1:
                    # A fixed small pool cannot reach this barrier: its long
                    # sampling loops would leave the remaining peers queued.
                    ready.wait(timeout=3)
                    stop.set()
                result = frame(1 if len(target_calls) == 1 else 3, rows)
                result['listeners'] = [socket('*:8080', '*:*', state='LISTEN')]
                return result
            index = int(terminal.identifier.rsplit('-', 1)[1])
            if terminal.identifier not in queried:
                queried.add(terminal.identifier)
                ready.wait(timeout=3)
            original = rows[index]
            pid = 1000+index
            reverse = socket(pid=pid, cookie=f'reverse-{index}') | {
                'local': original['peer'], 'peer': original['local']}
            return frame(2, [reverse], pid)

        report = c.inspect_application(asset, process(), [], remotes, {}, {}, 'TEST',
            duration=10, interval=0.001, cancel=stop, terminal_opener=opener, sample_probe=sample)
        self.assertEqual(len(report['upstream']), 15)
        self.assertEqual(len(report['downstream']), 3)
        self.assertEqual(queried, {peer['id'] for peer in remotes})
        self.assertEqual({p['serverIp'] for p in report['coverage']['peers']}, {p['ip'] for p in remotes})
        for direction in ('upstream', 'downstream'):
            for flow in report[direction]:
                index = next(i for i, peer in enumerate(remotes) if peer['ip'] == flow['peer']['ip'])
                self.assertEqual(flow['resolution'], 'EXACT')
                self.assertEqual([p['pid'] for p in flow['remoteProcesses']], [1000+index])
        self.assertTrue(all(terminal.closed for terminal in opened))

    def test_peer_finishing_login_after_target_sampling_still_gets_one_query(self):
        release_login = threading.Event()
        asset, peer = {'id': 'target', 'ip': '10.0.0.1'}, {'id': 'peer', 'ip': '10.0.0.2'}
        calls, opened = [], []
        class Terminal:
            def __init__(self): self.closed = False
            def close(self): self.closed = True
        def opener(peer_asset, *_):
            terminal = Terminal()
            opened.append(terminal)
            if peer_asset['id'] == peer['id']:
                self.assertTrue(release_login.wait(2))
            return terminal, 'root'
        def sample(_terminal, mode, _selector):
            calls.append(mode)
            if mode == 'target':
                result = frame(1, [socket()])
                result['listeners'] = [socket('*:8080', '*:*', state='LISTEN')]
                return result
            # This connection was observed after the target's lifetime ended.
            # Query it, but do not copy its PID into the earlier connection.
            return frame(3, [socket('10.0.0.2:50000', '10.0.0.1:8080', 456)], 456)
        def config(*_):
            release_login.set()  # inspect_application has already set done.
            return {'status': 'complete', 'endpoints': [], 'warnings': []}
        report = c.inspect_application(asset, process(), [], [peer], {}, {}, 'TEST',
            duration=0, terminal_opener=opener, sample_probe=sample, config_collector=config)
        self.assertEqual(calls, ['target', 'peer'])
        self.assertEqual(report['coverage']['peers'][0]['samples'], 1)
        self.assertEqual(report['upstream'][0]['remoteProcesses'], [])
        self.assertEqual(report['upstream'][0]['resolution'], 'UNKNOWN')
        self.assertTrue(all(terminal.closed for terminal in opened))

    def test_same_host_application_and_all_remote_hosts_are_checked_independently(self):
        for local_first, peer_ip in [(False, '10.0.0.1'), (True, '10.0.0.1'), (False, '127.0.0.1')]:
            with self.subTest(local_first=local_first, peer_ip=peer_ip):
                stop, local_ready = threading.Event(), threading.Event()
                remote_ready = [threading.Event() for _ in range(5)]
                asset = {'id': 'target', 'ip': '10.0.0.1'}
                remotes = [{'id': f'remote-{i}', 'ip': f'10.0.0.{i+2}'} for i in range(5)]
                local = socket(f'{peer_ip}:52078', f'{peer_ip}:20884', pid=31329, cookie='local')
                external = [socket(f'10.0.0.1:{51000+i}', f'{remote["ip"]}:9000', pid=31329,
                                   cookie=f'external-{i}') for i, remote in enumerate(remotes)]
                rows = [local]+external if local_first else external[:4]+[local, external[4]]
                caller = process(31329, 'java -Dcatalina.base=/opt/apache-tomcat_phoenix-preprocess '
                                 'org.apache.catalina.startup.Bootstrap') | {'kind': 'Tomcat'}
                product = process(32453, 'java -Dcatalina.base=/opt/apache-tomcat_phoenix-product '
                                  'org.apache.catalina.startup.Bootstrap') | {'kind': 'Tomcat'}
                opened, selectors, calls = [], [], []

                class Terminal:
                    def __init__(self, identifier):
                        self.identifier, self.closed = identifier, False
                    def close(self):
                        self.closed = True

                def opener(peer_asset, *_):
                    terminal = Terminal(peer_asset['id'])
                    opened.append(terminal)
                    return terminal, 'root'

                def sample(terminal, mode, selector):
                    if mode == 'target':
                        calls.append(mode)
                        if len(calls) > 1:
                            # All remote probes stay busy until the local
                            # probe gets its own worker and captures evidence.
                            for event in remote_ready:
                                event.wait(2)
                            local_ready.wait(2)
                            stop.set()
                        result = frame(1 if len(calls) == 1 else 3, rows, 31329)
                        return result | {'processes': {31329: caller}}
                    if terminal.identifier == asset['id']:
                        selectors.append(selector)
                        wrong = socket(f'{peer_ip}:20884', f'{peer_ip}:52079', pid=777, cookie='unrelated')
                        reverse = socket(f'{peer_ip}:20884', f'{peer_ip}:52078', pid=32453, cookie='product')
                        result = frame(2, [wrong, reverse], 32453)
                        result['processes'] = {32453: product, 777: process(777, 'java -jar unrelated.jar')}
                        result['listeners'] = [socket('*:'+str(port), '*:*', pid=32453, state='LISTEN')
                                               for port in (6001, 9006, 20884, 57109)]
                        local_ready.set()
                        return result
                    index = int(terminal.identifier.rsplit('-', 1)[1])
                    remote_ready[index].set()
                    local_ready.wait(2)
                    reverse = socket(f'{remotes[index]["ip"]}:9000', f'10.0.0.1:{51000+index}', pid=456)
                    return frame(2, [reverse], 456)

                report = c.inspect_application(asset, caller,
                    [app('1508', 'phoenix-product', port='6001', env='PRODUCT')], [asset]+remotes,
                    {}, {}, 'PRODUCT', duration=10, interval=0.001, cancel=stop,
                    terminal_opener=opener, sample_probe=sample)
                flow = next(f for f in report['downstream'] if f['peer']['port'] == 20884)
                self.assertEqual(flow['resolution'], 'EXACT')
                self.assertEqual([owner['pid'] for owner in flow['remoteProcesses']], [32453])
                self.assertEqual(flow['remoteProcesses'][0]['application']['appId'], '1508')
                self.assertEqual(flow['remoteProcesses'][0]['application']['appName'], 'phoenix-product')
                self.assertEqual(flow['remoteProcesses'][0]['application']['confidence'], 'INFERRED')
                self.assertIn('sport = :20884', selectors[0])
                self.assertIn('dport = :52078', selectors[0])
                self.assertEqual({terminal.identifier for terminal in opened},
                                 {'target', *(remote['id'] for remote in remotes)})
                fifth = next(f for f in report['downstream'] if f['peer']['ip'] == remotes[4]['ip'])
                self.assertEqual(fifth['resolution'], 'EXACT')
                self.assertEqual([owner['pid'] for owner in fifth['remoteProcesses']], [456])
                self.assertEqual(len(report['coverage']['peers']), 6)
                self.assertTrue(all(terminal.closed for terminal in opened))


if __name__ == '__main__':
    unittest.main()
