"""Import four user-confirmed PostgreSQL units with limited source evidence."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

RULE = 'postgresql-user-confirmed-v1'
VERSION = '20261103-113340-937050'
PROCESSES = {
    '10.58.1.54': ('6ca8259c-02ff-4308-ba38-5ea949af67f4', 773, '/var/opt/gitlab/postgresql/data'),
    '10.58.9.221': ('ba00a2b5-7992-4ebe-b43f-37252b40640e', 2407, '/opt/ManageEngine/EventLog/pgsql/data'),
}
CLOUD_IDS = {'pgm-uf62i84j46owgu0m', 'pgm-uf6l68e2ub1cr678'}


def plan(snapshot, dms, gitlab, identity, replication, confirmation, version, now=None):
    if version != VERSION or any(r.get('sourceVersion') != version for r in (gitlab, identity, replication, confirmation)):
        raise ValueError('来源版本不一致')
    if (confirmation.get('processAnswer') != '都需要，各自单独建档' or confirmation.get('managedAnswer') != '是，分别建档'
            or set(confirmation.get('processIPs') or []) != set(PROCESSES)
            or set(confirmation.get('cloudInstanceIds') or []) != CLOUD_IDS or not confirmation.get('confirmedAt')):
        raise ValueError('用户确认不完整')
    now = now or datetime.now(timezone.utc)
    items = []
    for ip, (asset_id, pid, data_dir) in PROCESSES.items():
        assets = [a for a in snapshot.get('assets', []) if a.get('id') == asset_id and a.get('ip') == ip]
        if len(assets) != 1: raise ValueError('资产不唯一')
        inspection = assets[0].get('inspection') or {}
        ps = [p for p in inspection.get('processes', []) if p.get('pid') == pid and p.get('name') == 'postgres']
        if len(ps) != 1 or data_dir not in ps[0].get('command', ''): raise ValueError('快照进程不匹配')
        live = gitlab if ip == '10.58.1.54' else identity
        rows = [r for r in live.get('results', []) if (r.get('assetId'), r.get('ip'), r.get('pid')) == (asset_id, ip, pid)]
        if len(rows) != 1 or rows[0].get('status') != 'OBSERVED': raise ValueError('实时进程身份未观察到')
        row = rows[0]; output = row.get('output', '')
        import re
        stamps = re.findall(r'(?m)^'+str(pid)+r' \(postgres\) (\d+)$', output)
        if len(stamps) != 2 or stamps[0] != stamps[1] or data_dir not in output: raise ValueError('实时进程身份不一致')
        age = now - datetime.fromisoformat(row['checkedAt'].replace('Z', '+00:00'))
        if not timedelta(minutes=-5) <= age <= timedelta(hours=2): raise ValueError('实时进程证据过期')
        endpoints = []
        if ip == '10.58.9.221':
            if '127.0.0.1:33335' not in output or '::1:33335' not in output or output.count('pid=2407,') != 2: raise ValueError('监听证据不完整')
            endpoints = [{'ip': address, 'port': 33335, 'protocol': 'tcp'} for address in ('127.0.0.1', '::1')]
        items.append({'kind': 'PROCESS', 'ip': ip, 'assetId': asset_id, 'pid': pid, 'dataDir': data_dir,
                      'observedAt': row['checkedAt'], 'endpoints': endpoints, 'startTicks': stamps[0],
                      'key': {'assetId': asset_id, 'dataDir': data_dir}})
    if dms.get('available') is not True or dms.get('complete') is not True: raise ValueError('DMS 快照不完整')
    records = [r for r in dms.get('instances', []) if r.get('dbType') == 'postgresql' and r.get('cloudInstanceId') in CLOUD_IDS]
    if len(records) != 2 or {r.get('cloudInstanceId') for r in records} != CLOUD_IDS: raise ValueError('DMS 实例身份不唯一')
    for r in sorted(records, key=lambda x: x['cloudInstanceId']):
        scopes = [t for t in dms.get('tenants', []) if t.get('tenantId') == r.get('tenantId') and t.get('dmsTenantId') == r.get('dmsTenantId')]
        if len(scopes) != 1 or scopes[0].get('complete') is not True or not r.get('resourceKey') or not r.get('sourceKeys') or not r.get('observedAt') or not r.get('host') or not isinstance(r.get('port'), int) or not 0 < r['port'] < 65536 or r.get('实例地址') != r['host']+':'+str(r['port']): raise ValueError('DMS 证据不完整')
        fields = ('cloudInstanceId', 'tenantId', 'dmsTenantId', 'resourceKey', 'sourceKeys', '实例地址', 'host', 'port', 'observedAt')
        items.append({'kind':'MANAGED', 'observedAt':r['observedAt'], 'record':{k:r.get(k) for k in fields},
                      'key':{'cloudInstanceId':r['cloudInstanceId'], 'collectorTenantId':r['tenantId'], 'dmsTenantId':r['dmsTenantId']}})
    return items


def apply(store, items, report, data, can_apply):
    payload, digest = db.encode(report), db.digest(report)
    root = Path(data)/'checks/postgresql-review/evidence'; root.mkdir(parents=True, exist_ok=True)
    path = root/(digest+'.json')
    if path.exists() and path.read_text() != payload: raise ValueError('证据哈希冲突')
    if not path.exists():
        temp = path.with_suffix('.tmp'); temp.write_text(payload); temp.chmod(0o600); os.replace(temp, path)
    store.migrate(); source = store.source_key+':postgresql'; results = []
    with store.connection() as conn, store.database_lock(conn, 'component-postgresql'):
        conn.begin()
        try:
            if not can_apply(): raise db.CatalogError('来源已经变化')
            previous = store.execute(conn, 'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, digest, RULE))
            if previous:
                for item in items:
                    found = store.execute(conn, 'SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind=%s AND s.native_key_hash=%s', (source, 'POSTGRESQL_'+item['kind'], db.digest(item['key'])))
                    if len(found) != 1: raise db.CatalogError('历史导入与当前单元不一致')
                    results.append({'name':found[0]['name'],'key':item['key'],'existing':True})
                conn.rollback(); return {'units':results,'evidence':str(path),'unchanged':True}
            now, run = db.utcnow(), db.identifier()
            store.insert(conn, 'catalog_import_run', {'id':run,'source_instance_key':source,'source_version':VERSION,'source_relative_path':'checks/postgresql-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'PostgreSQL'}),'completeness_json':db.encode({'complete':False}),'observed_at':db.timestamp(max(x['observedAt'] for x in items)),'imported_at':now,'status':'WRITING'})
            for item in items:
                key, kind = item['key'], item['kind']; object_kind = 'POSTGRESQL_'+kind
                found = store.execute(conn, 'SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind=%s AND s.native_key_hash=%s', (source, object_kind, db.digest(key)))
                if found:
                    if len(found)!=1: raise db.CatalogError('现有单元身份不唯一')
                    results.append({'name':found[0]['name'],'key':key,'existing':True}); continue
                if kind == 'PROCESS' and store.execute(conn, "SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s", (source,item['assetId'])): raise db.CatalogError('同资产 PostgreSQL 来源已存在，需复核')
                store.execute(conn, "INSERT INTO catalog_component_counter (technology,next_number) VALUES ('PostgreSQL',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='PostgreSQL' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='PostgreSQL'")
                name='PostgreSQL_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)): raise db.CatalogError('单元名称冲突')
                unit, env, instance = db.identifier(), db.identifier(), db.identifier(); stamp=db.timestamp(item['observedAt'])
                evidence={'sourceVersion':VERSION,'reportPath':'checks/postgresql-review/evidence/'+digest+'.json','groupingBasis':'USER_CONFIRMED_SEPARATE','groupingConfidence':'INFERRED','coverage':'用户确认分别建档；配置和复制关系未完全核实，未证明历史或未来状态'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'PostgreSQL','creation_origin':'PROCESS_REVIEW' if kind=='PROCESS' else 'DMS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(record_kind, owner, attrs):
                    locator={**key,'record':record_kind}; oid=db.identifier()
                    store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':record_kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK' if kind=='PROCESS' else 'SOURCE_OBSERVED','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_SEPARATE','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attrs)})
                    return oid
                unit_obs=obs('COMPONENT',{'unit_id':unit},{'technology':'PostgreSQL','groupingConfidence':'INFERRED'})
                env_obs=obs('ENVIRONMENT',{'unit_environment_id':env},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':env_obs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':object_kind,'native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':unit_obs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                if kind=='PROCESS':
                    attrs={'dataDir':item['dataDir'],'pid':item['pid'],'startTicks':item['startTicks'],'observedEndpoints':item['endpoints'],'replicationState':'UNKNOWN','portBasis':'LIVE_LISTENER' if item['endpoints'] else 'UNKNOWN'}
                    inst={'instance_kind':'SERVICE_PROCESS','primary_ip':item['ip'],'declared_port_raw':None,'transport_protocol':None,'host_reference_json':db.encode({'assetId':item['assetId']})}
                else:
                    r=item['record']; endpoint={'host':r['host'],'port':r['port'],'address':r['实例地址']}
                    attrs={'mode':'MANAGED','cloudInstanceId':r['cloudInstanceId'],'serviceEndpoints':[endpoint],'sourceRecords':[r],'portBasis':'DMS_RECORD','endpointMeaning':'DMS 登记地址，不代表物理节点或可达性'}
                    inst={'instance_kind':'MANAGED_SERVICE','primary_ip':None,'service_address':r['实例地址'],'declared_port_raw':str(r['port']),'transport_protocol':None}
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,**inst,'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                iobs=obs('DEPLOYMENT',{'instance_id':instance},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':object_kind+'_INSTANCE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_SEPARATE','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(evidence)})
                for typ,oid in (('UNIT',unit),('INSTANCE',instance)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'key':key}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户确认 PostgreSQL 分别建档'})
                results.append({'name':name,'key':key,'existing':False})
            if not can_apply(): raise db.CatalogError('来源已经变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results)})});conn.commit()
            return {'units':results,'evidence':str(path)}
        except Exception:
            conn.rollback(); raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data')); version=json.loads((data/'current.json').read_text())['id']; folder=data/'versions'/version; checks=data/'checks/postgresql-review'
    paths=[folder/'jumpserver-snapshot.json',folder/'dms-snapshot.json',checks/('live-gitlab-'+version+'.json'),checks/('live-identity-'+version+'.json'),checks/('live-replication-'+version+'.json'),checks/('user-confirmation-'+version+'.json')]
    raw=[p.read_bytes() for p in paths]; snapshot,dms,gitlab,identity,replication,confirmation=map(json.loads,raw)
    items=plan(snapshot,dms,gitlab,identity,replication,confirmation,version)
    if not args.apply: print(json.dumps(items,ensure_ascii=False,indent=2));return
    report={'ruleVersion':RULE,'sourceVersion':version,'items':items,'gitlab':gitlab,'identity':identity,'replication':replication,'confirmation':confirmation}
    valid=lambda:json.loads((data/'current.json').read_text()).get('id')==version and all(p.read_bytes()==b for p,b in zip(paths,raw))
    print(json.dumps(apply(db.Catalog.from_environment(),items,report,data,valid),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
