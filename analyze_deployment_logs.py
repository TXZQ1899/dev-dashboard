#!/usr/bin/env python3
"""Offline, read-only analysis of export_devops_details deployment logs. Python 3.9+."""
import argparse
import collections
import datetime as dt
import hashlib
import ipaddress
import json
import re
from pathlib import Path

PLACEHOLDER = re.compile(r'#\{([^}]+)\}')
AUTH = re.compile(r'(?i)(-auth\s+)([^\s"\'`]+)')
SENSITIVE = re.compile(r'(?i)(token|password|passwd|secret|credential|access.?key)')
IP = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')


def prop_entries(text):
    """Java Map.toString has no escaping; retain original text to audit this heuristic."""
    match = re.search(r'propNameValues:\s*\n-+\s*\n(.*?)\n-+\s*\n', text, re.S)
    if not match:
        return []
    block = match.group(1)
    keys = list(re.finditer(r'(?:^\{|, )([\w.:-]+)=', block))
    entries = []
    for i, key in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(block.rstrip()) - (1 if block.rstrip().endswith('}') else 0)
        start = match.start(1) + key.start(1)
        value_end = match.start(1) + end
        entries.append({'key': key.group(1), 'value': block[key.end():end],
                        'line': text.count('\n', 0, start) + 1,
                        'column': start - text.rfind('\n', 0, start),
                        'end_line': text.count('\n', 0, value_end) + 1})
    return entries


def category(key):
    if SENSITIVE.search(key) and not key.lower().endswith(('file', 'path')):
        return '敏感凭据'
    if key.startswith('SCRIPT_BLOCK'):
        return '脚本宏'
    if key.startswith('RESOURCE_ID_MAPPING'):
        return '全局资源映射'
    if re.search(r'black|blist|mapping|projects|frozen|approve', key, re.I):
        return '全局策略或应用映射'
    if re.search(r'kong', key, re.I):
        return '网关配置（含其他环境）'
    if re.search(r'apollo|nacos|MQAdmin|DUBBO_URL|zookeeper|brokerList|sink|flume|ch1_|log_', key):
        return '中间件或日志配置（不等同当前依赖）'
    if re.search(r'tomcat|server_xml|SSL|port|xmx|JVM|http1|checkVersion', key, re.I):
        return '应用运行配置'
    if re.search(r'jenkins|^dp_|version|sync|rsync|package|pkg_|svn', key, re.I):
        return '构建发布配置'
    return '其他参数'


def nested_params(value):
    keys = list(re.finditer(r'(?:^|\s)-p\s+([\w.-]+)=', value))
    return {m.group(1): value[m.end():keys[i + 1].start() if i + 1 < len(keys) else len(value)].strip()
            for i, m in enumerate(keys)}


def expand(template, params):
    result = template
    for _ in range(8):
        new = PLACEHOLDER.sub(lambda m: params.get(m.group(1), m.group()), result)
        if new == result:
            break
        result = new
    return result


def normalize_slashes(value):
    return re.sub(r'\\+/', '/', value)


