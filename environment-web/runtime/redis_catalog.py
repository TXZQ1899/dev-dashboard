"""Import Redis groups supported by reciprocal, live node evidence."""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def plan(report, containers):
    if not report.get('sourceVersion') or report['sourceVersion'] != containers.get('sourceVersion'):
        raise ValueError('核查报告版本不一致')
    rows = report.get('results', [])
    counts = Counter((x.get('assetId'), x.get('pid')) for x in rows)
    checks = {}
    for host in containers.get('results', []):
        if host.get('status') != 'OBSERVED' or not host.get('observedAt'):
            continue
        for node in host.get('nodes', []):
            key = (host.get('assetId'), node.get('pid'))
            checks.setdefault(key, []).append((host, node))
    eligible = []
    for row in rows:
        key = (row.get('assetId'), row.get('pid'))
        matches = checks.get(key, [])
        if counts[key] != 1 or len(matches) != 1:
            continue
        host, node = matches[0]; c = node.get('container') or {}; p = row.get('processPort')
        apps = (row.get('applicationCandidates'), node.get('hostApplicationCandidates'))
        mappings = c.get('ports', {}).get(str(p) + '/tcp') or []
        ports = {int(x['HostPort']) for x in mappings if x.get('HostIp') in ('0.0.0.0', row.get('ip')) and str(x.get('HostPort', '')).isdigit()}
        if not (row.get('status') == 'OBSERVED' and (row.get('redisInfo') or {}).get('status') == 'OBSERVED'
                and row.get('frames', 0) >= 2 and not row.get('warnings') and row.get('observedAt')
                and row.get('startTicks') and row['startTicks'] == node.get('startTicks') == c.get('startTicks')
                and c.get('initPid') == row.get('pid') and c.get('networkNamespace') == row.get('networkNamespace')
                and c.get('id') and c.get('name') and c.get('ips') and host.get('ip') == row.get('ip')
                and isinstance(p, int) and 0 < p <= 65535 and len(ports) == 1 and node.get('publishedPort') in ports
                and all(a is not None and set(a) == {'TEST','SIMULATION','PRODUCT'} and not any(a.values()) for a in apps)
                and any(x.get('port') == p and x.get('protocol') == 'tcp' for x in row.get('listeners', []))):
            continue
        eligible.append({'row':row, 'container':c, 'port':node['publishedPort']})
    groups = []; used = set()
    # Each cluster node must return the same complete, healthy view and identify itself.
    cluster = [x for x in eligible if x['row']['redisInfo']['sections'].get('cluster',{}).get('cluster_enabled') == '1']
    signatures = {}
    for item in cluster:
        row=item['row']; nodes=row['redisInfo']['sections'].get('clusterNodes',[]); view=[]; own=[]
        for n in nodes:
            flags=set(n.get('flags',[])); ident=n.get('id',''); address=n.get('address','').split('@')[0]
            if not re.fullmatch(r'[0-9a-f]{40}',ident) or n.get('link') != 'connected' or flags & {'fail','fail?','handshake','noaddr'}:
                view=[];break
            if 'myself' in flags: own.append(n)
            view.append((ident,address,tuple(sorted(flags-{'myself'})),n.get('masterId')))
        if len(own)!=1 or len(view)<2 or len({v[0] for v in view})!=len(view) or len({v[1] for v in view})!=len(view): continue
        if own[0].get('address','').split('@')[0] != row['ip']+':'+str(item['port']):continue
        sig=tuple(sorted(view)); signatures.setdefault(sig,[]).append((item,own[0]))
    for sig, members in signatures.items():
        if len(members)!=len(sig) or {n['id'] for _,n in members}!={v[0] for v in sig}:continue
        ids={v[0] for v in sig}; masters={v[0] for v in sig if 'master' in v[2] and v[3]=='-'}
        if not masters or any(('slave' in v[2] and v[3] not in masters) or not ({'slave','master'} & set(v[2])) for v in sig):continue
        members=sorted(members,key=lambda x:(x[0]['row']['ip'],x[0]['port']))
        group=[]
        for item,n in members:
            item=dict(item, role='master' if 'master' in n['flags'] else 'replica', nodeId=n['id'], masterId=n['masterId']);group.append(item)
            used.add((item['row']['assetId'],item['row']['pid']))
        groups.append({'kind':'CLUSTER','members':group})
    # Match each primary's reported replica endpoints to local container IP+port,
    # and corroborate each replica's live TCP peer and replication status.
    for master in eligible:
        row=master['row']; s=row['redisInfo']['sections']; rep=s.get('replication',{})
        if s.get('cluster',{}).get('cluster_enabled')!='0' or rep.get('role')!='master':continue
        try: count=int(rep.get('connected_slaves','0'))
        except ValueError:continue
        if count<1:continue
        selected=[]
        for i in range(count):
            fields=dict(part.split('=',1) for part in rep.get('slave'+str(i),'').split(',') if '=' in part)
            if fields.get('state')!='online' or not fields.get('port','').isdigit():break
            found=[]
            for replica in eligible:
                rr=replica['row']; rs=rr['redisInfo']['sections']; rp=rs.get('replication',{})
                if rr.get('assetId')!=row.get('assetId') or rr.get('pid')==row.get('pid'):continue
                if fields.get('ip') not in replica['container'].get('ips',[]) or int(fields['port'])!=rr.get('processPort'):continue
                if rs.get('cluster',{}).get('cluster_enabled')!='0' or rp.get('role')!='slave' or rp.get('master_link_status')!='up' or rp.get('master_host')!=master['container']['name'] or rp.get('master_port')!=str(row['processPort']):continue
                if not any(x.get('peer',{}).get('ip') in master['container']['ips'] and x.get('peer',{}).get('port')==row['processPort'] and x.get('peer',{}).get('protocol')=='tcp' for x in rr.get('connections',[])):continue
                found.append(replica)
            if len(found)!=1:break
            selected.append(found[0])
        if len(selected)!=count or len({x['row']['pid'] for x in selected})!=count:continue
        members=[dict(master,role='master')]+[dict(x,role='replica') for x in sorted(selected,key=lambda x:x['port'])]
        keys={(x['row']['assetId'],x['row']['pid']) for x in members}
        if used & keys:continue
        used |= keys;groups.append({'kind':'REPLICATION','members':members})
    groups.sort(key=lambda g:(g['members'][0]['row']['ip'],g['kind']))
    unresolved=[{'ip':r.get('ip'),'pid':r.get('pid'),'reason':'认证、进程身份、应用关联或完整成员关系尚未确认'} for r in rows if (r.get('assetId'),r.get('pid')) not in used]
    return {'groups':groups,'unresolved':unresolved}


