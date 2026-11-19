"""Container-side JumpServer bridge; stream private log archives to the caller."""
import base64
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import sys
import time

sys.path.insert(0, '/app/runtime')
import application_check
import application_files
import server_inventory


def emit(event, **data):
    print(json.dumps({'event':event, **data}, ensure_ascii=False), flush=True)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def transfer(terminal, process, scopes):
    args = [value for scope in scopes for pattern in scope['patterns'] for value in (scope['root'], pattern)]
    if not args: raise ValueError('未发现应用日志目录')
    remote = None
    try:
        cutoff = int(time.time())-7*86400
        body = application_files.run_script(terminal, 'application_logs.sh', process['pid'], process['startTicks'], cutoff, 'all', *args, timeout=145)
        line = next((line for line in body.splitlines() if line.startswith('ARCHIVE\t')), '')
        fields = line.split('\t')
        if len(fields)!=6 or not re.fullmatch(r'/tmp/envscope-logs\.[A-Za-z0-9]{10}/logs\.tar\.gz', fields[1]): raise ValueError('归档响应无效')
        remote, size, digest, count, tar_status = fields[1], int(fields[2]), fields[3], int(fields[4]), int(fields[5])
        if not 0<size<=application_files.MAX_ARCHIVE+1048576 or not re.fullmatch(r'[a-f0-9]{64}', digest): raise ValueError('归档大小或摘要无效')
        emit('archive_start', pid=process['pid'], size=size, sha256=digest, fileCount=count, cutoff=cutoff, tarStatus=tar_status)
        sha = hashlib.sha256()
        for offset in range(0,size,application_files.CHUNK):
            rc, text = terminal.run('dd if='+shlex.quote(remote)+' bs='+str(application_files.CHUNK)+' skip='+str(offset//application_files.CHUNK)+' count=1 status=none | base64', timeout=60)
            if rc: raise ValueError('归档传输失败')
            encoded = ''.join(text.split()); chunk = base64.b64decode(encoded,validate=True)
            if len(chunk)!=min(application_files.CHUNK,size-offset): raise ValueError('归档分块长度错误')
            sha.update(chunk); emit('archive_chunk', data=encoded)
        if sha.hexdigest()!=digest: raise ValueError('归档摘要不一致')
        emit('archive_end', sha256=digest)
    finally:
        if remote:
            try: terminal.run('rm -rf -- '+shlex.quote(posixpath.dirname(remote)), timeout=15)
            except Exception: pass


def main():
    ip, name, version, encoded = sys.argv[1:]
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name): raise ValueError('应用名不适合进程匹配')
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    snapshot = load(data/'versions'/version/'jumpserver-snapshot.json')
    assets = [a for a in snapshot.get('assets', []) if ip in re.split(r'[\s,]+', str(a.get('ip','')))]
    if len(assets)!=1: raise ValueError('JumpServer 资产无法唯一定位')
    terminal, elevation = application_check.open_terminal(assets[0], load(data/'secrets/cookies.json'), load(data/'secrets/ip-password-map.json'))
    try:
        command = 'python3 -c '+shlex.quote('import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"<inspection>","exec"))')+' '+shlex.quote(name)
        rc, output = terminal.run(command, timeout=90)
        if rc: raise RuntimeError('远端进程探针失败 (rc=%s)' % rc)
        result = json.loads(output)
        emit('host', ip=ip, elevation=elevation, observedAt=result['observedAt'], listenerStatus=result['listenerStatus'], count=len(result['processes']))
        for process in result['processes']:
            raw = dict(process)
            process['command'] = server_inventory.redact(process.get('command',''))
            process['jvmArgs'] = [application_files.redact_config(server_inventory.redact(arg)) for arg in process.get('jvmArgs', [])]
            emit('process', process=process)
            try:
                sample = application_check.probe(terminal, 'target', process['pid'])
                checked = sample['processes'].get(process['pid'])
                if not checked or checked.get('startTicks') != process['startTicks']: raise ValueError('进程身份已变化')
                scopes = []
                try:
                    catalog = application_files.catalogue(terminal, checked, {'appName':name}, kind='log')
                    scopes.extend(catalog.get('logScopes', []))
                    for warning in catalog.get('warnings', []): emit('warning', message=warning)
                except Exception as exc: emit('warning', message='日志目录发现失败：'+type(exc).__name__)
                for path in raw['stdoutPaths']:
                    root = posixpath.dirname(path)
                    if len([x for x in root.split('/') if x])>=3:
                        scopes.append({'root':root, 'patterns':[posixpath.basename(path)+'*']})
                scopes = [dict(t) for t in { (s['root'], tuple(s['patterns'])): {'root':s['root'], 'patterns':s['patterns']} for s in scopes }.values()]
                emit('scopes', pid=process['pid'], scopes=scopes)
                transfer(terminal, checked, scopes)
            except Exception as exc:
                emit('warning', pid=process['pid'], message='日志采集失败：'+type(exc).__name__+' '+str(exc)[:150])
    finally: terminal.close()


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        emit('error', message=type(exc).__name__+': '+str(exc)[:150])
        sys.exit(1)
