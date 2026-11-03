import copy
from datetime import datetime, timezone
import unittest
import canal_admin_zk_catalog as zk
from test_canal_admin_catalog import fixture as admin_fixture


def fixture():
    r=admin_fixture();r['ruleVersion']=zk.RULE;a=r['adminMembers']['results'][0];n=a['nodes']['items'][0];n.update(clusterId=3,tcpPort=11111)
    a['clusters']=[{'id':3,'name':'group','zkHosts':'10.0.0.3:2181/canal'}];a['instances']['items'][0].update(clusterId=3,serverId=None,runningStatus='1')
    c=r['adminConfigs']['results'][0]['configs'][0];c.update(clusterId=3);c['fields']['canal.zkServers']='10.0.0.3:2181/canal'
    ep='10.0.0.1:11111';r['zkEvidence']={'sourceVersion':'v','results':[{'zkHosts':'10.0.0.3:2181/canal','observedAt':datetime.now(timezone.utc).isoformat(),'status':'OBSERVED','serverRegistry':{'code':0,'children':[ep]},'destinationCount':1,'destinations':[{'name':'orders','clusterCode':0,'members':[ep],'runningCode':0,'running':{'active':True,'address':ep}}]}]}
    return r

class CanalAdminZkTests(unittest.TestCase):
    def test_exact_admin_and_zk_group(self):
        groups=zk.plan(fixture())['groups'];self.assertEqual(len(groups),1);self.assertEqual(groups[0]['adminClusterId'],3)
    def test_inactive_destination_may_be_unobserved(self):
        r=fixture();r['adminMembers']['results'][0]['instances']['items'].append({'id':8,'name':'stopped','clusterId':3,'runningStatus':'0'});self.assertEqual(len(zk.plan(r)['groups']),1)
    def test_conflicting_or_incomplete_views_stay_unresolved(self):
        changes=[lambda r:r['zkEvidence']['results'][0]['serverRegistry']['children'].append('10.0.0.4:11111'),lambda r:r['zkEvidence']['results'][0]['destinations'][0]['running'].update(address='10.0.0.4:11111'),lambda r:r['zkEvidence']['results'][0]['destinations'][0]['members'].append('10.0.0.4:11111'),lambda r:r['adminMembers']['results'][0]['nodes']['items'].append({'id':2,'clusterId':3}),lambda r:r['adminConfigs']['results'][0]['configs'][0]['fields'].update({'canal.zkServers':'10.0.0.4:2181/canal'}),lambda r:r['zkEvidence']['results'][0].update(status='PARTIAL'),lambda r:r['results'][0]['hostDeployments'].append({'appId':'x'}),lambda r:r['zkEvidence']['results'][0]['destinations'].append({'name':'unknown','members':['10.0.0.1:11111']}),lambda r:r['adminMembers']['results'][0]['instances']['items'][0].update(runningStatus='0')]
        for change in changes:
            with self.subTest(change=change):
                r=fixture();change(r);self.assertFalse(zk.plan(r)['groups'])
    def test_zk_version_mismatch_rejected(self):
        r=fixture();r['zkEvidence']['sourceVersion']='old'
        with self.assertRaises(ValueError):zk.plan(r)
    def test_standalone_in_shared_namespace_requires_disjoint_members_and_app_confirmation(self):
        r=fixture();a=r['adminMembers']['results'][0];n=a['nodes']['items'][0];n['clusterId']=None
        a['instances']['items'][0].update(clusterId=None,serverId=27)
        a['nodes']['items'].append({'id':28,'ip':'10.0.0.4','tcpPort':11111,'clusterId':4,'status':'1'})
        cfg=r['adminConfigs']['results'][0];cfg['configs'][0]['clusterId']=None
        cfg['configs'].append({'nodeId':28,'status':'OBSERVED','fields':{'canal.zkServers':'10.0.0.3:2181/canal'}})
        view=r['zkEvidence']['results'][0];view['serverRegistry']['children'].append('10.0.0.4:11111')
        view['destinations'].append({'name':'other','members':['10.0.0.4:11111'],'clusterCode':0,'runningCode':0,'running':{'active':True,'address':'10.0.0.4:11111'}});view['destinationCount']=2
        r['results'][0]['hostDeployments']=[{'env':'PRODUCT','appId':'9'}]
        self.assertFalse(zk.plan(r)['groups'])
        r['userConfirmation']={'sourceVersion':'v','kind':'USER_CONFIRMED_APPS_DO_NOT_MANAGE_CANAL','confirmedAt':'2026-11-02','confirmedApplications':[{'assetId':'a','env':'PRODUCT','appId':'9'}]}
        group=zk.plan(r)['groups'];self.assertEqual(len(group),1);self.assertEqual(group[0]['groupingBasis'],'ADMIN_STANDALONE_ZK_DISJOINT')
        altered=copy.deepcopy(r);altered['zkEvidence']['results'][0]['destinations'][0]['members'].append('10.0.0.4:11111');self.assertFalse(zk.plan(altered)['groups'])
        altered=copy.deepcopy(r);altered['userConfirmation']['confirmedApplications'][0]['appId']='other';self.assertFalse(zk.plan(altered)['groups'])

if __name__=='__main__':unittest.main()
