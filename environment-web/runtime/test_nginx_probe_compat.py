"""Exercise Nginx worker verification with legacy procps output on Linux."""
import base64
import gzip
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'requires Linux /proc')
class NginxProbeCompatibilityTests(unittest.TestCase):
    def test_legacy_ps_keeps_verified_worker_and_socket(self):
        worker = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); time.sleep(30)"
        launch = "exec -a 'nginx: worker process' python3 -c " + shlex.quote(worker)
        master = "import subprocess,time; subprocess.Popen(['bash','-c',%r]); time.sleep(30)" % launch
        master_process = subprocess.Popen(['bash', '-c', "exec -a 'nginx: master process /usr/sbin/nginx' python3 -c " + shlex.quote(master)], start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            children = Path(f'/proc/{master_process.pid}/task/{master_process.pid}/children')
            while time.monotonic() < deadline and not (children.exists() and children.read_text().strip()):
                time.sleep(.02)
            self.assertTrue(children.read_text().strip())
            worker_pid = int(children.read_text().split()[0])
            fds = Path(f'/proc/{worker_pid}/fd')
            while time.monotonic() < deadline:
                try:
                    if any(os.readlink(fd).startswith('socket:[') for fd in fds.iterdir()):
                        break
                except (FileNotFoundError, PermissionError):
                    pass
                time.sleep(.02)
            else:
                self.fail('worker did not open its socket')
            with tempfile.TemporaryDirectory() as directory:
                ss = Path(directory, 'ss')
                ss.write_text('''#!/usr/bin/env python3
import os,re
for pid in os.listdir('/proc'):
 if not pid.isdigit(): continue
 try:
  cmd=open('/proc/'+pid+'/cmdline','rb').read().replace(b'\\0',b' ')
  if not cmd.startswith(b'nginx: worker process'): continue
  for fd in os.listdir('/proc/'+pid+'/fd'):
   m=re.fullmatch(r'socket:\\[(\\d+)\\]',os.readlink('/proc/'+pid+'/fd/'+fd))
   if m: print('ESTAB 0 0 127.0.0.1:8080 127.0.0.1:12345 ino:'+m.group(1)+' sk:1')
 except OSError: pass
''')
                ps = Path(directory, 'ps')
                ps.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
pids=[args[args.index('-p')+1]] if '-p' in args else [x for x in os.listdir('/proc') if x.isdigit()]
for pid in pids:
 try:
  stat=open('/proc/'+pid+'/stat').read().split(') ',1)[1].split()
  cmd=open('/proc/'+pid+'/cmdline','rb').read().replace(b'\\0',b' ').decode().strip()
  ppid=stat[1]
  if args[-1]=='pid=,ppid=,args=': print(pid,ppid,cmd)
  elif 'ppid=,args=' in args: print(',args=\\n',ppid)
  elif 'ppid=' in args and 'args=' in args: print(ppid,cmd)
  else: print(pid,ppid,'root 60 Tue Sep 1 10:00:00 2026 nginx 0.0 0.0 1 2',cmd)
 except (OSError,IndexError): pass
''')
                ss.chmod(0o700)
                ps.chmod(0o700)
                probe = Path(os.environ.get('NGINX_TEST_PROBE', Path(__file__).parent/'probes/application_sockets.sh'))
                result = subprocess.run(['bash', str(probe), 'nginx', str(master_process.pid)],
                    env={**os.environ, 'PATH': directory+os.pathsep+os.environ['PATH']},
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                match = re.search(r'CHECK_BLOB_BEGIN\tsample-0\n(.*?)\nCHECK_BLOB_END', result.stdout, re.S)
                body = gzip.decompress(base64.b64decode(match.group(1))).decode() if match else result.stdout
                self.assertEqual(len(re.findall(r'^CHECK_NGINX_IDENTITY\t', body, re.M)), 2, body)
                self.assertIn('ESTAB 0 0 127.0.0.1:8080', body)
                self.assertNotIn('Nginx worker 身份变化', body)
        finally:
            os.killpg(master_process.pid, signal.SIGTERM)
            master_process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
