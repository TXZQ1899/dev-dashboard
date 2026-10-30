import os,unittest
import deployment_catalog as db
import redis_loopback_catalog as base
import redis_sentinel_single_catalog as mod
from test_redis_sentinel_single_catalog import fixture

@unittest.skipUnless(os.environ.get('ENVSCOPE_CATALOG_MYSQL_TEST')=='1','requires isolated MySQL')
class SentinelSingleMysqlTests(unittest.TestCase):
 def test_host_network_port_and_idempotence(self):
  store=db.Catalog.from_environment();store.source_key='redis-sentinel-'+db.identifier();reports=fixture();p=mod.plan(*reports)
  with self.assertRaises(db.CatalogError):base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='sentinel-test',can_apply=lambda:False)
  self.assertEqual(0,store.read()['summary']['units'])
  r=base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='sentinel-test');u=store.read(query=r['units'][0]['name'])['units'][0]
  node=store.read(unit_id=u['id'],environment='UNIDENTIFIED')['instances'][0];self.assertEqual('6379',node['port']);self.assertEqual('HOST_NETWORK',node['attributes']['listenerScope'])
  self.assertTrue(base.import_report(store,*reports[:4],'v',prepared=p,extra_reports=reports[4:],mapper_version='sentinel-test')['unchanged'])
