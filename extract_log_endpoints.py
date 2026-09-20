#!/usr/bin/env python3
"""Extract named addresses from the compact deployment analysis; no network requests."""
import json
import re
import ipaddress
import argparse
from pathlib import Path
from urllib.parse import urlsplit

IP = re.compile(r'(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?(?![\d.])')
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    args=parser.parse_args()
    data=json.loads(args.input.read_text())
    parameters=data['parameters']
    records={}
    domains={}
    def add(ip,port,name,config,scope,note=''):
        try: ipaddress.IPv4Address(ip)
        except ValueError: return
        port=int(port) if port else None
        if port is not None and not 1<=port<=65535: return
        r=records.setdefault((ip,port),dict(ip=ip,port=port,names=[],configuration_names=[],scopes=[],notes=[]))
        for key,val in [('names',name),('configuration_names',config),('scopes',scope),('notes',note)]:
            if val and val not in r[key]:r[key].append(val)
    def service(key):
        for pattern,name in [('kong','Kong 网关'),('apollo','Apollo 配置服务'),('nacos','Nacos 配置服务'),('jenkins','Jenkins 构建服务'),('MQAdmin','MQ 管理界面'),('DUBBO_URL','Dubbo 治理接口'),('brokerList','Kafka Broker'),('zookeeper','ZooKeeper'),('log_sink','日志接收端'),('rsync|pkg_repository','发布包仓库'),('svn','SVN 安装包仓库')]:
            if re.search(pattern,key,re.I):return name
        return key
    params={k:v['variants'][0]['value'] for k,v in parameters.items()}
    for key,record in parameters.items():
        for variant in record['variants']:
            value=re.sub(r'\\+/', '/',variant['value'])
            if key=='app_ng_mapping':
                mapping=json.loads(value)
                for name,addresses in mapping.items():
                    for m in IP.finditer(addresses):add(m[1],m[2],name,key,'全局应用映射','不是当前应用的部署目标；未推断映射服务类型')
                continue
            # Build parameters and script macros repeat facts without adding endpoint semantics.
            if key=='dp_params' or key.startswith('SCRIPT_BLOCK'):continue
            for m in IP.finditer(value):
                add(m[1],m[2],params.get('cur_app_name') if key=='_vmIP' else service(key),key,
                    '当前部署目标' if key=='_vmIP' else '声明配置（可包含其他环境）')
            for url in re.findall(r'https?://[^\s\'"<>]+',value):
                u=urlsplit(url)
                try:ipaddress.ip_address(u.hostname or '')
                except ValueError:
                    if u.hostname:
                        r=domains.setdefault((u.hostname,u.port),dict(domain=u.hostname,ip=None,port=u.port,names=[],configuration_names=[]))
                        if service(key) not in r['names']:r['names'].append(service(key))
                        if key not in r['configuration_names']:r['configuration_names'].append(key)
    for key,name in [('_vmPort','SSH'),('http1.1_port','HTTP'),('dubbo_protocol_port','Dubbo'),('server_xml_server_port','Tomcat 控制端口'),('SSLPort','SSL')]:
        if key in params:
            add(params['_vmIP'],params[key],params['cur_app_name']+' / '+name,key,'当前部署目标的端口配置',
                'SSLEnabled=false，端口配置存在不代表启用' if key=='SSLPort' and params.get('SSLEnabled')=='false' else '配置值；未验证监听或连通性')
    # These two addresses occur outside the property map, in output/template URLs.
    for entity in data.get('entities',[]):
        if entity['type']!='url':continue
        u=urlsplit(entity['value'])
        if u.hostname=='10.200.4.30':
            add(u.hostname,u.port,'Jenkins 构建日志页面',None,'脚本及输出 URL','URL 未显式指定端口')
        elif u.hostname=='10.200.3.13':
            add(u.hostname,u.port,'CAT 客户端安装包下载地址',None,'模板注释中的 URL','注释引用，不代表本次执行；URL 未显式指定端口')
    covered={ip for ip,port in records}
    for entity in data.get('entities',[]):
        if entity['type']=='ipv4' and entity['value'] not in covered:
            add(entity['value'],None,None,None,'未归属的文本地址','未找到可靠名称或端口')
    # Merge bare host declarations into existing endpoint records without inferring a port for them.
    result=sorted(records.values(),key=lambda r:(int(ipaddress.ip_address(r['ip'])),r['port'] or 0))
    output=dict(application=params.get('cur_app_name'),environment=params.get('activeEnv'),
                notes=['名称取自应用映射或配置键；服务名称是辅助解读，不是反向 DNS 查询结果。',
                       'port=null 表示来源未明确指定；未补填 HTTP、rsync 等协议默认端口。',
                       '全局参数包含其他应用/环境；这些地址不等同当前应用实际依赖，未进行连通性验证。'],
                unique_ip_count=len({r['ip'] for r in result}),endpoints=result,domain_endpoints=list(domains.values()))
    target=args.input.with_name('ip_ports_names.json')
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    md=['# IP、端口和名称清单','','| IP | 端口 | 名称 | 配置项 | 范围 / 备注 |','|---|---|---|---|---|']
    for r in result:
        md.append('| '+' | '.join([r['ip'],str(r['port']) if r['port'] is not None else '未明确', '、'.join(r['names']) or '未明确','、'.join(r['configuration_names']) or '—','；'.join(r['scopes']+r['notes'])])+' |')
    md+=['','## 仅有域名的配置','','以下域名未解析为 IP；来源未指定的端口保持为空。','','| 域名 | 端口 | 名称 |','|---|---|---|']
    for r in domains.values():md.append(f"| {r['domain']} | {r['port'] or '未明确'} | {'、'.join(r['names'])} |")
    md+=['']+['- '+n for n in output['notes']]
    target.with_suffix('.md').write_text('\n'.join(md)+'\n')
    assert {e['value'] for e in data['entities'] if e['type']=='ipv4'}=={r['ip'] for r in result}
    assert any(r['ip']=='10.58.9.217' and r['port']==7007 for r in result)
    print(json.dumps(dict(output=str(target),unique_ips=output['unique_ip_count'],endpoint_rows=len(result),domains=len(domains)),ensure_ascii=False))
if __name__=='__main__':main()
