"""Import reviewed Kong nodes under the user's temporary per-node rule."""
import ipaddress
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def plan(report, workers, listeners):
    version=report.get('sourceVersion')
    if not version or workers.get('sourceVersion')!=version or listeners.get('sourceVersion')!=version:
        raise ValueError('核查报告版本不一致')
    wkeys=Counter((x.get('ip'),x.get('master')) for x in workers.get('results',[]))
    lkeys=Counter((x.get('assetId'),x.get('pid')) for x in listeners.get('results',[]))
    wmap={(x.get('ip'),x.get('master')):x for x in workers.get('results',[])}
    lmap={(x.get('assetId'),x.get('pid')):x for x in listeners.get('results',[])}
    node_counts=Counter((x.get('admin') or {}).get('node_id') for x in report.get('results',[]))
    accepted=[];unresolved=[]
    for row in report.get('results',[]):
        ip,pid=row.get('ip'),row.get('pid');admin=row.get('admin') or {};node=admin.get('node_id')
        w=wmap.get((ip,pid)) or {};l=lmap.get((row.get('assetId'),pid)) or {};reason=None
        if row.get('status')!='OBSERVED':reason='主进程核查未完成'
        try:ipaddress.ip_address(ip)
        except (ValueError,TypeError):reason=reason or '资产 IP 无效'
        if not row.get('assetId') or not isinstance(pid,int) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',str(node or ''),re.I) or node_counts[node]!=1:
            reason=reason or 'Kong 节点身份不唯一'
        if row.get('frames',0)<2 or row.get('warnings') or row.get('connectionCount',0)>len(row.get('connections',[])):
            reason=reason or 'master TCP 采样不完整'
        apps=row.get('applicationCandidates') or {}
        if set(apps)!={'TEST','SIMULATION','PRODUCT'} or any(apps.values()):reason=reason or '存在应用候选或关联未确认'
        config=row.get('configEvidence') or []
        if not config or config[0]!='CONFIG_READABLE' or len(config)>=105:reason=reason or '配置不可读或截断'
        fields={}
        for line in config[1:]:
            key,sep,val=line.partition('=');key=key.strip()
            if not sep or key in fields:reason=reason or '配置字段冲突';break
            fields[key]=val.strip()
        if not admin.get('version') or not admin.get('role') or fields.get('role')!=admin.get('role') or fields.get('database')!=admin.get('database'):
            reason=reason or '管理接口与运行配置不一致'
        if wkeys[(ip,pid)]!=1 or w.get('status')!='OBSERVED' or not w.get('workers'):
            reason=reason or 'worker 核查未完成'
        worker_ids=set()
        for worker in w.get('workers',[]):
            if worker.get('status')!='OBSERVED' or worker.get('frames',0)<2 or worker.get('warnings') or worker.get('connectionCount',0)>len(worker.get('connections',[])):
                reason=reason or 'worker TCP 采样不完整'
            if not isinstance(worker.get('pid'),int) or worker['pid'] in worker_ids:reason=reason or 'worker 身份冲突'
            worker_ids.add(worker.get('pid'))
        if lkeys[(row.get('assetId'),pid)]!=1 or l.get('status')!='OBSERVED' or l.get('warnings') or l.get('dockerWarnings') or l.get('containerAncestors') or not l.get('hostNetwork') or l.get('hostNetwork')!=l.get('processNetwork') or not l.get('hostMountNamespace') or not l.get('processMountNamespace') or row.get('startTicks')!=l.get('startTicks'):
            reason=reason or '监听或网络归属未确认'
        if l.get('hostMountNamespace')!=l.get('processMountNamespace'):
            service_proof=(l.get('hostRoot') and l.get('hostRoot')==l.get('processRoot') and
                l.get('hostPidNamespace') and l.get('hostPidNamespace')==l.get('processPidNamespace') and
                l.get('serviceId')=='kong.service' and l.get('serviceMainPid')==str(pid) and
                l.get('serviceControlGroup')=='/system.slice/kong.service' and pid in l.get('ancestors',[]))
            if not service_proof:reason=reason or '进程挂载空间及 systemd 归属未确认'
        admin_listeners=[x for x in l.get('listeners',[]) if x.get('endpoint',{}).get('port')==8001]
        if not admin_listeners or any(set(x.get('pids',[]))!={pid,*worker_ids} for x in admin_listeners):
            reason=reason or '管理端口进程归属不唯一'
        if not row.get('observedAt') or not l.get('observedAt') or not row.get('startTicks'):
            reason=reason or '观察时间或进程身份缺失'
        endpoints=[x for x in row.get('listeners',[]) if x.get('protocol')=='tcp' and isinstance(x.get('port'),int) and 0<x['port']<=65535]
        if not endpoints:reason=reason or '无已核实 TCP 监听'
        if reason:unresolved.append({'ip':ip,'pid':pid,'reason':reason});continue
        accepted.append({'row':row,'worker':w,'listener':l,'endpoints':endpoints})
    return {'accepted':accepted,'unresolved':unresolved}


