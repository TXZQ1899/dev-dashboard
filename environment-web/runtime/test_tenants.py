import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import aliyun_tenants as tenancy
import tenant_collection as cloud
from collect import parallel_collect, mark_codeup
from export_devops import ExportError, AuthenticationError


def saved_accounts():
    saved = {'aliyun_auth_mode': {'value': 'accesskey'},
             'aliyun_accesskey_id': {'value': 'key-a'}, 'aliyun_accesskey_secret': {'value': 'secret-a'}}
    tenancy.normalize(saved)
    tenancy.update(saved, [{'name': '租户 B', 'accountId': '2002', 'accessKeyId': 'key-b',
                           'accessKeySecret': 'secret-b'}], verifier=lambda auth: '2002')
    return saved


class TenantConfigurationTests(unittest.TestCase):
    def test_legacy_migration_is_stable_and_codeup_is_bound_to_original(self):
        saved = saved_accounts()
        first = deepcopy(tenancy.normalize(saved))
        self.assertEqual(tenancy.normalize(saved), first)
        self.assertEqual(len(tenancy.select(saved)), 2)
        self.assertEqual([t['id'] for t in tenancy.select(saved, 'codeup')], [tenancy.LEGACY_TENANT])
        self.assertNotIn('secret-', json.dumps([tenancy.public_tenant(t) for t in first['tenants']]))

    def test_rotation_preserves_identity_and_blank_secret_and_rejects_other_account(self):
        saved = saved_accounts(); old = deepcopy(saved['aliyunTenants']['tenants'][1])
        tenancy.update(saved, [{'id': old['id'], 'credentialVersion': old['credentialVersion'], 'name': '重命名'}])
        current = saved['aliyunTenants']['tenants'][1]
        self.assertEqual(current['auth']['accessKeySecret'], 'secret-b')
        self.assertEqual(current['id'], old['id'])
        with self.assertRaises(ValueError):
            tenancy.update(deepcopy(saved), [{'id': current['id'], 'credentialVersion': current['credentialVersion'],
                                              'accessKeyId': 'other-key', 'accessKeySecret': 'other-secret'}], verifier=lambda _: '3003')
        with self.assertRaises(ValueError):
            tenancy.update(deepcopy(saved), [{'id': current['id'], 'credentialVersion': current['credentialVersion'], 'accessKeyId': 'new-key'}])
        with self.assertRaises(ValueError):
            tenancy.update(deepcopy(saved), [{'id': current['id'], 'credentialVersion': old['credentialVersion'], 'name': 'stale'}])

    def test_duplicate_cloud_accounts_rejected_and_disabled_accounts_not_scheduled(self):
        saved = saved_accounts(); config = tenancy.normalize(saved)
        with self.assertRaises(ValueError):
            tenancy.update(deepcopy(saved), [{'name': 'duplicate', 'accessKeyId': 'duplicate-key',
                                              'accessKeySecret': 'duplicate-secret'}], verifier=lambda _: '2002')
        config['tenants'][1]['enabled'] = False
        self.assertEqual(len(tenancy.select(saved)), 1)
        with self.assertRaises(ValueError): tenancy.select(saved, 'ecs', config['tenants'][1]['id'])

    def test_codeup_provenance_only_applies_to_codeup_repositories(self):
        snapshot = mark_codeup({'repos': [{'source': 'codeup'}, {'source': 'local_gitlab'}]}, saved_accounts())
        self.assertEqual(snapshot['repos'][0]['tenantId'], tenancy.LEGACY_TENANT)
        self.assertNotIn('tenantId', snapshot['repos'][1])

    def test_legacy_editor_manages_mode_cookie_and_codeup_without_losing_access_keys(self):
        saved = saved_accounts()
        original = tenancy.normalize(saved)['tenants'][0]
        tenancy.update(saved, [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                'mode': 'cookie', 'cookie': 'Cookie: session=private',
                                'codeupOrganizationId': 'organization-1'}])
        original = tenancy.normalize(saved)['tenants'][0]
        self.assertEqual(original['auth']['mode'], 'cookie')
        self.assertEqual(original['auth']['cookie'], 'session=private')
        self.assertEqual(tenancy.value(saved, 'aliyun'), 'session=private')
        self.assertEqual(tenancy.value(saved, 'codeup_organization_id'), 'organization-1')
        self.assertEqual(tenancy.public_tenant(original)['codeupOrganizationId'], 'organization-1')
        self.assertNotIn('session=private', json.dumps(tenancy.public_tenant(original)))
        tenancy.update(saved, [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                'mode': 'accesskey', 'cookie': '', 'accessKeyId': '', 'accessKeySecret': '',
                                'codeupOrganizationId': ''}], verifier=lambda _: '1001')
        current = tenancy.normalize(saved)['tenants'][0]
        self.assertEqual((current['auth']['mode'], current['auth']['accessKeyId'], current['auth']['accessKeySecret']),
                         ('accesskey', 'key-a', 'secret-a'))
        self.assertEqual(current['auth']['cookie'], 'session=private')
        self.assertEqual(tenancy.value(saved, 'codeup_organization_id'), '')

    def test_other_tenants_cannot_edit_codeup_or_cookie_compatibility(self):
        saved = saved_accounts(); tenant = tenancy.normalize(saved)['tenants'][1]
        for patch in ({'mode': 'cookie'}, {'cookie': 'private'}, {'codeupOrganizationId': 'other'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                tenancy.update(deepcopy(saved), [{'id': tenant['id'], 'credentialVersion': tenant['credentialVersion'], **patch}])

    def test_tenant_cookie_and_codeup_input_validation(self):
        saved = saved_accounts(); original = tenancy.normalize(saved)['tenants'][0]
        for patch in ({'cookie': 'bad\nheader'}, {'codeupOrganizationId': 'bad\nid'}, {'mode': 'invalid'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                tenancy.update(deepcopy(saved), [{'id': original['id'], 'credentialVersion': original['credentialVersion'], **patch}])


class TenantCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.saved = saved_accounts()

    def run_tasks(self, source=None, tenant_id=None, fail=False):
        def verify(tenant): tenant['accountId'] = '1001' if tenant['id'] == tenancy.LEGACY_TENANT else '2002'
        def collect_ecs(key, secret, out, region):
            self.assertEqual(secret, 'secret-' + key[-1])
            if fail and key == 'key-b': raise AuthenticationError('secret-b')
            return {'FetchedAt': '2026-10-23T01:00:00Z', 'RegionId': region, 'Instances': {'Instance': []}}
        def clb(*args, **kwargs): return {'complete': True, 'region': kwargs['region'], 'collectedAt': '2026-10-23T01:00:00Z', 'serverCertificates': {}, 'instances': []}
        def dms(*args, **kwargs): return {'available': True, 'tenantId': kwargs['tid'], 'collectedAt': '2026-10-23T01:00:00Z', 'instances': []}
        with patch.object(cloud, 'verify', side_effect=verify), patch.object(cloud, 'collect_ecs_ak', side_effect=collect_ecs), \
             patch.object(cloud, 'collect_clb_ak', side_effect=clb), \
             patch.object(cloud, 'collect_nat_ak', return_value={}), \
             patch.object(cloud, 'normalize_nat', return_value={'available': True, 'collectedAt': '2026-10-23T01:00:00Z', 'gateways': []}), \
             patch.object(cloud, 'collect_dms_ak', side_effect=dms), \
             patch.object(cloud, 'collect_dns_ak', return_value={'collectedAt': '2026-10-23T01:00:00Z', 'zones': [], 'available': True}), \
             patch.object(cloud.RpcClient, 'call', return_value={'Success': True, 'Tenant': {'Tid': 10}}):
            tasks, contexts, selected = cloud.build_tasks(self.saved, self.folder, source, tenant_id)
            results = parallel_collect(tasks, self.folder/'progress.json', contexts=contexts)
            return tasks, contexts, selected, results

    def test_two_accounts_collect_five_sources_and_zero_is_a_success(self):
        tasks, contexts, selected, results = self.run_tasks()
        self.assertEqual(len(tasks), 10)
        self.assertEqual({c['source'] for c in contexts.values()}, {'ECS', 'CLB', 'NAT', 'DMS', 'CLOUD-DNS'})
        self.assertTrue(all(r['complete'] for r in results.values()))
        self.assertTrue(all(r['tenants'][0]['count'] == 0 for r in results.values()))
        progress = json.loads((self.folder/'progress.json').read_text())
        self.assertEqual(len(progress['tasks']), 10)
        self.assertEqual(set(progress['sources'].values()), {'完成'})
        self.assertNotIn('secret-', json.dumps(progress))

    def test_selected_tenant_runs_only_its_source(self):
        tenant_id = self.saved['aliyunTenants']['tenants'][1]['id']
        tasks, contexts, selected, results = self.run_tasks('nat', tenant_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(selected[0]['id'], tenant_id)
        self.assertEqual(list(contexts.values())[0]['source'], 'NAT')

    def test_account_global_sources_are_not_repeated_per_region(self):
        self.saved['aliyunTenants']['tenants'][1]['enabled'] = False
        self.assertEqual(len(self.run_tasks()[0]), 5)
        self.saved['aliyunTenants']['tenants'][0]['regions'].append('cn-hangzhou')
        tasks, contexts, _, _ = self.run_tasks()
        self.assertEqual(len(tasks), 8)
        self.assertEqual(sum(c['source'] == 'DMS' for c in contexts.values()), 1)

    def test_full_entry_collects_both_accounts_but_calls_codeup_only_with_original_key(self):
        import collect
        from contextlib import ExitStack
        self.saved.update({key: {'value': 'private'} for key in ('jumpserver', 'devops', 'local_gitlab')})
        credentials_file = self.folder/'input.json'; credentials_file.write_text(json.dumps(self.saved))
        cloud_result = lambda *args, **kwargs: {'available': True, 'collectedAt': '2026-10-23T00:00:00Z', 'instances': [], 'gateways': []}
        def verify(tenant): tenant['accountId'] = '1001' if tenant['id'] == tenancy.LEGACY_TENANT else '2002'
        with ExitStack() as stack:
            stack.enter_context(patch.object(collect, 'ALIYUN_MODE', 'cookie'))
            stack.enter_context(patch.object(collect.sys, 'argv', ['collect.py', str(self.folder), str(credentials_file)]))
            stack.enter_context(patch.object(collect, 'install_slot_logs'))
            stack.enter_context(patch.object(cloud, 'verify', side_effect=verify))
            stack.enter_context(patch.object(cloud, 'collect_ecs_ak', return_value={'FetchedAt': 'now', 'RegionId': 'cn-shanghai', 'Instances': {'Instance': []}}))
            stack.enter_context(patch.object(cloud, 'collect_clb_ak', return_value={}))
            stack.enter_context(patch.object(cloud, 'normalize_clb', side_effect=cloud_result))
            stack.enter_context(patch.object(cloud, 'collect_nat_ak', return_value={}))
            stack.enter_context(patch.object(cloud, 'normalize_nat', side_effect=cloud_result))
            stack.enter_context(patch.object(cloud, 'collect_dms_ak', side_effect=cloud_result))
            stack.enter_context(patch.object(cloud, 'collect_dns_ak', return_value={'available': True, 'collectedAt': '2026-10-23T00:00:00Z', 'zones': []}))
            stack.enter_context(patch.object(cloud.RpcClient, 'call', return_value={'Success': True, 'Tenant': {'Tid': 10}}))
            stack.enter_context(patch.object(collect, 'jumpserver', return_value={}))
            stack.enter_context(patch.object(collect.daily, 'ROOT', self.folder))
            for name, result in [('collect_local_gitlab', {}), ('collect_devops', {'apps': []}), ('reconcile', {'repos': []})]:
                stack.enter_context(patch.object(collect.daily, name, return_value=result))
            codeup = stack.enter_context(patch.object(collect, 'collect_codeup_ak', return_value={'groups': [], 'repos': []}))
            stack.enter_context(patch.object(collect, 'codeup_reference_status', return_value={}))
            collect.main()
            self.assertEqual(codeup.call_count, 1)
            self.assertEqual(codeup.call_args.args[1:3], ('key-a', 'secret-a'))
        for source in tenancy.CLOUD_SOURCES:
            snapshot = json.loads((self.folder/f'{source}-snapshot.json').read_text())
            self.assertEqual(len(snapshot['tenants']), 2)

    def test_one_tenant_failure_keeps_structured_scope_and_redacts_credentials(self):
        with self.assertRaises(ExportError) as error: self.run_tasks('ecs', fail=True)
        failure = error.exception.failures[0]
        self.assertEqual(failure['tenantName'], '租户 B')
        self.assertEqual(failure['source'], 'ECS')
        self.assertEqual(failure['type'], 'accesskey_invalid')
        self.assertNotIn('secret-b', str(error.exception))

    def test_tenant_slice_merge_preserves_other_tenant_and_empty_refresh_removes_old_rows(self):
        a, b = self.saved['aliyunTenants']['tenants']
        def snapshot(tenant, ip):
            return cloud.scoped_snapshot({'fetchedAt': 'old', 'instances': [{'id': 'same-id', 'privateIps': [ip]}], 'projects': []}, tenant, 'ecs', 'cn-shanghai')
        previous = cloud.merge_snapshots({}, [snapshot(a, '10.1.0.1'), snapshot(b, '10.2.0.1')], 'ecs', True)
        self.assertNotEqual(previous['instances'][0]['resourceKey'], previous['instances'][1]['resourceKey'])
        refreshed = cloud.scoped_snapshot({'fetchedAt': 'new', 'instances': [], 'projects': []}, b, 'ecs', 'cn-shanghai')
        result = cloud.merge_snapshots(previous, [refreshed], 'ecs', selected_ids=[b['id']])
        self.assertEqual(result['instances'], previous['instances'][:1])
        self.assertEqual(len(result['tenants']), 2)
        self.assertEqual(result['tenants'][0]['collectedAt'], 'old')

    def test_dms_same_address_is_retained_in_both_tenants(self):
        a, b = self.saved['aliyunTenants']['tenants']
        raw = {'tenantId': 10, 'collectedAt': 'now', 'instances': [{'实例ID': '123', '实例地址': 'db:3306', 'dmsInstanceIds': ['123']}]}
        slices = [cloud.scoped_snapshot(raw, tenant, 'dms', '') for tenant in (a, b)]
        result = cloud.merge_snapshots({}, slices, 'dms', True)
        self.assertEqual(len(result['instances']), 2)
        self.assertNotIn('tenantId', result)
        self.assertEqual(result['instances'][0]['dmsTenantId'], 10)

    def test_clb_warning_merge_retains_other_tenants_and_clears_recovered_scope(self):
        a, b = self.saved['aliyunTenants']['tenants']
        def snapshot(tenant, failed):
            warnings = [{'loadBalancerId': 'lb-skipped', 'status': 'active',
                         'action': 'DescribeHealthStatus', 'params': {'LoadBalancerId': 'lb-skipped'},
                         'reason': 'request_failed', 'error': '网络超时或连接异常', 'skipped': True}] if failed else []
            return cloud.scoped_snapshot({'collectedAt': 'now', 'instances': [], 'warnings': warnings,
                                           'partial': bool(warnings)}, tenant, 'clb', 'cn-shanghai')
        previous = cloud.merge_snapshots({}, [snapshot(a, True), snapshot(b, True)], 'clb', True)
        self.assertTrue(previous['complete'])
        self.assertTrue(previous['partial'])
        self.assertEqual({w['tenantId'] for w in previous['warnings']}, {a['id'], b['id']})
        refreshed = cloud.merge_snapshots(previous, [snapshot(b, False)], 'clb', selected_ids=[b['id']])
        self.assertTrue(refreshed['partial'])
        self.assertEqual([w['tenantId'] for w in refreshed['warnings']], [a['id']])
        self.assertEqual(refreshed['tenants'][0]['warnings'][0]['region'], 'cn-shanghai')
        recovered = cloud.merge_snapshots(refreshed, [snapshot(a, False)], 'clb', selected_ids=[a['id']])
        self.assertTrue(recovered['complete'])
        self.assertFalse(recovered['partial'])
        self.assertEqual(recovered['warnings'], [])


if __name__ == '__main__': unittest.main()
