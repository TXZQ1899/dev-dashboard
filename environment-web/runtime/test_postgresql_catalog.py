import copy
import unittest
from datetime import datetime, timezone

try:
    import postgresql_catalog as pg
except ModuleNotFoundError:
    from runtime import postgresql_catalog as pg


class PostgreSQLPlanTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,3,7,0,tzinfo=timezone.utc)
        self.stamp='2026-11-03T06:45:00+00:00'
        self.snapshot={'assets':[]}; self.live=[]
        for ip,(asset,pid,path) in pg.PROCESSES.items():
            self.snapshot['assets'].append({'id':asset,'ip':ip,'inspection':{'processes':[{'pid':pid,'name':'postgres','command':'postgres -D '+path}]}})
            output=f'BEFORE\n{pid} (postgres) 99\nCMD postgres -D {path}\n'
            if pid==2407: output+='127.0.0.1:33335 pid=2407,\n::1:33335 pid=2407,\n'
            output+=f'AFTER\n{pid} (postgres) 99'
            self.live.append({'sourceVersion':pg.VERSION,'results':[{'assetId':asset,'ip':ip,'pid':pid,'status':'OBSERVED','checkedAt':self.stamp,'output':output}]})
        self.dms={'available':True,'complete':True,'tenants':[{'tenantId':'tenant','dmsTenantId':'tid','complete':True}],'instances':[]}
        for cloud in sorted(pg.CLOUD_IDS):
            self.dms['instances'].append({'dbType':'postgresql','cloudInstanceId':cloud,'tenantId':'tenant','dmsTenantId':'tid','resourceKey':cloud,'sourceKeys':['source'],'observedAt':self.stamp,'host':cloud+'.example','port':5432,'实例地址':cloud+'.example:5432'})
        self.confirm={'sourceVersion':pg.VERSION,'confirmedAt':self.stamp,'processIPs':list(pg.PROCESSES),'cloudInstanceIds':list(pg.CLOUD_IDS),'processAnswer':'都需要，各自单独建档','managedAnswer':'是，分别建档'}

    def plan(self):
        return pg.plan(self.snapshot,self.dms,self.live[0],self.live[1],{'sourceVersion':pg.VERSION},self.confirm,pg.VERSION,self.now)

    def test_separate_units_and_loopback(self):
        items=self.plan();self.assertEqual(len(items),4);self.assertEqual(items[0]['endpoints'],[])
        self.assertEqual({x['ip'] for x in items[1]['endpoints']},{'127.0.0.1','::1'})
        self.assertEqual({i['key']['cloudInstanceId'] for i in items[2:]},pg.CLOUD_IDS)

    def test_rejects_changed_process_or_unconfirmed_group(self):
        self.live[0]['results'][0]['output']=self.live[0]['results'][0]['output'].replace('AFTER\n773 (postgres) 99','AFTER\n773 (postgres) 100')
        with self.assertRaises(ValueError): self.plan()
        self.live[0]['results'][0]['output']=self.live[0]['results'][0]['output'].replace('AFTER\n773 (postgres) 100','AFTER\n773 (postgres) 99')
        self.confirm['processAnswer']='未确认'
        with self.assertRaises(ValueError): self.plan()

    def test_rejects_duplicate_cloud_records(self):
        self.dms['instances'].append(copy.deepcopy(self.dms['instances'][0]))
        with self.assertRaises(ValueError): self.plan()

    def test_rejects_stale_live_evidence(self):
        self.live[0]['results'][0]['checkedAt']='2026-11-02T00:00:00+00:00'
        with self.assertRaises(ValueError): self.plan()

if __name__=='__main__': unittest.main()
