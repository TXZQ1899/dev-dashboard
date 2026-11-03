import copy
from datetime import datetime, timezone
import unittest
import canal_admin_catalog as admin
from test_canal_catalog import fixture as local_fixture


def fixture():
    now=datetime.now(timezone.utc).isoformat();r=local_fixture()['results'][0];r['initial']['fields']['canal.admin.manager']='10.0.0.2:8089'
    node={'id':27,'ip':r['ip'],'adminPort':11110,'clusterId':None,'status':'1'}
    inst={'id':7,'name':'orders','serverId':27,'clusterId':None}
    entry={'admin':'10.0.0.2:8089','observedAt':now,'status':'COMPLETE','nodes':{'status':'COMPLETE','items':[node]},'instances':{'status':'COMPLETE','items':[inst]}}
    config={'admin':'10.0.0.2:8089','observedAt':now,'status':'COMPLETE','configs':[{'ip':r['ip'],'nodeId':27,'clusterId':None,'status':'OBSERVED','modifiedTime':'2020-01-01 00:00:00','fields':{'canal.zkServers':'','canal.instance.global.mode':'manager','canal.instance.global.spring.xml':'classpath:spring/file-instance.xml','canal.serverMode':'kafka'}}]}
    details={'admin':'10.0.0.2:8089','serverId':27,'observedAt':now,'status':'COMPLETE','instances':[{'id':7,'name':'orders','serverId':27,'clusterId':None,'contentStatus':'OBSERVED','coordinationKeys':[]}]}
    return {'sourceVersion':'v','ruleVersion':admin.RULE,'results':[r],'adminMembers':{'sourceVersion':'v','results':[entry]},'adminConfigs':{'sourceVersion':'v','results':[config]},'instanceDetails':{'sourceVersion':'v','result':details}}

class AdminPlanTests(unittest.TestCase):
    def test_confirmed_admin_single_server(self):
        p=admin.plan(fixture());self.assertEqual(len(p['groups']),1);self.assertEqual(p['groups'][0]['destinations'],['orders'])
    def test_cluster_or_unknown_evidence_never_promoted(self):
        changes=[lambda r:r['adminMembers']['results'][0]['nodes']['items'][0].update(clusterId=3),lambda r:r['adminMembers']['results'][0].update(status='PARTIAL'),lambda r:r['adminMembers']['results'][0]['nodes']['items'][0].update(adminPort=11111),lambda r:r['adminConfigs']['results'][0]['configs'][0]['fields'].update({'canal.zkServers':'10.0.0.3:2181'}),lambda r:r['instanceDetails']['result']['instances'][0]['coordinationKeys'].append('canal.zkServers'),lambda r:r['instanceDetails']['result'].update(status='PARTIAL'),lambda r:r['instanceDetails']['result']['instances'].clear(),lambda r:r['results'][0]['hostDeployments'].append({'appId':'1'}),lambda r:r['results'][0]['initial'].update(after='123 (java) 556')]
        for change in changes:
            with self.subTest(change=change):
                r=fixture();change(r);self.assertFalse(admin.plan(r)['groups']);self.assertEqual(len(admin.plan(r)['unresolved']),1)
    def test_missing_or_expired_data_rejected(self):
        r=fixture();r['instanceDetails']['result']['observedAt']='2020-01-01T00:00:00Z';self.assertFalse(admin.plan(r)['groups'])
        r=fixture();r['adminConfigs']['sourceVersion']='older'
        with self.assertRaises(ValueError):admin.plan(r)

if __name__=='__main__':unittest.main()
