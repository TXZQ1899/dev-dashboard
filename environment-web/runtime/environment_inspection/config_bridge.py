"""Export selected saved application configuration and extract explicit declarations."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, '/app/runtime')
from application_config.endpoints import parse_config


def classify(key, kind):
    key = key.lower()
    if 'dubbo' in key and ('registry' in key or 'register' in key): return 'Dubbo 注册中心'
    if 'zookeeper' in key or 'zookeeper' in kind.lower(): return 'ZooKeeper'
    if 'eureka' in key: return 'Eureka 注册中心'
    if 'nacos' in key: return 'Nacos'
    if 'consul' in key: return 'Consul'
    if 'spring.cloud.config' in key: return '配置中心'
    return kind


def clean_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http','https') or not parsed.netloc: return None
        host = parsed.hostname or ''
        if not host: return None
        display = '['+host+']' if ':' in host and not host.startswith('[') else host
        if parsed.port is not None: display += ':'+str(parsed.port)
        return urlunsplit((parsed.scheme.lower(),display,parsed.path,'',''))
    except ValueError: return None


def select_file(rel, environment, profiles):
    parts=Path(rel).parts
    if len(parts)<2 or parts[0] not in ('prod','test','uat','dev','other'): return False
    selected={'PRODUCT':'prod','TEST':'test','SIMULATION':None}[environment]
    groups={'other'}
    if selected: groups.add(selected)
    # Only use the legacy uat folder when the running process explicitly
    # names that profile; SIMULATION alone does not establish equivalence.
    if environment=='SIMULATION' and 'uat' in profiles: groups.add('uat')
    if parts[0] not in groups: return False
    filename=parts[-1].lower()
    m=re.match(r'^(?:application|bootstrap)-([a-z0-9_.-]+)\.(?:ya?ml|properties|xml)$',filename)
    if parts[0]=='other' and m and m.group(1) not in profiles: return False
    return True


def scan(data, app_id, environment, profiles):
    root=data/'application-config'
    folders=[p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and p.name.endswith('-'+app_id)]
    if len(folders)!=1: raise ValueError('应用配置目录无法唯一定位')
    folder=folders[0]; manifest_path=folder/'other'/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if str(manifest.get('appId'))!=app_id: raise ValueError('应用配置身份不一致')
    entries={e.get('file'):e for e in manifest.get('files',[]) if isinstance(e,dict)}
    files=[]; components=[]; urls=[]; evidence=[]; warnings=[]
    seen_bytes=0
    for current, dirs, names in os.walk(folder,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(current)/d).is_symlink()]
        for name in sorted(names):
            path=Path(current)/name
            if path==manifest_path or path.is_symlink() or not path.is_file(): continue
            rel=path.relative_to(folder).as_posix()
            if not select_file(rel,environment,profiles):continue
            if not path.resolve().is_relative_to(folder.resolve()): warnings.append('跳过越界路径');continue
            try: raw=path.read_bytes()
            except OSError: warnings.append('读取配置失败：'+rel);continue
            seen_bytes+=len(raw)
            if len(raw)>2*1024*1024 or seen_bytes>32*1024*1024: warnings.append('配置超过读取上限：'+rel);continue
            item=entries.get(rel,{})
            info={'file':rel,'sourcePath':item.get('path') or item.get('dataId') or rel,'source':item.get('source','unknown'),'group':Path(rel).parts[0], 'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
            files.append(info)
            text=raw.decode('utf-8','replace')
            endpoints=parse_config(text,info['sourcePath'])
            if len(endpoints)>=200:warnings.append('端点条目达到限制：'+rel)
            for e in endpoints:
                e['kind']=classify(e.get('key',''),e.get('kind','未知服务'))
                line_text = text.splitlines()[e['line']-1] if e.get('line') and e['line']<=len(text.splitlines()) else ''
                if e['kind']=='Dubbo 注册中心':
                    scheme = re.search(r'\b(zookeeper|nacos|redis|multicast)://', line_text, re.I)
                    e['registryProtocol'] = scheme.group(1).lower() if scheme else 'UNKNOWN'
                literal = re.search(re.escape(str(e.get('host','')))+r'\s*:\s*'+re.escape(str(e.get('port','')))+r'(?!\d)', line_text, re.I)
                e.update(source=info['source'], group=info['group'], confidence='EXACT' if literal else 'INFERRED', scope='配置声明；类型按配置键推断，生效状态未核实')
                components.append(e)
            for lineno,line in enumerate(text.splitlines(),1):
                if line.lstrip().startswith(('#','!','<!--')): continue
                if '${' in line and re.search(r'registry|register|nacos|eureka|zookeeper|redis|rabbit|kafka|rocketmq|datasource|jdbc',line,re.I) and not re.search(r'password|passwd|secret|token|username',line,re.I):
                    key=re.split(r'[:=]',line.strip(),maxsplit=1)[0][:160]
                    components.append({'kind':classify(key,'未知服务'),'host':'未解析变量','port':'','protocol':'','file':info['sourcePath'],'key':key,'line':lineno,'source':info['source'],'group':info['group'],'confidence':'UNKNOWN','scope':'配置含变量，端点未解析'})
                registry_xml=re.search(r'<\s*dubbo:registry\b[^>]*\baddress\s*=\s*["\']([^"\']+)["\']',line,re.I)
                if registry_xml:
                    address=registry_xml.group(1)
                    scheme=re.match(r'(zookeeper|nacos|redis|multicast)://([^/?]+)',address,re.I)
                    if scheme:
                        for authority in scheme.group(2).split(','):
                            try:
                                target=urlsplit(scheme.group(1)+'://'+authority)
                                if not target.hostname or not target.port:continue
                                row={'kind':'Dubbo 注册中心','host':target.hostname,'port':target.port,'protocol':'tcp','file':info['sourcePath'],'key':'dubbo:registry.address','line':lineno,'source':info['source'],'group':info['group'],'confidence':'EXACT','scope':'配置声明；类型按配置键推断，生效状态未核实','registryProtocol':scheme.group(1).lower()}
                                if not any(all(e.get(k)==row[k] for k in ('kind','host','port','file','line')) for e in components): components.append(row)
                            except ValueError: warnings.append('Dubbo 注册中心地址格式无法解析：'+info['sourcePath']+':'+str(lineno))
                if re.search(r'\bdubbo\b',line,re.I):
                    role = 'PROVIDER' if re.search(r'<\s*dubbo:service\b|@DubboService|@Service\s*\(',line,re.I) else 'CONSUMER' if re.search(r'<\s*dubbo:reference\b|@DubboReference',line,re.I) else 'UNKNOWN'
                    evidence.append({'file':info['sourcePath'],'line':lineno,'role':role,'note':'Dubbo 配置标记；运行时身份和注册关系未核实'})
                for match in re.finditer(r'https?://[^\s\"\'<>]+',line,re.I):
                    clean=clean_url(match.group(0).rstrip(';,)]}'))
                    if clean:urls.append({'url':clean,'file':info['sourcePath'],'line':lineno,'source':info['source'],'scope':'已下载配置，生效状态未核实'})
    return {'appId':app_id,'name':manifest.get('name',''),'snapshotVersion':manifest.get('snapshotVersion',''),'files':files,'components':components,'urls':urls,'dubboEvidence':evidence,'warnings':warnings,'profiles':sorted(profiles)}


if __name__=='__main__':
    try:
        config=scan(Path(os.environ.get('ENVSCOPE_DATA','/data')),sys.argv[1],sys.argv[2],set(json.loads(base64.b64decode(sys.argv[3]))))
        print(json.dumps(config,ensure_ascii=False))
    except Exception as exc:
        print('应用配置导出失败：'+type(exc).__name__,file=sys.stderr);sys.exit(1)
