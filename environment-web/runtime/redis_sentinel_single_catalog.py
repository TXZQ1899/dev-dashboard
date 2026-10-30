"""Import a host-network Redis with corroborated zero-replica Sentinel view."""
import json,os
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
 import deployment_catalog as db
 import redis_loopback_catalog as base
except ModuleNotFoundError:
 from runtime import deployment_catalog as db,redis_loopback_catalog as base


def plan(audit,deep,detail,paths,sentinels):
 version=audit.get('sourceVersion');reports=(deep,detail,paths,sentinels)
 if not version or any(x.get('sourceVersion')!=version for x in reports):raise ValueError('核查报告版本不一致')
 def idx(report):
  rows=report.get('results',[]);counts=Counter((x.get('assetId'),x.get('pid')) for x in rows)
  return {(x.get('assetId'),x.get('pid')):x for x in rows if counts[(x.get('assetId'),x.get('pid'))]==1}
 dm,cm,pm=map(idx,(deep,detail,paths));sent=sentinels.get('results',[])
 coverage=len(sent)==9 and all(x.get('status')=='OBSERVED' and (x.get('info') or {}).get('status')=='OBSERVED' for x in sent)
 accepted=[];unresolved=[]
 for a in audit.get('results',[]):
  key=(a.get('assetId'),a.get('pid'));d=dm.get(key) or {};c=cm.get(key) or {};p=pm.get(key) or {}
  if not c or not p:continue
  meta={}
  for line in a.get('lines',[]):
   parts=line.split('\t')
   if len(parts)>=3 and parts[0]=='META':meta[parts[1]]=parts[2]
  container=a.get('container') or {};detail=c.get('detail') or {};pathsdetail=p.get('detail') or {};scripts=detail.get('scripts') or [];run=[x for x in scripts if x.get('path')=='/opt/bitnami/scripts/redis/run.sh'];config=[x for x in detail.get('configs',[]) if x.get('path')=='/opt/bitnami/redis/etc/redis.conf'];assign=pathsdetail.get('assignments') or []
  relevant=[];ambiguous=False
  for s in sent:
   for m in (s.get('info') or {}).get('masters',[]):
    if m.get('ip')==a.get('ip'):relevant.append((s,m))
    replicas=m.get('replicas')
    if not isinstance(replicas,list):coverage=False
    elif any(x.get('ip')==a.get('ip') for x in replicas):ambiguous=True
  sections=(d.get('runtime') or {}).get('sections') or {};server=sections.get('server') or {};rep=sections.get('replication') or {};cluster=sections.get('cluster') or {};runtime=(d.get('runtime') or {}).get('config') or {};clients=(d.get('runtime') or {}).get('clients') or {}
  apps=a.get('applicationCandidates') or {};newapps=d.get('applicationCandidates') or {};connections=d.get('connections') or [];listeners=d.get('listeners') or [];values=(config[0].get('directives') or []) if len(config)==1 else [];conf={k:v for k,v in values}
  has_arg=(len(run)==1 and any(x.get('hasArgsArray') and x.get('hasConfPath') and 'REDIS_BASE_DIR' in x.get('redisVariables',[]) for x in run[0].get('safeLines',[])))
  has_exec=(len(run)==1 and any(x.get('hasExec') and x.get('hasRedisServer') and x.get('hasArgsArray') for x in run[0].get('safeLines',[])))
  base_vars={(x.get('key'),x.get('value')) for x in assign};sentinel=relevant[0][1] if len(relevant)==1 else {};source=relevant[0][0] if len(relevant)==1 else {}
  good=(coverage and not ambiguous and len(relevant)==1 and source.get('ip')==a.get('ip') and source.get('assetId')==a.get('assetId') and sentinel.get('port')=='6379' and sentinel.get('flags')=='master' and sentinel.get('num-slaves')=='0' and sentinel.get('replicas')==[]
   and a.get('status')==d.get('status')==c.get('status')==p.get('status')=='OBSERVED' and detail.get('status')=='OBSERVED' and (d.get('runtime') or {}).get('status')=='OBSERVED' and d.get('ip')==c.get('ip')==p.get('ip')==a.get('ip')
   and a.get('startTicks') and a['startTicks']==d.get('startTicks')==c.get('startTicks')==p.get('startTicks')
   and all(x.get('observedAt') for x in (a,d,c,p)) and a.get('frames',0)>=2 and d.get('frames',0)>=2 and not a.get('warnings') and not d.get('warnings')
   and a.get('containerMatches')==1 and a.get('dockerWarnings')==[] and container.get('id') and container.get('name') and container.get('initPid')==a.get('pid') and container.get('startTicks')==a['startTicks']
   and meta.get('netns') and meta['netns']==meta.get('hostnetns')==container.get('networkNamespace') and pathsdetail.get('exe')=='/opt/bitnami/redis/bin/redis-server'
   and ('BITNAMI_ROOT_DIR','/opt/bitnami') in base_vars and ('REDIS_BASE_DIR','${BITNAMI_ROOT_DIR}/redis') in base_vars and has_arg and has_exec
   and len(config)==1 and not config[0].get('unparsed') and not any(k in ('include','slaveof','replicaof') or v=='UNPARSED' for k,v in values) and conf.get('cluster-enabled','no')=='no'
   and conf.get('bind')==runtime.get('bind')=='0.0.0.0 ::' and conf.get('port')==runtime.get('port')=='6379' and conf.get('dir')==runtime.get('dir') and conf.get('dbfilename')==runtime.get('dbfilename')
   and server.get('redis_mode')=='standalone' and server.get('run_id') and rep.get('role')=='master' and rep.get('connected_slaves')=='0' and cluster.get('cluster_enabled')=='0' and clients.get('replicaCount')==0
   and len(connections)==d.get('connectionCount') and all(x.get('peer',{}).get('ip')==a.get('ip') and x.get('local',{}).get('port')==6379 for x in connections)
   and any(x.get('ip')=='*' and x.get('port')==6379 and x.get('protocol')=='tcp' for x in listeners)
   and set(apps)==set(newapps)=={'TEST','SIMULATION','PRODUCT'} and not any(apps.values()) and not any(newapps.values()))
  if good:
   accepted.append({'audit':a,'identity':{'assetId':a['assetId'],'containerName':container['name']},'sourceKind':'REDIS_CONTAINER_NODE','configPath':config[0]['path'],'endpoint':{'ip':'*','port':6379,'protocol':'tcp'},'hostPort':6379,'listenerScope':'HOST_NETWORK','clusterEvidence':'SENTINEL_ZERO_REPLICAS','groupingBasis':'SENTINEL_CONFIG_RUNTIME_TCP','matchingBasis':'REDIS_CONFIG_RUNTIME_SENTINEL_TCP','reason':'Redis 配置、Sentinel 和 TCP 只读核查','startup':{}})
  else:unresolved.append({'ip':a.get('ip'),'reason':'Sentinel、配置、容器或成员关系存在冲突或证据不足'})
 return {'accepted':accepted,'unresolved':unresolved}


def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text());names=('config-audit.json','deep-12.json','bitnami-detail.json','bitnami-paths.json','sentinels-all.json');reports=[read(root/n) for n in names];version=read(data/'current.json')['id']
 for value in reports[1:]:
  t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
  if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
 p=plan(*reports)
 if version!=reports[0]['sourceVersion']:raise ValueError('当前快照已切换')
 if not args.apply:
  print(json.dumps({'candidates':[{'ip':x['audit']['ip'],'endpoint':x['endpoint'],'config':x['configPath']} for x in p['accepted']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
 evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
 for value in reports:
  payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
  if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
  if not path.exists():
   tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
 result=base.import_report(db.Catalog.from_environment(),*reports[:4],version,can_apply=lambda:read(data/'current.json').get('id')==version,prepared=p,extra_reports=reports[4:],mapper_version='redis-sentinel-single-v1',source_path='checks/redis-review/sentinels-all.json')
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
