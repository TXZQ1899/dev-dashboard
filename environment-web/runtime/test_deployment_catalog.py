import copy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import deployment_catalog as c
except ModuleNotFoundError:
    from runtime import deployment_catalog as c


def fixture(day=1):
    def member(deploy, ip, port='8080'):
        return {'deploy': deploy, 'config': 'cfg-1', 'ip': ip, 'port': port, 'status': '成功',
                'branch': 'release-test', 'branchSource': '配置声明', 'repository': 'https://example.invalid/team/service.git',
                'lastPublishedAt': '2026-10-01 12:00:00', 'publishStatus': 'SUCCESS', 'pushIn': []}
    stamp = f'2026-10-{day:02d}T12:00:00+08:00'
    return {'complete': True, 'scope': 'all', 'collectedAt': stamp, 'source': f'fixture-{day}', 'apps': [
        {'id': 'app-1', 'name': 'sample-service', 'appType': 'JAR', 'port': '8080', 'capturedAt': stamp,
         'repository': 'https://example.invalid/team/service.git', 'envs': {
             'TEST': [{'status': '无部署实例', 'ip': '', 'deploy': '', 'config': ''}],
             'SIMULATION': [member('sim-1', '192.0.2.10')],
             'PRODUCT': [member('prod-1', '192.0.2.20'), member('prod-2', '192.0.2.20', '8081')],
         }}]}


def raw(snapshot):
    return json.dumps(snapshot, ensure_ascii=False).encode()


class PreparationTests(unittest.TestCase):
    def test_zero_one_many_and_two_members_on_same_ip(self):
        plan = c.prepare_snapshot(raw(fixture()))
        env = plan['apps'][0]['environments']
        self.assertEqual(env['TEST']['state'], 'CONFIGURED_EMPTY')
        self.assertEqual(len(env['TEST']['records']), 0)
        self.assertEqual(len(env['SIMULATION']['records']), 1)
        self.assertEqual(len(env['PRODUCT']['records']), 2)
        self.assertEqual(plan['observedAt'], datetime(2026, 10, 1, 4))

    def test_unconfigured_unknown_missing_and_conflicts_remain_distinct(self):
        app = fixture()['apps'][0]
        app['envs']['TEST'] = [{'status': '无环境配置'}]
        self.assertEqual(c.prepare_environment(app, 'TEST')['state'], 'UNCONFIGURED')
        app['envs']['TEST'] = []
        self.assertEqual(c.prepare_environment(app, 'TEST')['state'], 'UNKNOWN')
        app['envs']['TEST'] = [{'deploy': 'd', 'config': 'c', 'status': '实例未返回IP'}]
        prepared = c.prepare_environment(app, 'TEST')
        self.assertEqual(prepared['issues'][0]['reason'], 'MISSING_TARGET_IP')
        self.assertEqual(prepared['coverage'], 'PARTIAL')
        original = app['envs']['PRODUCT'][0]
        app['envs']['PRODUCT'] = [original, copy.deepcopy(original), {**original, 'branch': 'other'}]
        prepared = c.prepare_environment(app, 'PRODUCT')
        self.assertEqual(len(prepared['records']), 0)
        self.assertEqual(len(prepared['issues']), 2)

    def test_invalid_snapshot_or_duplicate_application_is_rejected(self):
        for value in (b'not-json', b'[]', b'{}', raw({**fixture(), 'complete': False})):
            with self.assertRaises(c.CatalogInputError): c.prepare_snapshot(value)
        value = fixture()
        value['apps'].append(copy.deepcopy(value['apps'][0]))
        with self.assertRaises(c.CatalogInputError): c.prepare_snapshot(raw(value))

    def test_declared_port_is_not_silently_replaced_when_explicitly_empty(self):
        app = fixture()['apps'][0]
        app['envs']['SIMULATION'][0]['port'] = ''
        result = c.prepare_environment(app, 'SIMULATION')['records'][0]
        self.assertEqual(result['port'], '')
        self.assertEqual(result['row']['portBasis'], 'DEPLOYMENT_RECORD')
        del app['envs']['SIMULATION'][0]['port']
        self.assertEqual(c.prepare_environment(app, 'SIMULATION')['records'][0]['row']['portBasis'], 'APPLICATION_DEFAULT')

    def test_same_scoped_native_member_can_change_ip_but_multi_target_stays_ambiguous(self):
        old = [{'instance_id': 'i-1', 'key': {'deploy': 'd', 'ip': '192.0.2.1', 'port': '8080'}}]
        new = {'deploy': 'd', 'ip': '192.0.2.2', 'port': '9090'}
        self.assertEqual(c.match_member(new, old, {'d': 1}, {'192.0.2.2': 1}, set()), ('i-1', 'UNIQUE_SCOPED_DEPLOYMENT'))
        old.append({'instance_id': 'i-2', 'key': {'deploy': 'd', 'ip': '192.0.2.3', 'port': '8080'}})
        self.assertEqual(c.match_member(new, old, {'d': 1}, {'192.0.2.2': 1}, set())[1], 'AMBIGUOUS_DEPLOYMENT_MEMBERS')


