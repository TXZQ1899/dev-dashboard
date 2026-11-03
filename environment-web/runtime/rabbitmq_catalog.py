"""Preview and import RabbitMQ single-node observations from bounded live checks."""
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

RULE = 'rabbitmq-current-members-v2'
FILES = ('membership-current-133150.json', 'pid-current-133150.json',
         'initial-current-133150.json', 'container-current-133150.json')
CONFIRMATION = 'user-app-scope-133150.json'


def report_from_sources(data):
    data = Path(data); root = data / 'checks' / 'rabbitmq-review'
    read = lambda p: json.loads(p.read_text())
    version = read(data / 'current.json')['id']
    reports = [read(root / name) for name in FILES]
    if any(r.get('sourceVersion') != version for r in reports):
        raise ValueError('核查来源不是当前快照')
    snapshot = read(data / 'versions' / version / 'snapshot.json')
    jump = read(data / 'versions' / version / 'jumpserver-snapshot.json')
    if snapshot.get('complete') is not True or snapshot.get('scope') != 'all':
        raise ValueError('DevOps 快照不完整')
    maps = []
    for report in reports:
        items = report.get('results', [])
        keys = [(r.get('assetId'), r.get('pid')) for r in items]
        if len(keys) != len(set(keys)):
            raise ValueError('核查报告存在重复进程')
        maps.append(dict(zip(keys, items)))
    rows = []
    for asset in jump.get('assets', []):
        ins = asset.get('inspection') or {}; processes = ins.get('processes', [])
        by_pid = {p.get('pid'): p for p in processes}
        for p in processes:
            if not re.search(r'beam.smp.*-s\s+rabbit\s+boot', p.get('command', ''), re.I):
                continue
            key = (asset.get('id'), p.get('pid'))
            member, pidcheck, host, container = (m.get(key) for m in maps)
            row = {'assetId': key[0], 'ip': asset.get('ip'), 'pid': key[1],
                   'inspectionAt': ins.get('checkedAt'), 'processStatus': ins.get('processStatus'),
                   'member': member, 'pidCheck': pidcheck, 'hostCheck': host, 'containerCheck': container,
                   'hostDeployments': []}
            for app in snapshot.get('apps', []):
                for env, deployments in app.get('envs', {}).items():
                    for d in deployments:
                        if ac.ips(d.get('ip')) & ac.ips(asset.get('ip')):
                            # Explicit empty deployment port is not replaced with an app default.
                            port = d.get('port') if 'port' in d else app.get('port')
                            row['hostDeployments'].append({'env': env, 'appId': str(app.get('id', '')),
                                                           'name': app.get('name'), 'appType': app.get('appType'), 'port': port})
            chain = []; cur = p
            while cur and cur.get('pid') not in chain and len(chain) < 30:
                chain.append(cur.get('pid')); cur = by_pid.get(cur.get('ppid'))
            cs = [c for c in (ins.get('docker') or {}).get('containers', []) if c.get('initPid') in chain]
            row['snapshotContainers'] = [{'id': c.get('id'), 'name': c.get('name'), 'initPid': c.get('initPid')} for c in cs]
            rows.append(row)
    confirmation_path = root / CONFIRMATION
    confirmation = read(confirmation_path) if confirmation_path.exists() else None
    if confirmation and confirmation.get('sourceVersion') != version:
        raise ValueError('用户确认与当前快照版本不一致')
    return {'sourceVersion': version, 'ruleVersion': RULE, 'devopsCollectedAt': snapshot.get('collectedAt'),
            'jumpserverCollectedAt': jump.get('collectedAt'), 'reportFiles': list(FILES),
            'userConfirmation': confirmation, 'results': rows}


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion') != RULE:
        raise ValueError('来源或规则版本无效')
    rows = report.get('results', []); counts = Counter((r.get('assetId'), r.get('pid')) for r in rows)
    groups = []; unresolved = []
    for r in rows:
        reason = None; m = r.get('member') or {}; p = r.get('pidCheck') or {}; cs = r.get('snapshotContainers') or []
        hc = r.get('hostCheck') or {}; cc = r.get('containerCheck') or {}
        try:
            ipaddress.ip_address(r.get('ip'))
        except (ValueError, TypeError): reason = '资产地址不唯一或无效'
        if not r.get('assetId') or counts[(r.get('assetId'), r.get('pid'))] != 1 or r.get('processStatus') != 'complete':
            reason = reason or '资产或进程采集不完整'
        node = str(m.get('node') or '').strip("'")
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', node): reason = reason or '节点名无效'
        norm = lambda s: re.sub(r"[\s']", '', str(s or ''))
        if (m.get('evalRc') != '0' or norm(m.get('all')) != '[' + node + ']' or
                norm(m.get('running')) != '[' + node + ']' or m.get('classicNodes') != '{[],disc}'):
            reason = reason or '成员视图或静态节点配置不满足单节点条件'
        if not m.get('before') or m.get('before') != m.get('after') or p.get('HOST') != p.get('HOST_AFTER') or p.get('HOST') != m.get('before'):
            reason = reason or '核查期间进程身份不一致'
        host = re.fullmatch(r'(\d+) \(beam.smp\) (\d+)', str(p.get('HOST') or ''))
        inside = re.fullmatch(r'(\d+) \(beam.smp\) (\d+)', str(p.get('INSIDE') or ''))
        if not host or not inside or int(host[1]) != r.get('pid') or inside[1] != str(m.get('osPid')) or host[2] != inside[2]:
            reason = reason or '管理查询未能对应目标进程'
        if len(cs) > 1: reason = reason or '容器归属不唯一'
        check = cc if cs else hc; listeners = check.get('listeners') or []
        if not any(re.search(r'(?:\*|\[?::\]?):5672\s', s) and ('pid='+str(r.get('pid'))+',') in s for s in listeners):
            reason = reason or '进程 5672 监听未确认'
        port = None; port_basis = 'WILDCARD_LISTENER'
        if cs:
            c = cs[0]; before = cc.get('containerBefore') or []; after = cc.get('containerAfter') or []
            if (not c.get('name') or c.get('id') != m.get('containerId') or c.get('id') != cc.get('containerId') or
                    before != after or len(before) != 1 or before[0][:2] != ['true', str(c.get('initPid'))] or
                    cc.get('processBefore') != [m.get('before')] or cc.get('processAfter') != [m.get('after')]):
                reason = reason or '容器身份或进程树不一致'
            mappings = (m.get('publishedPorts') or {}).get('5672/tcp') or []
            ports = {int(x['HostPort']) for x in mappings if x.get('HostIp') in ('0.0.0.0', r.get('ip')) and str(x.get('HostPort', '')).isdigit()}
            if len(ports) != 1: reason = reason or '容器发布端口不唯一'
            else: port = next(iter(ports)); port_basis = 'DOCKER_PUBLISHED'
        elif hc.get('processBefore') != [m.get('before')] or hc.get('processAfter') != [m.get('after')]:
            reason = reason or '宿主机监听核查的进程身份不一致'
        relevant_ports = {5672, 15672, 25672}
        if cs:
            for entries in (m.get('publishedPorts') or {}).values():
                for x in entries or []:
                    if str(x.get('HostPort', '')).isdigit(): relevant_ports.add(int(x['HostPort']))
        confirmation = report.get('userConfirmation') or {}
        confirmed = {(x.get('assetId'), x.get('env'), x.get('appId'), x.get('appType'))
                     for x in confirmation.get('confirmedApplications', [])} if (
                         confirmation.get('sourceVersion') == report['sourceVersion'] and
                         confirmation.get('kind') == 'USER_CONFIRMED_NOT_MANAGING_RABBITMQ' and
                         confirmation.get('confirmedAt')) else set()
        for app in r.get('hostDeployments', []):
            value = app.get('port')
            if not str(value or '').isdigit():
                if (r['assetId'], app.get('env'), app.get('appId'), app.get('appType')) not in confirmed:
                    reason = reason or '同机 DevOps 应用端口缺失或冲突'
            elif int(value) in relevant_ports:
                reason = reason or '同机 DevOps 应用端口缺失或冲突'
        for stamp in (m.get('observedAt'), p.get('observedAt'), check.get('checkedAt')):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if age > timedelta(hours=2) or age < -timedelta(minutes=5): reason = reason or '实时核查已过期'
            except (AttributeError, TypeError, ValueError): reason = reason or '实时核查时间无效'
        if reason:
            unresolved.append({'ip': r.get('ip'), 'pid': r.get('pid'), 'reason': reason}); continue
        key = {'assetId': r['assetId'], 'nodeName': node, 'containerName': cs[0]['name'] if cs else None}
        groups.append({'key': key, 'row': r, 'node': node, 'port': port, 'portBasis': port_basis})
    return {'groups': sorted(groups, key=lambda x: (x['row']['ip'], x['node'])), 'unresolved': unresolved}


