"""Collect a new, isolated snapshot using credentials supplied by the local service."""
import io
import json
import hashlib
import queue
import re
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import daily_collection as daily
from export_devops import NoRedirect, ExportError, AuthenticationError, CollectionError
from service import write
from server_inventory import collect_servers
from aliyun import collect_clb, collect_ecs, normalize_clb, ecs_snapshot
from export_nat import collect as collect_nat, normalize_nat

# Readers must never observe half-written progress or snapshot JSON.
daily.save_json = write

# Three sources collect concurrently in three reusable slots. Each worker thread is
# named "slot:N" (inner pools inherit the prefix) so stdout can be teed into one log
# file per slot, while the combined collection.log stays complete for post-mortems.
SLOT_THREAD_RE = re.compile(r'^(?:slot:(\d+)(?:/|$)|slot-(\d+)-)')




def current_slot():
    match = SLOT_THREAD_RE.match(threading.current_thread().name)
    if not match:
        return None
    return match.group(1) or match.group(2)


def slot_prefix():
    """Name inner pool threads after the slot running in this thread."""
    slot = current_slot()
    return 'slot:' + slot + '/' if slot else 'slot:unknown/'


class SlotLogRouter(io.TextIOBase):
    """Tee stdout into per-slot logs while keeping the combined log complete."""
    def __init__(self, original, directory):
        self._original = original
        self._directory = directory
        self._lock = threading.Lock()
        self._handles = {}

    def write(self, text):
        self._original.write(text)
        slot = current_slot()
        if slot and text:
            with self._lock:
                handle = self._handles.get(slot)
                if handle is None:
                    self._directory.mkdir(parents=True, exist_ok=True)
                    handle = (self._directory / (slot + '.log')).open('a')
                    self._handles[slot] = handle
                handle.write(text)
                handle.flush()
        return len(text)

    def flush(self):
        self._original.flush()
        with self._lock:
            for handle in self._handles.values():
                handle.flush()


def install_slot_logs(directory):
    sys.stdout = SlotLogRouter(sys.stdout, directory)


def jumpserver_getter(cookie, fetcher=None):
    """Authorized-API reader; `fetcher` is a test seam, production talks to the real endpoint."""
    if fetcher is not None:
        return fetcher
    opener = build_opener(NoRedirect)
    def get(path):
        request = Request('http://10.179.2.146:8080' + path,
                          headers={'Cookie': cookie, 'Accept': 'application/json'})
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    return get


