"""Read-only DevOps and Codeup collection and exact repository reconciliation."""
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import Counter
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from export_devops import AuthenticationError, ExportError, CollectionError, NoRedirect, fetch_apps, app_row, environment_rows, ENVIRONMENTS, APP_FIELDS, ENV_FIELDS, write_csv
from export_devops_details import RuntimeClient, parse_steps, save_json
from export_codeup_groups import read_cookie, namespace_from_page, validate_group
from check_codeup_access import normalize_url

ROOT = Path(__file__).resolve().parent

_SLOT_NAME = re.compile(r'^(slot:\d+)/')


def inner_prefix():
    """Keep inner pool threads attributable to the slot that spawned them."""
    match = _SLOT_NAME.match(threading.current_thread().name)
    return match.group(1).replace(':', '-') + '-' if match else ''


DEVOPS = 'http://devops.folidaymall.com'
CODEUP = 'https://codeup.aliyun.com'
LOCAL_GITLAB = 'http://gitlab.dev.thomascook.com.cn'

# Repositories are fetched concurrently; one slow repo must not stall the rest.
CODEUP_WORKERS = 5


def normalize_local_gitlab_url(host, path):
    return f'{LOCAL_GITLAB}/{path.strip("/").removesuffix(".git")}' if host == 'gitlab.dev.thomascook.com.cn' else None


def now():
    return datetime.now().astimezone().isoformat()


def unique_records(records):
    """Remove exact duplicates only; same IP or deployment ID alone is not identity."""
    seen, result = set(), []
    for record in records:
        fingerprint = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if fingerprint not in seen:
            seen.add(fingerprint)
            result.append(record)
    return result


def latest_push_in(steps):
    candidates = []
    for step in steps:
        if re.sub(r'[\s_-]', '', step.get('operation', '')).lower() != 'pushin':
            continue
        stamp = step.get('endTime') or step.get('startTime')
        if not stamp:
            continue
        try:
            datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise ExportError('Push In 时间格式变化') from None
        candidates.append((stamp, step))
    if not candidates:
        return {'lastPublishedAt': '', 'publishStatus': '无 Push In 时间', 'publishTimeSource': '', 'pushIn': steps}
    stamp, chosen = max(candidates, key=lambda item: item[0])
    return {'lastPublishedAt': stamp, 'publishStatus': chosen['status'],
            'publishTimeSource': 'Push In endTime' if chosen.get('endTime') else 'Push In startTime（无完成时间）', 'pushIn': steps}


