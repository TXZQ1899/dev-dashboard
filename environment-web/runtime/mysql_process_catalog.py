"""Create the user's individually specified MySQL/MariaDB process units."""
import argparse
import json
import os
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path
try: import deployment_catalog as db
except ModuleNotFoundError: from runtime import deployment_catalog as db

VERSION='20261103-113340-937050'
RULE='mysql-process-user-separate-v1'
TARGETS={
 '10.179.1.226':('34d57041-a80c-4736-85e1-91fa3066a819',11277),'10.179.4.47':('ad1c39fc-1ca2-4bd5-8561-9658b9fc0cff',1455),
 '10.179.4.75':('de541b13-7e29-47a6-b590-1a41bec11d95',1215),'10.179.4.76':('1adc25fa-2cd5-4536-80be-657f0b291d77',1230),
 '10.58.1.112':('3f01adcc-0d35-416a-a50b-afd3d5103f64',217066),'10.58.1.216':('4230334f-3eac-4892-80f8-93c870a57721',2059),
 '10.58.1.219':('552b4586-8b63-4678-9759-37054de4b3e5',1028),'10.58.1.221':('318d9e54-6db3-4eb3-aad4-daaa6d829782',1693),
 '10.58.1.54':('6ca8259c-02ff-4308-ba38-5ea949af67f4',1347),'10.58.10.31':('44c3192e-8c1d-45d2-8ddd-2cdb47c4a33c',1215),
 '10.58.10.90':('83af2494-e77b-4c32-b92e-836df7b4ce29',1314),'10.58.6.135':('f35ff49e-f6e4-45d2-96c0-5b64d52e279c',1240),
 '10.58.9.217':('3f892a2a-114d-44b7-861d-6ce5322699bd',2179),'10.58.9.68':('911285b8-ccdf-41f1-9b94-a0087b92f9e9',1238),
 '10.58.9.72':('0a7f58ee-ab5e-487a-8d75-8e1f2c05a104',2070),'10.58.9.81':('8e60a67b-1c46-4d78-8b5f-21466f263766',1060),
 '10.58.9.90':('0584c0cd-6a17-4e01-ac42-ea453ae60a98',2223),
}


def snapshot_scope(snapshot,devops):
    if devops.get('complete') is not True or devops.get('scope')!='all':raise ValueError('DevOps 范围不完整')
    rows=[]
    for ip,(asset,pid) in sorted(TARGETS.items()):
        assets=[a for a in snapshot.get('assets',[]) if a.get('id')==asset and a.get('ip')==ip]
        if len(assets)!=1:raise ValueError('资产不唯一')
        ins=assets[0].get('inspection') or {};processes=[p for p in ins.get('processes',[]) if p.get('pid')==pid and p.get('name')=='mysqld']
        if len(processes)!=1:raise ValueError('快照进程不唯一')
        apps=[{'appId':a.get('id'),'appName':a.get('name'),'appType':a.get('appType'),'environment':env,'port':p.get('port'),'deploy':p.get('deploy')} for a in devops.get('apps',[]) for env,deployments in (a.get('envs') or {}).items() for p in (deployments or []) if p.get('ip')==ip]
        rows.append({'ip':ip,'assetId':asset,'pid':pid,'snapshotStartedAt':processes[0].get('startedAt'),'processStatus':ins.get('processStatus'),'checkedAt':ins.get('checkedAt'),'hostDeployments':apps})
    return rows


