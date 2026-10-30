"""Import conservatively verified loopback-only Redis instances."""
import ipaddress, json, os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def plan(audit, fresh, startup, peers):
    version=audit.get('sourceVersion')
    if not version or any(x.get('sourceVersion')!=version for x in (fresh,startup,peers)):raise ValueError('核查报告版本不一致')
    def idx(report):
        rows=report.get('results',[]);counts=Counter((r.get('assetId'),r.get('pid')) for r in rows)
        return {(r.get('assetId'),r.get('pid')):r for r in rows if counts[(r.get('assetId'),r.get('pid'))]==1}
    fm,sm=idx(fresh),idx(startup);peer_by_ip={r.get('ip'):r for r in peers.get('results',[])};accepted=[];unresolved=[]
    for a in audit.get('results',[]):
        key=(a.get('assetId'),a.get('pid'));f=fm.get(key) or {};s=sm.get(key) or {};detail=s.get('detail') or {};start=detail.get('startup') or {};lines=a.get('lines') or []
        files=[];directives={};info={};runtime={};meta={}
        for line in lines:
            p=line.split('\t')
            if len(p)>=4 and p[0]=='FILE' and p[3]=='READABLE':files.append((p[1],p[2]))
            elif len(p)>=4 and p[0]=='DIRECTIVE':directives.setdefault(p[1],[]).append((p[2],p[3]))
            elif len(p)>=3 and p[0]=='INFO':info[p[1]]=p[2]
            elif len(p)>=3 and p[0]=='CONFIG_GET':runtime[p[1]]=p[2]
            elif len(p)>=3 and p[0]=='META':meta[p[1]]=p[2]
        matching=[path for path,basis in files if basis in ('systemd_unit_file','systemd_execstart') or path in start.get('unitConfigReferences',[])]
        matching=list(dict.fromkeys(matching));conf=matching[0] if len(matching)==1 else None;values=directives.get(conf,[])
        def last(k):return [v for n,v in values if n==k][-1:] or [None]
        sections=(f.get('redisInfo') or {}).get('sections') or {};server=sections.get('server') or {};rep=sections.get('replication') or {};cluster=sections.get('cluster') or {}
        apps=a.get('applicationCandidates') or {};listeners=f.get('listeners') or [];connections=a.get('connections') or []
        try:loopback=all(ipaddress.ip_address(x.get('ip','')).is_loopback and x.get('protocol')=='tcp' and x.get('port')==6379 for x in listeners)
        except ValueError:loopback=False
        try:local_conns=all(ipaddress.ip_address(x.get('local',{}).get('ip','')).is_loopback and ipaddress.ip_address(x.get('peer',{}).get('ip','')).is_loopback and x.get('local',{}).get('port')==6379 for x in connections)
        except ValueError:local_conns=False
        peer=peer_by_ip.get(a.get('ip')) or {};peer_ok=not connections or (peer.get('status')=='OBSERVED' and peer.get('startTicks')==a.get('startTicks') and peer.get('peerOwners') and all(x.get('name')=='java' for x in peer['peerOwners']) and not peer.get('peerWarnings') and all(flow in peer.get('flows',[]) for flow in connections))
        processes=detail.get('processes') or [];unique_process=len(processes)==1 and processes[0].get('pid')==a.get('pid') and processes[0].get('name','').startswith('redis-server')
        good=(a.get('status')=='OBSERVED' and f.get('status')=='OBSERVED' and (f.get('redisInfo') or {}).get('status')=='OBSERVED'
            and s.get('status')=='OBSERVED' and detail.get('status')=='OBSERVED' and a.get('ip')==f.get('ip')==s.get('ip')
            and a.get('startTicks') and a['startTicks']==f.get('startTicks')==s.get('startTicks') and a.get('observedAt') and f.get('observedAt') and s.get('observedAt')
            and a.get('frames',0)>=2 and f.get('frames',0)>=2 and not a.get('warnings') and not f.get('warnings')
            and meta.get('exe')=='redis-server' and meta.get('netns') and meta['netns']==meta.get('hostnetns') and meta.get('service')==start.get('unit') and bool(start.get('unit'))
            and a.get('container') is None and a.get('containerMatches')==0 and a.get('dockerWarnings')==[]
            and conf and values and len(values)<190 and not any(line.startswith(('DIRECTIVE_UNPARSED\t'+conf+'\t','INCLUDE_UNRESOLVED\t'+conf)) for line in lines) and not any(k in ('include','replicaof','slaveof') for k,v in values)
            and last('bind')==['127.0.0.1'] and last('port')==['6379'] and last('cluster-enabled')[0] in (None,'no')
            and server.get('redis_mode')=='standalone' and server.get('run_id') and rep.get('role')=='master' and rep.get('connected_slaves')=='0' and cluster.get('cluster_enabled')=='0'
            and (not info or (info.get('role')=='master' and info.get('connected_slaves')=='0' and info.get('run_id')==server.get('run_id')))
            and (not runtime or (runtime.get('bind')=='127.0.0.1' and runtime.get('port')=='6379'))
            and listeners and loopback and a.get('connectionCount')==len(connections) and local_conns and peer_ok and unique_process
            and set(apps)=={'TEST','SIMULATION','PRODUCT'} and not any(apps.values()))
        if good:accepted.append({'audit':a,'fresh':f,'startup':s,'configPath':conf,'peer':peer})
        else:unresolved.append({'ip':a.get('ip'),'pid':a.get('pid'),'reason':'未满足回环监听、有效配置、成员或应用归属的完整证据'})
    return {'accepted':sorted(accepted,key=lambda x:x['audit']['ip']),'unresolved':unresolved}


