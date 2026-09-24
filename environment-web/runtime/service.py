"""Local-only Settings API, persistent versions, and transactional web activation."""
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo

DATA = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
APP = Path(os.environ.get('ENVSCOPE_APP', '/app'))
ALLOWED_HOSTS = tuple(h for h in os.environ.get('ENVSCOPE_ALLOWED_HOSTS', '').replace(' ', '').split(',') if h)
FILES = ('snapshot.json', 'repositories.json', 'jumpserver-snapshot.json', 'ecs-snapshot.json')
OPTIONAL_FILES = ('clb-snapshot.json', 'nat-snapshot.json', 'server-specs.json')
EMPTY_CLB = {'collectedAt': None, 'instances': [], 'available': False}
EMPTY_NAT = {'collectedAt': None, 'gateways': [], 'available': False, 'region': 'cn-shanghai'}
EMPTY_SPECS = {'collectedAt': None, 'specs': [], 'available': False}
OPTIONAL_DEFAULTS = {'clb-snapshot.json': EMPTY_CLB, 'nat-snapshot.json': EMPTY_NAT, 'server-specs.json': EMPTY_SPECS}
TOPOLOGY_DYNAMIC_FILES = FILES + OPTIONAL_FILES
TOPOLOGY_STATIC_FILES = ('dns-snapshot.json', 'eip-snapshot.json')
PATH_KINDS = ('domain', 'application', 'host')
PATH_NODE_TYPES = ('DOMAIN','EIP','NAT_GATEWAY','DNAT_RULE','CLB','CLB_LISTENER','SERVER_GROUP','HOST','ENDPOINT','NGINX_ROUTE','UPSTREAM','APPLICATION','DEPLOYMENT','REPOSITORY')
PATH_ENVIRONMENTS = ('PRODUCT','TEST','SIMULATION','GLOBAL','UNKNOWN')
REQUEST_ENVIRONMENTS = ('PRODUCT','TEST','SIMULATION')
TZ = ZoneInfo('Asia/Shanghai')


def now(): return datetime.now(TZ).isoformat(timespec='seconds')


def read(path, default=None):
    try: return json.loads(path.read_text())
    except FileNotFoundError: return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    os.replace(temp, path)


def credentials():
    value = read(DATA/'secrets/cookies.json', {})
    if 'aliyun' not in value and 'codeup' in value:
        value['aliyun'] = value['codeup']
    value.pop('codeup', None)
    return value


def bounded_int(params, key, default, minimum, maximum):
    values = params.get(key) or []
    if not values: return default
    try: value = int(values[0])
    except (TypeError, ValueError): raise ValueError(f'{key} 必须是整数')
    if not minimum <= value <= maximum: raise ValueError(f'{key} 必须在 {minimum}-{maximum} 之间')
    return value


def validate(folder):
    data = {name: read(folder / name) for name in FILES}
    inventory, repos, jump = [data[name] for name in FILES[:3]]
    if not inventory or not inventory.get('apps') or not repos or not repos.get('repos'):
        raise ValueError('缺少完整的应用或代码库数据')
    if not jump or not jump.get('assets') or not jump.get('groups') or not data[FILES[3]]:
        raise ValueError('缺少完整的资源数据')
    for rows in (inventory['apps'], repos['repos'], jump['assets'], jump['groups']):
        if len({r['id'] for r in rows}) != len(rows): raise ValueError('存在重复 ID')
    ids = {a['id'] for a in jump['assets']}
    if {i for g in jump['groups'] for i in g['assetIds']} != ids:
        raise ValueError('分组与资产不一致')
    clb = read(folder/'clb-snapshot.json', EMPTY_CLB)
    if clb.get('available'):
        rows = clb['instances']
        if len({r['id'] for r in rows}) != len(rows): raise ValueError('CLB 存在重复 ID')
    nat = read(folder/'nat-snapshot.json', EMPTY_NAT)
    if nat.get('available'):
        gateways = nat.get('gateways')
        if not isinstance(gateways, list) or len({g['id'] for g in gateways}) != len(gateways):
            raise ValueError('NAT 网关清单缺失或 ID 重复')
        for gateway in gateways:
            entries = gateway.get('entries')
            if not isinstance(entries, list) or len({e['id'] for e in entries}) != len(entries):
                raise ValueError('DNAT 清单缺失或 ID 重复')
            if any(not all(str(e.get(k, '')).strip() for k in ('id','tableId','externalIp','externalPort','internalIp','internalPort','protocol','status')) for e in entries):
                raise ValueError('DNAT 条目字段不完整')
    return {'applications': len(inventory['apps']), 'repositories': len(repos['repos']), 'servers': len(ids),
            'ecs': len(data[FILES[3]].get('instances', [])), 'clb': len(clb['instances']) if clb.get('available') else None,
            'nat': len(nat['gateways']) if nat.get('available') else None,
            'dnat': sum(len(g['entries']) for g in nat['gateways']) if nat.get('available') else None}


