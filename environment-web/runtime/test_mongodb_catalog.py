import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from runtime.mongodb_catalog import plan, TARGET, PROFILES, db


class MongoPlanTest(unittest.TestCase):
    def setUp(self):
        t = {'assetId': TARGET[0], 'ip': TARGET[1], 'pid': TARGET[2]}
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        epoch = int(self.now.timestamp())
        self.snapshot = {'sourceVersion':'v', 'devopsComplete':True, 'devopsScope':'all', 'candidates':[{**t,'processStatus':'complete','name':'mongod','snapshotPorts':[27017], 'containerMatches':[], 'hostDeployments':[], 'applicationCandidates':{'TEST':[], 'SIMULATION':[], 'PRODUCT':[]},'startedAt':self.now.isoformat()}]}
        self.live = {'sourceVersion':'v', 'results':[{**t,'status':'OBSERVED','output':'27571 (mongod) 10000\nLISTEN 10.58.1.42:27017 *:* users:(("mongod",pid=27571,fd=11))\nLISTEN 127.0.0.1:27017 *:* users:(("mongod",pid=27571,fd=12))\n27571 (mongod) 10000'}]}
        self.config = {'sourceVersion':'v','results':[{**t,'status':'OBSERVED','exitCode':0,'checkedAt':self.now.isoformat(),'output':f'BEFORE\n27571 (mongod) 10000\nBTIME {epoch-100}\nHZ 100\nEXE /usr/bin/mongod\nARGV_MATCH\nCONFIG_READABLE\nCONFIG_STAT 10 {epoch-1} 50\nROOT systemLog\nROOT storage\nROOT processManagement\nROOT net\nFIELD net   port: 27017\nFIELD net   bindIp: 10.58.1.42,127.0.0.1\nROOT setParameter\nHELLO_JSON {{"ok":1}}\nAFTER\n27571 (mongod) 10000\nCONFIG_STAT_AFTER 10 {epoch-1} 50'}]}
    def run_plan(self):
        return plan(self.snapshot, self.live, self.config, 'v', self.now)
    def test_valid(self):
        self.assertEqual(self.run_plan()['startTicks'], '10000')
    def test_rejects_replica_or_unknown_application(self):
        self.config['results'][0]['output'] = self.config['results'][0]['output'].replace('HELLO_JSON {"ok":1}', 'HELLO_JSON {"ok":1,"setName":"rs"}')
        with self.assertRaises(ValueError): self.run_plan()
        self.setUp(); self.snapshot['candidates'][0]['hostDeployments'] = [{'name':'unknown'}]
        with self.assertRaises(ValueError): self.run_plan()
    def test_rejects_changed_or_newer_config(self):
        self.config['results'][0]['output'] = self.config['results'][0]['output'].replace('CONFIG_STAT_AFTER 10', 'CONFIG_STAT_AFTER 11')
        with self.assertRaises(ValueError): self.run_plan()
        self.setUp(); self.config['results'][0]['output'] += '\nROOT replication'
        with self.assertRaises(ValueError): self.run_plan()
    def test_rejects_restart_and_missing_listener(self):
        self.config['results'][0]['output'] = self.config['results'][0]['output'].replace('AFTER\n27571 (mongod) 10000', 'AFTER\n27571 (mongod) 10001')
        with self.assertRaises(ValueError): self.run_plan()
        self.setUp(); self.live['results'][0]['output'] = self.live['results'][0]['output'].replace('pid=27571,', 'pid=999,')
        with self.assertRaises(ValueError): self.run_plan()
    def test_rejects_stale_or_mismatched_version(self):
        self.config['results'][0]['checkedAt'] = '2025-12-31T20:00:00+00:00'
        with self.assertRaises(ValueError): self.run_plan()
        self.setUp(); self.live['sourceVersion'] = 'old'
        with self.assertRaises(ValueError): self.run_plan()

    def test_explicit_argv_requires_matching_process_and_no_overrides(self):
        target = PROFILES['10.58.16.4'][0]
        for report, key in ((self.snapshot,'candidates'), (self.live,'results'), (self.config,'results')):
            report[key][0].update(assetId=target[0], ip=target[1], pid=target[2])
        self.snapshot['candidates'][0]['snapshotPorts'] = [9998]
        self.live['results'][0]['output'] = '79868 (mongod) 10000\nLISTEN *:9998 *:* users:(("mongod",pid=79868,fd=11))\n79868 (mongod) 10000'
        args = ['/usr/local/server/mongod/bin/mongod','--dbpath=/usr/local/server/mongod/data','--logpath','/usr/local/server/mongod/mongolog/mongo.log','--port','9998','--fork','-auth']
        r = self.config['results'][0]; r['expectedArgv'] = args
        r['output'] = f'BEFORE\n79868 (mongod) 10000\nBTIME {int(self.now.timestamp())-100}\nHZ 100\nEXE {args[0]}\nARGV_MATCH\nNO_EXPLICIT_CONFIG\nHELLO_JSON {{"ok":1}}\nPS_START Thu Jan  1 00:00:00 2026\nAFTER\n79868 (mongod) 10000'
        with patch('runtime.mongodb_catalog.TARGET', target):
            self.assertEqual(self.run_plan()['port'], 9998)
            r['expectedArgv'] = args + ['--replSet', 'rs']
            with self.assertRaises(ValueError): self.run_plan()

    def test_user_confirmation_binds_exact_version_process_and_deployments(self):
        row=self.snapshot['candidates'][0]; row['hostDeployments']=[{'id':'app1'}]
        confirmation={'sourceVersion':'v','answer':'不负责','results':[{'assetId':TARGET[0],'ip':TARGET[1],'pid':TARGET[2],'hostDeploymentsCount':1,'hostDeploymentsDigest':db.digest(row['hostDeployments'])}]}
        self.assertEqual(plan(self.snapshot,self.live,self.config,'v',self.now,confirmation=confirmation)['mode'],'STANDALONE')
        row['hostDeployments'].append({'id':'app2'})
        with self.assertRaises(ValueError): plan(self.snapshot,self.live,self.config,'v',self.now,confirmation=confirmation)

    def test_replica_requires_full_single_member_configuration(self):
        target = PROFILES['10.58.1.112'][0]
        for report, key in ((self.snapshot,'candidates'), (self.live,'results'), (self.config,'results')):
            report[key][0].update(assetId=target[0], ip=target[1], pid=target[2])
        self.live['results'][0]['output'] = '255551 (mongod) 10000\nLISTEN 0.0.0.0:27017 *:* users:(("mongod",pid=255551,fd=11))\n255551 (mongod) 10000'
        out=self.config['results'][0]['output'].replace('27571 (mongod)', '255551 (mongod)').replace('ROOT setParameter', 'ROOT replication\nFIELD replication   replSetName: test').replace('10.58.1.42,127.0.0.1', '0.0.0.0').replace('HELLO_JSON {"ok":1}', 'HELLO_JSON {"ok":1,"setName":"test","hosts":["10.58.1.112:27017"],"primary":"10.58.1.112:27017","me":"10.58.1.112:27017"}')
        self.config['results'][0]['output']=out
        replica={'sourceVersion':'v','assetId':target[0],'pid':target[2],'status':'OBSERVED','exitCode':0,'checkedAt':self.now.isoformat(),'output':'BEFORE\n255551 (mongod) 10000\nCONFIG_JSON {"_id":"test","members":[{"_id":0,"host":"10.58.1.112:27017","hidden":false,"arbiterOnly":false,"votes":1}]}\nAFTER\n255551 (mongod) 10000'}
        with patch('runtime.mongodb_catalog.TARGET', target):
            self.assertEqual(plan(self.snapshot,self.live,self.config,'v',self.now,replica=replica)['mode'], 'REPLICA_SET')
            replica['output']=replica['output'].replace('"hidden":false','"hidden":true')
            with self.assertRaises(ValueError): plan(self.snapshot,self.live,self.config,'v',self.now,replica=replica)

    def test_devops_config_requires_exact_legacy_fields(self):
        target=PROFILES['10.179.2.152'][0]
        for report,key in ((self.snapshot,'candidates'),(self.live,'results'),(self.config,'results')):
            report[key][0].update(assetId=target[0],ip=target[1],pid=target[2])
        self.live['results'][0]['output']='23214 (mongod) 10000\nLISTEN *:27017 *:* users:(("mongod",pid=23214,fd=11))\n23214 (mongod) 10000'
        r=self.config['results'][0];r['expectedArgv']=['/opt/devops/mongodb/bin/mongod','--config','/opt/devops/mongodb/etc/mongodb.conf'];epoch=int(self.now.timestamp())
        r['output']=f'BEFORE\n23214 (mongod) 10000\nPS_START Thu Jan  1 00:00:00 2026\nEXE /opt/devops/mongodb/bin/mongod\nARGV_MATCH\nCONFIG_READABLE\nCONFIG_STAT 10 {epoch-1} 50\nKEY port\nVALUE port 27017\nKEY dbpath\nKEY logpath\nKEY pidfilepath\nKEY directoryperdb\nKEY logappend\nKEY bind_ip\nVALUE bind_ip 0.0.0.0\nKEY oplogSize\nKEY fork\nKEY noprealloc\nHELLO_JSON {{"ok":1}}\nAFTER\n23214 (mongod) 10000\nCONFIG_STAT_AFTER 10 {epoch-1} 50'
        with patch('runtime.mongodb_catalog.TARGET',target):
            self.assertEqual(self.run_plan()['configPath'],'/opt/devops/mongodb/etc/mongodb.conf')
            r['output'] += '\nKEY replSet'
            with self.assertRaises(ValueError): self.run_plan()

    def test_user_confirmed_standalone_keeps_late_config_distinct(self):
        target=PROFILES['10.58.6.4'][0]
        for report,key in ((self.snapshot,'candidates'),(self.live,'results'),(self.config,'results')):
            report[key][0].update(assetId=target[0],ip=target[1],pid=target[2])
        self.live['results'][0]['output']='6852 (mongod) 10000\nLISTEN 10.58.6.4:27017 *:* users:(("mongod",pid=6852,fd=11))\n6852 (mongod) 10000'
        r=self.config['results'][0];epoch=int(self.now.timestamp())
        r['output']=(r['output'].replace('27571 (mongod)','6852 (mongod)').replace('10.58.1.42,127.0.0.1','10.58.6.4').replace('ROOT setParameter','ROOT security\nROOT setParameter').replace('AFTER\n6852','PS_START Thu Jan  1 00:00:00 2026\nAFTER\n6852').replace(str(epoch-1),str(epoch+1)))
        confirmation={'sourceVersion':'v','standalone':{'assetId':target[0],'ip':target[1],'port':27017,'answer':'确认独立部署'}}
        with patch('runtime.mongodb_catalog.TARGET',target):
            self.assertEqual(plan(self.snapshot,self.live,self.config,'v',self.now,confirmation=confirmation)['mode'],'STANDALONE')
            with self.assertRaises(ValueError): plan(self.snapshot,self.live,self.config,'v',self.now)

if __name__ == '__main__': unittest.main()
