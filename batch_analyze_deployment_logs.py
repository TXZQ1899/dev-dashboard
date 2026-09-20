#!/usr/bin/env python3
"""Offline batch reports from exports/devops-details-*/: Python standard library only.

python3 batch_analyze_deployment_logs.py --input exports --output outputs
"""
import argparse
import collections
import datetime as dt
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from analyze_deployment_logs import prop_entries, nested_params, normalize_slashes

IP = re.compile(r'(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?(?![\d.])')
URL = re.compile(r'https?://[^\s<>"\'`,}]+')
SECRET_KEY = re.compile(r'token|password|passwd|secret|credential|access.?key', re.I)
AUTH = re.compile(r'(?i)(-auth\s+)([^\s"\'`]+)')
RUNTIME_KEYS = ['_vmIP','_vmPort','http1.1_port','dubbo_protocol_port','server_xml_server_port',
 'server_xml_path','server_xml_docBase','server_xml_displayName','targetTomcatFolderName',
 'defaultTomcatFolderName','SSLPort','SSLEnabled','xmx','theoneJVMProp','checkVersion',
 'if_clean_log_when_start','activeEnv','kongEnv','push_in_wait','apollo.config.server.info']
DISTRIBUTION_KEYS = ['pkg_repository_server','pkg_repository_server_ip','prd_rsync_server',
 'private_rsync_server','pub_rsync_server','targetdir','http_install_url','devopspkg_server_url',
 'PROP_NAME_FILE_NAME','PROP_NAME_REMOTE_FILE_PATH','install_package_path','version_code','sync_items']
BUILD_KEYS = ['dp_jobname','dp_jenkins_url','jenkins_build_server','dp_username','jenkins_job_thread']
NOTES = [
 '仅反映已导出的运行详情快照，不代表完整历史部署；缺失日志不能说明没有部署。',
 '同一记录及日志内容完全相同的重复导出合并；有变化的快照分别保留。',
 '端口仅提取明确配置；null 表示未指定，不补填协议默认端口。配置不等同实际监听。',
 'IP 清单区分当前目标、全局配置、模板、注释及实际输出；其他环境配置不等同当前依赖。',
 '同一配置存在多个不同值时用数组保留，不擅自选择。时间保持来源格式，未推断时区。',
 '模板命令不能证明被执行；JDK 配置标签保留原值，不据 JDK18 标签断言运行 Java 18。',
 '按敏感键和凭据模式脱敏；输出不含 evidence，不输出完整日志及完整全局参数表。',
 'Java Map 参数使用键边界解析，自由文本可能存在歧义；不执行日志中的命令。',
]


def unique(values):
    result=[]
    for v in values:
        if v not in result:result.append(v)
    return result


def collapsed(values):
    values=unique(values)
    return values[0] if len(values)==1 else values if values else None


def valid_ip(ip):
    try:ipaddress.IPv4Address(ip);return True
    except ValueError:return False


def scrubber(texts):
    secrets=set()
    for text in texts:
        for p in prop_entries(text):
            if SECRET_KEY.search(p['key']) and not p['key'].lower().endswith(('file','path')) and p['value'] not in ('','null','None','false','true'):
                secrets.add(p['value'])
        secrets.update(m[2] for m in AUTH.finditer(text))
    def scrub(text):
        for secret in sorted(secrets,key=len,reverse=True):text=text.replace(secret,'[REDACTED]')
        text=AUTH.sub(r'\1[REDACTED]',text)
        text=re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@',r'\1[REDACTED]@',text)
        text=re.sub(r'(?i)((?:password|passwd|token|secret)\s*[=:]\s*)[^\s,;"\']+',r'\1[REDACTED]',text)
        return text
    return scrub


