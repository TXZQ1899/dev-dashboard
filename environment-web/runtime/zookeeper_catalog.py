"""Import independently verified standalone ZooKeeper nodes into the catalog.

Only consumes server-side evidence produced by a trusted operator, never browser
input. Ambiguous candidates are returned for review and never created.
"""
import ipaddress
import json
import re
from collections import Counter

try:
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import deployment_catalog as db


def plan(report, listener_report):
    if not report.get('sourceVersion') or report.get('sourceVersion') != listener_report.get('sourceVersion'):
        raise ValueError('核查报告版本不一致')
    checks = {(r.get('ip'), r.get('pid')): r for r in listener_report.get('results', [])}
    check_counts = Counter((r.get('ip'), r.get('pid')) for r in listener_report.get('results', []))
    keys = Counter((r.get('assetId'), r.get('configPath')) for r in report.get('results', []))
    accepted, unresolved = [], []
    for row in report.get('results', []):
        ip, pid = row.get('ip'), row.get('pid')
        check = checks.get((ip, pid)) or {}
        reason = None
        try: ipaddress.ip_address(ip)
        except (ValueError, TypeError): reason = 'IP 无效'
        if not row.get('assetId') or not isinstance(row.get('configPath'), str) or not row['configPath'].startswith('/') or keys[(row.get('assetId'), row.get('configPath'))] != 1:
            reason = '资产或配置身份不唯一'
        if row.get('status') != 'OBSERVED' or row.get('mode') != 'standalone' or row.get('frames', 0) < 2 or row.get('warnings'):
            reason = '运行模式或 TCP 采样未充分确认'
        if any(row.get('applicationCandidates', {}).values()): reason = '存在 DevOps 应用候选'
        if len(row.get('configFields', [])) >= 100 or row.get('connectionCount', 0) > len(row.get('established', [])):
            reason = '配置或 TCP 观察被截断'
        fields = {}
        for line in row.get('configFields', []):
            key, sep, value = line.partition('=')
            if not sep or key.strip() in fields:
                reason = '配置字段重复或格式不明'; break
            fields[key.strip()] = value.strip()
        if fields.get('dynamicConfigFile'): reason = '存在动态配置，需另行核查'
        servers = [(k, v) for k, v in fields.items() if re.fullmatch(r'server\.\d+', k)]
        if servers and not (len(servers) == 1 and fields.get('standaloneEnabled', '').lower() == 'true' and
                re.fullmatch(r'(localhost|127\.0\.0\.1):\d+:\d+(?::(?:participant|observer))?;\d+', servers[0][1], re.I)):
            reason = '配置存在需核实的集群成员'
        port_text = fields.get('clientPort')
        if not port_text and servers: port_text = servers[0][1].rsplit(';', 1)[-1]
        if not port_text or not port_text.isdigit() or not 0 < int(port_text) <= 65535:
            reason = '客户端端口不明确'; port = 0
        else: port = int(port_text)
        owned = [x for x in row.get('listeners', []) if x.get('port') == port and x.get('protocol') == 'tcp']
        listeners = [x for x in check.get('listeners', []) if x.get('endpoint', {}).get('port') == port]
        if check_counts[(ip, pid)] != 1 or not owned or check.get('status') != 'OBSERVED' or check.get('warnings') or not listeners or any(x.get('pids') != [pid] for x in listeners):
            reason = '监听进程归属不唯一'
        if any(s.get('local', {}).get('port') in (2888, 3888) or s.get('peer', {}).get('port') in (2888, 3888) for s in row.get('established', [])):
            reason = '观察到需核实的 quorum 端口连接'
        if not row.get('observedAt') or not check.get('observedAt') or not row.get('startTicks'):
            reason = '观察时间或进程身份缺失'
        if reason:
            unresolved.append({'ip': ip, 'pid': pid, 'reason': reason}); continue
        accepted.append({'row': row, 'listenerCheck': check, 'port': port})
    return {'accepted': accepted, 'unresolved': unresolved}


