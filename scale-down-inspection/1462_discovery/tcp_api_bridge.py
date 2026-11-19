"""Start EnvScope's existing per-process TCP check, sequentially for the selected environment."""
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

BASE = 'http://127.0.0.1:3000'


def emit(event, **fields):
    print(json.dumps({'event':event, **fields}, ensure_ascii=False), flush=True)


def request(path, payload=None):
    headers = {'Accept':'application/json'}
    if payload is not None:
        headers.update({'Content-Type':'application/json','Origin':BASE})
    req = Request(BASE+path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    try:
        with urlopen(req, timeout=20) as response: return json.load(response)
    except HTTPError as exc:
        try: message = json.load(exc).get('error', '请求失败')
        except Exception: message = '请求失败'
        raise RuntimeError('EnvScope API %d：%s' % (exc.code, message))


def main():
    app_id, environment = sys.argv[1:3]
    data = Path(os.environ.get('ENVSCOPE_DATA','/data'))
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    version = read(data/'current.json')['id']; folder=data/'versions'/version
    apps = [a for a in read(folder/'devops.json').get('apps',[]) if str(a.get('id'))==app_id]
    if len(apps)!=1: raise ValueError('应用无法唯一定位')
    app=apps[0]; name=app.get('name','')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name): raise ValueError('应用名无效')
    deployments=app.get('envs',{}).get(environment,[])
    ips=sorted({str(d.get('ip')) for d in deployments if d.get('ip')})
    snapshot=read(folder/'jumpserver-snapshot.json')
    for ip in ips:
        try:
            if read(data/'current.json')['id'] != version: raise ValueError('当前快照已切换')
            assets=[a for a in snapshot.get('assets',[]) if ip in re.split(r'[\s,]+',str(a.get('ip','')))]
            if len(assets)!=1: raise ValueError('目标资产无法唯一定位')
            candidates=[]
            for p in (assets[0].get('inspection') or {}).get('processes',[]):
                cmd=p.get('command','')
                if re.search(r'(?:^|\s)(?:-Dappid=|-Dspring\.application\.name=|--spring\.application\.name=)'+re.escape(name)+r'(?:\s|$)',cmd) or re.search(r'(?:^|[/\s])'+re.escape(name)+r'(?:-exec)?\.(?:jar|war)(?:\s|$)',cmd): candidates.append(p)
            if len(candidates)!=1: raise ValueError('快照中应用进程候选数为 %d，无法唯一选择' % len(candidates))
            pid=candidates[0]['pid']; emit('start',ip=ip,pid=pid,seconds=300)
            state=request('/api/settings/application-check',{'asset':assets[0]['id'],'pid':pid,'environment':environment,'duration':300})
            job=state.get('job') or {}; task=job.get('id')
            if not task: raise ValueError('未返回任务 ID')
            last=''; last_log=0
            while True:
                state=request('/api/settings/application-check?'+urlencode({'id':task}));job=state.get('job') or {}
                phase=job.get('phase',''); progress=job.get('progress') or {}
                if phase!=last or time.monotonic()-last_log>=20:
                    emit('progress',ip=ip,phase=phase,samples=progress.get('samples'),elapsed=progress.get('elapsedSeconds'));last=phase;last_log=time.monotonic()
                if job.get('status')!='running': break
                time.sleep(3)
            report=state.get('report')
            if not report: raise ValueError('任务结束但没有报告')
            emit('report',ip=ip,report=report)
        except (OSError,ValueError,RuntimeError,URLError) as exc:
            emit('error',ip=ip,message=str(exc)[:200])
    emit('finished', version=version)


if __name__=='__main__':
    try: main()
    except Exception as exc: emit('fatal',message=type(exc).__name__+': '+str(exc)[:200]);sys.exit(1)