def collect_application(app, cookie, delay=.1):
    client = RuntimeClient(DEVOPS, cookie, timeout=25, delay=delay)
    ar = app_row(app, DEVOPS)
    result = {'id': str(app['id']), 'name': ar['应用名'], 'http': ar['HTTP名'], 'port': str(ar['HTTP端口'] or ''),
              'repository': ar['Git/代码库地址'], 'branch': ar['分支'], 'envs': {}, 'errors': [], 'capturedAt': now()}
    csv_rows = []
    configs = {}
    for env in ENVIRONMENTS:
        result['envs'][env] = []
        obj = client.get('/theone-web/ops/app/list/app', {'appId': app['id'], 'envtype': env}, allow_empty_environment=True)
        if isinstance(obj, dict) and isinstance(obj.get('deployList'), list):
            obj = {**obj, 'deployList': unique_records(obj['deployList'])}
        records = environment_rows(ar, env, obj)
        deployments = obj['deployList'] if obj else []
        for index, record in enumerate(records):
            row = {'ip': record.get('服务器IP', ''), 'deploy': str(record.get('部署ID', '')),
                   'config': str(record.get('配置ID', '')), 'status': record['状态'], 'error': '',
                   'port': result['port'], 'branch': '', 'branchSource': '', 'repository': result['repository'],
                   'lastPublishedAt': '', 'publishStatus': '无运行记录', 'publishTimeSource': '', 'pushIn': []}
            if deployments:
                deploy = deployments[index]
                if env == 'TEST' and deploy.get('configId'):
                    cid = deploy['configId']
                    if cid not in configs:
                        config = client.get('/theone-web/ops/instance/config/list', {'search.id_eq': cid, 'page.size': 1, 'page.pn': 1, 'sort.createTime': 'desc'})
                        content = config.get('content', []) if isinstance(config, dict) else []
                        if len(content) != 1 or str(content[0].get('id')) != str(cid):
                            raise ExportError('测试配置查询未返回对应记录')
                        configs[cid] = content[0]
                    config = configs[cid]
                    row['branch'] = config.get('versionControlName') or ''
                    row['repository'] = config.get('versionControlUrl') or result['repository']
                    row['branchSource'] = '测试实例配置 versionControlName'
                elif env != 'TEST':
                    row['branch'], row['branchSource'] = 'master', '非测试环境按约定使用 master'
                else:
                    row['branchSource'] = '缺少测试配置 ID'
                if deploy.get('sceneId') and deploy.get('deployId'):
                    params = {'sceneId': deploy['sceneId'], 'objectId': deploy['deployId'], 'objectType': 'deploy_type'}
                    page = client.read('/theone-web/ops/app/deploy/searchPlandetail', params)
                    if 'buildResourceClass' not in page:
                        # A valid empty runtime table is different from a login/error page.
                        if page.strip() and ('<table' not in page or '操作' not in page):
                            raise ExportError('运行详情不是可识别的步骤表格')
                    else:
                        states = []
                        for step in parse_steps(page):
                            if re.sub(r'[\s_-]', '', step['operation']).lower() != 'pushin':
                                continue
                            status = client.read('/theone-web/build/{build_id}/{resource_id}/{action_id}/actionRun'.format(**step), json_response=True)
                            states.append({**step, **{k: status.get(k) for k in ['startTime', 'endTime', 'status', 'toActionRunId']}})
                        row.update(latest_push_in(states))
            result['envs'][env].append(row)
            csv_rows.append({**record, 'HTTP端口': row['port'], 'Git分支': row['branch'], '分支来源': row['branchSource'],
                             '环境代码库地址': row['repository'], '最后发布时间': row['lastPublishedAt'], '发布状态': row['publishStatus'], '发布时间来源': row['publishTimeSource']})
    result['envs'] = {env: unique_records(rows) for env, rows in result['envs'].items()}
    result['csvRows'] = unique_records(csv_rows)
    return result


def collect_devops(folder, workers=4, app_id=None):
    cookie = read_cookie(ROOT / 'cookie.txt')
    raw = fetch_apps(RuntimeClient(DEVOPS, cookie, delay=.1), app_id=app_id)
    checkpoints = folder / 'applications'
    checkpoints.mkdir(exist_ok=True)
    results, errors = {}, []
    def job(app):
        target = checkpoints / f'{app["id"]}.json'
        if target.exists():
            existing = json.loads(target.read_text())
            if existing.get('complete'):
                return existing
        result = collect_application(app, cookie)
        result['complete'] = True
        save_json(target, result)
        return result
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=inner_prefix()) as pool:
        futures = {pool.submit(job, app): app for app in raw}
        for future in as_completed(futures):
            app = futures[future]
            try:
                results[str(app['id'])] = future.result()
            except AuthenticationError:
                for pending in futures: pending.cancel()
                raise
            except (ExportError, ValueError, KeyError, OSError) as exc:
                errors.append({'appId': app['id'], 'error': str(exc) if isinstance(exc, ExportError) else type(exc).__name__})
            if (len(results) + len(errors)) % 25 == 0:
                print(f'DevOps: {len(results)}/{len(raw)} applications; {len(errors)} errors', flush=True)
    save_json(folder / 'devops-errors.json', errors)
    if errors:
        raise ExportError(f'{len(errors)} 个应用采集失败；已保留检查点，可使用 --resume 重试')
    apps = [results[str(a['id'])] for a in raw]
    payload = {'complete': True, 'scope': 'single' if app_id else 'all', 'collectedAt': now(), 'source': folder.name, 'apps': apps}
    save_json(folder / 'devops.json', payload)
    write_csv(folder / 'applications.csv', APP_FIELDS, [app_row(a, DEVOPS) for a in raw])
    write_csv(folder / 'environments.csv', ENV_FIELDS + ['HTTP端口', 'Git分支', '分支来源', '环境代码库地址', '最后发布时间', '发布状态', '发布时间来源'], [r for a in apps for r in a['csvRows']])
    return payload


