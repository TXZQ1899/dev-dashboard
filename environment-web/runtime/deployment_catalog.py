"""MySQL management catalog derived from immutable, existing DevOps snapshots.

No collection or process discovery happens here. Source records remain evidence;
platform identities and manually managed fields survive subsequent imports.
"""
import hashlib
import ipaddress
import json
import os
import threading
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ENVIRONMENTS = ('TEST', 'SIMULATION', 'PRODUCT')
SUPPORTED_ENVIRONMENTS = (*ENVIRONMENTS, 'UNIDENTIFIED')
MAPPER_VERSION = 'devops-catalog-v1'


class CatalogError(Exception):
    pass


class CatalogInputError(CatalogError):
    pass


def identifier():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=lambda v: v.isoformat() + 'Z' if isinstance(v, datetime) else str(v))


def decode(value, default=None):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def text(value):
    return '' if value is None else str(value)


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def iso(value):
    return value.isoformat(timespec='seconds') + 'Z' if isinstance(value, datetime) else value


def prepare_environment(app, environment):
    """Separate real targets, placeholders and conflicting/unresolved records."""
    raw = app.get('envs', {}).get(environment)
    if raw is not None and not isinstance(raw, list):
        raise CatalogInputError('应用环境记录必须是数组')
    rows, issues, seen = [], [], set()
    for index, row in enumerate(raw or []):
        if not isinstance(row, dict):
            raise CatalogInputError('部署记录必须是对象')
        fingerprint = digest(row)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        attrs = dict(row)
        attrs['portBasis'] = 'DEPLOYMENT_RECORD' if 'port' in row else 'APPLICATION_DEFAULT'
        port = text(row.get('port') if 'port' in row else app.get('port'))
        ip = text(row.get('ip')).strip()
        item = {'index': index, 'row': attrs, 'ip': ip, 'port': port,
                'deploy': text(row.get('deploy')), 'semanticHash': digest(attrs)}
        if not ip and row.get('status') in ('无环境配置', '无部署实例') and not row.get('deploy') and not row.get('config'):
            continue
        try:
            item['ip'] = str(ipaddress.ip_address(ip))
        except ValueError:
            item['reason'] = 'MISSING_TARGET_IP' if not ip else 'INVALID_TARGET_IP'
            issues.append(item)
            continue
        rows.append(item)
    groups = defaultdict(list)
    for item in rows:
        groups[(item['deploy'], item['ip'], item['port'])].append(item)
    valid = []
    for group in groups.values():
        if len(group) > 1:
            issues.extend({**item, 'reason': 'CONFLICTING_DEPLOYMENT_RECORDS'} for item in group)
        else:
            valid.extend(group)
    statuses = [row.get('status') for row in raw or []]
    if valid or issues:
        state = 'HAS_DEPLOYMENTS'
    elif statuses and all(s == '无环境配置' for s in statuses):
        state = 'UNCONFIGURED'
    elif statuses and all(s == '无部署实例' for s in statuses):
        state = 'CONFIGURED_EMPTY'
    else:
        state = 'UNKNOWN'
    complete = bool(raw) and not issues and all(s in ('成功', '无环境配置', '无部署实例') for s in statuses)
    if state == 'HAS_DEPLOYMENTS' and any(s in ('无环境配置', '无部署实例') for s in statuses):
        complete = False
    return {'state': state, 'coverage': 'COMPLETE' if complete else 'PARTIAL' if raw else 'UNKNOWN',
            'records': sorted(valid, key=lambda r: r['index']), 'issues': issues}


