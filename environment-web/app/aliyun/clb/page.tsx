'use client';
import { useEffect, useState } from 'react';
import { Shell } from '@/app/page';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { clb, certificatesFromClb, certificateRoutes, filterClb, referencedGroups, summarizeClb, type Group, type Clb } from '@/lib/clb';
import { instances as ecsInstances, fetchedAt as ecsFetchedAt } from '@/lib/ecs';
import { jumpserver } from '@/lib/jumpserver';
import './clb.css';
import { dns, staticDns, integrateDns, type DnsView } from '@/lib/dns';
import { cloudDns, type DnsZone } from '@/lib/cloud-dns';
import { eip, integrateEip, type Eip, type EipView } from '@/lib/eip';
import { nat, portLabel, type DnatEntry, type NatGateway } from '@/lib/nat';
import { TenantFilter, resourceTenantId } from '@/components/tenant-filter';
import { tenantLabel, resourceIdentity } from '@/lib/tenants';
import { formatBeijingTime } from '@/lib/datetime';
import { dnsListenerRoutes, nginxRoutesForBackend, nginxFileIncludes, nginxServerNames } from '@/lib/dns-downstream';
import { loadNginxArchive } from '@/lib/nginx-config-cache';
import type { Asset } from '@/lib/server-processes';

const dnsIntegration = integrateDns(clb.instances, dns.records, eip.records, nat);
const eipIntegration = integrateEip(clb.instances, dnsIntegration, eip.records, ecsInstances, nat);
const viewLink = (view: string, query: string) => `/aliyun/clb?view=${view}&q=${encodeURIComponent(query)}`;
function EipSummary({ record, instance }: { record: Eip; instance?: Clb }) {
  return <div className="clb-group"><strong><a href={viewLink('eip', record.id)}>{record.ip}</a> · {record.owner}{!instance && <> · {record.name === '-' ? '未命名 EIP' : record.name}</>}</strong>
    <small>{record.id} · {record.status} · {record.bandwidth}</small>
    <p>静态绑定：{record.bindingType || '未提供'} · {record.bindingName || '未提供'} · {record.bindingId || '未提供'}</p>
    <EipBackends record={record} instance={instance} />
  </div>;
}
function DnatMapping({ entry, instance }: { entry: DnatEntry; instance?: Clb }) {
  const targets = instance ? [instance].filter(target => target.ip === entry.internalIp) : clb.instances.filter(target => target.ip === entry.internalIp);
  return <div className="clb-group"><strong>{entry.protocol.toUpperCase()} · {entry.name || entry.id}</strong>
    <p className="nat-mapping"><code>{entry.externalIp} : {portLabel(entry.externalPort)}</code> → <code>{entry.internalIp} : {portLabel(entry.internalPort)}</code></p>
    <small>规则状态：{entry.status} · {entry.id} · DNAT 表：{entry.tableId}</small>
    <ResourceLinks ip={entry.internalIp} showUnlinked={!targets.length} />
    {targets.map(instance => <p key={resourceIdentity(instance)}><a href={viewLink('instances', instance.id)}>CLB · {instance.name} · {instance.id}</a></p>)}
  </div>;
}
function EipBackends({ record, instance }: { record: Eip; instance?: Clb }) {
  const row = eipIntegration.find(row => row.record.key === record.key);
  if (!row) return null;
  if (record.bindingType === 'ECS实例') return <section><strong>绑定 ECS 的实例 IP</strong>
    <small>按绑定实例 ID 精确匹配 · ECS 快照：{formatBeijingTime(ecsFetchedAt, '未提供')}</small>
    {row.ecsMatches.length ? row.ecsMatches.map(instance => <div key={resourceIdentity(instance)}><p><a href={`/aliyun/instances?q=${encodeURIComponent(instance.id)}`}>{instance.name} · {instance.id}</a></p><p>内网 IP：{instance.privateIps.join('、') || '未提供'}；公网 IP：{instance.publicIps.join('、') || '未提供'}</p><small>ECS 绑定记录未提供端口转换规则。</small></div>) : <p className="clb-note">当前 ECS 快照中未找到绑定实例 ID：{record.bindingId}。</p>}
  </section>;
  if (record.bindingType !== 'NAT网关') return null;
  const mappings = instance ? row.mappings.filter(({ entry }) => entry.internalIp === instance.ip) : row.mappings;
  if (instance) {
    const groups = { matched: [] as typeof mappings, missing: [] as typeof mappings, unknown: [] as typeof mappings };
    for (const mapping of mappings) {
      const port = mapping.entry.internalPort.trim();
      if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) groups.unknown.push(mapping);
      else if (instance.listeners.some(listener => listener.port === Number(port))) groups.matched.push(mapping);
      else groups.missing.push(mapping);
    }
    const group = (label: string, items: typeof mappings, note: string) => <details onToggle={event => event.stopPropagation()}>
      <summary>{label}（{items.length} 条）</summary><p className="clb-note">{note}</p>{items.map(({ entry }) => <DnatMapping key={entry.id} entry={entry} instance={instance} />)}
    </details>;
    return <section><small>NAT 快照：{formatBeijingTime(nat.collectedAt, '未采集')} · <a href={viewLink('nat', record.bindingId)}>{row.natGateway?.name || record.bindingId}</a></small>
      {groups.matched.length > 0 && group('DNAT 有效监听端口', groups.matched, '仅表示端口配置匹配，不代表规则状态、协议或实际访问路径可用。')}
      {groups.missing.length > 0 && group('DNAT 无效监听端口', groups.missing, '该端口无对应监听端口，访问路径不可达（依据当前 CLB 快照）。')}
      {groups.unknown.length > 0 && group('DNAT 端口待核实', groups.unknown, '目标端口不是可明确比对的单个端口，需进一步核实。')}
    </section>;
  }
  const content = <>
    {!nat.available ? <p className="clb-note">该版本尚未采集 NAT。<a href="/settings">打开 Settings 同步 NAT</a></p>
      : !row.natGateway ? <p className="clb-note">当前 NAT 快照未包含网关 {record.bindingId}，无法判断其 DNAT 映射。</p>
      : <><small>NAT 快照：{formatBeijingTime(nat.collectedAt, '未采集')} · <a href={viewLink('nat', record.bindingId)}>{row.natGateway.name || record.bindingId}</a></small>{mappings.length ? mappings.map(({ entry }) => <DnatMapping key={entry.id} entry={entry} instance={instance} />) : <p className="clb-note">{instance ? '没有目标地址为此 CLB 的 DNAT 条目。' : '已采集该网关，但此 EIP 没有 DNAT 条目；SNAT 出站地址可能没有入站映射。'}</p>}</>}
  </>;
  return <section><strong>DNAT 后端 IP / 端口映射（{mappings.length} 条）</strong>{content}</section>;
}
function InstanceEips({ instance }: { instance: Clb }) {
  const records = eipIntegration.filter(row => row.matches.some(match => resourceIdentity(match.instance) === resourceIdentity(instance)));
  return <section className="clb-group"><strong>静态 EIP 关联（{records.length} 条）</strong>{records.length ? records.map(({ record }) => <div key={record.key}><EipSummary record={record} instance={instance} /></div>) : <p className="clb-note">三份 EIP 导出中未匹配此实例。</p>}</section>;
}