def import_report(store, report, containers, source_version, can_apply=lambda:True):
    prepared=plan(report,containers)
    if report['sourceVersion']!=source_version:raise ValueError('当前版本与核查来源不一致')
    store.migrate();source=store.source_key+':redis'; report_hash=db.digest(db.encode({'report':report,'containers':containers})); result=[]
    with store.connection() as conn,store.database_lock(conn,'component-redis'):
        conn.begin()
        try:
            if store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,report_hash,'redis-review-v1')):
                conn.rollback();return {'unchanged':True,'unresolved':prepared['unresolved']}
            now=db.utcnow();run=db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':source_version,'source_relative_path':'checks/redis-review/latest.json','snapshot_sha256':report_hash,'mapper_version':'redis-review-v1','scope_json':db.encode({'type':'Redis'}),'completeness_json':db.encode({'complete':False}),'observed_at':db.timestamp(report['completedAt']),'imported_at':now,'status':'WRITING'})
            for group in prepared['groups']:
                members=group['members']; observed=db.timestamp(members[0]['row']['observedAt']); keys=[{'assetId':x['row']['assetId'],'containerName':x['container']['name']} for x in members]
                key={'kind':group['kind'],'members':sorted(keys,key=lambda x:(x['assetId'],x['containerName']))}
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='REDIS_GROUP' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    result.append({'name':found[0]['name'],'existing':True,'members':keys});continue
                for member,nodekey in zip(members,keys):
                    if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND (native_key_hash=%s OR (%s IS NOT NULL AND JSON_UNQUOTE(JSON_EXTRACT(evidence_json,'$.nodeId'))=%s))",(source,db.digest(nodekey),member.get('nodeId'),member.get('nodeId'))):raise db.CatalogError('Redis 节点已绑定其他单元，需人工核实')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Redis',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Redis' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Redis'")
                name='Redis_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env=db.identifier(),db.identifier()
                evidence={'sourceVersion':source_version,'groupKind':group['kind'],'reportPaths':['checks/redis-review/evidence/'+db.digest(db.encode(x))+'.json' for x in (report,containers)]}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':'Redis','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                def obs(kind,owner,locator,attributes,ev):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':observed,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'REDIS_MEMBERSHIP_CONTAINER_TCP','source_attributes_json':db.encode(attributes),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attributes)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'Redis','groupKind':group['kind']},evidence)
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'unresolvedCount':0},evidence)
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'REDIS_GROUP','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                output=[]
                for x,nodekey in zip(members,keys):
                    row=x['row']; instance=db.identifier(); nodeev={**evidence,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],'container':x['container'],'observedAt':row['observedAt'],'nodeId':x.get('nodeId'),'masterId':x.get('masterId')}
                    store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':str(x['port']),'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'created_at':now,'updated_at':now})
                    attrs={'portBasis':'DOCKER_PUBLISHED','containerPort':row['processPort'],'role':x['role'],'groupKind':group['kind'],'nodeId':x.get('nodeId'),'masterId':x.get('masterId')}
                    iobs=obs('DEPLOYMENT',{'instance_id':instance},{**nodekey,'record':'instance'},attrs,nodeev)
                    store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                    store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'REDIS_CONTAINER_NODE','native_key_json':db.encode(nodekey),'native_key_hash':db.digest(nodekey),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONTAINER','first_seen_at':observed,'last_seen_at':observed,'evidence_json':db.encode(nodeev)})
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'INSTANCE','object_id':instance,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip'],'port':x['port']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Redis 成员、容器和 TCP 只读核查'})
                    output.append({'ip':row['ip'],'port':x['port'],'role':x['role']})
                store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'UNIT','object_id':unit,'action':'CREATE','after_json':db.encode({'name':name}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Redis 成员、容器和 TCP 只读核查'})
                result.append({'name':name,'kind':group['kind'],'members':output,'existing':False})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in result),'unresolved':len(prepared['unresolved'])})});conn.commit()
        except Exception:conn.rollback();raise
    return {'units':result,'unresolved':prepared['unresolved']}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'redis-review';read=lambda p:json.loads(p.read_text())
    report,containers=(read(root/x) for x in ('latest.json','containers.json')); version=read(data/'current.json')['id']
    for value in (report,containers):
        completed=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
        if completed>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-completed>timedelta(hours=2):raise ValueError('核查证据已过期')
    p=plan(report,containers)
    if version!=report['sourceVersion']:raise ValueError('当前版本与核查来源不一致')
    if not args.apply:
        print(json.dumps({'groups':[{'kind':g['kind'],'members':[{'ip':x['row']['ip'],'port':x['port'],'role':x['role']} for x in g['members']]} for g in p['groups']],'unresolved':p['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    for value in (report,containers):
        payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
        if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
        if not path.exists():
            tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(import_report(db.Catalog.from_environment(),report,containers,version,can_apply=lambda:read(data/'current.json').get('id')==version),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