def import_report(store, report, workers, listeners, source_version, can_apply=lambda:True):
    prepared=plan(report,workers,listeners)
    if report['sourceVersion']!=source_version:raise ValueError('当前版本与核查来源不一致')
    store.migrate();source=store.source_key+':kong';payload={'report':report,'workers':workers,'listeners':listeners};report_hash=db.digest(db.encode(payload));result=[]
    with store.connection() as conn,store.database_lock(conn,'component-kong'):
        conn.begin()
        try:
            old=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,report_hash,'kong-review-v1'))
            if old:conn.rollback();return {'unchanged':True,'unresolved':prepared['unresolved']}
            now=db.utcnow();run_id=db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run_id,'source_instance_key':source,'source_version':source_version,
                'source_relative_path':'checks/kong-review/latest.json','snapshot_sha256':report_hash,'mapper_version':'kong-review-v1',
                'scope_json':db.encode({'type':'Kong'}),'completeness_json':db.encode({'complete':False}),
                'observed_at':db.timestamp(report.get('completedAt')),'imported_at':now,'status':'WRITING'})
            for item in prepared['accepted']:
                row=item['row']; node=row['admin']['node_id'];observed=db.timestamp(row['observedAt']);key={'assetId':row['assetId'],'nodeId':node}
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='KONG_NODE_UNIT' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:result.append({'name':found[0]['name'],'ip':row['ip'],'endpoints':item['endpoints'],'existing':True});continue
                conflicts=store.execute(conn,"SELECT id FROM deployment_unit_source WHERE source_instance_key=%s AND object_kind='KONG_NODE_UNIT' AND (JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s OR JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.nodeId'))=%s)",(source,row['assetId'],node))
                if conflicts:raise db.CatalogError('Kong 资产或节点已有不同绑定，需人工核实')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Kong',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Kong' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Kong'")
                name='Kong_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier()
                evidence={'sourceVersion':source_version,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],
                    'observedAt':row['observedAt'],'nodeId':node,'version':row['admin']['version'],'role':row['admin']['role'],
                    'groupingBasis':'USER_PER_NODE_PENDING','groupingState':'PENDING','configEvidence':row['configEvidence'],
                    'workerFrames':[x['frames'] for x in item['worker']['workers']],'serviceEvidence':{key:item['listener'].get(key) for key in ('serviceId','serviceMainPid','serviceControlGroup','serviceFragment','hostRoot','processRoot','hostPidNamespace','processPidNamespace','hostMountNamespace','processMountNamespace')},
                    'reportPaths':['checks/kong-review/evidence/'+db.digest(db.encode(x))+'.json' for x in (report,workers,listeners)]}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'Kong','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'created_at':now,'updated_at':now})
                def observation(kind,owner,locator,attributes):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run_id,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':observed,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'KONG_CONFIG_ADMIN_WORKER_TCP','source_attributes_json':db.encode(attributes),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attributes)});return oid
                loc={'assetId':row['assetId'],'nodeId':node}
                uobs=observation('COMPONENT',{'unit_id':unit},{**loc,'kind':'unit'},{'technology':'Kong','groupingState':'PENDING'})
                eobs=observation('ENVIRONMENT',{'unit_environment_id':env},{**loc,'kind':'environment'},{'unresolvedCount':0})
                iobs=observation('DEPLOYMENT',{'instance_id':instance},{**loc,'kind':'instance'},{'portBasis':'LIVE_LISTENER','observedEndpoints':item['endpoints'],'groupingBasis':'USER_PER_NODE_PENDING','groupingState':'PENDING','role':row['admin']['role']})
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs});store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'KONG_NODE_UNIT','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'KONG_NODE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_AND_KONG_NODE_ID','first_seen_at':observed,'last_seen_at':observed,'evidence_json':db.encode(evidence)})
                for kind,ident in (('UNIT',unit),('INSTANCE',instance)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run_id,'object_type':kind,'object_id':ident,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip']}),'actor_type':'IMPORT','import_run_id':run_id,'changed_at':now,'reason':'Kong 只读核查，按用户规则每节点一个单元，编组待确认'})
                result.append({'name':name,'ip':row['ip'],'endpoints':item['endpoints'],'existing':False})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run_id,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in result),'unresolved':len(prepared['unresolved'])})})
            conn.commit()
        except Exception:conn.rollback();raise
    return {'units':result,'unresolved':prepared['unresolved']}


def main():
    import argparse
    parser=argparse.ArgumentParser(description='按用户暂定规则导入 Kong 节点，编组待确认')
    parser.add_argument('--apply',action='store_true',help='写入目录；默认只预览');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));root=data/'checks'/'kong-review';read=lambda p:json.loads(p.read_text())
    report,workers,listeners=(read(root/x) for x in ('latest.json','workers.json','listeners.json'));version=read(data/'current.json')['id']
    for value in (report,workers,listeners):
        completed=datetime.fromisoformat(value['completedAt'].replace('Z','+00:00'))
        if completed>datetime.now(timezone.utc)+timedelta(minutes=5) or datetime.now(timezone.utc)-completed>timedelta(hours=2):raise ValueError('核查证据已过期，请重新核查')
    preview=plan(report,workers,listeners)
    if report['sourceVersion']!=version:raise ValueError('当前版本与核查来源不一致')
    if not args.apply:
        print(json.dumps({'candidates':[{'ip':x['row']['ip'],'endpoints':x['endpoints']} for x in preview['accepted']],'unresolved':preview['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    for value in (report,workers,listeners):
        payload=db.encode(value);path=evidence/(db.digest(payload)+'.json')
        if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
        if not path.exists():
            tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(import_report(db.Catalog.from_environment(),report,workers,listeners,version,can_apply=lambda:read(data/'current.json').get('id')==version),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