def plan(snapshot,devops,live,special,confirmation,version,now=None):
    if version!=VERSION or any(x.get('sourceVersion')!=version for x in (live,special,confirmation)):raise ValueError('来源版本不一致')
    scope=snapshot_scope(snapshot,devops)
    if confirmation.get('answer')!='17 个主机进程，也按独立部署单元来建立' or confirmation.get('scopeDigest')!=db.digest(scope) or not confirmation.get('confirmedAt'):raise ValueError('用户确认范围不一致')
    now=now or datetime.now(timezone.utc);items=[]
    for row in scope:
        ip,asset,pid=row['ip'],row['assetId'],row['pid'];report=special if ip in ('10.58.1.54','10.58.1.112') else live
        found=[r for r in report.get('results',[]) if (r.get('ip'),r.get('assetId'),r.get('pid'))==(ip,asset,pid)]
        if len(found)!=1 or found[0].get('status')!='OBSERVED' or found[0].get('exitCode')!=0:raise ValueError('进程实时观察缺失')
        r=found[0];out=r.get('output','');ticks=re.findall(r'(?m)^'+str(pid)+r' \(mysqld\) (\d+)$',out)
        if len(ticks)!=2 or ticks[0]!=ticks[1]:raise ValueError('进程身份变化')
        checked=datetime.fromisoformat(r['checkedAt'].replace('Z','+00:00'));age=now-checked
        if not timedelta(minutes=-5)<=age<=timedelta(hours=2):raise ValueError('核查证据过期')
        technology='MariaDB' if ip=='10.58.1.54' else 'MySQL';container=None;endpoints=None;identity_key={'assetId':asset}
        if technology=='MariaDB':
            if '5.5.50-MariaDB' not in out or 'mariadb-server-5.5.50' not in out or '/system.slice/mariadb.service' not in out or '--datadir=/var/lib/mysql' not in out:raise ValueError('MariaDB 身份未确认')
            identity_key['dataDir']='/var/lib/mysql';port_basis='UNKNOWN';version_text='5.5.50-MariaDB'
        elif ip=='10.58.1.112':
            cid='20320a4a6ae8d9684c98d5b0765e87acc1db152995ae1e757d195c0ad8ff300b';pod=re.search(r'(?m)^'+cid+r' xxl-mysql true 217066 (\{.*\}) (\S+)$',out)
            if not pod or 'libpod-'+cid not in out or 'mysql:8.0' not in pod[2] or 'MySQL Community Server' not in out or not re.search(r'\*:3306\s+.*pid=217066,',out):raise ValueError('Podman 身份或容器监听不明')
            net=re.search(r'(?m)^NETNS (net:\[\d+\])$',out);host=re.search(r'(?m)^HOST_NETNS (net:\[\d+\])$',out)
            if not net or not host or net[1]==host[1]:raise ValueError('容器网络空间不明确')
            container={'runtime':'PODMAN','id':cid,'name':'xxl-mysql','publishedPorts':json.loads(pod[1]),'image':pod[2]}
            identity_key['containerName']='xxl-mysql';endpoints=[{'ip':'*','port':3306,'protocol':'tcp','scope':'CONTAINER'},{'ip':'*','port':33060,'protocol':'tcp','scope':'CONTAINER'}];port_basis='CONTAINER_LISTENER';version_text='8.0.46'
            if not re.search(r'\*:33060\s+.*pid=217066,',out):raise ValueError('容器附加监听不明确')
        else:
            if row['processStatus']!='complete' or 'MySQL ' not in out:raise ValueError('MySQL 类型或完整进程观察缺失')
            net=re.search(r'(?m)^NETNS (net:\[\d+\])$',out);host=re.search(r'(?m)^HOST_NETNS (net:\[\d+\])$',out)
            if not net or not host or net[1]!=host[1]:raise ValueError('主机网络空间不明确')
            endpoints=[]
            for line in out.splitlines():
                if not line.startswith('LISTEN ') or 'pid='+str(pid)+',' not in line:continue
                pieces=line.split(); address=pieces[3];name,port=address.rsplit(':',1);name=name.strip('[]') or '::'
                if not port.isdigit() or not 0<int(port)<65536:raise ValueError('监听端口无效')
                endpoints.append({'ip':name,'port':int(port),'protocol':'tcp','scope':'HOST'})
            if not any(x['port']==3306 for x in endpoints):raise ValueError('主机数据库监听缺失')
            identity_key['observedServicePort']=3306;port_basis='LIVE_LISTENER';version_text=re.search(r'(?m)^VERSION (.+)$',out)[1]
        items.append({**row,'key':identity_key,'technology':technology,'version':version_text,'observedAt':r['checkedAt'],'startTicks':ticks[0],'observedEndpoints':endpoints,'portBasis':port_basis,'container':container,'replicationState':'UNKNOWN','applicationAssociationState':'UNKNOWN'})
    return items


