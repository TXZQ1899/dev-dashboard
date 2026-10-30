"""Import a host Redis supported by current full retained logs and live checks."""
import json,os,re
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
 import deployment_catalog as db
 import redis_loopback_catalog as base
except ModuleNotFoundError:
 from runtime import deployment_catalog as db,redis_loopback_catalog as base


def plan(audit,deep,startup,logs,stats,sentinels):
 version=audit.get('sourceVersion');reports=(deep,startup,logs,stats,sentinels)
 if not version or any(x.get('sourceVersion')!=version for x in reports):raise ValueError('核查报告版本不一致')
 def idx(report):
  rows=report.get('results',[]);counts=Counter((x.get('assetId'),x.get('pid')) for x in rows)
  return {(x.get('assetId'),x.get('pid')):x for x in rows if counts[(x.get('assetId'),x.get('pid'))]==1}
 dm,sm,lm,tm=map(idx,(deep,startup,logs,stats));sent=sentinels.get('results',[])
 coverage=len(sent)==9 and all(x.get('status')=='OBSERVED' and (x.get('info') or {}).get('status')=='OBSERVED' for x in sent);sent_ips=set()
 for s in sent:
  for m in (s.get('info') or {}).get('masters',[]):
   sent_ips.add(m.get('ip'));replicas=m.get('replicas')
   if isinstance(replicas,list):sent_ips.update(x.get('ip') for x in replicas)
   else:coverage=False
 accepted=[];unresolved=[]
 for a in audit.get('results',[]):
  key=(a.get('assetId'),a.get('pid'));d=dm.get(key) or {};s=sm.get(key) or {};log=lm.get(key) or {};st=tm.get(key) or {}
  if not s or not log or not st:continue
  meta={}
  for line in a.get('lines',[]):
   p=line.split('\t')
   if len(p)>=3 and p[0]=='META':meta[p[1]]=p[2]
  info={};runtime={};client=None
  if d.get('method')=='python-resp':
   raw=d.get('runtime') or {};sections=raw.get('sections') or {}
   for x in sections.values():
    if isinstance(x,dict):info.update(x)
   runtime=raw.get('config') or {};client=(raw.get('clients') or {}).get('replicaCount');valid_runtime=raw.get('status')=='OBSERVED'
  else:
   valid_runtime=d.get('method')=='redis-cli'
   for line in d.get('runtimeLines',[]):
    p=line.split('\t')
    if len(p)>=3 and p[0]=='INFO':info[p[1]]=p[2]
    elif len(p)>=3 and p[0]=='CONFIG':runtime[p[1]]=p[2]
    elif len(p)>=3 and p[0]=='CLIENTS':client=int(p[2]) if p[2].isdigit() else None
  lines=s.get('lines') or [];files=[x.split('\t')[1] for x in lines if x.startswith('FILE\t') and x.endswith('\tREADABLE')];refs=[x.split('\t')[2] for x in lines if x.startswith('REFERENCE\t') and len(x.split('\t'))>=4 and x.split('\t')[3]=='DIRECT'];procs=[x for x in lines if x.startswith('PROCESS\t')];vals={}
  for line in lines:
   p=line.split('\t')
   if len(p)>=4 and p[0]=='DIRECTIVE':vals[p[2]]=p[3]
  config=files[0] if len(files)==1 else None;lg=log.get('lines') or [];ls=st.get('lines') or []
  event_values=[x.split('\t')[1] for x in lg if x.startswith('EVENTS\t')];file_counts=[x.split('\t')[1] for x in lg if x.startswith('FILE_COUNT\t')];sizes=[x.split('\t')[2] for x in lg if x.startswith('FILE\t') and len(x.split('\t'))>=3]
  recent=False
  if len(ls)==1:
   m=re.search(r'mtime=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)? ([+-]\d{4})',ls[0])
   if m:
    stamp=datetime.strptime(m.group(1)+' '+m.group(2),'%Y-%m-%d %H:%M:%S %z');end=datetime.fromisoformat(stats['completedAt'].replace('Z','+00:00'));age=end-stamp;recent=timedelta(minutes=-5)<=age<=timedelta(hours=24)
  conns=d.get('connections') or [];listeners=d.get('listeners') or [];apps=a.get('applicationCandidates') or {};apps2=d.get('applicationCandidates') or {};ip=a.get('ip');port=int(runtime.get('port','0')) if str(runtime.get('port','')).isdigit() else 0
  good=(coverage and ip not in sent_ips and a.get('status')==d.get('status')==s.get('status')==log.get('status')==st.get('status')=='OBSERVED'
   and a.get('startTicks') and a['startTicks']==d.get('startTicks')==s.get('startTicks')==log.get('startTicks')==st.get('startTicks')
   and all(x.get('observedAt') for x in (a,d,s,log,st)) and a.get('frames',0)>=2 and d.get('frames',0)>=2 and not a.get('warnings') and not d.get('warnings')
   and a.get('container') is None and a.get('containerMatches')==0 and a.get('dockerWarnings')==[] and meta.get('netns') and meta['netns']==meta.get('hostnetns') and meta.get('service')
   and config and config in refs and 'PIDFILE_MATCH\t'+vals.get('pidfile','') in lines and procs==['PROCESS\t'+str(a['pid'])+'\tredis-server']
   and not any(x.startswith(('DIRECTIVE_UNPARSED\t','RENAME_CLUSTER\t')) for x in lines) and not any(k in vals for k in ('include','slaveof','replicaof','cluster-enabled'))
   and valid_runtime and info.get('redis_mode')=='standalone' and info.get('run_id') and info.get('role')=='master' and info.get('connected_slaves')=='0' and info.get('cluster_enabled')=='0' and client==0
   and runtime.get('bind')==vals.get('bind')==ip and runtime.get('port')==vals.get('port')=='6379' and runtime.get('slaveof','')=='' and runtime.get('replicaof','')=='' and runtime.get('dir')==vals.get('dir') and runtime.get('dbfilename')==vals.get('dbfilename') and runtime.get('logfile')==vals.get('logfile')=='/var/log/redis_6379.log'
   and len(listeners)==1 and listeners[0].get('ip')==ip and listeners[0].get('port')==port and listeners[0].get('protocol')=='tcp' and len(conns)==d.get('connectionCount')
   and file_counts==['1'] and event_values==['0'] and len(sizes)==1 and sizes[0].isdigit() and int(sizes[0])>0 and not any(x.startswith(('LIMIT\t','UNREADABLE\t','READ_FAILED\t')) for x in lg) and recent
   and set(apps)==set(apps2)=={'TEST','SIMULATION','PRODUCT'} and not any(apps.values()) and not any(apps2.values()))
  if good:
   accepted.append({'audit':a,'startup':{'detail':{'startup':{'unit':meta['service']}}},'configPath':config,'endpoint':{'ip':ip,'port':port,'protocol':'tcp'},'hostPort':port,'listenerScope':'HOST_ADDRESS','clusterEvidence':'INFO_DISABLED_LOG_ZERO_SENTINEL_NONE','groupingBasis':'CONFIG_RUNTIME_LOG_SENTINEL_TCP','matchingBasis':'REDIS_CONFIG_RUNTIME_LOG_SENTINEL_TCP','reason':'Redis 配置、现存日志、Sentinel 和 TCP 核查'})
  else:unresolved.append({'ip':ip,'reason':'现存日志、启动配置、运行状态或关联证据不足'})
 return {'accepted':accepted,'unresolved':unresolved}


def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text());names=('config-audit.json','deep-12.json','startup-combined.json','full-log-three-dated.json','log-file-stat.json','sentinels-all.json');reports=[read(root/n) for n in names];version=read(data/'current.json')['id']
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
 result=base.import_report(db.Catalog.from_environment(),*reports[:4],version,can_apply=lambda:read(data/'current.json').get('id')==version,prepared=p,extra_reports=reports[4:],mapper_version='redis-log-single-v1',source_path='checks/redis-review/full-log-three-dated.json')
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
