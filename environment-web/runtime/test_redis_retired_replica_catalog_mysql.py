import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as base
import redis_retired_replica_catalog as mod
from test_redis_retired_replica_catalog import retired_fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class RetiredReplicaMysqlTests(unittest.TestCase):
 def test_current_node_without_phantom_historical_replica(self):
  store=db.Catalog.from_environment();store.source_key='redis-retired-'+db.identifier();reports=retired_fixture();p=mod.plan(*reports)
  with self.assertRaises(db.CatalogError):base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='retired-test',can_apply=lambda:False)
  self.assertEqual(0,store.read()['summary']['units'])
  r=base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='retired-test');u=store.read(query=r['units'][0]['name'])['units'][0]
  nodes=store.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances'];self.assertEqual(1,len(nodes));self.assertEqual('10.58.9.219',nodes[0]['ip']);self.assertEqual('6379',nodes[0]['port'])
  self.assertTrue(base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='retired-test')['unchanged'])
