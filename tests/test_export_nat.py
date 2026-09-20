import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from export_nat import collect, normalize_nat, paged, NatClient


def page(rows, total=None, outer='NatGateways', inner='NatGateway'):
    return {'TotalCount': len(rows) if total is None else total, outer: {inner: rows}}


class NatTests(unittest.TestCase):
    def test_pagination_and_duplicate_or_changed_counts(self):
        client = Mock()
        client.call.side_effect = [page([{'id': '1'}], 2), page([{'id': '2'}], 2)]
        self.assertEqual(len(paged(client, 'DescribeNatGateways', 'NatGateways', 'NatGateway', 'id')), 2)
        self.assertEqual(client.call.call_args[0][1]['PageNumber'], 2)
        for responses in ([page([{'id': '1'}], 2), page([{'id': '1'}], 2)],
                          [page([{'id': '1'}], 2), page([], 3)], [page([], 1)], [{}]):
            client.call.side_effect = responses
            with self.assertRaises(ValueError):
                paged(client, 'DescribeNatGateways', 'NatGateways', 'NatGateway', 'id')

    def test_all_tables_and_zero_rules_preserve_any_and_ranges(self):
        gateway = {'NatGatewayId': 'ngw', 'ForwardTableIds': {'ForwardTableId': ['table1', 'table2']}}
        rule = dict(ForwardEntryId='rule', ExternalIp='1.2.3.4', ExternalPort='Any',
                    InternalIp='10.0.0.1', InternalPort='Any', IpProtocol='Any', Status='Available')
        client = Mock()
        client.call.side_effect = [page([gateway]), page([rule], outer='ForwardTableEntries', inner='ForwardTableEntry'),
                                  page([], outer='ForwardTableEntries', inner='ForwardTableEntry'), page([gateway])]
        with tempfile.TemporaryDirectory() as folder:
            raw = collect('', Path(folder), client)
        normalized = normalize_nat(raw)
        entry = normalized['gateways'][0]['entries'][0]
        self.assertEqual(entry['externalPort'], 'Any')
        self.assertEqual(entry['tableId'], 'table1')
        raw['gateways'][0]['entries'][0].update(ExternalPort='80/90', InternalPort='8080/8090')
        self.assertEqual(normalize_nat(raw)['gateways'][0]['entries'][0]['internalPort'], '8080/8090')

    def test_failure_and_table_changes_never_save_complete_export(self):
        gateway = {'NatGatewayId': 'ngw', 'ForwardTableIds': {'ForwardTableId': []}}
        changed = copy.deepcopy(gateway)
        changed['ForwardTableIds']['ForwardTableId'] = ['new-table']
        client = Mock()
        client.call.side_effect = [page([gateway]), page([changed])]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): collect('', Path(folder), client)
            self.assertFalse((Path(folder) / 'nat-all.json').exists())
        with self.assertRaises(ValueError): normalize_nat({'complete': False})
        with self.assertRaises(ValueError):
            NatClient.__new__(NatClient).call('CreateForwardEntry')

    def test_empty_inventory_is_explicitly_successful(self):
        client = Mock()
        client.call.return_value = page([])
        with tempfile.TemporaryDirectory() as folder:
            result = normalize_nat(collect('', Path(folder), client))
        self.assertTrue(result['available'])
        self.assertEqual(result['gateways'], [])


if __name__ == '__main__': unittest.main()
