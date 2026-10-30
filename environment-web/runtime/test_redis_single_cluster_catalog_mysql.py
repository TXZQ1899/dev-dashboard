import os,unittest
import deployment_catalog as db
import redis_single_cluster_catalog as mod
from test_redis_single_cluster_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class SingleClusterMysqlTests(unittest.TestCase):
    def test_atomic_idempotent_import(self):
        s=db.Catalog.from_environment();s.source_key='redis-single-'+db.identifier();a,b,c=fixture()
        with self.assertRaises(db.CatalogError):mod.import_report(s,a,b,c,'v',can_apply=lambda:False)
        self.assertEqual(0,s.read()['summary']['units'])
        r=mod.import_report(s,a,b,c,'v');self.assertEqual(1,len(r['units']))
        u=s.read(query=r['units'][0]['name'])['units'][0];self.assertEqual('UNIDENTIFIED',u['environments'][0]['environment'])
        self.assertEqual(1,len(s.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances']))
        self.assertTrue(mod.import_report(s,a,b,c,'v')['unchanged'])