def import_report(store, report, version, can_apply=lambda: True):
    prepared = plan(report)
    if report['sourceVersion'] != version: raise ValueError('当前版本已切换')
    source = store.source_key + ':rabbitmq'; payload = db.encode(report); digest = db.digest(report)
    root = Path(os.environ.get('ENVSCOPE_DATA', '/data')) / 'checks' / 'rabbitmq-review' / 'evidence'
    root.mkdir(parents=True, exist_ok=True); path = root / (digest + '.json')
    if path.exists() and path.read_text() != payload: raise ValueError('证据哈希冲突')
    if not path.exists():
        tmp = path.with_suffix('.tmp'); tmp.write_text(payload); tmp.chmod(0o600); os.replace(tmp, path)
    store.migrate(); results = []
    with store.connection() as conn, store.database_lock(conn, 'component-rabbitmq'):
        conn.begin()
        try:
            if store.execute(conn, 'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, digest, RULE)):
                conn.rollback(); return {'unchanged': True, 'unresolved': prepared['unresolved']}
            now = db.utcnow(); run = db.identifier()
            observed = db.timestamp(max((x['row']['member']['observedAt'] for x in prepared['groups']), default=datetime.now(timezone.utc).isoformat()))
            store.insert(conn, 'catalog_import_run', {'id':run,'source_instance_key':source,'source_version':version,'source_relative_path':'checks/rabbitmq-review/evidence/'+digest+'.json','snapshot_sha256':digest,'mapper_version':RULE,'scope_json':db.encode({'type':'RabbitMQ'}),'completeness_json':db.encode({'complete':False}),'observed_at':observed,'imported_at':now,'status':'WRITING'})
            for item in prepared['groups']:
                key=item['key']; row=item['row']; stamp=db.timestamp(row['member']['observedAt'])
                found=store.execute(conn,"SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='RABBITMQ_NODE' AND s.native_key_hash=%s",(source,db.digest(key)))
                if found:
                    results.append({'name':found[0]['name'],'ip':row['ip'],'existing':True}); continue
                # A node name or container name changed on the same asset is a review conflict.
                existing=store.execute(conn,"SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s",(source,key['assetId']))
                if existing: raise db.CatalogError('资产已有 RabbitMQ 节点绑定，身份变化或多实例需复核')
                store.execute(conn,"INSERT INTO catalog_component_counter (technology,next_number) VALUES ('RabbitMQ',1) ON DUPLICATE KEY UPDATE technology=technology")
                number=store.execute(conn,"SELECT next_number FROM catalog_component_counter WHERE technology='RabbitMQ' FOR UPDATE")[0]['next_number']
                store.execute(conn,"UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='RabbitMQ'")
                name='RabbitMQ_cluster_'+str(number)
                if store.execute(conn,'SELECT id FROM deployment_unit WHERE name=%s',(name,)): raise db.CatalogError('部署单元名称冲突')
                unit,env,instance=db.identifier(),db.identifier(),db.identifier()
                ev={'sourceVersion':version,'reportPath':'checks/rabbitmq-review/evidence/'+digest+'.json','assetId':key['assetId'],'nodeName':item['node'],'coverage':'当前成员视图；历史关系及未来状态未知','groupingBasis':'CURRENT_ALL_MEMBERS_SINGLE_NODE'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'RabbitMQ','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':stamp,'updated_at':now})
                def obs(kind,owner,locator,attrs):
                    oid=db.identifier();store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'RABBITMQ_CURRENT_MEMBER_PID_LISTENER','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)});return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'RabbitMQ','groupingBasis':'CURRENT_ALL_MEMBERS_SINGLE_NODE'})
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'unresolvedCount':0});store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'RABBITMQ_NODE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(ev)})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':str(item['port']) if item['port'] else None,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':key['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                endpoints=[]
                for line in (row.get('containerCheck') or row['hostCheck']).get('listeners',[]):
                    parts=line.split()
                    if len(parts)<4 or ':' not in parts[3]: continue
                    address,port_text=parts[3].rsplit(':',1)
                    if port_text.isdigit(): endpoints.append({'ip':address.strip('[]'),'port':int(port_text),'protocol':'tcp','scope':'CONTAINER' if key['containerName'] else 'HOST'})
                attrs={'nodeName':item['node'],'portBasis':item['portBasis'],'observedEndpoints':endpoints,'containerPort':5672 if key['containerName'] else None,'groupingBasis':'CURRENT_ALL_MEMBERS_SINGLE_NODE'}
                iobs=obs('DEPLOYMENT',{'instance_id':instance},{**key,'record':'instance'},attrs);store.update(conn,'deployment_instance',instance,{'current_observation_id':iobs})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,'object_kind':'RABBITMQ_NODE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'ASSET_NODE_CONTAINER','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                for typ,oid in (('INSTANCE',instance),('UNIT',unit)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':typ,'object_id':oid,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'RabbitMQ 当前成员、进程与监听只读核查'})
                results.append({'name':name,'ip':row['ip'],'node':item['node'],'port':item['port'],'existing':False})
            if not can_apply(): raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in results),'unresolved':len(prepared['unresolved'])})});conn.commit()
        except Exception:
            conn.rollback(); raise
    return {'units':results,'unresolved':prepared['unresolved']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data'));report=report_from_sources(data);prepared=plan(report)
    if not args.apply:
        print(json.dumps({'groups':[{'ip':x['row']['ip'],'node':x['node'],'port':x['port'],'portBasis':x['portBasis']} for x in prepared['groups']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
    result=import_report(db.Catalog.from_environment(),report,report['sourceVersion'],can_apply=lambda:json.loads((data/'current.json').read_text()).get('id')==report['sourceVersion'])
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
