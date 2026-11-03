"""Import Kafka units backed by process, ZooKeeper and broker metadata evidence."""
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

RULE = 'kafka-zk-single-v1'


def report_from_sources(data):
    data = Path(data)
    read = lambda p: json.loads(p.read_text())
    version = read(data / 'current.json')['id']
    root = data / 'checks' / 'kafka-review'
    names = [prefix + '-' + version + '.json' for prefix in ('initial', 'containers', 'identity', 'zk-members-expanded', 'metadata', 'metadata-peers')]
    reports = [read(root / name) for name in names]
    remote_paths = [root / (prefix + '-' + version + '.json') for prefix in ('remote-zk-v2', 'remote-metadata')]
    remote = [read(path) if path.exists() else {} for path in remote_paths]
    confirmation_path = root / ('user-app-confirmation-' + version + '.json')
    confirmation = read(confirmation_path) if confirmation_path.exists() else {}
    if confirmation and confirmation.get('sourceVersion') != version:
        raise ValueError('用户确认来源不是当前快照')
    if any(x.get('sourceVersion') != version for x in reports):
        raise ValueError('核查来源不是当前快照')
    maps = []
    for report in reports:
        rows = report.get('results', [])
        keys = [x.get('ip') for x in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('核查报告中地址重复')
        maps.append(dict(zip(keys, rows)))
    snap = read(data / 'versions' / version / 'snapshot.json')
    jump = read(data / 'versions' / version / 'jumpserver-snapshot.json')
    if snap.get('complete') is not True or snap.get('scope') != 'all':
        raise ValueError('DevOps 快照不完整')
    rows = []
    for asset in jump.get('assets', []):
        ins = asset.get('inspection') or {}
        for proc in ins.get('processes', []):
            if not re.search(r'(?:^|\s)kafka\.Kafka(?:\s|$)', proc.get('command', '')):
                continue
            deployments = []
            for app in snap.get('apps', []):
                for env, items in app.get('envs', {}).items():
                    for item in items:
                        if ac.ips(item.get('ip')) & ac.ips(asset.get('ip')):
                            deployments.append({'env': env, 'appId': str(app.get('id', '')), 'name': app.get('name')})
            initial, container, identity, zk, metadata = (m.get(asset.get('ip')) for m in maps[:5])
            peers = list(maps[5].values())
            rz, rm = remote
            if zk and zk.get('status') != 'OBSERVED' and rz.get('ip') == asset.get('ip') and rz.get('rc') == 0:
                result = rz.get('result') or {}
                cluster = result.get('cluster') or {}
                if cluster.get('code') == 0 and result.get('idsCode') == 0:
                    zk = {'ip': rz['ip'], 'zk': zk.get('zk'), 'checkedAt': rz['checkedAt'], 'status': 'OBSERVED', 'clusterCode': 0, 'brokersCode': 0, 'clusterId': (cluster.get('data') or {}).get('id'), 'brokers': [{**b, 'code': 0} for b in result.get('brokers', [])], 'observationScope': 'ASSET_LOCAL'}
            if metadata and metadata.get('status') != 'OBSERVED' and rm.get('ip') == asset.get('ip') and rm.get('rc') == 0 and (rm.get('result') or {}).get('status') == 'OBSERVED':
                metadata = {**rm['result'], 'ip': rm['ip'], 'checkedAt': rm['checkedAt'], 'observationScope': 'ASSET_LOCAL'}
            if container:
                container = dict(container)
                matched = [x for x in (ins.get('docker') or {}).get('containers', []) if x.get('id') == container.get('containerId') and x.get('initPid') == proc.get('pid')]
                container['publishedPorts'] = matched[0].get('ports', []) if len(matched) == 1 else []
            confirmed = [x for x in confirmation.get('results', []) if x.get('assetId') == asset.get('id') and x.get('ip') == asset.get('ip') and x.get('pid') == proc.get('pid') and x.get('deploymentsDigest') == db.digest(deployments)]
            rows.append({'appConfirmation': confirmed[0] if len(confirmed) == 1 else None, 'assetId': asset.get('id'), 'ip': asset.get('ip'), 'pid': proc.get('pid'),
                         'startedAt': proc.get('startedAt'), 'processStatus': ins.get('processStatus'),
                         'initial': initial, 'container': container, 'identity': identity,
                         'zk': zk, 'metadata': metadata, 'peerMetadata': peers, 'hostDeployments': deployments})
    return {'sourceVersion': version, 'ruleVersion': RULE, 'jumpserverCollectedAt': jump.get('collectedAt'),
            'devopsCollectedAt': snap.get('collectedAt'), 'reportFiles': names + [p.name for p in remote_paths if p.exists()], 'appConfirmation': confirmation, 'remoteObservations': remote, 'results': rows}


def plan(report):
    if not report.get('sourceVersion') or report.get('ruleVersion') != RULE:
        raise ValueError('来源或规则版本无效')
    rows = report.get('results', [])
    counts = Counter((r.get('assetId'), r.get('pid')) for r in rows)
    clusters = Counter((r.get('zk') or {}).get('clusterId') for r in rows if (r.get('zk') or {}).get('clusterId'))
    groups, unresolved = [], []
    for r in rows:
        i, c, ident, z, m = (r.get(k) or {} for k in ('initial', 'container', 'identity', 'zk', 'metadata'))
        reason = None
        try:
            ipaddress.ip_address(r.get('ip'))
        except (TypeError, ValueError):
            reason = '资产地址无效'
        if not r.get('assetId') or r.get('processStatus') != 'complete' or counts[(r.get('assetId'), r.get('pid'))] != 1:
            reason = reason or '进程采集不完整或重复'
        proc = i.get('BEFORE')
        match = re.fullmatch(r'(\d+) \(java\) (\d+)', str(proc or ''))
        if (not match or int(match[1]) != r.get('pid') or i.get('AFTER') != proc or
                ident.get('BEFORE') != proc or ident.get('AFTER') != proc or i.get('MAIN') != 'MATCH' or i.get('ARG') != 'MATCH'):
            reason = reason or '进程身份或配置参数不一致'
        if i.get('OVERRIDE_COUNT') != '0':
            reason = reason or '存在启动参数覆盖'
        fields = c.get('CONFIG') if c else i.get('fields')
        fields = fields or {}
        if c:
            if (c.get('INNER_CONFIG') != 'YES' or c.get('PROC') != proc or c.get('PROC_AFTER') != proc or
                    c.get('BEFORE') != c.get('AFTER') or not str(c.get('BEFORE') or '').startswith('true '+str(r.get('pid'))+' ')):
                reason = reason or '容器身份或配置未确认'
            if not any(x.get('containerPort') == '9092/tcp' and x.get('hostPort') == '9092' and x.get('hostIp') in ('0.0.0.0', r.get('ip')) for x in c.get('publishedPorts', [])):
                reason = reason or '容器发布端口未确认'
        elif i.get('CONFIG_READ') != 'YES' or not i.get('configOutputComplete'):
            reason = reason or '启动配置不可读'
        if not fields.get('zookeeper.connect') or fields.get('process.roles'):
            reason = reason or '协调模式未确认'
        if z.get('status') != 'OBSERVED' or z.get('clusterCode') != 0 or z.get('brokersCode') != 0 or not z.get('clusterId'):
            reason = reason or 'ZooKeeper 集群身份或成员未确认'
        if clusters.get(z.get('clusterId'), 0) != 1:
            reason = reason or '集群身份重复或未确认'
        brokers = z.get('brokers') or []
        views = m.get('brokers') or []
        def member(b):
            return (str(b.get('id')), b.get('host'), b.get('port'))
        registered = {member(b) for b in brokers}
        observed = {member(b) for b in views}
        if (not brokers or len(registered) != len(brokers) or len(observed) != len(views) or
                registered != observed or m.get('status') != 'OBSERVED' or any(b.get('code') != 0 for b in brokers)):
            reason = reason or 'ZooKeeper 和 Broker 成员视图不一致'
        own = [b for b in brokers if b.get('host') == r.get('ip') and b.get('port') == 9092]
        if len(own) != 1:
            reason = reason or '无法唯一确认当前 Broker'
        else:
            bid = str(own[0].get('id'))
            if ident.get('meta', {}).get('broker.id') != bid or fields.get('broker.id') not in (bid, '-1'):
                reason = reason or '配置、磁盘与注册的 Broker ID 不一致'
            if ident.get('meta', {}).get('cluster.id') not in (None, z.get('clusterId')):
                reason = reason or '磁盘和协调服务集群 ID 不一致'
        peers = [b for b in brokers if b not in own]
        for peer in peers:
            peer_views = [x for x in r.get('peerMetadata', []) if x.get('ip') == peer.get('host') and x.get('port') == peer.get('port') and x.get('status') == 'OBSERVED']
            if len(peer_views) != 1 or {member(b) for b in peer_views[0].get('brokers', [])} != registered:
                reason = reason or '其他 Broker 成员视图未交叉确认'
            else:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(peer_views[0]['checkedAt'].replace('Z', '+00:00'))
                    if age > timedelta(hours=2) or age < -timedelta(minutes=5):
                        reason = reason or '其他 Broker 核查已过期'
                except (KeyError, TypeError, ValueError, AttributeError):
                    reason = reason or '其他 Broker 核查时间无效'
        if not any(re.search(r':9092\s', line) and 'pid='+str(r.get('pid'))+',' in line for line in i.get('listeners', [])):
            reason = reason or '进程 9092 监听未确认'
        if r.get('hostDeployments'):
            confirmation = r.get('appConfirmation') or {}
            if not (confirmation.get('assetId') == r.get('assetId') and confirmation.get('ip') == r.get('ip') and confirmation.get('pid') == r.get('pid') and confirmation.get('deploymentsDigest') == db.digest(r['hostDeployments'])):
                reason = reason or '同机 DevOps 应用归属待核实'
        try:
            modified = int(ident['STAT'])
            started = datetime.fromisoformat(r['startedAt'].replace('Z', '+00:00')).timestamp()
            if modified > started + (10 if c else 0):
                reason = reason or '配置在进程启动后变更'
        except (KeyError, TypeError, ValueError, AttributeError):
            reason = reason or '配置或进程时间未知'
        for stamp in (i.get('checkedAt'), ident.get('checkedAt'), z.get('checkedAt'), m.get('checkedAt')) + ((c.get('checkedAt'),) if c else ()):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if age > timedelta(hours=2) or age < -timedelta(minutes=5):
                    reason = reason or '实时核查已过期'
            except (TypeError, ValueError, AttributeError):
                reason = reason or '核查时间无效'
        if reason:
            unresolved.append({'ip': r.get('ip'), 'pid': r.get('pid'), 'reason': reason})
        else:
            groups.append({'key': {'clusterId': z['clusterId']}, 'row': r, 'brokerId': str(own[0]['id']), 'unresolvedMembers': [{'brokerId': str(b['id']), 'ip': b['host'], 'port': b['port'], 'reason': '未关联资产或进程'} for b in peers]})
    return {'groups': sorted(groups, key=lambda x: x['row']['ip']), 'unresolved': unresolved}


def import_report(store, report, version, can_apply=lambda: True):
    prepared = plan(report)
    if report['sourceVersion'] != version:
        raise ValueError('当前版本已切换')
    source = store.source_key + ':kafka'
    payload, digest = db.encode(report), db.digest(report)
    root = Path(os.environ.get('ENVSCOPE_DATA', '/data')) / 'checks' / 'kafka-review' / 'evidence'
    root.mkdir(parents=True, exist_ok=True)
    path = root / (digest + '.json')
    if path.exists() and path.read_text() != payload:
        raise ValueError('证据哈希冲突')
    if not path.exists():
        tmp = path.with_suffix('.tmp')
        tmp.write_text(payload)
        tmp.chmod(0o600)
        os.replace(tmp, path)
    store.migrate()
    results = []
    with store.connection() as conn, store.database_lock(conn, 'component-kafka'):
        conn.begin()
        try:
            if store.execute(conn, 'SELECT id FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s', (source, digest, RULE)):
                conn.rollback()
                return {'unchanged': True, 'unresolved': prepared['unresolved']}
            now, run = db.utcnow(), db.identifier()
            observed = db.timestamp(max((x['row']['initial']['checkedAt'] for x in prepared['groups']), default=datetime.now(timezone.utc).isoformat()))
            store.insert(conn, 'catalog_import_run', {'id': run, 'source_instance_key': source, 'source_version': version,
                'source_relative_path': 'checks/kafka-review/evidence/'+digest+'.json', 'snapshot_sha256': digest,
                'mapper_version': RULE, 'scope_json': db.encode({'type': 'Kafka'}), 'completeness_json': db.encode({'complete': False}),
                'observed_at': observed, 'imported_at': now, 'status': 'WRITING'})
            for item in prepared['groups']:
                key, row = item['key'], item['row']
                stamp = db.timestamp(row['initial']['checkedAt'])
                found = store.execute(conn, "SELECT s.unit_id,u.name FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='KAFKA_CLUSTER' AND s.native_key_hash=%s", (source, db.digest(key)))
                if found:
                    results.append({'name': found[0]['name'], 'ip': row['ip'], 'existing': True})
                    continue
                existing = store.execute(conn, "SELECT native_key_json FROM deployment_instance_source WHERE source_instance_key=%s AND JSON_UNQUOTE(JSON_EXTRACT(native_key_json,'$.assetId'))=%s", (source, row['assetId']))
                if existing:
                    raise db.CatalogError('资产已有 Kafka 节点绑定，身份变化或多实例需复核')
                store.execute(conn, "INSERT INTO catalog_component_counter (technology,next_number) VALUES ('Kafka',1) ON DUPLICATE KEY UPDATE technology=technology")
                number = store.execute(conn, "SELECT next_number FROM catalog_component_counter WHERE technology='Kafka' FOR UPDATE")[0]['next_number']
                store.execute(conn, "UPDATE catalog_component_counter SET next_number=next_number+1 WHERE technology='Kafka'")
                name = 'Kafka_cluster_' + str(number)
                if store.execute(conn, 'SELECT id FROM deployment_unit WHERE name=%s', (name,)):
                    raise db.CatalogError('部署单元名称冲突')
                unit, env, instance = db.identifier(), db.identifier(), db.identifier()
                ev = {'sourceVersion': version, 'reportPath': 'checks/kafka-review/evidence/'+digest+'.json',
                      'assetId': row['assetId'], 'groupingBasis': 'ZOOKEEPER_AND_BROKER_METADATA',
                      'coverage': '实时成员已交叉确认；未关联资产的成员仅保留地址证据，历史或未来成员未知'}
                store.insert(conn, 'deployment_unit', {'id': unit, 'code': 'DU-'+unit, 'name': name, 'unit_type': 'MIDDLEWARE',
                    'technology_code': 'Kafka', 'creation_origin': 'PROCESS_REVIEW', 'name_policy': 'MANUAL', 'created_at': now, 'updated_at': now})
                store.insert(conn, 'deployment_unit_environment', {'id': env, 'unit_id': unit, 'environment': 'UNIDENTIFIED',
                    'configuration_state': 'HAS_DEPLOYMENTS', 'coverage_state': 'PARTIAL', 'observed_at': stamp, 'updated_at': now})
                def obs(kind, owner, locator, attrs):
                    oid = db.identifier()
                    store.insert(conn, 'catalog_observation', {'id': oid, 'import_run_id': run, 'record_kind': kind, **owner,
                        'source_locator_json': db.encode(locator), 'source_locator_hash': db.digest(locator), 'observed_at': stamp,
                        'time_basis': 'LIVE_CHECK', 'source_presence': 'PRESENT', 'confidence': 'INFERRED',
                        'matching_basis': 'KAFKA_ZK_BROKER_METADATA', 'source_attributes_json': db.encode(attrs),
                        'evidence_json': db.encode(ev), 'semantic_hash': db.digest(attrs)})
                    return oid
                uobs = obs('COMPONENT', {'unit_id': unit}, {**key, 'record': 'unit'}, {'technology': 'Kafka', 'clusterId': key['clusterId'], 'mode': 'ZOOKEEPER', 'unresolvedMembers': item['unresolvedMembers']})
                eobs = obs('ENVIRONMENT', {'unit_environment_id': env}, {**key, 'record': 'environment'}, {'unresolvedCount': len(item['unresolvedMembers']), 'unresolvedMembers': item['unresolvedMembers']})
                store.update(conn, 'deployment_unit_environment', env, {'current_observation_id': eobs})
                store.insert(conn, 'deployment_unit_source', {'id': db.identifier(), 'unit_id': unit, 'source_instance_key': source,
                    'object_kind': 'KAFKA_CLUSTER', 'native_key_json': db.encode(key), 'native_key_hash': db.digest(key),
                    'presence_status': 'PRESENT', 'first_seen_at': stamp, 'last_seen_at': stamp, 'latest_observation_id': uobs,
                    'confidence': 'INFERRED', 'evidence_json': db.encode(ev)})
                store.insert(conn, 'deployment_instance', {'id': instance, 'unit_environment_id': env, 'instance_kind': 'SERVICE_PROCESS',
                    'primary_ip': row['ip'], 'declared_port_raw': '9092', 'transport_protocol': 'tcp',
                    'host_reference_json': db.encode({'assetId': row['assetId']}), 'identity_quality': 'INFERRED', 'presence_status': 'PRESENT',
                    'first_seen_at': stamp, 'last_seen_at': stamp, 'created_at': now, 'updated_at': now})
                c = row.get('container')
                attrs = {'portBasis': 'OBSERVED_LISTENERS_AND_METADATA', 'brokerId': item['brokerId'], 'role': 'BROKER', 'mode': 'ZOOKEEPER',
                         'observedEndpoints': [{'ip': row['ip'], 'port': 9092, 'protocol': 'tcp', 'scope': 'HOST_PUBLISHED' if c else 'HOST'}],
                         'advertisedEndpoints': next(b for b in row['zk']['brokers'] if str(b['id']) == item['brokerId']).get('endpoints', []), 'containerId': c.get('containerId') if c else None}
                ikey = {'assetId': row['assetId'], 'clusterId': key['clusterId'], 'brokerId': item['brokerId']}
                iobs = obs('DEPLOYMENT', {'instance_id': instance}, {**ikey, 'record': 'instance'}, attrs)
                store.update(conn, 'deployment_instance', instance, {'current_observation_id': iobs})
                store.insert(conn, 'deployment_instance_source', {'id': db.identifier(), 'instance_id': instance, 'unit_environment_id': env,
                    'source_instance_key': source, 'object_kind': 'KAFKA_BROKER', 'native_key_json': db.encode(ikey), 'native_key_hash': db.digest(ikey),
                    'identity_strength': 'INFERRED', 'confidence': 'INFERRED', 'matching_basis': 'CLUSTER_BROKER_ASSET',
                    'first_seen_at': stamp, 'last_seen_at': stamp, 'evidence_json': db.encode(ev)})
                for typ, oid in (('INSTANCE', instance), ('UNIT', unit)):
                    store.insert(conn, 'catalog_change', {'id': db.identifier(), 'change_batch_id': run, 'object_type': typ, 'object_id': oid,
                        'action': 'CREATE', 'after_json': db.encode({'name': name, 'ip': row['ip']}), 'actor_type': 'IMPORT',
                        'import_run_id': run, 'changed_at': now, 'reason': 'Kafka 进程、ZooKeeper 和 Broker 元数据只读核查'})
                results.append({'name': name, 'ip': row['ip'], 'brokerId': item['brokerId'], 'unresolvedMembers': item['unresolvedMembers'], 'existing': False})
            if not can_apply():
                raise db.CatalogError('核查来源版本已切换，本批未写入')
            store.execute(conn, 'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE', (source,))
            store.update(conn, 'catalog_import_run', run, {'status': 'READY', 'is_current': True,
                'counts_json': db.encode({'createdUnits': sum(not x['existing'] for x in results), 'unresolved': len(prepared['unresolved']) + sum(len(x['unresolvedMembers']) for x in prepared['groups'])})})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {'units': results, 'unresolved': prepared['unresolved']}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    data = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
    report = report_from_sources(data)
    prepared = plan(report)
    if not args.apply:
        print(json.dumps({'groups': [{'ip': x['row']['ip'], 'brokerId': x['brokerId'], 'unresolvedMembers': x['unresolvedMembers'], **x['key']} for x in prepared['groups']],
                          'unresolved': prepared['unresolved']}, ensure_ascii=False, indent=2))
        return
    result = import_report(db.Catalog.from_environment(), report, report['sourceVersion'],
                           can_apply=lambda: json.loads((data/'current.json').read_text()).get('id') == report['sourceVersion'])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