def import_report(store, report, listener_report, source_version, can_apply=lambda: True):
    prepared = plan(report, listener_report)
    if report['sourceVersion'] != source_version: raise ValueError('当前版本与核查来源不一致')
    store.migrate()
    source = store.source_key + ':zookeeper'
    raw = db.encode({'report': report, 'listenerChecks': listener_report})
    report_hash = db.digest(raw)
    result = []
    with store.connection() as conn, store.database_lock(conn, 'component-zookeeper'):
        conn.begin()
        try:
            existing = store.execute(conn, 'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, report_hash, 'zookeeper-review-v1'))
            if existing:
                conn.rollback()
                return {'unchanged': True, 'unresolved': prepared['unresolved']}
            now = db.utcnow(); run_id = db.identifier()
            store.insert(conn, 'catalog_import_run', {'id':run_id,'source_instance_key':source,'source_version':source_version,
                'source_relative_path':'checks/zookeeper-review/latest.json','snapshot_sha256':report_hash,'mapper_version':'zookeeper-review-v1',
                'scope_json':db.encode({'type':'Zookeeper'}),'completeness_json':db.encode({'complete':False}),
                'observed_at':db.timestamp(report.get('completedAt')),'imported_at':now,'status':'WRITING'})
            for item in prepared['accepted']:
                row = item['row']; port = item['port']; observed = db.timestamp(row['observedAt'])
                key = {'assetId':row['assetId'], 'configPath':row['configPath']}
                found = store.execute(conn, "SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='ZOOKEEPER_STANDALONE' AND s.native_key_hash=%s", (source, db.digest(key)))
                if found:
                    result.append({'name':found[0]['name'], 'ip':row['ip'], 'port':port, 'existing':True}); continue
                store.execute(conn, "INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Zookeeper',1) ON DUPLICATE KEY UPDATE technology=technology")
                number = store.execute(conn, "SELECT next_number FROM catalog_component_counter WHERE technology='Zookeeper' FOR UPDATE")[0]['next_number']
                store.execute(conn, "UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Zookeeper'")
                name = 'Zookeeper_cluster_'+str(number)
                if store.execute(conn, 'SELECT id FROM deployment_unit WHERE name=%s', (name,)): raise db.CatalogError('部署单元名称冲突')
                unit, env, instance = db.identifier(), db.identifier(), db.identifier()
                evidence = {'sourceVersion':source_version,'assetId':row['assetId'],'pid':row['pid'],'startTicks':row['startTicks'],
                    'observedAt':row['observedAt'],'listenerObservedAt':item['listenerCheck']['observedAt'],
                    'mode':'standalone','configPath':row['configPath'],'configFields':row['configFields'],
                    'tcpFrames':row['frames'],'tcpConnections':row['connectionCount'],'reportPath':'checks/zookeeper-review/evidence/'+db.digest(db.encode(report))+'.json',
                    'listenerReportPath':'checks/zookeeper-review/evidence/'+db.digest(db.encode(listener_report))+'.json'}
                store.insert(conn,'deployment_unit',{'id':unit,'code':'DU-'+unit,'name':name,'unit_type':'MIDDLEWARE','technology_code':'Zookeeper',
                    'creation_origin':'PROCESS_REVIEW','name_policy':'MANUAL','created_at':now,'updated_at':now})
                store.insert(conn,'deployment_unit_environment',{'id':env,'unit_id':unit,'environment':'UNIDENTIFIED','configuration_state':'HAS_DEPLOYMENTS',
                    'coverage_state':'PARTIAL','observed_at':observed,'updated_at':now})
                store.insert(conn,'deployment_instance',{'id':instance,'unit_environment_id':env,'instance_kind':'SERVICE_PROCESS',
                    'primary_ip':row['ip'],'declared_port_raw':str(port),'transport_protocol':'tcp','host_reference_json':db.encode({'assetId':row['assetId']}),
                    'identity_quality':'INFERRED','presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,'created_at':now,'updated_at':now})
                def observation(kind, owner, locator, attributes, confidence):
                    oid=db.identifier()
                    store.insert(conn,'catalog_observation',{'id':oid,'import_run_id':run_id,'record_kind':kind,**owner,
                        'source_locator_json':db.encode(locator),'source_locator_hash':db.digest(locator),'observed_at':observed,'time_basis':'LIVE_CHECK',
                        'source_presence':'PRESENT','confidence':confidence,'matching_basis':'CONFIG_MODE_LISTENER_TCP',
                        'source_attributes_json':db.encode(attributes),'evidence_json':db.encode(evidence),'semantic_hash':db.digest(attributes)})
                    return oid
                loc={'assetId':row['assetId'],'pid':row['pid']}
                unit_obs=observation('COMPONENT',{'unit_id':unit},{**loc,'kind':'unit'},{'technology':'Zookeeper'},'INFERRED')
                env_obs=observation('ENVIRONMENT',{'unit_environment_id':env},{**loc,'kind':'environment'},{'unresolvedCount':0},'INFERRED')
                instance_obs=observation('DEPLOYMENT',{'instance_id':instance},{**loc,'kind':'instance'},{'portBasis':'LIVE_LISTENER','mode':'standalone'},'INFERRED')
                store.update(conn,'deployment_unit_environment',env,{'current_observation_id':env_obs})
                store.update(conn,'deployment_instance',instance,{'current_observation_id':instance_obs})
                store.insert(conn,'deployment_unit_source',{'id':db.identifier(),'unit_id':unit,'source_instance_key':source,'object_kind':'ZOOKEEPER_STANDALONE',
                    'native_key_json':db.encode(key),'native_key_hash':db.digest(key),'presence_status':'PRESENT','first_seen_at':observed,'last_seen_at':observed,
                    'latest_observation_id':unit_obs,'confidence':'INFERRED','evidence_json':db.encode(evidence)})
                store.insert(conn,'deployment_instance_source',{'id':db.identifier(),'instance_id':instance,'unit_environment_id':env,'source_instance_key':source,
                    'object_kind':'ZOOKEEPER_NODE','native_key_json':db.encode(key),'native_key_hash':db.digest(key),'identity_strength':'INFERRED',
                    'confidence':'INFERRED','matching_basis':'ASSET_AND_CONFIG','first_seen_at':observed,'last_seen_at':observed,'evidence_json':db.encode(evidence)})
                for kind, ident in (('UNIT',unit),('INSTANCE',instance)):
                    store.insert(conn,'catalog_change',{'id':db.identifier(),'change_batch_id':run_id,'object_type':kind,'object_id':ident,
                        'action':'CREATE','after_json':db.encode({'name':name,'ip':row['ip']}),'actor_type':'IMPORT','import_run_id':run_id,
                        'changed_at':now,'reason':'Zookeeper 配置、运行模式和 TCP 核查'})
                result.append({'name':name,'ip':row['ip'],'port':port,'existing':False})
            if not can_apply(): raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn,'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE',(source,))
            store.update(conn,'catalog_import_run',run_id,{'status':'READY','is_current':True,'counts_json':db.encode({'createdUnits':sum(not x['existing'] for x in result),'unresolved':len(prepared['unresolved'])})})
            conn.commit()
        except Exception:
            conn.rollback(); raise
    return {'units':result,'unresolved':prepared['unresolved']}


def main():
    import argparse
    import hashlib
    import os
    from datetime import datetime, timezone, timedelta
    from pathlib import Path
    parser = argparse.ArgumentParser(description='核查报告导入 Zookeeper 单节点部署单元')
    parser.add_argument('--apply', action='store_true', help='写入目录；默认只预览')
    args = parser.parse_args()
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    root = data/'checks'/'zookeeper-review'
    read = lambda path: json.loads(path.read_text())
    report, checks = read(root/'latest.json'), read(root/'listener-checks.json')
    version = read(data/'current.json')['id']
    completed = datetime.fromisoformat(report['completedAt'].replace('Z', '+00:00'))
    if completed > datetime.now(timezone.utc) + timedelta(minutes=5) or datetime.now(timezone.utc) - completed > timedelta(hours=2):
        raise ValueError('核查证据已过期，请重新核查')
    preview = plan(report, checks)
    if report['sourceVersion'] != version: raise ValueError('当前版本与核查来源不一致')
    if not args.apply:
        print(json.dumps({'candidates':[{'ip': x['row']['ip'], 'port':x['port']} for x in preview['accepted']],
                          'unresolved':preview['unresolved']}, ensure_ascii=False, indent=2)); return
    evidence_dir = root/'evidence'; evidence_dir.mkdir(parents=True, exist_ok=True)
    for value in (report, checks):
        payload = db.encode(value); path = evidence_dir/(db.digest(payload)+'.json')
        if path.exists() and path.read_text() != payload: raise ValueError('核查证据校验失败')
        if not path.exists():
            temp = path.with_suffix('.tmp'); temp.write_text(payload); temp.chmod(0o600); os.replace(temp,path)
    result = import_report(db.Catalog.from_environment(), report, checks, version,
        can_apply=lambda: read(data/'current.json').get('id') == version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
