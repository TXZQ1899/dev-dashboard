#!/usr/bin/env python3
"""Aggregate existing outputs only; do not resolve DNS or probe services."""
import argparse
import collections
import ipaddress
import json
import re
from pathlib import Path

RULES = [
 ('ZooKeeper', r'zookeeper|zkaddress|zk_connect'), ('Nacos', r'nacos'),
 ('Apollo', r'apollo'), ('SVN', r'svn'), ('Jenkins', r'jenkins'),
 ('Kong 网关', r'kongApi|^Kong 网关$'), ('Kafka', r'brokerList|^Kafka'),
 ('MQ 管理接口', r'MQAdmin|^MQ 管理'), ('Dubbo 治理接口', r'DUBBO_URL|^Dubbo 治理'),
 ('发布包仓库/分发', r'rsync|pkg_repository|devopspkg|http_install_url|^发布包仓库$|^安装包下载$'),
 ('Logstash', r'Logstash'), ('日志接收端', r'log_sink|^日志接收端$'),
 ('CAT 监控', r'cat_remote_server|^cat$'), ('CAT 安装包下载（注释）', r'^CAT 客户端安装包下载地址$'),
 ('服务器状态监控', r'server_status_monitor'), ('Anchor 客户端服务（用途待核实）', r'anchor_client_server'),
 ('Redis', r'\bredis\b'), ('MySQL', r'\bmysql\b'), ('MongoDB', r'mongodb'),
 ('RabbitMQ', r'rabbitmq'), ('RocketMQ', r'rocketmq'), ('Elasticsearch', r'elasticsearch'),
 ('Nexus', r'\bnexus\b'), ('GitLab', r'\bgitlab\b'),
]
INFRA_APPS = {'nginx': 'Nginx', 'cat': 'CAT 监控', 'xxl-job-admin': 'XXL-JOB 调度中心',
              'fcrs-xxl-job-admin': 'XXL-JOB 调度中心', 'eureka_server': 'Eureka'}

