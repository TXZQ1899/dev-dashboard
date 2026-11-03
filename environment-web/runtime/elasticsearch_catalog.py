"""Import Elasticsearch clusters supported by consistent live member views."""
import ipaddress
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def sampled_single_node(row, transport_port, source_version, known_node_ips):
    """Apply the user's current-observation policy to independently sampled sockets."""
    evidence = row.get('trafficEvidence') or {}
    socket_check = evidence.get('socketCheck') or {}
    traffic = evidence.get('applicationCheck') or {}
    target = traffic.get('target') or {}
    frames = socket_check.get('frames') or []
    listeners = socket_check.get('listeners') or []
    coverage = traffic.get('coverage') or {}
    if (socket_check.get('sourceVersion') != source_version or traffic.get('sourceVersion') != source_version or
            socket_check.get('status') != 'OBSERVED' or socket_check.get('pid') != row.get('pid') or
            socket_check.get('assetId') != row.get('assetId') or socket_check.get('startTicks') != row.get('startTicks') or
            socket_check.get('intervalSeconds') != 5 or len(frames) != 6 or len(listeners) != 6 or
            target.get('pid') != row.get('pid') or target.get('assetId') != row.get('assetId') or
            target.get('startTicks') != row.get('startTicks') or traffic.get('status') in ('failed', 'cancelled') or
            coverage.get('actualSeconds', 0) < 25 or coverage.get('samples', 0) < 2):
        return False
    for stamp in (socket_check.get('startedAt'), socket_check.get('completedAt'), traffic.get('startedAt'), traffic.get('finishedAt'), row.get('observedAt')):
        try:
            timestamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            age = datetime.now(timezone.utc) - timestamp
            if age > timedelta(hours=2) or age < -timedelta(minutes=5):
                return False
        except (AttributeError, TypeError, ValueError):
            return False
    for observed in listeners:
        ports = {x.get('port') for x in observed if x.get('protocol') == 'tcp'}
        if row['httpPort'] not in ports or transport_port not in ports:
            return False
    flows = traffic.get('upstream', []) + traffic.get('downstream', [])
    for flow in flows:
        if any((owner.get('service') or {}).get('name') == 'Elasticsearch' for owner in flow.get('remoteProcesses', [])):
            return False
    socket_start = datetime.fromisoformat(socket_check['startedAt'].replace('Z', '+00:00'))
    socket_end = datetime.fromisoformat(socket_check['completedAt'].replace('Z', '+00:00'))
    for frame in frames:
        for conn in frame:
            local, peer = conn.get('local') or {}, conn.get('peer') or {}
            if local.get('protocol') != 'tcp' or peer.get('protocol') != 'tcp':
                return False
            if local.get('port') == transport_port or peer.get('port') == transport_port:
                return False
            if peer.get('ip') in known_node_ips and peer.get('ip') != row.get('ip'):
                return False
            if local.get('port') == row['httpPort']:
                continue
            matches = [flow for flow in flows if flow.get('local') == local and flow.get('peer') == peer]
            if len(matches) != 1:
                return False
            flow = matches[0]; owners = flow.get('remoteProcesses') or []
            if flow.get('resolution') != 'EXACT' or not flow.get('socketId') or len(owners) != 1:
                return False
            owner = owners[0]
            if owner.get('confidence') != 'EXACT' or (owner.get('service') or {}).get('name') in ('Elasticsearch', '应用/未知服务', None):
                return False
            try:
                parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
                first, last = parse(flow['firstSeenAt']), parse(flow['lastSeenAt'])
                if not (first <= socket_start <= socket_end <= last and
                        first <= parse(owner['observationStart']) <= parse(owner['observationEnd']) <= last):
                    return False
            except (KeyError, AttributeError, TypeError, ValueError):
                return False
    return True