class CodeupClient:
    def __init__(self, cookie):
        self.cookie = cookie
        self.opener = build_opener(NoRedirect())

    def get(self, path, params=None, html=False):
        if path not in ('/groups', '/api/v4/groups/my', '/api/v3/projects/authorized/list', '/api/v3/projects/counts') and not re.fullmatch(r'/api/v3/projects/\d+/repository/(branches|commits)', path):
            raise ExportError('拒绝非白名单 Codeup 接口')
        for attempt in range(3):
            try:
                time.sleep(.15)
                request = Request(CODEUP + path + ('?' + urlencode(params) if params else ''), headers={'Cookie': self.cookie, 'Accept': 'text/html' if html else 'application/json', 'User-Agent': 'Mozilla/5.0'})
                with self.opener.open(request, timeout=30) as response:
                    body = response.read().decode('utf-8')
                if html: return body
                data = json.loads(body)
                if isinstance(data, dict) and (data.get('success') is False or str(data.get('errorCode', '')).upper() in ('UNAUTHORIZED', 'NOT_LOGIN')):
                    raise AuthenticationError('Codeup 登录态或权限异常，请更新 codeup-cookie.txt')
                return data
            except HTTPError as exc:
                if exc.code in (301,302,303,307,308,401,403):
                    raise AuthenticationError('Codeup 登录失效或权限异常，请更新 codeup-cookie.txt') from None
                if exc.code not in (429,500,502,503,504): raise ExportError(f'Codeup HTTP {exc.code}') from None
            except (ValueError, UnicodeError):
                raise AuthenticationError('Codeup 未返回有效 JSON，请更新登录态') from None
            except (URLError, TimeoutError, OSError):
                pass
            if attempt == 2: raise ExportError('Codeup 连接失败或服务异常')
            time.sleep(2 ** attempt)

    def path_info(self, path):
        request = Request(CODEUP + '/portal/path_info?' + urlencode({'path':'/' + path.lstrip('/')}), headers={'Cookie':self.cookie, 'Accept':'application/json', 'User-Agent':'Mozilla/5.0'})
        try:
            with self.opener.open(request, timeout=30) as response: data=json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code in (401,403): raise AuthenticationError('Codeup 登录失效或权限异常，请更新 Cookie') from None
            raise ExportError(f'Codeup path_info HTTP {exc.code}') from None
        if not isinstance(data,dict): raise ExportError('Codeup path_info 响应格式变化')
        code=str(data.get('errorCode') or '').upper()
        if data.get('success') is True: return {'access':'是','reason':'仓库查询成功（网页读取权限）'}
        if code == 'NOT_FOUND': return {'access':'地址不存在','reason':'Codeup 返回 NOT_FOUND：地址不存在'}
        if code == 'FORBIDDEN': return {'access':'暂无权限访问','reason':'Codeup 返回 FORBIDDEN：暂无权限访问'}
        if code in ('UNAUTHORIZED','NOT_LOGIN'): raise AuthenticationError('Codeup 登录失效或权限异常，请更新 Cookie')
        return {'access':'无法确认','reason':data.get('errorMessage') or 'Codeup 接口未提供明确访问状态'}


