import unittest
from catalog_processes import attach_processes, build_process_observations


class CatalogProcessTests(unittest.TestCase):
    def setUp(self):
        self.row = {'ip': '10.0.0.1', 'environment': 'PRODUCT', 'presenceStatus': 'PRESENT', 'port': '8080'}
        self.app = {'id': 'a', 'name': 'sample', 'envs': {'PRODUCT': [{'ip': '10.0.0.1', 'port': '8080'}]}}
        self.process = {'pid': 5, 'command': 'java -jar /app/sample.jar', 'ports': [9090], 'startedAt': '2026-01-01T00:00:00Z', 'elapsedSeconds': 86401}
        self.inspection = {'loginStatus': 'can_login', 'processStatus': 'complete', 'portStatus': 'complete', 'checkedAt': '2026-01-02T00:00:01Z', 'processes': [self.process]}
        self.asset = {'ip': '10.0.0.1', 'inspection': self.inspection}

    def observe(self, rows=None, assets=None, apps=None, current=True):
        rows = rows if rows is not None else [self.row]
        attach_processes(rows, 'a', apps if apps is not None else [self.app], assets if assets is not None else [self.asset], current)
        return rows[0]['processObservation']

    def test_actual_port_and_process_observation(self):
        result = self.observe()
        self.assertEqual((result['status'], result['pid'], result['ports'], result['elapsedSeconds']), ('MATCHED', 5, [9090], 86401))

    def test_absence_requires_complete_collection(self):
        self.inspection['processes'] = []
        self.assertEqual(self.observe()['status'], 'NOT_FOUND')
        self.inspection['processStatus'] = 'partial'
        self.assertEqual(self.observe()['status'], 'UNKNOWN')
        self.inspection['loginStatus'] = 'cannot_login'
        self.assertEqual(self.observe()['status'], 'UNKNOWN')

    def test_ambiguity_and_historical_evidence(self):
        self.assertEqual(self.observe(assets=[self.asset, self.asset])['status'], 'UNKNOWN')
        self.assertEqual(self.observe(current=False)['status'], 'UNKNOWN')
        self.inspection['processes'].append({**self.process, 'pid': 6})
        self.assertEqual(self.observe()['status'], 'AMBIGUOUS')
        self.row['presenceStatus'] = 'NOT_OBSERVED'
        self.assertEqual(self.observe()['status'], 'UNKNOWN')

    def test_shared_host_deployments_require_unique_port(self):
        other = {**self.row, 'port': '9091'}
        self.assertEqual(self.observe(rows=[self.row, other])['status'], 'AMBIGUOUS')
        self.process['ports'] = [8080]
        self.assertEqual(self.observe(rows=[self.row, other])['status'], 'MATCHED')

    def test_other_application_candidate_remains_ambiguous(self):
        other = {**self.app, 'id': 'b'}
        self.assertEqual(self.observe(apps=[self.app, other])['status'], 'AMBIGUOUS')

    def test_bulk_refresh_preserves_ambiguous_app_candidates(self):
        row = {**self.row, 'id': 'i', 'unitId': 'u', 'appId': 'a'}
        other = {**self.app, 'id': 'b'}
        self.assertEqual(build_process_observations([row], [self.app, other], [self.asset])['i']['status'], 'AMBIGUOUS')


if __name__ == '__main__':
    unittest.main()
