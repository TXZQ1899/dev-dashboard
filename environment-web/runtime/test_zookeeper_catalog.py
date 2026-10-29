import copy
import unittest
import zookeeper_catalog as z


def sample():
    row = {'assetId':'asset-1','ip':'192.0.2.1','pid':21,'configPath':'/conf/zoo.cfg','status':'OBSERVED',
           'mode':'standalone','frames':5,'warnings':[],'startTicks':'42','observedAt':'2026-10-29T00:00:00Z',
           'configFields':['dataDir=/data','clientPort=2181'], 'applicationCandidates':{},
           'listeners':[{'ip':'*','port':2181,'protocol':'tcp'}], 'established':[], 'connectionCount':0}
    listener = {'ip':row['ip'],'pid':21,'status':'OBSERVED','warnings':[], 'observedAt':'2026-10-29T00:00:02Z',
                'listeners':[{'endpoint':{'ip':'*','port':2181,'protocol':'tcp'},'pids':[21]}]}
    return {'sourceVersion':'v1','results':[row]}, {'sourceVersion':'v1','results':[listener]}


class PlanTest(unittest.TestCase):
    def test_single_node_and_explicit_localhost(self):
        report, listeners = sample()
        self.assertEqual(len(z.plan(report,listeners)['accepted']),1)
        report['results'][0]['configFields'] = ['dataDir=/data','standaloneEnabled=true','server.1=localhost:2888:3888;2181']
        self.assertEqual(len(z.plan(report,listeners)['accepted']),1)

    def test_conflicts_and_ambiguity_never_create(self):
        for change in ('mode','dynamic','app','socket','owner','duplicate','truncated'):
            report, listeners = sample(); row=report['results'][0]
            if change=='mode': row['mode']='follower'
            if change=='dynamic': row['configFields'].append('dynamicConfigFile=/conf/dynamic')
            if change=='app': row['applicationCandidates']={'PRODUCT':[{'appId':'1'}]}
            if change=='socket': row['established']=[{'local':{'port':3888},'peer':{'port':5}}]; row['connectionCount']=1
            if change=='owner': listeners['results'][0]['listeners'][0]['pids']=[21,22]
            if change=='duplicate': report['results'].append(copy.deepcopy(row))
            if change=='truncated': row['connectionCount']=1
            self.assertEqual(z.plan(report,listeners)['accepted'],[],change)

    def test_versions_cannot_mix(self):
        a,b=sample(); b['sourceVersion']='v2'
        with self.assertRaises(ValueError): z.plan(a,b)


if __name__ == '__main__': unittest.main()
