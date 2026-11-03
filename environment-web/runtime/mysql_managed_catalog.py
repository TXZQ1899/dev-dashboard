"""Import user-confirmed DMS MySQL service identities without inferring topology."""
import argparse
import json
import os
from pathlib import Path
try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

VERSION='20261103-113340-937050'
RULE='mysql-dms-user-cloud-id-v1'
FIELDS=('cloudInstanceId','tenantId','dmsTenantId','resourceKey','sourceKeys','实例地址','host','port','observedAt','实例来源')


def source_records(snapshot):
    return [{k:r.get(k) for k in FIELDS} for r in snapshot.get('instances',[]) if r.get('dbType')=='mysql' and r.get('cloudInstanceId')]


def plan(snapshot,confirmation,version):
    if version!=VERSION or confirmation.get('sourceVersion')!=version or snapshot.get('available') is not True or snapshot.get('complete') is not True:raise ValueError('DMS 来源版本或完整性无效')
    records=source_records(snapshot);ids=confirmation.get('cloudInstanceIds') or []
    if confirmation.get('answer')!='按云实例 ID 分别建档（Recommended）' or not confirmation.get('confirmedAt') or len(ids)!=50 or len(set(ids))!=50 or {r['cloudInstanceId'] for r in records}!=set(ids) or confirmation.get('recordsDigest')!=db.digest(records):raise ValueError('用户确认的云实例范围或来源已变化')
    output=[]
    for cloud in sorted(ids):
        group=[r for r in records if r['cloudInstanceId']==cloud];scopes=set(); addresses=set();source_keys=set()
        for r in group:
            scope=(r['tenantId'],r['dmsTenantId']);slices=[x for x in snapshot.get('tenants',[]) if (x.get('tenantId'),x.get('dmsTenantId'))==scope]
            if not all(scope) or scope in scopes or len(slices)!=1 or slices[0].get('complete') is not True:raise ValueError('采集切片缺失、不完整或重复')
            scopes.add(scope)
            if not r.get('resourceKey') or not isinstance(r.get('sourceKeys'),list) or not r['sourceKeys'] or any(not isinstance(k,str) or not k or k in source_keys for k in r['sourceKeys']):raise ValueError('DMS 来源键缺失或冲突')
            source_keys.update(r['sourceKeys'])
            if not r.get('host') or not isinstance(r.get('port'),int) or isinstance(r['port'],bool) or not 0<r['port']<65536 or r.get('实例地址')!=r['host']+':'+str(r['port']) or not db.timestamp(r.get('observedAt')):raise ValueError('DMS 端点或观察时间不明确')
            addresses.add((r['host'],r['port'],r['实例地址']))
        if len(group)>1 and len(addresses)!=1:raise ValueError('跨租户重复 ID 的地址不一致，需复核')
        endpoints=[{'host':h,'port':p,'address':a} for h,p,a in sorted(addresses)]
        output.append({'cloudInstanceId':cloud,'records':group,'endpoints':endpoints,'observedAt':max((r['observedAt'] for r in group),key=db.timestamp)})
    return output


