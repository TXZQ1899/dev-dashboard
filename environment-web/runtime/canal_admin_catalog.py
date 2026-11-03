"""Import an Admin-confirmed standalone Canal server with scoped read-only evidence."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
try:
    import canal_catalog as base
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import canal_catalog as base
    from runtime import deployment_catalog as db

RULE='canal-admin-single-v2'


def report_from_sources(data):
    data=Path(data);read=lambda p:json.loads(p.read_text());raw=base.report_from_sources(data);v=raw['sourceVersion'];root=data/'checks'/'canal-review'
    names=[p+'-'+v+'.json' for p in ('admin-members','admin-config-v2','admin-standalone-details')];members,configs,details=[read(root/n) for n in names]
    if any(x.get('sourceVersion')!=v for x in (members,configs,details)):raise ValueError('Admin 报告来源版本不一致')
    candidates=[x for x in raw['results'] if (x.get('initial') or {}).get('fields',{}).get('canal.admin.manager')]
    confirmation_path=root/('user-canal-app-scope-'+v+'.json')
    confirmation=read(confirmation_path) if confirmation_path.exists() else None
    if confirmation and confirmation.get('sourceVersion')!=v:raise ValueError('用户确认来源版本不一致')
    return {'sourceVersion':v,'ruleVersion':RULE,'jumpserverCollectedAt':raw['jumpserverCollectedAt'],'devopsCollectedAt':raw['devopsCollectedAt'],'reportFiles':raw['reportFiles']+names,'results':candidates,'adminMembers':members,'adminConfigs':configs,'instanceDetails':details,'userConfirmation':confirmation}


def host_apps_unresolved(report,row):
    apps=row.get('hostDeployments') or []
    if not apps:return False
    confirmation=report.get('userConfirmation') or {}
    if confirmation.get('sourceVersion')!=report.get('sourceVersion') or confirmation.get('kind')!='USER_CONFIRMED_APPS_DO_NOT_MANAGE_CANAL' or not confirmation.get('confirmedAt'):return True
    confirmed={(x.get('assetId'),x.get('env'),x.get('appId')) for x in confirmation.get('confirmedApplications',[])}
    return any(not x.get('appId') or (row.get('assetId'),x.get('env'),x.get('appId')) not in confirmed for x in apps)


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion')!=RULE:raise ValueError('来源或规则版本无效')
    members=report.get('adminMembers') or {};configs=report.get('adminConfigs') or {};details=report.get('instanceDetails') or {}
    if any(x.get('sourceVersion')!=report['sourceVersion'] for x in (members,configs,details)):raise ValueError('Admin 证据版本不一致')
    by_admin={x.get('admin'):x for x in members.get('results',[])};by_config={x.get('admin'):x for x in configs.get('results',[])}
    if len(by_admin)!=len(members.get('results',[])) or len(by_config)!=len(configs.get('results',[])):raise ValueError('Admin 证据重复')
    rows=report.get('results',[]);counts=Counter((r.get('assetId'),r.get('pid')) for r in rows);groups=[];unresolved=[]
    for r in rows:
        i=r.get('initial') or {};fields=i.get('fields') or {};manager=fields.get('canal.admin.manager','');reason=None
        try:ipaddress.ip_address(r.get('ip'))
        except (ValueError,TypeError):reason='资产地址无效'
        if manager.startswith('127.0.0.1:'):manager=r.get('ip','')+manager[len('127.0.0.1'):]
        a=by_admin.get(manager) or {};cfg=by_config.get(manager) or {};dt=details.get('result') or {}
        if not r.get('assetId') or r.get('processStatus')!='complete' or counts[(r.get('assetId'),r.get('pid'))]!=1:reason=reason or '进程采集不完整或重复'
        path=i.get('configArgument') or '';proc=i.get('before');match=re.fullmatch(r'(\d+) \(java\) (\d+)',str(proc or ''))
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+/conf/canal\.properties',path) or i.get('configArgumentMatch')!='MATCH' or i.get('configReadable')!='YES' or not i.get('configOutputComplete') or not match or int(match[1])!=r.get('pid') or i.get('after')!=proc:reason=reason or '启动配置或进程身份不一致'
        nodes=(a.get('nodes') or {}).get('items') or [];matches=[x for x in nodes if x.get('ip')==r.get('ip') and x.get('adminPort')==11110]
        node=matches[0] if len(matches)==1 else {}
        if a.get('status')!='COMPLETE' or (a.get('nodes') or {}).get('status')!='COMPLETE' or (a.get('instances') or {}).get('status')!='COMPLETE' or len(matches)!=1 or node.get('clusterId') is not None or node.get('status')!='1':reason=reason or 'Admin 单机 Server 视图不完整或冲突'
        if not any(re.search(r'(?:\*|\[?::\]?)?:11110\s',x) and 'pid='+str(r.get('pid'))+',' in x for x in i.get('listeners') or []):reason=reason or 'Admin 监听归属未确认'
        matches_cfg=[x for x in cfg.get('configs',[]) if x.get('ip')==r.get('ip') and x.get('nodeId')==node.get('id') and x.get('clusterId') is None]
        config=matches_cfg[0] if len(matches_cfg)==1 else {};cf=config.get('fields') or {}
        if cfg.get('status')!='COMPLETE' or config.get('status')!='OBSERVED' or len(matches_cfg)!=1 or cf.get('canal.zkServers')!='' or cf.get('canal.instance.global.mode')!='manager' or cf.get('canal.instance.global.spring.xml')!='classpath:spring/file-instance.xml':reason=reason or 'Admin 单机协调配置未确认'
        try:
            changed=datetime.strptime(config['modifiedTime'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8)))
            started=datetime.fromisoformat(r['startedAt'].replace('Z','+00:00'))
            if changed>started:reason=reason or 'Admin 配置在进程启动后修改'
        except (KeyError,ValueError,TypeError,AttributeError):reason=reason or 'Admin 配置时间未知'
        if host_apps_unresolved(report,r):reason=reason or '同机 DevOps 应用归属待核实'
        listed=[x for x in (a.get('instances') or {}).get('items',[]) if x.get('serverId')==node.get('id') and x.get('clusterId') is None]
        detail_rows=dt.get('instances') or [];expected={x.get('id') for x in listed};actual={x.get('id') for x in detail_rows}
        if (dt.get('status')!='COMPLETE' or dt.get('admin')!=manager or dt.get('serverId')!=node.get('id') or not expected or expected!=actual or len(detail_rows)!=len(actual) or any(x.get('contentStatus')!='OBSERVED' or x.get('coordinationKeys')!=[] or x.get('serverId')!=node.get('id') or x.get('clusterId') is not None for x in detail_rows)):
            reason=reason or 'destination 完整性或协调字段未确认'
        for stamp in (i.get('checkedAt'),a.get('observedAt'),cfg.get('observedAt'),dt.get('observedAt')):
            try:
                age=datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if age>timedelta(hours=2) or age< -timedelta(minutes=5):reason=reason or '实时核查已过期'
            except (ValueError,TypeError,AttributeError):reason=reason or '核查时间无效'
        if reason:unresolved.append({'ip':r.get('ip'),'pid':r.get('pid'),'reason':reason});continue
        groups.append({'key':{'assetId':r['assetId'],'configPath':path},'row':r,'destinations':sorted(x['name'] for x in detail_rows),'serverMode':cf.get('canal.serverMode'),'admin':manager,'adminNodeId':node['id']})
    return {'groups':sorted(groups,key=lambda x:x['row']['ip']),'unresolved':unresolved}
def import_report(store,report,version,can_apply=lambda:True,planner=plan):
    prepared=planner(report);mapper=report['ruleVersion']
    if report['sourceVersion']!=version:raise ValueError('当前版本已切换')
    source=store.source_key+':canal';payload=db.encode(report);digest=db.digest(report)
    root=Path(os.environ.get('ENVSCOPE_DATA','/data'))/'checks'/'canal-review'/'evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
    if not path.exists():
        tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();results=[]
    with store.connection() as conn,store.database_lock(conn,'component-canal'):
        conn.begin()
        try:
            if store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,mapper)):
                conn.rollback();return {'unchanged':True,'unresolved':prepared['unresolved']}
            now=db.utcnow();run=db.identifier();observed=db.timestamp(max((x['row']['initial']['checkedAt'] for x in prepared['groups']),default=datetime.now(timezone.utc).isoformat()))
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/canal-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':mapper,'scope_json':db.encode({'type':'Canal'}),'completeness_json':db.encode({'complete':False}),'observed_at':observed,'imported_at':now,'status':'WRITING'})
            for item in prepared['groups']:
                key=item['key'];row=item['row'];stamp=db.timestamp(row['initial']['checkedAt']);basis=item.get('groupingBasis','ADMIN_STANDALONE_NO_ZOOKEEPER')
                found=store.execute(conn,"SELECT s.unit_id,u.name,s.evidence_json FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='CANAL_SERVER' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    prior=db.decode(found[0]['evidence_json'],{})
                    if prior.get('groupingBasis')!=basis or prior.get('admin')!=item['admin'] or prior.get('adminNodeId')!=item['adminNodeId'] or prior.get('adminClusterId')!=item.get('adminClusterId'):
                        raise db.CatalogError('Canal 已有单元的 Admin 归属变化，需人工复核')
                    results.append({'name':found[0]['name'],'ip':row['ip'],'existing':True});continue
                existing=store.execute(conn,"SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,key['assetId']))
                if existing:raise db.CatalogError('资产已有 Canal 节点绑定，身份变化或多实例需复核')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Canal',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Canal' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Canal'")
                name='Canal_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier()
                grouping_state='PENDING' if basis=='USER_PER_NODE_PENDING' else None
                coverage='Admin 不可达；用户指定单节点展示，编组和成员未知' if grouping_state else 'Admin 与协调视图及当前进程观察；历史或未来变化未知'
                grouping_confidence='UNKNOWN' if grouping_state else 'INFERRED'
                ev={'sourceVersion':version,'reportPath':'checks/canal-review/evidence/'+digest+'.json','assetId':key['assetId'],'configPath':key['configPath'],'admin':item['admin'],'adminNodeId':item['adminNodeId'],'adminClusterId':item.get('adminClusterId'),'coverage':coverage,'groupingBasis':basis,'groupingState':grouping_state,'groupingConfidence':grouping_confidence}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'Canal','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,locator,attrs):
                    matching='CANAL_USER_PENDING_PROCESS' if grouping_state else ('CANAL_ADMIN_ZK_PROCESS' if basis in ('ADMIN_CLUSTER_ZK_AGREEMENT','ADMIN_STANDALONE_ZK_DISJOINT') else 'CANAL_ADMIN_SINGLE_PROCESS')
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':matching,'source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'Canal','groupingBasis':basis,'groupingState':grouping_state,'groupingConfidence':grouping_confidence})
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'unresolvedCount':0});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'CANAL_SERVER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':key['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                endpoints=[]
                for line in row['initial']['listeners']:
                    parts=line.split()
                    if len(parts)<4 or ':' not in parts[3]:continue
                    address,port=parts[3].rsplit(':',1)
                    if port.isdigit():endpoints.append({'ip':address.strip('[]'),'port':int(port),'protocol':'tcp','scope':'HOST'})
                attrs={'portBasis':'OBSERVED_LISTENERS','observedEndpoints':endpoints,'destinations':item['destinations'],'destinationsState':'UNKNOWN' if grouping_state else 'OBSERVED','serverMode':item['serverMode'],'groupingBasis':basis,'groupingState':grouping_state,'groupingConfidence':grouping_confidence}
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'CANAL_SERVER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONFIG_PATH','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('INSTANCE',instance),('UNIT',unit)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Canal Admin、进程与协调视图只读核查'})
                results.append({'name':name,'ip':row['ip'],'destinations':item['destinations'],'existing':False})
            if not can_apply():raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results),'unresolved':len(prepared['unresolved'])})});conn.commit()
        except Exception:conn.rollback();raise
    return {'units':results,'unresolved':prepared['unresolved']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'))
    report=report_from_sources(data);prepared=plan(report)
    if not args.apply:
        print(json.dumps({'groups':[{'ip':x['row']['ip'],'destinations':x['destinations'],'configPath':x['key']['configPath']} for x in prepared['groups']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
    result=import_report(db.Catalog.from_environment(),report,report['sourceVersion'],can_apply=lambda:json.loads((data/'current.json').read_text()).get('id')==report['sourceVersion'])
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
