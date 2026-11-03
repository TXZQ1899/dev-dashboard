import copy,json,unittest
from datetime import datetime,timezone
from runtime import mongodb_replica_catalog as m

class MongoReplicaTest(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,1,1,tzinfo=timezone.utc);self.snapshot={'sourceVersion':'v','devopsComplete':True,'devopsScope':'all','candidates':[]};self.initial={'sourceVersion':'v','results':[]};self.detail={'sourceVersion':'v','assetId':m.ASSET,'ip':m.IP,'checkedAt':self.now.isoformat(),'results':[]};self.confirm={'sourceVersion':'v','answer':'不负责','results':[]}
  config={'_id':'rs0','members':[{'_id':0,'host':m.IP+':27017','hidden':False,'arbiterOnly':False,'votes':1},{'_id':1,'host':m.IP+':27018','hidden':False,'arbiterOnly':False,'votes':1}]}
  for n,(pid,(name,port)) in enumerate(m.MEMBERS.items()):
   cid='container-'+str(n);row={'assetId':m.ASSET,'ip':m.IP,'pid':pid,'startedAt':self.now.isoformat(),'name':'mongod','processStatus':'complete','hostDeployments':[{'id':'app'}],'applicationCandidates':{'TEST':[],'SIMULATION':[],'PRODUCT':[]},'containerMatches':[{'id':cid,'name':name,'initPid':pid,'ports':[{'containerPort':'27017/tcp','hostIp':'0.0.0.0','hostPort':str(port)}]}]};self.snapshot['candidates'].append(row)
   self.confirm['results'].append({'assetId':m.ASSET,'ip':m.IP,'pid':pid,'hostDeploymentsDigest':m.db.digest(row['hostDeployments']),'hostDeploymentsCount':1})
   hello={'ok':1,'setName':'rs0','hosts':[m.IP+':27017',m.IP+':27018'],'primary':m.IP+':27017','me':m.IP+':'+str(port)};identity=f'{pid} (mongod) 123';self.initial['results'].append({'assetId':m.ASSET,'ip':m.IP,'pid':pid,'status':'OBSERVED','checkedAt':self.now.isoformat(),'output':f'BEFORE\n{identity}\nARGV_MATCH\nDOCKER {pid} true /{name}\nHELLO_JSON {json.dumps(hello)}\nAFTER\n{identity}'})
   mapping={'27017/tcp':[{'HostIp':'0.0.0.0','HostPort':str(port)}]};out=f'BEFORE\n{identity}\nPS_START Thu Jan  1 00:00:00 2026\nDOCKER_JSON {pid} true {json.dumps(mapping)}\nLISTEN *:27017 *:* users:(("mongod",pid={pid},fd=10))\nCONFIG_JSON {json.dumps(config)}\nAFTER\n{identity}';self.detail['results'].append({'pid':pid,'containerId':cid,'exitCode':0,'output':out})
 def check(self):return m.plan(self.snapshot,self.initial,self.detail,self.confirm,'v',self.now)
 def test_two_members(self):self.assertEqual([x['hostPort'] for x in self.check()['members']],[27017,27018])
 def test_hidden_member_rejected(self):
  self.detail['results'][0]['output']=self.detail['results'][0]['output'].replace('"hidden": false','"hidden": true',1)
  with self.assertRaises(ValueError):self.check()
 def test_confirmation_not_transferable(self):
  self.snapshot['candidates'][0]['hostDeployments'].append({'id':'another'})
  with self.assertRaises(ValueError):self.check()
 def test_port_or_identity_conflict_rejected(self):
  self.detail['results'][1]['output']=self.detail['results'][1]['output'].replace('"HostPort": "27018"','"HostPort": "27017"')
  with self.assertRaises(ValueError):self.check()
 def test_missing_member_rejected(self):
  self.detail['results'].pop()
  with self.assertRaises(ValueError):self.check()
if __name__=='__main__':unittest.main()
