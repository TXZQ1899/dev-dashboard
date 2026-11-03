import copy
import unittest
try:
    import mysql_managed_catalog as m
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import mysql_managed_catalog as m
    from runtime import deployment_catalog as db


class ManagedMySQLTest(unittest.TestCase):
    def setUp(self):
        self.snapshot={'available':True,'complete':True,'tenants':[{'tenantId':'a','dmsTenantId':'1','complete':True},{'tenantId':'b','dmsTenantId':'2','complete':True}],'instances':[]}
        for n in range(50):
            cloud=f'rm-{n:04d}';self.snapshot['instances'].append({'dbType':'mysql','cloudInstanceId':cloud,'tenantId':'a','dmsTenantId':'1','resourceKey':'resource-'+cloud,'sourceKeys':['record-'+cloud],'实例地址':cloud+'.example:3306','host':cloud+'.example','port':3306,'observedAt':'2026-11-03T02:34:15+00:00','实例来源':'RDS'})
        repeated=copy.deepcopy(self.snapshot['instances'][0]);repeated.update(tenantId='b',dmsTenantId='2',resourceKey='resource-b',sourceKeys=['record-b'],observedAt='2026-11-03T02:34:20+00:00');self.snapshot['instances'].append(repeated)
        self.snapshot['instances'].append({'dbType':'mysql','cloudInstanceId':'','实例地址':'unresolved.example:3306'})
        self.confirm={'sourceVersion':m.VERSION,'answer':'按云实例 ID 分别建档（Recommended）','confirmedAt':'2026-11-03T08:00:00+00:00','cloudInstanceIds':sorted({x['cloudInstanceId'] for x in m.source_records(self.snapshot)}),'recordsDigest':db.digest(m.source_records(self.snapshot))}
    def plan(self):return m.plan(self.snapshot,self.confirm,m.VERSION)
    def refresh(self):self.confirm['recordsDigest']=db.digest(m.source_records(self.snapshot))
    def test_duplicate_sources_preserved_in_one_service(self):
        groups=self.plan();self.assertEqual(len(groups),50);self.assertEqual(len(groups[0]['records']),2);self.assertEqual(len(groups[0]['endpoints']),1);self.assertEqual(groups[0]['observedAt'],'2026-11-03T02:34:20+00:00')
    def test_unconfirmed_or_changed_data_rejected(self):
        self.confirm['answer']='先保留'
        with self.assertRaises(ValueError):self.plan()
        self.confirm['answer']='按云实例 ID 分别建档（Recommended）';self.snapshot['instances'][0]['port']=3307
        with self.assertRaises(ValueError):self.plan()
    def test_conflicting_cross_tenant_address_rejected(self):
        r=self.snapshot['instances'][50];r['host']='different.example';r['实例地址']='different.example:3306';self.refresh()
        with self.assertRaises(ValueError):self.plan()
    def test_incomplete_slice_and_duplicate_source_rejected(self):
        self.snapshot['tenants'][1]['complete']=False
        with self.assertRaises(ValueError):self.plan()
        self.snapshot['tenants'][1]['complete']=True;self.snapshot['instances'][50]['sourceKeys']=self.snapshot['instances'][0]['sourceKeys'];self.refresh()
        with self.assertRaises(ValueError):self.plan()

if __name__=='__main__':unittest.main()