def codeup_reference_status(devops, codeup):
    client=CodeupClient(read_cookie(ROOT / 'codeup-cookie.txt')); result={}
    listed={r['url'] for r in codeup['repos']}
    references=set()
    for app in devops.get('apps',[]): references |= {app.get('repository','')} | {r.get('repository','') for rows in app.get('envs',{}).values() for r in rows}
    for raw in sorted(references):
        if not raw: continue
        try: host,path,url=normalize_url(raw)
        except ValueError: continue
        if host == 'codeup.aliyun.com' and url not in listed: result[url]=client.path_info(path)
    return result


def paginate(client, path, params):
    result, seen = [], set()
    for page in range(1,10001):
        rows = client.get(path, {**params, 'page': page, 'per_page': 100})
        if not isinstance(rows, list): raise ExportError('Codeup 分页响应格式变化')
        if not rows: return result
        for row in rows:
            if not isinstance(row, dict) or row.get('id') is None: raise ExportError('Codeup 记录缺少 ID')
            if str(row['id']) in seen: raise ExportError('Codeup 分页重复，请在列表稳定后重新采集')
            seen.add(str(row['id'])); result.append(row)
        print(f'Codeup {path}: page {page}, {len(result)} records', flush=True)
        if len(rows) < 100: return result
    raise ExportError('Codeup 分页超过安全上限')


def normalize_repository(row):
    host, path, url = normalize_url(row.get('http_url_to_repo') or row['web_url'])
    if host != 'codeup.aliyun.com': raise ExportError('Codeup 返回非预期仓库域名')
    stats = row.get('basic_statistics') or {}
    numbers = {}
    for dest, source in [('branches','total_branch'),('mergeRequests','total_change_requests'),('commits','total_commits')]:
        value = stats.get(source)
        if not isinstance(value,int) or isinstance(value,bool) or value < 0:
            raise ExportError(f'Codeup 仓库统计字段 {source} 无效，不将缺失值当作 0')
        numbers[dest] = value
    return {'codeupId':str(row['id']), 'name':row['name'], 'path':path, 'url':url,
            'namespaceId':str(row['namespace_id']), 'groupName':path.split('/')[-2],
            'description':row.get('description') or '', **numbers}


def collect_commit_history(client, repo, heads):
    known = {}
    pending = dict.fromkeys(reversed(heads))
    while pending:
        head, _ = pending.popitem()
        if head in known: continue
        # Freeze each request to a SHA. Follow missing parents in batches, so
        # shared branch histories are never scanned again just to reach an old fork.
        rows=client.get('/api/v3/projects/'+repo['codeupId']+'/repository/commits',
                        {'ref_name':head,'page':1,'per_page':100})
        if not isinstance(rows,list) or not any(c.get('id') == head for c in rows):
            raise ExportError('提交历史不完整，请求的提交未返回')
        for commit in rows:
            cid=commit.get('id');parents=commit.get('parent_ids');stamp=commit.get('committed_date')
            if not cid or not isinstance(parents,list) or not stamp: raise ExportError('提交记录缺少 ID、日期或父提交')
            dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if dt.tzinfo is None: raise ExportError('提交时间缺少时区')
            pending.pop(cid,None)
            if cid in known: continue
            known[cid]=stamp
            for parent in parents:
                if parent not in known: pending[parent]=None
    daily=Counter(datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for t in known.values())
    repo.update(reportedCommits=repo['commits'],commits=len(known),commitHistoryComplete=True,
                commitHistoryScope='all_current_branches_unique_sha',commitDailyCounts=dict(sorted(daily.items())))
    if known:
        cid,stamp=max(known.items(),key=lambda p:datetime.fromisoformat(p[1].replace('Z','+00:00')))
        repo.update(lastCommittedAt=stamp,lastCommitId=cid,commitTimeScope='all_current_branches_unique_sha')
        if cid not in heads: repo['lastCommitBranch']=''
    return repo


