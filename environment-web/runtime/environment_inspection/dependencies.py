"""Summarize environment-scoped config, query registry evidence, aggregate TCP results."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent


def unlisted_urls(urls, components):
    """Suppress only a URL represented by a component on the same source line."""
    found = set()
    for component in components:
        try:
            host = str(component.get('host') or '').rstrip('.').lower()
            port = int(component.get('port'))
            line = int(component.get('line'))
            if host and 0 < port <= 65535:
                found.add((str(component.get('file') or ''), line, host, port))
        except (TypeError, ValueError):
            continue
    result = []
    for item in urls:
        try:
            parsed = urlsplit(item.get('url') or '')
            port = parsed.port or {'http':80, 'https':443}.get(parsed.scheme.lower())
            key = (str(item.get('file') or ''), int(item.get('line')), (parsed.hostname or '').rstrip('.').lower(), port)
        except (TypeError, ValueError):
            result.append(item); continue
        if key not in found: result.append(item)
    return result


def display_applications(applications):
    """Show identifiers and names, retaining explicitly unresolved candidates."""
    result = []
    for app in applications:
        app_id, name = app.get('appId'), app.get('appName')
        if app_id and app.get('confidence') not in ('AMBIGUOUS', 'UNKNOWN'):
            value = {'appId':app_id or '', 'appName':name or ''}
        else:
            candidates = [{'appId':c.get('appId') or '', 'appName':c.get('appName') or ''}
                          for c in (app.get('candidates') or []) if c.get('appId') or c.get('appName')]
            if not candidates and (app_id or name): candidates = [{'appId':app_id or '', 'appName':name or ''}]
            value = {'状态':'待确认', '候选':candidates} if candidates else {'状态':'待确认'}
        if value not in result: result.append(value)
    return result or [{'状态':'待确认'}]


def tcp_display_rows(report):
    header=['方向','对端IP','对端端口集合','对端进程 PID/名称','进程证据','应用ID/名称']
    rows=[]
    for item in aggregate_tcp(report):
        rows.append([item['direction'],item['peerIp'],item['ports'],item['processes'],item['evidence'],display_applications(item['applications'])])
    return [header]+rows


def profiles_from(processes_by_ip):
    profiles=set()
    for processes in processes_by_ip.values():
        for process in processes:
            try: args=shlex.split(process.get('command',''))
            except ValueError: continue
            for arg in args:
                for prefix in ('-Dspring.profiles.active=','--spring.profiles.active=','--spring.cloud.config.profile='):
                    if arg.startswith(prefix):
                        profiles.update(x for x in arg[len(prefix):].split(',') if re.fullmatch(r'[A-Za-z0-9_.-]+',x))
    return sorted(profiles)


def run_container(script, args):
    proc=subprocess.run([sys.executable,str(HERE/script),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=90)
    return json.loads(proc.stdout)


def collect_config(app_id, environment, processes, output):
    profiles=profiles_from(processes)
    encoded=base64.b64encode(json.dumps(profiles).encode()).decode()
    config=run_container('config_bridge.py',[app_id,environment,encoded])
    output=Path(output)/('配置脱敏摘要-'+uuid.uuid4().hex[:8]);output.mkdir(parents=True,mode=0o700);output.chmod(0o700)
    for entry in config['files']:
        relative=Path(entry['file'])
        if relative.is_absolute() or any(p in ('..','.') for p in relative.parts): raise ValueError('无效配置路径')
        target=output/Path(str(relative)+'.summary.json');target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        summary={'file':entry,'components':[x for x in config['components'] if x['file']==entry['sourcePath']], 'urls':[x for x in config['urls'] if x['file']==entry['sourcePath']]}
        with target.open('x',encoding='utf-8') as f:json.dump(summary,f,ensure_ascii=False,indent=2)
        target.chmod(0o600)
    config['downloadPath']=str(output)
    config['warnings'].append('生产配置原文未导出；仅保存脱敏后的端点、URL 和文件摘要')
    if not profiles:config['warnings'].append('无法从进程参数确认活动 profile，其他目录内的 profile 文件不纳入')
    if not config['files']:config['warnings'].append('未发现指定环境的已下载配置文件')
    return config


def inspect_dubbo(app_id, environment, snap, config, processes):
    regs=[];unsupported=[]
    for e in config.get('components',[]):
        if e.get('kind')!='Dubbo 注册中心':continue
        if e.get('registryProtocol')=='zookeeper':
            candidate={'host':e['host'],'port':e['port']}
            if candidate not in regs:regs.append(candidate)
        else:unsupported.append(e.get('registryProtocol','UNKNOWN'))
    observed={}
    for ip,rows in processes.items():
        ports=[]
        for p in rows:
            for line in p.get('listeners',[]):
                parts=line.split()
                if len(parts)>3:
                    m=re.search(r':(\d+)$',parts[3])
                    if m and m.group(1) not in ports:ports.append(m.group(1))
        observed[ip]=ports
    query={'registries':regs,'endpoints':snap['endpoints'],'observedPorts':observed}
    encoded=base64.b64encode(json.dumps(query).encode()).decode()
    result=run_container('dubbo_bridge.py',[snap['version'],app_id,environment,encoded])
    if unsupported:
        result['warnings'].append('以下注册中心类型未支持：'+', '.join(sorted(set(unsupported))))
        if result['status']=='COMPLETE':result['status']='PARTIAL'
    if config.get('dubboEvidence') and not regs: result['warnings'].append('已发现 Dubbo 配置标记，但未能确认受支持的注册中心地址')
    return result


def aggregate_tcp(report):
    """Mirror the UI's peer-IP, evidence-type and candidate-process grouping."""
    groups={}
    for direction in ('upstream','downstream'):
        for flow in report.get(direction,[]):
            remote=flow.get('remoteProcesses') or []
            owners=remote or flow.get('peerListeningProcesses') or []
            identities=sorted({(str(p.get('pid','')),str(p.get('processName',''))) for p in owners})
            key=(direction,str((flow.get('peer') or {}).get('ip','')), 'connection' if remote else 'listener',tuple(identities))
            entry=groups.setdefault(key,{'direction':direction,'peerIp':key[1],'processes':identities,'evidence':'连接进程' if remote else '监听候选' if owners else '未解析','ports':set(),'services':{},'applications':{},'owners':{},'reasons':set(),'resolutions':set()})
            port=(flow.get('peer') or {}).get('port')
            if port is not None:entry['ports'].add(str(port))
            service=flow.get('service') or {}
            if service.get('name'):
                encoded=json.dumps(service,ensure_ascii=False,sort_keys=True);entry['services'][encoded]=service
            for owner in owners:
                evidence={'pid':owner.get('pid'),'processName':owner.get('processName'),'confidence':owner.get('confidence')}
                encoded=json.dumps(evidence,ensure_ascii=False,sort_keys=True);entry['owners'][encoded]=evidence
                app=owner.get('application') or {}
                encoded=json.dumps({'appId':app.get('appId'),'appName':app.get('appName'),'confidence':app.get('confidence'),'candidates':app.get('candidates')},ensure_ascii=False,sort_keys=True)
                entry['applications'][encoded]=json.loads(encoded)
            if flow.get('reason'):entry['reasons'].add(str(flow['reason']))
            entry['resolutions'].add(str(flow.get('resolution') or 'UNKNOWN'))
    return [{'direction':v['direction'],'peerIp':v['peerIp'],'processes':v['processes'],'evidence':v['evidence'],'ownerDetails':list(v['owners'].values()),'ports':sorted(v['ports']),'services':list(v['services'].values()),'applications':list(v['applications'].values()),'resolutions':sorted(v['resolutions']),'reasons':sorted(v['reasons'])} for v in groups.values()]
