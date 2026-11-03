"""Attach Kibana service instances to evidenced existing Elasticsearch units."""
import argparse
import ipaddress
import json
import os
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path
try: import deployment_catalog as db
except ModuleNotFoundError: from runtime import deployment_catalog as db

VERSION='20261103-113340-937050'
RULE='kibana-es-connection-v1'


def bindings(store,conn):
    rows=store.execute(conn,"SELECT u.id AS unit_id,u.name AS unit_name,e.id AS environment_id,i.primary_ip,s.evidence_json FROM deployment_instance_source s JOIN deployment_instance i ON i.id=s.instance_id JOIN deployment_unit_environment e ON e.id=i.unit_environment_id JOIN deployment_unit u ON u.id=e.unit_id WHERE s.source_instance_key=%s AND s.object_kind='ELASTICSEARCH_NODE' AND s.confidence IN ('EXACT','INFERRED') AND i.presence_status='PRESENT' AND e.environment='UNIDENTIFIED'",(store.source_key+':elasticsearch',))
    result=[]
    for x in rows:
        ev=db.decode(x['evidence_json'],{});result.append({'unitId':x['unit_id'],'unitName':x['unit_name'],'environmentId':x['environment_id'],'ip':x['primary_ip'],'assetId':ev.get('assetId'),'pid':ev.get('pid'),'startTicks':str(ev.get('startTicks')),'nodeId':ev.get('nodeId'),'clusterUuid':ev.get('clusterUuid')})
    return sorted(result,key=lambda r:(r['unitId'],str(r['nodeId'])))


def current_es(binding,es_report,now):
    rows=[x for x in es_report.get('results',[]) if (x.get('assetId'),x.get('ip'),x.get('pid'),x.get('unitName'),x.get('catalogNodeId'))==(binding['assetId'],binding['ip'],binding['pid'],binding['unitName'],binding['nodeId'])]
    if len(rows)!=1:return None
    r=rows[0];out=r.get('output','');pid=binding['pid'];ticks=re.findall(r'(?m)^'+str(pid)+r' \(java\) (\d+)$',out)
    if r.get('status')!='OBSERVED' or r.get('exitCode')!=0 or ticks!=[binding['startTicks'],binding['startTicks']] or str(r.get('catalogStartTicks'))!=binding['startTicks']:return None
    net=re.search(r'(?m)^NETNS (net:\[\d+\])$',out);host=re.search(r'(?m)^HOST_NETNS (net:\[\d+\])$',out)
    if not net or not host or net[1]!=host[1]:return None
    try:age=now-datetime.fromisoformat(r['observedAt'].replace('Z','+00:00'))
    except (KeyError,ValueError):return None
    if not timedelta(minutes=-5)<=age<=timedelta(hours=2):return None
    section=out.split('LISTEN\n',1)
    if len(section)!=2:return None
    endpoints=[]
    for line in section[1].split('AFTER',1)[0].splitlines():
        try: address,port=line.rsplit(':',1);port=int(port);address=address.strip('[]') or '::'
        except (ValueError,AttributeError):continue
        if 0<port<65536:endpoints.append({'ip':address,'port':port,'protocol':'tcp'})
    if not endpoints:return None
    return {**binding,'observedAt':r['observedAt'],'listeners':endpoints,'networkNamespace':net[1]}


