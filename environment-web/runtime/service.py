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
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo

try:
    from deployment_catalog import Catalog, CatalogError, CatalogInputError, ENVIRONMENTS as ENVIRONMENTS_CATALOG
except ModuleNotFoundError:
    from runtime.deployment_catalog import Catalog, CatalogError, CatalogInputError, ENVIRONMENTS as ENVIRONMENTS_CATALOG

DATA = Path(os.environ.get('ENVSCOPE_DATA', '/data'))
APP = Path(os.environ.get('ENVSCOPE_APP', '/app'))
ALLOWED_HOSTS = tuple(h for h in os.environ.get('ENVSCOPE_ALLOWED_HOSTS', '').replace(' ', '').split(',') if h)
FILES = ('snapshot.json', 'repositories.json', 'jumpserver-snapshot.json', 'ecs-snapshot.json')
OPTIONAL_FILES = ('clb-snapshot.json', 'nat-snapshot.json', 'server-specs.json', 'dms-snapshot.json')
EMPTY_CLB = {'collectedAt': None, 'instances': [], 'available': False}
EMPTY_NAT = {'collectedAt': None, 'gateways': [], 'available': False, 'region': 'cn-shanghai'}
EMPTY_SPECS = {'collectedAt': None, 'specs': [], 'available': False}
EMPTY_DMS = {'collectedAt': None, 'instances': [], 'available': False}
OPTIONAL_DEFAULTS = {'clb-snapshot.json': EMPTY_CLB, 'nat-snapshot.json': EMPTY_NAT,
                     'server-specs.json': EMPTY_SPECS, 'dms-snapshot.json': EMPTY_DMS}
TOPOLOGY_DYNAMIC_FILES = FILES + OPTIONAL_FILES
TOPOLOGY_STATIC_FILES = ('dns-snapshot.json', 'eip-snapshot.json')
PATH_KINDS = ('domain', 'application', 'host')
PATH_NODE_TYPES = ('DOMAIN','EIP','NAT_GATEWAY','DNAT_RULE','CLB','CLB_LISTENER','SERVER_GROUP','HOST','ENDPOINT','NGINX_ROUTE','UPSTREAM','APPLICATION','DEPLOYMENT','REPOSITORY')
PATH_ENVIRONMENTS = ('PRODUCT','TEST','SIMULATION','GLOBAL','UNKNOWN')
REQUEST_ENVIRONMENTS = ('PRODUCT','TEST','SIMULATION')
# Single-source sync kinds that may run concurrently with a primary job, mapped
# to the display names used by collect.py's parallel_collect() source states.
SOURCE_LABELS = {'ecs':'ECS','clb':'CLB','nat':'NAT','dms':'DMS','jumpserver':'JumpServer',
                 'codeup':'Codeup','local_gitlab':'Local GitLab','devops':'DevOps'}
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


def aliyun_mode(saved):
    mode = (saved.get('aliyun_auth_mode') or {}).get('value') or 'cookie'
    return mode if mode in ('cookie', 'accesskey') else 'cookie'


def aliyun_configured(saved):
    """Cookie and AccessKey are two independent credential paths."""
    if aliyun_mode(saved) == 'accesskey':
        return bool((saved.get('aliyun_accesskey_id') or {}).get('value') and
                    (saved.get('aliyun_accesskey_secret') or {}).get('value'))
    return bool((saved.get('aliyun') or {}).get('value'))


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
    dms = read(folder/'dms-snapshot.json', EMPTY_DMS)
    if dms.get('available'):
        rows = dms['instances']
        if len({(r.get('实例ID'), r.get('实例地址')) for r in rows}) != len(rows):
            raise ValueError('DMS 实例存在重复的记录')
    return {'applications': len(inventory['apps']), 'repositories': len(repos['repos']), 'servers': len(ids),
            'ecs': len(data[FILES[3]].get('instances', [])), 'clb': len(clb['instances']) if clb.get('available') else None,
            'dms': len(dms['instances']) if dms.get('available') else None,
            'nat': len(nat['gateways']) if nat.get('available') else None,
            'dnat': sum(len(g['entries']) for g in nat['gateways']) if nat.get('available') else None}


