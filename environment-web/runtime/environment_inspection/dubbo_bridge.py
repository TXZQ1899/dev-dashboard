"""Read-only Dubbo ZooKeeper registry inspection, without third-party packages."""
import base64
import json
import os
from pathlib import Path
import socket
import struct
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def recv_exact(sock, size):
    chunks=[]
    while size:
        part=sock.recv(size)
        if not part: raise ConnectionError('注册中心连接关闭')
        chunks.append(part);size-=len(part)
    return b''.join(chunks)


def frame(sock, payload):
    sock.sendall(struct.pack('!i',len(payload))+payload)
    size=struct.unpack('!i',recv_exact(sock,4))[0]
    if size<0 or size>8*1024*1024: raise ValueError('注册中心响应长度无效')
    return recv_exact(sock,size)


class Zookeeper:
    def __init__(self,host,port):
        self.sock=socket.create_connection((host,port),timeout=5);self.sock.settimeout(8);self.xid=1
        response=frame(self.sock,struct.pack('!iqiqi',0,0,5000,0,16)+b'\0'*16+b'\0')
        if len(response)<20 or struct.unpack_from('!i',response,4)[0]<=0: raise ConnectionError('注册中心会话建立失败')
    def children(self,path):
        encoded=path.encode('utf-8')
        data=frame(self.sock,struct.pack('!iii',self.xid,12,len(encoded))+encoded+b'\0'); self.xid+=1
        if len(data)<20: raise ValueError('注册中心响应不完整')
        xid,zxid,error=struct.unpack_from('!iqi',data,0)
        if error == -101:return []
        if error:raise ValueError('注册中心返回错误 %d'%error)
        count=struct.unpack_from('!i',data,16)[0]
        if count<0 or count>5000:raise ValueError('注册中心返回条目超限')
        pos=20;result=[]
        for _ in range(count):
            if pos+4>len(data):raise ValueError('注册中心数据截断')
            n=struct.unpack_from('!i',data,pos)[0];pos+=4
            if n<0 or pos+n>len(data):raise ValueError('注册中心数据截断')
            result.append(data[pos:pos+n].decode('utf-8'));pos+=n
        return result
    def close(self):self.sock.close()


def registration(value):
    uri=unquote(value); parsed=urlsplit(uri)
    if not parsed.scheme or not parsed.hostname:return None
    try: port=parsed.port
    except ValueError:return None
    query=parse_qs(parsed.query)
    return {'host':parsed.hostname,'port':port if port else None,'application':(query.get('application') or [''])[0],
            'group':(query.get('group') or [''])[0], 'version':(query.get('version') or [''])[0]}


def app_mapping(row, apps, environment):
    candidates=[]
    for app in apps:
        if not row['application'] or app.get('name')!=row['application']:continue
        for dep in app.get('envs',{}).get(environment,[]):
            if dep.get('ip')!=row['host']:continue
            if row['port'] and str(dep.get('port') or '') != str(row['port']):continue
            candidate=(str(app.get('id','')),app.get('name',''))
            if candidate not in candidates:candidates.append(candidate)
    if len(candidates)==1:return candidates[0][0],candidates[0][1], 'EXACT' if row['port'] else 'INFERRED'
    return '','', 'AMBIGUOUS' if len(candidates)>1 else 'UNKNOWN'


def scan_registry(host,port,app_name,target_endpoints,apps,environment,observed_ports=None,client_factory=Zookeeper):
    registry='%s:%s'%(host,port); result=[];warnings=[]
    client=client_factory(host,port)
    try:
        services=client.children('/dubbo')
        if not services: warnings.append('默认 /dubbo 路径无已观察接口，不能排除其他命名空间')
        if len(services)>500: warnings.append('服务接口超过 500，结果截断');services=services[:500]
        for interface in services:
            path='/dubbo/'+interface
            try:
                providers=[registration(x) for x in client.children(path+'/providers')]
                consumers=[registration(x) for x in client.children(path+'/consumers')]
            except (OSError,ValueError,ConnectionError) as exc:
                warnings.append('接口读取失败 '+interface+'：'+type(exc).__name__);continue
            providers=[p for p in providers if p];consumers=[c for c in consumers if c]
            ips={ip for ip,_ in target_endpoints}
            observed_ports = observed_ports or {}
            own_providers=[p for p in providers if p['application']==app_name and ((p['host'],str(p['port'])) in target_endpoints or str(p['port']) in observed_ports.get(p['host'],[]))]
            own_consumers=[c for c in consumers if c['host'] in ips and c['application']==app_name]
            for role,own,peers in (('PROVIDER',own_providers,consumers),('CONSUMER',own_consumers,providers)):
                for item in own:
                    compatible=[p for p in peers if not (p['group'] and item['group'] and p['group']!=item['group']) and not (p['version'] and item['version'] and p['version']!=item['version'])]
                    for peer in compatible or [None]:
                        provider=item if role=='PROVIDER' else peer
                        consumer=peer if role=='PROVIDER' else item
                        provider_id,provider_app,provider_confidence=app_mapping(provider,apps,environment) if provider else ('','','UNKNOWN')
                        consumer_id,consumer_app,consumer_confidence=app_mapping(consumer,apps,environment) if consumer else ('','','UNKNOWN')
                        result.append({'registry':registry,'interface':interface,'role':role,
                            'providerName':provider.get('application','') if provider else '',
                            'providerIp':provider.get('host','') if provider else '', 'providerPort':provider.get('port') if provider else None,
                            'providerAppId':provider_id,'providerAppName':provider_app,'providerConfidence':provider_confidence,
                            'consumerName':consumer.get('application','') if consumer else '',
                            'consumerIp':consumer.get('host','') if consumer else '', 'consumerPort':consumer.get('port') if consumer else None,
                            'consumerAppId':consumer_id,'consumerAppName':consumer_app,'consumerConfidence':consumer_confidence,
                            'evidence':'注册记录与目标地址/进程监听端口匹配；同 ZooKeeper 注册中心与接口的关联为推断，分组和版本缺失时不视为已核实'})
    finally:client.close()
    return result,warnings


def main():
    data=Path(os.environ.get('ENVSCOPE_DATA','/data')); version,app_id,environment,encoded=sys.argv[1:]
    query=json.loads(base64.b64decode(encoded)); apps=json.loads((data/'versions'/version/'snapshot.json').read_text()).get('apps',[])
    app=[a for a in apps if str(a.get('id'))==app_id]
    if len(app)!=1:raise ValueError('应用身份不唯一')
    if not query['registries']:
        return {'status':'UNKNOWN','rows':[],'warnings':['已下载配置未明确识别 Dubbo ZooKeeper 注册中心，角色及注册关系未知']}
    rows=[];warnings=[];completed=0
    for reg in query['registries']:
        try:
            r,w=scan_registry(reg['host'],int(reg['port']),app[0]['name'],{(str(ip),str(port)) for ip,port in query['endpoints']},apps,environment,query.get('observedPorts',{}))
            rows+=r;warnings+=w;completed+=1
        except Exception as exc:warnings.append('注册中心 %s:%s 查询失败：%s'%(reg.get('host'),reg.get('port'),type(exc).__name__))
    return {'status':'UNKNOWN' if not completed else 'PARTIAL','rows':rows,'warnings':['仅检查 ZooKeeper 默认 /dubbo 路径；其他注册中心或命名空间未检查']+warnings}


if __name__=='__main__':
    try:print(json.dumps(main(),ensure_ascii=False))
    except Exception as exc:print(json.dumps({'status':'FAILED','rows':[],'warnings':[type(exc).__name__]},ensure_ascii=False))
