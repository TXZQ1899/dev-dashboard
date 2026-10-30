import copy
import unittest
import redis_catalog

APPS={'TEST':[], 'SIMULATION':[], 'PRODUCT':[]}

def fixture():
    rows=[]; nodes=[]
    for pid,port,role in [(1,6379,'master'),(2,6380,'slave')]:
        ip='172.20.0.'+str(pid); name='redis-master' if pid==1 else 'replica'
        rep={'role':role,'connected_slaves':'1' if pid==1 else '0'}
        if pid==1:rep['slave0']='ip=172.20.0.2,port=6379,state=online'
        else:rep.update(master_host='redis-master',master_port='6379',master_link_status='up')
        row={'assetId':'asset','ip':'10.1.1.1','pid':pid,'status':'OBSERVED','frames':2,'warnings':[],'observedAt':'2026-01-01T00:00:00Z','startTicks':str(pid),'networkNamespace':str(pid),'processPort':6379,'listeners':[{'port':6379,'protocol':'tcp'}],'applicationCandidates':copy.deepcopy(APPS),'connections':([] if pid==1 else [{'peer':{'ip':'172.20.0.1','port':6379,'protocol':'tcp'}}]),'redisInfo':{'status':'OBSERVED','sections':{'cluster':{'cluster_enabled':'0'},'replication':rep}}}
        c={'name':name,'id':str(pid),'initPid':pid,'startTicks':str(pid),'networkNamespace':str(pid),'ips':[ip],'ports':{'6379/tcp':[{'HostIp':'0.0.0.0','HostPort':str(port)}]}}
        rows.append(row); nodes.append({'pid':pid,'startTicks':str(pid),'container':c,'publishedPort':port,'hostApplicationCandidates':copy.deepcopy(APPS)})
    return {'sourceVersion':'v','results':rows},{'sourceVersion':'v','results':[{'assetId':'asset','ip':'10.1.1.1','status':'OBSERVED','observedAt':'now','nodes':nodes}]}

class RedisReviewTests(unittest.TestCase):
    def test_reciprocal_replication_and_published_ports(self):
        report,checks=fixture(); result=redis_catalog.plan(report,checks)
        self.assertEqual([[6379,6380]],[[n['port'] for n in x['members']] for x in result['groups']])
        self.assertEqual([],result['unresolved'])
    def test_missing_tcp_or_application_candidate_blocks_whole_group(self):
        for field in ('tcp','app','identity'):
            report,checks=fixture()
            if field=='tcp':report['results'][1]['connections']=[]
            if field=='app':checks['results'][0]['nodes'][1]['hostApplicationCandidates']['PRODUCT']=['app']
            if field=='identity':checks['results'][0]['nodes'][1]['container']['initPid']=999
            self.assertEqual([],redis_catalog.plan(report,checks)['groups'],field)
    def test_zero_connected_is_not_proof_of_singleton(self):
        report,checks=fixture();report['results'][0]['redisInfo']['sections']['replication']['connected_slaves']='0'
        self.assertEqual([],redis_catalog.plan(report,checks)['groups'])
    def test_cluster_requires_all_matching_views(self):
        report,checks=fixture(); ids=['a'*40,'b'*40]
        for i,r in enumerate(report['results']):
            r['redisInfo']['sections']['cluster']['cluster_enabled']='1'
            r['redisInfo']['sections']['clusterNodes']=[{'id':ids[j],'address':'10.1.1.1:'+str(6379+j)+'@1','flags':(['myself'] if i==j else [])+(['master'] if j==0 else ['slave']),'masterId':'-' if j==0 else ids[0],'link':'connected'} for j in range(2)]
        self.assertEqual(2,len(redis_catalog.plan(report,checks)['groups'][0]['members']))
        report['results'][1]['redisInfo']['sections']['clusterNodes'][0]['link']='disconnected'
        self.assertEqual([],redis_catalog.plan(report,checks)['groups'])
    def test_source_mismatch_is_error(self):
        report,checks=fixture();checks['sourceVersion']='other'
        with self.assertRaises(ValueError):redis_catalog.plan(report,checks)

if __name__=='__main__':unittest.main()
