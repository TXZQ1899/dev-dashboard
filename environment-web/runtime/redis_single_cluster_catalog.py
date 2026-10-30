"""Import single-member Redis Cluster nodes verified by held node files and TCP."""
import json, os, re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def plan(report, hosts, files):
    v=report.get('sourceVersion')
    if not v or any(x.get('sourceVersion')!=v for x in (hosts,files)):raise ValueError('核查报告版本不一致')
    rows=report.get('results',[]);h=hosts.get('results',[]);f=files.get('results',[])
    def index(items):
        count=Counter((x.get('assetId'),x.get('pid')) for x in items)
        return {(x.get('assetId'),x.get('pid')):x for x in items if count[(x.get('assetId'),x.get('pid'))]==1}
    hm=index(h);fm=index(f);accepted=[];unresolved=[]
    for row in rows:
        key=(row.get('assetId'),row.get('pid'))
        if key not in hm and key not in fm:continue
        host=hm.get(key) or {};item=fm.get(key) or {};live=host.get('live') or {};remote=host.get('remote') or {}; app=row.get('applicationCandidates') or {}
        candidates=(item.get('files') or {}).get('files') or [];valid=[]
        for candidate in candidates:
            nodes=candidate.get('nodes') or []
            if len(nodes)!=1 or not candidate.get('openFileDescriptors'):continue
            n=nodes[0];flags=set(n.get('flags') or []);address=n.get('address','')
            if re.fullmatch(r'[0-9a-f]{40}',n.get('id','')) and flags=={'myself','master'} and n.get('masterId')=='-' and n.get('link')=='connected' and address==':6379@16379':valid.append((candidate,n))
        conns=live.get('connections') or [];listeners=live.get('listeners') or []
        good=(row.get('status')=='OBSERVED' and host.get('status')=='OBSERVED' and item.get('status')=='OBSERVED'
            and item.get('ip')==host.get('ip')==row.get('ip') and live.get('snapshotMatches') is True
            and row.get('startTicks') and row['startTicks']==live.get('startTicks')
            and remote.get('status')=='OBSERVED' and remote.get('exe')=='redis-server' and remote.get('hostNet') is True
            and remote.get('networkNamespace')==live.get('netns') and host.get('dockerWarnings')==[] and host.get('containerMatches')==[]
            and live.get('tcpFrames',0)>=2 and not live.get('warnings') and live.get('connectionCount')==len(conns)
            and not any(x.get('local',{}).get('port')==16379 or x.get('peer',{}).get('port')==16379 for x in conns)
            and any(x.get('port')==6379 and x.get('protocol')=='tcp' for x in listeners)
            and any(x.get('port')==16379 and x.get('protocol')=='tcp' for x in listeners)
            and row.get('clusterHint') is True and set(app)=={'TEST','SIMULATION','PRODUCT'} and not any(app.values())
            and len(valid)==1 and host.get('observedAt') and item.get('observedAt'))
        if good:accepted.append({'row':row,'host':host,'file':valid[0][0],'node':valid[0][1]})
        else:unresolved.append({'ip':row.get('ip'),'pid':row.get('pid'),'reason':'节点文件、进程归属、TCP 或应用关联未充分确认'})
    return {'accepted':sorted(accepted,key=lambda x:x['row']['ip']),'unresolved':unresolved}


def import_report(store, report, hosts, files, version, can_apply=lambda:True):
    p=plan(report,hosts,files)
    if report['sourceVersion']!=version:raise ValueError('当前快照已切换')
    store.migrate();source=store.source_key+':redis';payload={'report':report,'hosts':hosts,'files':files};sha=db.digest(db.encode(payload));units=[]
    with store.connection() as conn,store.database_lock(conn,'component-redis'):
        conn.begin()
        try:
            if store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,sha,'redis-single-cluster-v1')):
                conn.rollback();return {'unchanged':True,'unresolved':p['unresolved']}
            now=db.utcnow();run=db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/redis-review/cluster-files.json','snapshot_sha256':sha,'mapper_version':'redis-single-cluster-v1','scope_json':db.encode({'type':'Redis','scope':'held-single-member-cluster-file'}),'completeness_json':db.encode({'complete':False}),'observed_at':db.timestamp(files['completedAt']),'imported_at':now,'status':'WRITING'})
            for x in p['accepted']:
                row=x['row'];node=x['node'];key={'assetId':row['assetId'],'nodeId':node['id']};unitkey={'kind':'CLUSTER_SINGLE_FILE','members':[key]}
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='REDIS_GROUP' AND s.native_key_hash=%s",(source,db.digest(unitkey)))
                if found:units.append({'name':found[0]['name'],'ip':row['ip'],'existing':True});continue
                if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND (native_key_hash=%s OR JSON_UNQUOTE(JSON_EXTRACT(evidence_json,'$.nodeId'))=%s)",(source,db.digest(key),node['id'])):raise db.CatalogError('Redis 节点已绑定其他单元')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Redis',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Redis' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Redis'")
                name='Redis_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier();observed=db.timestamp(x['host']['observedAt'])
                evidence={'sourceVersion':version,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],'nodeId':node['id'],'nodeFile':x['file']['path'],'groupingBasis':'HELD_NODE_FILE_AND_TCP','adminStatus':'NOAUTH','reportPaths':['checks/redis-review/evidence/'+db.digest(db.encode(y))+'.json' for y in (report,hosts,files)]}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'Redis','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':'6379','transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'created_at':now,'updated_at':now})
                def obs(kind,owner,locator,attrs):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':observed,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'HELD_CLUSTER_FILE_LISTENER_TCP','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attrs)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'kind':'unit'},{'technology':'Redis','groupKind':'CLUSTER_SINGLE_FILE'})
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'kind':'environment'},{'unresolvedCount':0})
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'kind':'instance'},{'portBasis':'LIVE_LISTENER','observedEndpoints':x['host']['live']['listeners'],'role':'master','nodeId':node['id'],'groupKind':'CLUSTER_SINGLE_FILE','adminStatus':'NOAUTH'})
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs});store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'REDIS_GROUP','native_key_json':db.encode(unitkey),'native_key_hash':db.digest(unitkey),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'REDIS_CLUSTER_NODE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CLUSTER_NODE_ID','first_seen_at':observed,'last_seen_at':observed,'evidence_json':db.encode(evidence)})
                for kind,ident in (('UNIT',unit),('INSTANCE',instance)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':kind,'object_id':ident,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip'],'port':6379}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Redis Cluster 进程持有的节点文件和 TCP 核查'})
                units.append({'name':name,'ip':row['ip'],'port':6379,'existing':False})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in units),'unresolved':len(p['unresolved'])})});conn.commit()
        except Exception:conn.rollback();raise
    return {'units':units,'unresolved':p['unresolved']}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text())
    report,hosts,files=(read(root/x) for x in ('latest.json','cluster-single-hosts.json','cluster-files.json'));version=read(data/'current.json')['id']
    for value in (hosts,files):
        t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
        if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
    p=plan(report,hosts,files)
    if version!=report['sourceVersion']:raise ValueError('当前快照已切换')
    if not args.apply:
        print(json.dumps({'candidates':[{'ip':x['row']['ip'],'port':6379} for x in p['accepted']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    for value in (report,hosts,files):
        payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
        if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
        if not path.exists():
            tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(import_report(db.Catalog.from_environment(),report,hosts,files,version,can_apply=lambda:read(data/'current.json').get('id')==version),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
