import unittest
try:
    import zookeeper_candidates as zk
except ModuleNotFoundError:
    from runtime import zookeeper_candidates as zk

MAIN = zk.MAIN_CLASS


def asset(command, ports=None):
    return {'id': 'host-1', 'ip': '192.0.2.10', 'inspection': {'processStatus': 'complete',
        'checkedAt': '2026-10-01T00:00:00Z', 'processes': [{'pid': 10, 'startedAt': '2026-09-01T00:00:00Z',
        'command': command, 'ports': ports or []}]}}


class ZookeeperCandidatesTest(unittest.TestCase):
    def test_server_only_and_unresolved_no_created_units(self):
        snapshot = {'assets': [asset('java -cp x '+MAIN+' /etc/zk/zoo.cfg'),
                               asset('java -jar app.jar --zookeeper=192.0.2.10:2181')]}
        result = zk.discover(snapshot, {'apps': []}, 'v1')
        self.assertEqual(len(result['candidates']), 1)
        row = result['candidates'][0]
        self.assertEqual(row['configPathCandidate'], '/etc/zk/zoo.cfg')
        self.assertEqual(row['clusterStatus'], 'UNKNOWN')
        self.assertEqual(row['applicationStatus'], 'UNKNOWN')
        self.assertNotIn('command', row)

    def test_app_matches_from_all_environments_remain_candidates(self):
        app = lambda ident: {'id': ident, 'name': ident, 'envs': {'PRODUCT': [{'ip': '192.0.2.10', 'port': '2181'}]}}
        result = zk.discover({'assets': [asset('java '+MAIN+' /etc/zoo.cfg', [2181])]},
                             {'apps': [app('a'), app('b')]}, 'v1')
        row = result['candidates'][0]
        self.assertEqual(row['applicationStatus'], 'AMBIGUOUS')
        self.assertEqual([x['appId'] for x in row['applicationCandidates']], ['a', 'b'])
        self.assertEqual(row['clusterStatus'], 'UNKNOWN')

    def test_incomplete_coverage_and_missing_config(self):
        result = zk.discover({'assets': [asset('java '+MAIN), {'id': 'missing'}]}, {'apps': []}, 'v')
        self.assertEqual(result['coverage'], {'complete': 1, 'not_collected': 1})
        self.assertIsNone(result['candidates'][0]['configPathCandidate'])
        self.assertIsNone(zk.config_argument('java '+MAIN+' /etc/zoo.cfg extra'))


if __name__ == '__main__':
    unittest.main()
