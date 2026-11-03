import copy
from datetime import datetime, timezone
import unittest
from runtime import kafka_catalog as k


class KafkaCatalogPlanTest(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc).isoformat()
        proc = '123 (java) 456'
        self.row = {'assetId': 'a', 'ip': '10.0.0.1', 'pid': 123, 'startedAt': now,
                    'processStatus': 'complete', 'hostDeployments': [],
                    'initial': {'BEFORE': proc, 'AFTER': proc, 'MAIN': 'MATCH', 'ARG': 'MATCH',
                                'OVERRIDE_COUNT': '0', 'CONFIG_READ': 'YES', 'configOutputComplete': True,
                                'checkedAt': now, 'fields': {'zookeeper.connect': 'zk:2181/kafka', 'broker.id': '0'},
                                'listeners': ['LISTEN 0 50 10.0.0.1:9092 *:* users:(("java",pid=123,fd=1))']},
                    'identity': {'BEFORE': proc, 'AFTER': proc, 'checkedAt': now,
                                 'STAT': str(int(datetime.now(timezone.utc).timestamp()) - 5), 'meta': {'broker.id': '0'}},
                    'zk': {'status': 'OBSERVED', 'clusterCode': 0, 'brokersCode': 0, 'clusterId': 'cluster-one',
                           'checkedAt': now, 'brokers': [{'id': '0', 'code': 0, 'host': '10.0.0.1', 'port': 9092}]},
                    'metadata': {'status': 'OBSERVED', 'checkedAt': now, 'brokers': [{'id': 0, 'host': '10.0.0.1', 'port': 9092}]}}
        self.report = {'sourceVersion': 'v1', 'ruleVersion': k.RULE, 'results': [self.row]}

    def test_independent_cluster_views_allow_single(self):
        self.assertEqual(len(k.plan(self.report)['groups']), 1)

    def test_metadata_and_zookeeper_must_agree(self):
        self.row['metadata']['brokers'][0]['id'] = 3
        self.assertEqual(len(k.plan(self.report)['groups']), 0)

    def test_multiple_brokers_are_not_misclassified_single(self):
        self.row['zk']['brokers'].append({'id': '3', 'code': 0, 'host': '10.0.0.2', 'port': 9092})
        self.assertEqual(len(k.plan(self.report)['groups']), 0)

    def test_multi_broker_requires_each_peer_view_and_retains_unresolved_member(self):
        peer = {'id': '3', 'code': 0, 'host': '10.0.0.2', 'port': 9092}
        self.row['zk']['brokers'].append(peer)
        view = {'id': 3, 'host': '10.0.0.2', 'port': 9092}
        self.row['metadata']['brokers'].append(view)
        self.assertEqual(len(k.plan(self.report)['groups']), 0)
        self.row['peerMetadata'] = [{'ip': '10.0.0.2', 'port': 9092, 'status': 'OBSERVED',
                                     'checkedAt': self.row['metadata']['checkedAt'],
                                     'brokers': copy.deepcopy(self.row['metadata']['brokers'])}]
        groups = k.plan(self.report)['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['unresolvedMembers'][0]['ip'], '10.0.0.2')
        self.row['peerMetadata'][0]['brokers'].pop()
        self.assertEqual(len(k.plan(self.report)['groups']), 0)

    def test_same_zookeeper_different_cluster_not_merged(self):
        other = copy.deepcopy(self.row)
        other.update(assetId='b', ip='10.0.0.2', pid=124)
        other['initial']['BEFORE'] = other['initial']['AFTER'] = '124 (java) 456'
        other['identity']['BEFORE'] = other['identity']['AFTER'] = '124 (java) 456'
        other['initial']['listeners'] = ['LISTEN 0 50 10.0.0.2:9092 *:* users:(("java",pid=124,fd=1))']
        other['zk']['clusterId'] = 'cluster-two'
        other['zk']['brokers'][0]['host'] = '10.0.0.2'
        other['metadata']['brokers'][0]['host'] = '10.0.0.2'
        self.report['results'].append(other)
        self.assertEqual(len(k.plan(self.report)['groups']), 2)
        other['zk']['clusterId'] = 'cluster-one'
        self.assertEqual(len(k.plan(self.report)['groups']), 0)

    def test_unknown_app_scope_and_stale_evidence_block(self):
        self.row['hostDeployments'] = [{'name': 'app'}]
        self.assertEqual(len(k.plan(self.report)['groups']), 0)
        self.row['hostDeployments'] = []
        self.row['metadata']['checkedAt'] = '2020-01-01T00:00:00+00:00'
        self.assertEqual(len(k.plan(self.report)['groups']), 0)

    def test_container_requires_published_port(self):
        now = self.row['initial']['checkedAt']
        self.row['container'] = {'INNER_CONFIG': 'YES', 'PROC': '123 (java) 456', 'PROC_AFTER': '123 (java) 456',
                                 'BEFORE': 'true 123 0', 'AFTER': 'true 123 0', 'CONFIG': self.row['initial']['fields'],
                                 'checkedAt': now, 'publishedPorts': []}
        self.assertEqual(len(k.plan(self.report)['groups']), 0)
        self.row['container']['publishedPorts'] = [{'containerPort': '9092/tcp', 'hostPort': '9092', 'hostIp': '0.0.0.0'}]
        self.assertEqual(len(k.plan(self.report)['groups']), 1)


if __name__ == '__main__':
    unittest.main()
