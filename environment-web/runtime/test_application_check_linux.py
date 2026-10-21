"""Small real-socket/probe checks for the Linux container acceptance boundary."""
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import application_check as c


class LocalTerminal:
    def __init__(self, env=None):
        self.env = env

    def run(self, command, timeout=35):
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True, timeout=timeout, env=self.env)
        return result.returncode, result.stdout


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bash'), 'requires a Linux PTY and Bash')
class CanonicalTerminalTests(unittest.TestCase):
    def test_long_reverse_filter_survives_canonical_terminal_input(self):
        import pty
        import termios

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        flags = termios.tcgetattr(slave)
        flags[3] = (flags[3] | termios.ICANON) & ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, flags)
        shell = subprocess.Popen(['bash', '--noprofile', '--norc', '--noediting', '-s'],
                                 stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        def close_shell():
            shell.kill()
            shell.wait(timeout=5)
        self.addCleanup(close_shell)
        terminal = object.__new__(c.si.Terminal)
        terminal.send = lambda value: os.write(master, value.encode())
        terminal.recv = lambda: os.read(master, 65536).decode() if select.select([master], [], [], 0.1)[0] else ''
        class BoundedTerminal:
            def run(self, command, timeout):
                return terminal.run(command, min(timeout, 2))

        flows = [{'local': c.endpoint('[2001:db8::1]:'+str(40000+index)),
                  'peer': c.endpoint('[2001:db8::2]:6379')} for index in range(100)]
        selector = c.reverse_filter(flows)
        self.assertGreater(len(selector), 4096)
        with tempfile.TemporaryDirectory() as directory:
            # Echo the selector back as a warning to verify every tuple survives
            # the PTY, without requiring ss on the application container itself.
            Path(directory, 'application_sockets.sh').write_text(
                '# '+('long script comment with quotes \'" $() `text` ' * 250)+'\n'+
                'printf "CHECK_WARNING\\t%s\\n" "$2"\n'
                'printf "CHECK_SS_RC\\t0\\nCHECK_LISTEN_RC\\t0\\nCHECK_UID\\t0\\n"\n')
            with patch.object(c, 'PROBES', Path(directory)):
                sample = c.probe(BoundedTerminal(), 'peer', selector)
        self.assertEqual(sample['warnings'], [selector])

    def test_staged_probe_is_checksummed_cached_and_removed_on_close(self):
        class StagedTerminal(c.ProbeTerminal):
            def __init__(self):
                self.commands = []
                self.bad_checksum = False
                self.ws = type('WS', (), {'close': lambda *_a, **_k: None})()
            def run(self, command, timeout=35, **_kwargs):
                self.commands.append(command)
                result = LocalTerminal().run(command, timeout)
                return (0, '0'*64) if self.bad_checksum and command.startswith('base64 -d ') else result
        terminal = StagedTerminal()
        script = '# '+('long quoted comment \'" $() `text` ' * 300)+'\nprintf "verified\\n"\n'
        path = terminal.prepare_probe(script)
        self.assertEqual(Path(path).read_text(), script)
        uploads = len(terminal.commands)
        self.assertEqual(terminal.prepare_probe(script), path)
        self.assertEqual(len(terminal.commands), uploads)
        self.assertTrue(all(len(command) < 1024 for command in terminal.commands))
        rc, body = terminal.run(terminal.probe_command(script, []))
        self.assertEqual((rc, body.strip()), (0, 'verified'))
        value = ('x'*600+"'\"; $(exit 91) `exit 92`\n")*8
        command = terminal.probe_command('printf "%s" "$1"\n', [value])
        self.assertLess(len(command), 1024)
        self.assertEqual(terminal.run(command), (0, value))
        terminal.bad_checksum = True
        with self.assertRaises(c.ProbeError):
            terminal.prepare_probe('exit 99\n')
        self.assertIsNone(terminal._socket_probe_digest)
        directory = Path(path).parent
        terminal.close()
        self.assertFalse(directory.exists())


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('ss') and shutil.which('ps'), 'requires Linux ss/procps')
class LinuxProbeTests(unittest.TestCase):
    def test_listener_only_probe_identifies_pid_after_the_client_has_closed(self):
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(('127.0.0.1', 0))
        server.listen()
        client = socket.socket()
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        client.close()
        accepted.close()
        port = server.getsockname()[1]
        sample = c.sampling_probe(LocalTerminal(), 'listeners', f'( sport = :{port} )')
        listeners = [row for row in sample['listeners'] if row['local']['port'] == port]
        self.assertTrue(listeners)
        self.assertTrue(all(row['state'] == 'LISTEN' and os.getpid() in row['pids'] for row in listeners))
        self.assertIn(os.getpid(), sample['processes'])
        flow = {'peer': c.endpoint(f'127.0.0.1:{port}')}
        result = c.listener_processes(flow, sample, {'ip': '127.0.0.1'}, [], None)
        self.assertEqual([entry['pid'] for entry in result], [os.getpid()])
        self.assertEqual(result[0]['confidence'], 'INFERRED')

    def test_batch_retains_short_socket_that_is_gone_before_the_response(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            signal = Path(directory)/'first-snapshot'
            real_ss = shutil.which('ss')
            wrapper = Path(directory)/'ss'
            wrapper.write_text('#!/bin/bash\n'+shlex.quote(real_ss)+' "$@"\nrc=$?\n'
                'if [[ "$1" == -antnie ]]; then touch '+shlex.quote(str(signal))+'; fi\nexit "$rc"\n')
            wrapper.chmod(0o700)
            server = socket.socket()
            self.addCleanup(server.close)
            server.bind(('127.0.0.1', 0)); server.listen()
            ports, errors = [], []
            def short_connection():
                try:
                    deadline = time.monotonic()+3
                    while not signal.exists() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    with socket.socket() as client:
                        client.connect(server.getsockname())
                        ports.append(client.getsockname()[1])
                        accepted, _ = server.accept()
                        with accepted:
                            time.sleep(0.35)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            worker = threading.Thread(target=short_connection)
            worker.start()
            try:
                terminal = LocalTerminal(dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']))
                batch = c.probe(terminal, 'target', os.getpid(), batch=True)
            finally:
                worker.join(timeout=5)
            self.assertEqual(errors, [])
            self.assertEqual(len(batch['observations']), c.BATCH_SAMPLES)
            self.assertTrue(all(os.getpid() in frame['processes'] for frame in batch['observations']))
            self.assertTrue(ports)
            captured = [s for frame in batch['observations'] for s in frame['sockets']
                        if s['local']['port'] == ports[0] and s['peer']['port'] == server.getsockname()[1]]
            self.assertTrue(captured, 'short socket must survive batch transfer')
            self.assertTrue(all(s['local']['port'] != ports[0] for s in batch['sockets']))
            self.assertTrue(all(frame['observationStart'] <= frame['observationEnd'] for frame in batch['observations']))

    def test_partial_fd_inventory_retains_only_verified_ownership_and_warns(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory)/'find'
            wrapper.write_text('#!/bin/bash\n'+shlex.quote(shutil.which('find'))+' "$@"\nexit 1\n')
            wrapper.chmod(0o700)
            server = socket.socket()
            self.addCleanup(server.close)
            server.bind(('127.0.0.1', 0)); server.listen()
            terminal = LocalTerminal(dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']))
            sample = c.probe(terminal, 'target', os.getpid())
            self.assertTrue(any('文件描述符未能读取' in warning for warning in sample['warnings']))
            self.assertTrue(any(row['local']['port'] == server.getsockname()[1] and os.getpid() in row['pids']
                                for row in sample['listeners']))

    def test_peer_limits_rows_before_transfer_and_marks_truncation(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)/'sockets'
            fixture.write_text('State Recv-Q Send-Q Local Address:Port Peer Address:Port\n'+''.join(
                f'ESTAB 0 0 10.0.0.2:3306 10.0.0.1:{40000+i} users:(("fixture",pid={os.getpid()},fd=8)) ino:{i+1} sk:s{i}\n'
                for i in range(500)))
            wrapper = Path(directory)/'ss'
            wrapper.write_text('#!/bin/bash\nif [[ "$1" == -antpie ]]; then cat '+shlex.quote(str(fixture))+'; fi\n')
            wrapper.chmod(0o700)
            terminal = LocalTerminal(dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']))
            sample = c.probe(terminal, 'peer', 'sport = :3306')
            self.assertEqual(len(sample['sockets']), 400)
            self.assertTrue(any('超过 400' in warning for warning in sample['warnings']))

    def test_cross_network_namespace_finds_socket_hidden_from_host_ss(self):
        if not shutil.which('unshare') or subprocess.run(['unshare', '-n', '--', 'true'],
                capture_output=True).returncode:
            self.skipTest('requires an isolated test container with SYS_ADMIN/NET_ADMIN')
        child_code = '''import json, socket, subprocess, sys
subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
server = socket.socket()
server.bind(('127.0.0.1', 0))
server.listen()
client = socket.socket()
client.connect(server.getsockname())
accepted, _ = server.accept()
print(json.dumps([server.getsockname()[1], client.getsockname()[1]]), flush=True)
sys.stdin.readline()
'''
        with subprocess.Popen(['unshare', '-n', '--', sys.executable, '-u', '-c', child_code],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'namespace child did not start')
                server_port, client_port = json.loads(child.stdout.readline())
                namespace = os.readlink('/proc/'+str(child.pid)+'/ns/net')
                self.assertNotEqual(namespace, os.readlink('/proc/self/ns/net'))
                ticks = Path('/proc/'+str(child.pid)+'/stat').read_text().rsplit(') ', 1)[1].split()[19]
                value = {'id': 'b'*64, 'name': 'isolated-service', 'initPid': child.pid,
                    'startTicks': ticks, 'networkNamespace': namespace, 'ips': ['127.0.0.1'],
                    'ports': {str(server_port)+'/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '7000'}]}}
                flow = {'local': c.endpoint('127.0.0.1:'+str(client_port)),
                        'peer': c.endpoint('127.0.0.1:7000'), 'socketId': 'target'}
                terminal = LocalTerminal()
                host = c.probe(terminal, 'peer', c.reverse_filter([{**flow, 'peer': c.endpoint('127.0.0.1:'+str(server_port))}]))
                self.assertEqual(host['sockets'], [])
                with patch.object(c, 'docker_containers', return_value=([value], [])):
                    result = c.docker_peer_connections(terminal, [flow], c.probe)[c.flow_key(flow)]
                self.assertEqual(result['warnings'], [])
                self.assertEqual(len(result['owners']), 1)
                self.assertEqual(result['owners'][0]['pid'], child.pid)
            finally:
                child.stdin.close()

    def test_docker_discovery_and_namespaced_probe_use_live_sockets_and_guard_pid_identity(self):
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(('127.0.0.1', 0)); server.listen()
        client = socket.socket()
        self.addCleanup(client.close)
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        self.addCleanup(accepted.close)
        # A tiny Docker CLI fixture exercises the remote Bash metadata protocol.
        # Socket/process identity and the namespace probe are real Linux reads.
        with tempfile.TemporaryDirectory() as directory:
            identifier = 'a' * 64
            metadata = '\t'.join([identifier, json.dumps('/test-redis'), str(os.getpid()),
                json.dumps({str(server.getsockname()[1])+'/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '7000'}]}), '127.0.0.1'])
            executable = Path(directory)/'docker'
            executable.write_text('#!/usr/bin/env python3\nimport sys\nprint('+repr(identifier)+
                ' if sys.argv[1] == "ps" else '+repr(metadata)+')\n')
            executable.chmod(0o700)
            terminal = LocalTerminal(dict(os.environ, PATH=directory+os.pathsep+os.environ['PATH']))
            containers, warnings = c.docker_containers(terminal)
            self.assertEqual(warnings, [])
            self.assertEqual(len(containers), 1)
            value = containers[0]
            flow = {'local': c.endpoint('127.0.0.1:'+str(client.getsockname()[1])),
                    'peer': c.endpoint('127.0.0.1:7000'), 'socketId': 'target'}
            result = c.docker_peer_connections(terminal, [flow], c.probe)[c.flow_key(flow)]
            self.assertEqual(len(result['owners']), 1)
            self.assertEqual(result['owners'][0]['pid'], os.getpid())
            self.assertEqual(result['owners'][0]['connection']['local']['port'], server.getsockname()[1])
            selector = {'pid': os.getpid(), 'startTicks': str(int(value['startTicks'])+1),
                        'filter': 'sport = :'+str(server.getsockname()[1]), 'listenFilter': 'sport = :'+str(server.getsockname()[1])}
            with self.assertRaises(c.ProbeError) as error:
                c.probe(terminal, 'container-peer', selector)
            self.assertEqual(error.exception.details['exitCode'], 3)

    def test_live_process_socket_directions_and_byte_deltas(self):
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(('127.0.0.1', 0)); server.listen()
        client = socket.socket()
        self.addCleanup(client.close)
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        self.addCleanup(accepted.close)
        terminal = LocalTerminal()
        before = c.probe(terminal, 'target', os.getpid())
        self.assertIn(os.getpid(), before['processes'])
        self.assertTrue(any(s['local']['port'] == server.getsockname()[1] for s in before['listeners']))
        flows = {}
        for value in before['sockets']:
            if value['state'] != 'LISTEN': c.update_flow(flows, value, before['listeners'], 'before')
        client.sendall(b'application-check-business-sample')
        accepted.recv(1024)
        accepted.sendall(b'response')
        client.recv(1024)
        after = c.probe(terminal, 'target', os.getpid())
        for value in after['sockets']:
            if value['state'] != 'LISTEN': c.update_flow(flows, value, after['listeners'], 'after')
        self.assertEqual({flow['direction'] for flow in flows.values()}, {'upstream', 'downstream'})
        self.assertTrue(any(flow['networkActivity'] == 'DATA_OBSERVED' for flow in flows.values()))
        flow = next(flow for flow in flows.values() if flow['direction'] == 'downstream')
        peer = c.probe(terminal, 'peer', c.reverse_filter([flow]))
        matches = [row for row in peer['sockets'] if c.same_connection(flow, row)]
        self.assertEqual(len(matches), 1)
        self.assertIn(os.getpid(), matches[0]['pids'])

    def test_configuration_probe_extracts_without_persisting_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'app'; root.mkdir()
            (root/'application.properties').write_text('spring.datasource.url=jdbc:mysql://user:password@10.1.1.2:3307/db?password=hidden\n')
            proc = {'command': 'java -jar '+str(root/'app.jar'), 'cwd': str(root)}
            result = c.collect_config(LocalTerminal(), proc)
            self.assertEqual(result['endpoints'][0]['host'], '10.1.1.2')
            self.assertEqual(result['endpoints'][0]['port'], 3307)
            self.assertNotIn('hidden', json.dumps(result))
            self.assertNotIn('user:', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
