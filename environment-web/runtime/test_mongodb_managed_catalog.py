import copy,unittest
from runtime import mongodb_managed_catalog as m
class ManagedMongoTest(unittest.TestCase):
 def setUp(self):
  def row(cloud,tenant,host):return {'cloudInstanceId':cloud,'tenantId':tenant,'dmsTenantId':'dms-'+tenant,'resourceKey':'resource-'+host,'sourceKeys':['source-'+host],'实例地址':host+':3717','host':host,'port':3717,'observedAt':'2026-11-03T02:34:00+00:00','dbType':'mongo'}
  self.rows=[row('cloud-a','a','a1.example'),row('cloud-a','a','a2.example'),row('cloud-b','b','b1.example')];self.snapshot={'available':True,'complete':True,'instances':self.rows,'tenants':[{'tenantId':x,'dmsTenantId':'dms-'+x,'complete':True} for x in ('a','b')]};records=[{k:r.get(k) for k in m.FIELDS} for r in self.rows];self.confirm={'sourceVersion':'v','managed':{'answer':'是，按云实例分别建档','cloudInstanceIds':['cloud-a','cloud-b'],'recordsDigest':m.db.digest(records),'records':records}}
 def run_plan(self):return m.plan(self.snapshot,self.confirm,'v')
 def test_group_addresses_as_one_managed_service(self):
  p=self.run_plan();self.assertEqual(len(p),2);self.assertEqual(len(p[0]['endpoints']),2);self.assertEqual(len(p[1]['endpoints']),1)
 def test_changed_dms_records_rejected(self):
  self.rows[0]['port']=1234
  with self.assertRaises(ValueError):self.run_plan()
 def test_incomplete_slice_rejected(self):
  self.snapshot['tenants'][0]['complete']=False
  with self.assertRaises(ValueError):self.run_plan()
 def test_user_confirmation_is_required(self):
  self.confirm['managed']['answer']='不清楚'
  with self.assertRaises(ValueError):self.run_plan()
 def test_cross_scope_group_rejected(self):
  self.rows[1]['tenantId']='b';self.rows[1]['dmsTenantId']='dms-b';records=[{k:r.get(k) for k in m.FIELDS} for r in self.rows];self.confirm['managed'].update(records=records,recordsDigest=m.db.digest(records))
  with self.assertRaises(ValueError):self.run_plan()
if __name__=='__main__':unittest.main()