def import_report(store,audit,fresh,startup,peers,version,can_apply=lambda:True,prepared=None,extra_reports=(),mapper_version='redis-loopback-v1',source_path='checks/redis-review/config-audit.json'):
    p=prepared if prepared is not None else plan(audit,fresh,startup,peers)
    if audit['sourceVersion']!=version:raise ValueError('当前快照已切换')
    store.migrate();source=store.source_key+':redis';data=[audit,fresh,startup,peers]+list(extra_reports);sha=db.digest(db.encode(data));units=[]
    with store.connection() as conn,store.database_lock(conn,'component-redis'):
        conn.begin()
        try:
            if store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,sha,mapper_version)):
                conn.rollback();return {'unchanged':True,'unresolved':p['unresolved']}
            now=db.utcnow();run=db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':source_path,'snapshot_sha256':sha,'mapper_version':mapper_version,'scope_json':db.encode({'type':'Redis','scope':'loopback-single'}),'completeness_json':db.encode({'complete':False}),'observed_at':db.timestamp(audit['completedAt']),'imported_at':now,'status':'WRITING'})
            for x in p['accepted']:
                row=x['audit'];key=x.get('identity') or {'assetId':row['assetId'],'service':x['startup']['detail']['startup']['unit'],'configPath':x['configPath']};unitkey={'kind':'STANDALONE','members':[key]}
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='REDIS_GROUP' AND s.native_key_hash=%s",(source,db.digest(unitkey)))
                if found:units.append({'name':found[0]['name'],'ip':row['ip'],'existing':True});continue
                conflicts=store.execute(conn,"SELECT i.id FROM deployment_instance i JOIN deployment_unit_environment e ON e.id=i.unit_environment_id JOIN deployment_unit_source s ON s.unit_id=e.unit_id WHERE s.source_instance_key=%s AND i.primary_ip=%s AND i.presence_status='PRESENT'",(source,row['ip']))
                if conflicts:raise db.CatalogError('资产已有 Redis 单元，需人工复核避免重复绑定')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Redis',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Redis' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Redis'")
                name='Redis_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier();observed=db.timestamp(row['observedAt']);endpoint=x.get('endpoint') or {'ip':'127.0.0.1','port':x.get('port',6379),'protocol':'tcp'}
                evidence={'sourceVersion':version,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],'service':key.get('service'),'containerName':key.get('containerName'),'configPath':x['configPath'],'groupingBasis':x.get('groupingBasis','LOOPBACK_CONFIG_RUNTIME_TCP'),'clusterEvidence':x.get('clusterEvidence'),'detail':x.get('evidenceExtra',{}),'limitations':['不排除未观察到的历史或失联副本'],'reportPaths':['checks/redis-review/evidence/'+db.digest(db.encode(y))+'.json' for y in data]}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'Redis','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':str(x['hostPort']) if x.get('hostPort') else None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'created_at':now,'updated_at':now})
                def obs(kind,owner,locator,attrs):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':observed,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':x.get('matchingBasis','REDIS_CONFIG_RUNTIME_LOOPBACK_TCP'),'source_attributes_json':db.encode(attrs),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attrs)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'kind':'unit'},{'technology':'Redis','groupKind':'STANDALONE'})
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'kind':'environment'},{'unresolvedCount':0})
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'kind':'instance'},{'portBasis':x.get('portBasis','LIVE_LISTENER'),'observedEndpoints':[endpoint],'containerPort':x.get('containerPort'),'role':'master','groupKind':'STANDALONE','listenerScope':x.get('listenerScope','LOOPBACK')})
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs});store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'REDIS_GROUP','native_key_json':db.encode(unitkey),'native_key_hash':db.digest(unitkey),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':x.get('sourceKind','REDIS_HOST_SERVICE'),'native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':x.get('sourceMatchingBasis') or ('ASSET_CONTAINER' if x.get('sourceKind')=='REDIS_CONTAINER_NODE' else 'ASSET_SERVICE_CONFIG'),'first_seen_at':observed,'last_seen_at':observed,'evidence_json':db.encode(evidence)})
                for kind,ident in (('UNIT',unit),('INSTANCE',instance)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':kind,'object_id':ident,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip'],'endpoint':endpoint}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':x.get('reason','Redis 配置、运行状态和回环 TCP 只读核查')})
                units.append({'name':name,'ip':row['ip'],'listener':endpoint,'existing':False})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in units),'unresolved':len(p['unresolved'])})});conn.commit()
        except Exception:conn.rollback();raise
    return {'units':units,'unresolved':p['unresolved']}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text())
    reports=[read(root/x) for x in ('config-audit.json','standalone-fresh.json','startup-audit.json','loopback-extra.json')];version=read(data/'current.json')['id']
    for value in reports:
        t=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
        if t>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-t>timedelta(hours=2):raise ValueError('核查证据已过期')
    p=plan(*reports)
    if version!=reports[0]['sourceVersion']:raise ValueError('当前快照已切换')
    if not args.apply:
        print(json.dumps({'candidates':[{'ip':x['audit']['ip'],'configPath':x['configPath'],'listener':'127.0.0.1:6379'} for x in p['accepted']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    for value in reports:
        payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
        if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
        if not path.exists():
            tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(import_report(db.Catalog.from_environment(),*reports,version,can_apply=lambda:read(data/'current.json').get('id')==version),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
