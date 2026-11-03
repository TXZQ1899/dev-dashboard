import json,unittest
from datetime import datetime,timezone
from runtime import mongodb_container_catalog as m
class MongoContainerTest(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,1,1,tzinfo=timezone.utc);self.ip='10.179.2.141';p=m.PROFILES[self.ip];pid=p['pid'];ports=[{'containerPort':'27017/tcp','hostIp':'0.0.0.0','hostPort':'27017'}];t={'assetId':p['asset'],'ip':self.ip,'pid':pid};self.s={'sourceVersion':'v','devopsComplete':True,'devopsScope':'all','candidates':[{**t,'startedAt':self.now.isoformat(),'name':'mongod','processStatus':'complete','hostDeployments':[{'id':'app'}],'applicationCandidates':{'TEST':[],'SIMULATION':[],'PRODUCT':[]},'containerMatches':[{'id':'container','name':p['name'],'initPid':pid,'ports':ports}]}]};self.confirm={'sourceVersion':'v','answer':'不负责','results':[{**t,'hostDeploymentsCount':1,'hostDeploymentsDigest':m.db.digest([{'id':'app'}])}]};mapping={'27017/tcp':[{'HostIp':'0.0.0.0','HostPort':'27017'}]};out=f'BEFORE\n{pid} (mongod) 123\nPS_START Thu Jan  1 00:00:00 2026\nARGV_MATCH\nDOCKER_JSON {pid} true "default" {json.dumps(mapping)}\nLISTEN *:27017 *:* users:(("mongod",pid={pid},fd=9))\nHELLO_JSON {{"ok":1}}\nAFTER\n{pid} (mongod) 123';self.d={'sourceVersion':'v','results':[{**t,'checkedAt':self.now.isoformat(),'status':'OBSERVED','exitCode':0,'expectedArgv':p['argv'],'containerId':'container','output':out}]}
 def plan(self):return m.plan(self.s,self.d,self.confirm,'v',self.ip,self.now)
 def test_valid(self):self.assertEqual(self.plan()['endpoints'][1]['ip'],'0.0.0.0')
 def test_stale_confirmation(self):
  self.s['candidates'][0]['hostDeployments'].append({'id':'new'})
  with self.assertRaises(ValueError):self.plan()
 def test_mismatch_network(self):
  self.d['results'][0]['output']=self.d['results'][0]['output'].replace('"HostPort": "27017"','"HostPort": "27018"')
  with self.assertRaises(ValueError):self.plan()
 def test_replica_service_rejected(self):
  self.d['results'][0]['output']=self.d['results'][0]['output'].replace('HELLO_JSON {"ok":1}','HELLO_JSON {"ok":1,"setName":"rs0"}')
  with self.assertRaises(ValueError):self.plan()
 def test_command_override_rejected(self):
  self.d['results'][0]['expectedArgv']=['mongod','--replSet','rs0']
  with self.assertRaises(ValueError):self.plan()
if __name__=='__main__':unittest.main()
