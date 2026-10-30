import copy
import os
import unittest
import deployment_catalog as db
import nginx_catalog as n
from test_nginx_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class NginxMysqlTests(unittest.TestCase):
    def setUp(self):
        self.store=db.Catalog.from_environment();self.store.source_key='nginx-test-'+db.identifier();self.a,self.b=fixture()
    def test_create_rollback_idempotence_and_endpoint_evidence(self):
        with self.assertRaises(db.CatalogError):n.import_report(self.store,self.a,self.b,'v1',can_apply=lambda:False)
        self.assertEqual(self.store.read()['summary']['units'],0)
        result=n.import_report(self.store,self.a,self.b,'v1');self.assertTrue(result['units'][0]['name'].startswith('Nginx_cluster_'))
        listing=self.store.read(query='Nginx_cluster_');self.assertEqual(listing['summary']['units'],1)
        unit=listing['units'][0];self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
        row=self.store.read(unit_id=unit['id'],environment='UNIDENTIFIED')['instances'][0]
        self.assertIsNone(row['port']);self.assertEqual(len(row['attributes']['observedEndpoints']),2)
        self.assertEqual(row['attributes']['groupingBasis'],'USER_PER_INSTANCE_POLICY')
        self.assertTrue(n.import_report(self.store,self.a,self.b,'v1')['unchanged'])
        later=copy.deepcopy(self.a);later['completedAt']='2026-10-29T00:02:00Z'
        self.assertTrue(n.import_report(self.store,later,self.b,'v1')['units'][0]['existing'])
        self.assertEqual(self.store.read()['summary']['units'],1)