def classify(n):
    labels = n.get('configuration_names', []) + n.get('names', [])
    return [name for name, pattern in RULES if any(re.search(pattern, str(x), re.I) for x in labels)]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=Path('outputs'))
    ap.add_argument('--output', type=Path, default=Path('outputs/shared-services-summary'))
    args = ap.parse_args()
    root, out = args.input.resolve(), args.output.resolve()
    files = sorted(p for p in root.rglob('*') if p.is_file() and out not in p.parents)
    records, candidates, manifest, invalid = {}, {}, [], []
    app_count = dep_count = endpoint_count = 0
    def add(service, n, source, pointer, app, env, deploy=None, candidate=False):
        address = n.get('ip') or n.get('domain')
        if not address: return
        if n.get('ip'):
            try: ipaddress.ip_address(address)
            except ValueError:
                invalid.append({'source': source, 'pointer': pointer, 'address': address}); return
        else: address = address.lower().rstrip('.')
        port = n.get('port')
        if port is not None and (not isinstance(port, int) or not 1 <= port <= 65535):
            invalid.append({'source': source, 'pointer': pointer, 'port': port}); return
        target = candidates if candidate else records
        rec = target.setdefault((service, address), {'service': service, 'address': address,
            'address_type': 'IP' if n.get('ip') else 'domain', 'ports': set(),
            'has_unspecified_port_evidence': False, 'configuration_names': set(),
            'scopes': set(), 'notes': set(), 'source_applications': set(),
            'source_environments': set(), 'environment_key_hints': set(), 'evidence': []})
        if port is None: rec['has_unspecified_port_evidence'] = True
        else: rec['ports'].add(port)
        for key in ['configuration_names', 'scopes', 'notes']: rec[key].update(n.get(key, []))
        rec['source_applications'].add(app)
        if env: rec['source_environments'].add(env)
        for key in n.get('configuration_names', []):
            if re.search(r'(?:^|[._])(test\w*|uat|prod|product|simulation)(?:$|[._])', key, re.I):
                rec['environment_key_hints'].add(key)
        rec['evidence'].append({'source': source, 'json_pointer': pointer, 'application': app,
            'source_environment': env, 'deploy_id': deploy, 'port': port,
            'configuration_names': n.get('configuration_names', []), 'scopes': n.get('scopes', [])})
    for p in files:
        source = str(p.relative_to(root))
        status = '派生文档或非结构化文件；不重复计数'
        if p.suffix == '.json':
            try: data = json.loads(p.read_text())
            except (ValueError, UnicodeError) as ex:
                manifest.append({'file': source, 'status': 'JSON 解析失败', 'error': str(ex)}); continue
            if isinstance(data, dict) and isinstance(data.get('environments'), list) and data.get('application'):
                status = '主输入：应用提取结果'; app_count += 1
                app = data['application']
                infra = INFRA_APPS.get(app.lower())
                for ei, env in enumerate(data['environments']):
                    base = f'/environments/{ei}'
                    if infra:
                        for ii, ip in enumerate(env.get('server_ips', [])):
                            add(infra, {'ip': ip, 'scopes': ['应用部署登记（未验证运行）'],
                                'configuration_names': ['server_ips']}, source,
                                base + f'/server_ips/{ii}', app, env.get('name'))
                    for di, dep in enumerate(env.get('deployments', [])):
                        dep_count += 1
                        for ni, n in enumerate(dep.get('network_endpoints', [])):
                            endpoint_count += 1
                            pointer = base + f'/deployments/{di}/network_endpoints/{ni}'
                            for service in classify(n):
                                add(service, n, source, pointer, app, env.get('name'), dep.get('deploy_id'))
                            if 'app_ng_mapping' in n.get('configuration_names', []):
                                add('应用 NG 映射（待核实是否 Nginx）', n, source, pointer, app,
                                    env.get('name'), dep.get('deploy_id'), candidate=True)
                            if infra and n.get('ip') == dep.get('server_ip') and 'http1.1_port' in n.get('configuration_names', []):
                                add(infra, dict(n, configuration_names=['http1.1_port']), source, pointer,
                                    app, env.get('name'), dep.get('deploy_id'))
            elif isinstance(data, dict) and isinstance(data.get('endpoints'), list) and data.get('application'):
                status = '补充输入：专项端点提取结果'
                for key in ['endpoints', 'domain_endpoints']:
                    for ni, n in enumerate(data.get(key, [])):
                        for service in classify(n):
                            add(service, n, source, f'/{key}/{ni}', data['application'], data.get('environment'))
                        if 'app_ng_mapping' in n.get('configuration_names', []):
                            add('应用 NG 映射（待核实是否 Nginx）', n, source, f'/{key}/{ni}',
                                data['application'], data.get('environment'), candidate=True)
            else: status = '索引、原始日志或派生结果；由主输入/补充输入覆盖，不重复计数'
        if p.suffix == '.md' and p.with_suffix('.json').exists(): status = '同名 JSON 的 Markdown 展示副本；不重复计数'
        manifest.append({'file': source, 'status': status})
    def finalize(mapping):
        result = []
        for rec in mapping.values():
            for key, value in list(rec.items()):
                if isinstance(value, set): rec[key] = sorted(value)
            rec['source_application_count'] = len(rec['source_applications'])
            result.append(rec)
        priority = ['ZooKeeper', 'Nacos', 'Apollo', 'SVN', 'Jenkins', 'Kong 网关', 'Nginx']
        return sorted(result, key=lambda r: (priority.index(r['service']) if r['service'] in priority else len(priority), r['service'], r['address_type'],
            int(ipaddress.ip_address(r['address'])) if r['address_type'] == 'IP' else r['address']))
    rows, pending = finalize(records), finalize(candidates)
    ips = sorted({r['address'] for r in rows if r['address_type'] == 'IP'}, key=ipaddress.ip_address)
    stats = {'traversed_file_count': len(files), 'application_json_count': app_count,
        'deployment_record_count': dep_count, 'source_network_endpoint_count': endpoint_count,
        'service_type_count': len({r['service'] for r in rows}), 'service_address_count': len(rows),
        'unique_ip_count': len(ips), 'domain_record_count': sum(r['address_type'] == 'domain' for r in rows),
        'pending_ng_mapping_ip_count': len(pending), 'invalid_records': invalid}
    notes = [
        '公共服务指共用基础设施/中间件，不是公网 IP；内网地址与公网格式地址均保留。',
        '按服务类型 + IP/域名去重，合并明确端口；同 IP 多服务保留不同角色，纯 IP 清单再次全局去重。',
        '未明确端口不补默认值；同地址无端口证据与有端口证据合并，但 JSON 保留原始端口与定位。',
        '来源应用数表示提取文件中引用该地址的不同应用数；全局配置重复广泛出现，不等于真实依赖数。',
        '来源环境仅指记录所属环境，不能直接推断服务环境；带环境配置键作为独立线索列出。',
        '实际输出、声明配置、模板引用、部署登记按原始粒度保留；一个端点的实际输出不能证明同记录所有配置键均生效。',
        '未做 DNS 解析、端口扫描或连通性验证；域名不转换成臆测 IP，公网/内网双地址不合并成同一主机。',
        'Nginx 采用 Nginx 应用 server_ips；app_ng_mapping 只有名称暗示，单列候选，不纳入公共服务 IP 总数。',
        '本次以已有结构化提取结果为准；原始日志、同名 Markdown、演示文稿和工作簿等派生文件不重新抽取。扫描清单记录全部遍历文件。',
    ]
    out.mkdir(parents=True, exist_ok=True)
    (out / 'shared_services.json').write_text(json.dumps({'statistics': stats, 'methodology': notes,
        'services': rows, 'ng_mapping_candidates': pending}, ensure_ascii=False, indent=2) + '\n')
    (out / 'scan_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (out / 'public_service_ips.txt').write_text('\n'.join(ips) + '\n')
    def cell(x): return str(x).replace('|', '\\|').replace('\n', ' ')
    def sample_sources(rec):
        seen = []
        for e in rec['evidence']:
            if e['source'] not in seen: seen.append(e['source'])
        return '、'.join(f'[{Path(s).stem}](<{root / s}>)' for s in seen[:2])
    md = ['# 公共服务地址汇总（去重）', '',
        f'遍历 {len(files)} 个文件，使用 {app_count} 个应用 JSON 和专项端点提取结果；识别 **{len(ips)} 个唯一 IP**、**{stats["service_type_count"]} 类服务**、**{len(rows)} 条服务地址记录**（含域名）。', '',
        '## 口径', '', *['- ' + n for n in notes], '',
        '## 按服务汇总', '', '| 服务 | 唯一 IP 数 | 域名数 |', '|---|---:|---:|']
    grouped = collections.defaultdict(list)
    for r in rows: grouped[r['service']].append(r)
    for service, group in grouped.items():
        md.append(f'| {service} | {sum(r["address_type"] == "IP" for r in group)} | {sum(r["address_type"] == "domain" for r in group)} |')
    md += ['', '## 地址明细', '']
    for service, group in grouped.items():
        md += [f'### {service}', '', '| IP / 域名 | 明确端口 | 来源应用数 | 配置键 / 环境线索 | 证据范围 | 示例来源 |', '|---|---|---:|---|---|---|']
        for r in group:
            md.append('| ' + ' | '.join([r['address'], '、'.join(map(str, r['ports'])) or '未明确',
                str(r['source_application_count']), cell('、'.join(r['configuration_names']) or '—'),
                cell('；'.join(r['scopes']) or '未标注'), sample_sources(r)]) + ' |')
        md.append('')
    md += ['## Nginx 部署环境', '', '| 环境 | 登记 IP |', '|---|---|']
    ng_env = collections.defaultdict(set)
    for r in rows:
        if r['service'] == 'Nginx':
            for env in r['source_environments']: ng_env[env].add(r['address'])
    for env, addresses in ng_env.items(): md.append(f'| {env} | {"、".join(sorted(addresses, key=ipaddress.ip_address))} |')
    md += ['', '## 待核实的 NG 映射', '', '这些地址只出现在 app_ng_mapping，未直接确认 Nginx 身份，不计入上述唯一 IP 总数。', '', '| IP | 映射应用名称 | 来源 |', '|---|---|---|']
    for r in pending:
        names = set()
        # Names are available in the original endpoints referenced by the evidence pointers.
        for e in r['evidence'][:1]:
            d = json.loads((root / e['source']).read_text())
            for token in e['json_pointer'].strip('/').split('/'): d = d[int(token)] if isinstance(d, list) else d[token]
            names.update(d.get('names', []))
        md.append(f'| {r["address"]} | {cell("、".join(sorted(names)))} | {sample_sources(r)} |')
    md += ['', '## 核对事项', '',
        '- Apollo 同时出现带环境 meta 键和通用 apollo.config.server.info 地址；两组均保留，不能据此判定哪一组在当前应用生效。',
        '- Kong 地址来自 kongApi 配置，表示管理 API 或 DevOps 代理入口；不能直接作为业务流量代理端口使用。',
        '- Jenkins 的 10.200.4.30 来自专项结果的构建日志 URL，角色与 10.179.1.226:8080 的构建服务分别保留。',
        '- CAT 安装包 10.200.3.13 仅为模板注释引用；SVN、下载仓库、历史环境地址都需要后续确认是否仍在使用。',
        '- 未在本次结构化端点中识别出 Redis、MySQL、MongoDB、RabbitMQ、RocketMQ、Elasticsearch、Nexus、GitLab 的可靠公共服务地址；这不代表系统没有部署。Eureka 应用记录存在但没有登记 IP。',
        '', '完整证据定位、应用清单及端口明细见 [shared_services.json](shared_services.json)；',
        '全局去重 IP 见 [public_service_ips.txt](public_service_ips.txt)；遍历范围见 [scan_manifest.json](scan_manifest.json)。', '']
    (out / '公共服务IP汇总.md').write_text('\n'.join(md))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    for service, group in grouped.items(): print(service, ':', ', '.join(r['address'] + (':' + '/'.join(map(str, r['ports'])) if r['ports'] else '') for r in group))

if __name__ == '__main__': main()
