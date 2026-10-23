import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cloud_dns import collect_dns_ak, pages
from export_devops import ExportError
import tenant_collection as cloud


class Client:
    def __init__(self, data):
        self.data, self.calls = data, []

    def call(self, action, params):
        self.calls.append((action, params))
        return deepcopy(self.data[action])


class CloudDnsTests(unittest.TestCase):
    def collect(self, public, private):
        with tempfile.TemporaryDirectory() as tmp:
            return collect_dns_ak('key', 'secret', Path(tmp),
                                  lambda key, secret, host, version: private if host.startswith('pvtz') else public)

    def fixture(self):
        public = Client({
            'DescribeDomains': {'TotalCount': 2, 'Domains': {'Domain': [
                {'DomainId': 'zero', 'DomainName': 'empty.example.cn', 'RecordCount': 0},
                {'DomainId': 'shared', 'DomainName': 'example.cn', 'RecordCount': 1}]}},
            'DescribeDomainRecords': {'TotalCount': 1, 'DomainRecords': {'Record': [
                {'RecordId': 'same', 'RR': '@', 'Type': 'A', 'Value': '192.0.2.1', 'Line': 'telecom',
                 'Status': 'DISABLE', 'TTL': 600, 'Weight': 0, 'Remark': 'paused'}]}},
        })
        private = Client({
            'DescribeZones': {'TotalItems': 2, 'Zones': {'Zone': [
                {'ZoneId': 'private-zero', 'ZoneName': 'empty.example.cn', 'RecordCount': 0},
                {'ZoneId': 'shared', 'ZoneName': 'example.cn', 'RecordCount': 1}]}},
            'DescribeZoneRecords': {'TotalItems': 1, 'Records': {'Record': [
                {'RecordId': 'same', 'Rr': 'api', 'Type': 'AAAA', 'Value': '2001:db8::1',
                 'Status': 'ENABLE', 'Ttl': 60, 'Weight': 7}]}},
        })
        return public, private

    def test_two_levels_zero_details_skipped_and_fields_preserved(self):
        public, private = self.fixture()
        result = self.collect(public, private)
        self.assertEqual((result['zoneCount'], result['recordCount']), (4, 2))
        self.assertEqual([p for a, p in public.calls if a == 'DescribeDomainRecords'],
                         [{'DomainName': 'example.cn', 'PageNumber': 1, 'PageSize': 100}])
        self.assertEqual([p['ZoneId'] for a, p in private.calls if a == 'DescribeZoneRecords'], ['shared'])
        paused = result['zones'][1]['records'][0]
        self.assertEqual((paused['name'], paused['status'], paused['weight'], paused['line'], paused['ttl']),
                         ('example.cn', '暂停', 0, 'telecom', 600))
        self.assertEqual(result['zones'][0]['skipReason'], 'zero-records')
        self.assertEqual(result['zones'][3]['records'][0]['name'], 'api.example.cn')

    def test_missing_count_or_incomplete_details_fails(self):
        for mutate in ('missing', 'mismatch'):
            public, private = self.fixture()
            zone = public.data['DescribeDomains']['Domains']['Domain'][0]
            if mutate == 'missing': zone.pop('RecordCount')
            else: zone['RecordCount'] = 2
            with self.subTest(mutate=mutate), self.assertRaises(ExportError):
                self.collect(public, private)

    def test_pagination_checks_duplicates_and_changing_total(self):
        class Paged:
            def call(self, action, params):
                page = params['PageNumber']
                return {'TotalCount': 2, 'Items': {'Item': [{'Id': str(page)}]}}
        self.assertEqual(len(pages(Paged(), 'Read', 'Items', 'Item', 'TotalCount', 'Id')), 2)
        client = Client({'Read': {'TotalCount': 2, 'Items': {'Item': [{'Id': 'duplicate'}]}}})
        with self.assertRaises(ExportError): pages(client, 'Read', 'Items', 'Item', 'TotalCount', 'Id')

    def test_nested_provenance_and_tenant_replacement_include_zero_domains(self):
        public, private = self.fixture()
        raw = self.collect(public, private)
        tenant = {'id': 'a', 'name': '账号 A', 'accountId': '1001', 'auth': {'mode': 'accesskey'}}
        a = cloud.scoped_snapshot(raw, tenant, 'cloud-dns', '')
        tenant['id'] = 'b'
        b = cloud.scoped_snapshot(raw, tenant, 'cloud-dns', '')
        all_keys = [r['resourceKey'] for part in (a, b) for z in part['zones'] for r in z['records']]
        self.assertEqual(len(set(all_keys)), 4)
        combined = cloud.merge_snapshots({}, [a, b], 'cloud-dns', True)
        empty = cloud.scoped_snapshot({**raw, 'zones': []}, {**tenant, 'id': 'a'}, 'cloud-dns', '')
        merged = cloud.merge_snapshots(combined, [empty], 'cloud-dns', selected_ids=['a'])
        self.assertEqual(len(merged['zones']), 4)
        self.assertTrue(all(z['tenantId'] == 'b' for z in merged['zones']))
        self.assertEqual(merged['recordCount'], 2)

    def test_dns_is_collected_once_per_tenant_independent_of_regions(self):
        from test_tenants import saved_accounts
        saved = saved_accounts()
        saved['aliyunTenants']['tenants'][0]['regions'].append('cn-hangzhou')
        with tempfile.TemporaryDirectory() as tmp, patch.object(cloud, 'verify'):
            tasks, contexts, selected = cloud.build_tasks(saved, Path(tmp), 'cloud-dns')
        self.assertEqual(len(tasks), 2)
        self.assertEqual({c['region'] for c in contexts.values()}, {''})


if __name__ == '__main__':
    unittest.main()
