import copy
from datetime import datetime, timedelta, timezone
import unittest
import canal_catalog as canal


def fixture():
    now=datetime.now(timezone.utc);stamp=now.isoformat();proc='123 (java) 555'
    initial={'before':proc,'after':proc,'checkedAt':stamp,'configArgument':'/opt/canal.deploy-1.1.6/bin/../conf/canal.properties','configArgumentMatch':'MATCH','configReadable':'YES','configOutputComplete':True,'fields':{'canal.zkServers':'','canal.serverMode':'rabbitMQ','canal.instance.global.mode':'spring','canal.instance.global.spring.xml':'classpath:spring/file-instance.xml','canal.destinations':'dw_dms'},'listeners':['LISTEN 0 50 127.0.0.1:11110 *:* users:(("java",pid=123,fd=1))']}
    local={'before':proc,'after':proc,'checkedAt':stamp,'configStat':str(int((now-timedelta(days=2)).timestamp()))+' 100','propertyOverrides':['canal.conf'],'confDirectories':['dw_dms','spring']}
    scope={'BEFORE':proc,'AFTER':proc,'checkedAt':stamp,'PID_NET':'net:1','HOST_NET':'net:1','PID_PIDNS':'pid:1','HOST_PIDNS':'pid:1','PID_MNT':'mnt:1','HOST_MNT':'mnt:1','environmentKeys':[]}
    spring={'BEFORE':proc,'AFTER':proc,'checkedAt':stamp,'STAT':str(int((now-timedelta(days=2)).timestamp()))+' 100','HASH':'a'*64,'imports':['classpath:spring/base-instance.xml'],'classes':['com.alibaba.otter.canal.meta.FileMixedMetaManager']}
    return {'sourceVersion':'v','ruleVersion':canal.RULE,'results':[{'assetId':'a','ip':'10.0.0.1','pid':123,'startedAt':(now-timedelta(days=1)).isoformat(),'processStatus':'complete','initial':initial,'local':local,'scope':scope,'spring':spring,'hostDeployments':[]}]}

class CanalPlanTests(unittest.TestCase):
    def test_single_server_local_configuration(self):
        p=canal.plan(fixture());self.assertEqual(len(p['groups']),1);self.assertEqual(p['groups'][0]['destinations'],['dw_dms'])
    def test_conflicts_remain_unresolved(self):
        changes=[lambda r:r['initial']['fields'].update({'canal.zkServers':'10.0.0.1:2181'}),lambda r:r['initial']['fields'].update({'canal.admin.manager':'10.0.0.2:8089'}),lambda r:r['initial']['fields'].pop('canal.zkServers'),lambda r:r['local'].update(propertyOverrides=['canal.conf','canal.zkServers']),lambda r:r['scope'].update(environmentKeys=['CANAL_ZK']),lambda r:r['scope'].update(PID_NET='net:2'),lambda r:r['hostDeployments'].append({'appId':'1'}),lambda r:r['local'].update(configStat='9999999999 1'),lambda r:r['spring'].update(classes=['org.other.Custom']),lambda r:r['initial']['listeners'].clear(),lambda r:r['initial'].update(after='123 (java) 556'),lambda r:r.update(processStatus='partial')]
        for change in changes:
            with self.subTest(change=change):
                report=fixture();change(report['results'][0]);result=canal.plan(report);self.assertEqual(result['groups'],[]);self.assertEqual(len(result['unresolved']),1)
    def test_same_destination_does_not_merge_servers(self):
        r=fixture();other=copy.deepcopy(r['results'][0]);other.update(assetId='b',ip='10.0.0.2');r['results'].append(other)
        self.assertEqual(len(canal.plan(r)['groups']),2)
    def test_missing_checks_duplicates_and_stale_observations(self):
        r=fixture();r['results'][0]['local']=None;p=canal.plan(r);self.assertFalse(p['groups']);self.assertIn('未覆盖',p['unresolved'][0]['reason'])
        r=fixture();r['results'].append(copy.deepcopy(r['results'][0]));self.assertFalse(canal.plan(r)['groups'])
        r=fixture();r['results'][0]['scope']['checkedAt']='2020-01-01T00:00:00+00:00';self.assertFalse(canal.plan(r)['groups'])

if __name__=='__main__':unittest.main()
