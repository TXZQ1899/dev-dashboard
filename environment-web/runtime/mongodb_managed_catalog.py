"""Import user-confirmed DMS MongoDB cloud IDs as managed service units."""
import json,os
from collections import Counter
from pathlib import Path
try: import deployment_catalog as db
except ModuleNotFoundError: from runtime import deployment_catalog as db
RULE='mongodb-dms-user-group-v1'
FIELDS=('cloudInstanceId','tenantId','dmsTenantId','resourceKey','sourceKeys','实例地址','host','port','observedAt')


def plan(snapshot,confirmation,version):
 if confirmation.get('sourceVersion')!=version or snapshot.get('available') is not True or snapshot.get('complete') is not True:raise ValueError('来源版本或 DMS 采集不完整')
 selected=(confirmation.get('managed') or {}); ids=selected.get('cloudInstanceIds') or []
 if selected.get('answer')!='是，按云实例分别建档' or len(ids)!=2 or len(set(ids))!=2:raise ValueError('托管实例的用户确认无效')
 rows=[r for r in snapshot.get('instances',[]) if r.get('dbType')=='mongo' and r.get('cloudInstanceId') in ids]
 records=[{k:r.get(k) for k in FIELDS} for r in rows]
 if records!=selected.get('records') or db.digest(records)!=selected.get('recordsDigest') or {r['cloudInstanceId'] for r in records}!=set(ids):raise ValueError('DMS 记录与用户确认不一致')
 result=[]
 for cloud_id in sorted(ids):
  group=[r for r in records if r['cloudInstanceId']==cloud_id];scopes={(r['tenantId'],r['dmsTenantId']) for r in group}
  if len(scopes)!=1:raise ValueError('同一云实例出现多个 DMS 采集范围，需复核')
  tenant,tid=next(iter(scopes));slices=[x for x in snapshot.get('tenants',[]) if x.get('tenantId')==tenant and x.get('dmsTenantId')==tid]
  if len(slices)!=1 or slices[0].get('complete') is not True:raise ValueError('DMS 采集切片不完整')
  addresses=[r['实例地址'] for r in group]
  if len(addresses)!=len(set(addresses)) or any(not r.get('resourceKey') or not r.get('sourceKeys') or not r.get('host') or not isinstance(r.get('port'),int) or not 0<r['port']<65536 or r['实例地址']!=str(r['host'])+':'+str(r['port']) or not r.get('observedAt') for r in group):raise ValueError('DMS 端点或来源键缺失')
  if len({r['observedAt'] for r in group})!=1:raise ValueError('同组 DMS 观察时间不一致')
  result.append({'cloudInstanceId':cloud_id,'collectorTenantId':tenant,'dmsTenantId':tid,'observedAt':group[0]['observedAt'],'records':group,'endpoints':[{'host':r['host'],'port':r['port'],'address':r['实例地址']} for r in group]})
 return result


def import_report(store,snapshot,confirmation,version,data,can_apply=lambda:True):
 groups=plan(snapshot,confirmation,version);report={'ruleVersion':RULE,'sourceVersion':version,'confirmation':confirmation,'records':[r for g in groups for r in g['records']]};payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/mongodb-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
 if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
 if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
 store.migrate();source=store.source_key+':mongodb';results=[]
 with store.connection() as conn,store.database_lock(conn,'component-mongodb'):
  conn.begin()
  try:
   now,run=db.utcnow(),db.identifier(); max_stamp=db.timestamp(max(g['observedAt'] for g in groups))
   already=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE))
   if already:
    conn.rollback();return {'unchanged':True}
   if not can_apply():raise db.CatalogError('DMS 来源或用户确认已变化')
   store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/mongodb-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'MongoDB','cloudInstanceIds':[g['cloudInstanceId'] for g in groups]}),'completeness_json':db.encode({'complete':False}),'observed_at':max_stamp,'imported_at':now,'status':'WRITING'})
   for g in groups:
    key={'cloudInstanceId':g['cloudInstanceId'],'collectorTenantId':g['collectorTenantId'],'dmsTenantId':g['dmsTenantId']};stamp=db.timestamp(g['observedAt']);found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MONGODB_MANAGED' AND s.native_key_hash=%s",(source,db.digest(key)))
    if found:results.append({'name':found[0]['name'],'cloudInstanceId':g['cloudInstanceId'],'existing':True});continue
    store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('MongoDB',1) ON DUPLICATE KEY UPDATE technology=technology");n=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='MongoDB' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='MongoDB'");name='MongoDB_cluster_'+str(n)
    if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
    unit,env,instance=db.identifier(),db.identifier(),db.identifier();ev={'sourceVersion':version,'reportPath':'checks/mongodb-review/evidence/'+digest+'.json','groupingBasis':'USER_CONFIRMED_CLOUD_INSTANCE_ID','confidence':'INFERRED','coverage':'DMS 可见记录；采集账号不证明资源所有权，地址不代表物理节点，未验证健康或可达性'}
    store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'MongoDB','creation_origin':'DMS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
    store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
    def obs(kind,owner,loc,attrs):
     oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'SOURCE_OBSERVED','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'DMS_CLOUD_ID_USER_GROUP','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
    uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'MongoDB','mode':'MANAGED','cloudInstanceId':g['cloudInstanceId']});eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
    store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'MONGODB_MANAGED','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
    ports={str(x['port']) for x in g['endpoints']};common_port=next(iter(ports)) if len(ports)==1 else None
    store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'MANAGED_SERVICE','primary_ip':None,'service_address':g['endpoints'][0]['address'],'declared_port_raw':common_port,'transport_protocol':None,'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
    attrs={'mode':'MANAGED','cloudInstanceId':g['cloudInstanceId'],'collectorTenantId':g['collectorTenantId'],'dmsTenantId':g['dmsTenantId'],'serviceEndpoints':g['endpoints'],'portBasis':'DMS_RECORD','endpointMeaning':'DMS 登记地址，不代表物理节点或已确认可达','sourceRecords':g['records']};iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
    store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MONGODB_MANAGED_SERVICE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'DMS_CLOUD_ID_USER_GROUP','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
    for typ,oid in (('INSTANCE',instance),('UNIT',unit)):store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'cloudInstanceId':g['cloudInstanceId']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户确认按 DMS 云实例 ID 建档'})
    results.append({'name':name,'cloudInstanceId':g['cloudInstanceId'],'endpoints':g['endpoints'],'existing':False})
   if not can_apply():raise db.CatalogError('DMS 来源或用户确认已变化')
   store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results),'createdInstances':sum(not x['existing'] for x in results)})});conn.commit();return {'units':results,'evidence':'checks/mongodb-review/evidence/'+digest+'.json'}
  except Exception:conn.rollback();raise

def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda x:json.loads(x.read_text());version=read(data/'current.json')['id'];paths=[data/'versions'/version/'dms-snapshot.json',data/'checks/mongodb-review'/('user-final-confirmation-'+version+'.json')];raw=[x.read_bytes() for x in paths];snap,confirmation=map(json.loads,raw);groups=plan(snap,confirmation,version)
 if not args.apply:print(json.dumps(groups,ensure_ascii=False));return
 def unchanged():return read(data/'current.json').get('id')==version and all(x.read_bytes()==b for x,b in zip(paths,raw))
 print(json.dumps(import_report(db.Catalog.from_environment(),snap,confirmation,version,data,unchanged),ensure_ascii=False))
if __name__=='__main__':main()