@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST') == '1', 'requires the isolated MySQL integration test container')
class MySQLCatalogTests(unittest.TestCase):
    def setUp(self):
        self.store = c.Catalog.from_environment()
        self.store.source_key = 'test-' + c.identifier()
        self.store.migrate()

    def ingest(self, value, version=None, **kwargs):
        return self.store.import_snapshot(raw(value), version or value['source'], 'fixtures/snapshot.json', **kwargs)

    def sql(self, sql, args=()):
        with self.store.connection() as conn:
            return self.store.execute(conn, sql, args)

    def details(self):
        listing = self.store.read(presence='all')
        unit = listing['units'][0]
        return unit, self.store.read(unit_id=unit['id'], presence='all')['instances']

    def test_bootstrap_idempotency_and_same_host_multiple_members(self):
        first = self.ingest(fixture())
        second = self.ingest(fixture(), 'copied-version')
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['unchanged'])
        listing = self.store.read()
        self.assertEqual(listing['summary'], {'units': 1, 'presentUnits': 1, 'instances': 3})
        unit = listing['units'][0]
        self.assertEqual(unit['appId'], 'app-1')
        self.assertIsNone(unit['systemName'])
        self.assertIsNone(unit['domainName'])
        envs = {e['environment']: e for e in unit['environments']}
        self.assertEqual(envs['TEST']['instanceCount'], 0)
        self.assertEqual(envs['PRODUCT']['instanceCount'], 2)
        self.assertEqual(envs['PRODUCT']['serverCount'], 1)
        self.assertEqual(len(self.store.read(unit_id=unit['id'])['instances']), 3)

    def test_updates_keep_ids_and_manual_management_fields(self):
        self.ingest(fixture())
        unit, instances = self.details()
        original = {r['attributes']['deploy']: r['id'] for r in instances}
        with self.store.connection() as conn:
            domain_id, system_id, now = c.identifier(), c.identifier(), c.utcnow()
            self.store.insert(conn, 'business_domain', {'id': domain_id, 'code': domain_id, 'name': 'domain', 'created_at': now, 'updated_at': now})
            self.store.insert(conn, 'business_system', {'id': system_id, 'domain_id': domain_id, 'code': system_id, 'name': 'system', 'created_at': now, 'updated_at': now})
            self.store.update(conn, 'deployment_unit', unit['id'], {'name': 'managed-name', 'name_policy': 'MANUAL', 'description': 'preserve', 'system_id': system_id, 'management_status': 'DISABLED'})
        updated = fixture(2)
        updated['apps'][0]['name'] = 'source-renamed'
        record = updated['apps'][0]['envs']['SIMULATION'][0]
        record.update(ip='192.0.2.99', config='cfg-2', branch='release-new', lastPublishedAt='2026-10-02 11:00:00')
        self.ingest(updated)
        current, rows = self.details()
        self.assertEqual(current['id'], unit['id'])
        self.assertEqual(current['name'], 'managed-name')
        self.assertEqual(current['systemName'], 'system')
        self.assertEqual(current['managementStatus'], 'DISABLED')
        self.assertEqual({r['attributes']['deploy']: r['id'] for r in rows}, original)
        changed = next(r for r in rows if r['environment'] == 'SIMULATION')
        self.assertEqual(changed['ip'], '192.0.2.99')
        self.assertEqual(changed['attributes']['config'], 'cfg-2')
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM catalog_observation WHERE instance_id=%s", (changed['id'],))[0]['n'], 2)

    def test_partial_missing_and_empty_source_do_not_delete(self):
        self.ingest(fixture())
        value = fixture(2)
        value['apps'][0]['envs']['PRODUCT'] = [{'status': '读取失败', 'ip': ''}]
        self.ingest(value)
        self.assertEqual(self.store.read()['summary']['instances'], 3)
        value = fixture(3)
        value['apps'][0]['envs']['PRODUCT'] = [{'status': '无部署实例'}]
        self.ingest(value)
        self.assertEqual(self.store.read()['summary']['instances'], 1)
        unit, rows = self.details()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(r['presenceStatus'] == 'NOT_OBSERVED' for r in rows), 2)
        value = fixture(4)
        value['apps'] = []
        self.ingest(value)
        self.assertEqual(self.store.read()['pagination']['total'], 0)
        self.assertEqual(self.store.read(presence='all')['pagination']['total'], 1)
        self.assertEqual(self.store.read()['summary']['instances'], 0)
        self.assertEqual(len(self.store.read(unit_id=unit['id'], presence='all')['instances']), 3)

    def test_failure_rolls_back_and_old_snapshot_cannot_replace_current(self):
        with self.assertRaises(c.CatalogError): self.ingest(fixture(), can_apply=lambda: False)
        self.assertEqual(self.store.read()['summary']['units'], 0)
        newest = self.ingest(fixture(3))
        older = fixture(2)
        older['apps'][0]['name'] = 'old-name'
        result = self.ingest(older)
        self.assertTrue(result['historical'])
        self.assertEqual(result['id'], newest['id'])
        self.assertEqual(self.store.read()['units'][0]['name'], 'sample-service')

    def test_unresolved_conflicts_never_create_phantom_instances(self):
        value = fixture()
        app = value['apps'][0]
        original = app['envs']['PRODUCT'][0]
        app['envs']['PRODUCT'] = [original, {**original, 'branch': 'conflicting'}]
        app['envs']['SIMULATION'] = [{'deploy': 'sim-1', 'config': 'cfg', 'status': '实例未返回IP'}]
        result = self.ingest(value)
        self.assertEqual(result['counts']['unresolved'], 3)
        self.assertEqual(self.store.read()['summary']['instances'], 0)

    def test_multi_target_native_id_preserves_distinct_members_and_old_id(self):
        value = fixture()
        value['apps'][0]['envs']['PRODUCT'] = [value['apps'][0]['envs']['PRODUCT'][0]]
        self.ingest(value)
        _, before = self.details()
        old_id = next(r['id'] for r in before if r['environment'] == 'PRODUCT')
        new = fixture(2)
        first = new['apps'][0]['envs']['PRODUCT'][0]
        new['apps'][0]['envs']['PRODUCT'] = [{**first, 'ip': '192.0.2.21'}, first]
        self.ingest(new)
        unit, rows = self.details()
        product = [r for r in rows if r['environment'] == 'PRODUCT']
        self.assertEqual(len(product), 2)
        self.assertEqual(next(r['id'] for r in product if r['ip'] == '192.0.2.20'), old_id)
        third = fixture(3)
        third['apps'][0]['envs']['PRODUCT'] = [{**first, 'ip': '192.0.2.22'}]
        result = self.ingest(third)
        self.assertEqual(result['counts']['unresolved'], 1)
        self.assertEqual(len(self.store.read(unit_id=unit['id'], presence='all')['instances']), 3)

    def test_search_escapes_wildcards_and_concurrent_import_is_idempotent(self):
        value = fixture()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.ingest(value), range(2)))
        self.assertEqual(len({r['id'] for r in results}), 1)
        self.assertEqual(self.store.read()['summary']['units'], 1)
        self.assertEqual(self.store.read(query='%')['pagination']['total'], 0)
        self.assertEqual(self.store.read(query='app-1')['pagination']['total'], 1)


if __name__ == '__main__':
    unittest.main()
