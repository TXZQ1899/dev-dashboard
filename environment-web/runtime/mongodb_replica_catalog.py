"""Import one directly corroborated two-member MongoDB replica set."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

RULE = 'mongodb-docker-replica-v1'
ASSET = '50e90ae2-9fe8-4a84-9ea7-63f683c836ce'
IP = '10.179.2.10'
MEMBERS = {29243: ('mongoserver',27017), 30000: ('mongoreplserver',27018)}


def plan(snapshot, initial, detail, confirmation, version, now=None):
    reports=(snapshot,initial,detail,confirmation)
    if any(r.get('sourceVersion') != version for r in reports): raise ValueError('来源版本不一致')
    if snapshot.get('devopsComplete') is not True or snapshot.get('devopsScope') != 'all' or confirmation.get('answer') != '不负责': raise ValueError('应用来源或用户确认无效')
    now=now or datetime.now(timezone.utc)
    for value in [detail.get('checkedAt')]+[r.get('checkedAt') for r in initial.get('results',[]) if r.get('ip')==IP and r.get('pid') in MEMBERS]:
        if not value or not timedelta(minutes=-5)<=now-datetime.fromisoformat(value.replace('Z','+00:00'))<=timedelta(hours=2): raise ValueError('实时证据过期')
    if detail.get('assetId')!=ASSET or detail.get('ip')!=IP: raise ValueError('资产不匹配')
    if len([r for r in detail.get('results',[]) if r.get('pid') in MEMBERS])!=2: raise ValueError('成员核查数量错误')
    expected_config={'_id':'rs0','members':[{'_id':0,'host':IP+':27017','hidden':False,'arbiterOnly':False,'votes':1},{'_id':1,'host':IP+':27018','hidden':False,'arbiterOnly':False,'votes':1}]}
    result=[]
    for pid,(name,hostport) in MEMBERS.items():
        def one(rows, pred):
            found=[r for r in rows if pred(r)]
            if len(found)!=1: raise ValueError('候选、确认或核查记录不唯一')
            return found[0]
        row=one(snapshot.get('candidates',[]),lambda x:x.get('assetId')==ASSET and x.get('ip')==IP and x.get('pid')==pid)
        confirm=one(confirmation.get('results',[]),lambda x:x.get('assetId')==ASSET and x.get('ip')==IP and x.get('pid')==pid)
        if confirm.get('hostDeploymentsDigest')!=db.digest(row.get('hostDeployments') or []) or confirm.get('hostDeploymentsCount')!=len(row.get('hostDeployments') or []): raise ValueError('用户确认的应用范围已变化')
        if row.get('name')!='mongod' or row.get('processStatus')!='complete' or set(row.get('applicationCandidates',{}))!={'TEST','SIMULATION','PRODUCT'} or any(row['applicationCandidates'].values()): raise ValueError('进程或应用关联待核实')
        containers=row.get('containerMatches') or []
        if len(containers)!=1: raise ValueError('容器不唯一')
        container=containers[0]
        if container.get('name')!=name or container.get('initPid')!=pid or container.get('ports')!=[{'containerPort':'27017/tcp','hostIp':'0.0.0.0','hostPort':str(hostport)}]: raise ValueError('快照容器映射不符')
        first=one(initial.get('results',[]),lambda x:x.get('assetId')==ASSET and x.get('pid')==pid)
        d=one(detail.get('results',[]),lambda x:x.get('pid')==pid)
        if first.get('status')!='OBSERVED' or d.get('exitCode')!=0 or d.get('containerId')!=container.get('id'): raise ValueError('实时容器核查失败')
        lines=d.get('output','').splitlines()
        def unique(pattern, source=lines):
            vals=[m for line in source if (m:=re.fullmatch(pattern,line))]
            if len(vals)!=1: raise ValueError('核查字段不唯一')
            return vals[0]
        bi=lines.index('BEFORE');ai=lines.index('AFTER');pattern=re.compile(str(pid)+r' \(mongod\) (\d+)');b=pattern.fullmatch(lines[bi+1]);a=pattern.fullmatch(lines[ai+1])
        if not b or not a or b[1]!=a[1]: raise ValueError('进程重启')
        ps=datetime.strptime(unique(r'PS_START\s+(.+)')[1],'%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
        if abs((ps-datetime.fromisoformat(row['startedAt'].replace('Z','+00:00'))).total_seconds())>30: raise ValueError('快照进程身份不符')
        mapping=json.loads(unique(r'DOCKER_JSON '+str(pid)+r' true (\{.*\})')[1])
        if mapping!={'27017/tcp':[{'HostIp':'0.0.0.0','HostPort':str(hostport)}]}: raise ValueError('实时发布端口变化')
        if not any(re.search(r'\*:27017\s+.*pid='+str(pid)+',',line) for line in lines): raise ValueError('容器内直接监听不明')
        if json.loads(unique(r'CONFIG_JSON (\{.*\})')[1])!=expected_config: raise ValueError('完整副本集配置不一致')
        ilines=first.get('output','').splitlines()
        if ilines.count(str(pid)+' (mongod) '+b[1])!=2 or 'ARGV_MATCH' not in ilines or 'DOCKER '+str(pid)+' true /'+name not in ilines: raise ValueError('启动及容器身份不一致')
        hello=json.loads(unique(r'HELLO_JSON (\{.*\})',ilines)[1])
        if hello!={'ok':1,'setName':'rs0','hosts':[IP+':27017',IP+':27018'],'primary':IP+':27017','me':IP+':'+str(hostport)}: raise ValueError('服务成员视图不一致')
        result.append({'pid':pid,'startTicks':b[1],'containerId':container['id'],'containerName':name,'hostPort':hostport})
    return {'members':result,'checkedAt':detail['checkedAt'],'configuredMembers':expected_config['members']}


def import_report(store, snapshot, initial, detail, confirmation, version, data, can_apply=lambda:True):
    prepared=plan(snapshot,initial,detail,confirmation,version)
    report={'ruleVersion':RULE,'sourceVersion':version,'snapshot':snapshot,'initial':initial,'detail':detail,'confirmation':confirmation}
    payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/mongodb-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload: raise ValueError('证据哈希冲突')
    if not path.exists():
        tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();source=store.source_key+':mongodb';key={'assetId':ASSET,'replicaSetName':'rs0','members':[IP+':27017',IP+':27018']};stamp=db.timestamp(prepared['checkedAt'])
    ev={'sourceVersion':version,'reportPath':'checks/mongodb-review/evidence/'+digest+'.json','groupingBasis':'CROSS_CHECKED_REPLICA_CONFIG_AND_CONTAINER_PORTS','groupingConfidence':'INFERRED','coverage':'两个容器当前成员配置交叉确认；不排除历史或后续变动'}
    with store.connection() as conn,store.database_lock(conn,'component-mongodb'):
        conn.begin()
        try:
            found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MONGODB_REPLICA_SET' AND s.native_key_hash=%s",(source,db.digest(key)))
            if found:conn.rollback();return {'name':found[0]['name'],'existing':True}
            if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,ASSET)): raise db.CatalogError('同资产 MongoDB 绑定待复核')
            if not can_apply():raise db.CatalogError('核查来源已变化')
            now,run,unit,env=db.utcnow(),db.identifier(),db.identifier(),db.identifier()
            store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('MongoDB',1) ON DUPLICATE KEY UPDATE technology=technology")
            num=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='MongoDB' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='MongoDB'");name='MongoDB_cluster_'+str(num)
            if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':ev['reportPath'],'snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'MongoDB','assetId':ASSET}),'completeness_json':db.encode({'complete':False}),'observed_at':stamp,'imported_at':now,'status':'WRITING'})
            store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'MongoDB','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
            store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
            def obs(kind,owner,locator,attrs):
                oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'MONGODB_REPLICA_CONFIG_CONTAINER','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
            uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'MongoDB','mode':'REPLICA_SET','replicaSetName':'rs0','configuredMembers':prepared['configuredMembers']})
            eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
            store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'MONGODB_REPLICA_SET','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
            for member in prepared['members']:
                instance=db.identifier();ikey={'assetId':ASSET,'containerName':member['containerName'],'publishedPort':member['hostPort'],'replicaSetName':'rs0'}
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':IP,'declared_port_raw':str(member['hostPort']),'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':ASSET}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                attrs={'mode':'REPLICA_SET','replicaSetName':'rs0','containerId':member['containerId'],'containerName':member['containerName'],'pid':member['pid'],'startTicks':member['startTicks'],'observedEndpoints':[{'ip':'*','port':27017,'protocol':'tcp','scope':'CONTAINER'},{'ip':'0.0.0.0','port':member['hostPort'],'protocol':'tcp','scope':'HOST_PUBLISHED'}]}
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**ikey,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MONGODB_PROCESS','native_key_json':db.encode(ikey),'native_key_hash':db.digest(ikey),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONTAINER_PORT','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'INSTANCE','object_id':instance,'action':'CREATE','after_json':db.encode({'name':name,'ip':IP,'port':member['hostPort']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'MongoDB 副本集配置和容器映射只读核查'})
            store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'UNIT','object_id':unit,'action':'CREATE','after_json':db.encode({'name':name,'ip':IP}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'MongoDB 副本集配置和容器映射只读核查'})
            if not can_apply():raise db.CatalogError('核查来源已变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':1,'createdInstances':2})});conn.commit()
            return {'name':name,'ip':IP,'ports':[m['hostPort'] for m in prepared['members']],'evidence':ev['reportPath'],'existing':False}
        except Exception:conn.rollback();raise


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda p:json.loads(p.read_text());version=read(data/'current.json')['id'];root=data/'checks/mongodb-review';paths=[root/(x+version+'.json') for x in ('snapshot-','config-remaining-','replica-two-','user-app-confirmation-')];raw=[x.read_bytes() for x in paths];reports=[json.loads(x) for x in raw];prepared=plan(*reports,version)
    if not args.apply:print(json.dumps({'ip':IP,'members':prepared['members'],'mode':'REPLICA_SET'},ensure_ascii=False));return
    def unchanged():return read(data/'current.json').get('id')==version and all(x.read_bytes()==v for x,v in zip(paths,raw))
    print(json.dumps(import_report(db.Catalog.from_environment(),*reports,version,data,unchanged),ensure_ascii=False))

if __name__=='__main__':main()
