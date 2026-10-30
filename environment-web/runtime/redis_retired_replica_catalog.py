"""Import a live Redis primary after its historical replica's retirement is confirmed."""
import hashlib,json,os,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
 import deployment_catalog as db
 import redis_loopback_catalog as base
 import redis_current_standalone_catalog as current
except ModuleNotFoundError:
 from runtime import deployment_catalog as db,redis_loopback_catalog as base,redis_current_standalone_catalog as current


def plan(config,live,startup,docker,sentinels,checks,history,confirmation):
 version=config.get('sourceVersion');reports=(live,startup,docker,sentinels,checks,history,confirmation)
 if not version or any(x.get('sourceVersion')!=version for x in reports):raise ValueError('核查报告版本不一致')
 p=current.plan(config,live,startup,docker,sentinels,checks)
 proof=(confirmation.get('confirmedBy')=='USER' and confirmation.get('evidenceBasis')=='USER_CONFIRMATION' and confirmation.get('retiredIp')=='10.58.1.229' and confirmation.get('retiredPort')==6379 and confirmation.get('statement') and confirmation.get('retirementTime')=='UNKNOWN')
 rows=history.get('results',[])
 if len(rows)!=1 or rows[0].get('status')!='OBSERVED':proof=False
 else:
  h=rows[0];lines=h.get('lines') or [];ends=[x.split('\t')[1] for x in lines if x.startswith('EVENT_IP\t') and len(x.split('\t'))>=3]
  proof=proof and h.get('ip')=='10.58.9.219' and h.get('peerEndpoint')=={'ip':'10.58.1.229','port':6379,'protocol':'tcp'} and 'UPSTREAM_PATTERN\t0' in lines and ends==['10.58.1.229:6379'] and h.get('startTicks')
  proof=proof and any(x.startswith('CONFIRMED_IP_EVENTS\t') and x.split('\t')[1].isdigit() and int(x.split('\t')[1])>0 for x in lines)
 accepted=[];unresolved=list(p['unresolved'])
 for item in p['accepted']:
  row=item['audit'];valid=proof and row.get('ip')=='10.58.9.219' and row.get('pid')==rows[0].get('pid') and row.get('startTicks')==rows[0].get('startTicks')
  if not valid:unresolved.append({'ip':row.get('ip'),'reason':'历史副本退役或日志关联未确认'});continue
  item=dict(item);extra=dict(item.get('evidenceExtra') or {});extra.update({'historicalReplica':{'ip':'10.58.1.229','port':6379,'protocol':'tcp'},'retirementBasis':'USER_CONFIRMATION','retirementTime':'UNKNOWN','history':'已知历史副本 IP 经用户确认下线；日志中不含端点的历史事件无法进一步归属'})
  item.update(evidenceExtra=extra,groupingBasis='USER_RETIRED_PEER_CONFIG_RUNTIME_TCP',matchingBasis='REDIS_USER_RETIREMENT_CONFIG_RUNTIME_TCP',clusterEvidence='CURRENT_ZERO_REPLICAS_USER_CONFIRMED_RETIRED',reason='用户确认历史副本退役，结合 Redis 当前配置及 TCP 核查')
  accepted.append(item)
 return {'accepted':accepted,'unresolved':unresolved}


def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text());names=('current-config-eight.json','deep-219-normalized.json','startup-219-current.json','container-config-current.json','sentinels-current.json','check-219-current.json','history-classification-current.json','user-retired-229.json');reports=[read(root/n) for n in names];version=read(data/'current.json')['id']
 for value in reports:
  t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
  if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
 def verify_sources():
  source=root/'current-deep-eight.json';expected=reports[1].get('derivedFromSha256')
  if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('运行时源报告已变化')
  checked=[]
  for c in reports[5].get('results',[]):
   rel=c.get('reportPath') or '';sha=c.get('reportSha256') or ''
   if not re.fullmatch(r'checks/[A-Za-z0-9-]{1,80}/report\.json',rel) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('上下游报告来源无效')
   payload=(data/rel).read_bytes()
   if hashlib.sha256(payload).hexdigest()!=sha:raise ValueError('上下游报告已变化')
   checked.append((sha,payload))
  return checked
 raw=verify_sources();prepared=plan(*reports)
 if version!=reports[0]['sourceVersion']:raise ValueError('当前快照已切换')
 if not args.apply:
  print(json.dumps({'candidates':[{'ip':x['audit']['ip'],'endpoint':x['endpoint'],'historicalReplica':'10.58.1.229:6379','retirement':'USER_CONFIRMED'} for x in prepared['accepted']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
 evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
 for value in reports:
  payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
  if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
  if not path.exists():
   tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
 for sha,payload in raw:
  path=evidence/('check-'+sha+'.json')
  if path.exists() and path.read_bytes()!=payload:raise ValueError('上下游证据校验失败')
  if not path.exists():
   tmp=path.with_suffix('.tmp');tmp.write_bytes(payload);tmp.chmod(0o600);os.replace(tmp,path)
 def can_apply():
  if read(data/'current.json').get('id')!=version:return False
  try:return verify_sources()==raw
  except (OSError,ValueError):return False
 result=base.import_report(db.Catalog.from_environment(),*reports[:4],version,can_apply=can_apply,prepared=prepared,extra_reports=reports[4:],mapper_version='redis-retired-replica-v1',source_path='checks/redis-review/user-retired-229.json')
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
