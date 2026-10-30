import copy
import os
import unittest
import deployment_catalog as db
import kong_catalog as k
from test_kong_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class KongMysqlTests(unittest.TestCase):
    def setUp(self):
        self.store=db.Catalog.from_environment();self.store.source_key='kong-test-'+db.identifier();self.a,self.b,self.c=fixture()
    def test_transaction_rollback_repeat_and_pending_group(self):
        with self.assertRaises(db.CatalogError):k.import_report(self.store,self.a,self.b,self.c,'v1',can_apply=lambda:False)
        self.assertEqual(self.store.read()['summary']['units'],0)
        result=k.import_report(self.store,self.a,self.b,self.c,'v1');self.assertTrue(result['units'][0]['name'].startswith('Kong_cluster_'))
        unit=self.store.read(query='Kong_cluster_')['units'][0];self.assertEqual(unit['groupingState'],'PENDING');self.assertEqual(unit['environments'][0]['environment'],'UNIDENTIFIED')
        instance=self.store.read(unit_id=unit['id'],environment='UNIDENTIFIED')['instances'][0]
        self.assertEqual(instance['attributes']['groupingState'],'PENDING');self.assertEqual(len(instance['attributes']['observedEndpoints']),2)
        self.assertTrue(k.import_report(self.store,self.a,self.b,self.c,'v1')['unchanged'])
        newer=copy.deepcopy(self.a);newer['completedAt']='2026-10-30T00:05:00Z'
        self.assertTrue(k.import_report(self.store,newer,self.b,self.c,'v1')['units'][0]['existing'])
        conflict=copy.deepcopy(newer);conflict['completedAt']='2026-10-30T00:06:00Z';conflict['results'][0]['admin']['node_id']='11111111-1111-1111-1111-111111111111'
        with self.assertRaises(db.CatalogError):k.import_report(self.store,conflict,self.b,self.c,'v1')
        self.assertEqual(self.store.read()['summary']['units'],1)