def service(key):
    for pattern,name in [('kong','Kong 网关'),('apollo','Apollo'),('nacos','Nacos'),('jenkins','Jenkins'),
      ('MQAdmin','MQ 管理接口'),('DUBBO_URL','Dubbo 治理接口'),('brokerList','Kafka'),('zookeeper','ZooKeeper'),
      ('log_sink','日志接收端'),('rsync|pkg_repository','发布包仓库'),('svn','SVN'),('install_url|devopspkg','安装包下载')]:
        if re.search(pattern,key,re.I):return name
    return key


class Networks:
    def __init__(self):self.rows={}
    def add(self,host,port,name,config,scope,note=None):
        if not host:return
        is_ip=valid_ip(host)
        if port:
            try:port=int(port)
            except (ValueError,TypeError):return
            if not 1<=port<=65535:return
        else:port=None
        key=(host,port)
        row=self.rows.setdefault(key,dict(ip=host if is_ip else None,domain=None if is_ip else host,port=port,
                                              names=[],configuration_names=[],scopes=[],notes=[]))
        for field,value in [('names',name),('configuration_names',config),('scopes',scope),('notes',note)]:
            if value and value not in row[field]:row[field].append(value)
    def scan(self,text,name,config,scope):
        text=normalize_slashes(text)
        for match in IP.finditer(text):
            if valid_ip(match[1]):self.add(match[1],match[2],name,config,scope)
        for match in URL.finditer(text):
            try:
                parsed=urlsplit(match[0])
                if parsed.hostname and not valid_ip(parsed.hostname):self.add(parsed.hostname,parsed.port,name,config,scope)
            except ValueError:pass
    def list(self):return sorted(self.rows.values(),key=lambda x:(x['ip'] or x['domain'],x['port'] or 0))


