import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service
import aliyun_tenants as tenancy


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); self.data = root/'data'; self.app = root/'app'
        for key, val in [('DATA', self.data), ('APP', self.app)]:
            mock = patch.object(service, key, val); mock.start(); self.addCleanup(mock.stop)
        fixtures = {'snapshot.json': {'apps': [{'id': 'app'}]}, 'repositories.json': {'repos': [{'id': 'repo'}]},
                    'ecs-snapshot.json': {'instances': []},
                    'jumpserver-snapshot.json': {'assets': [{'id': 'asset'}], 'groups': [{'id': 'group', 'assetIds': ['asset']}]}}
        for name, data in fixtures.items(): service.write(self.app/'lib'/name, data)
        self.svc = service.Service(); self.svc.backend = {'port': 1}
        self.svc.save_settings({'cookies': {'devops': 'devops-private', 'jumpserver': 'jump-private', 'local_gitlab': 'git-private'},
                                'aliyunAccessKey': {'mode': 'accesskey', 'accessKeyId': 'key-a', 'accessKeySecret': 'secret-a'}})
        with patch.object(tenancy, 'identity', return_value='2002'):
            self.svc.save_settings({'aliyunTenants': [{'name': '新增租户', 'accessKeyId': 'key-b', 'accessKeySecret': 'secret-b'}]})
        self.tenant = self.svc.status()['aliyunTenants'][1]

    def test_tenants_and_secrets_survive_restart_without_public_credential_exposure(self):
        status = service.Service().status()
        self.assertEqual(len(status['aliyunTenants']), 2)
        self.assertEqual(status['codeupTenantId'], tenancy.LEGACY_TENANT)
        for secret in ('secret-a', 'secret-b', 'devops-private', 'jump-private'):
            self.assertNotIn(secret, json.dumps(status))
        self.assertEqual((self.data/'secrets/cookies.json').stat().st_mode & 0o777, 0o600)

    def test_rejected_identity_change_never_changes_saved_configuration(self):
        before = (self.data/'secrets/cookies.json').read_bytes()
        with patch.object(tenancy, 'identity', return_value='3003'), self.assertRaises(ValueError):
            self.svc.save_settings({'aliyunTenants': [{'id': self.tenant['id'], 'credentialVersion': self.tenant['credentialVersion'],
                                                      'accessKeyId': 'other-key', 'accessKeySecret': 'other-secret'}]})
        self.assertEqual((self.data/'secrets/cookies.json').read_bytes(), before)

    def test_job_freezes_credentials_and_manifest_has_no_secrets(self):
        with patch.object(service.threading.Thread, 'start'):
            identifier = self.svc.start('ecs', tenant_id=self.tenant['id'])
        self.svc.save_settings({'aliyunTenants': [{'id': self.tenant['id'], 'credentialVersion': self.tenant['credentialVersion'], 'name': '已改名'}]})
        frozen = self.svc.collection_inputs[identifier]
        self.assertEqual(frozen['aliyunTenants']['tenants'][1]['name'], '新增租户')
        self.assertEqual(frozen['_tenantId'], self.tenant['id'])
        report = service.read(self.data/'versions'/identifier/'version.json')
        self.assertEqual(report['cloudPlan'][0]['sources'], ['ecs'])
        self.assertNotIn('secret-b', json.dumps(report))

    def test_dns_validation_checks_nested_records_and_planned_tenants(self):
        from tenant_collection import scoped_snapshot
        tenant = tenancy.select(service.credentials(), 'cloud-dns')[0]
        folder = self.data/'versions'/service.read(self.data/'current.json')['id']
        raw = {'collectedAt': '2026-10-23T01:00:00Z', 'zones': [
            {'id': 'zone', 'name': 'example.cn', 'visibility': 'public', 'recordCount': 1, 'records': [{'id': 'record', 'visibility': 'public'}]}]}
        snapshot = scoped_snapshot(raw, tenant, 'cloud-dns', '')
        service.write(folder/'cloud-dns-snapshot.json', snapshot)
        plan = {'tenantId': tenant['id'], 'sources': ['cloud-dns'], 'regions': tenant['regions'],
                'credentialVersion': tenant['credentialVersion']}
        service.write(folder/'version.json', {'cloudPlan': [plan]})
        self.assertEqual(service.validate(folder)['dnsRecords'], 1)
        snapshot['zones'][0]['records'][0]['tenantId'] = 'wrong-account'
        service.write(folder/'cloud-dns-snapshot.json', snapshot)
        with self.assertRaisesRegex(ValueError, '范围不一致'): service.validate(folder)
        service.write(folder/'version.json', {'cloudPlan': [plan, {**plan, 'tenantId': 'missing'}]})
        with self.assertRaisesRegex(ValueError, '缺少计划'): service.validate(folder)

    def test_tenant_editor_connection_is_preserved_when_general_settings_are_saved(self):
        original = self.svc.status()['aliyunTenants'][0]
        with patch.object(tenancy, 'identity', return_value='1001'):
            self.svc.save_settings({'aliyunTenants': [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                    'mode': 'accesskey', 'accessKeyId': 'updated-key', 'accessKeySecret': 'updated-secret',
                                    'codeupOrganizationId': 'organization-1'}]})
        self.svc.save_settings({'cookies': {'devops': 'new-devops-cookie'}, 'schedule': {'enabled': False, 'time': '18:00'}})
        saved = service.credentials()
        self.assertEqual(tenancy.value(saved, 'aliyun_accesskey_id'), 'updated-key')
        self.assertEqual(tenancy.value(saved, 'aliyun_accesskey_secret'), 'updated-secret')
        self.assertEqual(tenancy.value(saved, 'aliyun_auth_mode'), 'accesskey')
        self.assertEqual(self.svc.status()['aliyunTenants'][0]['codeupOrganizationId'], 'organization-1')
        self.assertNotIn('updated-secret', json.dumps(self.svc.status()))

    def test_legacy_credential_alert_moves_with_the_editor_and_clears_on_rotation(self):
        service.write(self.data/'cookie-alerts.json', {'aliyun_accesskey_id': {'message': '旧 AccessKey 错误'}})
        original = self.svc.status()['aliyunTenants'][0]
        self.assertEqual(original['alert']['message'], '旧 AccessKey 错误')
        with patch.object(tenancy, 'identity', return_value='1001'):
            self.svc.save_settings({'aliyunTenants': [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                                     'accessKeySecret': 'rotated-secret'}]})
        self.assertIsNone(self.svc.status()['aliyunTenants'][0]['alert'])
        original = self.svc.status()['aliyunTenants'][0]
        self.svc.save_settings({'aliyunTenants': [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                                 'mode': 'cookie', 'cookie': 'session=old'}]})
        service.write(self.data/'cookie-alerts.json', {'aliyun': {'message': '旧 Cookie 错误'}})
        original = self.svc.status()['aliyunTenants'][0]
        self.assertEqual(original['alert']['message'], '旧 Cookie 错误')
        self.svc.save_settings({'aliyunTenants': [{'id': original['id'], 'credentialVersion': original['credentialVersion'],
                                                 'cookie': 'session=new'}]})
        self.assertIsNone(self.svc.status()['aliyunTenants'][0]['alert'])

    def test_missing_tenant_scope_or_copied_old_data_cannot_publish(self):
        with patch.object(service.threading.Thread, 'start'):
            identifier = self.svc.start('ecs', tenant_id=self.tenant['id'])
        with patch.object(service.subprocess, 'run', return_value=type('Result', (), {'returncode': 0})()), \
             patch.object(self.svc, 'activate') as activate:
            self.svc.work('ecs', identifier)
        self.assertFalse(activate.called)
        self.assertEqual(self.svc.job['status'], 'failed')

    def test_tenant_wide_sync_plans_four_cloud_sources_without_codeup(self):
        with patch.object(service.threading.Thread, 'start'):
            identifier = self.svc.start('aliyun', tenant_id=self.tenant['id'])
        report = service.read(self.data/'versions'/identifier/'version.json')
        self.assertEqual(report['cloudPlan'][0]['sources'], list(tenancy.CLOUD_SOURCES))
        self.assertEqual(len(report['cloudPlan']), 1)
        self.assertNotIn('codeup', report['cloudPlan'][0]['sources'])

    def test_completed_cloud_scope_keeps_other_tenants_and_publishes_atomically(self):
        from tenant_collection import scoped_snapshot, merge_snapshots
        saved = service.credentials(); a, b = saved['aliyunTenants']['tenants']
        def snap(t, ip): return scoped_snapshot({'fetchedAt': '2026-10-23T01:00:00Z', 'instances': [{'id': 'same', 'privateIps': [ip]}], 'projects': []}, t, 'ecs', 'cn-shanghai')
        current = self.svc.version(self.svc.status()['current']['id'])
        service.write(current/'ecs-snapshot.json', merge_snapshots({}, [snap(a, '10.1.0.1'), snap(b, '10.2.0.1')], 'ecs', True))
        with patch.object(service.threading.Thread, 'start'):
            identifier = self.svc.start('ecs', tenant_id=b['id'])
        folder = self.data/'versions'/identifier
        def collect(*args, **kwargs):
            old = service.read(folder/'base/ecs-snapshot.json')
            fresh = snap(b, '10.2.0.2')
            service.write(folder/'ecs-snapshot.json', merge_snapshots(old, [fresh], 'ecs', selected_ids=[b['id']]))
            return type('Result', (), {'returncode': 0})()
        def activate(_):
            self.assertEqual(service.read(folder/'version.json')['status'], 'collecting')
        with patch.object(service.subprocess, 'run', side_effect=collect), patch.object(self.svc, 'activate', side_effect=activate):
            self.svc.work('ecs', identifier)
        self.assertEqual(self.svc.job['status'], 'succeeded')
        rows = service.read(folder/'ecs-snapshot.json')['instances']
        self.assertEqual([r['privateIps'][0] for r in rows], ['10.1.0.1', '10.2.0.2'])

    def test_old_tenant_warning_cannot_override_rotated_credentials(self):
        folder = self.data/'versions/failure'
        failure = {'tenantId': self.tenant['id'], 'credentialVersion': self.tenant['credentialVersion'], 'message': '凭据无效'}
        service.write(folder/'collection-error.json', {'failures': [failure]})
        self.svc.collection_error(folder)
        self.assertIsNotNone(self.svc.status()['aliyunTenants'][1]['alert'])
        with patch.object(tenancy, 'identity', return_value='2002'):
            self.svc.save_settings({'aliyunTenants': [{'id': self.tenant['id'], 'credentialVersion': self.tenant['credentialVersion'], 'accessKeySecret': 'new-secret'}]})
        self.svc.collection_error(folder)
        self.assertIsNone(self.svc.status()['aliyunTenants'][1]['alert'])

    def record_tenant_failure(self, tenant=None, identifier='20261023-100000-000001', source='CLB'):
        tenant = tenant or self.tenant
        folder = self.data/'versions'/identifier
        failure = {'tenantId': tenant['id'], 'credentialVersion': tenant['credentialVersion'],
                   'source': source, 'region': 'cn-shanghai', 'message': f'{source} 采集失败'}
        service.write(folder/'version.json', {'id': identifier, 'kind': 'sync', 'status': 'failed', 'createdAt': service.now()})
        service.write(folder/'collection-error.json', {'failures': [failure]})
        self.svc.collection_error(folder)
        return folder, failure

    def activate_tenant_success(self, tenant=None, identifier='20261023-110000-000002',
                                source='clb', region='cn-shanghai', credential=None, complete=True,
                                source_version=None, status='ready', active=True, planned_source=None):
        tenant = tenant or self.tenant
        credential = credential or tenant['credentialVersion']
        folder = self.data/'versions'/identifier
        plan = {'tenantId': tenant['id'], 'credentialVersion': credential, 'regions': [region],
                'sources': [planned_source or source]}
        service.write(folder/'version.json', {'id': identifier, 'kind': source, 'status': status,
                                             'createdAt': service.now(), 'cloudPlan': [plan]})
        service.write(folder/f'{source}-snapshot.json', {'available': True, 'schemaVersion': 2, 'instances': [],
                      'tenants': [{'tenantId': tenant['id'], 'credentialVersion': credential, 'region': region,
                                   'complete': complete, 'sourceVersion': source_version or identifier}]})
        if active: service.write(self.data/'current.json', {'id': identifier})
        return folder

    def test_successful_same_scope_clears_persisted_tenant_alert(self):
        self.record_tenant_failure()
        alert = service.read(self.data/'tenant-alerts.json')[self.tenant['id']]
        self.assertEqual((alert['source'], alert['region'], alert['versionId']),
                         ('CLB', 'cn-shanghai', '20261023-100000-000001'))
        self.activate_tenant_success()
        self.svc._reconcile_tenant_alerts()
        self.assertIsNone(self.svc.status()['aliyunTenants'][1]['alert'])
        self.assertEqual(service.read(self.data/'tenant-alerts.json'), {})
        self.assertIsNone(service.Service().status()['aliyunTenants'][1]['alert'])

    def test_unrelated_stale_or_unactivated_snapshot_cannot_clear_tenant_alert(self):
        initial = service.read(self.data/'current.json')
        cases = [
            {'source': 'ecs'}, {'region': 'cn-beijing'}, {'credential': 'other-revision'},
            {'complete': False}, {'source_version': '20261023-100000-000001'},
            {'status': 'collecting'}, {'active': False}, {'planned_source': 'ecs'},
        ]
        for index, options in enumerate(cases):
            with self.subTest(options=options):
                service.write(self.data/'current.json', initial)
                self.record_tenant_failure()
                self.activate_tenant_success(identifier=f'20261023-11000{index}-000002', **options)
                self.svc._reconcile_tenant_alerts()
                self.assertIsNotNone(self.svc.status()['aliyunTenants'][1]['alert'])

    def test_success_clears_only_the_recovered_tenant(self):
        original = service.Service().status()['aliyunTenants'][0]
        self.record_tenant_failure(tenant=original)
        self.record_tenant_failure()
        self.activate_tenant_success()
        self.svc._reconcile_tenant_alerts()
        self.assertEqual(set(service.read(self.data/'tenant-alerts.json')), {original['id']})

    def test_restart_recovers_legacy_alert_scope_from_original_failure(self):
        _, failure = self.record_tenant_failure()
        legacy = {k: failure[k] for k in ('message', 'credentialVersion')}
        service.write(self.data/'tenant-alerts.json', {self.tenant['id']: legacy})
        self.activate_tenant_success()
        self.assertIsNone(service.Service().status()['aliyunTenants'][1]['alert'])
        self.assertEqual(service.read(self.data/'tenant-alerts.json'), {})

    def test_unknown_legacy_alert_is_kept_without_recovery_evidence(self):
        legacy = {'message': '未知历史错误', 'credentialVersion': self.tenant['credentialVersion']}
        service.write(self.data/'tenant-alerts.json', {self.tenant['id']: legacy})
        self.activate_tenant_success()
        self.assertEqual(service.Service().status()['aliyunTenants'][1]['alert'], legacy)

    def test_late_old_failure_cannot_resurrect_a_recovered_alert(self):
        self.activate_tenant_success()
        self.record_tenant_failure()
        self.assertIsNone(self.svc.status()['aliyunTenants'][1]['alert'])

    def test_new_failure_is_not_cleared_by_older_success(self):
        self.activate_tenant_success()
        self.record_tenant_failure(identifier='20261023-120000-000003')
        self.assertIsNotNone(self.svc.status()['aliyunTenants'][1]['alert'])

    def test_late_old_failure_does_not_replace_newer_unresolved_alert(self):
        self.record_tenant_failure(identifier='20261023-120000-000003', source='NAT')
        self.activate_tenant_success()
        self.record_tenant_failure()
        alert = self.svc.status()['aliyunTenants'][1]['alert']
        self.assertEqual(alert['source'], 'NAT')
        self.assertEqual(alert['versionId'], '20261023-120000-000003')

    def test_clb_instance_warnings_allow_successful_activation(self):
        from tenant_collection import scoped_snapshot, merge_snapshots
        tenant = service.credentials()['aliyunTenants']['tenants'][1]
        self.record_tenant_failure(identifier='20200101-000000-000001')
        with patch.object(service.threading.Thread, 'start'):
            identifier = self.svc.start('clb', tenant_id=tenant['id'])
        folder = self.data/'versions'/identifier
        warning = {'loadBalancerId': 'lb-skipped', 'status': 'active', 'skipped': True,
                   'action': 'DescribeHealthStatus', 'params': {'LoadBalancerId': 'lb-skipped'},
                   'reason': 'request_failed', 'error': '网络超时或连接异常'}
        def collect(*args, **kwargs):
            snapshot = scoped_snapshot({'available': True, 'collectedAt': '2026-10-23T01:00:00Z',
                                        'instances': [], 'partial': True, 'warnings': [warning]},
                                       tenant, 'clb', 'cn-shanghai')
            snapshot['tenants'][0]['sourceVersion'] = identifier
            service.write(folder/'clb-snapshot.json', merge_snapshots({}, [snapshot], 'clb', True))
            return type('Result', (), {'returncode': 0})()
        def activate_version(identifier):
            self.assertIsNotNone(self.svc.status()['aliyunTenants'][1]['alert'])
            service.write(self.data/'current.json', {'id': identifier})
        with patch.object(service.subprocess, 'run', side_effect=collect), \
                patch.object(self.svc, 'activate', side_effect=activate_version) as activate:
            self.svc.work('clb', identifier)
        activate.assert_called_once_with(identifier)
        self.assertEqual(self.svc.job['status'], 'succeeded')
        self.assertEqual(service.read(folder/'version.json')['status'], 'ready')
        snapshot = service.read(folder/'clb-snapshot.json')
        self.assertTrue(snapshot['partial'])
        self.assertEqual(snapshot['warnings'][0]['tenantId'], tenant['id'])
        self.assertEqual(snapshot['tenants'][0]['warnings'], snapshot['warnings'])
        self.assertIsNone(self.svc.status()['aliyunTenants'][1]['alert'])


if __name__ == '__main__': unittest.main()
