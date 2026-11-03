"""Import locally configured Canal servers with directly observed standalone evidence."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re

try:
    import application_check as ac
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import application_check as ac
    from runtime import deployment_catalog as db

RULE = 'canal-local-single-v1'


def report_from_sources(data):
    data=Path(data);read=lambda p:json.loads(p.read_text());version=read(data/'current.json')['id']
    root=data/'checks'/'canal-review';names=[prefix+'-'+version+'.json' for prefix in ('initial','local','scope-ns','spring')]
    reports=[read(root/n) for n in names]
    if any(x.get('sourceVersion')!=version for x in reports): raise ValueError('核查来源不是当前快照')
    maps=[]
    for report in reports:
        rows=report.get('results',[]);keys=[(x.get('assetId'),x.get('pid')) for x in rows]
        if len(keys)!=len(set(keys)): raise ValueError('核查报告存在重复进程')
        maps.append(dict(zip(keys,rows)))
    snapshot=read(data/'versions'/version/'snapshot.json');jump=read(data/'versions'/version/'jumpserver-snapshot.json')
    if snapshot.get('complete') is not True or snapshot.get('scope')!='all':raise ValueError('DevOps 快照不完整')
    results=[]
    for asset in jump.get('assets',[]):
        ins=asset.get('inspection') or {}
        for proc in ins.get('processes',[]):
            if 'com.alibaba.otter.canal.deployer.CanalLauncher' not in proc.get('command',''):continue
            key=(asset.get('id'),proc.get('pid'));initial,local,scope,spring=(m.get(key) for m in maps)
            deployments=[]
            for app in snapshot.get('apps',[]):
                for env,items in app.get('envs',{}).items():
                    for item in items:
                        if ac.ips(item.get('ip')) & ac.ips(asset.get('ip')):
                            deployments.append({'env':env,'appId':str(app.get('id','')),'name':app.get('name')})
            results.append({'assetId':key[0],'ip':asset.get('ip'),'pid':key[1],
                            'startedAt':proc.get('startedAt'),'processStatus':ins.get('processStatus'),
                            'initial':initial,'local':local,'scope':scope,'spring':spring,'hostDeployments':deployments})
    return {'sourceVersion':version,'ruleVersion':RULE,'jumpserverCollectedAt':jump.get('collectedAt'),
            'devopsCollectedAt':snapshot.get('collectedAt'),'reportFiles':names,'results':results}


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion')!=RULE:raise ValueError('来源或规则版本无效')
    rows=report.get('results',[]);counts=Counter((r.get('assetId'),r.get('pid')) for r in rows);groups=[];unresolved=[]
    for r in rows:
        i=r.get('initial') or {};l=r.get('local') or {};s=r.get('scope') or {};spring=r.get('spring') or {};f=i.get('fields') or {};reason=None
        try:ipaddress.ip_address(r.get('ip'))
        except (TypeError,ValueError):reason='资产地址无效'
        if not r.get('assetId') or r.get('processStatus')!='complete' or counts[(r.get('assetId'),r.get('pid'))]!=1:reason=reason or '进程采集不完整或重复'
        if not l or not s or not spring:reason=reason or '本地独立部署专项核查未覆盖，Admin 或协调归属待核实'
        path=i.get('configArgument') or ''
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+/conf/canal\.properties',path) or i.get('configArgumentMatch')!='MATCH' or i.get('configReadable')!='YES' or not i.get('configOutputComplete'):
            reason=reason or '启动配置不可确认'
        proc=i.get('before');expected=re.fullmatch(r'(\d+) \(java\) (\d+)',str(proc or ''))
        if (not expected or int(expected[1])!=r.get('pid') or i.get('after')!=proc or l.get('before')!=proc or l.get('after')!=proc or s.get('BEFORE')!=proc or s.get('AFTER')!=proc):reason=reason or '进程身份不一致'
        if any(not s.get('PID_'+name) or s.get('PID_'+name)!=s.get('HOST_'+name) for name in ('NET','PIDNS','MNT')):reason=reason or '命名空间或宿主机归属未确认'
        if l.get('propertyOverrides')!=['canal.conf'] or s.get('environmentKeys')!=[]:reason=reason or '存在未解析的配置覆盖'
        allowed_classes={'com.alibaba.otter.canal.instance.spring.CanalInstanceWithSpring','com.alibaba.otter.canal.common.alarm.LogAlarmHandler','com.alibaba.otter.canal.meta.FileMixedMetaManager','com.alibaba.otter.canal.store.memory.MemoryEventStoreWithBuffer','com.alibaba.otter.canal.sink.entry.EntryEventSink','com.alibaba.otter.canal.parse.ha.HeartBeatHAController','com.alibaba.otter.canal.filter.aviater.AviaterRegexFilter','com.alibaba.otter.canal.parse.index.FailbackLogPositionManager','com.alibaba.otter.canal.parse.index.MemoryLogPositionManager','com.alibaba.otter.canal.parse.index.MetaLogPositionManager','com.alibaba.otter.canal.parse.support.AuthenticationInfo','com.alibaba.otter.canal.protocol.position.EntryPosition','com.alibaba.otter.canal.instance.core.CanalMQConfig'}
        classes=set(spring.get('classes') or [])
        if (path!='/opt/canal.deploy-1.1.6/bin/../conf/canal.properties' or spring.get('BEFORE')!=proc or spring.get('AFTER')!=proc or spring.get('imports')!=['classpath:spring/base-instance.xml'] or not classes or not classes.issubset(allowed_classes) or 'com.alibaba.otter.canal.meta.FileMixedMetaManager' not in classes or not re.fullmatch(r'[0-9a-f]{64}',str(spring.get('HASH') or ''))):
            reason=reason or 'Spring 文件实例配置未确认'
        if ('canal.zkServers' not in f or f.get('canal.zkServers')!='' or f.get('canal.admin.manager') not in (None,'') or f.get('canal.serverMode')!='rabbitMQ' or f.get('canal.instance.global.mode')!='spring' or f.get('canal.instance.global.spring.xml')!='classpath:spring/file-instance.xml'):
            reason=reason or '协调模式、Admin 或本地配置未确认'
        destinations=[x.strip() for x in str(f.get('canal.destinations') or '').split(',') if x.strip()]
        if not destinations or len(destinations)!=len(set(destinations)) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+',x) for x in destinations) or not set(destinations).issubset(set(l.get('confDirectories') or [])):
            reason=reason or 'destination 配置未确认'
        try:
            modified=int(str(l.get('configStat') or '').split()[0]);started=datetime.fromisoformat(r['startedAt'].replace('Z','+00:00')).timestamp()
            spring_modified=int(str(spring.get('STAT') or '').split()[0])
            if modified>started or spring_modified>started:reason=reason or '配置在进程启动后变更'
        except (ValueError,TypeError,KeyError,AttributeError,IndexError):reason=reason or '配置或进程时间未知'
        if r.get('hostDeployments'):reason=reason or '同机 DevOps 应用归属待核实'
        listeners=i.get('listeners') or []
        if not any(re.search(r'127\.0\.0\.1:11110\s',x) and 'pid='+str(r.get('pid'))+',' in x for x in listeners):reason=reason or '回环监听未确认'
        for stamp in (i.get('checkedAt'),l.get('checkedAt'),s.get('checkedAt'),spring.get('checkedAt')):
            try:
                age=datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if age>timedelta(hours=2) or age< -timedelta(minutes=5):reason=reason or '实时核查已过期'
            except (TypeError,ValueError,AttributeError):reason=reason or '核查时间无效'
        if reason:unresolved.append({'ip':r.get('ip'),'pid':r.get('pid'),'reason':reason});continue
        groups.append({'key':{'assetId':r['assetId'],'configPath':path},'row':r,'destinations':destinations})
    return {'groups':sorted(groups,key=lambda x:x['row']['ip']),'unresolved':unresolved}


def import_report(store,report,version,can_apply=lambda:True):
    prepared=plan(report)
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
            if store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE)):
                conn.rollback();return {'unchanged':True,'unresolved':prepared['unresolved']}
            now=db.utcnow();run=db.identifier();observed=db.timestamp(max((x['row']['initial']['checkedAt'] for x in prepared['groups']),default=datetime.now(timezone.utc).isoformat()))
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/canal-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'Canal'}),'completeness_json':db.encode({'complete':False}),'observed_at':observed,'imported_at':now,'status':'WRITING'})
            for item in prepared['groups']:
                key=item['key'];row=item['row'];stamp=db.timestamp(row['initial']['checkedAt'])
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='CANAL_SERVER' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:results.append({'name':found[0]['name'],'ip':row['ip'],'existing':True});continue
                existing=store.execute(conn,"SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,key['assetId']))
                if existing:raise db.CatalogError('资产已有 Canal 节点绑定，身份变化或多实例需复核')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Canal',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Canal' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Canal'")
                name='Canal_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier()
                ev={'sourceVersion':version,'reportPath':'checks/canal-review/evidence/'+digest+'.json','assetId':key['assetId'],'configPath':key['configPath'],'coverage':'本地配置及当前进程观察；历史或未来变化未知','groupingBasis':'LOCAL_NO_ZOOKEEPER_NO_ADMIN'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'Canal','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,locator,attrs):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'CANAL_LOCAL_CONFIG_PROCESS_LISTENER','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'Canal','groupingBasis':'LOCAL_NO_ZOOKEEPER_NO_ADMIN'})
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'unresolvedCount':0});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'CANAL_SERVER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':key['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                endpoints=[]
                for line in row['initial']['listeners']:
                    parts=line.split()
                    if len(parts)<4 or ':' not in parts[3]:continue
                    address,port=parts[3].rsplit(':',1)
                    if port.isdigit():endpoints.append({'ip':address.strip('[]'),'port':int(port),'protocol':'tcp','scope':'HOST'})
                attrs={'portBasis':'OBSERVED_LISTENERS','observedEndpoints':endpoints,'destinations':item['destinations'],'serverMode':'rabbitMQ','groupingBasis':'LOCAL_NO_ZOOKEEPER_NO_ADMIN'}
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'CANAL_SERVER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_CONFIG_PATH','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('INSTANCE',instance),('UNIT',unit)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Canal 本地配置、进程与监听只读核查'})
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