def analyze_deployment(index_path,app_name):
    index=json.loads(index_path.read_text())
    logs=[];issues=[]
    for step in index.get('steps',[]):
        filename=step.get('log_file')
        if not filename:
            issues.append(f"{step.get('operation','未知操作')}：未记录日志文件")
            logs.append((step,None,''));continue
        path=(index_path.parent/filename).resolve()
        if not path.is_relative_to(index_path.parent.resolve()):raise ValueError('日志路径超出部署目录')
        if not path.exists():
            issues.append(f"{step.get('operation','未知操作')}：日志文件不存在")
            logs.append((step,None,''));continue
        logs.append((step,path,path.read_text(encoding='utf-8-sig')))
    scrub=scrubber([text for _,_,text in logs])
    props=[];steps=[];net=Networks();findings=[];jdks=[];targets=[]
    for step,path,raw in logs:
        text=scrub(raw)
        entries=prop_entries(text)
        params={p['key']:p['value'] for p in entries}
        props.append(params)
        if raw and not entries:issues.append(f"{step.get('operation')}：非空日志未解析出参数表")
        if not raw:issues.append(f"{step.get('operation')}：日志为空或未获取")
        steps.append({**{k:step.get(k) for k in ['operation','start_time','end_time','status','build_id','resource_id','action_id']},
                      'log_available':path is not None,'log_empty':not bool(raw),'parameter_count':len(entries)})
        ip=params.get('_vmIP') or index.get('deployment',{}).get('ip')
        if ip and valid_ip(ip):net.add(ip,None,app_name,'_vmIP','当前部署目标')
        for key,name in [('_vmPort','SSH'),('http1.1_port','HTTP'),('dubbo_protocol_port','Dubbo'),('server_xml_server_port','Tomcat 控制端口'),('SSLPort','SSL')]:
            if ip and key in params:
                net.add(ip,params[key],app_name+' / '+name,key,'当前部署目标端口配置',
                        'SSL 配置为关闭' if key=='SSLPort' and params.get('SSLEnabled')=='false' else None)
        for key,value in params.items():
            if key.startswith('SCRIPT_BLOCK') or key in ('dp_params','_vmIP'):continue
            if key=='app_ng_mapping':
                try:
                    mapping=json.loads(value)
                    for name,addresses in mapping.items():net.scan(str(addresses),name,key,'全局应用映射')
                except (ValueError,AttributeError):net.scan(value,'应用映射',key,'全局应用映射（未解析）')
            else:net.scan(value,service(key),key,'声明配置（包含全局及其他环境）')
        scope='header';section=None
        decoder=json.JSONDecoder()
        for line in text.splitlines():
            match=re.search(r'>>>>>> start to executing script:(.*?) >>>>>>',line)
            if match:section=match[1];scope='section';continue
            if '---------------SCRIPT' in line:scope='script';continue
            if '---------------RESULT' in line:scope='output';continue
            if 'script finished.' in line:scope='footer';continue
            if scope not in ('script','output'):continue
            stripped=line.lstrip()
            commented=scope=='script' and (stripped.startswith('//') or (stripped.startswith('#') and not stripped.startswith('#{')))
            context='注释引用' if commented else '实际输出' if scope=='output' else '脚本模板（未确认执行）'
            net.scan(line,None,None,context)
            vm=re.search(r'VM:\[name:(.*?), ip:([\d.]+), port:(\d+)',line)
            if vm:net.add(vm[2],vm[3],vm[1],None,'脚本头 VM（脚本可另选执行主机）')
            if scope=='script' and not commented:
                java=re.search(r'JAVA_HOME=([^\s;]+)',line)
                if java:jdks.append(java[1])
            if scope!='output':continue
            for pattern,title in [(r'curl:','curl 错误'),(r'UNIQUE violation|unique constraint violation','Kong 唯一性冲突'),
                                  (r'^\s*Usage:','命令用法提示'),(r'#\{[^}]+\}','输出含未替换占位符'),
                                  (r'(?i)permission denied|connection refused|no such file|command not found|build failure|build failed|exception|\berror\b','输出包含错误关键词（需人工复核）')]:
                if re.search(pattern,line):findings.append(dict(operation=step.get('operation'),type=title))
            pos=0
            while (pos:=line.find('{',pos))>=0:
                try:obj,n=decoder.raw_decode(line[pos:]);pos+=n
                except ValueError:pos+=1;continue
                if isinstance(obj,dict) and isinstance(obj.get('target'),str):
                    targets.append({k:obj.get(k) for k in ['target','weight','id','upstream']})
                    net.scan(obj['target'],'Kong target',None,'实际接口返回')
        if step.get('status') not in ('SUCCESS',None):findings.append(dict(operation=step.get('operation'),type='平台操作状态：'+str(step.get('status'))))
        jvm=re.search(r'-Xmx(\S+)',params.get('theoneJVMProp',''))
        if jvm and params.get('xmx') and jvm[1].lower()!=params['xmx'].lower():
            findings.append(dict(operation=step.get('operation'),type='JVM 内存声明不同：xmx='+params['xmx']+'，-Xmx'+jvm[1]+'；实际生效值待确认'))
        apollo=normalize_slashes(params.get('apollo.config.server.info',''))
        expected=params.get('apollo.meta_'+params.get('activeEnv',''))
        if apollo and expected and normalize_slashes(expected) not in apollo:
            findings.append(dict(operation=step.get('operation'),type='Apollo 通用配置与当前环境 meta 地址不一致；是否使用需确认'))
    def fields(keys):return {key:collapsed([p[key] for p in props if key in p]) for key in keys if any(key in p for p in props)}
    builds=unique([nested_params(p['dp_params']) for p in props if p.get('dp_params')])
    missing_code=not any(b.get('res_url') for b in builds)
    deploy=index.get('deployment',{})
    result=dict(deploy_id=deploy.get('deploy_id'),scene_id=deploy.get('scene_id'),server_ip=deploy.get('ip'),
        log_status=deploy.get('log_status'),operations=steps,
        latest_observed_deploy_start=max([s['start_time'] for s in steps if s['operation']=='Deploy' and s.get('start_time')],default=None),
        code_and_build=dict(jenkins=fields(BUILD_KEYS),build_parameters=builds,
                            source_code_in_log='未解析到仓库参数' if missing_code else '已解析',
                            commit_note='code_version 为配置值；未将其验证为实际提交 SHA'),
        runtime_configuration=fields(RUNTIME_KEYS),distribution_configuration=fields(DISTRIBUTION_KEYS),
        java_home_template_candidates=unique(jdks),network_endpoints=net.list(),gateway_targets=unique(targets),
        review_findings=unique(findings),data_issues=unique(issues+[scrub(str(e.get('error',e))) for e in index.get('errors',[]) if isinstance(e,dict)]))
    # Identity includes bytes, status, build metadata and source-export errors, but ignores export capture time.
    signature=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode())
    for step,path,text in logs:signature.update(hashlib.sha256(text.encode()).digest())
    return result,signature.hexdigest()