def flatten(value, pointer=''):
    if isinstance(value, dict) and value:
        for k, v in value.items():
            yield from flatten(v, pointer + '/' + str(k).replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            yield from flatten(v, pointer + '/' + str(i))
    else:
        yield {'json_pointer': pointer, 'value': value}


def analyze(source, out):
    index = json.loads((source / 'index.json').read_text())
    inputs = []
    for step in index['steps']:
        if not step.get('log_file'):
            continue
        path = (source / step['log_file']).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Log path escapes input directory')
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        inputs.append((step, path, raw, text, prop_entries(text)))
    if not inputs:
        raise ValueError('No logs found in index.json')
    secrets = set()
    for _, _, _, text, props in inputs:
        for p in props:
            if SENSITIVE.search(p['key']) and not p['key'].lower().endswith(('file', 'path')) and p['value']:
                secrets.add(p['value'])
        secrets.update(m.group(2) for m in AUTH.finditer(text))

    def redact(text):
        for value in sorted(secrets, key=len, reverse=True):
            text = text.replace(value, '[REDACTED]')
        return AUTH.sub(r'\1[REDACTED]', text)

    def evidence(operation, file, line, **extra):
        return dict(operation=operation, file=file, line=line, **extra)

    operations, all_props, commands, objects, output_kv, findings = [], [], [], [], [], []
    entities = collections.defaultdict(list)
    files = collections.defaultdict(list)
    decoder = json.JSONDecoder()
    for step, path, raw, original, _ in inputs:
        text = redact(original)
        lines = text.splitlines()
        file = str(path.relative_to(source))
        op = step['operation']
        props = prop_entries(text)
        params = {p['key']: p['value'] for p in props}
        for p in props:
            all_props.append(dict(key=p['key'], value=p['value'], category=category(p['key']),
                                  redacted='[REDACTED]' in p['value'],
                                  evidence=evidence(op, file, p['line'], end_line=p['end_line'], column=p['column'])))
        sections, section, scope = [], None, 'header'
        line_records = []
        for n, line in enumerate(lines, 1):
            start = re.search(r'>>>>>> start to executing script:(.*?) >>>>>>', line)
            if start:
                if section:
                    section['end_line'] = n - 1
                section = {'name': start.group(1), 'start_line': n, 'script_lines': [], 'result_lines': []}
                sections.append(section)
                scope = 'section_header'
            elif '---------------SCRIPT' in line:
                scope = 'script_template'
            elif '---------------RESULT' in line:
                scope = 'observed_output'
            elif 'script finished.' in line:
                scope = 'section_footer'
            elif n >= 3 and section is None:
                scope = 'parameter_block'
            stripped = line.lstrip()
            is_comment = stripped.startswith('//') or (stripped.startswith('#') and not stripped.startswith('#{'))
            current_scope = 'commented_template' if scope == 'script_template' and is_comment else scope
            ev = evidence(op, file, n, scope=current_scope)
            line_records.append(dict(line=n, text=line, scope=current_scope))
            if section and scope == 'script_template':
                section['script_lines'].append(n)
                vm = re.search(r'VM:\[name:(.*?), ip:([\d.]+), port:(\d+), (.*)\]', line)
                if vm:
                    section['declared_vm'] = dict(name=vm[1], ip=vm[2], ssh_port=int(vm[3]), other=vm[4],
                                                   note='脚本头目标；Groovy 可另行选择执行主机')
                if not line.startswith(('VM:', '---------------')) and re.search(
                    r'\b(curl|rsync|java |export |mkdir |chmod |sed |rm |cp |echo |wget |kill|shellExec\.doExecute|String command\s*=)|startup\.sh', line):
                    resolved = expand(line, params)
                    commands.append(dict(section=section['name'], language='Groovy expression' if 'shellExec' in line or 'String command' in line else 'shell/template fragment',
                                         template=line, parameter_expanded=resolved,
                                         unresolved_parameters=sorted(set(PLACEHOLDER.findall(resolved))),
                                         execution_confirmed=False, evidence=ev,
                                         note='静态模板/表达式片段；仅替换已声明参数，未执行命令，未计算条件、Groovy 表达式或 Shell 变量'))
            if section and scope == 'observed_output' and not line.startswith('---------------'):
                section['result_lines'].append(n)
                pos = 0
                while (pos := line.find('{', pos)) >= 0:
                    try:
                        obj, consumed = decoder.raw_decode(line[pos:])
                    except ValueError:
                        pos += 1
                        continue
                    objects.append(dict(value=obj, fields=list(flatten(obj)), evidence=dict(ev, column=pos + 1)))
                    pos += consumed
                kv = re.fullmatch(r'([\w.-]+)=(.*)', line.strip())
                if kv:
                    output_kv.append(dict(key=kv[1], value=kv[2], evidence=ev))
                fm = re.fullmatch(r'(deleting )?([\w.-]+/(?:[^\s]+))', line.strip())
                if fm and 'rsync ' in '\n'.join(lines[j - 1] for j in section['script_lines']) and not line.startswith(('http', '//')):
                    files[(fm[2], 'delete_reported' if fm[1] else 'listed_by_output')].append(ev)
                for pattern, kind, detail in [
                    (r'curl: no URL specified', 'curl 参数错误', '实际输出报错，平台 SUCCESS 不能说明所有子命令成功。'),
                    (r'UNIQUE violation|unique constraint violation', 'Kong 唯一性冲突', '服务/上游创建返回冲突；可能已有对象，需结合后续结果判断。'),
                    (r'^\s*Usage:', '命令用法提示', '出现用法提示；Stop 中可能是未匹配到 PID，日志未证明停止结果。'),
                    (r'#\{[^}]+\}', '输出含未替换占位符', '输出仍有模板参数，需核对变量名和使用场景。')]:
                    if re.search(pattern, line):
                        findings.append(dict(kind=kind, severity='需复核', detail=detail, evidence=ev))
            for candidate in IP.findall(line):
                try:
                    ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                entities[('ipv4', candidate)].append(ev)
            normalized = normalize_slashes(line)
            for pattern, kind in [(r'https?://[^\s<>"\'`,}]+', 'url'),
                                  (r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', 'email'),
                                  (r'(?<![\w:/])/(?:[\w.@{}#*-]+/)*[\w.@{}#*-]+', 'path_candidate')]:
                for value in re.findall(pattern, normalized):
                    entities[(kind, value)].append(ev)
        if section:
            section['end_line'] = len(lines)
        for s in sections:
            s['script_text'] = '\n'.join(lines[n - 1] for n in s['script_lines'])
            s['result_text'] = '\n'.join(lines[n - 1] for n in s['result_lines'])
            s['execution_host_selection_expressions'] = re.findall(r'[^\n]*findByIp[^\n]*', s['script_text'])
        metadata = json.loads(redact(json.dumps(step, ensure_ascii=False)))
        try:
            duration = (dt.datetime.fromisoformat(step['end_time']) - dt.datetime.fromisoformat(step['start_time'])).total_seconds()
        except (ValueError, KeyError):
            duration = None
        operations.append(dict(operation=op, metadata=metadata, duration_seconds=duration, source_file=file,
                               source_sha256=hashlib.sha256(raw).hexdigest(), original_line_count=len(original.splitlines()),
                               parameters=props, sections=sections, lines=line_records))

    properties = {}
    for p in all_props:
        k = p['key']
        record = properties.setdefault(k, dict(category=p['category'], variants=[]))
        variant = next((v for v in record['variants'] if v['value'] == p['value']), None)
        if variant is None:
            variant = dict(value=p['value'], redacted=p['redacted'], evidence=[])
            record['variants'].append(variant)
        variant['evidence'].append(p['evidence'])
    params = {p['key']: p['value'] for p in operations[0]['parameters']}
    build = nested_params(params.get('dp_params', ''))

    def fact(key):
        return dict(value=params.get(key), evidence=properties.get(key, {}).get('variants', [{}])[0].get('evidence', []),
                    basis='declared_parameter', has_conflicting_values=len(properties.get(key, {}).get('variants', [])) > 1)

    semantic = {
        'application': {k: fact(k) for k in ['cur_app_name', 'activeEnv', '_vmIP', '_vmPort', '_buildId', '_sceneResourceId']},
        'source_and_build': {k: fact(k) for k in ['dp_jobname', 'dp_jenkins_url', 'jenkins_build_server', 'dp_username', 'version_code', 'sync_items']},
        'build_parameters': {'values': build, 'evidence': fact('dp_params')['evidence'], 'parser': '-p key=value 边界；保留空值和含空格的 Maven 参数'},
        'runtime': {k: fact(k) for k in ['http1.1_port', 'dubbo_protocol_port', 'server_xml_server_port', 'server_xml_path', 'server_xml_docBase', 'targetTomcatFolderName', 'defaultTomcatFolderName', 'SSLPort', 'SSLEnabled', 'xmx', 'theoneJVMProp', 'checkVersion', 'if_clean_log_when_start']},
        'distribution': {k: fact(k) for k in ['pkg_repository_server', 'pkg_repository_server_ip', 'prd_rsync_server', 'private_rsync_server', 'targetdir']},
        'gateways_and_configuration_services': {k: fact(k) for k in params if re.search(r'kong|apollo|nacos', k, re.I)},
        'java_system_properties': [dict(parameter=k, flags=[dict(key=m[1], value=m[2]) for m in re.finditer(r'-D([^=\s]+)=([^\s]+)', v)], evidence=fact(k)['evidence'])
                                   for k, v in params.items() if '-D' in v],
        'commit_sha': {'value': None, 'basis': '未观察到明确提交 SHA；code_version=null 不能确定实际提交'},
        'jdk': {'value': 'JDK 8 分支（推断）' if params.get('dp_jobname', '').endswith('18') else '需检查启动模板',
                'basis': 'Start 模板按作业名后缀选择 JAVA_HOME；没有 java -version 输出，不能验证实际运行版本',
                'evidence': [c['evidence'] for c in commands if 'JAVA_HOME=' in c['template']]},
    }
    special = []
    jvm = re.search(r'-Xmx(\S+)', params.get('theoneJVMProp', ''))
    if jvm and params.get('xmx') and jvm[1].lower() != params['xmx'].lower():
        special.append(dict(kind='JVM 内存参数存在两个不同声明', detail=f"xmx={params['xmx']}，theoneJVMProp 中 -Xmx{jvm[1]}。需核对最终启动命令，不能直接判断实际内存。", evidence=fact('xmx')['evidence'] + fact('theoneJVMProp')['evidence']))
    if params.get('activeEnv') == 'test' and params.get('apollo.config.server.info'):
        apollo = normalize_slashes(params['apollo.config.server.info'])
        if params.get('apollo.meta_uat') in apollo and params.get('apollo.meta_test') not in apollo:
            special.append(dict(kind='Apollo 配置可能跨环境', detail='测试环境参数中的 apollo.config.server.info 指向 meta_uat 地址；需确认该通用参数是否被实际使用。', evidence=fact('apollo.config.server.info')['evidence']))
    for op in operations:
        for s in op['sections']:
            if 'setExitStatus(ScriptResult.EXIT_STATUS_SUCCESS)' in s['script_text'] and op['operation'] == 'Stop':
                special.append(dict(kind='Stop 脚本显式设置成功状态', detail='需结合用法提示复核进程停止结果，平台状态不能替代进程检查。', evidence=[evidence('Stop', op['source_file'], s['start_line'])]))
    unique_findings = {}
    for f in findings:
        key = (f['kind'], f['evidence']['operation'])
        entry = unique_findings.setdefault(key, dict(kind=f['kind'], operation=key[1], severity=f['severity'], detail=f['detail'], evidence=[]))
        entry['evidence'].append(f['evidence'])
    findings = list(unique_findings.values()) + special
    file_inventory = []
    for (path, action), evs in sorted(files.items()):
        item = dict(path=path, entry_type='directory' if path.endswith('/') else 'file',
                    action=action, occurrence_count=len(evs), evidence=evs)
        if path.endswith('.jar'):
            m = re.match(r'(.+?)-(\d[^/]*)\.jar$', Path(path).name)
            item['java_library'] = dict(name=m[1] if m else Path(path).stem, version=m[2] if m else None,
                                        basis='文件名推断，不是解析 Maven 依赖树')
        file_inventory.append(item)
    entity_inventory = []
    for (kind, value), evs in sorted(entities.items()):
        evs = [dict(t) for t in dict.fromkeys(tuple(e.items()) for e in evs)]
        entity_inventory.append(dict(type=kind, value=value, evidence=evs,
                                     note='文本观察；参数/注释中的地址不自动视为当前应用实际使用的服务，路径项为候选'))
    network_parameters = []
    for key, value in params.items():
        addresses = sorted(set(IP.findall(value)))
        addresses = [ip for ip in addresses if all(int(part) <= 255 for part in ip.split('.'))]
        if not addresses:
            continue
        role = ('部署目标' if key == '_vmIP' else '构建服务器配置' if key == 'jenkins_build_server'
                else '其他地址引用；可能是全局配置、其他环境或脚本条件')
        network_parameters.append(dict(parameter=key, addresses=addresses, role=role, evidence=fact(key)['evidence']))
    output_observations = []
    for op in operations:
        for row in op['lines']:
            if row['scope'] != 'observed_output':
                continue
            text = row['text'].strip()
            ev = evidence(op['operation'], op['source_file'], row['line'])
            transfer = re.fullmatch(r'sent ([\d,]+) bytes\s+received ([\d,]+) bytes\s+([\d,.]+) bytes/sec', text)
            total = re.fullmatch(r'total size is ([\d,]+)\s+speedup is ([\d,.]+)', text)
            if transfer:
                output_observations.append(dict(type='rsync_transfer', sent_bytes=int(transfer[1].replace(',', '')),
                    received_bytes=int(transfer[2].replace(',', '')), bytes_per_second=float(transfer[3].replace(',', '')), evidence=ev))
            elif total:
                output_observations.append(dict(type='rsync_total', total_size_bytes=int(total[1].replace(',', '')),
                    speedup=float(total[2].replace(',', '')), evidence=ev))
            elif text in ['success', 'Tomcat started.'] or re.match(r'vpc\s+\w+\s+[\d.]+$', text):
                output_observations.append(dict(type='status_message', value=text, evidence=ev,
                    note='观察到的输出消息，不作为健康或子命令全部成功的证明'))
    changes = []
    for removed in file_inventory:
        if removed['action'] != 'delete_reported' or 'java_library' not in removed:
            continue
        library = removed['java_library']['name']
        replacements = [f for f in file_inventory if f['action'] == 'listed_by_output' and f.get('java_library', {}).get('name') == library]
        changes.append(dict(library=library, removed_version=removed['java_library']['version'],
                            observed_versions=sorted(set(f['java_library']['version'] for f in replacements if f['java_library']['version'])),
                            evidence=removed['evidence'] + [e for f in replacements for e in f['evidence']],
                            note='按 rsync 文件名对比；不是解析依赖树或验证服务器最终文件状态'))
    limitations = [
        '仅分析输入目录本次部署的 5 类操作（以实际目录为准），不代表其他应用或其他部署。',
        '参数原文为 Java Map.toString，不是 JSON；采用键边界启发式，含类似键分隔符的自由文本可能有歧义，完整脱敏行保留供复核。',
        'script_template 是模板，observed_output 是输出；静态参数展开不会执行命令，也不能证明某个条件分支已运行。',
        '所有参数值保留字符串，不将空串、null、N 等擅自转换；接口 JSON 保留实际类型。',
        '相同输出可重复出现，保留每次证据位置；不据此认定命令重复执行。',
        '时间按来源字符串保留，原日志未标注时区；version_code/datestmp 是构建参数，不作为本次部署时间。',
        '未观察到提交 SHA、实际 java -version 或健康检查响应；Tomcat started 不等同业务健康。',
        '已脱敏按敏感键名和 -auth 识别的凭据；自动规则不能保证识别任意格式的所有敏感数据。',
        '不重复解析 source_response.txt（原始响应副本）；*_log.txt 为本次分析基准，原文件不改写。',
    ]
    structured = dict(schema_version='1.0', source_directory=str(source), parser_notes=limitations,
                      deployment=index['deployment'], operations=operations)
    information = dict(schema_version='1.0', source_directory=str(source), summary=semantic,
                       counts=dict(operations=len(operations), sections=sum(len(o['sections']) for o in operations),
                                   distinct_parameter_keys=len(properties), command_fragments=len(commands),
                                   output_json_occurrences=len(objects), file_action_pairs=len(file_inventory),
                                   ipv4_values=sum(e['type'] == 'ipv4' for e in entity_inventory)),
                       timeline=[dict(operation=o['operation'], start=o['metadata'].get('start_time'), end=o['metadata'].get('end_time'),
                                      duration_seconds=o['duration_seconds'], platform_status=o['metadata'].get('status'), source_file=o['source_file']) for o in operations],
                       parameters=properties, commands=commands, observed_json=objects, observed_key_values=output_kv,
                       observed_messages_and_transfer_stats=output_observations, network_parameters=network_parameters,
                       entities=entity_inventory, files=file_inventory, library_changes=changes,
                       review_findings=findings, limitations=limitations)
    out.mkdir(parents=True, exist_ok=False)
    for name, data in [('structured_logs.json', structured), ('key_information.json', information)]:
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        assert not any(secret in serialized for secret in secrets), 'Redaction validation failed'
        (out / name).write_text(serialized, encoding='utf-8')
    # Small per-operation files ease review of the large rsync output.
    (out / 'operations').mkdir()
    for i, op in enumerate(operations, 1):
        (out / 'operations' / f'{i:02d}_{op["operation"]}.json').write_text(json.dumps(op, ensure_ascii=False, indent=2) + '\n')

    def refs(evs):
        return '；'.join(f"{e['operation']}:{e['line']}" for e in evs[:3]) + (' 等' if len(evs) > 3 else '')
    md = ['# 部署日志审查报告', '', f"应用：**{params.get('cur_app_name')}**；环境：**{params.get('activeEnv')}**；目标服务器：**{params.get('_vmIP')}**。", '',
          '## 文件使用方式', '', '- `structured_logs.json`：完整脱敏文本逐行保留，同时拆分参数、脚本和输出。',
          '- `key_information.json`：参数分类、命令片段、静态参数展开、接口 JSON、IP/URL/路径、文件与 JAR 清单、复核项。',
          '- `operations/`：按操作拆分，适合逐个审查。引用格式为“操作:原始日志行号”，完整相对路径见 JSON 的 evidence.file。', '',
          '## 操作时间线', '', '|操作|开始时间|结束时间|耗时（秒）|平台状态|', '|---|---|---|---:|---|']
    for t in information['timeline']:
        md.append(f"|{t['operation']}|{t['start']}|{t['end']}|{t['duration_seconds']}|{t['platform_status']}|")
    md += ['', '## 代码与构建', '', f"- 仓库：`{build.get('res_url')}`。", f"- 分支：`{build.get('branch')}`；code_version：`{build.get('code_version')}`；未取得明确提交 SHA。",
           f"- Jenkins 作业：`{params.get('dp_jobname')}`，配置构建主机：`{params.get('jenkins_build_server')}`。",
           f"- Maven：`{build.get('goal_options')}`；根 POM：`{build.get('root_pom')}`（包含跳过测试参数）。",
           f"- 版本参数：`{params.get('version_code')}`，不能当作本次部署时间。", f"- 来源：{refs(fact('dp_params')['evidence'])}。", '',
           '## 应用运行与分发配置', '', '|参数|值|证据|', '|---|---|---|']
    for k in ['_vmIP', '_vmPort', 'http1.1_port', 'dubbo_protocol_port', 'server_xml_server_port', 'server_xml_path', 'server_xml_docBase', 'targetTomcatFolderName', 'defaultTomcatFolderName', 'xmx', 'theoneJVMProp', 'checkVersion', 'pkg_repository_server', 'pkg_repository_server_ip']:
        md.append(f"|{k}|{params.get(k, '').replace('|', '&#124;')}|{refs(fact(k)['evidence'])}|")
    md += ['', '- JDK：作业名后缀与 Start 模板相符，推断选择 JDK 8；未验证实际运行版本。',
           '- rsync 发布包使用 `--delete`；输出列出文件及删除项，文件清单不等同于完整构建依赖树。',
           '- 参数中还包含其他环境的 Kong、Apollo、Nacos、Dubbo、MQ、Kafka、ZooKeeper、日志配置及全局规则，完整保留在 parameters，不自动认定为当前依赖。', '',
           '## 输出中解析出的网关对象', '']
    dedup_obj = set()
    for obj in objects:
        encoded = json.dumps(obj['value'], ensure_ascii=False, sort_keys=True)
        if encoded in dedup_obj:
            continue
        dedup_obj.add(encoded)
        md += [f"来源：{refs([obj['evidence']])}", '```json', json.dumps(obj['value'], ensure_ascii=False, indent=2), '```', '']
    md += ['## 需复核的结果', '']
    for f in findings:
        md.append(f"- **{f['kind']}**：{f['detail']}（{refs(f['evidence'])}）")
    md += ['', '## 提取规模', '', '```json', json.dumps(information['counts'], ensure_ascii=False, indent=2), '```', '', '## 解读限制', '']
    insert = md.index('## 提取规模')
    more = ['## 发布包传输与 JAR 变化', '']
    seen_stats = set()
    for observation in output_observations:
        if not observation['type'].startswith('rsync_'):
            continue
        content = {k: v for k, v in observation.items() if k not in ('evidence', 'type')}
        key = json.dumps(content, sort_keys=True)
        if key not in seen_stats:
            seen_stats.add(key)
            more.append(f"- `{key}`（{refs([observation['evidence']])}）。")
    for change in changes:
        more.append(f"- `{change['library']}`：删除版本 `{change['removed_version']}`，输出列出版本 `{', '.join(change['observed_versions'])}`（{refs(change['evidence'])}）。")
    more += ['', '重复的传输统计只展示一次；JAR 变化来自文件名，不代表完整依赖分析。', '']
    md[insert:insert] = more
    md.extend('- ' + item for item in limitations)
    review = '\n'.join(md) + '\n'
    assert not any(secret in review for secret in secrets)
    (out / 'REVIEW.md').write_text(review, encoding='utf-8')
    validation = dict(all_original_lines_preserved=all(len(o['lines']) == o['original_line_count'] for o in operations),
                      parsed_parameter_counts={o['operation']:len(o['parameters']) for o in operations},
                      known_sensitive_values_absent=True, source_files_unchanged=True,
                      output_json_occurrences=len(objects), output_json_distinct=len(dedup_obj))
    for _, path, raw, _, _ in inputs:
        assert path.read_bytes() == raw
    for p in out.rglob('*.json'):
        content = p.read_text()
        json.loads(content)
        assert not any(secret in content for secret in secrets)
    (out / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(output=str(out), counts=information['counts'], validation=validation), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Directory containing deployment index.json')
    parser.add_argument('--output', required=True, type=Path, help='New output directory (must not already exist)')
    args = parser.parse_args()
    analyze(args.input.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