def jumpserver(cookie, folder, password="", fetcher=None):
    get = jumpserver_getter(cookie, fetcher)
    def assets(path):
        rows, expected = [], None
        for _ in range(1000):
            data = get(path + '?limit=100&offset=' + str(len(rows)))
            if expected is None: expected = data['count']
            if data['count'] != expected: raise ExportError('JumpServer 采集期间数量变化')
            batch = data['results']
            rows.extend(batch)
            if len(rows) >= expected: break
            if not batch: raise ExportError('JumpServer 分页不完整')
        if len(rows) != expected or len({a['id'] for a in rows}) != expected:
            raise ExportError('JumpServer 资产数量校验失败')
        return rows
    all_assets = assets('/api/v1/perms/users/assets/')
    if not all_assets: raise ExportError('JumpServer 返回空清单，请检查账号权限')
    print(f'JumpServer 授权资产：{len(all_assets)} 台', flush=True)
    nodes = [n for n in get('/api/v1/perms/users/nodes/') if n['id'] != 'favorite']
    print(f'JumpServer 授权分组：{len(nodes)} 个', flush=True)
    keys = {n['key']: n for n in nodes}
    covered = set()
    for node in nodes:
        rows = assets('/api/v1/perms/users/nodes/' + node['id'] + '/assets/')
        node['assetIds'] = sorted(a['id'] for a in rows)
        node['fetchedAssetCount'] = len(rows)
        covered.update(node['assetIds'])
        node['parentKey'] = node['key'].rsplit(':', 1)[0] if ':' in node['key'] else None
        parts = node['key'].split(':')
        node['path'] = ' / '.join(keys[':'.join(parts[:i])]['name'] for i in range(1, len(parts)+1))
    if covered != {a['id'] for a in all_assets}: raise ExportError('JumpServer 分组未覆盖完整资产')
    for node in nodes:
        child_ids = {aid for child in nodes if child['parentKey'] == node['key'] for aid in child['assetIds']}
        node['assetsOutsideChildGroups'] = len(set(node['assetIds']) - child_ids)
    result = {'collectedAt': daily.now(), 'groups': nodes,
              'assets': [{k: a.get(k) for k in ('id','hostname','ip','os','platform')} for a in all_assets]}
    print(f'JumpServer 开始检查服务器、进程与 Nginx（共 {len(all_assets)} 台，最多 6 台并行）', flush=True)
    total_assets = len(all_assets)
    def report_progress(done, total):
        write(folder/'server-progress.json', {'phase':'正在通过 JumpServer 检查服务器、进程与 Nginx', 'completed':done, 'total':total})
        # Log at a readable cadence; the last asset always closes the run.
        if done % 25 == 0 or done == total:
            print(f'JumpServer 检查：{done}/{total} 台', flush=True)
    inspections = collect_servers(all_assets, get, cookie, password, report_progress)
    for asset in result['assets']:
        inspection = inspections[asset['id']]
        configs = inspection.pop('nginxConfigurations', [])
        inspection['configurationCount'] = len(configs)
        inspection['configurationVersion'] = folder.name
        if configs:
            filename = hashlib.sha256(asset['id'].encode()).hexdigest()+'.json'
            write(folder/'nginx-configs'/filename, {'assetId':asset['id'], 'files':configs, 'collectedAt':inspection['checkedAt']})
        asset['inspection'] = inspection
    summary = {
        'can_login': sum(1 for i in inspections.values() if i.get('loginStatus') == 'can_login'),
        'cannot_login': sum(1 for i in inspections.values() if i.get('loginStatus') != 'can_login'),
        'processes': sum(len(i.get('processes') or []) for i in inspections.values()),
        'nginx': sum(1 for i in inspections.values() if i.get('nginxStatus') == 'complete'),
        'configs': sum(i.get('configurationCount', 0) for i in inspections.values()),
    }
    print(f"JumpServer 登录：可登录 {summary['can_login']} 台，不能登录 {summary['cannot_login']} 台", flush=True)
    print(f"JumpServer 进程：{summary['processes']} 条；Nginx 读取完整 {summary['nginx']} 台，原始配置 {summary['configs']} 份", flush=True)
    print(f"JumpServer 采集完成：{len(result['assets'])} 台资产，{len(result['groups'])} 个分组", flush=True)
    daily.save_json(folder / 'jumpserver-snapshot.json', result)
    return result


def jumpserver_asset(cookie, folder, password, asset_id, fetcher=None):
    """Re-inspect one asset only (processes and Nginx) and merge into the copied snapshot."""
    get = jumpserver_getter(cookie, fetcher)
    snapshot = json.loads((folder / 'jumpserver-snapshot.json').read_text())
    asset = next((a for a in snapshot.get('assets', []) if a['id'] == asset_id), None)
    if not asset:
        raise ExportError('当前数据版本不存在此资产')
    phase = '正在单独采集服务器的进程与 Nginx'
    write(folder/'server-progress.json', {'phase':phase, 'completed':0, 'total':1})
    inspections = collect_servers([asset], get, cookie, password,
                                  lambda done, total: write(folder/'server-progress.json', {'phase':phase, 'completed':done, 'total':total}))
    inspection = inspections[asset_id]
    configs = inspection.pop('nginxConfigurations', [])
    inspection['configurationCount'] = len(configs)
    inspection['configurationVersion'] = folder.name
    filename = hashlib.sha256(asset_id.encode()).hexdigest()+'.json'
    if configs:
        write(folder/'nginx-configs'/filename, {'assetId':asset_id, 'files':configs, 'collectedAt':inspection['checkedAt']})
    else:
        # Stale raw configs from the copied version must not survive a failed re-inspection.
        (folder/'nginx-configs'/filename).unlink(missing_ok=True)
    asset['inspection'] = inspection
    daily.save_json(folder / 'jumpserver-snapshot.json', snapshot)
    print(f"JumpServer 单独采集完成：{asset.get('ip','')} 进程 {len(inspection.get('processes') or [])} 条，Nginx 状态 {inspection.get('nginxStatus')}", flush=True)
    return snapshot


