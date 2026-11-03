"""Import user-confirmed separate Jenkins controllers from scoped evidence."""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db

VERSION='20261103-113340-937050'
RULE='jenkins-user-separate-v1'
TARGETS={
    '10.179.1.226':('34d57041-a80c-4736-85e1-91fa3066a819',8033,'/var/lib/jenkins','/usr/lib/jenkins/jenkins.war'),
    '10.58.10.31':('44c3192e-8c1d-45d2-8ddd-2cdb47c4a33c',903,'/var/jenkins_home','/usr/share/jenkins/jenkins.war'),
    '10.58.9.66':('bb24c69a-b988-4bd1-a9db-fd1790b9184d',998,'/var/lib/jenkins','/usr/share/java/jenkins.war'),
}


def plan(snapshot,devops,live,confirmation,version,now=None):
    if version!=VERSION or live.get('sourceVersion')!=version or confirmation.get('sourceVersion')!=version:raise ValueError('来源版本不一致')
    if confirmation.get('answer')!='是，分别建档' or set(confirmation.get('ips') or [])!=set(TARGETS) or not confirmation.get('confirmedAt'):raise ValueError('缺少用户归组确认')
    if devops.get('complete') is not True or devops.get('scope')!='all':raise ValueError('DevOps 快照范围不完整')
    now=now or datetime.now(timezone.utc);items=[]
    for ip,(asset,pid,home,war) in TARGETS.items():
        assets=[a for a in snapshot.get('assets',[]) if a.get('id')==asset and a.get('ip')==ip]
        if len(assets)!=1:raise ValueError('资产身份不唯一')
        ins=assets[0].get('inspection') or {};ps=[p for p in ins.get('processes',[]) if p.get('pid')==pid and p.get('name')=='java' and war in p.get('command','')]
        if ins.get('processStatus')!='complete' or len(ps)!=1:raise ValueError('快照进程未确认')
        if any(dep.get('ip')==ip for app in devops.get('apps',[]) for deps in (app.get('envs') or {}).values() for dep in (deps or [])):raise ValueError('同机存在 DevOps 应用，需复核')
        checks=[r for r in live.get('results',[]) if (r.get('assetId'),r.get('ip'),r.get('pid'))==(asset,ip,pid)]
        if len(checks)!=1 or checks[0].get('status')!='OBSERVED' or checks[0].get('exitCode')!=0:raise ValueError('实时进程未核实')
        row=checks[0]; out=row.get('output',''); stamps=re.findall(r'(?m)^'+str(pid)+r' \(java\) (\d+)$',out)
        if len(stamps)!=2 or stamps[0]!=stamps[1] or war not in out or not re.search(r'(?m)^(?:-D)?JENKINS_HOME='+re.escape(home)+r'$',out):raise ValueError('进程或 Jenkins home 不一致')
        age=now-datetime.fromisoformat(row['checkedAt'].replace('Z','+00:00'))
        if not timedelta(minutes=-5)<=age<=timedelta(hours=2):raise ValueError('实时核查过期')
        host_net=re.search(r'(?m)^HOST_NETNS (net:\[\d+\])$',out);net=re.search(r'(?m)^NETNS (net:\[\d+\])$',out)
        if not host_net or not net:raise ValueError('网络空间未知')
        container=None
        if ip=='10.58.10.31':
            matches=[c for c in (ins.get('docker') or {}).get('containers',[]) if c.get('name')=='devjekins2893']
            docker=re.search(r'(?m)^([0-9a-f]{64}) true (\d+) (\d+) (\{.*\})$',out)
            expected={'50000/tcp':[{'HostIp':'0.0.0.0','HostPort':'50000'}], '8080/tcp':[{'HostIp':'0.0.0.0','HostPort':'49001'}]}
            if len(matches)!=1 or not docker or matches[0].get('id')!=docker[1] or matches[0].get('initPid')!=int(docker[2]) or ps[0].get('ppid')!=int(docker[2]) or json.loads(docker[4])!=expected or net[1]==host_net[1]:raise ValueError('容器身份或发布端口未确认')
            if not re.search(r'\*:8080\s+.*pid=903,',out) or not re.search(r'\*:50000\s+.*pid=903,',out):raise ValueError('容器监听未确认')
            container={'id':docker[1],'name':'devjekins2893','initPid':int(docker[2]),'restartCount':int(docker[3])}
            endpoints=[{'ip':'*','port':8080,'protocol':'tcp','scope':'CONTAINER'},{'ip':'*','port':50000,'protocol':'tcp','scope':'CONTAINER'},{'ip':'0.0.0.0','port':49001,'protocol':'tcp','scope':'HOST_PUBLISHED'},{'ip':'0.0.0.0','port':50000,'protocol':'tcp','scope':'HOST_PUBLISHED'}]
            declared='49001';basis='DOCKER_PUBLISHED'
        else:
            address='*' if ip=='10.179.1.226' else '::'
            if net[1]!=host_net[1] or not re.search(re.escape('*:8080' if address=='*' else '[::]:8080')+r'\s+.*pid='+str(pid)+',',out):raise ValueError('主机监听未确认')
            endpoints=[{'ip':address,'port':8080,'protocol':'tcp','scope':'HOST'}]
            if ip=='10.179.1.226' and re.search(r'\*:42581\s+.*pid=8033,',out):endpoints.append({'ip':'*','port':42581,'protocol':'tcp','scope':'HOST'})
            declared=None;basis='LIVE_LISTENER'
        key={'assetId':asset,'jenkinsHome':home}
        if container:key['containerName']=container['name']
        items.append({'ip':ip,'assetId':asset,'pid':pid,'home':home,'war':war,'startTicks':stamps[0],'observedAt':row['checkedAt'],'key':key,'endpoints':endpoints,'declaredPort':declared,'portBasis':basis,'container':container})
    return items


