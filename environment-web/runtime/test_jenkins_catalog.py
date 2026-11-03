import copy
import json
import unittest
from datetime import datetime,timezone
try:
    import jenkins_catalog as j
except ModuleNotFoundError:
    from runtime import jenkins_catalog as j


class JenkinsPlanTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,3,7,0,tzinfo=timezone.utc);self.snapshot={'assets':[]};self.live={'sourceVersion':j.VERSION,'results':[]}
        self.devops={'complete':True,'scope':'all','apps':[]};self.confirm={'sourceVersion':j.VERSION,'answer':'是，分别建档','ips':list(j.TARGETS),'confirmedAt':self.now.isoformat()}
        for ip,(aid,pid,home,war) in j.TARGETS.items():
            container=ip=='10.58.10.31';init=875
            ins={'processStatus':'complete','processes':[{'pid':pid,'ppid':init if container else 1,'name':'java','command':'java -jar '+war}],'docker':{'containers':[]}}
            out=f'BEFORE\n{pid} (java) 5\nARGS\n{war}\nHOME\nJENKINS_HOME={home}\nNETNS net:[2]\nHOST_NETNS net:[{1 if container else 2}]\n'
            if container:
                cid='a'*64;ins['docker']['containers']=[{'id':cid,'name':'devjekins2893','initPid':init}]
                ports={'50000/tcp':[{'HostIp':'0.0.0.0','HostPort':'50000'}],'8080/tcp':[{'HostIp':'0.0.0.0','HostPort':'49001'}]}
                out+=f'DOCKER\n{cid} true {init} 0 {json.dumps(ports)}\n*:8080  users:(("java",pid=903,fd=1))\n*:50000  users:(("java",pid=903,fd=2))\n'
            else:out+=('*:8080' if ip=='10.179.1.226' else '[::]:8080')+f'  users:(("java",pid={pid},fd=1))\n'
            out+=f'AFTER\n{pid} (java) 5';self.snapshot['assets'].append({'id':aid,'ip':ip,'inspection':ins});self.live['results'].append({'assetId':aid,'ip':ip,'pid':pid,'status':'OBSERVED','exitCode':0,'checkedAt':self.now.isoformat(),'output':out})

    def plan(self):return j.plan(self.snapshot,self.devops,self.live,self.confirm,j.VERSION,self.now)
    def test_controllers_and_published_endpoint(self):
        items=self.plan();self.assertEqual(len(items),3);self.assertEqual(items[1]['declaredPort'],'49001');self.assertEqual(items[1]['portBasis'],'DOCKER_PUBLISHED');self.assertIsNone(items[0]['declaredPort'])
    def test_changed_process_or_ambiguous_application(self):
        self.live['results'][0]['output']=self.live['results'][0]['output'].replace('AFTER\n8033 (java) 5','AFTER\n8033 (java) 6')
        with self.assertRaises(ValueError):self.plan()
        self.live['results'][0]['output']=self.live['results'][0]['output'].replace('AFTER\n8033 (java) 6','AFTER\n8033 (java) 5')
        self.devops['apps']=[{'envs':{'TEST':[{'ip':'10.179.1.226'}]}}]
        with self.assertRaises(ValueError):self.plan()
    def test_container_mapping_and_confirmation(self):
        original=copy.deepcopy(self.live);self.live['results'][1]['output']=self.live['results'][1]['output'].replace('49001','49002')
        with self.assertRaises(ValueError):self.plan()
        self.live=original;self.confirm['answer']='暂不确定'
        with self.assertRaises(ValueError):self.plan()
    def test_old_observation(self):
        self.live['results'][0]['checkedAt']='2026-11-01T00:00:00+00:00'
        with self.assertRaises(ValueError):self.plan()

if __name__=='__main__':unittest.main()