def plan(report):
    if not report.get('sourceVersion'):
        raise ValueError('缺少来源版本')
    rows = report.get('results', [])
    counts = Counter((r.get('assetId'), r.get('pid')) for r in rows)
    clusters = defaultdict(list)
    for r in rows:
        root = r.get('root') or {}; local = (r.get('local') or {}).get('nodes') or {}
        view = r.get('view') or {}; nodes = view.get('nodes') or {}; stats = view.get('_nodes') or {}
        ip = r.get('ip'); port = r.get('httpPort'); reason = None
        try:
            ipaddress.ip_address(ip)
        except (ValueError, TypeError):
            reason = '资产地址无效'
        warnings = r.get('warnings') or []
        independently_checked = (report.get('singleNodePolicy') == 'USER_CURRENT_TCP_NO_OTHER_ES_V1' and
            all(w == '部分目标文件描述符未能读取，仅关联前后均已核验的 socket' for w in warnings))
        if (r.get('status') != 'OBSERVED' or not r.get('hostNetwork') or not r.get('startTicks') or
                not r.get('observedAt') or not r.get('assetId') or counts[(r.get('assetId'), r.get('pid'))] != 1 or
                (warnings and not independently_checked)):
            reason = reason or '进程、网络或采样证据不完整'
        apps = r.get('applicationCandidates') or {}
        if set(apps) != {'TEST', 'SIMULATION', 'PRODUCT'} or any(apps.values()):
            reason = reason or '应用关联未排除'
        if not re.fullmatch(r'[A-Za-z0-9_-]{22}', str(root.get('cluster_uuid') or '')) or len(local) != 1:
            reason = reason or '集群或节点身份未确认'
        if (not nodes or stats.get('failed') != 0 or stats.get('total') != len(nodes) or
                stats.get('successful') != len(nodes)):
            reason = reason or '成员查询不完整'
        if report.get('singleNodePolicy') == 'USER_CURRENT_TCP_NO_OTHER_ES_V1' and len(nodes) != 1:
            reason = reason or '当前采样规则仅处理单节点视图'
        if root.get('cluster_name') != view.get('cluster_name') or root.get('cluster_name') != (r.get('local') or {}).get('cluster_name'):
            reason = reason or '集群视图不一致'
        if len(local) == 1:
            nodeid, node = next(iter(local.items()))
            if nodeid not in nodes or nodes[nodeid] != node or root.get('name') != node.get('name'):
                reason = reason or '本地节点视图不一致'
            http_address = node.get('http', {}).get('publish_address')
            if http_address not in (f'{ip}:{port}', f'127.0.0.1:{port}'):
                reason = reason or 'HTTP 发布地址与进程监听未能对应'
            transport = node.get('transport_address', '')
            match = re.fullmatch(r'(?:'+re.escape(str(ip))+r'|127\.0\.0\.1):(\d+)', transport)
            listeners = r.get('listeners') or []
            ports = {x.get('port') for x in listeners if x.get('protocol') == 'tcp' and x.get('ip') in (ip, '*', '::', '0.0.0.0', '127.0.0.1')}
            if not match or int(match[1]) not in ports or port not in ports:
                reason = reason or 'HTTP 或 transport 监听归属未确认'
        else:
            nodeid = None
        if reason:
            continue
        clusters[root['cluster_uuid']].append({'row': r, 'nodeId': nodeid})
    groups = []
    for uuid, members in sorted(clusters.items()):
        first = members[0]['row']['view']['nodes']; expected = set(first)
        # A one-node view alone does not establish an independent deployment.
        if len(expected) == 1:
            member = members[0]
            settings = ((member['row'].get('settings') or {}).get('nodes') or {}).get(member['nodeId']) or {}
            discovery = (settings.get('settings') or {}).get('discovery') or {}
            row = member['row']
            transport_port = int(next(iter(first.values()))['transport_address'].rsplit(':', 1)[1])
            known_ips = report.get('knownNodeIps') or []
            sampled = (report.get('singleNodePolicy') == 'USER_CURRENT_TCP_NO_OTHER_ES_V1' and row['ip'] in known_ips and
                sampled_single_node(row, transport_port, report['sourceVersion'], set(known_ips)))
            if len(members) != 1 or (discovery.get('type') != 'single-node' and not sampled):
                continue
        if len(members) != len(expected) or {x['nodeId'] for x in members} != expected:
            continue
        if any(x['row']['view']['nodes'] != first for x in members):
            continue
        if len({(x['row']['assetId'], x['row']['httpPort']) for x in members}) != len(members):
            continue
        groups.append({'clusterUuid': uuid, 'members': sorted(members, key=lambda x: (x['row']['ip'], x['row']['httpPort']))})
    used = {(x['row']['assetId'], x['row']['pid']) for g in groups for x in g['members']}
    unresolved = [{'ip': r.get('ip'), 'pid': r.get('pid'), 'reason': r.get('reason') or '集群成员、独立模式、监听或应用关联证据不足'} for r in rows if (r.get('assetId'), r.get('pid')) not in used]
    return {'groups': groups, 'unresolved': unresolved}


