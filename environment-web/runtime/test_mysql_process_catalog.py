import copy
import json
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
try:
    import mysql_process_catalog as m
    import deployment_catalog as db
except ModuleNotFoundError:
    from runtime import mysql_process_catalog as m
    from runtime import deployment_catalog as db


class MySQLProcessPlanTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,3,8,0,tzinfo=timezone.utc);self.targets={'10.179.1.226':('asset-host',11277),'10.58.1.54':('asset-maria',1347),'10.58.1.112':('asset-pod',217066)}
        self.snapshot={'assets':[{'id':aid,'ip':ip,'inspection':{'processStatus':'partial' if ip=='10.58.1.54' else 'complete','checkedAt':self.now.isoformat(),'processes':[{'pid':pid,'name':'mysqld','startedAt':self.now.isoformat()}]}} for ip,(aid,pid) in self.targets.items()]}
        self.devops={'complete':True,'scope':'all','apps':[{'id':'1','envs':{'TEST':[{'ip':'10.179.1.226','port':'3306','deploy':'1'}]}}]}
        cid='20320a4a6ae8d9684c98d5b0765e87acc1db152995ae1e757d195c0ad8ff300b'
        def row(ip,body):
            aid,pid=self.targets[ip];return {'ip':ip,'assetId':aid,'pid':pid,'status':'OBSERVED','exitCode':0,'checkedAt':self.now.isoformat(),'output':f'BEFORE\n{pid} (mysqld) 88\n'+body+f'\nAFTER\n{pid} (mysqld) 88'}
        self.live={'sourceVersion':m.VERSION,'results':[row('10.179.1.226','NETNS net:[1]\nHOST_NETNS net:[1]\nVERSION mysql  Ver 5.7 (MySQL Community Server)\nLISTEN 0 80 [::]:3306 [::]:* users:(("mysqld",pid=11277,fd=1))')]}
        self.special={'sourceVersion':m.VERSION,'results':[row('10.58.1.54','/system.slice/mariadb.service\n--datadir=/var/lib/mysql\n5.5.50-MariaDB\nmariadb-server-5.5.50'),row('10.58.1.112','NETNS net:[2]\nHOST_NETNS net:[1]\nlibpod-'+cid+'\n*:3306 x pid=217066,\n*:33060 x pid=217066,\n'+cid+' xxl-mysql true 217066 '+json.dumps({'8080/tcp':[{'HostIp':'','HostPort':'8081'}]})+' docker/mysql:8.0\nMySQL Community Server')]}
        self.patch=patch.object(m,'TARGETS',self.targets);self.patch.start();self.addCleanup(self.patch.stop)
        self.confirm={'sourceVersion':m.VERSION,'answer':'17 个主机进程，也按独立部署单元来建立','confirmedAt':self.now.isoformat(),'scopeDigest':db.digest(m.snapshot_scope(self.snapshot,self.devops))}
    def plan(self):return m.plan(self.snapshot,self.devops,self.live,self.special,self.confirm,m.VERSION,self.now)
    def test_distinguishes_mariadb_and_podman_endpoints(self):
        items={x['ip']:x for x in self.plan()};self.assertEqual(items['10.58.1.54']['technology'],'MariaDB');self.assertIsNone(items['10.58.1.54']['observedEndpoints']);self.assertEqual(items['10.58.1.112']['portBasis'],'CONTAINER_LISTENER');self.assertTrue(all(x['scope']=='CONTAINER' for x in items['10.58.1.112']['observedEndpoints']));self.assertEqual(items['10.179.1.226']['applicationAssociationState'],'UNKNOWN');self.assertEqual(len(items['10.179.1.226']['hostDeployments']),1)
    def test_rejects_changed_user_scope_and_process(self):
        self.devops['apps'][0]['envs']['TEST'][0]['port']='9999'
        with self.assertRaises(ValueError):self.plan()
        self.devops['apps'][0]['envs']['TEST'][0]['port']='3306';self.live['results'][0]['output']=self.live['results'][0]['output'].replace('AFTER\n11277 (mysqld) 88','AFTER\n11277 (mysqld) 89')
        with self.assertRaises(ValueError):self.plan()
    def test_rejects_unverified_container_and_old_evidence(self):
        original=copy.deepcopy(self.special);self.special['results'][1]['output']=self.special['results'][1]['output'].replace('pid=217066,','pid=1,')
        with self.assertRaises(ValueError):self.plan()
        self.special=original;self.live['results'][0]['checkedAt']='2026-11-01T00:00:00+00:00'
        with self.assertRaises(ValueError):self.plan()

if __name__=='__main__':unittest.main()