def apply(store,items,report,data,valid):
    payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/jenkins-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
    if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();source=store.source_key+':jenkins';results=[]
    with store.connection() as conn,store.database_lock(conn,'component-jenkins'):
        conn.begin()
        try:
            if not valid():raise db.CatalogError('核查来源已经变化')
            previous=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE))
            if previous:
                for item in items:
                    found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='JENKINS_CONTROLLER' AND s.native_key_hash=%s",(source,db.digest(item['key'])))
                    if len(found)!=1:raise db.CatalogError('历史导入身份不一致')
                    results.append({'name':found[0]['name'],'ip':item['ip'],'existing':True})
                conn.rollback();return {'units':results,'unchanged':True,'evidence':str(path)}
            now,run=db.utcnow(),db.identifier()
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':VERSION,'source_relative_path':'checks/jenkins-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'Jenkins'}),'completeness_json':db.encode({'complete':False}),'observed_at':db.timestamp(max(x['observedAt'] for x in items)),'imported_at':now,'status':'WRITING'})
            for item in items:
                key=item['key'];found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='JENKINS_CONTROLLER' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    if len(found)!=1:raise db.CatalogError('已有身份不唯一')
                    results.append({'name':found[0]['name'],'ip':item['ip'],'existing':True});continue
                if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,item['assetId'])):raise db.CatalogError('同资产已存在 Jenkins 身份，需复核')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Jenkins',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='Jenkins' FOR UPDATE")[0]['next_number'];store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Jenkins'")
                name='Jenkins_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier();stamp=db.timestamp(item['observedAt'])
                ev={'sourceVersion':VERSION,'reportPath':'checks/jenkins-review/evidence/'+digest+'.json','groupingBasis':'USER_CONFIRMED_SEPARATE','groupingConfidence':'INFERRED','coverage':'用户确认独立编组；未核实全部 agent、历史或未来状态'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'OTHER','technology_code':'Jenkins','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,attrs):
                    oid=db.identifier();loc={**key,'record':kind};store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_SEPARATE','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                u=obs('COMPONENT',{'unit_id':unit},{'technology':'Jenkins','groupingConfidence':'INFERRED'});e=obs('ENVIRONMENT',{'unit_environment_id':env},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':e})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'JENKINS_CONTROLLER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':u,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':item['ip'],'declared_port_raw':item['declaredPort'],'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':item['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                attrs={'jenkinsHome':item['home'],'war':item['war'],'pid':item['pid'],'startTicks':item['startTicks'],'container':item['container'],'observedEndpoints':item['endpoints'],'portBasis':item['portBasis'],'agentMembership':'UNKNOWN'}
                i=obs('DEPLOYMENT',{'instance_id':instance},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':i})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'JENKINS_CONTROLLER_INSTANCE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_HOME_CONTAINER','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('UNIT',unit),('INSTANCE',instance)):store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'key':key}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户确认 Jenkins controller 分别建档'})
                results.append({'name':name,'ip':item['ip'],'existing':False})
            if not valid():raise db.CatalogError('核查来源已经变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results)})});conn.commit();return {'units':results,'evidence':str(path)}
        except Exception:conn.rollback();raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));version=json.loads((data/'current.json').read_text())['id'];folder=data/'versions'/version;checks=data/'checks/jenkins-review'
    paths=[folder/'jumpserver-snapshot.json',folder/'snapshot.json',checks/('live-process-'+version+'.json'),checks/('user-confirmation-'+version+'.json')];raw=[p.read_bytes() for p in paths];snap,devops,live,confirmation=map(json.loads,raw);items=plan(snap,devops,live,confirmation,version)
    if not args.apply:print(json.dumps(items,ensure_ascii=False,indent=2));return
    report={'ruleVersion':RULE,'sourceVersion':version,'items':items,'live':live,'confirmation':confirmation,'devopsCollectedAt':devops.get('collectedAt'),'jumpserverCollectedAt':snap.get('collectedAt')}
    valid=lambda:json.loads((data/'current.json').read_text()).get('id')==version and all(p.read_bytes()==b for p,b in zip(paths,raw))
    print(json.dumps(apply(db.Catalog.from_environment(),items,report,data,valid),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
