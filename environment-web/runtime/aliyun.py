"""Versioned cloud snapshots. Raw collection and display data remain separate."""
from export_slb import collect as collect_clb
from fetch_ecs_inventory import collect as collect_ecs


def array(value, outer, inner): return value.get(outer, {}).get(inner, [])


def normalize_clb(data):
    if not data.get('complete'): raise ValueError('CLB collection incomplete')
    certificates = {c['ServerCertificateId']: c for c in array(data['serverCertificates'], 'ServerCertificates', 'ServerCertificate')}
    instances = []
    for item in data['instances']:
        a = item['attributes']
        listeners = []
        for listener in item['listeners']:
            v = listener['attributes']
            certs = []
            for domain, cid in [('',v.get('ServerCertificateId'))] + [(e.get('Domain',''),e.get('ServerCertificateId')) for e in array(v,'DomainExtensions','DomainExtension')]:
                if not cid: continue
                c = certificates.get(cid,{})
                certs.append({'id':cid,'name':c.get('ServerCertificateName',''),'domain':domain,
                    'commonName':c.get('CommonName',''),'expiresAt':c.get('ExpireTime','')})
            listeners.append({'protocol': listener['protocol'],'port':v['ListenerPort'],'status':v.get('Status',''),
                'description':v.get('Description',''),'groupId':v.get('VServerGroupId') or v.get('MasterSlaveServerGroupId') or 'default',
                'backendPort':v.get('BackendServerPort'),'forwardPort':v.get('ForwardPort') if v.get('ListenerForward')=='on' else None,
                'healthCheck':v.get('HealthCheck',''),'certificates':certs,
                'rules':[{'id':r['RuleId'],'domain':r.get('Domain',''),'path':r.get('Url',''),'groupId':r['VServerGroupId']}
                         for r in array(listener.get('rules',{}),'Rules','Rule')]})
        # DescribeLoadBalancerAttribute's default BackendServers omit Port: for
        # the default server group the backend port is configured per listener
        # (BackendServerPort). When every listener routed to the default group
        # uses one identical port, it is the unambiguous default-group port.
        default_ports = {l['backendPort'] for l in listeners
                         if l['groupId'] == 'default' and l['backendPort'] is not None}
        default_port = next(iter(default_ports)) if len(default_ports) == 1 else None
        groups = []
        for kind, attrs in [('default', a)] + [(g['kind'], g['attributes']) for g in item['serverGroups']]:
            is_default = not (attrs.get('VServerGroupId') or attrs.get('MasterSlaveServerGroupId'))
            group_id = attrs.get('VServerGroupId') or attrs.get('MasterSlaveServerGroupId') or 'default'
            servers = []
            for s in array(attrs,'BackendServers','BackendServer') + array(attrs,'MasterSlaveBackendServers','MasterSlaveBackendServer'):
                port = s.get('Port')
                if port is None and is_default:
                    port = default_port
                servers.append({'id': s.get('ServerId',''), 'ip': s.get('ServerIp') or s.get('ResolvedServerIp',''),
                    'port': port, 'weight': s.get('Weight'), 'type': s.get('Type','')})
            groups.append({'id': group_id,
                'name': attrs.get('VServerGroupName') or attrs.get('MasterSlaveServerGroupName') or '默认服务器组', 'kind': kind,
                'servers': servers})
        ids = {g['id'] for g in groups}
        if any(l['groupId'] not in ids or any(r['groupId'] not in ids for r in l['rules']) for l in listeners):
            raise ValueError('Unresolved CLB server group')
        instances.append({'id':a['LoadBalancerId'],'name':a['LoadBalancerName'],'ip':a['Address'],
            'addressType':a['AddressType'],'status':a['LoadBalancerStatus'],'listeners':listeners,'groups':groups})
    return {'available':True,'collectedAt':data['collectedAt'],'region':data['region'],'instances':instances}


def ecs_snapshot(raw, projects):
    rows=[]
    for i in raw['Instances']['Instance']:
        extra=i.get('extraProperties',{})
        private=array(i.get('VpcAttributes',{}),'PrivateIpAddress','IpAddress')+array(i,'InnerIpAddress','IpAddress')
        for nic in array(i,'NetworkInterfaces','NetworkInterface'):
            private += [nic.get('PrimaryIpAddress')]+[p.get('PrivateIpAddress') for p in array(nic,'PrivateIpSets','PrivateIpSet')]
        public=array(i,'PublicIpAddress','IpAddress')+[i.get('EipAddress',{}).get('IpAddress'),extra.get('publicIpAddress'),extra.get('eipAddress')]
        rows.append({'id':i['InstanceId'],'name':i.get('InstanceName') or i['InstanceId'],'projectId':i.get('ResourceGroupId',''),
            'cpu':i['Cpu'],'memoryGiB':i['Memory']/1024,'os':i.get('OSName') or '未提供',
            'privateIps':list(dict.fromkeys(p for p in private if p)) or ([extra['innerIpAddress']] if extra.get('innerIpAddress') else []),
            'publicIps':list(dict.fromkeys(p for p in public if p)),
            'tags':{t['TagKey']:t.get('TagValue','') for t in extra.get('tags',[])+array(i,'Tags','Tag')},
            'status':i.get('Status','Unknown'),'region':i['RegionId'],'zone':i.get('ZoneId',''),'instanceType':i.get('InstanceType','')})
    return {'fetchedAt':raw['FetchedAt'],'region':raw['RegionId'],'projects':projects,'instances':rows}
