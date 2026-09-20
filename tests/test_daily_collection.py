import unittest
from unittest.mock import patch
from daily_collection import latest_push_in, reconcile, normalize_repository, paginate, collect_application, collect_commit_time, collect_commit_history, ExportError, unique_records


def app(url):
    return {'id':'1','name':'a','branch':'master','repository':url,'envs':{'TEST':[], 'SIMULATION':[], 'PRODUCT':[]}}


def codeup_repo(url):
    return {'codeupId':'2','name':'repo','path':'org/group/repo','url':url,'namespaceId':'g','groupName':'group','description':'','updatedAt':'2026-09-07T18:00:00+08:00','branches':1,'mergeRequests':0,'commits':2}


class DailyTests(unittest.TestCase):
    def test_history_deduplicates_merge_parents_and_branches(self):
        def c(cid, parents, date='2026-09-13T17:00:00Z'):
            return {'id':cid,'parent_ids':parents,'committed_date':date}
        class Client:
            calls=[]
            def get(self,path,params):
                self.calls.append(params['ref_name'])
                return {'merge':[c('merge',['a','b']),c('a',['root']),c('b',['root']),c('root',[])], 'other':[c('other',['a']),c('a',['root'])]}[params['ref_name']]
        client=Client();r=collect_commit_history(client,{'codeupId':'1','commits':1001},['merge','a','other'])
        self.assertEqual(r['commits'],5)
        self.assertEqual(r['commitDailyCounts'],{'2026-09-14':5})
        self.assertEqual(client.calls,['merge','other'])
        self.assertEqual(r['reportedCommits'],1001)

    def test_history_follows_missing_parent_sha_instead_of_rescanning_branch(self):
        class Client:
            refs=[]
            def get(self,path,params):
                self.refs.append(params['ref_name'])
                return [{'id':params['ref_name'],'parent_ids':['root'] if params['ref_name']=='head' else [],'committed_date':'2026-01-01T00:00:00Z'}]
        c=Client();r=collect_commit_history(c,{'codeupId':'1','commits':1001},['head'])
        self.assertEqual(c.refs,['head','root']);self.assertEqual(r['commits'],2)

    def test_incomplete_history_is_rejected(self):
        class Client:
            def get(self,path,params):
                return [{'id':'h','parent_ids':['missing'],'committed_date':'2026-09-14T00:00:00Z'}] if params['ref_name']=='h' else []
        with self.assertRaises(ExportError):collect_commit_history(Client(),{'codeupId':'1','commits':1},['h'])

    def test_commit_time_uses_committer_date_across_branches(self):
        class Client:
            def get(self,path,params):return [] if params['page']>1 else [
                {'name':'master','commit':{'id':'a','committed_date':'2026-01-01T00:00:00+08:00'}},
                {'name':'test','commit':{'id':'b','committed_date':'2026-09-01T00:00:00+08:00'}}]
        repo={'codeupId':'1','branches':2,'updatedAt':'2026-09-14T00:00:00+08:00'}
        collect_commit_time(Client(),repo)
        self.assertEqual(repo['lastCommittedAt'],'2026-09-01T00:00:00+08:00')
        self.assertEqual(repo['lastCommitBranch'],'test')
        self.assertNotIn('updatedAt',repo)

    def test_missing_commit_date_fails_instead_of_using_activity(self):
        class Client:
            def get(self,*args):return [{'name':'master','commit':{'id':'a','created_at':'today'}}]
        with self.assertRaises(ExportError):collect_commit_time(Client(),{'codeupId':'1','branches':1})

    def test_collection_deduplication_keeps_config_and_deployment_alignment(self):
        calls=[]
        class Client:
            def __init__(self,*args,**kwargs):pass
            def get(self,path,params,**kwargs):
                if path.endswith('/list/app'):
                    if params['envtype']!='TEST':return None
                    first={'ip':'10.179.1.224','deployId':16411,'configId':14703}
                    return {'deployList':[first,dict(first),{'ip':'10.0.0.0','deployId':16798,'configId':15408}]}
                cid=params['search.id_eq'];calls.append(cid)
                return {'content':[{'id':cid,'versionControlName':str(cid)}]}
        with patch('daily_collection.RuntimeClient',Client):
            result=collect_application({'id':1562,'appName':'static'},'fake')
        self.assertEqual(calls,[14703,15408])
        self.assertEqual([(r['deploy'],r['branch']) for r in result['envs']['TEST']],[('16411','14703'),('16798','15408')])
        self.assertEqual(len([r for r in result['csvRows'] if r['环境代码']=='TEST']),2)

    def test_exact_duplicate_deployments_removed_without_collapsing_distinct_records(self):
        row = {'ip':'10.179.1.224','deployId':16411,'configId':14703,'port':'8001'}
        duplicate = dict(reversed(list(row.items())))
        rows = [row, duplicate, {**row,'deployId':16412}, {**row,'configId':14704}, {**row,'port':'8002'}]
        self.assertEqual(len(unique_records(rows)), 4)
        self.assertEqual(len(rows), 5)

    def test_last_push_in_uses_time_not_table_order_and_preserves_failed_state(self):
        steps=[{'operation':'Push In','startTime':'2026-09-07 10:00:00','endTime':'2026-09-07 10:01:00','status':'FAILED'}, {'operation':'PushIn','endTime':'2026-09-06 09:00:00','status':'SUCCESS'}, {'operation':'Deploy','endTime':'2026-09-08 09:00:00','status':'SUCCESS'}]
        value=latest_push_in(steps)
        self.assertEqual(value['lastPublishedAt'],'2026-09-07 10:01:00')
        self.assertEqual(value['publishStatus'],'FAILED')
        self.assertEqual(latest_push_in([])['lastPublishedAt'],'')

    def test_repo_statistics_do_not_fabricate_missing_values(self):
        row={'http_url_to_repo':'https://codeup.aliyun.com/org/repo.git','name':'repo','namespace_id':2,'id':1,'updated_at':'2026-09-07T00:00:00+08:00'}
        with self.assertRaises(ExportError):normalize_repository(row)
        row['basic_statistics']={'total_branch':0,'total_change_requests':0,'total_commits':0}
        self.assertEqual(normalize_repository(row)['branches'],0)

    def test_exact_urls_handle_ssh_test_overrides_and_bidirectional_difference(self):
        url='https://codeup.aliyun.com/org/group/repo.git'
        a=app('git@codeup.aliyun.com:org/group/repo.git')
        a['envs']['TEST']=[{'repository':'https://other.example/org/group/repo.git'}]
        cp={'complete':True,'collectedAt':'2026-09-07','groups':[],'repos':[codeup_repo(url),{**codeup_repo('https://codeup.aliyun.com/org/group/unused.git'),'path':'org/group/unused','name':'unused'}]}
        d={'complete':True,'scope':'all','source':'fixture','collectedAt':'2026-09-07','apps':[a]}
        r=reconcile(d,cp)
        self.assertEqual(r['comparison'],{'both':1,'codeup_only':1,'devops_only':1})
        missing=next(x for x in r['repos'] if x['difference']=='devops_only')
        self.assertEqual(missing['access'],'无法确认')
        self.assertEqual(next(x for x in r['repos'] if x['difference']=='both')['apps'][0]['id'],'1')
        with self.assertRaises(ExportError):reconcile({**d,'complete':False},cp)
        with self.assertRaises(ExportError):reconcile({**d,'scope':'single'},cp)

    def test_codeup_repositories_are_fetched_five_at_a_time(self):
        # 369 repositories at four workers took ~18 minutes; five keeps each repo's
        # branch/history walk concurrent without widening cluster load.
        import tempfile, threading, time
        from pathlib import Path as P
        import daily_collection as dc
        lock = threading.Lock()
        state = {'inflight': 0, 'peak': 0}
        class Client:
            def __init__(self, cookie): self.cookie = cookie
            def get(self, path, params=None, html=False):
                with lock:
                    state['inflight'] += 1
                    state['peak'] = max(state['peak'], state['inflight'])
                time.sleep(0.05)
                with lock:
                    state['inflight'] -= 1
                if html: return "organization: {namespace_id: '1'}"
                if path == '/api/v3/projects/counts': return {'authorized': 20}
                if path == '/api/v4/groups/my': return []
                if path == '/api/v3/projects/authorized/list':
                    return [] if (params and params.get('page', 1) > 1) else [
                        {'id': i, 'name': f'r{i}', 'path_with_namespace': f'g/r{i}', 'namespace_id': '1',
                         'web_url': f'https://codeup.aliyun.com/g/r{i}',
                         'basic_statistics': {'total_branch': 1, 'total_change_requests': 0, 'total_commits': 5}}
                        for i in range(1, 21)]
                if path.endswith('/repository/branches'):
                    return [] if params.get('page', 1) > 1 else [{'name': 'm', 'commit': {'id': 'a', 'committed_date': '2026-09-01T00:00:00Z'}}]
                if path.endswith('/repository/commits'):
                    return [{'id': 'a', 'parent_ids': [], 'committed_date': '2026-09-01T00:00:00Z'}]
                return []
        original_client, original_root, original_workers = dc.CodeupClient, dc.ROOT, dc.CODEUP_WORKERS
        try:
            dc.CodeupClient = Client
            dc.ROOT = P(tempfile.mkdtemp())
            (dc.ROOT / 'codeup-cookie.txt').write_text('x')
            with tempfile.TemporaryDirectory() as tmp:
                dc.collect_codeup(P(tmp))
            # Five concurrent repositories, never more.
            self.assertEqual(dc.CODEUP_WORKERS, 5)
            self.assertEqual(state['peak'], 5)
        finally:
            dc.CodeupClient, dc.ROOT, dc.CODEUP_WORKERS = original_client, original_root, original_workers

    def test_codeup_keeps_every_repo_when_one_fails(self):
        # A single failing repository must abort the run, not silently drop data.
        import tempfile
        from pathlib import Path as P
        import daily_collection as dc
        class Client:
            def __init__(self, cookie): self.cookie = cookie
            def get(self, path, params=None, html=False):
                if html: return "organization: {namespace_id: '1'}"
                if path == '/api/v3/projects/counts': return {'authorized': 2}
                if path == '/api/v4/groups/my': return []
                if path == '/api/v3/projects/authorized/list':
                    return [] if (params and params.get('page', 1) > 1) else [
                        {'id': i, 'name': f'r{i}', 'path_with_namespace': f'g/r{i}', 'namespace_id': '1',
                         'web_url': f'https://codeup.aliyun.com/g/r{i}',
                         'basic_statistics': {'total_branch': 1, 'total_change_requests': 0, 'total_commits': 5}}
                        for i in (1, 2)]
                if path.endswith('/repository/branches'):
                    # Repo 2 has no usable commit date; the run must fail loudly.
                    if '/projects/2/' in path: return [{'name': 'm', 'commit': {'id': 'a', 'created_at': 'today'}}]
                    return [] if params.get('page', 1) > 1 else [{'name': 'm', 'commit': {'id': 'a', 'committed_date': '2026-09-01T00:00:00Z'}}]
                if path.endswith('/repository/commits'):
                    return [{'id': 'a', 'parent_ids': [], 'committed_date': '2026-09-01T00:00:00Z'}]
                return []
        original_client, original_root = dc.CodeupClient, dc.ROOT
        try:
            dc.CodeupClient = Client
            dc.ROOT = P(tempfile.mkdtemp())
            (dc.ROOT / 'codeup-cookie.txt').write_text('x')
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ExportError):
                    dc.collect_codeup(P(tmp))
        finally:
            dc.CodeupClient, dc.ROOT = original_client, original_root

    def test_pagination_stops_on_short_page_and_rejects_repetition(self):
        class Client:
            def __init__(self,pages):self.pages=iter(pages)
            def get(self,*args):return next(self.pages)
        self.assertEqual(len(paginate(Client([[{'id':1}]]),'/x',{})),1)
        full=[{'id':n} for n in range(100)]
        with self.assertRaises(ExportError):paginate(Client([full,full]),'/x',{})

    def test_empty_environments_and_runtime_are_not_errors(self):
        class Client:
            def __init__(self,*args,**kwargs):pass
            def get(self,path,params,**kwargs):
                if params.get('envtype')=='TEST':return {'deployList':[{'ip':'10.0.0.1','deployId':1,'sceneId':2,'configId':3}]}
                if 'config/list' in path:return {'content':[{'id':3,'versionControlName':'feature/test','versionControlUrl':'https://codeup.aliyun.com/org/r.git'}]}
                return None
            def read(self,*args,**kwargs):return '\r\n\n'
        with patch('daily_collection.RuntimeClient',Client):
            result=collect_application({'id':1,'appName':'x','httpPort':'80'},'not-a-real-cookie')
        self.assertEqual(result['envs']['TEST'][0]['branch'],'feature/test')
        self.assertEqual(result['envs']['TEST'][0]['publishStatus'],'无运行记录')
        self.assertEqual(result['envs']['PRODUCT'][0]['status'],'无环境配置')


if __name__=='__main__':unittest.main()
