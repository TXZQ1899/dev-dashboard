import copy
import os
import unittest
import deployment_catalog as db
import nacos_catalog as n
from test_nacos_catalog import fixture


@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST') == '1', 'requires isolated MySQL')
class NacosMySQLTests(unittest.TestCase):
    def setUp(self):
        self.store=db.Catalog.from_environment()
        self.store.source_key='nacos-test-'+db.identifier()
        self.report,self.listeners=fixture()

    def test_creation_rollback_idempotence_and_queries(self):
        with self.assertRaises(db.CatalogError):n.import_report(self.store,self.report,self.listeners,'v1',can_apply=lambda:False)
        self.assertEqual(self.store.read()['summary']['units'],0)
        result=n.import_report(self.store,self.report,self.listeners,'v1')
        self.assertTrue(result['units'][0]['name'].startswith('Nacos_cluster_'))
        listing=self.store.read(query='Nacos_cluster_')
        self.assertEqual(listing['summary']['units'],1)
        unit=listing['units'][0]
        self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
        details=self.store.read(unit_id=unit['id'],environment='UNIDENTIFIED')['instances']
        self.assertEqual((details[0]['ip'],details[0]['port']),('192.0.2.10','8083'))
        self.assertEqual(details[0]['attributes']['portBasis'],'DOCKER_PUBLISHED')
        self.assertTrue(n.import_report(self.store,self.report,self.listeners,'v1')['unchanged'])
        later=copy.deepcopy(self.report);later['completedAt']='2026-10-29T00:02:00Z'
        self.assertTrue(n.import_report(self.store,later,self.listeners,'v1')['units'][0]['existing'])
        self.assertEqual(self.store.read()['summary']['units'],1)