def prepare_snapshot(raw):
    try:
        snapshot = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise CatalogInputError('应用快照不是有效 JSON') from None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('apps'), list):
        raise CatalogInputError('缺少应用清单')
    if snapshot.get('complete') is False:
        raise CatalogInputError('未完成的应用快照不能导入管理目录')
    apps, ids = [], set()
    for index, app in enumerate(snapshot['apps']):
        if not isinstance(app, dict) or isinstance(app.get('id'), bool) or not text(app.get('id')).strip():
            raise CatalogInputError('应用缺少有效 ID')
        app_id = text(app['id'])
        if app_id in ids:
            raise CatalogInputError('应用清单存在重复 ID')
        ids.add(app_id)
        if not isinstance(app.get('envs', {}), dict):
            raise CatalogInputError('应用环境格式无效')
        if not isinstance(app.get('name', ''), str) or len(app.get('name', '')) > 255:
            raise CatalogInputError('应用名称格式无效')
        apps.append({'index': index, 'id': app_id, 'name': app.get('name') or app_id,
                     'attributes': {k: v for k, v in app.items() if k != 'envs'},
                     'observedAt': timestamp(app.get('capturedAt')) or timestamp(snapshot.get('collectedAt')),
                     'timeBasis': 'SOURCE' if timestamp(app.get('capturedAt')) else 'SOURCE_BATCH' if timestamp(snapshot.get('collectedAt')) else 'UNKNOWN',
                     'environments': {env: prepare_environment(app, env) for env in ENVIRONMENTS}})
    return {'apps': apps, 'observedAt': timestamp(snapshot.get('collectedAt')),
            'complete': snapshot.get('complete') is True,
            'scope': snapshot.get('scope', 'unknown'), 'sha256': hashlib.sha256(raw).hexdigest()}


def member_key(app_id, environment, record):
    return {'appId': app_id, 'environment': environment, 'deploy': record['deploy'],
            'ip': record['ip'], 'port': record['port']}


def match_member(record, bindings, deploy_counts, ip_counts, claimed):
    """Conservative continuity in an application/environment, never IP globally."""
    candidates = [b for b in bindings if b['key'].get('deploy', '') == record['deploy']]
    def unique(rows):
        return {b['instance_id'] for b in rows}
    exact = unique(b for b in candidates if b['key'].get('ip') == record['ip'] and b['key'].get('port') == record['port'])
    if len(exact) == 1:
        selected, basis = next(iter(exact)), 'SCOPED_SOURCE_RECORD'
    elif len(exact) > 1:
        return None, 'AMBIGUOUS_SOURCE_RECORD'
    else:
        same_ip = unique(b for b in candidates if b['key'].get('ip') == record['ip'])
        if len(same_ip) == 1 and ip_counts[record['ip']] == 1:
            selected, basis = next(iter(same_ip)), 'UNIQUE_SCOPED_TARGET'
        elif record['deploy'] and deploy_counts[record['deploy']] == 1 and len(unique(candidates)) == 1:
            selected, basis = next(iter(unique(candidates))), 'UNIQUE_SCOPED_DEPLOYMENT'
        elif record['deploy'] and deploy_counts[record['deploy']] == 1 and len(unique(candidates)) > 1:
            return None, 'AMBIGUOUS_DEPLOYMENT_MEMBERS'
        else:
            return None, 'NEW_SOURCE_MEMBER'
    return (None, 'SOURCE_MEMBER_ALREADY_CLAIMED') if selected in claimed else (selected, basis)


