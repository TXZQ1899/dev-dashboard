#!/usr/bin/env python3
"""Partial serial inspection runner; incomplete checks are explicitly reported."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape
from analyze_logs import analyze
from dependencies import collect_config, inspect_dubbo, tcp_display_rows, unlisted_urls
from tcp_run import collect as collect_tcp

HERE = Path(__file__).resolve().parent


def log(message):
    print('[%s] %s' % (dt.datetime.now().astimezone().isoformat(timespec='seconds'), message), flush=True)


def cell(value):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]', '', str('' if value is None else value))
    # Excel's maximum text length per cell.
    return '<c t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % escape(value[:32767])


def workbook(path, sheets):
    names = list(sheets)
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i+1) for i in range(len(names))) + '</Types>'
    wb = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(name, {'"': '&quot;'}), i+1, i+1) for i, name in enumerate(names)) + '</sheets></workbook>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i+1, i+1) for i in range(len(names))) + '</Relationships>'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', types)
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', rels)
        for index, rows in enumerate(sheets.values(), 1):
            body = ''.join('<row r="%d">%s</row>' % (n, ''.join(cell(v) for v in row)) for n, row in enumerate(rows, 1))
            z.writestr('xl/worksheets/sheet%d.xml' % index, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + body + '</sheetData></worksheet>')


def collect_host(ip, name, version, directory):
    encoded = base64.b64encode((HERE/'remote_probe.py').read_bytes()).decode('ascii')
    command = ['docker', '--context', 'lenovo', 'exec', '-i', 'envscope-local', 'python3', '-', ip, name, version, encoded]
    processes, archives, issues = [], [], []
    output = None; target = None; expected = ''; digest = None; announced = 0; count = 0; observed = None
    with (HERE/'live_scan.py').open('rb') as bridge:
        child = subprocess.Popen(command, stdin=bridge, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in child.stdout:
                event = json.loads(line)
                kind = event.get('event')
                if kind == 'host':
                    observed = event.get('observedAt')
                    log('%s 已登录，发现 %s 个进程候选' % (ip, event.get('count')))
                    if not event.get('count'): issues.append('未发现应用进程，不能证明未运行')
                    if event.get('listenerStatus') != 'complete': issues.append('监听端口查询失败')
                elif kind == 'process': processes.append(event['process'])
                elif kind == 'warning' or kind == 'error':
                    issues.append(event.get('message', '未知错误')); log('%s：%s' % (ip, issues[-1]))
                elif kind == 'scopes': log('%s PID %s：发现 %d 个日志范围' % (ip, event['pid'], len(event['scopes'])))
                elif kind == 'archive_start':
                    if output: raise ValueError('重复的归档开始事件')
                    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    directory.chmod(0o700)
                    target = directory / ('%s-%s-%s.tar.gz' % (ip, event['pid'], uuid.uuid4().hex[:8]))
                    output = target.open('xb'); expected = event['sha256']; digest = hashlib.sha256(); announced = event['size']; count = 0
                    if event.get('tarStatus'): issues.append('日志打包时文件仍在变化')
                    log('%s PID %s：开始传输 %d 个日志文件（压缩后 %d 字节）' % (ip, event['pid'], event['fileCount'], announced))
                elif kind == 'archive_chunk':
                    if not output: raise ValueError('归档分块没有开始事件')
                    data = base64.b64decode(event['data'], validate=True); count += len(data)
                    if count > announced: raise ValueError('归档长度超出声明')
                    output.write(data); digest.update(data)
                    log('%s：日志传输 %d/%d 字节' % (ip, count, announced))
                elif kind == 'archive_end':
                    if not output or count != announced or digest.hexdigest() != expected or event.get('sha256') != expected: raise ValueError('归档完整性校验失败')
                    output.close(); output = None; archives.append(target); target = None
            rc = child.wait()
            if rc: issues.append('JumpServer 桥接进程失败（退出码 %s）' % rc)
            if output: issues.append('日志归档传输未完成')
        except Exception:
            child.kill(); child.wait(); raise
        finally:
            if output: output.close()
            if target: target.unlink(missing_ok=True)
    return processes, archives, issues, observed


def log_status(archives, analyzed, analysis_errors, no_access, issues):
    errors = list(analysis_errors)
    if any('日志采集失败' in x or '归档传输未完成' in x or '打包时文件仍在变化' in x for x in issues):
        errors.append('部分日志采集或传输不完整')
    state = 'UNKNOWN' if not archives or not analyzed else 'PARTIAL' if errors else 'COMPLETE'
    note = ('无 ACCESS LOG（已采集日志中未识别标准格式）；' if no_access else '') + '仅覆盖已发现日志；' + '; '.join(errors)
    return state, note


def kong_rows(kong):
    rows = [['Service Name', 'Route Name', 'Upstream Name', 'Target IP + 端口']]
    for match in kong.get('matches', []):
        upstream = match.get('upstream', {}).get('name', '')
        target = match.get('target', {}).get('target', '')
        services = match.get('services') or []
        if not services:
            rows.append(['', '', upstream, target])
        for service in services:
            name = service.get('service', {}).get('name') or ''
            routes = service.get('routes') or []
            if not routes: rows.append([name, '', upstream, target])
            for route in routes: rows.append([name, route.get('name') or '', upstream, target])
    return rows


def short_status(row, snap, kong, dns, processes):
    label, state, detail = row
    if label.startswith('当前进程 ') and state == 'UNKNOWN':
        return [label, state, '未匹配进程']
    if state not in ('COMPLETE', 'PARTIAL'):
        return row
    if label == 'Nginx' and snap:
        parts = []
        for endpoint in snap['endpoints']:
            matches = [r for r in snap['nginx'] if list(endpoint) in [list(e) for e in r.get('matchedEndpoints', [])]]
            routes = [r for r in matches if r.get('evidence') == '解析路由']
            hosts = {r.get('serverIp') for r in routes if r.get('serverIp')}
            prefix = '%s:%s ' % tuple(endpoint)
            if routes and not hosts: part = '匹配路由，Nginx 服务器 IP 未知'
            elif routes: part = '被 %d 个 Nginx 代理' % len(hosts) + ('（部分服务器 IP 未知）' if any(not r.get('serverIp') for r in routes) else '')
            elif matches: part = '发现配置引用，代理关系待确认'
            else: part = '未发现被 Nginx 代理'
            parts.append(prefix+part)
        summary = '；'.join(parts) or '无可匹配的应用端点'
    elif label == '配置引用' and snap:
        refs = [r for r in snap['references'] if str(r.get('appId','')) != str(snap['appId'])]
        ids = {str(r['appId']) for r in refs if r.get('appId')}
        summary = '被 %d 个其他应用引用' % len(ids) if ids else '未发现被其他应用直接引用'
        if any(not r.get('appId') for r in refs): summary += '；部分应用身份未知'
    elif label == 'Kong' and kong is not None:
        summary = '被「Kong 网关：%s」管理' % urlsplit(kong.get('admin','')).netloc if kong.get('matches') else '未发现被 Kong 网关管理'
    elif label == 'DNS' and dns is not None: summary = '匹配 %d 条 DNS 记录' % len(dns.get('records', []))
    elif label.startswith('当前进程 '):
        found = processes.get(label[len('当前进程 '):], [])
        summary = '匹配进程 PID：'+', '.join(str(p['pid']) for p in found) if found else '未匹配进程'
    elif label.startswith('应用日志 '):
        summary = '无 ACCESS LOG' if '无 ACCESS LOG' in detail else '有 ACCESS LOG'
        if state == 'PARTIAL': summary += '；分析不完整'
    else: return row
    if state == 'PARTIAL' and not label.startswith('应用日志 '): summary += '；检查不完整'
    return [label, state, summary]


def component_system_counts(config):
    components = set()
    unresolved = 0
    for item in config.get('components', []):
        host = str(item.get('host') or '').strip().rstrip('.').lower()
        try: port = int(item.get('port'))
        except (TypeError, ValueError): port = 0
        if host and 0 < port <= 65535 and item.get('confidence') != 'UNKNOWN': components.add((host, port))
        else: unresolved += 1
    systems = set()
    for item in unlisted_urls(config.get('urls', []), config.get('components', [])):
        try:
            value = urlsplit(item.get('url') or '')
            port = value.port or {'http':80, 'https':443}.get(value.scheme.lower())
            if value.hostname and port: systems.add((value.hostname.rstrip('.').lower(), port))
        except ValueError: continue
    return len(components), len(systems), unresolved


def endpoint_counts(report):
    result, missing = {}, 0
    for direction in ('upstream', 'downstream'):
        endpoints = set()
        for flow in report.get(direction, []):
            peer = flow.get('peer') or {}
            ip, port = peer.get('ip'), peer.get('port')
            if ip and isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535:
                endpoints.add((ip, port))
            else: missing += 1
        result[direction] = len(endpoints)
    return result, missing


def requested_status(row, config, dubbo, reports):
    label, state, _ = row
    if label == '应用配置与关联组件' and config and config.get('files'):
        actual_warnings = [w for w in config.get('warnings', []) if not w.startswith('生产配置原文未导出')]
        count, systems, unresolved = component_system_counts(config)
        note = '识别 %d 个关联组件，%d 个关联系统' % (count, systems)
        if unresolved: note += '；%d 条地址未解析' % unresolved
        if actual_warnings: note += '；部分配置分析不完整'
        return [label, 'PARTIAL' if actual_warnings else 'COMPLETE', note]
    if label == 'Dubbo' and dubbo and state != 'FAILED':
        roles = {r.get('role') for r in dubbo.get('rows', []) if r.get('role') in ('PROVIDER', 'CONSUMER')}
        declared = {r.get('role') for r in (config or {}).get('dubboEvidence', []) if r.get('role') in ('PROVIDER', 'CONSUMER')}
        labels = {'PROVIDER':'Dubbo Provider', 'CONSUMER':'Dubbo Consumer'}
        selected = roles or declared
        note = '、'.join(labels[r] for r in ('PROVIDER', 'CONSUMER') if r in selected) if selected else '未识别 Dubbo 角色'
        if declared and not roles: note += '（配置声明，运行时未核实）'
        failures = [w for w in dubbo.get('warnings', []) if '查询失败' in w or '未支持' in w or '截断' in w or '读取失败' in w]
        if failures: note += '；注册中心检查不完整'
        return [label, 'PARTIAL' if failures else 'COMPLETE', note]
    if label.startswith('TCP '):
        report = reports.get(label[4:])
        if not report or report.get('status') not in ('complete', 'partial'): return row
        counts, missing = endpoint_counts(report)
        note = '识别上游 %d 个，下游 %d 个' % (counts['upstream'], counts['downstream'])
        warnings = report.get('warnings') or []
        if any('超过 100' in str(w) or '超过100' in str(w) for w in warnings): note += '（采样达到上限，结果不完整）'
        elif report.get('status') == 'partial': note += '（部分信息未确认）'
        if missing: note += '；%d 条连接缺少 IP 或端口' % missing
        return [label, 'COMPLETE', note]
    return row


def main():
    parser = argparse.ArgumentParser(description='缩容巡检（开发中，未完成的检查会标记 UNKNOWN）')
    parser.add_argument('--app-id', default='1462')
    parser.add_argument('--environment', choices=['PRODUCT', 'TEST', 'SIMULATION'], default='PRODUCT')
    parser.add_argument('--output', type=Path, required=True, help='结果目录')
    parser.add_argument('--tcp-reports', type=Path, help='复用指定目录中已完成的 300 秒检测报告；不指定时执行实时检测')
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    status = [['检查', '状态', '说明']]
    sheets = {}
    log('开始读取应用部署、Nginx 和配置快照')
    try:
        with (HERE / 'snapshot_scan.py').open('rb') as script:
            result = subprocess.run(['docker', '--context', 'lenovo', 'exec', '-i', 'envscope-local', 'python3', '-', args.app_id, args.environment], stdin=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        snap = json.loads(result.stdout)
        log('快照 %s；端点 %d；Nginx 路由 %d；配置引用 %d' % (snap['version'], len(snap['endpoints']), len(snap['nginx']), len(snap['references'])))
        sheets['Nginx'] = [['服务器IP', '配置文件', 'URI', '域名', '目标', '后端', '命中端点', 'location', '片段置信度', '采集时间', '来源', '证据']] + [[r.get(k, '') for k in ('serverIp', 'file', 'uri', 'domains', 'target', 'backends', 'matchedEndpoints', 'snippet', 'snippetStatus', 'checkedAt', 'source', 'evidence')] for r in snap['nginx']]
        sheets['配置引用'] = [['应用ID', '应用名', '配置文件', '行号', '配置项', '命中端点', '配置分组']] + [[r.get(k, '') for k in ('appId', 'appName', 'file', 'line', 'content', 'matchedEndpoints', 'group')] for r in snap['references']]
        nginx_state = 'UNKNOWN' if not snap['nginxFilesScanned'] else 'PARTIAL' if snap['nginxErrors'] else 'COMPLETE'
        status.append(['Nginx', nginx_state, '扫描已下载配置 %d 个；读取错误 %d；解析提示 %d 条；主机名和变量后端未解析；%s' % (snap['nginxFilesScanned'], len(snap['nginxErrors']), len(snap['nginxWarnings']), '; '.join((snap['nginxErrors']+snap['nginxWarnings'])[:5]))])
        status.append(['配置引用', 'PARTIAL' if snap['configErrors'] else 'COMPLETE', '按实际已下载文件扫描 %d 个；读取错误 %d；%s' % (snap['configFilesScanned'], len(snap['configErrors']), '; '.join(snap['configErrors'][:10]))])
        for warning in snap['warnings']:
            status.append(['部署端点', 'UNKNOWN', warning])
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        log('快照扫描失败：' + type(exc).__name__)
        status.extend([['Nginx', 'FAILED', type(exc).__name__], ['配置引用', 'FAILED', type(exc).__name__]])
    log('开始 Kong Admin 查询')
    if args.app_id == '1462' and args.environment == 'PRODUCT':
        try:
            result = subprocess.run(['sh', str(HERE / 'kong_targets.sh')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180)
            kong = json.loads(result.stdout)
            sheets['Kong'] = kong_rows(kong)
            unresolved = [r for r in kong['matches'] if not r.get('services') or not any(s.get('routes') for s in r.get('services', []))]
            status.append(['Kong', 'PARTIAL' if unresolved else 'COMPLETE', '已查询 Service、Route、Target；匹配 %d 条，关联不完整 %d 条；未核实健康状态' % (len(kong['matches']),len(unresolved))])
            log('Kong 命中 %d 条 target' % len(kong['matches']))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            status.append(['Kong', 'FAILED', type(exc).__name__])
            log('Kong 查询失败：' + type(exc).__name__)
    else:
        status.append(['Kong', 'UNKNOWN', '此环境的 Admin 地址和查询范围尚未配置'])
    log('开始 DNS 记录检查')
    if 'snap' in locals():
        try:
            names = [name for row in snap['nginx'] if row.get('evidence') == '解析路由' for name in row.get('domains',[])]
            if 'kong' in locals():
                names.extend(host for row in kong['matches'] for service in row.get('services',[]) for route in service.get('routes',[]) for host in (route.get('hosts') or []))
            query = base64.b64encode(json.dumps({'names':names,'ips':[e[0] for e in snap['endpoints']]},ensure_ascii=False).encode()).decode()
            with (HERE/'dns_scan.py').open('rb') as script:
                p = subprocess.run(['docker','--context','lenovo','exec','-i','envscope-local','python3','-',snap['version'],query],stdin=script,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
            dns = json.loads(p.stdout)
            sheets['DNS'] = [['域名','解析方式','解析记录','记录状态','线路','来源','租户','公网/内网','观察时间','匹配依据']] + [[r.get(k,'') for k in ('name','type','value','status','line','source','tenant','visibility','observedAt','reason')] for r in dns['records']]
            name_note = '入口域名 %d 个' % len(set(names)) if names else '未发现明确入口域名，仅按服务器 IP 查询'
            status.append(['DNS','PARTIAL' if dns['warnings'] else 'COMPLETE','来源：%s；%s；匹配 %d 条；%s' % (','.join(dns['sources']),name_note,len(dns['records']),'; '.join(dns['warnings']))])
            log('DNS 匹配 %d 条记录' % len(dns['records']))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            status.append(['DNS','FAILED',type(exc).__name__]); log('DNS 检查失败：'+type(exc).__name__)
    else: status.append(['DNS','UNKNOWN','应用端点不可用'])
    processes_sheet = [['服务器IP','PID','启动时间','运行天数','JVM 内存参数（Xms / Xmx）','RSS (MiB)','RSS / 启动参数 Xmx (%)','命令详情（脱敏）','工作目录','工作目录状态','应用目录候选及实际存在状态','监听端口']]
    processes_by_ip = {}
    files_sheet = [['服务器IP','归档/日志文件','总行数','识别访问日志行数','一周内行数','错误']]
    apis_sheet = [['服务器IP','方法','URI（不含查询参数）','状态码','访问次数','访问方 IP 集合']]
    hours_sheet = [['服务器IP','时间区间（小时）','访问次数']]
    errors_sheet = [['服务器IP','时间区间（小时）','日志级别','条数']]
    if 'snap' in locals():
        for ip in sorted({e[0] for e in snap['endpoints']}):
            log('开始通过 JumpServer 检查 '+ip)
            try:
                processes, archives, issues, observed = collect_host(ip, snap['appName'], snap['version'], args.output/'raw_logs')
                processes_by_ip[ip] = processes
                for p in processes:
                    processes_sheet.append([ip]+[p.get(k,'') for k in ('pid','startedAt','days','jvmArgs','rssMiB','rssXmxPercent','command','cwd','cwdStatus','directories','listeners')])
                if not processes: issues.append('未发现可确认的应用进程')
                if not archives: issues.append('未采集到日志归档')
                status.append(['当前进程 '+ip, 'COMPLETE' if processes else 'UNKNOWN', '按应用名参数或 jar 名匹配；共 %d 个；目录存在性已实查，业务归属仍为推断。'%len(processes)])
                analyzed = 0; analysis_errors = []; no_access = True
                for archive in archives:
                    log('%s：本地分析 %s' % (ip, archive.name))
                    end = dt.datetime.fromisoformat(observed) if observed else dt.datetime.now().astimezone(); result = analyze(archive, end-dt.timedelta(days=7), end)
                    for f in result['files']: files_sheet.append([ip]+[f.get(k,'') for k in ('file','lines','parsed','inWindow','error')])
                    for a in result['apis']: apis_sheet.append([ip]+[a[k] for k in ('method','uri','status','count','clients')])
                    for h in result['hours']: hours_sheet.append([ip,h['hour'],h['count']])
                    for e in result['errors']: errors_sheet.append([ip,e['hour'],e['level'],e['count']])
                    analyzed += len(result['files'])
                    if any(f['parsed'] for f in result['files']): no_access = False
                    analysis_errors.extend(f['error'] for f in result['files'] if f['error'])
                    analysis_errors.extend(w for w in result['warnings'] if not w.startswith('未识别标准 Apache/Nginx access'))
                    if result['hours']:
                        peak = max(h['count'] for h in result['hours'])
                        status.append(['API峰值 '+ip, 'OBSERVED', '每小时最高 %d 次：%s' % (peak, ', '.join(h['hour'] for h in result['hours'] if h['count'] == peak))])
                    issues.extend(result['warnings']); issues.extend(f['error'] for f in result['files'] if f['error'])
                log_state, note = log_status(archives, analyzed, analysis_errors, no_access, issues)
                status.append(['应用日志 '+ip, log_state, note])
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                status.extend([['当前进程 '+ip,'FAILED',type(exc).__name__],['应用日志 '+ip,'FAILED',type(exc).__name__]])
                log(ip+' 实时采集失败：'+type(exc).__name__)
    sheets.update({'当前进程':processes_sheet, '日志文件':files_sheet, 'API访问':apis_sheet, '访问时段':hours_sheet, '异常时段':errors_sheet})
    if 'snap' in locals():
        log('开始在容器内分析当前环境的已保存应用配置并导出脱敏摘要')
        try:
            config = collect_config(args.app_id, args.environment, processes_by_ip, args.output/'app_configs')
            sheets['关联组件'] = [['组件类型','主机/IP','端口','协议','配置文件','配置键','行号','来源','端点声明置信度','证据及范围']] + [[c.get(k,'') for k in ('kind','host','port','protocol','file','key','line','source','confidence','scope')] for c in config['components']]
            urls = unlisted_urls(config['urls'], config['components'])
            sheets['引用URL'] = [['URL（不含认证信息及查询参数）','配置文件','行号','来源','证据及范围']] + [[u.get(k,'') for k in ('url','file','line','source','scope')] for u in urls]
            state='UNKNOWN' if not config['files'] else 'PARTIAL' if config['warnings'] else 'COMPLETE'
            status.append(['应用配置与关联组件',state,'配置快照 %s；分析 %d 个文件，导出脱敏摘要；识别 %d 个端点，引用URL %d 条（已排除组件重复 %d 条）；生效状态未核实；%s' % (config['snapshotVersion'],len(config['files']),len(config['components']),len(urls),len(config['urls'])-len(urls),'; '.join(config['warnings']))])
            log('已分析 %d 个配置文件，识别 %d 个端点和 %d 个非重复 URL（仅导出脱敏摘要）' % (len(config['files']),len(config['components']),len(urls)))
            log('开始 Dubbo 注册关系检查')
            dubbo=inspect_dubbo(args.app_id,args.environment,snap,config,processes_by_ip)
            keys=('registry','interface','role','providerName','providerIp','providerPort','providerAppId','providerAppName','providerConfidence','consumerName','consumerIp','consumerPort','consumerAppId','consumerAppName','consumerConfidence')
            sheets['Dubbo服务'] = [['Dubbo Register','服务接口','当前应用角色','Provider Name','Provider IP','Provider 端口','Provider 应用ID','Provider 应用名称','Provider 归属置信度','Consumer Name','Consumer IP','Consumer 端口','Consumer 应用ID','Consumer 应用名称','Consumer 归属置信度']] + [[r.get(k,'') for k in keys] for r in dubbo['rows'] if r.get('role') in ('PROVIDER','CONSUMER')]
            status.append(['Dubbo',dubbo['status'],'匹配 %d 条注册关系；%s' % (len(dubbo['rows']),'; '.join(dubbo['warnings']))])
            log('Dubbo：'+dubbo['status']+'，匹配 %d 条' % len(dubbo['rows']))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            status.extend([['应用配置与关联组件','FAILED',type(exc).__name__],['Dubbo','UNKNOWN','配置分析未完成']]);log('配置分析失败：'+type(exc).__name__)
        log('开始逐台进行 300 秒 TCP 连接检测' if not args.tcp_reports else '读取指定的 300 秒 TCP 检测报告')
        tcp_errors=[]
        if args.tcp_reports:
            tcp_paths = sorted(args.tcp_reports.glob('tcp-*.json'))
        else:
            tcp_paths,tcp_errors=collect_tcp(args.app_id,args.environment,args.output/'tcp_reports',log)
        by_ip={}
        for path in tcp_paths:
            try:
                report=json.loads(path.read_text(encoding='utf-8')); ip=(report.get('target') or {}).get('serverIp')
                if not ip or report.get('environment')!=args.environment or report.get('sourceVersion')!=snap['version'] or report.get('coverage',{}).get('requestedSeconds')!=300:
                    tcp_errors.append((str(ip or path.name),'报告版本、环境或观察时长不匹配'));continue
                by_ip[ip]=report
            except (OSError,ValueError,TypeError):tcp_errors.append((path.name,'读取报告失败'))
        for ip in sorted({e[0] for e in snap['endpoints']}):
            report=by_ip.get(ip)
            if not report:
                detail='；'.join(message for host,message in tcp_errors if host in ('',ip)) or '没有该服务器的 300 秒检测报告'
                status.append(['TCP '+ip,'UNKNOWN',detail]);sheets['TCP_'+ip]=[['状态','说明'],['UNKNOWN',detail]];continue
            sheets['TCP_'+ip]=tcp_display_rows(report)
            coverage=report.get('coverage',{});st=str(report.get('status','UNKNOWN')).upper()
            status.append(['TCP '+ip,st,'请求 300 秒，实际有效跨度 %s 秒；%s' % (coverage.get('actualSeconds','UNKNOWN'),'；'.join(report.get('warnings',[])[:4]))])
    else:
        status.extend([['应用配置与关联组件','UNKNOWN','应用端点不可用'],['Dubbo','UNKNOWN','应用端点不可用'],['TCP','UNKNOWN','应用端点不可用']])
    status.append(['目标服务器资源', 'UNKNOWN', '尚未实现，不能据此判断缩容条件'])
    sheets['检查详情'] = [list(row) for row in status]
    snap_result, kong_result, dns_result = locals().get('snap'), locals().get('kong'), locals().get('dns')
    status = [status[0]] + [short_status(row, snap_result, kong_result, dns_result, processes_by_ip) for row in status[1:]]
    config_result, dubbo_result, tcp_results = locals().get('config'), locals().get('dubbo'), locals().get('by_ip', {})
    status = [status[0]] + [requested_status(row, config_result, dubbo_result, tcp_results) for row in status[1:]]
    sheets = {'检查状态': status, **sheets}
    path = args.output / '巡检结果.xlsx'
    workbook(path, sheets)
    log('已生成未完成的报告：' + str(path))
    return 1


if __name__ == '__main__':
    sys.exit(main())
