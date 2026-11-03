import copy
from datetime import datetime, timezone
import unittest
import rabbitmq_catalog as rabbit

def fixture():
    stamp=datetime.now(timezone.utc).isoformat();proc='123 (beam.smp) 555'
    member={'node':'rabbit@host','all':'[rabbit@host]','running':'[rabbit@host]','classicNodes':'{[],disc}','before':proc,'after':proc,'evalRc':'0','osPid':'123','observedAt':stamp}
    pid={'HOST':proc,'HOST_AFTER':proc,'INSIDE':proc,'observedAt':stamp}
    host={'processBefore':[proc],'processAfter':[proc],'checkedAt':stamp,'listeners':['LISTEN 0 128 :::5672 :::* users:(("beam.smp",pid=123,fd=1))']}
    row={'assetId':'a','ip':'10.0.0.1','pid':123,'processStatus':'complete','member':member,'pidCheck':pid,'hostCheck':host,'containerCheck':None,'snapshotContainers':[],'hostDeployments':[]}
    return {'sourceVersion':'v','ruleVersion':rabbit.RULE,'results':[row]}

class RabbitMQPlanTests(unittest.TestCase):
    def test_single_view_and_direct_process(self):
        p=rabbit.plan(fixture());self.assertEqual(len(p['groups']),1);self.assertIsNone(p['groups'][0]['port'])
    def test_incomplete_and_conflicting_evidence_stays_unresolved(self):
        changes=[lambda r:r['member'].update(all='[rabbit@host,rabbit@other]'),lambda r:r['member'].update(running='[]'),lambda r:r['member'].update(classicNodes='{[rabbit@other],disc}'),lambda r:r['pidCheck'].update(INSIDE='321 (beam.smp) 554'),lambda r:r['hostCheck']['listeners'].clear(),lambda r:r['hostDeployments'].append({'port':''}),lambda r:r['hostDeployments'].append({'port':'5672'}),lambda r:r.update(processStatus='partial')]
        for change in changes:
            with self.subTest(change=change):
                report=fixture();change(report['results'][0]);p=rabbit.plan(report);self.assertEqual(p['groups'],[]);self.assertEqual(len(p['unresolved']),1)
    def test_same_node_name_on_different_assets_not_merged(self):
        report=fixture();other=copy.deepcopy(report['results'][0]);other.update(assetId='b',ip='10.0.0.2');report['results'].append(other)
        groups=rabbit.plan(report)['groups'];self.assertEqual(len(groups),2);self.assertNotEqual(groups[0]['key'],groups[1]['key'])
    def test_container_requires_mapping_and_stable_identity(self):
        report=fixture();r=report['results'][0];r['member'].update(containerId='c',osPid='7',publishedPorts={'5672/tcp':[{'HostIp':'0.0.0.0','HostPort':'5672'}]});r['pidCheck']['INSIDE']='7 (beam.smp) 555';r['snapshotContainers']=[{'id':'c','name':'rabbit','initPid':99}];r['containerCheck']={**r['hostCheck'],'containerId':'c','containerBefore':[['true','99','0']],'containerAfter':[['true','99','0']]}
        self.assertEqual(rabbit.plan(report)['groups'][0]['port'],5672);r['containerCheck']['containerAfter']=[['true','99','1']];self.assertEqual(rabbit.plan(report)['groups'],[])
    def test_duplicates_and_expired_evidence_rejected(self):
        r=fixture();r['results'].append(copy.deepcopy(r['results'][0]));self.assertEqual(rabbit.plan(r)['groups'],[])
        r=fixture();r['results'][0]['member']['observedAt']='2020-01-01T00:00:00+00:00';self.assertEqual(rabbit.plan(r)['groups'],[])
    def test_user_confirmation_is_scoped_to_asset_app_type_and_missing_port(self):
        r=fixture();app={'env':'TEST','appId':'1','appType':'node','port':''};r['results'][0]['hostDeployments']=[app]
        r['userConfirmation']={'sourceVersion':'v','kind':'USER_CONFIRMED_NOT_MANAGING_RABBITMQ','confirmedAt':'2026-11-02','confirmedApplications':[{'assetId':'a','env':'TEST','appId':'1','appType':'node'}]}
        self.assertEqual(len(rabbit.plan(r)['groups']),1)
        for key,value in [('appId','2'),('appType','war8'),('env','PRODUCT')]:
            altered=copy.deepcopy(r);altered['results'][0]['hostDeployments'][0][key]=value
            self.assertFalse(rabbit.plan(altered)['groups'])
        altered=copy.deepcopy(r);altered['results'][0]['hostDeployments'][0]['port']='5672'
        self.assertFalse(rabbit.plan(altered)['groups'])
        altered=copy.deepcopy(r);altered['userConfirmation']['sourceVersion']='other'
        self.assertFalse(rabbit.plan(altered)['groups'])

if __name__=='__main__':unittest.main()