def collect_commit_time(client, repo, with_history=False):
    seen, commits = set(), []
    for page in range(1,10001):
        rows = client.get('/api/v3/projects/'+repo['codeupId']+'/repository/branches', {'page':page,'per_page':100})
        if not isinstance(rows,list): raise ExportError('Codeup 分支响应格式变化')
        if not rows: break
        for row in rows:
            name=row.get('name')
            if not name or name in seen: raise ExportError('Codeup 分支分页重复')
            seen.add(name)
            commit=row.get('commit') or {}
            stamp=commit.get('committed_date')
            if not stamp or datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:
                raise ExportError('缺少真实代码提交时间')
            commits.append((datetime.fromisoformat(stamp.replace('Z','+00:00')),stamp,commit['id'],name))
    else: raise ExportError('Codeup 分支分页超过上限')
    repo['reportedBranchCount'] = repo['branches']
    repo['branches'] = len(seen)
    latest=max(commits,key=lambda c:c[0]) if commits else None
    repo.update(lastCommittedAt=latest[1] if latest else '',lastCommitId=latest[2] if latest else '',
                lastCommitBranch=latest[3] if latest else '',commitTimeStatus='available' if latest else 'empty',
                commitTimeScope='current_branch_heads')
    repo.pop('updatedAt',None)
    if with_history: collect_commit_history(client,repo,[c[2] for c in sorted(commits,reverse=True)])
    return repo


def collect_codeup(folder):
    client = CodeupClient(read_cookie(ROOT / 'codeup-cookie.txt'))
    namespace = namespace_from_page(client.get('/groups', {'navKey':'mine'}, html=True))
    before = client.get('/api/v3/projects/counts', {'search':'','contains_sub_projects':'true','archived':'false'})
    groups = [validate_group(r) for r in paginate(client, '/api/v4/groups/my', {'parent_id':namespace, 'order_by':'updated_at','search':'','sort':''})]
    repos = [normalize_repository(r) for r in paginate(client, '/api/v3/projects/authorized/list', {'order_by':'last_activity_at','contains_sub_projects':'true','search':'','group_by':'','archived':'false'})]
    save_json(folder/'progress.json', {'phase':'正在采集 Codeup 仓库分支和提交历史','completed':0,'total':len(repos)})
    (folder/'commit-times').mkdir(parents=True, exist_ok=True)
    # Codeup kills long runs on per-repo latency; five workers keeps each repo's
    # branch/history walk concurrent without widening load on the shared cluster.
    with ThreadPoolExecutor(max_workers=CODEUP_WORKERS, thread_name_prefix=inner_prefix()) as pool:
        tasks = [pool.submit(collect_commit_time, CodeupClient(client.cookie), repo, True) for repo in repos]
        for index, task in enumerate(as_completed(tasks)):
            try: repo = task.result()
            except Exception as exc:
                print(f'Codeup history failed: {type(exc).__name__}: {exc}', flush=True)
                for pending in tasks: pending.cancel()
                raise
            save_json(folder/'commit-times'/(repo['codeupId']+'.json'), repo)
            save_json(folder/'progress.json', {'phase':'正在采集 Codeup 仓库分支和提交历史','completed':index+1,'total':len(repos),'current':repo['name']})
            print(f'Codeup commit time: {index+1}/{len(repos)}', flush=True)
    after = client.get('/api/v3/projects/counts', {'search':'','contains_sub_projects':'true','archived':'false'})
    if not isinstance(before,dict) or not isinstance(after,dict) or before.get('authorized') != len(repos) or after.get('authorized') != len(repos):
        raise ExportError('Codeup 可访问仓库总数与分页清单不一致，或采集期间总数发生变化')
    if len({r['url'] for r in repos}) != len(repos): raise ExportError('Codeup 仓库完整地址重复')
    result = {'complete':True, 'collectedAt':now(), 'namespaceId':namespace, 'scope':'mine', 'groups':groups, 'repos':repos}
    save_json(folder / 'codeup.json',result)
    write_csv(folder/'codeup-groups.csv',['代码组描述','代码组名','代码库数','完整路径'],[{'代码组描述':g['chinese_name'],'代码组名':g['group_name'],'代码库数':g['repository_count'],'完整路径':g['full_path']} for g in groups])
    write_csv(folder/'codeup-repositories.csv',['组名','库名','Git地址','分支数','合并请求数','提交数','最近代码提交时间'],[dict(zip(['组名','库名','Git地址','分支数','合并请求数','提交数','最近代码提交时间'],[r['groupName'],r['name'],r['url'],r['branches'],r['mergeRequests'],r['commits'],r['lastCommittedAt']])) for r in repos])
    return result