function ResourceLinks({ ip, showUnlinked = true }: { ip: string; showUnlinked?: boolean }) {
  const ecs = ecsInstances.find((item) => item.privateIps.includes(ip) || item.publicIps.includes(ip));
  const jump = jumpserver.assets.find((item) => item.ip === ip);
  return <span className="clb-resources">
    <a className="clb-resource" href={`/resources?q=${encodeURIComponent(ip)}`} target="_blank" rel="noopener noreferrer">资源全景 ↗</a>
    {ecs ? <a className="clb-resource ecs" href={`/aliyun/instances?q=${encodeURIComponent(ip)}`} target="_blank" rel="noopener noreferrer">ECS · {ecs.name} ↗</a> : null}
    {jump ? <a className="clb-resource jump" href={`/jumpserver?q=${encodeURIComponent(ip)}`} target="_blank" rel="noopener noreferrer">JumpServer · {jump.hostname || ip} ↗</a> : null}
    {!ecs && !jump && showUnlinked ? <span className="clb-unlinked">未关联资源</span> : null}
  </span>;
}
function Backends({ group, port, compact = false }: { group?: Group; port?: number | null; compact?: boolean }) {
  if (!group) return <p className="clb-warning">服务器组数据缺失</p>;
  return <div className="clb-group"><strong>{group.name}</strong><small>{group.id === 'default' ? '默认服务器组' : group.id}</small>
    {group.servers.length ? <div className="clb-backends">{group.servers.map((s,index) => <div key={`${s.id}-${s.port}-${index}`}>
      <code>{s.ip || 'IP 未提供'} : {s.port ?? port ?? '由监听器指定'}</code>{!compact && s.ip ? <ResourceLinks ip={s.ip} /> : null}{!compact && <small>{s.id} · {s.type} · 权重 {s.weight ?? '未提供'}</small>}
    </div>)}</div> : <p className="clb-warning">无后端成员</p>}
  </div>;
}
function Routes({ instance, listener }: { instance: Clb; listener: Clb['listeners'][number] }) {
  if (listener.forwardPort != null) return <p>HTTP 跳转 → 本实例 HTTPS : {listener.forwardPort}</p>;
  return <div className="clb-routes"><section><h3>默认路由</h3><Backends group={instance.groups.find(g => g.id === listener.groupId)} port={listener.backendPort} /></section>
    {listener.rules.map(rule => <section key={rule.id}><h3>域名：{rule.domain || '任意'} · 路径：{rule.path || '未限定'}</h3><small>{rule.id}</small><Backends group={instance.groups.find(g => g.id === rule.groupId)} /></section>)}
  </div>;
}
function ListenerDetails({ instance, listener, keyPrefix, expanded, toggle, hideSummary = false }: { instance: Clb; listener: Clb['listeners'][number]; keyPrefix: string; expanded: Record<string, boolean>; toggle: (key: string, open: boolean) => void; hideSummary?: boolean }) {
  const key = `${keyPrefix}-${listener.protocol}-${listener.port}`;
  return <details className="clb-listener" open={!!expanded[key]} onToggle={e => { e.stopPropagation(); toggle(key, e.currentTarget.open); }}><summary><strong>{listener.protocol} : {listener.port}</strong>{!hideSummary && <span>{listener.status} · {listener.rules.length} 条规则</span>}</summary><p className="clb-note">{listener.description} · 健康检查：{listener.healthCheck || '未提供'}</p>{listener.certificates.length > 0 && <p className="clb-note">绑定证书：{listener.certificates.map(c => c.domain || c.commonName || c.name || c.id).join('、')}</p>}<Routes instance={instance} listener={listener} /></details>;
}
function CertificateListener({ instance, listener, certificateId, keyPrefix, expanded, toggle }: { instance: Clb; listener: Clb['listeners'][number]; certificateId: string; keyPrefix: string; expanded: Record<string, boolean>; toggle: (key: string, open: boolean) => void }) {
  const key = `${keyPrefix}-${listener.protocol}-${listener.port}`;
  const routes = certificateRoutes(listener, certificateId);
  return <details className="clb-listener" open={!!expanded[key]} onToggle={e => { e.stopPropagation(); toggle(key, e.currentTarget.open); }}>
    <summary><strong>{listener.protocol} : {listener.port}</strong><span>绑定该证书</span></summary>
    <div className="clb-routes">
      {routes.defaultRoute && <section><h3>默认服务器组</h3><Backends compact group={instance.groups.find(g => g.id === listener.groupId)} port={listener.backendPort} /></section>}
      {routes.rules.map(rule => <section key={rule.id}><h3>域名：{rule.domain} · 路径：{rule.path || '未限定'}</h3><Backends compact group={instance.groups.find(g => g.id === rule.groupId)} /></section>)}
      {!routes.defaultRoute && !routes.rules.length && <p className="clb-note">域名扩展未找到域名一致的转发规则，后端待确认。</p>}
    </div>
  </details>;
}
function Toolbar({ label, placeholder, query, onQuery, onExpand, onCollapse }: { label: string; placeholder: string; query: string; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void }) {
  return <section className="panel clb-toolbar"><Input aria-label={label} placeholder={placeholder} value={query} onChange={e => onQuery(e.target.value)} /><Button variant="outline" onClick={onExpand}>展开全部</Button><Button variant="outline" onClick={onCollapse}>折叠全部</Button></section>;
}
function InstanceView({ rows, query, expanded, toggle, onQuery, onExpand, onCollapse }: { rows: Clb[]; query: string; expanded: Record<string, boolean>; toggle: (key: string, open: boolean) => void; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void }) {
  const stats = summarizeClb(rows);
  return <><div className="clb-stats">{[['CLB 实例', stats.instances], ['监听入口', stats.listeners], ['去重后端', stats.backends], ['已关联后端', stats.used], ['后端关联记录', stats.memberships], ['无后端监听器', stats.empty]].map(([name,count]) => <section className="panel" key={name}><small>{name}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 CLB" placeholder="搜索实例名称、IP、端口、域名或服务器组" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} /><p className="clb-note">CLB 实例页展示全部采集到的 CLB，不要求实例必须配置证书。EIP 和 DNAT 关联基于静态导出及 NAT 快照。</p>
    {!rows.length && <p>没有匹配的实例</p>}{rows.map(instance => { const used = referencedGroups(instance); const unused = instance.groups.filter(g => !used.has(g.id) && (g.id !== 'default' || g.servers.length)); return <details className="panel clb-instance" key={resourceIdentity(instance)} open={!!expanded[resourceIdentity(instance)]} onToggle={e => toggle(resourceIdentity(instance), e.currentTarget.open)}>
      <summary><strong>{instance.name} · {tenantLabel(instance)}</strong><code>{instance.ip}</code><span>{instance.addressType === 'intranet' ? '内网' : '公网'} · {instance.status} · {instance.listeners.length} 个监听器</span></summary><small className="clb-id">{instance.id}</small><div className="clb-branches">
        <InstanceEips instance={instance} />{instance.listeners.map(listener => <ListenerDetails hideSummary key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={resourceIdentity(instance)} expanded={expanded} toggle={toggle} />)}
        {unused.length > 0 && <details open={!!expanded[`${instance.id}-unused`]} onToggle={e => { e.stopPropagation(); toggle(`${instance.id}-unused`, e.currentTarget.open); }}><summary>未被监听器或规则引用的服务器组（{unused.length}）</summary>{unused.map(group => <Backends key={group.id} group={group} />)}</details>}
      </div></details>; })}</>;
}
function CertificateView({ tenant, query, expanded, onQuery, onExpand, onCollapse, toggle }: { tenant: string; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  const scoped = clb.instances.filter(instance => !tenant || resourceTenantId(instance) === tenant);
  const rows = certificatesFromClb(scoped, query);
  const allCertificates = certificatesFromClb(scoped);
  const certificateInstances = new Set(allCertificates.flatMap(c => c.instances.map(i => i.instance.id)));
  return <><div className="clb-stats">{[['域名证书', rows.length], ['全部 CLB 实例', clb.instances.length], ['证书关联 CLB', certificateInstances.size], ['未配置证书', scoped.filter(i => !certificateInstances.has(i.id)).length]].map(([name,count]) => <section className="panel" key={name}><small>{name}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索域名证书" placeholder="搜索证书、域名、CLB、IP、端口或路由" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} /><p className="clb-note">证书页只展示实际绑定证书的 CLB；当前证书关联 {certificateInstances.size} 个实例，全部 CLB 为 {clb.instances.length} 个，差异为未配置证书或没有可识别证书绑定的实例。</p>
    {!rows.length && <p>没有匹配的证书，或当前快照尚未采集 CLB。</p>}{rows.map(certificate => <details className="panel clb-instance" key={certificate.key} open={!!expanded[certificate.key]} onToggle={e => toggle(certificate.key, e.currentTarget.open)}>
      <summary><strong>{certificate.certificate.domain || certificate.certificate.commonName || '未命名证书'}</strong><span>{certificate.certificate.name || certificate.certificate.id} · 到期：{certificate.certificate.expiresAt || '未提供'} · {certificate.instances.length} 个 CLB</span></summary><div className="clb-branches">
        {certificate.instances.map(({ instance, listeners }) => <section key={resourceIdentity(instance)} className="clb-certificate-clb"><div className="clb-clb-heading"><strong>{instance.name} · {tenantLabel(instance)}</strong><code>{instance.ip}</code><small>{instance.addressType === 'intranet' ? '内网' : '公网'} · {instance.status} · {instance.id}</small></div>{listeners.map(listener => <CertificateListener key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} certificateId={certificate.certificate.id} keyPrefix={`${certificate.key}-${instance.id}`} expanded={expanded} toggle={toggle} />)}</section>)}
      </div></details>)}</>;
}
function DnsBackend({ ip, port, domain }: { ip: string; port: number | null; domain: string }) {
  const nginx = nginxRoutesForBackend(jumpserver.assets as Asset[], ip, port, domain);
  const [references, setReferences] = useState<ReturnType<typeof nginxFileIncludes> | null>(null);
  const [serverNames, setServerNames] = useState<ReturnType<typeof nginxServerNames>>([]);
  const [referenceError, setReferenceError] = useState('');
  const [referenceBusy, setReferenceBusy] = useState(false);
  useEffect(() => {
    if (!nginx.routes.length) return;
    let active = true;
    setReferences(null); setServerNames([]); setReferenceError('');
    if (!nginx.version || !nginx.assetId) { setReferenceError('缺少配置版本或资产身份，无法读取配置归档。'); return; }
    setReferenceBusy(true);
    (async () => {
      try {
        const data = await loadNginxArchive(nginx.version, nginx.assetId);
        if (active) { setServerNames(nginxServerNames(data.files || [], nginx.routes, domain)); setReferences(nginxFileIncludes(data.files || [], nginx.routes)); }
      } catch (error) { if (active) setReferenceError(error instanceof Error ? error.message : '无法读取配置归档'); }
      finally { if (active) setReferenceBusy(false); }
    })();
    return () => { active = false; };
  }, [nginx.version, nginx.assetId, domain, ip, port]);
  const ecs = ecsInstances.filter(item => item.privateIps.includes(ip) || item.publicIps.includes(ip));
  const showRoute = (route: typeof nginx.routes[number], index: number) => <div key={`${route.file}-${route.uri}-${index}`}>
    <p>路径：{route.uri || '未提供'} · {route.directive} {route.target && `→ ${route.target}`}{route.file && <> · 配置文件：<code>{route.file}</code></>}</p>
    {route.backends.map((backend, i) => <p key={`${backend.host}-${backend.port}-${i}`}>下游：<code>{backend.host} : {backend.port || '端口未知'}</code></p>)}
  </div>;
  const main = nginx.routes.filter(route => route.uri === '/');
  const others = nginx.routes.filter(route => route.uri !== '/');
  return <div className="clb-group"><strong>{ecs.length === 1 ? `ECS · ${ecs[0].name}` : '后端'} · <code>{ip ? <a href={`/jumpserver?q=${encodeURIComponent(ip)}&view=processes&exact=1`}>{ip}</a> : 'IP 未提供'} : {port ?? '端口未知'}</code></strong>
    {ecs.length > 1 && <p className="clb-note">多个 ECS 匹配此 IP，归属待核实。</p>}
    {nginx.routes.length > 0 ? <section><p><strong>Nginx server_name 声明文件</strong></p>
      {referenceBusy && <p className="clb-note">正在读取配置归档…</p>}
      {referenceError && <p className="clb-warning">{referenceError}</p>}
      {references && (serverNames.length ? serverNames.map((declaration, index) => <div key={`${declaration.path}-${index}`}><p><code>{declaration.path}</code></p><p>server_name：{declaration.names.join(' · ')}</p></div>) : <p className="clb-note">未能从路由来源文件中确定 server_name 声明，入口文件待核实。</p>)}
      {references && <details onToggle={event => event.stopPropagation()}><summary>查看 include 引用</summary><p className="clb-note">以下为归档文件级引用；不表示每条引用均作用于该域名。路径、变量或未归档文件可能导致引用不完整。</p>
        {references.edges.length ? references.edges.map(edge => <p key={`${edge.from}-${edge.to}`}><code>{edge.from}</code> → <code>{edge.to}</code></p>) : <p>未发现可展开的 include 引用。</p>}
        {references.warnings.map(warning => <p className="clb-warning" key={warning}>{warning}</p>)}
      </details>}{main.map(showRoute)}
      {others.length > 0 && <details onToggle={event => event.stopPropagation()}><summary>其他路径和访问控制（{others.length} 条）</summary>
        <div className="dns-route-table"><table><thead><tr><th>路径</th><th>处理方式</th><th>upstream / 下游</th><th>配置文件</th></tr></thead><tbody>
          {others.map((route, index) => <tr key={`${route.file}-${route.uri}-${index}`}><td><code>{route.uri || '未提供'}</code></td><td>{route.directive || '未提供'}{route.directive !== 'proxy_pass' && route.target && <p>{route.target}</p>}</td>
            <td>{route.upstream && <p>upstream：<code>{route.upstream}</code></p>}{route.directive === 'proxy_pass' && route.target && <p>proxy_pass：<code>{route.target}</code></p>}
              {route.backends.length ? route.backends.map((backend, i) => <p key={`${backend.host}-${backend.port}-${i}`}>下游：<code>{backend.host} : {backend.port || '端口未知'}</code> · {backend.resolution}</p>) : <span>{route.upstream || route.directive === 'proxy_pass' ? '下游未解析' : '—'}</span>}
            </td><td><code>{route.file || '未提供'}</code></td></tr>)}
        </tbody></table></div>
      </details>}
    </section> : <p className="clb-note">未找到此域名及端口对应的 Nginx 配置；不能据此排除 Nginx 或其他转发。</p>}
  </div>;
}
function DnsListener({ instance, listener, domain }: { instance: Clb; listener: Clb['listeners'][number]; domain: string }) {
  const routes = dnsListenerRoutes(listener, domain);
  const showGroup = (groupId: string, port: number | null, label: string) => {
    const group = instance.groups.find(item => item.id === groupId);
    return <section><h3>{label} · {group?.name || '服务器组未找到'}</h3>{group ? group.servers.length ? group.servers.map((server, i) => <DnsBackend key={`${server.id}-${i}`} ip={server.ip} port={server.port ?? port} domain={domain} />) : <p>服务器组没有后端成员。</p> : <p>后端待核实。</p>}</section>;
  };
  return <section className="clb-routes"><h3>{listener.protocol} : {listener.port} · {listener.status}</h3>
    {listener.forwardPort != null ? <p>跳转到本实例 HTTPS : {listener.forwardPort}，目标监听器请另行核对。</p> : <>
      {routes.rules.map(rule => <div key={rule.id}>{showGroup(rule.groupId, null, `域名：${rule.domain || '任意'} · 路径：${rule.path || '未限定'}`)}</div>)}
      {routes.defaultPossible && showGroup(listener.groupId, listener.backendPort, '默认路由（其他规则未命中时）')}
    </>}
  </section>;
}
function DnsDomains({ rows, query, expanded, onQuery, onExpand, onCollapse, toggle }: { rows: DnsView[]; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  return <>
    <p className="clb-note">按启用 A / AAAA 地址及同一来源、同一公网/内网范围的 CNAME 链匹配 CLB、EIP。保留线路、权重和暂停状态；未执行实时 DNS 查询。内网地址关联还需核对实际 VPC 可见范围；监听端口与路径需结合下方配置判断。</p>
    <div className="clb-stats">{[['筛选记录', rows.length], ['已关联 CLB 记录', rows.filter(r => r.matches.length).length], ['已关联 EIP 记录', rows.filter(r => r.eipMatches.length).length], ['关联 EIP', new Set(rows.flatMap(r => r.eipMatches.map(m => m.eip.key))).size], ['关联 CLB', new Set(rows.flatMap(r => r.matches.map(m => m.instance.id))).size], ['未启用', rows.filter(r => r.record.status !== '启用').length]].map(([label, count]) => <section className="panel" key={label}><small>{label}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 DNS 域名" placeholder="搜索域名、解析值、CLB、状态或线路" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} />
    {!rows.length && <p>没有匹配的 DNS 记录</p>}
    {rows.map(({ record, matches, eipMatches, status, resolutions }) => <details className="panel clb-instance" key={record.id} open={!!expanded[record.id]} onToggle={e => toggle(record.id, e.currentTarget.open)}>
      <summary><strong>{record.name}</strong><code>{record.type} → {record.value}</code><span>{record.line} · {record.status} · {status}</span></summary>
      {expanded[record.id] && <>
      <p className="clb-note">TTL：{record.ttl ?? '未提供'} 秒 · 策略：{record.policy || '未提供'} · 权重：{record.weight ?? '未提供'} · 来源：{record.source}{record.row ? ` 第 ${record.row} 行` : ` · 记录 ID：${record.nativeId || record.id} · ${record.tenantName}`} · {record.observedAt ? formatBeijingTime(record.observedAt) : '静态导入'}{record.priority != null ? ` · 优先级：${record.priority}` : ''}{record.remark ? ` · 备注：${record.remark}` : ''}</p>
      {record.name.startsWith('*.') && <p className="clb-note">通配符记录，仅展示导出中的配置，不展开为具体子域名。</p>}
      {eipMatches.map(({ eip, paths }) => <section key={eip.key} className="clb-group"><strong>EIP · <a href={viewLink('eip', eip.id)}>{eip.ip}</a></strong>{paths.map((path, index) => <p className="clb-note" key={index}>解析链：{path.chain.join(' → ')}</p>)}</section>)}
      {eipMatches.some(({ eip }) => eip.bindingType === 'NAT网关') && <p className="clb-note">下方聚焦标准 HTTP 80 / HTTPS 443 公网入口，非标准端口可在 EIP 页面另行核实；DNS 本身不限定端口。</p>}
      {eipMatches.filter(({ eip }) => eip.bindingType === 'NAT网关').map(({ eip }) => {
        const mappings = eipIntegration.find(row => row.record.key === eip.key)?.mappings ?? [];
        const found = mappings.some(({ entry }) => ['80','443'].includes(entry.externalPort) && entry.protocol.toLowerCase() === 'tcp' && entry.status.toLowerCase() === 'available' && matches.some(({ instance }) => instance.ip === entry.internalIp && instance.listeners.some(listener => ['HTTP','HTTPS'].includes(listener.protocol.toUpperCase()) && String(listener.port) === entry.internalPort)));
        return !found ? <p className="clb-note" key={eip.key}>EIP {eip.ip} 的标准 HTTP/HTTPS 入口未找到可对应的 CLB 监听器，后续路径待核实。</p> : null;
      })}
      {matches.map(({ instance, paths }) => {
        const direct = resolutions.some(result => result.ip === instance.ip) || eipMatches.some(({ eip }) => /SLB|CLB/i.test(eip.bindingType) && eip.bindingId === instance.id);
        const mapped = eipMatches.flatMap(({ eip }) => eipIntegration.find(row => row.record.key === eip.key)?.mappings ?? []).filter(({ entry }) => entry.internalIp === instance.ip && ['80','443'].includes(entry.externalPort) && entry.protocol.toLowerCase() === 'tcp' && entry.status.toLowerCase() === 'available');
        const listeners = direct ? instance.listeners : instance.listeners.filter(listener => mapped.some(({ entry }) => String(listener.port) === entry.internalPort) && ['HTTP','HTTPS'].includes(listener.protocol.toUpperCase()));
        if (!direct && !listeners.length) return null;
        return <section key={resourceIdentity(instance)} className="clb-certificate-clb">
        <div className="clb-clb-heading"><strong><a href={viewLink('instances', instance.id)}>{instance.name}</a></strong><code>{instance.ip}</code><small>{instance.id} · {instance.status}</small></div>
        {direct && paths.map((path, i) => <p className="clb-note" key={i}>解析链：{path.chain.join(' → ')}</p>)}
        <p className="clb-note">DNS 不指定端口或路径；以下为配置候选，需结合请求端口、协议、路径及网络范围判断。</p>
        {listeners.length ? listeners.map(listener => <div key={`${listener.protocol}-${listener.port}`}>
          {!direct && mapped.filter(({ entry }) => String(listener.port) === entry.internalPort).map(({ entry }) => <p className="clb-note" key={entry.id}>DNAT：{entry.externalIp}:{entry.externalPort} → {entry.internalIp}:{entry.internalPort} · {entry.protocol.toUpperCase()}</p>)}
          <DnsListener instance={instance} listener={listener} domain={record.name} />
        </div>) : <p>未采集到监听器，后续路径待核实。</p>}
      </section>; })}
      {!matches.length && resolutions.map(({ ip }) => ecsInstances.filter(item => item.privateIps.includes(ip) || item.publicIps.includes(ip)).map(item => <div className="clb-group" key={resourceIdentity(item)}>ECS · {item.name} · <code>{ip}</code><p className="clb-note">DNS 未提供目标端口，后续路径待核实。</p></div>))}
      </>}
    </details>)}
  </>;
}
function DnsBrowser(props: Parameters<typeof DnsDomains>[0] & { tenant: string; scope?: 'public' | 'private' }) {
  const [chosenVisibility, setVisibility] = useState<'public' | 'private' | null>(null);
  const visibility = chosenVisibility ?? props.scope ?? 'public';
  const [selected, setSelected] = useState('');
  const staticZones: DnsZone[] = staticDns.zones.map(name => ({ id: `static:${name}`, name, visibility: 'public', recordCount: staticDns.records.filter(r => r.zone === name).length, recordsCollected: true, skipReason: '', records: staticDns.records.filter(r => r.zone === name) }));
  const zones = [...cloudDns.zones, ...staticZones];
  const available = zones.filter(zone => zone.visibility === visibility && (!props.tenant || zone.tenantId === props.tenant));
  const query = props.query.trim().toLowerCase();
  const zoneKey = (zone: DnsZone) => zone.resourceKey || zone.id;
  const zoneRows = (zone: DnsZone) => props.rows.filter(row => zone.tenantId
    ? row.record.tenantId === zone.tenantId && row.record.zoneId === zone.id && row.record.visibility === zone.visibility
    : !row.record.tenantId && row.record.zone === zone.name);
  const visible = available.filter(zone => !query || JSON.stringify({name: zone.name, tenantName: zone.tenantName, source: zone.tenantId ? '云采集' : '静态导入'}).toLowerCase().includes(query) || zoneRows(zone).length)
    .sort((a, b) => b.recordCount - a.recordCount);
  const populated = visible.filter(zone => zone.recordCount > 0);
  const empty = visible.filter(zone => zone.recordCount === 0);
  const zoneTable = (items: DnsZone[]) => <div className="panel dns-zone-list"><table><thead><tr><th>域名</th><th>租户 / 来源</th><th>解析记录数</th><th>采集时间</th><th>详情</th></tr></thead><tbody>{items.map(zone => <tr key={zoneKey(zone)}><td><button className="dns-zone-link" onClick={() => setSelected(zoneKey(zone))}>{zone.name}</button></td><td>{zone.tenantName || '静态导入 · 租户未归属'}</td><td>{zone.recordCount}</td><td>{formatBeijingTime(zone.observedAt, '静态导入')}</td><td>{zone.recordCount === 0 ? '已跳过（0 条记录）' : <button className="dns-zone-link" onClick={() => setSelected(zoneKey(zone))}>查看解析记录</button>}</td></tr>)}</tbody></table></div>;
  const current = available.find(zone => zoneKey(zone) === selected);
  return <>
    <p className="clb-note">云 DNS 按租户采集域名和解析记录。两份静态导出独立保留，共 {staticDns.records.length} 条，未归属当前租户。公网同名域名的云采集与静态导入分别展示。</p>
    <nav className="panel clb-tabs" aria-label="DNS 解析范围">{(['public', 'private'] as const).map(scope => <button key={scope} className={visibility === scope ? 'active' : ''} onClick={() => { setVisibility(scope); setSelected(''); }}>{scope === 'public' ? '公网权威解析' : '内网域名解析'}（{zones.filter(z => z.visibility === scope && (!props.tenant || z.tenantId === props.tenant)).length}）</button>)}</nav>
    {!cloudDns.available && <p className="clb-note">该版本尚未采集云 DNS，可在 Settings 单独同步 DNS。</p>}
    {current ? <><Button variant="outline" onClick={() => setSelected('')}>返回域名列表</Button><h2>{current.name}</h2><p className="clb-note">{current.tenantName || '静态导入 · 租户未归属'} · {current.recordCount} 条记录 · {formatBeijingTime(current.observedAt, '静态导入')}</p>{current.recordCount === 0 ? <p className="panel">解析记录数为 0，已跳过解析详情采集。</p> : <DnsDomains {...props} rows={zoneRows(current)} />}</> : <>
      <div className="panel clb-toolbar"><label htmlFor="dns-zone-search">搜索 DNS 域名<Input id="dns-zone-search" aria-label="搜索 DNS 域名" placeholder="搜索域名、租户或解析记录" value={props.query} onChange={event => props.onQuery(event.target.value)} /></label></div>
      {populated.length > 0 && zoneTable(populated)}
      {empty.length > 0 && <>
        {populated.length > 0 && <hr className="dns-zone-divider" />}
        <details key={`${props.tenant}/${visibility}`} className="panel dns-empty-zones">
          <summary>无解析记录的域名（{empty.length}）</summary>
          {zoneTable(empty)}
        </details>
      </>}
      {!visible.length && <p className="panel dns-zone-list">没有匹配的域名</p>}
    </>}
  </>;
}
function EipDomains({ rows, query, expanded, onQuery, onExpand, onCollapse, toggle }: { rows: EipView[]; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  return <>
    <p className="clb-note">EIP 为 {eip.snapshotDate} 静态导出，{eip.sources.map(source => `${source.owner} ${source.count} 条`).join('、')}。CLB 按绑定实例 ID 或相同公网 IP 关联；域名按 DNS 快照中的启用地址及同一来源范围的 CNAME 链关联。历史绑定与当前 CLB 快照可能不同；ECS 按绑定实例 ID 查找实例 IP，NAT 按采集到的 DNAT 规则匹配后端 IP 与端口；补充备注不作为映射依据。</p>
    <div className="clb-stats">{[['筛选 EIP', rows.length], ['已关联 CLB 的 EIP', rows.filter(row => row.matches.length).length], ['已关联 DNS 的 EIP', rows.filter(row => row.domains.length).length], ['关联 DNS 记录', new Set(rows.flatMap(row => row.domains.map(domain => domain.record.id))).size], ['NAT 网关 EIP', rows.filter(row => row.record.bindingType === 'NAT网关').length], ['未匹配当前 CLB', rows.filter(row => !row.matches.length).length]].map(([label, count]) => <section className="panel" key={label}><small>{label}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 EIP" placeholder="搜索来源、EIP、绑定实例、域名或备注" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} />
    {!rows.length && <p>没有匹配的 EIP</p>}
    {rows.map(({ record, matches, domains }) => <details key={record.key} className="panel clb-instance" open={!!expanded[record.key]} onToggle={event => toggle(record.key, event.currentTarget.open)}>
      <summary><strong>{record.ip}</strong><span>{record.owner} · {record.name === '-' ? '未命名 EIP' : record.name} · {record.bindingType} · {record.bandwidth} · {matches.length} 个 CLB · {domains.length} 条 DNS</span></summary>
      <EipSummary record={record} />
      <p className="clb-note">{record.network} · {record.billing} · {record.bandwidthPackage} · 分配时间：{record.allocatedAt} · 资源组：{record.resourceGroup}</p>
      <p className="clb-note">安全防护：{record.protection || '未提供'} · 标签：{record.tags || '无'} · IP 地址池：{record.poolId || '未提供'}</p>
      <p className="clb-note">来源：{record.source} 第 {record.row} 行 · 快照日期：{eip.snapshotDate}</p>
      {record.notes.length > 0 && <section className="clb-group"><strong>原始补充备注（未命名列，未验证）</strong>{record.notes.map(note => <p className="eip-source-note" key={note.column}>第 {note.column} 列：{note.value}</p>)}</section>}
      <section className="clb-group"><strong>关联 DNS 域名（{domains.length} 条）</strong>{domains.length ? domains.map(domain => <div key={domain.record.id}><p><a href={viewLink('dns', domain.record.name)}>{domain.record.name}</a> · {domain.record.type} · {domain.record.line}</p><small>{domain.eipMatches.find(match => match.eip.key === record.key)?.paths.map(path => path.chain.join(' → ')).join('；')}</small></div>) : <p className="clb-note">当前 DNS 快照中暂无启用记录指向此 EIP。</p>}</section>
      {!matches.length && <p className="clb-note">未匹配当前 CLB 快照；保留原始绑定信息。ECS 绑定不推断为 CLB；NAT 仅通过可用 DNAT 规则的后端 IP 匹配 CLB。</p>}
      {matches.map(({ instance, evidence }) => <section key={resourceIdentity(instance)} className="clb-certificate-clb"><div className="clb-clb-heading"><strong><a href={viewLink('instances', instance.id)}>{instance.name}</a></strong><code>{instance.ip}</code><small>{instance.id} · {evidence.join('、')}</small></div>{instance.listeners.map(listener => <ListenerDetails key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={`${record.key}-${instance.id}`} expanded={expanded} toggle={toggle} />)}</section>)}
    </details>)}
  </>;
}
function filterNat(query: string) {
  const q = query.trim().toLowerCase();
  return nat.gateways.flatMap(gateway => {
    const { entries, ...identity } = gateway;
    if (!q || JSON.stringify(identity).toLowerCase().includes(q)) return [gateway];
    const matched = entries.filter(entry => JSON.stringify({ entry,
      eips: eipIntegration.filter(row => row.record.bindingId === gateway.id && row.record.ip === entry.externalIp).map(row => ({ record: row.record, domains: row.domains.map(domain => domain.record) })),
      ecs: ecsInstances.filter(instance => instance.privateIps.includes(entry.internalIp)),
      clbs: clb.instances.filter(instance => instance.ip === entry.internalIp),
    }).toLowerCase().includes(q));
    return matched.length ? [{ ...gateway, entries: matched }] : [];
  });
}
function NatView({ rows, query, expanded, onQuery, onExpand, onCollapse, toggle }: { rows: NatGateway[]; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  const entries = rows.flatMap(gateway => gateway.entries);
  return <>
    <p className="clb-note">已配置租户及地域可见的 NAT 网关与 DNAT 配置 · 采集时间：{formatBeijingTime(nat.collectedAt, '未采集')}。按网关 ID 和公网 IP 精确关联静态 EIP；保留协议、端口范围、全部端口和规则状态。映射表示采集到的配置，不代表实际连通性。</p>
    {!nat.available && <section className="panel clb-empty"><h2>该版本尚未采集 NAT</h2><p>在 Settings 配置租户凭据并单独同步 NAT，或执行全量同步。</p><a href="/settings">打开 Settings</a></section>}
    <div className="clb-stats">{[['筛选 NAT 网关', rows.length], ['DNAT 规则', entries.length], ['公网入口 IP', new Set(entries.map(entry => entry.externalIp)).size], ['后端 IP', new Set(entries.map(entry => entry.internalIp)).size], ['可用规则', entries.filter(entry => entry.status.toLowerCase() === 'available').length], ['其他状态规则', entries.filter(entry => entry.status.toLowerCase() !== 'available').length]].map(([label, count]) => <section className="panel" key={label}><small>{label}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 NAT 映射" placeholder="搜索网关、EIP、后端 IP、端口、协议、实例或域名" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} />
    {nat.available && !rows.length && <p>没有匹配的 NAT 网关或 DNAT 映射</p>}
    {rows.map(gateway => <details key={resourceIdentity(gateway)} className="panel clb-instance" open={!!expanded[resourceIdentity(gateway)]} onToggle={event => toggle(resourceIdentity(gateway), event.currentTarget.open)}>
      <summary><strong>{gateway.name || gateway.id} · {tenantLabel(gateway)}</strong>{gateway.entries[0] && <code title="首条 DNAT 规则的后端 IP">{gateway.entries[0].internalIp}</code>}<span>{gateway.id} · {gateway.status} · {gateway.entries.length} 条 DNAT</span></summary>
      <p className="clb-note">VPC：{gateway.vpcId} · <a href={`https://vpc.console.aliyun.com/nat/${encodeURIComponent(gateway.region || 'cn-shanghai')}/nats/${encodeURIComponent(gateway.id)}/dnats`} target="_blank" rel="noopener noreferrer">打开阿里云 DNAT 控制台</a></p>
      {!gateway.entries.length && <p className="clb-note">该网关没有 DNAT 条目。</p>}
      {gateway.entries.map(entry => <section key={entry.id} className="nat-entry"><DnatMapping entry={entry} />
        <p className="clb-note">关联 EIP：{eip.records.filter(record => record.bindingType === 'NAT网关' && record.bindingId === gateway.id && record.ip === entry.externalIp).map(record => <a key={record.key} href={viewLink('eip', record.id)}>{record.owner} · {record.ip} </a>)}</p>
        <p className="clb-note"><a href={viewLink('dns', entry.externalIp)}>查看此公网 IP 的 DNS 记录（{dnsIntegration.filter(domain => domain.resolutions.some(result => result.ip === entry.externalIp)).length} 条）</a> · 域名解析不限定端口，需结合 DNAT 规则判断。</p>
      </section>)}
    </details>)}
  </>;
}
type View = 'dns' | 'certificates' | 'instances' | 'eip' | 'nat';
export default function ClbPage() {
  const [query, setQuery] = useState('');
  const [tenant, setTenant] = useState('');
  const [dnsScope, setDnsScope] = useState<'public' | 'private'>('public');
  const [tab, setTab] = useState<View>('certificates');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => { const params = new URLSearchParams(window.location.search); setQuery(params.get('q') ?? ''); setTenant(params.get('tenant') ?? ''); setDnsScope(params.get('dnsScope') === 'private' ? 'private' : 'public'); const view = params.get('view'); setTab(view === 'instances' || view === 'dns' || view === 'eip' || view === 'nat' ? view : 'certificates'); }, []);
  const normalizedQuery = query.trim().toLowerCase();
  const directlyMatched = new Set(filterClb(clb.instances, query).map(resourceIdentity));
  const rows = clb.instances.filter(instance => (!tenant || resourceTenantId(instance) === tenant) && (directlyMatched.has(resourceIdentity(instance))
    || eipIntegration.some(row => row.matches.some(match => match.instance.id === instance.id) && JSON.stringify(row.record).toLowerCase().includes(normalizedQuery))
    || dnsIntegration.some(row => row.matches.some(match => match.instance.id === instance.id) && JSON.stringify(row.record).toLowerCase().includes(normalizedQuery))));
  const eipRows = eipIntegration.filter(row => !normalizedQuery || JSON.stringify(row).toLowerCase().includes(normalizedQuery));
  const natRows = filterNat(query).filter(gateway => !tenant || resourceTenantId(gateway) === tenant);
  const dnsRows = dnsIntegration.filter(row => (!tenant || row.record.tenantId === tenant) && (!query.trim() || JSON.stringify(row).toLowerCase().includes(query.trim().toLowerCase())));
  const allKeys = tab === 'nat' ? natRows.map(resourceIdentity) : tab === 'eip' ? eipRows.flatMap(row => [row.record.key, ...row.matches.flatMap(match => match.instance.listeners.map(listener => `${row.record.key}-${match.instance.id}-${listener.protocol}-${listener.port}`))]) : tab === 'dns' ? dnsRows.flatMap(r => [r.record.id, ...r.matches.flatMap(m => m.instance.listeners.map(l => `${r.record.id}-${m.instance.id}-${l.protocol}-${l.port}`))]) : tab === 'instances' ? rows.flatMap(i => [resourceIdentity(i), ...i.listeners.map(l => `${resourceIdentity(i)}-${l.protocol}-${l.port}`)]) : certificatesFromClb(clb.instances.filter(i => !tenant || resourceTenantId(i) === tenant), query).flatMap(c => [c.key, ...c.instances.flatMap(i => i.listeners.map(l => `${c.key}-${i.instance.id}-${l.protocol}-${l.port}`))]);
  const onQuery = (value: string) => { setQuery(value); const params = new URLSearchParams(window.location.search); if (value) params.set('q', value); else params.delete('q'); window.history.replaceState(null, '', `${window.location.pathname}?${params}`); };
  const toggle = (key: string, open: boolean) => setExpanded(prev => ({ ...prev, [key]: open }));
  const expandAll = () => setExpanded(Object.fromEntries(allKeys.map(key => [key, true])));
  const changeTab = (next: View) => { setTab(next); setExpanded({}); const params = new URLSearchParams(window.location.search); params.set('view', next); window.history.replaceState(null, '', `${window.location.pathname}?${params}`); };
  return <Shell active="clb"><main className="clb-page"><div className="page-heading"><div><h1>CLB 全景</h1><p>已配置地域 · 全部 {clb.instances.length} 个 CLB · DNS / EIP / ECS / NAT 数据联动</p></div></div>
    <TenantFilter rows={[...clb.instances, ...nat.gateways, ...cloudDns.zones]} value={tenant} onChange={value => { setTenant(value); setExpanded({}); const params = new URLSearchParams(window.location.search); if (value) params.set('tenant', value); else params.delete('tenant'); window.history.replaceState(null, '', `${window.location.pathname}?${params}`); }} />
    {(tab === 'nat' ? nat.tenants ?? [] : tab === 'dns' ? cloudDns.tenants ?? [] : tab === 'instances' || tab === 'certificates' ? clb.tenants ?? [] : []).filter(t => !tenant || t.tenantId === tenant).map(t => <p key={`${t.tenantId}/${t.region}`} className="clb-note">{t.tenantName} · {t.region} · 采集：{formatBeijingTime(t.collectedAt, '时间未知')}</p>)}
    {!clb.available && <section className="panel clb-empty"><h2>该版本尚未采集 CLB</h2><p>在 Settings 配置租户凭据并同步，或切换到包含 CLB 的版本。</p><a href="/settings">打开 Settings</a></section>}<><nav className="panel clb-tabs" aria-label="CLB 视图"><button className={tab === 'certificates' ? 'active' : ''} onClick={() => changeTab('certificates')}>域名证书</button><button className={tab === 'instances' ? 'active' : ''} onClick={() => changeTab('instances')}>CLB 实例（{clb.instances.length}）</button><button className={tab === 'dns' ? 'active' : ''} onClick={() => changeTab('dns')}>DNS 域名（{dns.records.length}）</button><button className={tab === 'eip' ? 'active' : ''} onClick={() => changeTab('eip')}>弹性公网 IP（{eip.records.length}）</button><button className={tab === 'nat' ? 'active' : ''} onClick={() => changeTab('nat')}>NAT 网关（{nat.available ? nat.gateways.length : '未采集'}）</button></nav>{tab === 'nat' ? <NatView rows={natRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'eip' ? <EipDomains rows={eipRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'dns' ? <DnsBrowser scope={dnsScope} tenant={tenant} rows={dnsRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'certificates' ? <CertificateView tenant={tenant} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : <InstanceView rows={rows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} />}</>
  </main></Shell>;
}