def apply(store,snapshot,confirmation,version,data,valid=lambda:True):
    groups=plan(snapshot,confirmation,version);report={'ruleVersion':RULE,'sourceVersion':version,'confirmation':confirmation,'records':[r for g in groups for r in g['records']]};payload=db.encode(report);digest=db.digest(report)
    root=Path(data)/'checks/mysql-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
    if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();source=store.source_key+':mysql';results=[]
    with store.connection() as conn,store.database_lock(conn,'component-mysql'):
        conn.begin()
        try:
            if not valid():raise db.CatalogError('DMS 来源或用户确认已变化')
            already=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE))
            if already:
                for g in groups:
                    rows=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MYSQL_MANAGED' AND s.native_key_hash=%s",(source,db.digest({'cloudInstanceId':g['cloudInstanceId']})))
                    if len(rows)!=1:raise db.CatalogError('历史导入身份不一致')
                    results.append({'name':rows[0]['name'],'cloudInstanceId':g['cloudInstanceId'],'existing':True})
                conn.rollback();return {'units':results,'unchanged':True,'evidence':str(path)}
            now,run=db.utcnow(),db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/mysql-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'MySQL','cloudInstanceIds':[g['cloudInstanceId'] for g in groups]}),'completeness_json':db.encode({'complete':False}),'observed_at':max(db.timestamp(g['observedAt']) for g in groups),'imported_at':now,'status':'WRITING'})
            for g in groups:
                key={'cloudInstanceId':g['cloudInstanceId']};stamp=db.timestamp(g['observedAt'])
                found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MYSQL_MANAGED' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    if len(found)!=1:raise db.CatalogError('相同云实例身份不唯一')
                    results.append({'name':found[0]['name'],'cloudInstanceId':g['cloudInstanceId'],'existing':True});continue
                conflict=store.execute(conn,"SELECT id FROM deployment_unit_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.cloudInstanceId'))=%s",(source,g['cloudInstanceId']))
                if conflict:raise db.CatalogError('云实例已存在其他来源身份，需复核')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('MySQL',1) ON DUPLICATE KEY UPDATE technology=technology")
                n=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='MySQL' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='MySQL'");name='MySQL_cluster_'+str(n)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier()
                ev={'sourceVersion':version,'reportPath':'checks/mysql-review/evidence/'+digest+'.json','groupingBasis':'USER_CONFIRMED_CLOUD_INSTANCE_ID','groupingConfidence':'INFERRED','coverage':'DMS 登记；跨实例复制、资源所有权、物理节点、可达性和健康均未知'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'MySQL','creation_origin':'DMS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,attrs):
                    oid=db.identifier();loc={**key,'record':kind};store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'SOURCE_OBSERVED','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_CLOUD_INSTANCE_ID','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                u=obs('COMPONENT',{'unit_id':unit},{'technology':'MySQL','mode':'MANAGED','cloudInstanceId':g['cloudInstanceId'],'groupingConfidence':'INFERRED'});e=obs('ENVIRONMENT',{'unit_environment_id':env},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':e})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'MYSQL_MANAGED','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':u,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                ports={str(x['port']) for x in g['endpoints']};port=next(iter(ports)) if len(ports)==1 else None
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'MANAGED_SERVICE','primary_ip':None,'service_address':g['endpoints'][0]['address'],'declared_port_raw':port,'transport_protocol':None,'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                attrs={'mode':'MANAGED','cloudInstanceId':g['cloudInstanceId'],'serviceEndpoints':g['endpoints'],'portBasis':'DMS_RECORD','endpointMeaning':'DMS 登记地址，不代表物理节点或已确认可达','sourceRecords':g['records'],'replicationState':'UNKNOWN'}
                i=obs('DEPLOYMENT',{'instance_id':instance},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':i})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MYSQL_MANAGED_SERVICE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_CLOUD_INSTANCE_ID','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('UNIT',unit),('INSTANCE',instance)):store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'cloudInstanceId':g['cloudInstanceId']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户确认按 MySQL 云实例 ID 分别建档'})
                results.append({'name':name,'cloudInstanceId':g['cloudInstanceId'],'existing':False})
            if not valid():raise db.CatalogError('DMS 来源或用户确认已变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results),'createdInstances':sum(not x['existing'] for x in results)})});conn.commit();return {'units':results,'evidence':str(path)}
        except Exception:conn.rollback();raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda x:json.loads(x.read_text());version=read(data/'current.json')['id'];paths=[data/'versions'/version/'dms-snapshot.json',data/'checks/mysql-review'/('user-managed-confirmation-'+version+'.json')];raw=[x.read_bytes() for x in paths];snapshot,confirmation=map(json.loads,raw);groups=plan(snapshot,confirmation,version)
    if not args.apply:print(json.dumps({'count':len(groups),'groups':groups},ensure_ascii=False));return
    valid=lambda:read(data/'current.json').get('id')==version and all(p.read_bytes()==b for p,b in zip(paths,raw))
    print(json.dumps(apply(db.Catalog.from_environment(),snapshot,confirmation,version,data,valid),ensure_ascii=False))
if __name__=='__main__':main()
