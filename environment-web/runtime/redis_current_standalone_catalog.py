"""Import current single-node candidates corroborated by live TCP checks."""
import hashlib,json,os,re
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
 import deployment_catalog as db
 import redis_loopback_catalog as base
except ModuleNotFoundError:
 from runtime import deployment_catalog as db,redis_loopback_catalog as base


def plan(config,live,startup,docker,sentinels,checks,discovery=None):
 version=config.get('sourceVersion');reports=(live,startup,docker,sentinels,checks)+((discovery,) if discovery is not None else ())
 if not version or any(x.get('sourceVersion')!=version for x in reports):raise ValueError('核查报告版本不一致')
 def idx(report):
  rows=report.get('results',[]);counts=Counter((x.get('assetId'),x.get('pid')) for x in rows)
  return {(x.get('assetId'),x.get('pid')):x for x in rows if counts[(x.get('assetId'),x.get('pid'))]==1}
 lm,sm,dm=map(idx,(live,startup,docker));xm=idx(discovery) if discovery is not None else {};cm={}
 for check in checks.get('results',[]):
  target=check.get('target') or {};key=target.get('assetId')
  if key in cm:cm[key]=None
  else:cm[key]=check
 sent=sentinels.get('results',[]);coverage=len(sent)==9 and all(x.get('status')=='OBSERVED' and (x.get('info') or {}).get('status')=='OBSERVED' for x in sent);sent_ips=set()
 for s in sent:
  for m in (s.get('info') or {}).get('masters',[]):
   sent_ips.add(m.get('ip'));repl=m.get('replicas')
   if isinstance(repl,list):sent_ips.update(x.get('ip') for x in repl)
   else:coverage=False
 accepted=[];unresolved=[]
 for a in config.get('results',[]):
  key=(a.get('assetId'),a.get('pid'));b=lm.get(key) or {};s=sm.get(key) or {};dock=dm.get(key) or {};found=xm.get(key) or {};check=cm.get(a.get('assetId')) or {}
  if not check:continue
  target=check.get('target') or {};cov=check.get('coverage') or {};runtime=b.get('runtime') or {};sections=runtime.get('sections') or {};server=sections.get('server') or {};rep=sections.get('replication') or {};cluster=sections.get('cluster') or {};cfg=runtime.get('config') or {};clients=runtime.get('clients') or {};apps=a.get('applicationCandidates') or {};apps2=b.get('applicationCandidates') or {};conn=b.get('connections') or [];listeners=b.get('listeners') or [];ip=a.get('ip');meta={}
  for line in a.get('lines',[]):
   p=line.split('\t')
   if len(p)>=3 and p[0]=='META':meta[p[1]]=p[2]
  try:
   seen=datetime.fromisoformat(cov['lastObservedAt'].replace('Z','+00:00'));end=datetime.fromisoformat(checks['completedAt'].replace('Z','+00:00'));fresh=timedelta(minutes=-5)<=end-seen<=timedelta(hours=2)
  except (ValueError,KeyError,TypeError):fresh=False
  common=(coverage and ip not in sent_ips and a.get('status')==b.get('status')=='OBSERVED' and a.get('startTicks') and a['startTicks']==b.get('startTicks')
   and a.get('observedAt') and b.get('observedAt') and a.get('frames',0)>=2 and b.get('frames',0)>=2 and not a.get('warnings') and not b.get('warnings')
   and runtime.get('status')=='OBSERVED' and server.get('redis_mode')=='standalone' and server.get('run_id') and rep.get('role')=='master' and rep.get('connected_slaves')=='0' and cluster.get('cluster_enabled')=='0'
   and clients.get('replicaCount')==0 and cfg.get('slaveof','')=='' and cfg.get('replicaof','')=='' and cfg.get('port')=='6379'
   and len(conn)==b.get('connectionCount') and all(x.get('local',{}).get('port')==6379 and x.get('local',{}).get('protocol')=='tcp' for x in conn)
   and listeners and all(x.get('port')==6379 and x.get('protocol')=='tcp' for x in listeners)
   and set(apps)==set(apps2)=={'TEST','SIMULATION','PRODUCT'} and not any(apps.values()) and not any(apps2.values())
   and check.get('sourceVersion')==version and check.get('status') in ('complete','partial') and target.get('serverIp')==ip and target.get('assetId')==a.get('assetId')
   and fresh and cov.get('samples',0)>=100 and cov.get('actualSeconds',0)>=55 and check.get('downstreamCount')==0)
  if not common:unresolved.append({'ip':ip,'reason':'当前运行状态、连接采样或应用关联不完整'});continue
  kind=None;extra={'checkReport':check.get('reportPath'),'checkReportSha256':check.get('reportSha256'),'checkReportEvidence':'checks/redis-review/evidence/check-'+str(check.get('reportSha256'))+'.json','checkStatus':check.get('status'),'checkWarnings':check.get('warnings',[])}
  if s:
   lines=s.get('lines') or [];processes=[x for x in lines if x.startswith('PROCESS\t')];files=[x.split('\t')[1] for x in lines if x.startswith('FILE\t') and x.endswith('\tREADABLE')];refs=[x.split('\t')[2] for x in lines if x.startswith('REFERENCE\t') and len(x.split('\t'))>=4 and x.split('\t')[3]=='DIRECT'];values=[]
   for line in lines:
    p=line.split('\t')
    if len(p)>=4 and p[0]=='DIRECTIVE':values.append((p[2],p[3]))
   last={k:v for k,v in values};path=files[0] if len(files)==1 else None;unit=meta.get('service');bind=last.get('bind');checkok=target.get('pid')==a.get('pid') and target.get('startTicks')==a['startTicks'] and not check.get('warnings')
   host_ok=(s.get('status')=='OBSERVED' and s.get('startTicks')==a['startTicks'] and s.get('observedAt') and processes==['PROCESS\t'+str(a['pid'])+'\tredis-server']
    and a.get('container') is None and a.get('containerMatches')==0 and a.get('dockerWarnings')==[] and meta.get('netns') and meta['netns']==meta.get('hostnetns') and unit
    and path and path in refs and last.get('port')=='6379' and bind==cfg.get('bind') and bind in (ip,'0.0.0.0')
    and not any(k in ('include','slaveof','replicaof') or v=='UNPARSED' for k,v in values) and last.get('cluster-enabled','no')=='no'
    and not any(x.startswith(('DIRECTIVE_UNPARSED\t','RENAME_CLUSTER\t')) for x in lines) and checkok)
   if host_ok:
    extra['configuration']='SYSTEMD_OR_INIT';extra['configRuntimeDbfilenameMismatch']=bool(last.get('dbfilename') and cfg.get('dbfilename') and last['dbfilename']!=cfg['dbfilename']);extra['history']='无完整近期日志，失联历史副本未排除'
    accepted.append({'audit':a,'startup':{'detail':{'startup':{'unit':unit}}},'configPath':path,'endpoint':{'ip':ip if bind==ip else '*','port':6379,'protocol':'tcp'},'hostPort':6379,'listenerScope':'HOST_ADDRESS' if bind==ip else 'HOST_NETWORK','clusterEvidence':'CURRENT_INFO_CLIENT_SENTINEL_TCP','groupingBasis':'CONFIG_RUNTIME_SENTINEL_LONG_TCP','matchingBasis':'REDIS_CONFIG_RUNTIME_SENTINEL_LONG_TCP','reason':'Redis 配置、当前复制状态及上下游检测','evidenceExtra':extra});kind='host'
  elif dock:
   cont=dock.get('container') or {};dd=dock.get('detail') or {};hostapp=dock.get('publishedApplicationCandidates') or {};mapped=target.get('dockerTarget') or {};maps=mapped.get('mappings') or []
   direct=target.get('pid')==a.get('pid') and target.get('startTicks')==a['startTicks'];proxy=(mapped.get('pid')==a.get('pid') and mapped.get('startTicks')==a['startTicks'] and any(x.get('containerId')==cont.get('id') for x in maps))
   limits=check.get('warnings') or [];allowed_warning=(not limits or (proxy and all('连接数量超过 100' in x for x in limits)))
   bindings=(cont.get('ports') or {}).get('6379/tcp') or [];mapped_ports={int(x['HostPort']) for x in bindings if x.get('HostIp') in ('0.0.0.0',ip) and str(x.get('HostPort','')).isdigit()};ips=cont.get('ips') or [];container_ips=[x for x in ips if any(y.get('ip') in ('*',x,'0.0.0.0') for y in listeners)]
   docker_ok=(dock.get('status')=='OBSERVED' and dock.get('observedAt') and dock.get('startTicks')==a['startTicks'] and cont.get('id') and cont.get('name') and cont.get('initPid')==a['pid'] and cont.get('startTicks')==a['startTicks']
    and a.get('container') and a['container'].get('id')==cont['id'] and cont.get('networkNamespace')==a['container'].get('networkNamespace') and len(container_ips)==1 and mapped_ports=={6379} and dock.get('publishedPort')==6379
    and set(hostapp)=={'TEST','SIMULATION','PRODUCT'} and not any(hostapp.values()) and dd.get('status')=='OBSERVED' and dd.get('scanComplete') is True and dd.get('configFiles')==[]
    and dd.get('argsRedisServerOnly') is True and dd.get('imageCmdRedisServerOnly') is True and dd.get('entrypointExecArgv') is True and dd.get('entrypointMentionsConf') is False and dd.get('configArgs')==[] and dd.get('modeFlags')==[] and dd.get('hasExtraArguments') is False
    and (direct or proxy) and allowed_warning)
   if docker_ok:
    extra.update(configuration='NO_CONFIG_ARGUMENT_IN_SCANNED_PATHS',containerId=cont['id'],containerName=cont['name'],publishedPort=6379,history='仅已发现 Sentinel 和现存观察，失联历史副本未排除')
    accepted.append({'audit':a,'identity':{'assetId':a['assetId'],'containerName':cont['name']},'sourceKind':'REDIS_CONTAINER_NODE','configPath':'NO_CONFIG_ARGUMENT','endpoint':{'ip':container_ips[0],'port':6379,'protocol':'tcp'},'hostPort':6379,'containerPort':6379,'portBasis':'DOCKER_PUBLISHED','listenerScope':'CONTAINER','clusterEvidence':'CURRENT_INFO_CLIENT_SENTINEL_TCP','groupingBasis':'CONTAINER_DEFAULT_RUNTIME_SENTINEL_TCP','matchingBasis':'REDIS_CONTAINER_RUNTIME_SENTINEL_TCP','reason':'Redis 容器启动、运行状态及上下游核查','startup':{},'evidenceExtra':extra});kind='docker'
  elif found:
   detail=found.get('detail') or {};procs=detail.get('processes') or [];exe=detail.get('exePath') or '';service=detail.get('service') or {};files=detail.get('files') or []
   checkok=target.get('pid')==a.get('pid') and target.get('startTicks')==a['startTicks'] and not check.get('warnings')
   process_ok=(found.get('status')=='OBSERVED' and found.get('startTicks')==a['startTicks'] and found.get('observedAt') and detail.get('status')=='OBSERVED' and not detail.get('discoveryTruncated')
    and exe.startswith('/') and exe.endswith('/redis-server') and detail.get('cwd')==cfg.get('dir') and service.get('unit') is None and len(procs)==1 and procs[0].get('pid')==a['pid'] and procs[0].get('name')=='redis-server'
    and not any(f.get('pidfileMatches') for f in files) and a.get('container') is None and a.get('containerMatches')==0 and a.get('dockerWarnings')==[] and meta.get('netns') and meta['netns']==meta.get('hostnetns')
    and cfg.get('bind')=='' and checkok)
   if process_ok:
    extra.update(configuration='RUNTIME_CONFIG_FILE_UNRESOLVED',exePath=exe,cwd=detail.get('cwd'),configFileCandidates=[f.get('path') for f in files],history='未找到进程实际配置文件，失联历史副本未排除')
    accepted.append({'audit':a,'identity':{'assetId':a['assetId'],'exePath':exe,'port':6379},'sourceKind':'REDIS_HOST_PROCESS','sourceMatchingBasis':'ASSET_EXECUTABLE_LISTENER','configPath':'UNIDENTIFIED','endpoint':{'ip':'*','port':6379,'protocol':'tcp'},'hostPort':6379,'listenerScope':'HOST_NETWORK','clusterEvidence':'CURRENT_INFO_CLIENT_SENTINEL_TCP','groupingBasis':'RUNTIME_CONFIG_SENTINEL_LONG_TCP','matchingBasis':'REDIS_RUNTIME_SENTINEL_LONG_TCP','reason':'Redis 运行配置、客户端及上下游检测，启动配置文件待识别','startup':{},'evidenceExtra':extra});kind='process'
  if not kind:unresolved.append({'ip':ip,'reason':'配置、容器或上下游报告不足以关联实例'})
 return {'accepted':sorted(accepted,key=lambda x:x['audit']['ip']),'unresolved':unresolved}