COOKIE_SOURCES = {'ecs':'aliyun', 'clb':'aliyun', 'nat':'aliyun', 'codeup':'aliyun',
                  'local gitlab':'local_gitlab', 'local_gitlab':'local_gitlab',
                  'devops':'devops', 'jumpserver':'jumpserver'}
COOKIE_LABELS = {'aliyun':'阿里云（ECS / Codeup / CLB / NAT 共用）',
                 'local_gitlab':'Local GitLab', 'devops':'DevOps', 'jumpserver':'JumpServer'}


def collection_failure(exc, source):
    key = COOKIE_SOURCES.get(source.lower())
    status = exc.code if isinstance(exc, HTTPError) else None
    auth = isinstance(exc, AuthenticationError) or status in (301,302,303,307,308,401)
    # Legacy collectors use typed errors only for some session-expiry paths.
    known_session = ('session may have expired', '会话令牌，请更新 Cookie',
                     '更新统一阿里云 Cookie')
    auth = auth or any(marker in str(exc) for marker in known_session)
    if auth and key:
        message = COOKIE_LABELS[key] + ' Cookie 已失效或登录态不可用，请更新 Cookie 后重新同步'
        return {'source':source, 'type':'cookie_expired', 'credential':key, 'message':message}
    if status == 403:
        return {'source':source, 'type':'permission_denied', 'message':source+' 访问被拒绝，请检查账号读取权限或登录态'}
    return {'source':source, 'type':type(exc).__name__,
            'message':exc.safe_message if isinstance(exc, CollectionError) else source+'：'+type(exc).__name__}


def parallel_collect(tasks, progress_path, workers=3):
    """Bound source concurrency; publish progress and per-slot state under one lock."""
    lock = threading.Lock()
    states = {name: '等待' for name in tasks}
    slots = {str(i): None for i in range(1, workers + 1)}   # slot -> running source
    history = {str(i): [] for i in range(1, workers + 1)}   # slot -> ordered sources
    available = queue.Queue()
    for index in range(1, workers + 1):
        available.put(index)
    results, errors = {}, {}
    def publish():
        active = [name for name, state in states.items() if state == '采集中']
        failed = [name for name, state in states.items() if state == '失败']
        write(progress_path, {'phase': '并行采集：' + '、'.join(active or ['收尾']) +
              ('；失败：' + '、'.join(failed) if failed else ''),
              'completed': sum(s in ('完成', '失败') for s in states.values()),
              'total': len(states), 'sources': dict(states),
              'slots': {key: ({'source': value, 'status': '采集中'} if value else {'source': None, 'status': '空闲'})
                        for key, value in slots.items()},
              'slotHistory': {key: list(value) for key, value in history.items()}})
    def run(name, task):
        index = available.get()
        slot = str(index)
        # The thread name drives the per-slot log sink for this and inner pools.
        threading.current_thread().name = 'slot:' + slot
        with lock:
            slots[slot] = name
            history[slot].append({'source': name, 'status': '采集中'})
            states[name] = '采集中'
            publish()
        try:
            result = task()
        except Exception:
            with lock:
                slots[slot] = None
                if history[slot] and history[slot][-1]['source'] == name:
                    history[slot][-1]['status'] = '失败'
                states[name] = '失败'
                publish()
            available.put(index)
            raise
        with lock:
            slots[slot] = None
            if history[slot] and history[slot][-1]['source'] == name:
                history[slot][-1]['status'] = '完成'
            states[name] = '完成'
            publish()
        available.put(index)
        return result
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, name, task): name for name, task in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try: results[name] = future.result()
            except Exception as exc: errors[name] = collection_failure(exc, name)
    if errors:
        # Never forward raw HTTP exceptions or response bodies containing credentials.
        failure = ExportError('；'.join(errors[name]['message'] for name in tasks if name in errors))
        failure.failures = list(errors.values())
        raise failure
    return results