def cell(value):
    if value is None:return '未明确'
    if not isinstance(value,str):value=json.dumps(value,ensure_ascii=False)
    return value.replace('|','&#124;').replace('\n','<br>')


def table(rows):
    return ['|配置项|值|','|---|---|']+[f'|{cell(k)}|{cell(v)}|' for k,v in rows.items()]


def report(app):
    lines=[f"# {app['application']} 部署日志报告",'',f"应用详情：{cell(app['application_info'].get('urls'))}",'',
           f"数据范围：{app['coverage']['deployment_snapshots']} 个不同运行快照；{app['coverage']['log_files']} 份已找到的步骤日志。",'',
           '## 应用基本信息','']+table(app['application_info'])
    for env in app['environments']:
        lines+=['',f"## {env['name']}",'',f"服务器：{', '.join(env['server_ips']) or '未记录'}",'',
                f"日志中最新 Deploy 开始时间：{env['latest_observed_deploy_start'] or '无法从已导出日志确认'}",'']
        if not env['deployments']:lines+=['没有可分析的运行详情快照；不能据此认定未部署。','']
        for i,dep in enumerate(env['deployments'],1):
            lines += [f"### {dep['server_ip'] or '未知服务器'} / 部署 {dep['deploy_id']} / 快照 {i}",'',
                      f"导出日志状态：{dep['log_status']}；合并来源记录数：{len(dep['source_exports'])}。",'',
                      '|操作|开始|结束|平台状态|日志|','|---|---|---|---|---|']
            for step in dep['operations']:
                lines.append('| '+' | '.join(cell(step[k]) for k in ['operation','start_time','end_time','status'])+' | '+('有内容' if step['log_available'] and not step['log_empty'] else '缺失或为空')+' |')
            lines+=['','#### 代码与构建','']+table(dep['code_and_build']['jenkins'])
            for build in dep['code_and_build']['build_parameters']:lines+=['']+table(build)
            if not dep['code_and_build']['build_parameters']:lines+=['','日志未提供构建参数；应用详情的仓库与 JDK 标签见应用基本信息。']
            lines+=['','#### 应用运行与分发配置','']+table({**dep['runtime_configuration'],**dep['distribution_configuration']})
            if dep['java_home_template_candidates']:lines+=['',f"JAVA_HOME 模板候选：{cell(dep['java_home_template_candidates'])}。未确认条件分支或实际运行版本。"]
            lines+=['','#### IP、端口和名称','', '|IP / 域名|端口|名称|配置项|范围及备注|','|---|---|---|---|---|']
            for row in dep['network_endpoints']:
                lines.append('| '+' | '.join(cell(v) for v in [row['ip'] or row['domain'],row['port'],'、'.join(row['names']) or None,
                         '、'.join(row['configuration_names']) or None,'；'.join(row['scopes']+row['notes'])])+' |')
            lines+=['','#### 数据缺失及需复核项','']
            lines+=['- '+cell(x) for x in dep['data_issues']]
            lines+=['- '+cell(f.get('operation'))+'：'+cell(f['type']) for f in dep['review_findings']]
            if not dep['data_issues'] and not dep['review_findings']:lines+=['未命中当前检查规则；不代表已验证应用健康。']
    if app['data_issues']:lines+=['','## 应用数据问题','']+['- '+cell(v) for v in app['data_issues']]
    lines+=['','## 解读说明','']+['- '+v for v in NOTES]
    return '\n'.join(lines)+'\n'