def plan(snapshot,devops,live,workers,es_report,es_bindings,version,now=None,config_candidates=None):
    if version!=VERSION or any(x.get('sourceVersion')!=version for x in (live,workers,es_report)) or (config_candidates is not None and config_candidates.get('sourceVersion')!=version):raise ValueError('来源版本不一致')
    if devops.get('complete') is not True or devops.get('scope')!='all':raise ValueError('DevOps 数据范围不完整')
    now=now or datetime.now(timezone.utc);targets=[x for b in es_bindings if (x:=current_es(b,es_report,now))];selected=[];unresolved=[]
    replacements={(r['ip'],r['pid']):r for r in workers.get('results',[])}
    replacements.update({(r['ip'],r['pid']):r for r in (config_candidates or {}).get('results',[])})
    for original in live.get('results',[]):
        row=replacements.get((original.get('ip'),original.get('pid')),original);ip,pid,asset=row.get('ip'),row.get('pid'),row.get('assetId');reason=None;details=row.get('details') or {}
        assets=[a for a in snapshot.get('assets',[]) if (a.get('id'),a.get('ip'))==(asset,ip)]
        ps=[p for p in (assets[0].get('inspection') or {}).get('processes',[]) if p.get('pid')==pid and re.search(r'(?:^|/)kibana(?:/|\s|$)',p.get('command',''),re.I)] if len(assets)==1 else []
        if row.get('status')!='OBSERVED' or len(ps)!=1 or row.get('snapshotProcessStatus')!='complete':reason='进程核查或快照不完整'
        before,after=details.get('before'),details.get('after')
        if not before or before!=after or before.get('comm')!='node' or not details.get('argvReadable') or not details.get('envReadable'):reason=reason or '进程身份不一致或无法读取'
        if details.get('socketStatus')!='OBSERVED' or not details.get('netns') or details.get('netns')!=details.get('hostNetns'):reason=reason or '网络空间或监听不可核实'
        try:
            age=now-datetime.fromisoformat(row['observedAt'].replace('Z','+00:00'))
            if not timedelta(minutes=-5)<=age<=timedelta(hours=2):reason=reason or '实时证据过期'
        except (KeyError,ValueError):reason=reason or '观察时间无效'
        children=details.get('children') or []
        if children:
            post=details.get('childrenAfter') or []
            if {(c['pid'],c['startTicks'],pid) for c in children}!={(c.get('pid'),c.get('startTicks'),c.get('ppid')) for c in post}:reason=reason or 'Kibana 子进程身份不稳定'
        listeners=[x.get('local') for x in details.get('listeners',[]) if x.get('local') and x['local'].get('port')==5601]
        if len(listeners)!=1:reason=reason or 'Kibana 监听端点不唯一'
        apps=[{'id':a.get('id'),'name':a.get('name'),'environment':e,'port':p.get('port')} for a in devops.get('apps',[]) for e,ds in (a.get('envs') or {}).items() for p in (ds or []) if p.get('ip')==ip and (not p.get('port') or str(p['port'])=='5601' or 'kibana' in str(a.get('name','')).lower())]
        if apps:reason=reason or '存在可能的 DevOps 应用关联'
        if details.get('overrideKeysPresent'):reason=reason or '存在额外目标覆盖'
        configs=details.get('configuredEndpoints');ambiguous=False
        if details.get('configBasis')=='AMBIGUOUS_DEFAULTS':
            candidates=details.get('configCandidates') or []
            if len(candidates)>=2 and all(c.get('status')=='READ' and c.get('endpoints') for c in candidates) and all(c['endpoints']==candidates[0]['endpoints'] for c in candidates):
                configs=candidates[0]['endpoints'];ambiguous=True
        if not ambiguous and (details.get('configStatus')!='READ' or not configs or details.get('configBasis') not in ('EXPLICIT_ARG','ENV_KBN_PATH_CONF','DEFAULT_CANDIDATE')):reason=reason or '实际目标配置未唯一确认'
        if not reason and not ambiguous:
            try:
                if datetime.fromisoformat(details['configMtime'].replace('Z','+00:00'))>datetime.fromisoformat(row['snapshotStartedAt'].replace('Z','+00:00')):reason='配置修改晚于进程启动'
            except (KeyError,ValueError,TypeError):reason='配置或进程时间未知'
        def resolve(host,port):
            try:address=ipaddress.ip_address(host.replace('::ffff:',''))
            except ValueError:return []
            found=[]
            for t in targets:
                if address.is_loopback:
                    if t['assetId']!=asset:continue
                elif str(address)!=t['ip']:continue
                for ep in t['listeners']:
                    if ep['port']!=port:continue
                    listen=ep['ip'].replace('::ffff:','')
                    if listen in ('*','::','0.0.0.0',str(address)) or address.is_loopback and listen in ('127.0.0.1','::1'):found.append(t);break
            return found
        nodes=[]
        if not reason:
            for ep in configs:
                host=ep.get('host');port=ep.get('port')
                if host=='localhost':host='127.0.0.1'
                matches=resolve(host,port) if host and isinstance(port,int) else []
                if len(matches)!=1:reason='配置目标无法唯一匹配当前 ES 节点';break
                nodes.append(matches[0])
        actual=[]
        if not reason:
            for c in details.get('connections',[]):
                peer=c.get('peer') or {}
                if peer.get('port') not in {ep['port'] for ep in configs}:continue
                matches=resolve(peer.get('host',''),peer['port'])
                if len(matches)!=1:reason='实际连接目标无法唯一匹配 ES 节点';break
                actual.append({'host':peer['host'],'port':peer['port'],'nodeId':matches[0]['nodeId'],'unitId':matches[0]['unitId']})
            if not actual:reason=reason or '未观察到进程连接 ES 端点'
        if not reason and (len({n['unitId'] for n in nodes})!=1 or any(x['unitId']!=nodes[0]['unitId'] for x in actual)):reason='连接或配置涉及多个 ES 单元'
        if reason:unresolved.append({'ip':ip,'pid':pid,'reason':reason});continue
        target=nodes[0];item={'assetId':asset,'ip':ip,'pid':pid,'startTicks':before['startTicks'],'observedAt':row['observedAt'],'unitId':target['unitId'],'unitName':target['unitName'],'environmentId':target['environmentId'],'listener':listeners[0],'configuredEndpoints':configs,'actualTargets':sorted({(x['host'],x['port'],x['nodeId']) for x in actual}),'esNodes':[{'nodeId':x['nodeId'],'ip':x['ip'],'pid':x['pid'],'startTicks':x['startTicks']} for x in nodes],'configPath':details['configPath'],'configBasis':details['configBasis'],'key':{'assetId':asset,'service':'Kibana','listenPort':5601}}
        selected.append(item)
    return {'items':selected,'unresolved':unresolved}