def apply(store,items,report,data,valid):
    payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/mysql-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
    if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();source=store.source_key+':mysql-process';results=[]
    with store.connection() as conn,store.database_lock(conn,'component-mysql'):
        conn.begin()
        try:
            if not valid():raise db.CatalogError('核查来源已经变化')
            previous=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE))
            if previous:
                for item in items:
                    found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MYSQL_PROCESS' AND s.native_key_hash=%s",(source,db.digest(item['key'])))
                    if len(found)!=1:raise db.CatalogError('历史导入身份不一致')
                    results.append({'name':found[0]['name'],'ip':item['ip'],'existing':True})
                conn.rollback();return {'units':results,'evidence':str(path),'unchanged':True}
            now,run=db.utcnow(),db.identifier();stamp=max(db.timestamp(i['observedAt']) for i in items)
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':VERSION,'source_relative_path':'checks/mysql-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'types':['MySQL','MariaDB']}),'completeness_json':db.encode({'complete':False}),'observed_at':stamp,'imported_at':now,'status':'WRITING'})
            for item in items:
                key=item['key'];found=store.execute(conn,"SELECT u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='MYSQL_PROCESS' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    if len(found)!=1:raise db.CatalogError('已有来源不唯一')
                    results.append({'name':found[0]['name'],'ip':item['ip'],'existing':True});continue
                if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,item['assetId'])):raise db.CatalogError('该资产已有数据库来源，须复核')
                tech=item['technology'];store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES (%s,1) ON DUPLICATE KEY UPDATE technology=technology",(tech,))
                n=store.execute(conn,'SELECT next_number FROM catalog_component_counter WHERE technology=%s FOR UPDATE',(tech,))[0]['next_number'];store.execute(conn,'UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology=%s',(tech,));name=tech+'_cluster_'+str(n)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)):raise db.CatalogError('单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier();stamp=db.timestamp(item['observedAt']);ev={'sourceVersion':VERSION,'reportPath':'checks/mysql-review/evidence/'+digest+'.json','groupingBasis':'USER_CONFIRMED_PER_PROCESS','groupingConfidence':'INFERRED','coverage':'用户指定逐进程建档；复制关系与应用管理关系未知，不能推断无其他成员'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'DATABASE','technology_code':tech,'creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,attrs):
                    oid=db.identifier();loc={**key,'record':kind};store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'USER_CONFIRMED_PER_PROCESS','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                u=obs('COMPONENT',{'unit_id':unit},{'technology':tech,'groupingConfidence':'INFERRED'});e=obs('ENVIRONMENT',{'unit_environment_id':env},{'coverage':'PARTIAL'});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':e})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'MYSQL_PROCESS','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':u,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':item['ip'],'declared_port_raw':None,'transport_protocol':None,'host_reference_json':db.encode({'assetId':item['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                attrs={k:item[k] for k in ('pid','startTicks','version','observedEndpoints','portBasis','container','hostDeployments','replicationState','applicationAssociationState','processStatus')}
                if item['portBasis']=='CONTAINER_LISTENER':
                    attrs['containerObservedEndpoints']=attrs['observedEndpoints'];attrs['observedEndpoints']=None
                i=obs('DEPLOYMENT',{'instance_id':instance},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':i})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'MYSQL_PROCESS_INSTANCE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_AND_OBSERVED_SERVICE_IDENTITY','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('UNIT',unit),('INSTANCE',instance)):store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'key':key}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户明确指定数据库进程分别建档'})
                results.append({'name':name,'ip':item['ip'],'existing':False})
            if not valid():raise db.CatalogError('核查来源已经变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results)})});conn.commit();return {'units':results,'evidence':str(path)}
        except Exception:conn.rollback();raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda p:json.loads(p.read_text());version=read(data/'current.json')['id'];folder=data/'versions'/version;checks=data/'checks/mysql-review'
    paths=[folder/'jumpserver-snapshot.json',folder/'snapshot.json',checks/('live-identity-'+version+'.json'),checks/('live-special-v3-'+version+'.json'),checks/('user-process-confirmation-'+version+'.json')];raw=[p.read_bytes() for p in paths];snapshot,devops,live,special,confirmation=map(json.loads,raw);items=plan(snapshot,devops,live,special,confirmation,version)
    if not args.apply:print(json.dumps({'count':len(items),'items':items},ensure_ascii=False));return
    report={'ruleVersion':RULE,'sourceVersion':version,'items':items,'live':live,'special':special,'confirmation':confirmation,'devopsCollectedAt':devops.get('collectedAt'),'jumpserverCollectedAt':snapshot.get('collectedAt')};valid=lambda:read(data/'current.json').get('id')==version and all(p.read_bytes()==b for p,b in zip(paths,raw))
    print(json.dumps(apply(db.Catalog.from_environment(),items,report,data,valid),ensure_ascii=False))
if __name__=='__main__':main()