def main():
    folder = Path(sys.argv[1])
    credentials = json.loads(Path(sys.argv[2]).read_text())
    # Cookies are temporary inputs, never stored in a historical snapshot.
    mode = sys.argv[3] if len(sys.argv) > 3 else 'full'
    install_slot_logs(folder / 'slots')
    # A single-source run has no worker pool; expose it as slot 1 for the same UI.
    if mode != 'full':
        threading.current_thread().name = 'slot:1'
    with tempfile.TemporaryDirectory(prefix='envscope-cookie-') as tmp:
        daily.ROOT = Path(tmp)
        for key, name in [('devops','cookie.txt'),('aliyun','codeup-cookie.txt')]:
            if key not in credentials: continue
            p = daily.ROOT / name
            p.write_text(credentials[key]['value']); p.chmod(0o600)
        if mode in ('ecs', 'clb', 'nat', 'jumpserver', 'devops', 'jumpserver_asset'):
            if mode == 'ecs':
                daily.save_json(folder / 'progress.json', {'phase': '正在独立采集阿里云 ECS 实例'})
                raw = collect_ecs(credentials['aliyun']['value'], folder/'ecs')
                previous = json.loads((folder/'ecs-snapshot.json').read_text())
                daily.save_json(folder/'ecs-snapshot.json', ecs_snapshot(raw, previous.get('projects', [])))
            elif mode == 'clb':
                daily.save_json(folder / 'progress.json', {'phase': '正在独立采集阿里云 CLB 实例、监听器和后端'})
                daily.save_json(folder/'clb-snapshot.json', normalize_clb(collect_clb(credentials['aliyun']['value'], folder/'clb')))
            elif mode == 'nat':
                daily.save_json(folder / 'progress.json', {'phase': '正在独立采集阿里云 NAT 网关与 DNAT IP / 端口映射'})
                daily.save_json(folder/'nat-snapshot.json', normalize_nat(collect_nat(credentials['aliyun']['value'], folder/'nat')))
            elif mode == 'jumpserver':
                daily.save_json(folder / 'progress.json', {'phase': '正在独立采集 JumpServer 资产和分组'})
                jumpserver(credentials['jumpserver']['value'], folder, credentials.get('folidev',{}).get('value',''))
            elif mode == 'jumpserver_asset':
                asset_id = sys.argv[4] if len(sys.argv) > 4 else ''
                try:
                    uuid.UUID(asset_id)
                except (ValueError, AttributeError, TypeError):
                    raise ExportError('单独采集需要有效的资产 ID')
                daily.save_json(folder / 'progress.json', {'phase': '正在单独采集一台服务器的进程与 Nginx', 'completed': 0, 'total': 1})
                jumpserver_asset(credentials['jumpserver']['value'], folder, credentials.get('folidev',{}).get('value',''), asset_id)
            else:
                daily.save_json(folder / 'progress.json', {'phase': '正在独立采集 DevOps 应用、环境和发布记录'})
                result = daily.collect_devops(folder, 4)
                inventory = {k: v for k,v in result.items() if k != 'apps'}
                inventory['apps'] = [{k:v for k,v in a.items() if k not in ('csvRows','complete','errors')} for a in result['apps']]
                daily.save_json(folder/'snapshot.json', inventory)
            daily.save_json(folder / 'progress.json', {'phase': f'{mode} 采集完成', 'completed': 1, 'total': 1})
            return
        if mode == 'codeup':
            daily.save_json(folder / 'progress.json', {'phase': '正在独立采集 Codeup 代码组和仓库'})
            codeup = daily.collect_codeup(folder)
            current = json.loads((folder / 'repositories.json').read_text())
            groups = current.get('groups', []); repos = {}
            for repo in current.get('repos', []):
                parts = urlsplit(repo.get('url', ''))
                canonical = daily.normalize_local_gitlab_url(parts.hostname or '', parts.path.strip('/')) or repo.get('url')
                repos[canonical] = {**repo, 'url': canonical}
            for group in codeup['groups']:
                group_id = str(group['id'])
                target = next((g for g in groups if g['id'] == group_id), None)
                if not target: groups.append({'id':group_id,'name':group['group_name'],'description':group['chinese_name'],'path':group['full_path'],'total':group['repository_count'],'listed':True})
            for repo in codeup['repos']:
                group_id = next((g['id'] for g in groups if repo['path'].startswith(g['path'] + '/')), '')
                repos[repo['url']] = {**repo, 'id': hashlib.sha256(repo['url'].encode()).hexdigest()[:20], 'groupId':group_id, 'access':'是', 'reason':'当前账号可访问 Codeup 项目列表返回', 'match':'完整仓库地址匹配', 'apps':repos.get(repo['url'],{}).get('apps',[]), 'source':'codeup', 'inCodeup':True, 'difference':repos.get(repo['url'],{}).get('difference','codeup_only')}
            by_name={}
            for candidate in codeup['repos']: by_name.setdefault(candidate['name'].lower(), []).append(candidate)
            for repo in repos.values():
                if repo.get('inDevops') and not repo.get('inCodeup') and repo.get('source') == 'codeup' and by_name.get(repo.get('name','').lower()):
                    repo['possibleCodeupMatches']=[{'id':hashlib.sha256(c['url'].encode()).hexdigest()[:20],'name':c['name'],'url':c['url'],'groupName':c['groupName'],'path':c['path']} for c in by_name[repo['name'].lower()]]
            inventory=json.loads((folder / 'snapshot.json').read_text())
            statuses=daily.codeup_reference_status({'apps':inventory.get('apps',[])}, codeup)
            for repo in repos.values():
                if repo.get('url') in statuses and not repo.get('inCodeup'):
                    repo.update(statuses[repo['url']])
            current.update(groups=groups, repos=sorted(repos.values(), key=lambda r:r['url']))
            daily.save_json(folder / 'repositories.json', current)
            return
        if mode == 'local_gitlab':
            daily.save_json(folder / 'progress.json', {'phase': '正在采集 Local GitLab 代码组和仓库', 'completed': 0, 'total': 0})
            local_gitlab = daily.collect_local_gitlab(credentials['local_gitlab']['value'], folder / 'progress.json')
            current = json.loads((folder / 'repositories.json').read_text())
            groups = current.get('groups', []); repos = {}
            for existing in current.get('repos', []):
                parts = urlsplit(existing.get('url', ''))
                canonical = daily.normalize_local_gitlab_url(parts.hostname or '', parts.path.strip('/')) or existing.get('url')
                is_local = (parts.hostname or '').lower() == 'gitlab.dev.thomascook.com.cn'
                repos[canonical] = {**existing, 'url': canonical, **({'source':'local_gitlab','access':'无法确认','reason':'未出现在当前账号可访问 Local GitLab 清单，可能无权限或项目已不存在，不能据此判断不可访问'} if is_local else {})}
            def repo_id(url): return hashlib.sha256(url.encode()).hexdigest()[:20]
            for repo in local_gitlab['repos']:
                group_id = 'local-gitlab-' + repo['groupPath'].replace('/', '-')
                group = next((g for g in groups if g['id'] == group_id), None)
                if not group:
                    group = {'id': group_id, 'name': repo['groupName'], 'description': 'Local GitLab 代码组', 'path': repo['groupPath'], 'total': 0, 'listed': True}; groups.append(group)
                existing = repos.get(repo['url'], {})
                repos[repo['url']] = {**existing, **repo, 'id': repo_id(repo['url']), 'groupId': group_id, 'access': '是', 'reason': '当前账号可访问 Local GitLab 项目列表返回', 'match': '完整仓库地址匹配', 'apps': existing.get('apps', []), 'source': 'local_gitlab', 'inDevops': existing.get('inDevops', False), 'difference': 'both' if existing.get('inDevops') else 'codeup_only'}
            for group in groups:
                if group['id'].startswith('local-gitlab-'): group['total'] = sum(r['groupId'] == group['id'] for r in repos.values())
            current.update(groups=groups, repos=sorted(repos.values(), key=lambda r: r['url']))
            daily.save_json(folder / 'repositories.json', current)
            daily.save_json(folder / 'progress.json', {'phase': 'Local GitLab 采集完成', 'completed': len(local_gitlab['repos']), 'total': len(local_gitlab['repos'])})
            return
        projects = json.loads((folder/'ecs-projects.json').read_text())
        # Collectors have independent progress/checkpoint directories; credentials are read-only.
        codeup_folder = folder/'sources'/'codeup'
        codeup_folder.mkdir(parents=True, exist_ok=True)
        def ecs_job():
            raw = collect_ecs(credentials['aliyun']['value'], folder/'ecs')
            write(folder/'ecs-snapshot.json', ecs_snapshot(raw, projects['projects']))
        tasks = {
            'ECS': ecs_job,
            'CLB': lambda: write(folder/'clb-snapshot.json', normalize_clb(collect_clb(credentials['aliyun']['value'], folder/'clb'))),
            'NAT': lambda: write(folder/'nat-snapshot.json', normalize_nat(collect_nat(credentials['aliyun']['value'], folder/'nat'))),
            'JumpServer': lambda: jumpserver(credentials['jumpserver']['value'], folder, credentials.get('folidev',{}).get('value','')),
            'Codeup': lambda: daily.collect_codeup(codeup_folder),
            'Local GitLab': lambda: daily.collect_local_gitlab(credentials['local_gitlab']['value'], folder/'gitlab-progress.json'),
            'DevOps': lambda: daily.collect_devops(folder, 4),
        }
        results = parallel_collect(tasks, folder/'progress.json')
        codeup, local_gitlab, devops = (results[name] for name in ('Codeup', 'Local GitLab', 'DevOps'))
        daily.save_json(folder / 'progress.json', {'phase': '正在核对 DevOps 仓库的 Codeup 地址状态', 'completed': 0, 'total': 0})
        codeup_status = daily.codeup_reference_status(devops, codeup)
        daily.save_json(folder / 'repositories.json', daily.reconcile(devops, codeup, local_gitlab, codeup_status))
        inventory = {k: v for k,v in devops.items() if k != 'apps'}
        inventory['apps'] = [{k:v for k,v in a.items() if k not in ('csvRows','complete','errors')} for a in devops['apps']]
        daily.save_json(folder / 'snapshot.json', inventory)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        # No raw HTTP response, credential, or exception text enters UI/logs.
        failures = getattr(exc, 'failures', [collection_failure(exc, sys.argv[3] if len(sys.argv)>3 else '数据源')])
        supplied = json.loads(Path(sys.argv[2]).read_text())
        for failure in failures:
            failure['credentialUpdatedAt'] = supplied.get(failure.get('credential'), {}).get('updatedAt')
        daily.save_json(Path(sys.argv[1]) / 'collection-error.json', {'type': type(exc).__name__, 'message': '；'.join(f['message'] for f in failures), 'failures':failures})
        sys.exit(1)
