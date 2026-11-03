"""Conservative import of directly observed standalone MongoDB containers."""
import json,os,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
try: import deployment_catalog as db
except ModuleNotFoundError: from runtime import deployment_catalog as db
RULE='mongodb-container-standalone-v1'
PROFILES={
 '10.179.2.141':{'asset':'98c15a0a-4737-4b4b-b1c5-8c5db924db4f','pid':9270,'name':'mongoserver','argv':['mongod','--bind_ip_all'],'network':'default','hostIp':'0.0.0.0'},
 '10.179.2.14':{'asset':'6e9b4297-0914-4a21-978a-f071de6960aa','pid':12553,'name':'mongoserver','argv':['mongod','--auth','--bind_ip_all'],'network':'host','hostIp':None},
 '10.179.4.15':{'asset':'ccacdfb2-647b-484f-ab85-402e35f42fc1','pid':14000,'name':'mongo','argv':['mongod','--bind_ip_all'],'network':'back-net','hostIp':'127.0.0.1'},
}

def plan(snapshot,detail,confirmation,version,ip,now=None):
 if ip not in PROFILES:raise ValueError('不支持的候选')
 p=PROFILES[ip];pid=p['pid']; asset=p['asset']; now=now or datetime.now(timezone.utc)
 if snapshot.get('sourceVersion')!=version or detail.get('sourceVersion')!=version or snapshot.get('devopsComplete') is not True or snapshot.get('devopsScope')!='all':raise ValueError('来源版本或应用快照不完整')
 def one(rows):
  found=[r for r in rows if (r.get('assetId'),r.get('ip'),r.get('pid'))==(asset,ip,pid)]
  if len(found)!=1:raise ValueError('资产和进程记录不唯一')
  return found[0]
 row=one(snapshot.get('candidates',[]));d=one(detail.get('results',[]));stamp=datetime.fromisoformat(d['checkedAt'].replace('Z','+00:00'))
 if not timedelta(minutes=-5)<=now-stamp<=timedelta(hours=2):raise ValueError('实时证据过期')
 deployments=row.get('hostDeployments') or []
 if deployments:
  if not confirmation or confirmation.get('sourceVersion')!=version or confirmation.get('answer')!='不负责':raise ValueError('应用关联未确认')
  c=one(confirmation.get('results',[]))
  if c.get('hostDeploymentsDigest')!=db.digest(deployments) or c.get('hostDeploymentsCount')!=len(deployments):raise ValueError('确认应用范围变化')
 if row.get('processStatus')!='complete' or row.get('name')!='mongod' or set(row.get('applicationCandidates',{}))!={'TEST','SIMULATION','PRODUCT'} or any(row['applicationCandidates'].values()):raise ValueError('进程或应用关联未确认')
 containers=row.get('containerMatches') or []
 if len(containers)!=1:raise ValueError('容器不唯一')
 c=containers[0]; snapshot_ports=[] if p['hostIp'] is None else [{'containerPort':'27017/tcp','hostIp':p['hostIp'],'hostPort':'27017'}]
 if c.get('name')!=p['name'] or c.get('initPid')!=pid or c.get('ports')!=snapshot_ports or d.get('containerId')!=c.get('id'):raise ValueError('容器身份或端口不一致')
 if d.get('status')!='OBSERVED' or d.get('exitCode')!=0 or d.get('expectedArgv')!=p['argv']:raise ValueError('启动参数或实时核查不符')
 lines=d.get('output','').splitlines()
 def unique(pattern):
  vals=[m for line in lines if (m:=re.fullmatch(pattern,line))]
  if len(vals)!=1:raise ValueError('核查字段缺失或重复')
  return vals[0]
 bi=lines.index('BEFORE');ai=lines.index('AFTER');ident=re.compile(str(pid)+r' \(mongod\) (\d+)');b=ident.fullmatch(lines[bi+1]);a=ident.fullmatch(lines[ai+1])
 if not b or not a or b[1]!=a[1] or 'ARGV_MATCH' not in lines:raise ValueError('进程身份改变')
 started=datetime.fromisoformat(row['startedAt'].replace('Z','+00:00'));ps=datetime.strptime(unique(r'PS_START\s+(.+)')[1],'%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
 if abs((ps-started).total_seconds())>30:raise ValueError('快照进程时间不一致')
 net,ports=unique(r'DOCKER_JSON '+str(pid)+r' true ("[^"]+") (\{.*\})').groups();mapping=json.loads(ports)
 expected={} if p['hostIp'] is None else {'27017/tcp':[{'HostIp':p['hostIp'],'HostPort':'27017'}]}
 if json.loads(net)!=p['network'] or mapping!=expected:raise ValueError('实时容器网络或发布端口不匹配')
 if not any(re.search(r'\*:27017\s+.*pid='+str(pid)+',',line) for line in lines):raise ValueError('进程网络命名空间内未确认监听')
 if json.loads(unique(r'HELLO_JSON (\{.*\})')[1])!={'ok':1}:raise ValueError('当前服务角色未确认独立')
 endpoints=[{'ip':'*','port':27017,'protocol':'tcp','scope':'HOST' if p['hostIp'] is None else 'CONTAINER'}]
 if p['hostIp'] is not None:endpoints.append({'ip':p['hostIp'],'port':27017,'protocol':'tcp','scope':'HOST_PUBLISHED'})
 return {'assetId':asset,'ip':ip,'pid':pid,'containerId':c['id'],'containerName':p['name'],'port':27017,'network':p['network'],'endpoints':endpoints,'startTicks':b[1],'checkedAt':d['checkedAt']}

def import_report(store,snapshot,detail,confirmation,version,ip,data,can_apply=lambda:True):
 item=plan(snapshot,detail,confirmation,version,ip);report={'ruleVersion':RULE,'sourceVersion':version,'target':ip,'snapshot':snapshot,'detail':detail,'confirmation':confirmation};payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/mongodb-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
 if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
 if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
 store.migrate();source=store.source_key+':mongodb';key={'assetId':item['assetId'],'containerName':item['containerName'],'port':item['port']};stamp=db.timestamp(item['checkedAt']);ev={'sourceVersion':version,'reportPath':'checks/mongodb-review/evidence/'+digest+'.json','groupingBasis':'CONTAINER_ARGV_AND_ISMASTER','groupingConfidence':'INFERRED','coverage':'当前容器启动参数、网络监听、端口映射与服务响应；历史关系未知'}
 with store.connection() as conn,store.database_lock(conn,'component-mongodb'):
  conn.begin()
  try:
   found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MONGODB_CONTAINER_STANDALONE' AND s.native_key_hash=%s",(source,db.digest(key)))
   if found:conn.rollback();return {'name':found[0]['name'],'existing':True}
   old=store.execute(conn,"SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,item['assetId']))
   for record in old:
    k=db.decode(record['native_key_json'],{})
    if k.get('containerName')==item['containerName'] or str(k.get('port') or k.get('publishedPort') or '')==str(item['port']):raise db.CatalogError('同资产容器或端口已有绑定')
   if not can_apply():raise db.CatalogError('核查来源变化')
   now,run,unit,env,instance=db.utcnow(),db.identifier(),db.identifier(),db.identifier(),db.identifier();store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('MongoDB',1) ON DUPLICATE KEY UPDATE technology=technology");n=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='MongoDB' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='MongoDB'");name='MongoDB_cluster_'+str(n)
   if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
   store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':ev['reportPath'],'snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'MongoDB','assetId':item['assetId']}),'completeness_json':db.encode({'complete':False}),'observed_at':stamp,'imported_at':now,'status':'WRITING'})
   store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'MongoDB','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
   store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
   def obs(kind,owner,loc,attrs):
    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'MONGODB_CONTAINER_RUNTIME','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
   uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'MongoDB','mode':'STANDALONE'});eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
   store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'MONGODB_CONTAINER_STANDALONE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
   store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':ip,'declared_port_raw':'27017','transport_protocol':'tcp','host_reference_json':db.encode({'assetId':item['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
   attrs={'mode':'STANDALONE','containerId':item['containerId'],'containerName':item['containerName'],'network':item['network'],'pid':item['pid'],'startTicks':item['startTicks'],'observedEndpoints':item['endpoints']};iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
   store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MONGODB_CONTAINER_PROCESS','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONTAINER_PORT','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
   for typ,oid in (('INSTANCE',instance),('UNIT',unit)):store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'ip':ip,'port':27017}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'MongoDB 容器启动、服务及监听只读核查'})
   if not can_apply():raise db.CatalogError('核查来源变化')
   store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':1,'createdInstances':1})});conn.commit();return {'name':name,'ip':ip,'port':27017,'existing':False,'evidence':ev['reportPath']}
  except Exception:conn.rollback();raise

def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--target',required=True,choices=PROFILES);p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda p:json.loads(p.read_text());version=read(data/'current.json')['id'];root=data/'checks/mongodb-review';paths=[root/(n+version+'.json') for n in ('snapshot-','container-standalone-','user-app-confirmation-')];raw=[x.read_bytes() for x in paths];reports=[json.loads(x) for x in raw];item=plan(*reports,version,args.target)
 if not args.apply:print(json.dumps(item,ensure_ascii=False));return
 def unchanged():return read(data/'current.json').get('id')==version and all(x.read_bytes()==v for x,v in zip(paths,raw))
 print(json.dumps(import_report(db.Catalog.from_environment(),*reports,version,args.target,data,unchanged),ensure_ascii=False))
if __name__=='__main__':main()
