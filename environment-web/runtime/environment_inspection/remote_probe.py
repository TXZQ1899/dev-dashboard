"""Run on the target: discover application processes and their stdout/stderr paths."""
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time

app = sys.argv[1]
now = dt.datetime.now().astimezone()
try:
    lines = subprocess.check_output(['ss', '-lntp'], stderr=subprocess.DEVNULL).decode('utf-8', 'replace').splitlines()
    listeners_status = 'complete'
except Exception:
    lines = []; listeners_status = 'failed'
uptime = float(open('/proc/uptime').read().split()[0]); hz = os.sysconf('SC_CLK_TCK')
result = {'observedAt':now.isoformat(), 'processes':[], 'listenerStatus':listeners_status}

def directory_status(path):
    try:
        import stat
        info = os.stat(path)
        return 'EXISTS' if stat.S_ISDIR(info.st_mode) else 'NOT_DIRECTORY'
    except FileNotFoundError:
        return 'NOT_FOUND'
    except OSError:
        return 'UNKNOWN'


def memory_size(value):
    match = re.fullmatch(r'([0-9]+)([kKmMgGtT]?)', value)
    if not match: return None
    return int(match.group(1)) * {'':1, 'k':1024, 'm':1024**2, 'g':1024**3, 't':1024**4}[match.group(2).lower()]


def jvm_details(args, status_text):
    if not args or os.path.basename(args[0]) not in ('java', 'java.exe'):
        return {'jvmArgs':[], 'rssMiB':None, 'rssXmxPercent':None}
    options = []
    for arg in args[1:]:
        if arg == '-jar' or not arg.startswith('-'): break
        if arg.startswith(('-Xms', '-Xmx')):
            options.append(arg)
    xmx_values = [memory_size(arg[4:]) for arg in options if arg.startswith('-Xmx')]
    xmx = xmx_values[-1] if xmx_values else None
    rss_match = re.search(r'(?m)^VmRSS:\s*(\d+)\s+kB\s*$', status_text)
    rss = int(rss_match.group(1))*1024 if rss_match else None
    return {'jvmArgs':options, 'rssMiB':round(rss/1024**2, 2) if rss is not None else None,
            'rssXmxPercent':round(rss/xmx*100, 2) if rss is not None and xmx else None}

for name in os.listdir('/proc'):
    if not name.isdigit(): continue
    try:
        pid = int(name)
        args = [v.decode('utf-8', 'replace') for v in open('/proc/%d/cmdline' % pid, 'rb').read().split(b'\0') if v]
        if not any(a in ('-Dappid='+app, '-Dspring.application.name='+app, '--spring.application.name='+app) for a in args) and not any(os.path.basename(a) in (app+'.jar', app+'-exec.jar', app+'.war') for a in args): continue
        stat = open('/proc/%d/stat' % pid).read(); ticks = int(stat[stat.rfind(')')+2:].split()[19])
        elapsed = uptime - ticks/hz
        cwd = os.readlink('/proc/%d/cwd' % pid)
        logs = []
        for fd in ('1','2'):
            try:
                p = os.readlink('/proc/%d/fd/%s' % (pid, fd))
                if p.startswith('/') and os.path.isfile(p) and re.search(r'\.(?:log|out|txt)$', p, re.I): logs.append(p)
            except OSError: pass
        dirs = sorted(set([cwd] + [os.path.dirname(a.split('=',1)[-1]) for a in args if a.split('=',1)[-1].startswith('/') and '/' in a.split('=',1)[-1]] + [os.path.dirname(os.path.dirname(p)) for p in logs if os.path.basename(os.path.dirname(p)) == 'logs']))
        try: status_text = open('/proc/%d/status' % pid).read()
        except OSError: status_text = ''
        metrics = jvm_details(args, status_text)
        stat2 = open('/proc/%d/stat' % pid).read(); ticks2 = int(stat2[stat2.rfind(')')+2:].split()[19])
        if ticks2 != ticks: continue
        result['processes'].append({'pid':pid, 'startTicks':str(ticks), 'command':' '.join(shlex.quote(a) for a in args), 'cwd':cwd, 'cwdStatus':directory_status(cwd), 'startedAt':dt.datetime.fromtimestamp(time.time()-elapsed, now.tzinfo).isoformat(), 'days':round(elapsed/86400,2), 'listeners':[x.strip() for x in lines if re.search(r'pid='+str(pid)+r'(?:,|\))', x)], 'directories':[{'path':p,'status':directory_status(p)} for p in dirs], 'stdoutPaths':sorted(set(logs)), 'identity':'INFERRED: appid/应用名参数或 jar 文件名', **metrics})
    except (OSError, ValueError, IndexError): continue
print('ENVSCOPE_INSPECTION_RESULT\t'+json.dumps(result,ensure_ascii=False))