def import_report(store, report, version, can_apply=lambda: True, report_name='latest.json'):
    prepared = plan(report)
    if report_name not in ('latest.json', 'auth-extra.json', 'current-tcp.json'):
        raise ValueError('不支持的核查报告')
    if report['sourceVersion'] != version:
        raise ValueError('当前版本与核查来源不一致')
    store.migrate(); source = store.source_key + ':elasticsearch'; payload = db.encode(report); digest = db.digest(payload); result = []
    with store.connection() as conn, store.database_lock(conn, 'component-elasticsearch'):
        conn.begin()
        try:
            if store.execute(conn, 'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, digest, 'elasticsearch-review-v1')):
                conn.rollback(); return {'unchanged': True, 'unresolved': prepared['unresolved']}
            now = db.utcnow(); run = db.identifier()
            store.insert(conn, 'catalog_import_run', {'id': run, 'source_instance_key': source, 'source_version': version, 'source_relative_path': 'checks/elasticsearch-review/'+report_name, 'snapshot_sha256': digest, 'mapper_version': 'elasticsearch-review-v1', 'scope_json': db.encode({'type': 'Elasticsearch'}), 'completeness_json': db.encode({'complete': False}), 'observed_at': db.timestamp(report['completedAt']), 'imported_at': now, 'status': 'WRITING'})
            for group in prepared['groups']:
                key = {'clusterUuid': group['clusterUuid']}; members = group['members']
                found = store.execute(conn, "SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='ELASTICSEARCH_CLUSTER' AND s.native_key_hash=%s", (source, db.digest(key)))
                if found:
                    # Membership changes require review; never silently update an existing unit.
                    existing = store.execute(conn, "SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND unit_environment_id IN (SELECT id FROM deployment_unit_environment WHERE unit_id=%s)", (source, found[0]['unit_id']))
                    if {db.decode(x['native_key_json']).get('nodeId') for x in existing} != {x['nodeId'] for x in members}:
                        raise db.CatalogError('Elasticsearch 已有单元成员变化，需人工复核')
                    result.append({'name': found[0]['name'], 'existing': True}); continue
                for member in members:
                    nodekey = {'nodeId': member['nodeId']}
                    if store.execute(conn, 'SELECT id FROM deployment_instance_source WHERE source_instance_key=%s AND native_key_hash=%s', (source, db.digest(nodekey))):
                        raise db.CatalogError('Elasticsearch 节点已绑定其他单元')
                store.execute(conn, "INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Elasticsearch',1) ON DUPLICATE KEY UPDATE technology=technology")
                number = store.execute(conn, "SELECT next_number FROM catalog_component_counter WHERE technology='Elasticsearch' FOR UPDATE")[0]['next_number']
                store.execute(conn, "UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Elasticsearch'")
                name = 'Elasticsearch_cluster_'+str(number)
                if store.execute(conn, 'SELECT id FROM deployment_unit WHERE name=%s', (name,)): raise db.CatalogError('部署单元名称冲突')
                unit, env = db.identifier(), db.identifier(); observed = db.timestamp(members[0]['row']['observedAt'])
                evidence = {'sourceVersion': version, 'clusterUuid': group['clusterUuid'], 'reportPath': 'checks/elasticsearch-review/evidence/'+digest+'.json', 'coverage': '实时节点视图，不代表历史或未来成员', 'singleNodePolicy': report.get('singleNodePolicy'), 'userInstruction': report.get('userInstruction'), 'policyRecordedAt': report.get('policyRecordedAt')}
                store.insert(conn, 'deployment_unit', {'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'Elasticsearch','creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn, 'deployment_unit_environment', {'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS','coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                def obs(kind, owner, locator, attrs, ev, stamp):
                    oid = db.identifier(); store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run,'record_kind':kind,**owner,'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':stamp,'time_basis':'LIVE_CHECK','source_presence':'PRESENT','confidence':'INFERRED','matching_basis':'ELASTICSEARCH_CROSS_NODE_VIEW','source_attributes_json':db.encode(attrs),'evidence_json':db.encode(ev),'semantic_hash':db.digest(attrs)}); return oid
                uobs=obs('COMPONENT',{'unit_id':unit},{**key,'record':'unit'},{'technology':'Elasticsearch','groupingBasis':report.get('singleNodePolicy')},evidence,observed)
                eobs=obs('ENVIRONMENT',{'unit_environment_id':env},{**key,'record':'environment'},{'unresolvedCount':0},evidence,observed)
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':eobs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'ELASTICSEARCH_CLUSTER','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'latest_observation_id':uobs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                output=[]
                for member in members:
                    row=member['row']; stamp=db.timestamp(row['observedAt']); nodekey={'nodeId':member['nodeId']}; node=row['local']['nodes'][member['nodeId']]; inst=db.identifier()
                    explicit=any(x.get('ip')==row['ip'] and x.get('port')==row['httpPort'] for x in row['listeners'])
                    port=str(row['httpPort']) if explicit else None
                    ev={**evidence,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],'nodeId':member['nodeId'],'observedAt':row['observedAt']}
                    attrs={'portBasis':'LIVE_LISTENER' if explicit else 'WILDCARD_LISTENER','observedEndpoints':row['listeners'],'httpPublishAddress':node['http']['publish_address'],'transportAddress':node['transport_address'],'nodeId':member['nodeId'],'nodeName':node['name'],'roles':node.get('roles',[]),'version':node.get('version'),'groupingBasis':report.get('singleNodePolicy')}
                    store.insert(conn,'deployment_instance',{'id':inst,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS','primary_ip':row['ip'],'declared_port_raw':port,'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':stamp,'last_seen_at':stamp,'created_at':now,'updated_at':now})
                    iobs=obs('DEPLOYMENT',{'instance_id':inst},{**nodekey,'record':'instance'},attrs,ev,stamp);store.update(conn,'deployment_instance',inst,{'current_observation_id':iobs})
                    store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':inst,'unit_environment_id':env,'source_instance_key':source,'object_kind':'ELASTICSEARCH_NODE','native_key_json':db.encode(nodekey),'native_key_hash':db.digest(nodekey),'identity_strength':'INFERRED','confidence':'INFERRED','matching_basis':'NODE_ID_AND_ASSET_LISTENER','first_seen_at':stamp,'last_seen_at':stamp,'evidence_json':db.encode(ev)})
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'INSTANCE','object_id':inst,'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip'],'nodeId':member['nodeId']}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Elasticsearch 节点视图与发现模式或 TCP 采样核查'})
                    output.append({'ip':row['ip'],'httpPort':row['httpPort'],'nodeId':member['nodeId']})
                store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run,'object_type':'UNIT','object_id':unit,'action':'CREATE','after_json':db.encode({'name':name}),'actor_type':'IMPORT','import_run_id':run,'changed_at':now,'reason':'Elasticsearch 节点视图与发现模式或 TCP 采样核查'})
                result.append({'name':name,'members':output,'existing':False})
            if not can_apply(): raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in result),'unresolved':len(prepared['unresolved'])})});conn.commit()
        except Exception:
            conn.rollback(); raise
    return {'units':result,'unresolved':prepared['unresolved']}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');group=parser.add_mutually_exclusive_group();group.add_argument('--single-node-review',action='store_true');group.add_argument('--current-tcp-review',action='store_true');args=parser.parse_args()
    data=Path(os.environ.get('ENVSCOPE_DATA','/data')); root=data/'checks'/'elasticsearch-review'; report_name='current-tcp.json' if args.current_tcp_review else 'auth-extra.json' if args.single_node_review else 'latest.json';report=json.loads((root/report_name).read_text());version=json.loads((data/'current.json').read_text())['id']
    for stamp in (report['startedAt'],report['completedAt']):
        t=datetime.fromisoformat(stamp.replace('Z','+00:00')); now=datetime.now(timezone.utc)
        if t>now+timedelta(minutes=5) or now-t>timedelta(hours=2): raise ValueError('核查证据已过期')
    if report['sourceVersion']!=version:raise ValueError('当前版本与核查来源不一致')
    prepared=plan(report)
    if not args.apply:
        print(json.dumps({'groups':[{'clusterUuid':g['clusterUuid'],'members':[{'ip':x['row']['ip'],'httpPort':x['row']['httpPort']} for x in g['members']]} for g in prepared['groups']],'unresolved':prepared['unresolved']},ensure_ascii=False,indent=2));return
    evidence=root/'evidence';evidence.mkdir(parents=True,exist_ok=True);payload=db.encode(report);path=evidence/(db.digest(payload)+'.json')
    if path.exists() and path.read_text()!=payload:raise ValueError('核查证据校验失败')
    if not path.exists():
        tmp=path.with_suffix('.tmp');tmp.write_text(payload);tmp.chmod(0o600);os.replace(tmp,path)
    print(json.dumps(import_report(db.Catalog.from_environment(),report,version,can_apply=lambda:json.loads((data/'current.json').read_text()).get('id')==version,report_name=report_name),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