class LocalGitlabClient:
    def __init__(self, cookie):
        self.cookie = cookie
        self.opener = build_opener(NoRedirect())

    def request_rows(self, path, page, label):
        params={'per_page':100,'page':page}
        if path=='/api/v3/projects': params.update(order_by='last_activity_at',sort='desc')
        request=Request(LOCAL_GITLAB+path+'?'+urlencode(params),headers={'Cookie':self.cookie,'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        for attempt in range(3):
            try:
                print(f'Local GitLab 请求：{label}，page={page}，第 {attempt+1}/3 次',flush=True)
                with self.opener.open(request,timeout=30) as response:
                    body=response.read().decode('utf-8')
                    headers={k.lower():v for k,v in response.headers.items()}
                try: batch=json.loads(body)
                except (ValueError,UnicodeError):
                    if any(marker in body.lower() for marker in ('users/sign_in','user_login','sign in')):
                        raise AuthenticationError('Local GitLab 返回登录页') from None
                    raise CollectionError(f'Local GitLab {label} 第 {page} 页未返回有效 JSON') from None
                if isinstance(batch,dict) and str(batch.get('message','')).startswith('401'):
                    raise AuthenticationError('Local GitLab 未登录')
                if not isinstance(batch,list): raise CollectionError(f'Local GitLab {label} 第 {page} 页响应格式变化')
                return batch,headers
            except HTTPError as exc:
                if exc.code in (301,302,303,307,308,401): raise AuthenticationError('Local GitLab 登录失效，请更新 Cookie') from None
                if exc.code==403: raise CollectionError(f'Local GitLab {label} 读取权限不足（HTTP 403）') from None
                if exc.code not in (429,500,502,503,504): raise CollectionError(f'Local GitLab {label} 第 {page} 页 HTTP {exc.code}') from None
                reason=f'HTTP {exc.code}'
            except (URLError,TimeoutError,OSError):
                reason='连接失败或超时，请检查内网 / VPN 和 GitLab 服务'
            if attempt<2: time.sleep(2**attempt)
        raise CollectionError(f'Local GitLab {label} 第 {page} 页重试 3 次后仍失败：{reason}')

    def get_projects(self, page):
        return self.request_rows('/api/v3/projects',page,'项目列表')[0]

    def get_project_rows(self, project_id, resource):
        rows=[]; seen=set()
        label=f'项目 {project_id} / {resource}'
        for page in range(1,10001):
            path=f'/api/v3/projects/{project_id}/repository/{resource}' if resource in ('branches','commits') else f'/api/v3/projects/{project_id}/{resource}'
            batch,headers=self.request_rows(path,page,label)
            fingerprint=hashlib.sha256(json.dumps(batch,sort_keys=True).encode()).hexdigest()
            if batch and fingerprint in seen:
                raise CollectionError(f'Local GitLab {label} 第 {page} 页重复，已停止采集以避免重复计数')
            seen.add(fingerprint)
            rows.extend(batch)
            # This legacy v3 branches endpoint returns the entire list and ignores page/per_page.
            paginated=any(k in headers for k in ('x-page','x-next-page','x-total-pages','link'))
            if resource=='branches' and not paginated: return rows
            if 'x-next-page' in headers:
                if not headers['x-next-page'].strip(): return rows
                if headers['x-next-page'].strip()!=str(page+1):
                    raise CollectionError(f'Local GitLab {label} 返回不连续的分页信息')
            elif len(batch)<100: return rows
        raise CollectionError(f'Local GitLab {label} 分页超过安全上限')


def collect_local_gitlab(cookie, progress_path=None):
    client=LocalGitlabClient(cookie); repos=[]
    for page in range(1,10001):
        rows=client.get_projects(page)
        if not rows: break
        print(f'Local GitLab 项目列表：第 {page} 页，已读取 {len(repos) + len(rows)} 个项目', flush=True)
        for row in rows:
            namespace=row.get('namespace') or {}; path=row.get('path_with_namespace'); group_path=path.rsplit('/',1)[0] if isinstance(path,str) and '/' in path else ''
            if not row.get('id') or not path or not group_path: raise ExportError('Local GitLab 项目缺少 ID、完整路径或代码组')
            print(f"Local GitLab 项目：{path}，正在采集分支、提交和合并请求", flush=True)
            branches=client.get_project_rows(row['id'],'branches')
            commits=client.get_project_rows(row['id'],'commits')
            merge_requests=client.get_project_rows(row['id'],'merge_requests')
            latest=max((c for c in commits if c.get('committed_date')),key=lambda c:c['committed_date'],default=None)
            repos.append({'localGitlabId':str(row['id']),'name':row.get('name') or row.get('path'),'path':path,'url':(row.get('web_url') or LOCAL_GITLAB+'/'+path).rstrip('/').removesuffix('.git'),'namespaceId':str(namespace.get('id') or ''),'groupName':group_path.split('/')[-1],'groupPath':group_path,'description':row.get('description') or '','branches':len(branches),'mergeRequests':len(merge_requests),'commits':len(commits),'lastCommittedAt':latest.get('committed_date','') if latest else '','commitTimeStatus':'available' if latest else 'empty','source':'local_gitlab'})
            print(f"Local GitLab 项目完成：{path}，分支 {len(branches)}，提交 {len(commits)}，合并请求 {len(merge_requests)}", flush=True)
            if progress_path: save_json(progress_path, {'phase':'正在采集 Local GitLab 项目统计','completed':len(repos),'total':0,'current':path})
        if len(rows)<100: break
    else: raise ExportError('Local GitLab 项目分页超过安全上限')
    if len({r['url'] for r in repos})!=len(repos): raise ExportError('Local GitLab 仓库完整地址重复')
    return {'complete':True,'collectedAt':now(),'scope':'mine','repos':repos}


def reconcile(devops, codeup, local_gitlab=None, codeup_status=None):
    if not devops.get('complete') or devops.get('scope')!='all' or not codeup.get('complete'):
        raise ExportError('仅完整全量快照可进行仓库差异对比')
    groups = [{'id':g['id'], 'name':g['group_name'], 'description':g['chinese_name'], 'path':g['full_path'], 'total':g['repository_count'], 'listed':True} for g in codeup['groups']]
    repos, invalid = {}, []
    def key(url):
        return hashlib.sha256(url.encode()).hexdigest()[:20]
    def find_group(path):
        matches=[g for g in groups if path.startswith(g['path']+'/')]
        return max(matches,key=lambda g:len(g['path']))['id'] if matches else ''
    for r in codeup['repos']:
        group_id = find_group(r['path'])
        if not group_id:
            # Repository-only membership can expose a repo without exposing its group.
            group_id = 'visible-namespace-' + r['namespaceId']
            if not any(g['id']==group_id for g in groups):
                groups.append({'id':group_id,'name':r['groupName'],'description':'仅通过可访问仓库识别，未出现在我的代码组列表','path':r['path'].rsplit('/',1)[0],'total':0,'listed':False})
        repos[r['url']]={**r,'id':key(r['url']),'groupId':group_id,'access':'是','reason':'当前账号可访问仓库列表返回','match':'完整仓库地址匹配','apps':[],'source':'codeup','inCodeup':True,'inDevops':False,'difference':'codeup_only'}
    for r in (local_gitlab or {}).get('repos', []):
        group_id='local-gitlab-'+r['groupPath'].replace('/','-')
        if not any(g['id']==group_id for g in groups): groups.append({'id':group_id,'name':r['groupName'],'description':'Local GitLab 代码组','path':r['groupPath'],'total':0,'listed':True})
        repos[r['url']]={**r,'id':key(r['url']),'groupId':group_id,'access':'是','reason':'当前账号可访问 Local GitLab 项目列表返回','match':'完整仓库地址匹配','apps':[],'inCodeup':False,'inDevops':False,'difference':'codeup_only'}
    for app in devops['apps']:
        # Include TEST overrides as well as the application's default repository.
        urls = {app['repository']} | {r.get('repository','') for rows in app['envs'].values() for r in rows}
        for raw in sorted(urls):
            if not raw: continue
            try:
                host,path,url=normalize_url(raw)
                url=normalize_local_gitlab_url(host,path) or url
            except ValueError as exc:
                invalid.append({'appId':app['id'],'appName':app['name'],'reason':str(exc)})
                continue
            if url not in repos:
                repos[url]={'id':key(url),'codeupId':'','name':path.split('/')[-1],'path':path,'url':url,'groupName':path.split('/')[-2],
                            'groupId':find_group(path) if host=='codeup.aliyun.com' else '',
                            # 非 Codeup 域名未做访问检查，只能标为待核实，不能断言不可访问。
                            'access':(codeup_status or {}).get(url,{}).get('access','无法确认') if host=='codeup.aliyun.com' else '无法确认',
                            'reason':(codeup_status or {}).get(url,{}).get('reason','未出现在当前账号可访问 Codeup 清单，不能据此判断仓库已删除') if host=='codeup.aliyun.com' else '未出现在当前账号可访问 Local GitLab 清单，可能无权限或项目已不存在',
                            'match':'完整地址未匹配','apps':[],'source':'local_gitlab' if host=='gitlab.dev.thomascook.com.cn' else 'codeup','inCodeup':False,'inDevops':True,'difference':'devops_only',
                            'branches':None,'mergeRequests':None,'commits':None,'lastCommittedAt':'','commitTimeStatus':'unavailable','description':''}
            repo=repos[url]
            repo['inDevops']=True
            repo['difference']='both' if repo['inCodeup'] else 'devops_only'
            if not any(a['id']==app['id'] for a in repo['apps']):repo['apps'].append({'id':app['id'],'name':app['name'],'branch':app['branch']})
    codeup_by_name={}
    for candidate in codeup['repos']:
        codeup_by_name.setdefault(candidate['name'].lower(), []).append(candidate)
    for repo in repos.values():
        if repo.get('inDevops') and not repo.get('inCodeup') and repo.get('source') == 'codeup':
            candidates=codeup_by_name.get(repo['name'].lower(), [])
            if candidates:
                repo['possibleCodeupMatches']=[{'id':key(c['url']),'name':c['name'],'url':c['url'],'groupName':c['groupName'],'path':c['path']} for c in candidates]
    for g in groups:
        if not g['listed']:g['total']=sum(r['groupId']==g['id'] and r['inCodeup'] for r in repos.values())
        if g['id'].startswith('local-gitlab-'): g['total']=sum(r['groupId']==g['id'] for r in repos.values())
    result={'source':devops['source'],'snapshotDate':codeup['collectedAt'],'accessDate':codeup['collectedAt'],
            'complete':True,'scope':'mine','groups':groups,'repos':sorted(repos.values(),key=lambda r:r['url']),
            'invalidReferences':invalid,'devopsCollectedAt':devops['collectedAt'],'codeupCollectedAt':codeup['collectedAt']}
    result['comparison']={k:sum(r['difference']==k for r in repos.values()) for k in ['both','codeup_only','devops_only']}
    return result