class Catalog:
    def __init__(self, config=None, connector=None):
        self.config = config or {}
        self.connector = connector
        self.source_key = self.config.get('source_key', 'devops-default')
        self._migrated = False
        self._migration_lock = threading.Lock()

    @classmethod
    def from_environment(cls):
        return cls({'host': os.environ.get('ENVSCOPE_DB_HOST', ''),
                    'port': os.environ.get('ENVSCOPE_DB_PORT', '3306'),
                    'database': os.environ.get('ENVSCOPE_DB_NAME', 'envscope'),
                    'user': os.environ.get('ENVSCOPE_DB_USER', 'envscope'),
                    'password': os.environ.get('ENVSCOPE_DB_PASSWORD', ''),
                    'ssl_ca': os.environ.get('ENVSCOPE_DB_SSL_CA'),
                    'source_key': os.environ.get('ENVSCOPE_DEVOPS_SOURCE', 'devops-default')})

    @property
    def configured(self):
        return bool(self.connector or self.config.get('host'))

    @contextmanager
    def connection(self):
        if not self.configured:
            raise CatalogError('部署单元数据库未配置，请通过 Compose 启动 MySQL 或配置 ENVSCOPE_DB_HOST')
        try:
            if self.connector:
                conn = self.connector()
            else:
                import pymysql
                conn = pymysql.connect(host=self.config['host'], port=int(self.config['port']),
                    user=self.config['user'], password=self.config['password'], database=self.config['database'],
                    charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                    connect_timeout=4, read_timeout=30, write_timeout=30,
                    ssl_ca=self.config.get('ssl_ca'), ssl_verify_identity=bool(self.config.get('ssl_ca')))
        except Exception:
            raise CatalogError('无法连接部署单元 MySQL 数据库，请检查服务及连接配置') from None
        try:
            yield conn
        except CatalogError:
            raise
        except Exception:
            raise CatalogError('部署单元数据库操作失败，请检查数据库状态后重试') from None
        finally:
            conn.close()

    @staticmethod
    def execute(conn, sql, args=()):
        with conn.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.fetchall() if cursor.description else []

    @staticmethod
    def insert(conn, table, values):
        # Tables and column names are internal constants, never request input.
        Catalog.execute(conn, f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join(['%s'] * len(values))})", tuple(values.values()))

    @staticmethod
    def update(conn, table, key, values):
        Catalog.execute(conn, f"UPDATE {table} SET {','.join(k + '=%s' for k in values)} WHERE id=%s", (*values.values(), key))

    @contextmanager
    def database_lock(self, conn, name):
        lock_name = 'envscope.catalog.' + hashlib.sha256(name.encode()).hexdigest()[:32]
        if self.execute(conn, 'SELECT GET_LOCK(%s, 10) AS acquired', (lock_name,))[0]['acquired'] != 1:
            raise CatalogError('部署单元正在更新，请稍后重试')
        try:
            yield
        finally:
            self.execute(conn, 'SELECT RELEASE_LOCK(%s)', (lock_name,))

    def migrate(self):
        with self._migration_lock:
            if self._migrated:
                return
            with self.connection() as conn, self.database_lock(conn, 'schema'):
                self.execute(conn, 'CREATE TABLE IF NOT EXISTS catalog_schema_migration (version INT PRIMARY KEY, checksum CHAR(64) NOT NULL, applied_at DATETIME(6) NOT NULL) ENGINE=InnoDB')
                path = Path(__file__).with_name('migrations') / '001_deployment_catalog.sql'
                sql = path.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                applied = self.execute(conn, 'SELECT checksum FROM catalog_schema_migration WHERE version=1')
                if applied and applied[0]['checksum'] != checksum:
                    raise CatalogError('部署目录迁移文件校验失败，请使用追加迁移升级')
                if not applied:
                    for statement in sql.split(';'):
                        if statement.strip():
                            self.execute(conn, statement)
                    self.execute(conn, 'INSERT INTO catalog_schema_migration VALUES (1,%s,%s)', (checksum, utcnow()))
                # DDL auto-commits. Named constraints and IF NOT EXISTS make
                # every migration step independently restartable under the lock.
                path = Path(__file__).with_name('migrations') / '002_component_catalog.sql'
                sql = path.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                applied = self.execute(conn, 'SELECT checksum FROM catalog_schema_migration WHERE version=2')
                if applied and applied[0]['checksum'] != checksum:
                    raise CatalogError('部署目录迁移文件校验失败，请使用追加迁移升级')
                if not applied:
                    statements = [item.strip() for item in sql.split(';') if item.strip()]
                    for table, name, statement in zip(('deployment_unit_environment', 'catalog_observation'),
                            ('deployment_unit_environment_allowed', 'catalog_observation_allowed'), statements[:2]):
                        found = self.execute(conn, "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s AND CONSTRAINT_TYPE='CHECK'", (table, name))
                        if not found:
                            self.execute(conn, statement)
                    self.execute(conn, statements[2])
                    self.execute(conn, 'INSERT INTO catalog_schema_migration VALUES (2,%s,%s)', (checksum, utcnow()))
            self._migrated = True

    def head(self, conn):
        rows = self.execute(conn, 'SELECT * FROM catalog_import_run WHERE source_instance_key=%s AND is_current=TRUE', (self.source_key,))
        return rows[0] if rows else None

    def observation(self, conn, run_id, kind, owner_id, locator, attrs, observed_at, time_basis, basis='SOURCE_RECORD', confidence='EXACT'):
        owner = {'APPLICATION': 'unit_id', 'ENVIRONMENT': 'unit_environment_id',
                 'UNRESOLVED_DEPLOYMENT': 'unit_environment_id', 'DEPLOYMENT': 'instance_id'}[kind]
        obs_id = identifier()
        self.insert(conn, 'catalog_observation', {
            'id': obs_id, 'import_run_id': run_id, 'record_kind': kind, owner: owner_id,
            'source_locator_json': encode(locator), 'source_locator_hash': digest(locator),
            'observed_at': observed_at, 'time_basis': time_basis, 'source_presence': 'PRESENT',
            'confidence': confidence, 'matching_basis': basis, 'source_attributes_json': encode(attrs),
            'evidence_json': encode({'source': 'devops', 'locator': locator}), 'semantic_hash': digest(attrs)})
        return obs_id

    def change(self, conn, run_id, kind, owner_id, action, before, after):
        self.insert(conn, 'catalog_change', {'id': identifier(), 'change_batch_id': run_id,
            'object_type': kind, 'object_id': owner_id, 'action': action,
            'before_json': encode(before) if before is not None else None,
            'after_json': encode(after) if after is not None else None,
            'actor_type': 'IMPORT', 'import_run_id': run_id, 'changed_at': utcnow(),
            'reason': '读取已完成的 DevOps 应用快照'})

    def import_snapshot(self, raw, source_version, source_relative_path, can_apply=lambda: True):
        plan = prepare_snapshot(raw)
        self.migrate()
        with self.connection() as conn, self.database_lock(conn, self.source_key):
            conn.begin()
            try:
                existing = self.execute(conn, 'SELECT * FROM catalog_import_run WHERE source_instance_key=%s AND snapshot_sha256=%s AND mapper_version=%s',
                                        (self.source_key, plan['sha256'], MAPPER_VERSION))
                if existing:
                    conn.rollback()
                    return {'id': existing[0]['id'], 'unchanged': True, 'counts': decode(existing[0]['counts_json'], {})}
                head = self.head(conn)
                if head and head['observed_at'] and (not plan['observedAt'] or plan['observedAt'] < head['observed_at']):
                    conn.rollback()
                    return {'id': head['id'], 'historical': True, 'counts': decode(head['counts_json'], {})}
                run_id, now = identifier(), utcnow()
                self.insert(conn, 'catalog_import_run', {'id': run_id, 'source_instance_key': self.source_key,
                    'source_version': source_version, 'source_relative_path': source_relative_path,
                    'snapshot_sha256': plan['sha256'], 'mapper_version': MAPPER_VERSION,
                    'scope_json': encode({'scope': plan['scope']}), 'completeness_json': encode({'complete': plan['complete']}),
                    'observed_at': plan['observedAt'], 'imported_at': now, 'status': 'WRITING'})
                counts = {'applications': len(plan['apps']), 'createdUnits': 0, 'createdInstances': 0, 'unresolved': 0}
                seen_units = set()
                for app in plan['apps']:
                    unit_id = self.import_application(conn, run_id, app, counts)
                    seen_units.add(unit_id)
                if plan['complete'] and plan['scope'] == 'all':
                    sources = self.execute(conn, "SELECT * FROM deployment_unit_source WHERE source_instance_key=%s AND object_kind='DEVOPS_APPLICATION'", (self.source_key,))
                    for source in sources:
                        if source['unit_id'] not in seen_units and source['presence_status'] != 'NOT_OBSERVED':
                            self.update(conn, 'deployment_unit_source', source['id'], {'presence_status': 'NOT_OBSERVED'})
                            self.change(conn, run_id, 'UNIT_SOURCE', source['unit_id'], 'NOT_OBSERVED', {'presence': source['presence_status']}, {'presence': 'NOT_OBSERVED'})
                            self.mark_missing(conn, run_id, unit_id=source['unit_id'])
                if not can_apply():
                    raise CatalogError('导入期间应用快照已切换，本批未发布，将读取新的当前快照')
                self.execute(conn, 'UPDATE catalog_import_run SET is_current=FALSE WHERE source_instance_key=%s AND is_current=TRUE', (self.source_key,))
                self.update(conn, 'catalog_import_run', run_id, {'status': 'READY', 'is_current': True, 'counts_json': encode(counts)})
                conn.commit()
                return {'id': run_id, 'counts': counts}
            except Exception:
                conn.rollback()
                raise

    def import_application(self, conn, run_id, app, counts):
        key = {'appId': app['id']}
        rows = self.execute(conn, "SELECT s.*,u.name,u.name_policy FROM deployment_unit_source s JOIN deployment_unit u ON u.id=s.unit_id WHERE s.source_instance_key=%s AND s.object_kind='DEVOPS_APPLICATION' AND s.native_key_hash=%s", (self.source_key, digest(key)))
        source, now = (rows[0] if rows else None), utcnow()
        if source:
            unit_id = source['unit_id']
            if source['name_policy'] == 'FOLLOW_SOURCE' and source['name'] != app['name']:
                self.execute(conn, 'UPDATE deployment_unit SET name=%s,revision=revision+1,updated_at=%s WHERE id=%s', (app['name'], now, unit_id))
                self.change(conn, run_id, 'UNIT', unit_id, 'RENAME', {'name': source['name']}, {'name': app['name']})
        else:
            unit_id = identifier()
            self.insert(conn, 'deployment_unit', {'id': unit_id, 'code': 'DU-' + unit_id,
                'name': app['name'], 'unit_type': 'APPLICATION', 'creation_origin': 'DEVOPS_IMPORT',
                'created_at': now, 'updated_at': now})
            source = {'id': identifier(), 'unit_id': unit_id}
            self.insert(conn, 'deployment_unit_source', {**source, 'source_instance_key': self.source_key,
                'object_kind': 'DEVOPS_APPLICATION', 'native_key_json': encode(key), 'native_key_hash': digest(key),
                'presence_status': 'PRESENT', 'first_seen_at': app['observedAt'], 'last_seen_at': app['observedAt'],
                'confidence': 'EXACT', 'evidence_json': encode({'appId': app['id']})})
            self.change(conn, run_id, 'UNIT', unit_id, 'CREATE', None, {'appId': app['id'], 'name': app['name']})
            counts['createdUnits'] += 1
        locator = {'file': 'snapshot.json', 'pointer': f"/apps/{app['index']}"}
        obs_id = self.observation(conn, run_id, 'APPLICATION', unit_id, locator, app['attributes'], app['observedAt'], app['timeBasis'])
        self.update(conn, 'deployment_unit_source', source['id'], {'presence_status': 'PRESENT', 'last_seen_at': app['observedAt'], 'latest_observation_id': obs_id})
        for env, prepared in app['environments'].items():
            self.import_environment(conn, run_id, unit_id, app, env, prepared, counts)
        return unit_id

    def mark_missing(self, conn, run_id, environment_id=None, unit_id=None, seen=()):
        if environment_id:
            rows = self.execute(conn, 'SELECT id,presence_status FROM deployment_instance WHERE unit_environment_id=%s', (environment_id,))
        else:
            rows = self.execute(conn, 'SELECT i.id,i.presence_status FROM deployment_instance i JOIN deployment_unit_environment e ON e.id=i.unit_environment_id WHERE e.unit_id=%s', (unit_id,))
        for row in rows:
            if row['id'] not in seen and row['presence_status'] != 'NOT_OBSERVED':
                self.execute(conn, "UPDATE deployment_instance SET presence_status='NOT_OBSERVED',revision=revision+1,updated_at=%s WHERE id=%s", (utcnow(), row['id']))
                self.change(conn, run_id, 'INSTANCE', row['id'], 'NOT_OBSERVED', {'presence': row['presence_status']}, {'presence': 'NOT_OBSERVED'})

    def import_environment(self, conn, run_id, unit_id, app, env, prepared, counts):
        rows = self.execute(conn, 'SELECT * FROM deployment_unit_environment WHERE unit_id=%s AND environment=%s', (unit_id, env))
        env_id = rows[0]['id'] if rows else identifier()
        if not rows:
            self.insert(conn, 'deployment_unit_environment', {'id': env_id, 'unit_id': unit_id, 'environment': env,
                'configuration_state': 'UNKNOWN', 'coverage_state': 'UNKNOWN', 'updated_at': utcnow()})
        bindings = self.execute(conn, 'SELECT * FROM deployment_instance_source WHERE source_instance_key=%s AND unit_environment_id=%s', (self.source_key, env_id))
        for binding in bindings:
            binding['key'] = decode(binding['native_key_json'])
        claimed, issues = set(), list(prepared['issues'])
        deploy_counts = Counter(r['deploy'] for r in prepared['records'])
        ip_counts = Counter(r['ip'] for r in prepared['records'])
        for record in prepared['records']:
            instance_id, basis = match_member(record, bindings, deploy_counts, ip_counts, claimed)
            if basis.startswith('AMBIGUOUS') or basis == 'SOURCE_MEMBER_ALREADY_CLAIMED':
                issues.append({**record, 'reason': basis})
                continue
            instance_id = self.import_instance(conn, run_id, env_id, app, env, record, instance_id, basis, counts)
            claimed.add(instance_id)
        for issue in issues:
            attrs = {'record': issue['row'], 'reason': issue['reason']}
            self.observation(conn, run_id, 'UNRESOLVED_DEPLOYMENT', env_id,
                {'pointer': f"/apps/{app['index']}/envs/{env}/{issue['index']}"}, attrs,
                app['observedAt'], app['timeBasis'], issue['reason'], 'AMBIGUOUS' if 'CONFLICT' in issue['reason'] or 'AMBIGUOUS' in issue['reason'] else 'UNKNOWN')
        coverage = 'PARTIAL' if issues else prepared['coverage']
        attrs = {'configurationState': prepared['state'], 'coverageState': coverage,
                 'unresolvedCount': len(issues), 'observedInstances': len(claimed)}
        obs_id = self.observation(conn, run_id, 'ENVIRONMENT', env_id,
            {'pointer': f"/apps/{app['index']}/envs/{env}"}, attrs, app['observedAt'], app['timeBasis'])
        self.update(conn, 'deployment_unit_environment', env_id, {'configuration_state': prepared['state'],
            'coverage_state': coverage, 'current_observation_id': obs_id, 'observed_at': app['observedAt'], 'updated_at': utcnow()})
        counts['unresolved'] += len(issues)
        if coverage == 'COMPLETE':
            self.mark_missing(conn, run_id, environment_id=env_id, seen=claimed)

    def import_instance(self, conn, run_id, env_id, app, env, record, instance_id, basis, counts):
        now = utcnow()
        before = None
        if instance_id:
            before = self.execute(conn, 'SELECT i.*,o.semantic_hash FROM deployment_instance i LEFT JOIN catalog_observation o ON o.id=i.current_observation_id WHERE i.id=%s', (instance_id,))[0]
        else:
            instance_id = identifier()
            self.insert(conn, 'deployment_instance', {'id': instance_id, 'unit_environment_id': env_id,
                'instance_kind': 'HOST_DEPLOYMENT', 'identity_quality': 'INFERRED', 'presence_status': 'PRESENT',
                'first_seen_at': app['observedAt'], 'created_at': now, 'updated_at': now})
            counts['createdInstances'] += 1
        obs_id = self.observation(conn, run_id, 'DEPLOYMENT', instance_id,
            {'pointer': f"/apps/{app['index']}/envs/{env}/{record['index']}"}, record['row'],
            app['observedAt'], app['timeBasis'], basis, 'INFERRED')
        self.execute(conn, 'UPDATE deployment_instance SET primary_ip=%s,declared_port_raw=%s,identity_quality=%s,presence_status=%s,current_observation_id=%s,last_seen_at=%s,revision=revision+1,updated_at=%s WHERE id=%s',
            (record['ip'], record['port'], 'INFERRED', 'PRESENT', obs_id, app['observedAt'], now, instance_id))
        key = member_key(app['id'], env, record)
        existing = self.execute(conn, 'SELECT * FROM deployment_instance_source WHERE source_instance_key=%s AND unit_environment_id=%s AND native_key_hash=%s AND object_kind=%s',
                                (self.source_key, env_id, digest(key), 'DEVOPS_DEPLOYMENT_MEMBER'))
        if existing:
            if existing[0]['instance_id'] != instance_id:
                raise CatalogInputError('部署成员来源绑定冲突，本批未发布')
            self.update(conn, 'deployment_instance_source', existing[0]['id'], {'last_seen_at': app['observedAt']})
        else:
            self.insert(conn, 'deployment_instance_source', {'id': identifier(), 'instance_id': instance_id,
                'unit_environment_id': env_id, 'source_instance_key': self.source_key, 'object_kind': 'DEVOPS_DEPLOYMENT_MEMBER',
                'native_key_json': encode(key), 'native_key_hash': digest(key), 'identity_strength': 'WEAK',
                'confidence': 'INFERRED', 'matching_basis': basis, 'first_seen_at': app['observedAt'],
                'last_seen_at': app['observedAt'], 'evidence_json': encode({'source': 'devops', 'key': key})})
        if not before or before['semantic_hash'] != record['semanticHash'] or before['presence_status'] != 'PRESENT':
            self.change(conn, run_id, 'INSTANCE', instance_id, 'CREATE' if not before else 'UPDATE_OBSERVATION',
                {'ip': before['primary_ip'], 'port': before['declared_port_raw'], 'presence': before['presence_status']} if before else None,
                {'ip': record['ip'], 'port': record['port'], 'source': record['row']})
        return instance_id

    def read(self, *, query='', page=1, page_size=20, presence='present', unit_id=None, environment=None):
        self.migrate()
        with self.connection() as conn:
            conn.begin()
            try:
                result = self._read(conn, query, page, page_size, presence, unit_id, environment)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def _read(self, conn, query, page, page_size, presence, unit_id, environment):
        head = self.head(conn)
        source = None if not head else {'sourceVersion': head['source_version'], 'snapshotSha256': head['snapshot_sha256'],
            'observedAt': iso(head['observed_at']), 'importedAt': iso(head['imported_at']),
            'counts': decode(head['counts_json'], {})}
        if unit_id:
            unit = self.execute(conn, 'SELECT u.id FROM deployment_unit u JOIN deployment_unit_source s ON s.unit_id=u.id WHERE u.id=%s AND s.source_instance_key IN (%s,%s,%s,%s,%s,%s)', (unit_id, self.source_key, self.source_key + ':zookeeper', self.source_key + ':nacos', self.source_key + ':nginx', self.source_key + ':kong', self.source_key + ':redis'))
            if not unit:
                raise CatalogInputError('部署单元不存在')
            sql = 'SELECT i.*,e.environment,o.source_attributes_json AS attributes FROM deployment_instance i JOIN deployment_unit_environment e ON e.id=i.unit_environment_id LEFT JOIN catalog_observation o ON o.id=i.current_observation_id WHERE e.unit_id=%s'
            args = [unit_id]
            if environment:
                sql += ' AND e.environment=%s'
                args.append(environment)
            if presence == 'present':
                sql += " AND i.presence_status='PRESENT'"
            rows = self.execute(conn, sql + ' ORDER BY e.environment,i.primary_ip,i.id', tuple(args))
            return {'source': source, 'instances': [{'id': r['id'], 'environment': r['environment'],
                'ip': r['primary_ip'], 'port': r['declared_port_raw'], 'identityQuality': r['identity_quality'],
                'presenceStatus': r['presence_status'], 'managementStatus': r['management_status'],
                'lastSeenAt': iso(r['last_seen_at']), 'attributes': decode(r['attributes'], {})} for r in rows]}
        where, args = 's.source_instance_key IN (%s,%s,%s,%s,%s,%s)', [self.source_key, self.source_key + ':zookeeper', self.source_key + ':nacos', self.source_key + ':nginx', self.source_key + ':kong', self.source_key + ':redis']
        if presence == 'present':
            where += " AND s.presence_status='PRESENT'"
        if query:
            needle = '%' + query.replace('=', '==').replace('%', '=%').replace('_', '=_') + '%'
            where += " AND (u.name LIKE %s ESCAPE '=' OR u.code LIKE %s ESCAPE '=' OR JSON_UNQUOTE(JSON_EXTRACT(s.native_key_json,'$.appId')) LIKE %s ESCAPE '=')"
            args.extend([needle] * 3)
        base = ' FROM deployment_unit u JOIN deployment_unit_source s ON s.unit_id=u.id '
        total = self.execute(conn, 'SELECT COUNT(*) AS n' + base + 'WHERE ' + where, tuple(args))[0]['n']
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = self.execute(conn, 'SELECT u.*,s.presence_status AS source_presence,s.native_key_json,o.source_attributes_json AS attributes,bs.name AS system_name,bd.name AS domain_name' + base +
            'LEFT JOIN catalog_observation o ON o.id=s.latest_observation_id LEFT JOIN business_system bs ON bs.id=u.system_id LEFT JOIN business_domain bd ON bd.id=bs.domain_id WHERE ' + where +
            ' ORDER BY u.name,u.id LIMIT %s OFFSET %s', (*args, page_size, (page - 1) * page_size))
        units = []
        for row in rows:
            attrs = decode(row['attributes'], {})
            environments = self.execute(conn, "SELECT e.*,o.source_attributes_json AS attributes,COUNT(CASE WHEN i.presence_status='PRESENT' THEN i.id END) AS instance_count,COUNT(DISTINCT CASE WHEN i.presence_status='PRESENT' THEN i.primary_ip END) AS server_count FROM deployment_unit_environment e LEFT JOIN deployment_instance i ON i.unit_environment_id=e.id LEFT JOIN catalog_observation o ON o.id=e.current_observation_id WHERE e.unit_id=%s GROUP BY e.id,o.source_attributes_json ORDER BY e.environment", (row['id'],))
            units.append({'id': row['id'], 'code': row['code'], 'name': row['name'], 'unitType': row['unit_type'],
                'appId': decode(row['native_key_json']).get('appId'), 'appType': attrs.get('appType') or row['technology_code'],
                'groupingState': attrs.get('groupingState'),
                'repository': attrs.get('repository'), 'systemName': row['system_name'], 'domainName': row['domain_name'],
                'managementStatus': row['management_status'], 'sourcePresence': row['source_presence'],
                'environments': [{'environment': e['environment'], 'configurationState': e['configuration_state'],
                    'coverageState': e['coverage_state'], 'instanceCount': e['instance_count'], 'serverCount': e['server_count'],
                    'unresolvedCount': decode(e['attributes'], {}).get('unresolvedCount', 0)} for e in environments]})
        summary = self.execute(conn, "SELECT COUNT(*) AS units,SUM(presence_status='PRESENT') AS present_units FROM deployment_unit_source WHERE source_instance_key IN (%s,%s,%s,%s,%s,%s)", (self.source_key, self.source_key + ':zookeeper', self.source_key + ':nacos', self.source_key + ':nginx', self.source_key + ':kong', self.source_key + ':redis'))[0]
        instance_count = self.execute(conn, "SELECT COUNT(*) AS n FROM deployment_instance i JOIN deployment_unit_environment e ON e.id=i.unit_environment_id JOIN deployment_unit_source s ON s.unit_id=e.unit_id WHERE s.source_instance_key IN (%s,%s,%s,%s,%s,%s) AND i.presence_status='PRESENT'", (self.source_key, self.source_key + ':zookeeper', self.source_key + ':nacos', self.source_key + ':nginx', self.source_key + ':kong', self.source_key + ':redis'))[0]['n']
        return {'source': source, 'units': units, 'pagination': {'page': page, 'pageSize': page_size, 'total': total, 'totalPages': pages},
                'summary': {'units': summary['units'], 'presentUnits': int(summary['present_units'] or 0), 'instances': instance_count}}
