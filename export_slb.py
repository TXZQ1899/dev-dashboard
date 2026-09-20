"""Read-only export of the Shanghai CLB console, without exporting credentials."""
from export_devops import AuthenticationError
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = Path(__file__).resolve().parent
HOST = 'https://slb.console.aliyun.com'
REGION = 'cn-shanghai'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class Client:
    def __init__(self, cookie=None):
        self.cookie = (cookie if cookie is not None else (BASE/'aliyun-cookie.txt').read_text()).strip()
        if self.cookie.lower().startswith('cookie:'): self.cookie = self.cookie.split(':',1)[1].strip()
        if not self.cookie or '\n' in self.cookie or '\r' in self.cookie: raise ValueError('Cookie 必须为单行')
        self.opener = build_opener(NoRedirect)
        request = Request(HOST+'/slb/'+REGION+'/slbs',headers=self.headers())
        with self.opener.open(request,timeout=40) as response: html=response.read().decode()
        match = re.search(r'SEC_TOKEN\s*:\s*["\x27]([^"\x27]+)',html)
        if not match: raise AuthenticationError('页面未返回会话令牌，请更新 Cookie')
        self.token = match.group(1)

    def headers(self):
        return {'Cookie':self.cookie,'Referer':HOST+'/slb/'+REGION+'/slbs','User-Agent':'Mozilla/5.0','Accept':'application/json'}

    def call(self, action, params=None, product='slb'):
        if not action.startswith(('Describe','List')): raise ValueError('Only read-only actions are allowed')
        data={'action':action,'product':product,'region':REGION,'sec_token':self.token,
              'params':json.dumps({'RegionId':REGION,**(params or {})},ensure_ascii=False)}
        headers=self.headers();headers['Content-Type']='application/x-www-form-urlencoded'
        req=Request(HOST+'/data/api.json?'+urlencode({'action':action}),data=urlencode(data).encode(),headers=headers)
        for attempt in range(3):
            try:
                with self.opener.open(req,timeout=45) as response:result=json.load(response)
                if str(result.get('code')).upper() in ('401','NOT_LOGIN','UNAUTHORIZED','LOGIN_REQUIRED') or result.get('isLogin') is False:
                    raise AuthenticationError('CLB Cookie expired')
                if str(result.get('code'))!='200':
                    raise RuntimeError(action+' failed: '+str(result.get('code'))+' / '+str(result.get('message',''))[:180])
                value=result['data']
                if isinstance(value,dict) and value.get('Code') and value['Code'] not in ('200','Success'):
                    raise RuntimeError(action+' failed: '+str(value['Code']))
                return value
            except HTTPError as exc:
                if exc.code in (301,302,303,307,308,401): raise AuthenticationError('CLB Cookie expired') from None
                if exc.code == 403: raise
                if exc.code<500:raise RuntimeError(action+' HTTP '+str(exc.code)) from None
                if attempt==2:raise RuntimeError(action+' server unavailable') from None
            except OSError:
                if attempt==2:raise RuntimeError(action+' network timeout') from None
            time.sleep(1+attempt)


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def items(value, outer, inner):
    return value.get(outer, {}).get(inner, [])


def enrich_backends(client, result):
    missing = set()
    for entry in result['instances']:
        health = items(entry['health'], 'BackendServers', 'BackendServer')
        for attrs in [entry['attributes']] + [g['attributes'] for g in entry['serverGroups']]:
            for server in items(attrs, 'BackendServers', 'BackendServer'):
                if server.get('ServerIp'): continue
                ips = {h['ServerIp'] for h in health if h.get('ServerIp') and h.get('ServerId') == server.get('ServerId')}
                if len(ips) == 1:
                    server['ResolvedServerIp'] = ips.pop()
                    server['ResolvedIpSource'] = 'DescribeHealthStatus'
                elif server.get('Type') == 'ecs': missing.add(server['ServerId'])
    result['backendEcsInstances'] = []
    for start in range(0, len(missing), 100):
        value = client.call('DescribeInstances', {'InstanceIds':json.dumps(sorted(missing)[start:start+100]), 'PageSize':100}, product='ecs')
        result['backendEcsInstances'].extend(items(value, 'Instances', 'Instance'))
    for entry in result['instances']:
        for attrs in [entry['attributes']] + [g['attributes'] for g in entry['serverGroups']]:
            for server in items(attrs, 'BackendServers', 'BackendServer'):
                match = next((e for e in result['backendEcsInstances'] if e['InstanceId'] == server.get('ServerId')), None)
                if match and not server.get('ServerIp') and not server.get('ResolvedServerIp'):
                    ips = match.get('VpcAttributes',{}).get('PrivateIpAddress',{}).get('IpAddress',[]) or items(match,'InnerIpAddress','IpAddress')
                    if len(ips) == 1:
                        server['ResolvedServerIp'] = ips[0]
                        server['ResolvedIpSource'] = 'DescribeInstances'