def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text());names=('current-config-eight.json','current-deep-eight.json','startup-current-two.json','container-config-current.json','sentinels-current.json','checks-current.json','config-discovery-current.json');reports=[read(root/n) for n in names];version=read(data/'current.json')['id']
 for value in reports:
  t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
  if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
 def checked_report_bytes():
  result=[]
  for check in reports[5].get('results',[]):
   rel=check.get('reportPath') or '';sha=check.get('reportSha256') or ''
   if not re.fullmatch(r'checks/[A-Za-z0-9-]{1,80}/report\.json',rel) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('上下游报告来源无效')
   payload=(data/rel).read_bytes()
   if hashlib.sha256(payload).hexdigest()!=sha:raise ValueError('上下游报告已变化')
   result.append((sha,payload))
  return result
 raw_checks=checked_report_bytes()
 p=plan(*reports)
 if version!=reports[0]['sourceVersion']:raise ValueError('当前快照已切换')
 if not args.apply:
  print(json.dumps({'candidates':[{'ip':x['audit']['ip'],'endpoint':x['endpoint'],'hostPort':x['hostPort'],'basis':x['groupingBasis']} for x in p['accepted']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
 evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
 for value in reports:
  payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
  if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
  if not path.exists():
   tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
 for sha,payload in raw_checks:
  path=evidence/('check-'+sha+'.json')
  if path.exists() and path.read_bytes()!=payload:raise ValueError('上下游证据校验失败')
  if not path.exists():
   tmp=path.with_suffix('.tmp');tmp.write_bytes(payload);tmp.chmod(0o600);os.replace(tmp,path)
 def can_apply():
  if read(data/'current.json').get('id')!=version:return False
  try:return checked_report_bytes()==raw_checks
  except (ValueError,OSError):return False
 result=base.import_report(db.Catalog.from_environment(),*reports[:4],version,can_apply=can_apply,prepared=p,extra_reports=reports[4:],mapper_version='redis-current-standalone-v1',source_path='checks/redis-review/checks-current.json')
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
