import copy
import os
import unittest
import deployment_catalog as db
import zookeeper_catalog as z
from test_zookeeper_catalog import sample


@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST') == '1', 'requires isolated MySQL')
class ZooKeeperMySQLTest(unittest.TestCase):
    def setUp(self):
        self.store=db.Catalog.from_environment()
        self.store.source_key='zk-test-'+db.identifier()
        self.report,self.listeners=sample()
        self.report['completedAt']='2026-10-29T00:00:00Z'

    def test_create_repeat_read_and_transaction_rollback(self):
        with self.assertRaises(db.CatalogError):
            z.import_report(self.store,self.report,self.listeners,'v1',can_apply=lambda: False)
        self.assertEqual(self.store.read()['summary']['units'],0)
        result=z.import_report(self.store,self.report,self.listeners,'v1')
        unit=result['units'][0]
        self.assertTrue(unit['name'].startswith('Zookeeper_cluster_'))
        listing=self.store.read()
        self.assertEqual(listing['summary']['units'],1)
        self.assertEqual(listing['units'][0]['environments'][0]['environment'],'UNIDENTIFIED')
        details=self.store.read(unit_id=listing['units'][0]['id'],environment='UNIDENTIFIED')
        self.assertEqual(details['instances'][0]['ip'],'192.0.2.1')
        self.assertTrue(z.import_report(self.store,self.report,self.listeners,'v1')['unchanged'])
        updated=copy.deepcopy(self.report);updated['completedAt']='2026-10-29T00:02:00Z'
        result=z.import_report(self.store,updated,self.listeners,'v1')
        self.assertTrue(result['units'][0]['existing'])
        self.assertEqual(result['units'][0]['name'],unit['name'])
        self.assertEqual(self.store.read()['summary']['units'],1)