class Service:
    def __init__(self):
        os.umask(0o077)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / 'versions').mkdir(exist_ok=True)
        if (DATA/'secrets/cookies.json').exists(): write(DATA/'secrets/cookies.json', credentials())
        self.lock = threading.RLock()
        # Serialises the whole build/launch/swap in activate() so a concurrent
        # single-source retry cannot race the primary job for ports 3101/3102.
        self.activate_lock = threading.Lock()
        self.catalog_store = Catalog.from_environment()
        self.catalog_lock = threading.Lock()
        self.catalog_job = read(DATA/'catalog-job.json', {'status': 'idle'})
        if self.catalog_job.get('status') in ('queued', 'running'):
            self.catalog_job = {'status': 'failed', 'finishedAt': now(), 'error': '容器重启，目录导入已中断；将重新读取当前快照'}
            write(DATA/'catalog-job.json', self.catalog_job)
        self.backend = None
        self.retired = None
        self.check_cancel = threading.Event()
        self.files_lock = threading.Lock()
        self.archive_cancel = threading.Event()
        self.archive_job = None
        for archived in (DATA/'checks').glob('*/archive.json'):
            job = read(archived, {})
            if job.get('status') == 'running':
                job.update(status='failed', phase='容器重启，日志下载已中断')
                write(archived, job)
            partial = archived.with_name('logs.part')
            partial.unlink(missing_ok=True)
        self.check_job = read(DATA/'checks/latest.json')
        if self.check_job and self.check_job.get('status') == 'running':
            self.update_application_check_progress(self.check_job['id'], '容器重启，核验任务已中断', stage='failed')
            for activity in self.check_job.get('activities', []):
                if activity['stage'] not in ('complete', 'partial', 'failed', 'cancelled'):
                    activity.update(stage='failed', phase='容器重启，采集已中断', updatedAt=now())
            self.check_job.update(status='failed', phase='容器重启，核验任务已中断', finishedAt=now())
            write(DATA/'checks/latest.json', self.check_job)
            write(DATA/'checks'/self.check_job['id']/'job.json', self.check_job)
        self.job = read(DATA / 'job.json')
        if self.job and self.job.get('status') == 'running':
            self.job.update(status='failed', phase='容器重启，任务已中断；当前版本保持不变', finishedAt=now())
            write(DATA / 'job.json', self.job)
            folder = DATA / 'versions' / self.job['id']
            report = read(folder / 'version.json')
            if report and report.get('status') == 'collecting':
                report.update(status='failed', error='容器重启，采集未完成')
                write(folder / 'version.json', report)
        # Concurrent single-source retries run alongside the primary job and are
        # persisted separately so the primary job.json stays untouched.
        self.extra_jobs = read(DATA / 'extra-jobs.json', [])
        # A restart may follow a retry that already recovered every failed source;
        # clear the stale failed-sync banner before the UI reads it.
        self._reconcile_recovered_failure()
        if self.extra_jobs:
            restored = False
            for job in self.extra_jobs:
                if job.get('status') != 'running': continue
                job.update(status='failed', phase='容器重启，任务已中断；当前版本保持不变', finishedAt=now())
                report = read(DATA / 'versions' / job['id'] / 'version.json')
                if report and report.get('status') == 'collecting':
                    report.update(status='failed', error='容器重启，采集未完成')
                    write(DATA / 'versions' / job['id'] / 'version.json', report)
                restored = True
            if restored: write(DATA / 'extra-jobs.json', self.extra_jobs)
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

    def catalog_context(self):
        """Pin a completed active snapshot; never consume a collecting directory."""
        with self.lock:
            current = read(DATA/'current.json', {})
            identifier = current.get('id')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
                raise CatalogInputError('当前应用版本无效')
            folder = DATA/'versions'/identifier
            report = read(folder/'version.json', {})
            if report.get('status') != 'ready':
                raise CatalogError('当前版本仍在完成激活，请稍后更新部署单元')
            return identifier, (folder/'snapshot.json').read_bytes()

    def catalog_sync_once(self):
        if not self.catalog_lock.acquire(blocking=False):
            return dict(self.catalog_job)
        try:
            self.catalog_job = {'status': 'running', 'startedAt': now()}
            write(DATA/'catalog-job.json', self.catalog_job)
            identifier, raw = self.catalog_context()
            expected_hash = hashlib.sha256(raw).hexdigest()
            def still_current():
                try:
                    return hashlib.sha256(self.catalog_context()[1]).hexdigest() == expected_hash
                except (CatalogError, OSError):
                    return False
            result = self.catalog_store.import_snapshot(raw, identifier, f'versions/{identifier}/snapshot.json', can_apply=still_current)
            self.catalog_job = {'status': 'ready', 'finishedAt': now(), 'result': result}
        except CatalogError as exc:
            self.catalog_job = {'status': 'failed', 'finishedAt': now(), 'error': str(exc)}
        except Exception:
            self.catalog_job = {'status': 'failed', 'finishedAt': now(),
                                'error': '部署单元导入未完成，请检查 MySQL 配置和当前快照后重试'}
        finally:
            try:
                write(DATA/'catalog-job.json', self.catalog_job)
            except OSError:
                pass  # Database transaction and the original collection remain independent.
            self.catalog_lock.release()
        return dict(self.catalog_job)

    def start_catalog_sync(self):
        if not self.catalog_store.configured:
            raise CatalogError('部署单元数据库未配置，请通过 Compose 启动 MySQL 或配置 ENVSCOPE_DB_HOST')
        if not self.catalog_lock.locked():
            self.catalog_job = {'status': 'queued'}
            threading.Thread(target=self.catalog_sync_once, daemon=True, name='catalog-import').start()
        return {'job': dict(self.catalog_job)}

    def catalog_loop(self):
        # Independent post-collection consumer: database failure cannot fail a sync.
        while True:
            self.catalog_sync_once()
            time.sleep(30)

    def catalog_query(self, params):
        query = params.get('q', [''])[0].strip()
        if len(query) > 200:
            raise CatalogInputError('搜索内容不能超过 200 个字符')
        presence = params.get('presence', ['present'])[0]
        environment = params.get('environment', [None])[0]
        unit_id = params.get('unitId', [None])[0]
        if presence not in ('present', 'all') or environment not in (None, *ENVIRONMENTS_CATALOG):
            raise CatalogInputError('部署单元筛选条件无效')
        if unit_id and not re.fullmatch(r'[0-9a-f-]{36}', unit_id):
            raise CatalogInputError('部署单元 ID 无效')
        result = self.catalog_store.read(query=query, page=bounded_int(params, 'page', 1, 1, 1000000),
            page_size=bounded_int(params, 'pageSize', 20, 1, 100), presence=presence,
            unit_id=unit_id, environment=environment)
        identifier, raw = self.catalog_context()
        current_snapshot = json.loads(raw)
        current_hash = hashlib.sha256(raw).hexdigest()
        result['activeSnapshot'] = {'version': identifier, 'collectedAt': current_snapshot.get('collectedAt'),
                                    'matchesCatalog': bool(result['source'] and result['source']['snapshotSha256'] == current_hash)}
        result['job'] = dict(self.catalog_job)
        return result

    def version(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
            raise ValueError('无效版本')
        folder = DATA/'versions'/identifier
        report = read(folder/'version.json')
        if not report or report['status'] != 'ready': raise ValueError('只能切换到完整版本')
        validate(folder)
        return folder

    def application_check_state(self, identifier=None):
        with self.lock:
            if identifier is None:
                job = self.check_job
            else:
                if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
                    raise ValueError('无效核验任务 ID')
                job = read(DATA/'checks'/identifier/'job.json')
                if not job: raise ValueError('核验任务不存在')
            result = read(DATA/'checks'/job['id']/'report.json') if job else None
            return {'job':deepcopy(job), 'report':result}

    def start_application_check(self, payload):
        with self.lock:
            if self.archive_job and self.archive_job.get('status') == 'running':
                raise ValueError('请等待日志下载完成后再开始检测')
            if self.check_job and self.check_job.get('status') == 'running':
                raise ValueError('已有应用核验任务正在运行')
            if self.job and self.job.get('status') == 'running' or any(j.get('status') == 'running' for j in self.extra_jobs):
                raise ValueError('请等待当前采集或版本切换完成后再核验')
            saved = credentials()
            if not saved.get('jumpserver', {}).get('value'):
                raise ValueError('请先保存 JumpServer Cookie')
            pid, duration, environment = payload.get('pid'), payload.get('duration', 30), payload.get('environment')
            if type(pid) is not int or not 0 < pid <= 2**31-1: raise ValueError('无效进程 ID')
            if type(duration) is not int or not 10 <= duration <= 300: raise ValueError('观察时间必须为 10–300 秒')
            if environment is not None and environment not in REQUEST_ENVIRONMENTS: raise ValueError('无效部署环境')
            source = read(DATA/'current.json')['id']
            folder = self.version(source)
            snapshot = read(folder/'jumpserver-snapshot.json', {})
            matches = [a for a in snapshot.get('assets', []) if a.get('id') == payload.get('asset')]
            if len(matches) != 1: raise ValueError('当前版本中不存在唯一的目标资产')
            asset = matches[0]
            processes = [p for p in (asset.get('inspection') or {}).get('processes', []) if p.get('pid') == pid]
            if len(processes) != 1: raise ValueError('所选进程不在当前快照中，请重新采集')
            identifier = datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')
            self.check_cancel = threading.Event()
            self.check_job = {'id':identifier, 'status':'running', 'phase':'正在通过 JumpServer 登录目标服务器',
                'startedAt':now(), 'sourceVersion':source, 'assetId':asset['id'], 'pid':pid,
                'environment':environment, 'duration':duration, 'progress':{}, 'activities':[], 'events':[]}
            self.update_application_check_progress(identifier, self.check_job['phase'],
                stage='login', serverIp=asset.get('ip'))
            threading.Thread(target=self.work_application_check,
                args=(identifier, asset, processes[0], snapshot, read(folder/'snapshot.json', {}), saved,
                      read(DATA/'secrets/ip-password-map.json', {}), environment, duration, self.check_cancel),
                daemon=True, name='application-check').start()
            return self.application_check_state(identifier)

    def update_application_check_progress(self, identifier, phase, *, scope='target', assetId=None,
                                          serverIp=None, stage=None, **stats):
        """Persist bounded, locally authored activity; never capture terminal output."""
        with self.lock:
            job = self.check_job
            if not job or job['id'] != identifier or job['status'] != 'running':
                return
            stamp = now()
            key = 'target' if scope == 'target' else 'peer:'+assetId
            activities = job.setdefault('activities', [])
            current = next((item for item in activities if item['key'] == key), None)
            if current is None:
                current = {'key':key, 'scope':scope, 'serverIp':serverIp}
                activities.append(current)
            current.update(phase=phase, stage=stage, updatedAt=stamp)
            if 'samples' in stats: current['samples'] = stats['samples']
            if scope == 'target':
                job['phase'] = phase
                job.setdefault('progress', {}).update({k:v for k,v in stats.items() if k in (
                    'samples', 'elapsedSeconds', 'samplingStartedAt', 'samplingFinishedAt',
                    'lastObservedAt', 'upstream', 'downstream')})
                if 'environment' in stats: job['environment'] = stats['environment']
            job['updatedAt'] = stamp
            events = job.setdefault('events', [])
            if not events or (events[-1]['key'], events[-1]['message']) != (key, phase):
                events.append({'at':stamp, 'key':key, 'scope':scope, 'serverIp':serverIp, 'message':phase})
                del events[:-60]
            write(DATA/'checks/latest.json', job)
            write(DATA/'checks'/identifier/'job.json', job)

    def work_application_check(self, identifier, asset, process, snapshot, inventory, saved, ip_map, environment, duration, cancel):
        def progress(phase, **details):
            if cancel.is_set() and details.get('scope', 'target') == 'target':
                return
            self.update_application_check_progress(identifier, phase, **details)
        try:
            from application_check import inspect_application
            report = inspect_application(asset, process, inventory.get('apps', []), snapshot.get('assets', []),
                saved, ip_map, environment, duration, cancel=cancel, progress=progress)
            report.update(taskId=identifier, sourceVersion=self.check_job['sourceVersion'])
            write(DATA/'checks'/identifier/'report.json', report)
            status = report['status']
            phase = {'complete':'核验完成', 'partial':'核验完成，部分信息未能确认',
                     'failed':'核验失败，请查看结果中的原因', 'cancelled':'核验已取消，已保留观察证据'}[status]
        except Exception as exc:
            status, phase = 'failed', '应用核验执行异常，请查看报告中的错误代码'
            error = {'stage':'worker', 'code':'CHECK_INTERNAL_ERROR', 'exitCode':None,
                     'message':'内部核验异常：'+type(exc).__name__, 'diagnostics':[]}
            write(DATA/'checks'/identifier/'report.json', {
                'schemaVersion':1, 'taskId':identifier, 'sourceVersion':self.check_job['sourceVersion'],
                'status':'failed', 'startedAt':self.check_job['startedAt'], 'finishedAt':now(),
                'environment':environment, 'target':{'assetId':asset['id'],'serverIp':asset.get('ip'),'pid':process['pid']},
                'upstream':[], 'downstream':[], 'warnings':[error['message']], 'error':error,
                'coverage':{'method':'ss-polling','requestedSeconds':duration,'limitations':[]}})
        with self.lock:
            self.update_application_check_progress(identifier, phase, stage=status, serverIp=asset.get('ip'))
            sampling = self.check_job.get('progress', {})
            if sampling.get('samplingStartedAt'):
                sampling.setdefault('samplingFinishedAt', now())
            for activity in self.check_job.get('activities', []):
                if activity['stage'] not in ('complete', 'partial', 'failed', 'cancelled'):
                    activity.update(stage=status, phase='任务已结束', updatedAt=now())
            self.check_job.update(status=status, phase=phase, finishedAt=now())
            if 'report' in locals(): self.check_job['environment'] = report.get('environment')
            write(DATA/'checks/latest.json', self.check_job)
            write(DATA/'checks'/identifier/'job.json', self.check_job)

    def cancel_application_check(self, identifier):
        with self.lock:
            if not self.check_job or self.check_job['id'] != identifier:
                raise ValueError('核验任务不存在')
            if self.check_job['status'] == 'running':
                self.check_cancel.set()
                self.update_application_check_progress(identifier, '正在结束检查并保存已观察证据', stage='cancelling')
            return self.application_check_state(identifier)

    def application_file_context(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
            raise ValueError('无效核验任务 ID')
        state = self.application_check_state(identifier)
        job, report = state['job'], state['report']
        if job['status'] == 'running' or not report:
            raise ValueError('请等待上下游检测完成后再读取应用文件')
        folder = self.version(job['sourceVersion'])
        snapshot = read(folder/'jumpserver-snapshot.json', {})
        assets = [a for a in snapshot.get('assets', []) if a.get('id') == job['assetId']]
        if len(assets) != 1: raise ValueError('原检测版本中目标资产不唯一')
        processes = [p for p in (assets[0].get('inspection') or {}).get('processes', []) if p.get('pid') == job['pid']]
        if len(processes) != 1: raise ValueError('原检测版本中目标进程不唯一')
        return assets[0], processes[0], report

    def open_application_files(self, identifier):
        from application_check import open_terminal, probe
        asset, selected, report = self.application_file_context(identifier)
        terminal, _ = open_terminal(asset, credentials(), read(DATA/'secrets/ip-password-map.json', {}))
        try:
            process = probe(terminal, 'target', selected['pid'])['processes'].get(selected['pid'])
            target = report.get('target', {})
            if not process or (selected.get('startedAt') and process.get('startedAt') != selected['startedAt']) or (
                target.get('startTicks') and target['startTicks'] != process['startTicks']):
                raise ValueError('应用进程已变化，请重新检测')
            return terminal, process, target.get('application')
        except Exception:
            terminal.close()
            raise

    def application_files(self, payload):
        from application_files import catalogue, read_file
        identifier, action = payload.get('id'), payload.get('action', 'list')
        self.application_file_context(identifier)
        if action == 'archive': return self.start_log_archive(identifier, payload.get('group', 'all'))
        if action == 'cancel-archive':
            with self.lock:
                if self.archive_job and self.archive_job['id'] == identifier and self.archive_job['status'] == 'running':
                    self.archive_cancel.set()
            return self.log_archive_state(identifier)
        if action not in ('list', 'read'): raise ValueError('无效文件操作')
        if not self.files_lock.acquire(blocking=False): raise ValueError('正在读取或下载应用文件，请稍后重试')
        terminal = None
        try:
            terminal, process, application = self.open_application_files(identifier)
            path = DATA/'checks'/identifier/'files.json'
            if action == 'list':
                result = catalogue(terminal, process, application)
                write(path, result)
                return result
            catalog = read(path)
            if not catalog or catalog.get('startTicks') != process['startTicks']:
                raise ValueError('请先刷新应用文件列表')
            entries = [f for f in catalog['files'] if f['id'] == payload.get('file')]
            if len(entries) != 1: raise ValueError('文件不在应用文件列表中')
            return read_file(terminal, process, entries[0])
        except ValueError: raise
        except Exception: raise ValueError('通过 JumpServer 读取应用文件失败，请检查登录态和权限')
        finally:
            if terminal: terminal.close()
            self.files_lock.release()

    def log_archive_state(self, identifier):
        self.application_check_state(identifier)
        return read(DATA/'checks'/identifier/'archive.json', {'status':'idle'})

    def start_log_archive(self, identifier, group='all'):
        if group not in ('all', 'history'): raise ValueError('无效日志打包范围')
        with self.lock:
            if self.archive_job and self.archive_job.get('status') == 'running': raise ValueError('已有日志下载任务正在运行')
            if self.check_job and self.check_job.get('status') == 'running': raise ValueError('请等待当前应用检测完成')
            if self.job and self.job.get('status') == 'running' or any(j.get('status') == 'running' for j in self.extra_jobs): raise ValueError('请等待当前采集或版本切换完成')
            if not read(DATA/'checks'/identifier/'files.json'): raise ValueError('请先读取应用文件列表')
            if not self.files_lock.acquire(blocking=False): raise ValueError('正在读取应用文件，请稍后重试')
            self.archive_cancel = threading.Event()
            self.archive_job = {'id':identifier, 'logGroup':group, 'status':'running', 'phase':'正在登录并确认日志目录', 'startedAt':now(), 'transferred':0, 'total':None}
            write(DATA/'checks'/identifier/'archive.json', self.archive_job)
            threading.Thread(target=self.work_log_archive, args=(identifier,), daemon=True, name='application-logs').start()
            return dict(self.archive_job)

    def work_log_archive(self, identifier):
        from application_files import archive_logs, catalogue
        folder = DATA/'checks'/identifier
        terminal = None
        def progress(phase, transferred, total):
            with self.lock:
                self.archive_job.update(phase=phase, transferred=transferred, total=total)
                write(folder/'archive.json', self.archive_job)
        try:
            terminal, process, application = self.open_application_files(identifier)
            catalog = catalogue(terminal, process, application)
            result = archive_logs(terminal, process, catalog.get('logScopes') or catalog['roots']['log'], folder/'logs.part', progress, self.archive_cancel.is_set,
                                  group=self.archive_job.get('logGroup', 'all'))
            os.replace(folder/'logs.part', folder/'logs.tar.gz')
            self.archive_job.update(result, status='complete', phase='日志压缩包已就绪')
        except Exception as exc:
            message = str(exc) if type(exc) is ValueError else '日志打包或传输失败，请检查 JumpServer 登录态与服务器权限'
            self.archive_job.update(status='cancelled' if self.archive_cancel.is_set() else 'failed', phase=message)
            (folder/'logs.part').unlink(missing_ok=True)
        finally:
            if terminal: terminal.close()
            self.archive_job['finishedAt'] = now()
            write(folder/'archive.json', self.archive_job)
            self.files_lock.release()

    def log_archive_file(self, identifier):
        state = self.log_archive_state(identifier)
        if state.get('status') != 'complete': raise ValueError('日志压缩包尚未就绪')
        path = DATA/'checks'/identifier/'logs.tar.gz'
        if not path.is_file(): raise ValueError('日志压缩包不存在，请重新打包')
        return path

    def nginx_configs(self, identifier, asset_id):
        folder = self.version(identifier)
        snapshot = read(folder/'jumpserver-snapshot.json', {})
        if not any(a['id'] == asset_id for a in snapshot.get('assets', [])):
            raise ValueError('该版本不存在此资产')
        filename = hashlib.sha256(asset_id.encode()).hexdigest()+'.json'
        result = read(folder/'nginx-configs'/filename)
        if result is None: raise ValueError('该版本未保存此资产的 Nginx 原始配置')
        return result

    def update_asset_root_password(self, asset_id, password):
        """Store one user-supplied root password so the next inspection can su -l with it.

        Writes only this asset's IP entry in the password map; collection then tries the
        mapped su password first and falls back to the normal probe (re-marking
        password_error if the new password is also wrong).
        """
        if not isinstance(asset_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', asset_id):
            raise ValueError('无效的资产 ID')
        if not isinstance(password, str) or not password or len(password) > 4096 or any(c in password for c in ('\r','\n','\x00')):
            raise ValueError('root 密码格式无效')
        with self.lock:
            current = read(DATA/'current.json')
            identifier = current.get('id') if isinstance(current, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
                raise ValueError('缺少当前数据版本')
            snapshot = read(DATA/'versions'/identifier/'jumpserver-snapshot.json', {})
            ip = next((a.get('ip') for a in snapshot.get('assets', []) if a.get('id') == asset_id and a.get('ip')), None)
            if not ip: raise ValueError('当前数据版本不存在此资产')
            ip_map = read(DATA/'secrets/ip-password-map.json', {})
            ip_map[ip] = {'method':'su', 'password':password, 'status':'normal', 'updatedAt':now()}
            write(DATA/'secrets/ip-password-map.json', ip_map)

    def ip_login_info(self, ip):
        """Return the login method and password stored for one JumpServer-managed IP.

        Reads the current data version's JumpServer snapshot (for hostname / asset id /
        inspection status) and secrets/ip-password-map.json (for the saved login
        method and password). Backs the /jumpserver/login/<ip> URL an operator can
        paste into the browser address bar to look up how to sudo or su into a
        specific machine; the URL is intentionally not linked from any page.
        """
        if not isinstance(ip, str) or not re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', ip):
            raise ValueError('IP 格式无效')
        with self.lock:
            current = read(DATA/'current.json')
            identifier = current.get('id') if isinstance(current, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', identifier):
                raise ValueError('缺少当前数据版本')
            snapshot = read(DATA/'versions'/identifier/'jumpserver-snapshot.json', {})
            ip_map = read(DATA/'secrets/ip-password-map.json', {})
        assets = snapshot.get('assets', []) if isinstance(snapshot, dict) else []
        asset = next((a for a in assets if isinstance(a, dict) and a.get('ip') == ip), None)
        if not asset:
            raise ValueError('当前数据版本未收录此 IP')
        inspection = asset.get('inspection') if isinstance(asset.get('inspection'), dict) else None
        entry = ip_map.get(ip) if isinstance(ip_map, dict) else None
        info = {
            'ip': ip,
            'hostname': asset.get('hostname'),
            'assetId': asset.get('id'),
            'loginStatus': inspection.get('loginStatus') if inspection else None,
            'sudoStatus': inspection.get('sudoStatus') if inspection else None,
            'inspectionCheckedAt': inspection.get('checkedAt') if inspection else None,
            'elevation': None,
            'note': '',
        }
        if isinstance(entry, dict):
            method = entry.get('method')
            status = entry.get('status')
            info['elevation'] = {
                'method': method,
                'status': status,
                # Only expose the password when the entry is known-good; never
                # surface a stored value alongside a password_error marker.
                'password': entry.get('password') if status == 'normal' else None,
                'updatedAt': entry.get('updatedAt'),
                'source': 'ip-password-map',
            }
            if status == 'password_error':
                info['note'] = '映射表已标记密码错误，采集时跳过此服务器的 root 提权；请在 JumpServer 页面"单独采集"时输入新 root 密码清除标记'
            elif status == 'normal' and method in ('sudo-i', 'su'):
                info['note'] = f'采集时使用 {method} 提权到 root；密码来自最近一次成功的提权或手动配置'
            else:
                info['note'] = '映射表存在此 IP 的记录，但方法或状态不完整'
        else:
            info['note'] = '未在 ip-password-map 中配置此服务器的提权方式；采集时按默认探测顺序：sudo -i（folidev）→ su -l（备选 root 密码1）→ su -l（备选 root 密码2）'
        return info

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

    def nginx_location_query(self,params):
        """Reverse lookup: nginx routes whose location URI contains the query -> file/host/upstream/backends."""
        status=self.topology_status()
        if not status: raise ValueError('尚未生成 Topology；请先在 Settings 页面基于当前数据版本生成')
        query=(params.get('q') or [''])[0].strip()
        if not query or len(query)>253: raise ValueError('q 必须是 1-253 个字符的 location URI')
        environment=(params.get('env') or [''])[0].strip().upper()
        if environment and environment not in REQUEST_ENVIRONMENTS: raise ValueError('env 必须是 PRODUCT、TEST 或 SIMULATION')
        command=['node',str(APP/'scripts'/'topology-path.mjs'),'nginx-location',query,'--json','--file',str(self.topology_file(status['id']))]
        if environment: command+=['--env',environment]
        try:
            proc=subprocess.run(command,cwd=APP,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            raise ValueError('Nginx Location 查询超时')
        if proc.returncode:
            detail=(proc.stderr or proc.stdout).strip()[-400:] or '未知错误'
            raise ValueError('Nginx Location 查询失败：'+detail)
        try: result=json.loads(proc.stdout)
        except json.JSONDecodeError: raise ValueError('Nginx Location 查询结果无法解析')
        return {'topology':{'id':status['id'],'generatedAt':status['generatedAt']},'result':result}

    def status(self):
        with self.lock:
            saved = credentials()
            versions = [read(p) for p in (DATA/'versions').glob('*/version.json')]
            job = self._job_view(dict(self.job)) if self.job else None
            alerts = read(DATA/'cookie-alerts.json', {})
            access_key_id = (saved.get('aliyun_accesskey_id') or {}).get('value') or ''
            secret = (saved.get('aliyun_accesskey_secret') or {}).get('value') or ''
            masked = (access_key_id[:6] + '****' + access_key_id[-4:]) if len(access_key_id) > 12 else access_key_id
            access_key_updated = max(filter(None, [
                (saved.get('aliyun_accesskey_id') or {}).get('updatedAt'),
                (saved.get('aliyun_accesskey_secret') or {}).get('updatedAt')]), default=None)
            return {'cookies':{key:{'configured':bool(saved.get(key,{}).get('value')),
                    'updatedAt':saved.get(key,{}).get('updatedAt'),
                    'alert':alerts.get(key)} for key in ('devops','aliyun','jumpserver','local_gitlab')},
                    'aliyunAccessKey':{'configured':bool(access_key_id and secret),
                    'mode':aliyun_mode(saved), 'accessKeyId':masked,
                    'updatedAt':access_key_updated,
                    'organizationId':(saved.get('codeup_organization_id') or {}).get('value') or '',
                    'alert':alerts.get('aliyun_accesskey_id')},
                    'folidev':{'configured':bool(saved.get('folidev',{}).get('value')), 'updatedAt':saved.get('folidev',{}).get('updatedAt')},
                    'rootAlt1':{'configured':bool(saved.get('rootAlt1',{}).get('value')), 'updatedAt':saved.get('rootAlt1',{}).get('updatedAt')},
                    'rootAlt2':{'configured':bool(saved.get('rootAlt2',{}).get('value')), 'updatedAt':saved.get('rootAlt2',{}).get('updatedAt')},
                    'current':read(DATA/'current.json'), 'schedule':read(DATA/'schedule.json'),
                    'versions':sorted(versions,key=lambda v:v['createdAt'],reverse=True), 'job':job,
                    'extraJobs':[self._job_view(dict(j)) for j in self.extra_jobs],
                    'sourceStates':self._source_states(),
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
            access = payload.get('aliyunAccessKey')
            if access is not None:
                if not isinstance(access, dict): raise ValueError('无效的阿里云 AccessKey 设置')
                mode = access.get('mode')
                if mode is not None:
                    if mode not in ('cookie','accesskey'): raise ValueError('无效的阿里云采集方式')
                    if aliyun_mode(saved) != mode:
                        saved['aliyun_auth_mode'] = {'value':mode,'updatedAt':now()}
                key_id = access.get('accessKeyId')
                if key_id is not None:
                    if not isinstance(key_id, str): raise ValueError('AccessKey ID 格式无效')
                    key_id = key_id.strip()
                    if key_id:
                        if len(key_id) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key_id):
                            raise ValueError('AccessKey ID 格式无效')
                        saved['aliyun_accesskey_id'] = {'value':key_id,'updatedAt':now()}
                secret = access.get('accessKeySecret')
                if secret is not None:
                    if not isinstance(secret, str): raise ValueError('AccessKey Secret 格式无效')
                    secret = secret.strip()
                    if secret:
                        if len(secret) > 256 or any(c in secret for c in ('\r','\n','\x00')):
                            raise ValueError('AccessKey Secret 格式无效')
                        saved['aliyun_accesskey_secret'] = {'value':secret,'updatedAt':now()}
                organization = access.get('organizationId')
                if organization is not None:
                    if not isinstance(organization, str): raise ValueError('Codeup 企业 ID 格式无效')
                    organization = organization.strip()
                    if len(organization) > 128 or any(ord(c) < 32 or ord(c) > 126 for c in organization):
                        raise ValueError('Codeup 企业 ID 格式无效')
                    if organization:
                        saved['codeup_organization_id'] = {'value':organization,'updatedAt':now()}
                    else:
                        saved.pop('codeup_organization_id', None)
                if aliyun_mode(saved) == 'accesskey' and not aliyun_configured(saved):
                    raise ValueError('选择 AccessKey 采集方式前，请填写 AccessKey ID 和 Secret')
            password = payload.get('folidevPassword', '')
            if not isinstance(password, str) or len(password) > 4096 or any(c in password for c in ('\r','\n','\x00')):
                raise ValueError('folidev 密码格式无效')
            if password: saved['folidev'] = {'value':password, 'updatedAt':now()}
            password_changed = bool(password)
            for alt_key, alt_field in (('rootAlt1','rootPasswordAlt1'), ('rootAlt2','rootPasswordAlt2')):
                alt_pw = payload.get(alt_field, '')
                if not isinstance(alt_pw, str) or len(alt_pw) > 4096 or any(c in alt_pw for c in ('\r','\n','\x00')):
                    raise ValueError('备选 root 密码格式无效')
                if alt_pw:
                    saved[alt_key] = {'value':alt_pw, 'updatedAt':now()}
                    password_changed = True
            if password_changed:
                ip_map = read(DATA/'secrets/ip-password-map.json', {})
                cleared = {ip: e for ip, e in ip_map.items() if e.get('status') != 'password_error'}
                if len(cleared) != len(ip_map):
                    write(DATA/'secrets/ip-password-map.json', cleared)
            schedule = payload.get('schedule')
            if schedule is not None:
                if not isinstance(schedule.get('enabled'),bool) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',schedule.get('time','')):
                    raise ValueError('无效定时设置')
                if schedule['enabled'] and (not aliyun_configured(saved) or
                        any(not saved.get(k,{}).get('value') for k in ('devops','jumpserver','local_gitlab'))):
                    raise ValueError('启用每日同步前，请配置四个平台所需的 Cookie / AccessKey')
            write(DATA/'secrets/cookies.json',saved)
            alerts=read(DATA/'cookie-alerts.json',{})
            for key,value in payload.get('cookies',{}).items():
                if isinstance(value,str) and value.strip(): alerts.pop('aliyun' if key=='codeup' else key,None)
            if isinstance(access, dict) and isinstance(access.get('accessKeyId'), str) and access['accessKeyId'].strip():
                alerts.pop('aliyun_accesskey_id', None)
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
                if (failure.get('type') in ('cookie_expired','accesskey_invalid') and key in saved and
                        failure.get('credentialUpdatedAt')==saved[key].get('updatedAt')):
                    alerts[key]={'message':failure['message'],'detectedAt':now()}
            write(DATA/'cookie-alerts.json',alerts)
        return error.get('message') or '采集失败，请检查连接或平台响应'

    def set_job(self, **changes):
        with self.lock:
            self.job.update(changes); write(DATA/'job.json',self.job)

    def set_extra_job(self, job, **changes):
        with self.lock:
            job.update(changes); write(DATA/'extra-jobs.json',self.extra_jobs)

    def _set_job(self, job, **changes):
        # Route updates to the primary job.json or the concurrent extra-jobs.json.
        if job is self.job: self.set_job(**changes)
        else: self.set_extra_job(job, **changes)

    def _reconcile_recovered_failure(self):
        """Drop a failed full sync once every failed source has a successful retry.

        A concurrent retry builds and activates its own version, so leaving the
        primary failure in job.json keeps advertising a problem the user has
        already resolved. Version history still retains the failed record.
        """
        with self.lock:
            primary = self.job
            if not primary or primary.get('kind') != 'sync' or primary.get('status') != 'failed': return
            folder = DATA/'versions'/str(primary.get('id') or '')
            try: error = read(folder/'collection-error.json', {})
            except (json.JSONDecodeError, OSError): error = {}
            failed = {str(item.get('source')) for item in (error or {}).get('failures') or [] if item.get('source')}
            if not failed: return
            succeeded = {SOURCE_LABELS.get(job.get('kind')) for job in self.extra_jobs
                         if job.get('status') == 'succeeded'}
            if not failed.issubset(succeeded): return
            self.job = None
            write(DATA/'job.json', {})
            self.extra_jobs = [job for job in self.extra_jobs if job.get('status') != 'succeeded']
            write(DATA/'extra-jobs.json', self.extra_jobs)

    def _active_jobs(self):
        jobs = []
        if self.job and self.job.get('status') == 'running': jobs.append(self.job)
        jobs.extend(j for j in self.extra_jobs if j.get('status') == 'running')
        return jobs

    def _any_running(self):
        return bool(self._active_jobs())

    def _job_view(self, job):
        """Return a job copy enriched with live progress, logs and failure detail."""
        if not job: return job
        # Every kind except 'switch' runs collect.py and writes collection.log
        # / progress.json / slots; switch only swaps an already-ready version.
        if job.get('status') == 'running' and job.get('kind') != 'switch':
            folder = DATA/'versions'/job['id']
            try: progress = read(folder/'progress.json')
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
            if progress and 'DevOps' in progress.get('phase','') and job.get('phase') == '正在采集数据':
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
        # For failed (or still-finishing) jobs, surface the full collection
        # log and structured failure details so the user can see exactly
        # which source failed and why, not just a generic error string.
        if job.get('id') and job.get('kind') != 'switch':
            folder = DATA/'versions'/job['id']
            if 'logTail' not in job:
                log = folder/'collection.log'
                if log.exists():
                    try: job['logTail'] = log.read_text(errors='replace')[-12000:]
                    except OSError: pass
            try: error = read(folder/'collection-error.json', {})
            except (json.JSONDecodeError, OSError): error = {}
            if error.get('failures'):
                job['failures'] = error['failures']
        return job

    def _source_available(self, kind):
        """Whether a single-source sync for `kind` may start right now."""
        if kind not in SOURCE_LABELS: return False
        if not self._any_running(): return True
        if any(j.get('kind') == kind for j in self.extra_jobs if j.get('status') == 'running'):
            return False
        # Only one concurrent retry at a time; two would share the same base version.
        if any(j.get('status') == 'running' for j in self.extra_jobs): return False
        # Only a full sync whose source already failed may be retried concurrently:
        # parallel_collect() raises as soon as one source fails, so that sync can
        # never activate and cannot overwrite the retry's freshly built version.
        if not (self.job and self.job.get('status') == 'running' and self.job.get('kind') == 'sync' and self.job.get('id')):
            return False
        progress = read(DATA/'versions'/self.job['id']/'progress.json', {})
        return ((progress or {}).get('sources') or {}).get(SOURCE_LABELS[kind]) == '失败'

    def _source_states(self):
        """Per-source {busy, retryable, reason} the Settings page renders as button gates."""
        active = self._any_running()
        primary = self.job if (self.job and self.job.get('status') == 'running') else None
        progress = read(DATA/'versions'/primary['id']/'progress.json', {}) if primary and primary.get('kind') == 'sync' and primary.get('id') else {}
        sources = (progress or {}).get('sources') or {}
        extra_running = any(j.get('status') == 'running' for j in self.extra_jobs)
        busy_kinds = {j.get('kind') for j in self.extra_jobs if j.get('status') == 'running'}
        states = {}
        for kind, label in SOURCE_LABELS.items():
            busy = kind in busy_kinds or sources.get(label) == '采集中'
            retryable = self._source_available(kind)
            if not active: reason = ''
            elif retryable: reason = '本轮同步该来源已失败，可立即并发重试'
            elif busy: reason = '该数据源正在采集中'
            elif extra_running: reason = '已有并发重试任务进行中，请等待其结束'
            else: reason = '需等待当前任务结束后再单独同步'
            states[kind] = {'busy': busy, 'retryable': retryable, 'reason': reason}
        return states

    def start(self, kind, identifier=None, asset_id=None):
        with self.lock:
            if self.archive_job and self.archive_job.get('status') == 'running': raise ValueError('请等待日志下载结束后再采集或切换版本')
            if self.check_job and self.check_job.get('status') == 'running': raise ValueError('请等待应用核验任务结束后再采集或切换版本')
            if self.backend is None: raise ValueError('应用正在恢复当前版本，请稍后重试')
            source_modes = ('local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'dms', 'jumpserver', 'devops', 'jumpserver_asset')
            # While a job is running only a single-source retry whose source already
            # failed in the running full sync may start, and it runs concurrently.
            is_extra = False
            if self._any_running():
                if kind in SOURCE_LABELS and self._source_available(kind):
                    is_extra = True
                else:
                    reason = self._source_states().get(kind, {}).get('reason')
                    raise ValueError(f'{SOURCE_LABELS[kind]} 当前不可单独同步：{reason}' if reason
                                     else '已有同步或切换任务正在进行')
            if kind in ('sync', *source_modes):
                saved = credentials()
                aliyun_ok = aliyun_configured(saved)
                aliyun_hint = ('请先保存阿里云 AccessKey ID 和 Secret' if aliyun_mode(saved) == 'accesskey'
                               else '请先保存阿里云 Cookie')
                if kind == 'sync':
                    missing_other = any(not saved.get(k,{}).get('value') for k in ('devops','jumpserver','local_gitlab'))
                    if missing_other or not aliyun_ok:
                        raise ValueError('请先保存四个平台的 Cookie' if missing_other else aliyun_hint)
                elif kind in ('ecs','clb','nat','dms','codeup'):
                    if not aliyun_ok: raise ValueError(aliyun_hint)
                elif kind == 'jumpserver_asset':
                    if not saved.get('jumpserver',{}).get('value'):
                        raise ValueError('请先保存 JumpServer Cookie')
                elif not saved.get(kind,{}).get('value'):
                    raise ValueError('请先保存 Local GitLab Cookie' if kind == 'local_gitlab'
                                     else '请先保存对应平台的 Cookie')
                if kind == 'jumpserver_asset':
                    if not isinstance(asset_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', asset_id):
                        raise ValueError('无效的资产 ID')
                for _ in range(5):
                    identifier = datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')
                    folder = DATA/'versions'/identifier
                    try: folder.mkdir(); break
                    except FileExistsError: continue
                else: raise ValueError('无法创建版本目录，请重试')
                note = '单独重新采集一台服务器的进程与 Nginx' if kind == 'jumpserver_asset' else \
                    f'单独同步 {kind}' if kind in source_modes else '同步 DevOps、Codeup、JumpServer、上海 ECS、CLB 和 NAT'
                write(folder/'version.json',{'id':identifier,'createdAt':now(),'status':'collecting','kind':kind,
                      'note':note})
            else: self.version(identifier)
            if is_extra:
                record = {'id':identifier,'kind':kind,'status':'running','startedAt':now(),
                          'phase':'任务准备中','assetId':asset_id,'concurrent':True}
                self.extra_jobs.append(record)
                if len(self.extra_jobs) > 6: self.extra_jobs = self.extra_jobs[-6:]
                write(DATA/'extra-jobs.json', self.extra_jobs)
                threading.Thread(target=self.work,args=(kind,identifier,asset_id,record),daemon=True,name='extra-sync').start()
                return identifier
            # No concurrent retry can be running here (they are handled above), so
            # this clears finished extra records before a fresh primary job starts.
            if self.extra_jobs:
                self.extra_jobs = [j for j in self.extra_jobs if j.get('status') == 'running']
                write(DATA/'extra-jobs.json', self.extra_jobs)
            self.job={'id':identifier,'kind':kind,'status':'running','startedAt':now(),'phase':'任务准备中','assetId':asset_id}
            write(DATA/'job.json',self.job)
            threading.Thread(target=self.work,args=(kind,identifier,asset_id),daemon=True).start()
            return identifier

    def work(self,kind,identifier,asset_id=None,job=None):
        folder=DATA/'versions'/identifier
        if job is None: job = self.job
        try:
            if kind in ('sync', 'local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'dms', 'jumpserver', 'devops', 'jumpserver_asset'):
                self._set_job(job, phase='正在采集数据')
                current_folder = self.version(read(DATA/'current.json')['id'])
                if kind in ('local_gitlab', 'codeup', 'ecs', 'clb', 'nat', 'dms', 'jumpserver', 'devops', 'jumpserver_asset'):
                    for name in FILES: shutil.copyfile(current_folder/name, folder/name)
                    for name in OPTIONAL_FILES: write(folder/name, read(current_folder/name, OPTIONAL_DEFAULTS[name]))
                    # A source sync must produce fresh data, not accidentally retain the copied snapshot.
                    if kind in ('clb', 'nat', 'dms'): (folder/f'{kind}-snapshot.json').unlink(missing_ok=True)
                    with tempfile.TemporaryDirectory(prefix='envscope-input-') as tmp:
                        creds=Path(tmp)/'credentials.json'
                        creds_data=credentials(); creds_data['_ipPasswordMap']=read(DATA/'secrets/ip-password-map.json',{})
                        write(creds, creds_data)
                        command=['python3',str(APP/'runtime/collect.py'),str(folder),str(creds),kind]
                        if kind == 'jumpserver_asset': command.append(asset_id)
                        with (folder/'collection.log').open('w') as log:
                            p=subprocess.run(command,stdout=log,stderr=log,timeout=7200)
                    if not p.returncode:
                        map_path=folder/'ip-password-map-updated.json'
                        if map_path.exists():
                            updates=read(map_path,{})
                            if updates:
                                with self.lock:
                                    ip_map=read(DATA/'secrets/ip-password-map.json',{})
                                    ip_map.update(updates)
                                    write(DATA/'secrets/ip-password-map.json',ip_map)
                    if p.returncode:
                        detail=self.collection_error(folder)
                        raise ValueError(f'{kind} 采集失败：{detail}；当前版本未改变')
                    if kind in ('clb', 'nat', 'dms') and not read(folder/f'{kind}-snapshot.json', {}).get('available'):
                        raise ValueError(f'缺少本次 {kind.upper()} 采集数据')
                    counts=validate(folder)
                    # Defer marking the version ready until activate() swaps in the
                    # new backend; otherwise Settings advertises CLB/NAT counts
                    # before the CLB panorama page can actually serve them.
                    self._set_job(job, phase='数据采集完成，正在构建并切换到新版本')
                else:
                    previous = read(current_folder/'ecs-snapshot.json')
                    write(folder/'ecs-projects.json', {'projects':previous['projects'],'note':'项目名称目录沿用前版；ECS 实例及项目 ID 本次实时采集'})
                    with tempfile.TemporaryDirectory(prefix='envscope-input-') as tmp:
                        creds=Path(tmp)/'credentials.json'
                        with self.lock:
                            creds_data=credentials(); creds_data['_ipPasswordMap']=read(DATA/'secrets/ip-password-map.json',{})
                            write(creds,creds_data)
                        with (folder/'collection.log').open('w') as log:
                            p=subprocess.run(['python3',str(APP/'runtime/collect.py'),str(folder),str(creds)],stdout=log,stderr=log,timeout=7200)
                        if not p.returncode:
                            map_path=folder/'ip-password-map-updated.json'
                            if map_path.exists():
                                updates=read(map_path,{})
                                if updates:
                                    with self.lock:
                                        ip_map=read(DATA/'secrets/ip-password-map.json',{})
                                        ip_map.update(updates)
                                        write(DATA/'secrets/ip-password-map.json',ip_map)
                        if p.returncode:
                            detail=self.collection_error(folder)
                            raise ValueError('采集失败：'+detail+'；当前版本未改变')
                    if not read(folder/'clb-snapshot.json',{}).get('available'): raise ValueError('缺少本次 CLB 采集数据')
                    if not read(folder/'nat-snapshot.json',{}).get('available'): raise ValueError('缺少本次 NAT 采集数据')
                    counts=validate(folder)
                    self._set_job(job, phase='数据采集完成，正在构建并切换到新版本')
            self.activate(identifier)
            # Only flip the version to ready after the new backend is live, so
            # the Settings version row and the CLB panorama page agree on what
            # data is actually served. switch() re-activates an already-ready
            # version, so it must not rewrite counts.
            if kind != 'switch':
                report=read(folder/'version.json');report.update(status='ready',finishedAt=now(),counts=counts)
                write(folder/'version.json',report)
            self._set_job(job, status='succeeded',phase='已切换到指定数据版本' if kind=='switch' else '同步完成，已启用新版本',finishedAt=now())
            self._reconcile_recovered_failure()
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else '任务失败或超时，当前版本保持不变；请检查连接后重试'
            report=read(folder/'version.json')
            if report and report['status']=='collecting':
                report.update(status='failed',error=message,finishedAt=now());write(folder/'version.json',report)
            self._set_job(job, status='failed',phase=message,finishedAt=now())
            self._reconcile_recovered_failure()

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
        # Serialise the whole build/launch/swap so a concurrent single-source
        # retry cannot race the primary job for ports 3101/3102 or clobber
        # self.backend/self.retired. Acquired outside self.lock: status() polls
        # self.lock every few seconds and must not block behind a long build.
        with self.activate_lock:
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
        if self.catalog_store.configured:
            threading.Thread(target=self.catalog_loop, daemon=True, name='catalog-worker').start()

    def scheduler(self):
        while True:
            try:
                with self.lock:
                    schedule=read(DATA/'schedule.json');stamp=datetime.now(TZ)
                    if schedule['enabled'] and stamp.strftime('%H:%M')>=schedule['time'] and schedule.get('lastDate')!=stamp.date().isoformat() and not self._any_running():
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

    def topology_download(self, identifier):
        try: body=self.server.service.topology_file(identifier).read_bytes()
        except (OSError,ValueError) as exc:return self.json(404,{'error':str(exc)})
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Disposition',f'attachment; filename="envscope-{identifier}.json"')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    def jumpserver_login_info(self):
        # /jumpserver/login/<ip> — operator-only lookup of the saved sudo/su
        # method and password for one machine. The URL is not linked from any
        # page; it must be typed into the browser address bar. The path may
        # carry a query string (e.g. cache busters), so split before parsing.
        path = urlsplit(self.path).path
        prefix = '/jumpserver/login/'
        if not path.startswith(prefix) or path == prefix:
            return self.json(404, {'error': '请在 /jumpserver/login/ 后提供 IP 地址'})
        ip = path[len(prefix):]
        if not re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', ip):
            return self.json(400, {'error': 'IP 格式无效'})
        try:
            return self.json(200, self.server.service.ip_login_info(ip))
        except ValueError as exc:
            return self.json(404, {'error': str(exc)})

    def allowed_host(self):
        host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        if host in ('localhost', '127.0.0.1', '::1'): return True
        if '*' in ALLOWED_HOSTS: return True
        return host in ALLOWED_HOSTS
    def do_GET(self):
        if not self.allowed_host():return self.json(403,{'error':'仅允许本机访问'})
        if urlsplit(self.path).path == '/api/catalog/deployment-units':
            try:
                return self.json(200, self.server.service.catalog_query(parse_qs(urlsplit(self.path).query)))
            except (CatalogInputError, ValueError) as exc: return self.json(400, {'error': str(exc)})
            except CatalogError as exc: return self.json(503, {'error': str(exc)})
            except Exception: return self.json(503, {'error': '部署单元暂不可用，请稍后重试'})
        if urlsplit(self.path).path in ('/api/settings/application-check/logs', '/api/settings/application-check/logs/download'):
            identifier = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            try:
                if urlsplit(self.path).path.endswith('/download'):
                    path = self.server.service.log_archive_file(identifier)
                    with path.open('rb') as source:
                        self.send_response(200); self.send_header('Content-Type','application/gzip')
                        self.send_header('Content-Disposition',f'attachment; filename="application-logs-{identifier}.tar.gz"')
                        self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(os.fstat(source.fileno()).st_size))
                        self.end_headers(); shutil.copyfileobj(source, self.wfile, 262144)
                    return
                return self.json(200, self.server.service.log_archive_state(identifier))
            except ValueError as exc: return self.json(404, {'error':str(exc)})
        if urlsplit(self.path).path == '/api/settings/application-check':
            params = parse_qs(urlsplit(self.path).query)
            try: return self.json(200,self.server.service.application_check_state(params.get('id',[None])[0]))
            except ValueError as exc: return self.json(404,{'error':str(exc)})
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
        if self.path == '/api/topology/nginx-location' or self.path.startswith('/api/topology/nginx-location?'):
            params=parse_qs(urlsplit(self.path).query)
            try: return self.json(200,self.server.service.nginx_location_query(params))
            except ValueError as exc: return self.json(400,{'error':str(exc)})
        if self.path.startswith('/jumpserver/login/'):
            return self.jumpserver_login_info()
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
            if self.path=='/api/catalog/deployment-units/import':
                return self.json(202, service.start_catalog_sync())
            elif self.path=='/api/settings/config':service.save_settings(payload)
            elif self.path=='/api/settings/application-check':
                return self.json(202,service.start_application_check(payload))
            elif self.path=='/api/settings/application-check/cancel':
                return self.json(200,service.cancel_application_check(payload.get('id')))
            elif self.path=='/api/settings/application-check/files':
                return self.json(200,service.application_files(payload))
            elif self.path=='/api/settings/topology':service.generate_topology()
            elif self.path=='/api/settings/sync':service.start('sync')
            elif self.path=='/api/settings/sync/local-gitlab':service.start('local_gitlab')
            elif self.path.startswith('/api/settings/sync/'):
                source = self.path.rsplit('/', 1)[-1]
                if source not in ('ecs', 'clb', 'nat', 'dms', 'jumpserver', 'devops', 'codeup'): return self.json(404,{'error':'不支持的数据源'})
                service.start(source)
                return self.json(200, service.status())
            elif self.path=='/api/settings/jumpserver-inspect':
                # rootPassword 存在时先更新这台服务器在密码映射表中的记录，再按原流程开始单独采集。
                root_password = payload.get('rootPassword')
                if root_password is not None:
                    service.update_asset_root_password(payload.get('asset'), root_password)
                service.start('jumpserver_asset', asset_id=payload.get('asset'))
                return self.json(200, service.status())
            elif self.path=='/api/settings/switch':service.start('switch',payload.get('id'))
            else:return self.json(404,{'error':'接口不存在'})
            return self.json(200,service.status())
        except json.JSONDecodeError:return self.json(400,{'error':'请求 JSON 格式无效'})
        except ValueError as exc:return self.json(400,{'error':str(exc)})
        except CatalogError as exc:return self.json(503,{'error':str(exc)})
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