class Service:
    def __init__(self):
        os.umask(0o077)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / 'versions').mkdir(exist_ok=True)
        if (DATA/'secrets/cookies.json').exists(): write(DATA/'secrets/cookies.json', credentials())
        self.lock = threading.RLock()
        self.backend = None
        self.retired = None
        self.job = read(DATA / 'job.json')
        if self.job and self.job.get('status') == 'running':
            self.job.update(status='failed', phase='容器重启，任务已中断；当前版本保持不变', finishedAt=now())
            write(DATA / 'job.json', self.job)
            folder = DATA / 'versions' / self.job['id']
            report = read(folder / 'version.json')
            if report and report.get('status') == 'collecting':
                report.update(status='failed', error='容器重启，采集未完成')
                write(folder / 'version.json', report)
        digest = hashlib.sha256(b''.join((APP/'lib'/f).read_bytes() for f in FILES)).hexdigest()[:16]
        seed = 'initial-' + digest
        if not (DATA/'versions'/seed).exists():
            folder = DATA/'versions'/seed; folder.mkdir()
            for f in FILES: shutil.copyfile(APP/'lib'/f, folder/f)
            for f in OPTIONAL_FILES: write(folder/f, OPTIONAL_DEFAULTS[f])
            write(folder/'version.json', {'id':seed,'createdAt':now(),'status':'ready','kind':'initial',
                  'note':'首次导入的现有快照；各来源采集时间不同','counts':validate(folder)})
        if not read(DATA/'current.json'): write(DATA/'current.json', {'id':seed,'activatedAt':now()})
        if not read(DATA/'schedule.json'):
            write(DATA/'schedule.json', {'enabled':False,'time':'18:00','lastDate':None})

    def version(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
            raise ValueError('无效版本')
        folder = DATA/'versions'/identifier
        report = read(folder/'version.json')
        if not report or report['status'] != 'ready': raise ValueError('只能切换到完整版本')
        validate(folder)
        return folder

    def nginx_configs(self, identifier, asset_id):
        folder = self.version(identifier)
        snapshot = read(folder/'jumpserver-snapshot.json', {})
        if not any(a['id'] == asset_id for a in snapshot.get('assets', [])):
            raise ValueError('该版本不存在此资产')
        filename = hashlib.sha256(asset_id.encode()).hexdigest()+'.json'
        result = read(folder/'nginx-configs'/filename)
        if result is None: raise ValueError('该版本未保存此资产的 Nginx 原始配置')
        return result

    def topology_status(self):
        pointer=read(DATA/'topology/latest.json')
        identifier=pointer.get('id') if isinstance(pointer,dict) else None
        if not isinstance(identifier,str) or not re.fullmatch(r'topology-[A-Za-z0-9-]{1,80}',identifier): return None
        folder=DATA/'topology'/identifier
        topology=read(folder/'topology.json'); report=read(folder/'version.json')
        if not topology or not report or report.get('status')!='ready': return None
        return {'id':identifier,'sourceVersion':report.get('sourceVersion'),'generatedAt':topology.get('generatedAt'),
                'stats':topology.get('stats'),'validation':{'valid':report.get('valid'),'errorCount':report.get('errorCount',0),'warningCount':report.get('warningCount',0)},
                'viewHref':'/topology.json','downloadHref':f'/topology/{identifier}/download'}

    def generate_topology(self):
        current=read(DATA/'current.json')
        if not current or not current.get('id'): raise ValueError('缺少当前数据版本')
        folder=self.version(current['id'])
        with self.lock:
            identifier='topology-'+datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')
            output=DATA/'topology'/identifier;output.mkdir(parents=True)
            report={'id':identifier,'createdAt':now(),'status':'generating','kind':'topology','sourceVersion':current['id'],
                    'note':'基于当前激活数据版本生成 Topology；DNS 和 EIP 使用镜像内静态快照'}
            write(output/'version.json',report)
            try:
                with tempfile.TemporaryDirectory(prefix='envscope-topology-') as tmp:
                    source=Path(tmp)/'lib';source.mkdir()
                    for name in TOPOLOGY_DYNAMIC_FILES:shutil.copyfile(folder/name,source/name)
                    for name in TOPOLOGY_STATIC_FILES:shutil.copyfile(APP/'lib'/name,source/name)
                    with (output/'topology.log').open('w') as log:
                        proc=subprocess.run(['node',str(APP/'scripts/generate-topology.mjs'),str(Path(tmp))],cwd=APP,stdout=log,stderr=subprocess.STDOUT,timeout=300)
                    generated=Path(tmp)/'outputs/topology/topology.json'
                    topology=read(generated)
                    if proc.returncode or not topology or not topology.get('nodes') or not topology.get('edges'):
                        raise ValueError('Topology 生成失败，请查看 topology.log')
                    shutil.copyfile(generated,output/'topology.json');os.chmod(output/'topology.json',0o600)
                    report.update(status='ready',finishedAt=now(),valid=True,errorCount=0,
                                  warningCount=topology.get('stats',{}).get('ambiguousEdges',0),counts=topology.get('stats'))
                    write(output/'version.json',report)
                    write(DATA/'topology/latest.json',{'id':identifier,'sourceVersion':current['id'],'generatedAt':topology.get('generatedAt')})
                return self.topology_status()
            except Exception as exc:
                report.update(status='failed',finishedAt=now(),error=str(exc));write(output/'version.json',report)
                raise

    def topology_file(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'topology-[A-Za-z0-9-]{1,80}',identifier):
            raise ValueError('无效 Topology 版本')
        folder=DATA/'topology'/identifier;report=read(folder/'version.json')
        if not report or report.get('status')!='ready': raise ValueError('Topology 版本不存在或未生成完成')
        path=folder/'topology.json'
        if not path.is_file(): raise ValueError('Topology 数据不存在')
        return path

    def path_query(self,params):
        """Answer one topology path question against the active Topology version."""
        status=self.topology_status()
        if not status: raise ValueError('尚未生成 Topology；请先在 Settings 页面基于当前数据版本生成')
        kind=(params.get('kind') or [''])[0].strip().lower()
        if kind not in PATH_KINDS: raise ValueError('kind 必须是 domain、application 或 host')
        query=(params.get('q') or [''])[0].strip()
        if not query or len(query)>253: raise ValueError('q 必须是 1-253 个字符的域名、应用名或 IP')
        targets=[]
        for value in params.get('to') or []:
            for item in value.split(','):
                item=item.strip().upper()
                if not item: continue
                if item not in PATH_NODE_TYPES: raise ValueError('to 包含不支持的节点类型')
                targets.append(item)
        environment=(params.get('env') or [''])[0].strip().upper()
        if environment and environment not in PATH_ENVIRONMENTS: raise ValueError('env 必须是 PRODUCT、TEST、SIMULATION、GLOBAL 或 UNKNOWN')
        max_depth=bounded_int(params,'maxDepth',8,1,16)
        max_paths=bounded_int(params,'maxPaths',50,1,200)
        command=['node',str(APP/'scripts'/'topology-path.mjs'),kind,query,'--json','--file',str(self.topology_file(status['id'])),
                 '--max-depth',str(max_depth),'--max-paths',str(max_paths)]
        if targets: command+=['--to',','.join(targets)]
        if environment: command+=['--env',environment]
        try:
            proc=subprocess.run(command,cwd=APP,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            raise ValueError('路径查询超时，请降低 maxDepth 后重试')
        if proc.returncode:
            detail=(proc.stderr or proc.stdout).strip()[-400:] or '未知错误'
            raise ValueError('路径查询失败：'+detail)
        try: result=json.loads(proc.stdout)
        except json.JSONDecodeError: raise ValueError('路径查询结果无法解析')
        return {'topology':{'id':status['id'],'generatedAt':status['generatedAt']},'result':result}

    def domain_chain_query(self,params):
        """Staged domain landing chain: DNS -> EIP -> NAT/ECS/CLB -> nginx -> upstream -> application."""
        status=self.topology_status()
        if not status: raise ValueError('尚未生成 Topology；请先在 Settings 页面基于当前数据版本生成')
        query=(params.get('q') or [''])[0].strip()
        if not query or len(query)>253: raise ValueError('q 必须是 1-253 个字符的域名')
        environment=(params.get('env') or [''])[0].strip().upper()
        if environment and environment not in PATH_ENVIRONMENTS: raise ValueError('env 必须是 PRODUCT、TEST、SIMULATION、GLOBAL 或 UNKNOWN')
        command=['node',str(APP/'scripts'/'topology-path.mjs'),'domain-chain',query,'--json','--file',str(self.topology_file(status['id']))]
        if environment: command+=['--env',environment]
        try:
            proc=subprocess.run(command,cwd=APP,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            raise ValueError('域名链路查询超时')
        if proc.returncode:
            detail=(proc.stderr or proc.stdout).strip()[-400:] or '未知错误'
            raise ValueError('域名链路查询失败：'+detail)
        try: result=json.loads(proc.stdout)
        except json.JSONDecodeError: raise ValueError('域名链路查询结果无法解析')
        return {'topology':{'id':status['id'],'generatedAt':status['generatedAt']},'result':result}

    def request_path_query(self,params):
        """Request-aware end-to-end chain: URL -> DNS -> NAT/CLB -> nginx -> application -> repository."""
        status=self.topology_status()
        if not status: raise ValueError('尚未生成 Topology；请先在 Settings 页面基于当前数据版本生成')
        query=(params.get('q') or [''])[0].strip()
        if not query or len(query)>2048: raise ValueError('q 必须是 1-2048 个字符的 URL 或域名')
        environment=(params.get('env') or [''])[0].strip().upper()
        if environment and environment not in REQUEST_ENVIRONMENTS: raise ValueError('env 必须是 PRODUCT、TEST 或 SIMULATION')
        max_depth=bounded_int(params,'maxDepth',16,1,24)
        max_paths=bounded_int(params,'maxPaths',50,1,200)
        command=['node',str(APP/'scripts'/'topology-path.mjs'),'request',query,'--json','--file',str(self.topology_file(status['id'])),
                 '--max-depth',str(max_depth),'--max-paths',str(max_paths)]
        if environment: command+=['--env',environment]
        try:
            proc=subprocess.run(command,cwd=APP,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            raise ValueError('请求链路查询超时')
        if proc.returncode:
            detail=(proc.stderr or proc.stdout).strip()[-400:] or '未知错误'
            raise ValueError('请求链路查询失败：'+detail)
        try: result=json.loads(proc.stdout)
        except json.JSONDecodeError: raise ValueError('请求链路查询结果无法解析')
        return {'topology':{'id':status['id'],'generatedAt':status['generatedAt']},'result':result}

    def status(self):
        with self.lock:
            saved = credentials()
            versions = [read(p) for p in (DATA/'versions').glob('*/version.json')]
            job = dict(self.job) if self.job else None
            if job and job['status'] == 'running' and job['kind'] in ('sync', 'local_gitlab', 'jumpserver', 'jumpserver_asset'):
                folder = DATA/'versions'/job['id']
                try: progress = read(DATA/'versions'/job['id']/'progress.json')
                except json.JSONDecodeError: progress = None
                if progress:
                    # Keep the seven per-source states; JumpServer's own counter must
                    # not replace them, or the UI can only ever show one source.
                    job['phase'] = progress.get('phase', job['phase'])
                    job['progress'] = progress
                server_progress = read(folder/'server-progress.json')
                if server_progress and server_progress.get('completed',0) < server_progress.get('total',0):
                    # Attach the JumpServer detail to the progress, not over it.
                    job['progress'] = {**(job.get('progress') or {}), 'jumpServer': server_progress}
                    if not (job.get('progress') or {}).get('sources'):
                        job['phase'] = server_progress.get('phase', job['phase'])
                log = folder/'collection.log'
                if log.exists():
                    try: job['logTail'] = log.read_text(errors='replace')[-12000:]
                    except OSError: pass
                if progress and 'DevOps' in progress.get('phase','') and self.job.get('phase') == '正在采集数据':
                    completed = len(list((folder/'applications').glob('*.json')))
                    job['phase'] += f'（已采集 {completed} 个应用）'
                # Per-slot logs power the three concurrent-slot tabs.
                slot_dir = folder/'slots'
                if slot_dir.is_dir():
                    slot_logs = {}
                    for path in sorted(slot_dir.glob('*.log')):
                        try: slot_logs[path.stem] = path.read_text(errors='replace')[-12000:]
                        except OSError: pass
                    if slot_logs: job['slotLogs'] = slot_logs
            return {'cookies':{key:{'configured':bool(saved.get(key,{}).get('value')),
                    'updatedAt':saved.get(key,{}).get('updatedAt'),
                    'alert':read(DATA/'cookie-alerts.json',{}).get(key)} for key in ('devops','aliyun','jumpserver','local_gitlab')},
                    'folidev':{'configured':bool(saved.get('folidev',{}).get('value')), 'updatedAt':saved.get('folidev',{}).get('updatedAt')},
                    'current':read(DATA/'current.json'), 'schedule':read(DATA/'schedule.json'),
                    'versions':sorted(versions,key=lambda v:v['createdAt'],reverse=True), 'job':job,
                    'storage':'Docker 数据卷 envscope-data；每次同步独立目录，重建容器后保留','topology':self.topology_status()}

    def save_settings(self, payload):
        with self.lock:
            saved = credentials()
            for key,value in payload.get('cookies',{}).items():
                if key not in ('devops','aliyun','codeup','jumpserver','local_gitlab') or not isinstance(value,str): raise ValueError('无效 Cookie 字段')
                if key == 'codeup': key = 'aliyun'  # Existing open Settings tabs remain compatible.
                value = value.strip()
                if not value: continue  # Empty form fields preserve saved credentials.
                if value.lower().startswith('cookie:'): value = value.split(':',1)[1].strip()
                if not value or len(value)>20000 or any(ord(c)<32 or ord(c)>255 for c in value):
                    raise ValueError('Cookie 必须是非空单行请求头内容')
                saved[key] = {'value':value,'updatedAt':now()}
            password = payload.get('folidevPassword', '')
            if not isinstance(password, str) or len(password) > 4096 or any(c in password for c in ('\r','\n','\x00')):
                raise ValueError('folidev 密码格式无效')
            if password: saved['folidev'] = {'value':password, 'updatedAt':now()}
            schedule = payload.get('schedule')
            if schedule is not None:
                if not isinstance(schedule.get('enabled'),bool) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',schedule.get('time','')):
                    raise ValueError('无效定时设置')
                if schedule['enabled'] and any(not saved.get(k,{}).get('value') for k in ('devops','aliyun','jumpserver','local_gitlab')):
                    raise ValueError('启用每日同步前，请配置四个平台的 Cookie')
            write(DATA/'secrets/cookies.json',saved)
            alerts=read(DATA/'cookie-alerts.json',{})
            for key,value in payload.get('cookies',{}).items():
                if isinstance(value,str) and value.strip(): alerts.pop('aliyun' if key=='codeup' else key,None)
            write(DATA/'cookie-alerts.json',alerts)
            if schedule is not None:
                previous = read(DATA/'schedule.json',{})
                write(DATA/'schedule.json',{**previous,**schedule})

    def collection_error(self, folder):
        error=read(folder/'collection-error.json',{})
        with self.lock:
            alerts=read(DATA/'cookie-alerts.json',{})
            saved=credentials()
            for failure in error.get('failures',[]):
                key=failure.get('credential')
                if (failure.get('type')=='cookie_expired' and key in saved and
                        failure.get('credentialUpdatedAt')==saved[key].get('updatedAt')):
                    alerts[key]={'message':failure['message'],'detectedAt':now()}
            write(DATA/'cookie-alerts.json',alerts)
        return error.get('message') or '采集失败，请检查连接或平台响应'

    def set_job(self, **changes):
        with self.lock:
            self.job.update(changes); write(DATA/'job.json',self.job)

    def start(self, kind, identifier=None, asset_id=None):
        with self.lock:
            if self.backend is None: raise ValueError('应用正在恢复当前版本，请稍后重试')
            if self.job and self.job['status']=='running': raise ValueError('已有同步或切换任务正在进行')
            source_modes = ('local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'jumpserver', 'devops', 'jumpserver_asset')
            if kind in ('sync', *source_modes):
                saved = credentials()
                required = ('devops','aliyun','jumpserver','local_gitlab') if kind == 'sync' else \
                    ('aliyun',) if kind in ('ecs','clb','nat','codeup') else ('jumpserver',) if kind == 'jumpserver_asset' else (kind,)
                if any(not saved.get(k,{}).get('value') for k in required):
                    raise ValueError('请先保存 Local GitLab Cookie' if kind == 'local_gitlab' else
                                     '请先保存 JumpServer Cookie' if kind == 'jumpserver_asset' else '请先保存四个平台的 Cookie')
                if kind == 'jumpserver_asset':
                    if not isinstance(asset_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', asset_id):
                        raise ValueError('无效的资产 ID')
                identifier = datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')
                folder=DATA/'versions'/identifier;folder.mkdir()
                note = '单独重新采集一台服务器的进程与 Nginx' if kind == 'jumpserver_asset' else \
                    f'单独同步 {kind}' if kind in source_modes else '同步 DevOps、Codeup、JumpServer、上海 ECS、CLB 和 NAT'
                write(folder/'version.json',{'id':identifier,'createdAt':now(),'status':'collecting','kind':kind,
                      'note':note})
            else: self.version(identifier)
            self.job={'id':identifier,'kind':kind,'status':'running','startedAt':now(),'phase':'任务准备中','assetId':asset_id}
            write(DATA/'job.json',self.job)
            threading.Thread(target=self.work,args=(kind,identifier,asset_id),daemon=True).start()
            return identifier

    def work(self,kind,identifier,asset_id=None):
        folder=DATA/'versions'/identifier
        try:
            if kind in ('sync', 'local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'jumpserver', 'devops', 'jumpserver_asset'):
                self.set_job(phase='正在采集数据')
                current_folder = self.version(read(DATA/'current.json')['id'])
                if kind in ('local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'jumpserver', 'devops', 'jumpserver_asset'):
                    for name in FILES: shutil.copyfile(current_folder/name, folder/name)
                    for name in OPTIONAL_FILES: write(folder/name, read(current_folder/name, OPTIONAL_DEFAULTS[name]))
                    # A source sync must produce fresh data, not accidentally retain the copied snapshot.
                    if kind in ('clb', 'nat'): (folder/f'{kind}-snapshot.json').unlink(missing_ok=True)
                    with tempfile.TemporaryDirectory(prefix='envscope-input-') as tmp:
                        creds=Path(tmp)/'credentials.json'; write(creds, credentials())
                        command=['python3',str(APP/'runtime/collect.py'),str(folder),str(creds),kind]
                        if kind == 'jumpserver_asset': command.append(asset_id)
                        with (folder/'collection.log').open('w') as log:
                            p=subprocess.run(command,stdout=log,stderr=log,timeout=7200)
                    if p.returncode:
                        detail=self.collection_error(folder)
                        raise ValueError(f'{kind} 采集失败：{detail}；当前版本未改变')
                    if kind in ('clb', 'nat') and not read(folder/f'{kind}-snapshot.json', {}).get('available'):
                        raise ValueError(f'缺少本次 {kind.upper()} 采集数据')
                    counts=validate(folder)
                    # Defer marking the version ready until activate() swaps in the
                    # new backend; otherwise Settings advertises CLB/NAT counts
                    # before the CLB panorama page can actually serve them.
                    self.set_job(phase='数据采集完成，正在构建并切换到新版本')
                else:
                    previous = read(current_folder/'ecs-snapshot.json')
                    write(folder/'ecs-projects.json', {'projects':previous['projects'],'note':'项目名称目录沿用前版；ECS 实例及项目 ID 本次实时采集'})
                    with tempfile.TemporaryDirectory(prefix='envscope-input-') as tmp:
                        creds=Path(tmp)/'credentials.json'
                        with self.lock: write(creds,credentials())
                        with (folder/'collection.log').open('w') as log:
                            p=subprocess.run(['python3',str(APP/'runtime/collect.py'),str(folder),str(creds)],stdout=log,stderr=log,timeout=7200)
                        if p.returncode:
                            detail=self.collection_error(folder)
                            raise ValueError('采集失败：'+detail+'；当前版本未改变')
                    if not read(folder/'clb-snapshot.json',{}).get('available'): raise ValueError('缺少本次 CLB 采集数据')
                    if not read(folder/'nat-snapshot.json',{}).get('available'): raise ValueError('缺少本次 NAT 采集数据')
                    counts=validate(folder)
                    self.set_job(phase='数据采集完成，正在构建并切换到新版本')
            self.activate(identifier)
            # Only flip the version to ready after the new backend is live, so
            # the Settings version row and the CLB panorama page agree on what
            # data is actually served. switch() re-activates an already-ready
            # version, so it must not rewrite counts.
            if kind != 'switch':
                report=read(folder/'version.json');report.update(status='ready',finishedAt=now(),counts=counts)
                write(folder/'version.json',report)
            self.set_job(status='succeeded',phase='已切换到指定数据版本' if kind=='switch' else '同步完成，已启用新版本',finishedAt=now())
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else '任务失败或超时，当前版本保持不变；请检查连接后重试'
            report=read(folder/'version.json')
            if report and report['status']=='collecting':
                report.update(status='failed',error=message,finishedAt=now());write(folder/'version.json',report)
            self.set_job(status='failed',phase=message,finishedAt=now())

    def launch(self,root,port):
        log=(root/'runtime.log').open('a')
        proc=subprocess.Popen(['node',str(APP/'node_modules/wrangler/bin/wrangler.js'),'dev','--local',
             '--config',str(root/'dist/server/wrangler.json'),'--ip','127.0.0.1','--port',str(port),'--inspector-port','0'],
             cwd=root,stdout=log,stderr=log,start_new_session=True)
        log.close()
        try:
            for _ in range(90):
                if proc.poll() is not None: raise ValueError('新版本启动失败，保留当前版本')
                try:
                    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                    connection.request('GET','/settings');response=connection.getresponse();response.read();connection.close()
                    if response.status==200:return {'port':port,'proc':proc,'root':root}
                except OSError: pass
                time.sleep(1)
            raise ValueError('新版本启动超时，保留当前版本')
        except Exception:
            self.stop({'proc':proc,'root':root});raise

    def stop(self,backend):
        import signal
        try: os.killpg(backend['proc'].pid,signal.SIGTERM)
        except ProcessLookupError: pass
        try: backend['proc'].wait(timeout=10)
        except subprocess.TimeoutExpired:
            try: os.killpg(backend['proc'].pid,signal.SIGKILL)
            except ProcessLookupError: pass
        if backend['root']!=APP: shutil.rmtree(backend['root'],ignore_errors=True)

    def activate(self,identifier):
        # activate() runs after work() has collected and validated the data but
        # BEFORE the version is marked ready. The ready check lives in start()
        # (for switch) and boot(); reaching activate() means the caller already
        # validated. Requiring 'ready' here would force work() to flip the
        # status prematurely and leak counts to the Settings UI before the new
        # backend is actually serving the fresh snapshot.
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
            raise ValueError('无效版本')
        folder=DATA/'versions'/identifier
        if not (folder/'version.json').is_file(): raise ValueError('版本不存在')
        root=Path(tempfile.mkdtemp(prefix='envscope-web-'))
        candidate=None
        try:
            shutil.copytree(APP,root,dirs_exist_ok=True,ignore=shutil.ignore_patterns('node_modules','dist','.wrangler','.vinext','runtime','*.log','__pycache__'))
            (root/'node_modules').symlink_to(APP/'node_modules',target_is_directory=True)
            for name in FILES:shutil.copyfile(folder/name,root/'lib'/name)
            for name in OPTIONAL_FILES:write(root/'lib'/name,read(folder/name,OPTIONAL_DEFAULTS[name]))
            with (folder/'build.log').open('w') as log:
                result=subprocess.run(['npm','run','build'],cwd=root,stdout=log,stderr=log,timeout=600)
            if result.returncode:raise ValueError('数据已保留，但版本构建失败；当前版本未改变')
            # Keep the preceding server for assets requested by already-open tabs.
            if self.retired:self.stop(self.retired);self.retired=None
            port=3102 if self.backend and self.backend['port']==3101 else 3101
            candidate=self.launch(root,port)
            with self.lock:
                write(DATA/'current.json',{'id':identifier,'activatedAt':now()})
                self.retired=self.backend;self.backend=candidate
        except Exception:
            if candidate:self.stop(candidate)
            else:shutil.rmtree(root,ignore_errors=True)
            raise

    def boot(self):
        # A container restart terminates previous jobs but retains its /tmp layer.
        for prefix in ('envscope-web-','envscope-input-','envscope-cookie-','envscope-topology-'):
            for stale in Path(tempfile.gettempdir()).glob(prefix+'*'):
                if stale.is_dir(): shutil.rmtree(stale,ignore_errors=True)
        folder=self.version(read(DATA/'current.json')['id'])
        if all((folder/f).read_bytes()==(APP/'lib'/f).read_bytes() for f in FILES) and all(read(folder/f,OPTIONAL_DEFAULTS[f])==read(APP/'lib'/f,OPTIONAL_DEFAULTS[f]) for f in OPTIONAL_FILES):
            self.backend=self.launch(APP,3101)
        else:self.activate(folder.name)
        threading.Thread(target=self.scheduler,daemon=True).start()

    def scheduler(self):
        while True:
            try:
                with self.lock:
                    schedule=read(DATA/'schedule.json');stamp=datetime.now(TZ)
                    if schedule['enabled'] and stamp.strftime('%H:%M')>=schedule['time'] and schedule.get('lastDate')!=stamp.date().isoformat() and not (self.job and self.job['status']=='running'):
                        self.start('sync')
                        schedule['lastDate']=stamp.date().isoformat();write(DATA/'schedule.json',schedule)
            except Exception:pass
            time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        if status >= 400:
            # Rejected requests may still have unread bodies; never reuse that connection.
            self.close_connection=True;self.send_header('Connection','close')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def topology(self):
        status=self.server.service.topology_status()
        if not status:return self.json(404,{'error':'尚未生成 Topology；请先在 Settings 页面基于当前数据版本生成'})
        try: body=self.server.service.topology_file(status['id']).read_bytes()
        except (OSError,ValueError):return self.json(404,{'error':'Topology 数据不存在，请重新生成'})
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    def topology_download(self,identifier):
        try: body=self.server.service.topology_file(identifier).read_bytes()
        except (OSError,ValueError) as exc:return self.json(404,{'error':str(exc)})
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Disposition',f'attachment; filename="envscope-{identifier}.json"')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    def allowed_host(self):
        host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        if host in ('localhost', '127.0.0.1', '::1'): return True
        if '*' in ALLOWED_HOSTS: return True
        return host in ALLOWED_HOSTS
    def do_GET(self):
        if not self.allowed_host():return self.json(403,{'error':'仅允许本机访问'})
        if self.path=='/topology.json':return self.topology()
        if self.path.startswith('/topology/') and self.path.endswith('/download'):
            return self.topology_download(self.path[len('/topology/'):-len('/download')])
        if self.path.startswith('/api/settings/nginx-configs?'):
            params = parse_qs(urlsplit(self.path).query)
            try:
                return self.json(200, self.server.service.nginx_configs(params.get('version',[''])[0], params.get('asset',[''])[0]))
            except ValueError as exc: return self.json(404, {'error':str(exc)})
        if self.path=='/api/settings/state':return self.json(200,self.server.service.status())
        if self.path == '/api/topology/domain-chain' or self.path.startswith('/api/topology/domain-chain?'):
            params=parse_qs(urlsplit(self.path).query)
            try: return self.json(200,self.server.service.domain_chain_query(params))
            except ValueError as exc: return self.json(400,{'error':str(exc)})
        if self.path.startswith('/api/topology/path?'):
            params=parse_qs(urlsplit(self.path).query)
            try: return self.json(200,self.server.service.path_query(params))
            except ValueError as exc: return self.json(400,{'error':str(exc)})
        if self.path == '/api/topology/request-path' or self.path.startswith('/api/topology/request-path?'):
            params=parse_qs(urlsplit(self.path).query)
            try: return self.json(200,self.server.service.request_path_query(params))
            except ValueError as exc: return self.json(400,{'error':str(exc)})
        if self.path.startswith('/api/settings/'):return self.json(404,{'error':'接口不存在'})
        self.proxy()
    def do_POST(self):
        if not self.allowed_host() or self.headers.get('Origin')!='http://'+self.headers.get('Host',''):
            return self.json(403,{'error':'请求来源无效'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.json(415,{'error':'需要 JSON 请求'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=65536:raise ValueError('请求大小无效')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError('请求格式无效')
            service=self.server.service
            if self.path=='/api/settings/config':service.save_settings(payload)
            elif self.path=='/api/settings/topology':service.generate_topology()
            elif self.path=='/api/settings/sync':service.start('sync')
            elif self.path=='/api/settings/sync/local-gitlab':service.start('local_gitlab')
            elif self.path.startswith('/api/settings/sync/'):
                source = self.path.rsplit('/', 1)[-1]
                if source not in ('ecs', 'clb', 'nat', 'jumpserver', 'devops', 'codeup'): return self.json(404,{'error':'不支持的数据源'})
                service.start(source)
                return self.json(200, service.status())
            elif self.path=='/api/settings/jumpserver-inspect':
                service.start('jumpserver_asset', asset_id=payload.get('asset'))
                return self.json(200, service.status())
            elif self.path=='/api/settings/switch':service.start('switch',payload.get('id'))
            else:return self.json(404,{'error':'接口不存在'})
            return self.json(200,service.status())
        except json.JSONDecodeError:return self.json(400,{'error':'请求 JSON 格式无效'})
        except ValueError as exc:return self.json(400,{'error':str(exc)})
        except (TypeError,AttributeError):return self.json(400,{'error':'请求格式无效'})
        except Exception:return self.json(500,{'error':'操作失败，请稍后重试'})
    def proxy(self):
        service=self.server.service
        backend=service.backend
        if not backend:return self.json(503,{'error':'应用正在启动'})
        try:
            connection=http.client.HTTPConnection('127.0.0.1',backend['port'],timeout=120)
            connection.request('GET',self.path,headers={k:v for k,v in self.headers.items() if k.lower() not in ('connection','host')})
            response=connection.getresponse();body=response.read()
            if response.status==404 and self.path.startswith('/_next/static/') and service.retired:
                connection.close();connection=http.client.HTTPConnection('127.0.0.1',service.retired['port'],timeout=15)
                connection.request('GET',self.path);response=connection.getresponse();body=response.read()
            self.send_response(response.status)
            for key,value in response.getheaders():
                if key.lower() not in ('connection','transfer-encoding','content-length','cache-control'):self.send_header(key,value)
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            connection.close()
        except (OSError,http.client.HTTPException):self.json(502,{'error':'应用暂时不可用，请刷新重试'})


if __name__=='__main__':
    service=Service()
    server=ThreadingHTTPServer(('0.0.0.0',3000),Handler);server.service=service
    threading.Thread(target=server.serve_forever,daemon=True).start()
    service.boot()
    threading.Event().wait()