def safe_name(name):
    name=re.sub(r'[\\/\x00-\x1f:*?"<>|]','_',name).strip('. ')
    return name or 'unnamed'


def run(input_dir,output_dir,only_app=None):
    roots=[input_dir] if input_dir.name.startswith('devops-details-') else sorted(input_dir.glob('devops-details-*'))
    if not roots:raise ValueError('找不到 devops-details-* 导出目录')
    apps={};errors=[]
    for root in roots:
        for path in sorted(root.glob('*/summary.json')):
            try:summary=json.loads(path.read_text())
            except (ValueError,OSError) as exc:errors.append(dict(file=str(path),error=type(exc).__name__));continue
            name=summary.get('app_name') or path.parent.name
            if only_app and name!=only_app:continue
            apps.setdefault(name,[]).append((path,summary))
        # Orphan directories must not be silently lost if summary.json is missing.
        for index in root.glob('*/*/logs/*/index.json'):
            appdir=index.parents[3]
            if not (appdir/'summary.json').exists() and (not only_app or only_app==appdir.name):
                entry=(appdir/'summary.json',{'app_name':appdir.name,'environments':{}})
                if entry not in apps.setdefault(appdir.name,[]):apps[appdir.name].append(entry)
    output_dir.mkdir(parents=True,exist_ok=True)
    manifest=[];used_names={}
    for number,(name,sources) in enumerate(sorted(apps.items()),1):
        app=dict(schema_version='1.0',application=name,application_info={},environments=[],coverage={},data_issues=[],notes=NOTES)
        for dest,key in [('urls','url'),('app_ids','app_id'),('repository_types','repository_type'),('repository_urls','repository_url'),('jdk_labels','jdk_version')]:
            app['application_info'][dest]=collapsed([s[key] for _,s in sources if s.get(key) is not None])
        envs={};seen={};index_count=0;analyzed=0
        for summary_path,summary in sources:
            if not summary_path.exists():app['data_issues'].append('缺少 summary.json：'+str(summary_path))
            if summary.get('export_status') not in ('complete',None):app['data_issues'].append('应用导出状态：'+str(summary.get('export_status')))
            for env_key,info in summary.get('environments',{}).items():
                env_name=info.get('name') or env_key
                env=envs.setdefault(env_name,dict(name=env_name,server_ips=[],deployments=[]))
                env['server_ips']=unique(env['server_ips']+info.get('server_ips',[]))
            indexes=sorted(summary_path.parent.glob('*/logs/*/index.json'))
            for index_path in indexes:
                index_count+=1
                env_name=index_path.parents[2].name
                env=envs.setdefault(env_name,dict(name=env_name,server_ips=[],deployments=[]))
                try:dep,sig=analyze_deployment(index_path,name);analyzed+=1
                except Exception as exc:
                    app['data_issues'].append('解析失败：'+str(index_path)+'（'+type(exc).__name__+'）')
                    errors.append(dict(application=name,file=str(index_path),error=type(exc).__name__))
                    continue
                identity=(env_name,dep['deploy_id'],dep['server_ip'],sig)
                if identity in seen:seen[identity]['source_exports'].append(str(index_path));continue
                dep['source_exports']=[str(index_path)];seen[identity]=dep;env['deployments'].append(dep)
                if dep['server_ip'] and dep['server_ip'] not in env['server_ips']:env['server_ips'].append(dep['server_ip'])
            # Summary deployments whose index is absent are retained as missing, not marked successful.
            for env_key,info in summary.get('environments',{}).items():
                env=envs[info.get('name') or env_key]
                for d in info.get('deployments',[]):
                    if not any(x['deploy_id']==d.get('deploy_id') and x['server_ip']==d.get('ip') for x in env['deployments']):
                        env['deployments'].append(dict(deploy_id=d.get('deploy_id'),scene_id=d.get('scene_id'),server_ip=d.get('ip'),
                          log_status='missing_index',operations=[],latest_observed_deploy_start=None,code_and_build={'jenkins':{},'build_parameters':[]},
                          runtime_configuration={},distribution_configuration={},java_home_template_candidates=[],network_endpoints=[],gateway_targets=[],
                          review_findings=[],data_issues=['应用摘要中有部署记录，但未找到可分析的 index.json'],source_exports=[str(summary_path)]))
        for env in envs.values():
            env['latest_observed_deploy_start']=max([d['latest_observed_deploy_start'] for d in env['deployments'] if d['latest_observed_deploy_start']],default=None)
            env['deployments'].sort(key=lambda d:(str(d['server_ip']),str(d['deploy_id']),str(d['latest_observed_deploy_start'])))
        app['environments']=sorted(envs.values(),key=lambda e:({'测试环境':0,'仿真环境':1,'线上环境':2}.get(e['name'],3),e['name']))
        deps=[d for e in app['environments'] for d in e['deployments']]
        app['coverage']=dict(source_index_records=index_count,analyzed_index_records=analyzed,deployment_snapshots=len(deps),
                             merged_duplicate_indexes=analyzed-len(seen),log_files=sum(s['log_available'] for d in deps for s in d['operations']),
                             snapshots_with_log_content=sum(any(s['log_available'] and not s['log_empty'] for s in d['operations']) for d in deps),
                             snapshots_without_log_content=sum(not any(s['log_available'] and not s['log_empty'] for s in d['operations']) for d in deps))
        filename=safe_name(name)
        if filename.casefold() in used_names and used_names[filename.casefold()]!=name:filename+='_'+hashlib.sha256(name.encode()).hexdigest()[:8]
        used_names[filename.casefold()]=name
        # Final artifact scrub also covers application summary fields. No evidence nodes are emitted.
        clean=scrubber([])
        payload=clean(json.dumps(app,ensure_ascii=False,indent=2)+'\n')
        json.loads(payload)
        json_path=output_dir/(filename+'.json');md_path=output_dir/(filename+'.md')
        json_path.write_text(payload,encoding='utf-8');md_path.write_text(clean(report(app)),encoding='utf-8')
        manifest.append(dict(application=name,json_file=json_path.name,report_file=md_path.name,**app['coverage'],application_issue_count=len(app['data_issues'])))
        if number%25==0 or number==len(apps):print(f'已完成 {number}/{len(apps)} 个应用',flush=True)
    result=dict(generated_at=dt.datetime.now().astimezone().isoformat(),input=str(input_dir.resolve()),output=str(output_dir.resolve()),
                application_count=len(manifest),applications=manifest,errors=errors)
    (output_dir/'_analysis_index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 应用报告索引','','|应用|报告|精简 JSON|有内容的快照|无日志内容的快照|','|---|---|---|---:|---:|']
    for m in manifest:lines.append(f"|{cell(m['application'])}|[报告](<{m['report_file']}> )|[JSON](<{m['json_file']}> )|{m['snapshots_with_log_content']}|{m['snapshots_without_log_content']}|")
    (output_dir/'_analysis_index.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(applications=len(manifest),source_indexes=sum(m['source_index_records'] for m in manifest),errors=len(errors)),ensure_ascii=False))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('exports'))
    parser.add_argument('--output',type=Path,default=Path('outputs'))
    parser.add_argument('--app',help='Only analyze one exact application name')
    args=parser.parse_args()
    run(args.input.resolve(),args.output.resolve(),args.app)