def collect(cookie=None, out=None):
    client = Client(cookie)
    out = Path(out) if out is not None else BASE/'slb-export-cn-shanghai'/datetime.now().strftime('%Y%m%d-%H%M%S')
    result = {'region': REGION, 'source': HOST+'/slb/'+REGION+'/slbs',
              'collectedAt': datetime.now().astimezone().isoformat(), 'instances': [], 'errors': []}
    def read(action, params=None):
        try: return client.call(action, params)
        except Exception as exc:
            result['errors'].append({'action': action, 'params': params, 'error': str(exc)})
            return {}
    def listing():
        rows = []
        page = 1
        while True:
            data = client.call('DescribeLoadBalancers', {'PageNumber': page, 'PageSize': 100})
            batch = items(data, 'LoadBalancers', 'LoadBalancer')
            rows.extend(batch)
            if len(rows) >= data['TotalCount']: break
            if not batch: raise RuntimeError('分页结果提前为空')
            page += 1
        assert len(rows) == data['TotalCount'] == len({x['LoadBalancerId'] for x in rows})
        return rows
    rows = listing()
    result['serverCertificates'] = read('DescribeServerCertificates')
    result['caCertificates'] = read('DescribeCACertificates')
    for index, row in enumerate(rows):
        lb = row['LoadBalancerId']; params = {'LoadBalancerId': lb}
        detail = read('DescribeLoadBalancerAttribute', params)
        entry = {'listAttributes': row, 'attributes': detail, 'listeners': [],
                 'health': read('DescribeHealthStatus', params), 'serverGroups': []}
        for listener in items(detail, 'ListenerPortsAndProtocol', 'ListenerPortAndProtocol'):
            protocol = listener['ListenerProtocol'].upper()
            lp = {**params, 'ListenerPort': listener['ListenerPort']}
            value = {'protocol': protocol, 'attributes': read('DescribeLoadBalancer'+protocol+'ListenerAttribute', lp)}
            if protocol in ('HTTP', 'HTTPS'): value['rules'] = read('DescribeRules', lp)
            entry['listeners'].append(value)
        for kind, action, outer, inner, key, attr in [
            ('virtual','DescribeVServerGroups','VServerGroups','VServerGroup','VServerGroupId','DescribeVServerGroupAttribute'),
            ('masterSlave','DescribeMasterSlaveServerGroups','MasterSlaveServerGroups','MasterSlaveServerGroup','MasterSlaveServerGroupId','DescribeMasterSlaveServerGroupAttribute')]:
            groups = items(read(action, params), outer, inner)
            ids = {g[key] for g in groups}
            for listener in entry['listeners']:
                refs = [listener['attributes']] + items(listener.get('rules',{}), 'Rules','Rule')
                for ref in refs:
                    if ref.get(key) and ref[key] not in ids:
                        groups.append({key:ref[key]}); ids.add(ref[key])
            for group in groups:
                entry['serverGroups'].append({'kind':kind,'listAttributes':group,'attributes':read(attr,{key:group[key]})})
        result['instances'].append(entry)
        save(out/'slb-all.json', result)
        print(f'{index+1}/{len(rows)} {row["LoadBalancerName"]}: {len(entry["listeners"])} listeners, {len(entry["serverGroups"])} groups', flush=True)
    enrich_backends(client, result)
    result['instanceSetStable'] = {x['LoadBalancerId'] for x in listing()} == {x['LoadBalancerId'] for x in rows}
    result['completedAt'] = datetime.now().astimezone().isoformat()
    result['complete'] = not result['errors'] and result['instanceSetStable']
    save(out/'slb-all.json', result)
    print(json.dumps({'output':str(out), 'complete':result['complete'], 'errors':result['errors']},ensure_ascii=False))
    if not result['complete']: raise RuntimeError('CLB 采集不完整')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',action='store_true')
    args=parser.parse_args()
    if not args.probe:
        collect()
        raise SystemExit()
    client=Client()
    out=BASE/'slb-export-cn-shanghai'
    listing=client.call('DescribeLoadBalancers',{'PageNumber':1,'PageSize':100})
    save(out/'list.json',listing)
    first=listing['LoadBalancers']['LoadBalancer'][0]['LoadBalancerId']
    for action in ['DescribeLoadBalancerAttribute','DescribeVServerGroups','DescribeMasterSlaveServerGroups','DescribeHealthStatus']:
        value=client.call(action,{'LoadBalancerId':first})
        save(out/(action+'.json'),value)
        print(action,json.dumps(value,ensure_ascii=False)[:4500])
    for action in ['DescribeServerCertificates','DescribeCACertificates']:
        value=client.call(action)
        save(out/(action+'.json'),value)
        print(action,'keys',list(value),'length',len(json.dumps(value)))