def apply(store,prepared,report,data,valid,expected_bindings):
    payload,digest=db.encode(report),db.digest(report);root=Path(data)/'checks/kibana-review/evidence';root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('证据哈希冲突')
    if not path.exists():tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    store.migrate();source=store.source_key+':kibana';result=[]
    with store.connection() as conn,store.database_lock(conn,'component-elasticsearch'):
        conn.begin()
        try:
            if not valid() or db.digest(bindings(store,conn))!=db.digest(expected_bindings):raise db.CatalogError('来源或 ES 目录身份发生变化')
            previous=store.execute(conn,'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',(source,digest,RULE))
            if previous:
                for item in prepared['items']:
                    found=store.execute(conn,"SELECT i.unit_environment_id FROM deployment_instance_source s JOIN deployment_instance i ON i.id=s.instance_id WHERE s.source_instance_key=%s AND s.object_kind='KIBANA_SERVICE' AND s.native_key_hash=%s",(source,db.digest(item['key'])))
                    if len(found)!=1 or found[0]['unit_environment_id']!=item['environmentId']:raise db.CatalogError('历史 Kibana 归属不一致')
                    result.append({'ip':item['ip'],'unitName':item['unitName'],'existing':True})
                conn.rollback();return {'items':result,'unresolved':prepared['unresolved'],'unchanged':True,'evidence':str(path)}
            now,run=db.utcnow(),db.identifier();observed=max(db.timestamp(x['observedAt']) for x in prepared['items'])
            store.insert(conn,'catalog_import_run',{'id':run,'source_instance_key':source,'source_version':VERSION,'source_relative_path':'checks/kibana-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'Kibana','relation':'ELASTICSEARCH_CLIENT'}),'completeness_json':db.encode({'complete':False}),'observed_at':observed,'imported_at':now,'status':'WRITING'})
            for item in prepared['items']:
                key=item['key'];found=store.execute(conn,"SELECT i.unit_environment_id FROM deployment_instance_source s JOIN deployment_instance i ON i.id=s.instance_id WHERE s.source_instance_key=%s AND s.object_kind='KIBANA_SERVICE' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    if len(found)!=1 or found[0]['unit_environment_id']!=item['environmentId']:raise db.CatalogError('Kibana 已绑定其他单元，需复核')
                    result.append({'ip':item['ip'],'unitName':item['unitName'],'existing':True});continue
                if store.execute(conn,"SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,item['assetId'])):raise db.CatalogError('该资产已有其他 Kibana 身份，需复核')
                instance=db.identifier();stamp=db.timestamp(item['observedAt']);ev={'sourceVersion':VERSION,'reportPath':'checks/kibana-review/evidence/'+digest+'.json','associationBasis':'PROCESS_TCP_AND_CONFIG_TO_VERIFIED_ES','associationConfidence':'INFERRED','coverage':'当前窗口连接，ES 节点身份与目录核对；不代表完整历史或未来状态'}
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':item['environmentId'],'name':'Kibana','instance_kind':'SERVICE_PROCESS','primary_ip':item['ip'],'declared_port_raw':str(item['listener']['port']) if item['listener']['host']==item['ip'] else None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':item['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                attrs={'componentRole':'KIBANA','relationship':'ELASTICSEARCH_CLIENT','observedEndpoints':[{'ip':item['listener']['host'],'port':item['listener']['port'],'protocol':'tcp'}],'portBasis':'LIVE_LISTENER','configuredEndpoints':item['configuredEndpoints'],'actualTargets':item['actualTargets'],'esNodes':item['esNodes'],'configPath':item['configPath'],'configBasis':item['configBasis'],'pid':item['pid'],'startTicks':item['startTicks'],'associationConfidence':'INFERRED'};loc={**key,'record':'instance'};oid=db.identifier()
                store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':'DEPLOYMENT','instance_id':instance,'source_locator_json':db.encode(loc),'source_locator_hash':db.digest(loc),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'PROCESS_TCP_AND_CONFIG_TO_VERIFIED_ES','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});store.update(conn,'deployment_instance',instance,{'current_observation_id':oid})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':item['environmentId'],'source_instance_key':source,'object_kind':'KIBANA_SERVICE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_AND_SERVICE_LISTENER','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'INSTANCE','object_id':instance,'action':'CREATE','after_json':db.encode({'name':'Kibana','unitName':item['unitName'],'ip':item['ip']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'用户要求将连接到 ES 的 Kibana 归入该 ES 部署单元'})
                result.append({'ip':item['ip'],'unitName':item['unitName'],'existing':False})
            if not valid() or db.digest(bindings(store,conn))!=db.digest(expected_bindings):raise db.CatalogError('来源或 ES 目录身份发生变化')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,));store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdInstances':sum(not x['existing'] for x in result),'unresolved':len(prepared['unresolved'])})});conn.commit();return {'items':result,'unresolved':prepared['unresolved'],'evidence':str(path)}
        except Exception:conn.rollback();raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args();data=Path(os.environ.get('ENVSCOPE_DATA','/data'));read=lambda p:json.loads(p.read_text());version=read(data/'current.json')['id'];f=data/'versions'/version;checks=data/'checks/kibana-review';paths=[f/'jumpserver-snapshot.json',f/'snapshot.json',checks/('live-v2-'+version+'.json'),checks/('live-workers-'+version+'.json'),checks/('es-targets-'+version+'.json'),checks/('live-config-candidates-'+version+'.json')];raw=[p.read_bytes() for p in paths];snapshot,devops,live,workers,es_report,config_candidates=map(json.loads,raw);store=db.Catalog.from_environment()
    with store.connection() as conn:bound=bindings(store,conn)
    prepared=plan(snapshot,devops,live,workers,es_report,bound,version,config_candidates=config_candidates)
    if not args.apply:print(json.dumps(prepared,ensure_ascii=False));return
    if not prepared['items']:raise ValueError('没有可确认的 Kibana 归属')
    report={'ruleVersion':RULE,'sourceVersion':version,'items':prepared['items'],'unresolved':prepared['unresolved'],'live':live,'workers':workers,'configCandidates':config_candidates,'esTargets':es_report,'esBindings':bound,'devopsCollectedAt':devops.get('collectedAt')};valid=lambda:read(data/'current.json').get('id')==version and all(p.read_bytes()==b for p,b in zip(paths,raw))
    print(json.dumps(apply(store,prepared,report,data,valid,bound),ensure_ascii=False))
if __name__=='__main__':main()
