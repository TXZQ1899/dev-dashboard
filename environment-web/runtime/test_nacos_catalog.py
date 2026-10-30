import copy
import unittest
import nacos_catalog as n


def fixture():
    empty={e:[] for e in ('TEST','SIMULATION','PRODUCT')}
    row={'assetId':'a1','ip':'192.0.2.10','pid':11,'nacosHome':'/home/nacos','status':'OBSERVED',
         'standalone':'true','frames':5,'warnings':[],'startTicks':'100','observedAt':'2026-10-29T00:00:00Z',
         'configEvidence':['CONFIG_READABLE','CLUSTER_UNAVAILABLE'],'applicationCandidates':empty,
         'listeners':[{'ip':'*','port':8848,'protocol':'tcp'}],'connectionCount':0,'established':[]}
    check={'assetId':'a1','ip':'192.0.2.10','pid':11,'status':'OBSERVED','warnings':[],'dockerWarnings':[],
           'observedAt':'2026-10-29T00:00:02Z','startTicks':'100','hostApplicationCandidates':copy.deepcopy(empty),
           'listeners':[{'endpoint':{'ip':'*','port':8848,'protocol':'tcp'},'pids':[11]}],
           'container':{'name':'nacos','ports':{'8848/tcp':[{'HostIp':'0.0.0.0','HostPort':'8083'}]}}}
    return {'sourceVersion':'v1','completedAt':'2026-10-29T00:00:03Z','results':[row]}, {'sourceVersion':'v1','results':[check]}


class PlanTest(unittest.TestCase):
    def test_docker_host_port_and_native_port(self):
        a,b=fixture(); r=n.plan(a,b);self.assertEqual((r['accepted'][0]['port'],r['accepted'][0]['portBasis']),(8083,'DOCKER_PUBLISHED'))
        del b['results'][0]['container'];b['results'][0]['hostNetwork']=True
        self.assertEqual(n.plan(a,b)['accepted'][0]['port'],8848)

    def test_unresolved_is_not_created(self):
        for problem in ('cluster','identity','app','hostapp','pid','mapping','truncation','config'):
            a,b=fixture();r=a['results'][0];check=b['results'][0]
            if problem=='cluster':r['standalone']=None
            if problem=='identity':check['startTicks']='101'
            if problem=='app':r['applicationCandidates']['TEST']=[{'appId':'1'}]
            if problem=='hostapp':check['hostApplicationCandidates']['PRODUCT']=[{'appId':'2'}]
            if problem=='pid':check['listeners'][0]['pids']=[11,12]
            if problem=='mapping':check['container']['ports']['8848/tcp'].append({'HostIp':'0.0.0.0','HostPort':'8084'})
            if problem=='truncation':r['connectionCount']=1
            if problem=='config':r['configEvidence']=['CONFIG_UNAVAILABLE']
            self.assertFalse(n.plan(a,b)['accepted'],problem)

    def test_version_mismatch(self):
        a,b=fixture();b['sourceVersion']='v2'
        with self.assertRaises(ValueError):n.plan(a,b)


if __name__=='__main__':unittest.main()
