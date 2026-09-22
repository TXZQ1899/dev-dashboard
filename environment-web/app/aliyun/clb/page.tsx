'use client';
import { useEffect, useState } from 'react';
import { Shell } from '@/app/page';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { clb, certificatesFromClb, filterClb, referencedGroups, summarizeClb, type Group, type Clb } from '@/lib/clb';
import { instances as ecsInstances, fetchedAt as ecsFetchedAt } from '@/lib/ecs';
import { jumpserver } from '@/lib/jumpserver';
import './clb.css';
import { dns, integrateDns, type DnsView } from '@/lib/dns';
import { eip, integrateEip, type Eip, type EipView } from '@/lib/eip';
import { nat, portLabel, type DnatEntry, type NatGateway } from '@/lib/nat';

const dnsIntegration = integrateDns(clb.instances, dns.records, eip.records, nat);
const eipIntegration = integrateEip(clb.instances, dnsIntegration, eip.records, ecsInstances, nat);
const viewLink = (view: string, query: string) => `/aliyun/clb?view=${view}&q=${encodeURIComponent(query)}`;
function EipSummary({ record }: { record: Eip }) {
  return <div className="clb-group"><strong><a href={viewLink('eip', record.id)}>{record.ip}</a> · {record.owner} · {record.name === '-' ? '未命名 EIP' : record.name}</strong>
    <small>{record.id} · {record.status} · {record.bandwidth}</small>
    <p>静态绑定：{record.bindingType || '未提供'} · {record.bindingName || '未提供'} · {record.bindingId || '未提供'}</p>
    <EipBackends record={record} />
  </div>;
}
function DnatMapping({ entry }: { entry: DnatEntry }) {
  const targets = clb.instances.filter(instance => instance.ip === entry.internalIp);
  return <div className="clb-group"><strong>{entry.protocol.toUpperCase()} · {entry.name || entry.id}</strong>
    <p className="nat-mapping"><code>{entry.externalIp} : {portLabel(entry.externalPort)}</code> → <code>{entry.internalIp} : {portLabel(entry.internalPort)}</code></p>
    <small>规则状态：{entry.status} · {entry.id} · DNAT 表：{entry.tableId}</small>
    <ResourceLinks ip={entry.internalIp} showUnlinked={!targets.length} />
    {targets.map(instance => <p key={instance.id}><a href={viewLink('instances', instance.id)}>CLB · {instance.name} · {instance.id}</a></p>)}
  </div>;
}
function EipBackends({ record }: { record: Eip }) {
  const row = eipIntegration.find(row => row.record.key === record.key);
  if (!row) return null;
  if (record.bindingType === 'ECS实例') return <section><strong>绑定 ECS 的实例 IP</strong>
    <small>按绑定实例 ID 精确匹配 · ECS 快照：{ecsFetchedAt || '未提供'}</small>
    {row.ecsMatches.length ? row.ecsMatches.map(instance => <div key={instance.id}><p><a href={`/aliyun/instances?q=${encodeURIComponent(instance.id)}`}>{instance.name} · {instance.id}</a></p><p>内网 IP：{instance.privateIps.join('、') || '未提供'}；公网 IP：{instance.publicIps.join('、') || '未提供'}</p><small>ECS 绑定记录未提供端口转换规则。</small></div>) : <p className="clb-note">当前 ECS 快照中未找到绑定实例 ID：{record.bindingId}。</p>}
  </section>;
  if (record.bindingType !== 'NAT网关') return null;
  return <section><strong>DNAT 后端 IP / 端口映射（{row.mappings.length} 条）</strong>
    {!nat.available ? <p className="clb-note">该版本尚未采集 NAT。<a href="/settings">打开 Settings 同步 NAT</a></p>
      : !row.natGateway ? <p className="clb-note">当前 NAT 快照未包含网关 {record.bindingId}，无法判断其 DNAT 映射。</p>
      : <><small>NAT 快照：{nat.collectedAt} · <a href={viewLink('nat', record.bindingId)}>{row.natGateway.name || record.bindingId}</a></small>{row.mappings.length ? row.mappings.map(({ entry }) => <DnatMapping key={entry.id} entry={entry} />) : <p className="clb-note">已采集该网关，但此 EIP 没有 DNAT 条目；SNAT 出站地址可能没有入站映射。</p>}</>}
  </section>;
}
function InstanceEips({ instance }: { instance: Clb }) {
  const records = eipIntegration.filter(row => row.matches.some(match => match.instance.id === instance.id));
  return <section className="clb-group"><strong>静态 EIP 关联（{records.length} 条）</strong>{records.length ? records.map(({ record, matches }) => <div key={record.key}><EipSummary record={record} /><small>关联依据：{matches.find(match => match.instance.id === instance.id)?.evidence.join('、')}</small></div>) : <p className="clb-note">三份 EIP 导出中未匹配此实例。</p>}</section>;
}
function InstanceDns({ instance }: { instance: Clb }) {
  const records = dnsIntegration.filter(row => row.matches.some(match => match.instance.id === instance.id));
  return <section className="clb-group"><strong>静态 DNS 关联（{records.length} 条）</strong>
    {records.length ? records.map(({ record, matches }) => <div key={record.id}><p><strong><a href={viewLink('dns', record.name)}>{record.name}</a></strong> · {record.type} · {record.line} · TTL {record.ttl} 秒</p><small>{matches.find(match => match.instance.id === instance.id)?.paths.map(path => `${path.chain.join(' → ')}${path.via ? `；经 ${path.via}` : ''}`).join('；')}</small></div>) : <p className="clb-note">这两份 DNS 导出中暂无启用记录指向此实例；保留全部监听器与后端配置。</p>}
  </section>;
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
function Backends({ group, port }: { group?: Group; port?: number | null }) {
  if (!group) return <p className="clb-warning">服务器组数据缺失</p>;
  return <div className="clb-group"><strong>{group.name}</strong><small>{group.id === 'default' ? '默认服务器组' : group.id}</small>
    {group.servers.length ? <div className="clb-backends">{group.servers.map((s,index) => <div key={`${s.id}-${s.port}-${index}`}>
      <code>{s.ip || 'IP 未提供'} : {s.port ?? port ?? '由监听器指定'}</code>{s.ip ? <ResourceLinks ip={s.ip} /> : null}<small>{s.id} · {s.type} · 权重 {s.weight ?? '未提供'}</small>
    </div>)}</div> : <p className="clb-warning">无后端成员</p>}
  </div>;
}
function Routes({ instance, listener }: { instance: Clb; listener: Clb['listeners'][number] }) {
  if (listener.forwardPort != null) return <p>HTTP 跳转 → 本实例 HTTPS : {listener.forwardPort}</p>;
  return <div className="clb-routes"><section><h3>默认路由</h3><Backends group={instance.groups.find(g => g.id === listener.groupId)} port={listener.backendPort} /></section>
    {listener.rules.map(rule => <section key={rule.id}><h3>域名：{rule.domain || '任意'} · 路径：{rule.path || '未限定'}</h3><small>{rule.id}</small><Backends group={instance.groups.find(g => g.id === rule.groupId)} /></section>)}
  </div>;
}
function ListenerDetails({ instance, listener, keyPrefix, expanded, toggle }: { instance: Clb; listener: Clb['listeners'][number]; keyPrefix: string; expanded: Record<string, boolean>; toggle: (key: string, open: boolean) => void }) {
  const key = `${keyPrefix}-${listener.protocol}-${listener.port}`;
  return <details className="clb-listener" open={!!expanded[key]} onToggle={e => { e.stopPropagation(); toggle(key, e.currentTarget.open); }}><summary><strong>{listener.protocol} : {listener.port}</strong><span>{listener.status} · {listener.rules.length} 条规则</span></summary><p className="clb-note">{listener.description} · 健康检查：{listener.healthCheck || '未提供'}</p>{listener.certificates.length > 0 && <p className="clb-note">绑定证书：{listener.certificates.map(c => c.domain || c.commonName || c.name || c.id).join('、')}</p>}<Routes instance={instance} listener={listener} /></details>;
}
function Toolbar({ label, placeholder, query, onQuery, onExpand, onCollapse }: { label: string; placeholder: string; query: string; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void }) {
  return <section className="panel clb-toolbar"><Input aria-label={label} placeholder={placeholder} value={query} onChange={e => onQuery(e.target.value)} /><Button variant="outline" onClick={onExpand}>展开全部</Button><Button variant="outline" onClick={onCollapse}>折叠全部</Button></section>;
}
function InstanceView({ rows, query, expanded, toggle, onQuery, onExpand, onCollapse }: { rows: Clb[]; query: string; expanded: Record<string, boolean>; toggle: (key: string, open: boolean) => void; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void }) {
  const stats = summarizeClb(rows);
  return <><div className="clb-stats">{[['CLB 实例', stats.instances], ['监听入口', stats.listeners], ['去重后端', stats.backends], ['已关联后端', stats.used], ['后端关联记录', stats.memberships], ['无后端监听器', stats.empty]].map(([name,count]) => <section className="panel" key={name}><small>{name}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 CLB" placeholder="搜索实例名称、IP、端口、域名或服务器组" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} /><p className="clb-note">CLB 实例页展示全部采集到的 CLB，不要求实例必须配置证书。DNS 为两份静态导出的补充关联，无 DNS 匹配的实例仍完整展示。</p>
    {!rows.length && <p>没有匹配的实例</p>}{rows.map(instance => { const used = referencedGroups(instance); const unused = instance.groups.filter(g => !used.has(g.id) && (g.id !== 'default' || g.servers.length)); return <details className="panel clb-instance" key={instance.id} open={!!expanded[instance.id]} onToggle={e => toggle(instance.id, e.currentTarget.open)}>
      <summary><strong>{instance.name}</strong><code>{instance.ip}</code><span>{instance.addressType === 'intranet' ? '内网' : '公网'} · {instance.status} · {instance.listeners.length} 个监听器</span></summary><small className="clb-id">{instance.id}</small><div className="clb-branches">
        <InstanceEips instance={instance} /><InstanceDns instance={instance} />{instance.listeners.map(listener => <ListenerDetails key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={instance.id} expanded={expanded} toggle={toggle} />)}
        {unused.length > 0 && <details open={!!expanded[`${instance.id}-unused`]} onToggle={e => { e.stopPropagation(); toggle(`${instance.id}-unused`, e.currentTarget.open); }}><summary>未被监听器或规则引用的服务器组（{unused.length}）</summary>{unused.map(group => <Backends key={group.id} group={group} />)}</details>}
      </div></details>; })}</>;
}
function CertificateView({ query, expanded, onQuery, onExpand, onCollapse, toggle }: { query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  const rows = certificatesFromClb(clb.instances, query);
  const allCertificates = certificatesFromClb(clb.instances);
  const certificateInstances = new Set(allCertificates.flatMap(c => c.instances.map(i => i.instance.id)));
  return <><div className="clb-stats">{[['域名证书', rows.length], ['全部 CLB 实例', clb.instances.length], ['证书关联 CLB', certificateInstances.size], ['未配置证书', clb.instances.filter(i => !certificateInstances.has(i.id)).length], ['ECS 关联 IP', new Set(ecsInstances.flatMap(i => i.privateIps)).size], ['JumpServer IP', new Set(jumpserver.assets.map(a => a.ip)).size]].map(([name,count]) => <section className="panel" key={name}><small>{name}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索域名证书" placeholder="搜索证书、域名、CLB、IP、端口或路由" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} /><p className="clb-note">证书页只展示实际绑定证书的 CLB；当前证书关联 {certificateInstances.size} 个实例，全部 CLB 为 {clb.instances.length} 个，差异为未配置证书或没有可识别证书绑定的实例。</p>
    {!rows.length && <p>没有匹配的证书，或当前快照尚未采集 CLB。</p>}{rows.map(certificate => <details className="panel clb-instance" key={certificate.key} open={!!expanded[certificate.key]} onToggle={e => toggle(certificate.key, e.currentTarget.open)}>
      <summary><strong>{certificate.certificate.domain || certificate.certificate.commonName || '未命名证书'}</strong><span>{certificate.certificate.name || certificate.certificate.id} · 到期：{certificate.certificate.expiresAt || '未提供'} · {certificate.instances.length} 个 CLB</span></summary><div className="clb-branches">
        {certificate.instances.map(({ instance, listeners }) => <section key={instance.id} className="clb-certificate-clb"><div className="clb-clb-heading"><strong>{instance.name}</strong><code>{instance.ip}</code><small>{instance.addressType === 'intranet' ? '内网' : '公网'} · {instance.status} · {instance.id}</small></div><InstanceEips instance={instance} /><InstanceDns instance={instance} />{listeners.map(listener => <ListenerDetails key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={`${certificate.key}-${instance.id}`} expanded={expanded} toggle={toggle} />)}</section>)}
      </div></details>)}</>;
}
function DnsDomains({ rows, query, expanded, onQuery, onExpand, onCollapse, toggle }: { rows: DnsView[]; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  return <>
    <p className="clb-note">仅整合 folidaymall.com、fosunholiday.com 两份静态导出，共 {dns.records.length} 条记录。按启用 A / AAAA 的地址及表内 CNAME 链匹配 CLB IP、EIP，并通过 EIP 静态绑定 ID 关联 CLB。保留线路、权重和暂停状态；未执行实时 DNS 查询。监听端口与路径需结合下方配置判断。</p>
    <div className="clb-stats">{[['筛选记录', rows.length], ['已关联 CLB 记录', rows.filter(r => r.matches.length).length], ['已关联 EIP 记录', rows.filter(r => r.eipMatches.length).length], ['关联 EIP', new Set(rows.flatMap(r => r.eipMatches.map(m => m.eip.key))).size], ['关联 CLB', new Set(rows.flatMap(r => r.matches.map(m => m.instance.id))).size], ['未启用', rows.filter(r => r.record.status !== '启用').length]].map(([label, count]) => <section className="panel" key={label}><small>{label}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 DNS 域名" placeholder="搜索域名、解析值、CLB、状态或线路" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} />
    {!rows.length && <p>没有匹配的 DNS 记录</p>}
    {rows.map(({ record, matches, eipMatches, status }) => <details className="panel clb-instance" key={record.id} open={!!expanded[record.id]} onToggle={e => toggle(record.id, e.currentTarget.open)}>
      <summary><strong>{record.name}</strong><code>{record.type} → {record.value}</code><span>{record.line} · {record.status} · {status}</span></summary>
      <p className="clb-note">TTL：{record.ttl ?? '未提供'} 秒 · 策略：{record.policy || '未提供'} · 权重：{record.weight ?? '未提供'} · 来源：{record.source} 第 {record.row} 行{record.remark ? ` · 备注：${record.remark}` : ''}</p>
      {record.name.startsWith('*.') && <p className="clb-note">通配符记录，仅展示导出中的配置，不展开为具体子域名。</p>}
      {eipMatches.map(({ eip, paths }) => <section key={eip.key}><EipSummary record={eip} />{paths.map((path, index) => <p className="clb-note" key={index}>解析链：{path.chain.join(' → ')}</p>)}</section>)}
      {matches.map(({ instance, paths }) => <section key={instance.id} className="clb-certificate-clb">
        <div className="clb-clb-heading"><strong><a href={viewLink('instances', instance.id)}>{instance.name}</a></strong><code>{instance.ip}</code><small>{instance.id} · {instance.status}</small></div>
        {paths.map((path, i) => <p className="clb-note" key={i}>解析链：{path.chain.join(' → ')}{path.via ? `；经 ${path.via}` : ''}</p>)}
        <p className="clb-note">以下为该实例的监听器配置，域名解析不限定端口；路由列出路径条件及默认回退，证书绑定不代表 DNS 指向。</p>
        {instance.listeners.map(listener => <ListenerDetails key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={`${record.id}-${instance.id}`} expanded={expanded} toggle={toggle} />)}
      </section>)}
    </details>)}
  </>;
}
function EipDomains({ rows, query, expanded, onQuery, onExpand, onCollapse, toggle }: { rows: EipView[]; query: string; expanded: Record<string, boolean>; onQuery: (value: string) => void; onExpand: () => void; onCollapse: () => void; toggle: (key: string, open: boolean) => void }) {
  return <>
    <p className="clb-note">EIP 为 {eip.snapshotDate} 静态导出，{eip.sources.map(source => `${source.owner} ${source.count} 条`).join('、')}。CLB 按绑定实例 ID 或相同公网 IP 关联；域名按两份 DNS 表中的启用地址及 CNAME 链关联。历史绑定与当前 CLB 快照可能不同；ECS 按绑定实例 ID 查找实例 IP，NAT 按采集到的 DNAT 规则匹配后端 IP 与端口；补充备注不作为映射依据。</p>
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
      <section className="clb-group"><strong>关联 DNS 域名（{domains.length} 条）</strong>{domains.length ? domains.map(domain => <div key={domain.record.id}><p><a href={viewLink('dns', domain.record.name)}>{domain.record.name}</a> · {domain.record.type} · {domain.record.line}</p><small>{domain.eipMatches.find(match => match.eip.key === record.key)?.paths.map(path => path.chain.join(' → ')).join('；')}</small></div>) : <p className="clb-note">两份静态 DNS 导出中暂无启用记录指向此 EIP。</p>}</section>
      {!matches.length && <p className="clb-note">未匹配当前 CLB 快照；保留原始绑定信息。ECS 绑定不推断为 CLB；NAT 仅通过可用 DNAT 规则的后端 IP 匹配 CLB。</p>}
      {matches.map(({ instance, evidence }) => <section key={instance.id} className="clb-certificate-clb"><div className="clb-clb-heading"><strong><a href={viewLink('instances', instance.id)}>{instance.name}</a></strong><code>{instance.ip}</code><small>{instance.id} · {evidence.join('、')}</small></div>{instance.listeners.map(listener => <ListenerDetails key={`${listener.protocol}-${listener.port}`} instance={instance} listener={listener} keyPrefix={`${record.key}-${instance.id}`} expanded={expanded} toggle={toggle} />)}</section>)}
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
    <p className="clb-note">上海区域当前账号可见的 NAT 网关与 DNAT 配置 · 采集时间：{nat.collectedAt || '未采集'}。按网关 ID 和公网 IP 精确关联静态 EIP；保留协议、端口范围、全部端口和规则状态。映射表示采集到的配置，不代表实际连通性。</p>
    {!nat.available && <section className="panel clb-empty"><h2>该版本尚未采集 NAT</h2><p>在 Settings 使用统一阿里云 Cookie 单独同步 NAT，或执行全量同步。</p><a href="/settings">打开 Settings</a></section>}
    <div className="clb-stats">{[['筛选 NAT 网关', rows.length], ['DNAT 规则', entries.length], ['公网入口 IP', new Set(entries.map(entry => entry.externalIp)).size], ['后端 IP', new Set(entries.map(entry => entry.internalIp)).size], ['可用规则', entries.filter(entry => entry.status.toLowerCase() === 'available').length], ['其他状态规则', entries.filter(entry => entry.status.toLowerCase() !== 'available').length]].map(([label, count]) => <section className="panel" key={label}><small>{label}</small><strong>{count}</strong></section>)}</div>
    <Toolbar label="搜索 NAT 映射" placeholder="搜索网关、EIP、后端 IP、端口、协议、实例或域名" query={query} onQuery={onQuery} onExpand={onExpand} onCollapse={onCollapse} />
    {nat.available && !rows.length && <p>没有匹配的 NAT 网关或 DNAT 映射</p>}
    {rows.map(gateway => <details key={gateway.id} className="panel clb-instance" open={!!expanded[gateway.id]} onToggle={event => toggle(gateway.id, event.currentTarget.open)}>
      <summary><strong>{gateway.name || gateway.id}</strong><span>{gateway.id} · {gateway.status} · {gateway.entries.length} 条 DNAT</span></summary>
      <p className="clb-note">VPC：{gateway.vpcId} · <a href={`https://vpc.console.aliyun.com/nat/cn-shanghai/nats/${encodeURIComponent(gateway.id)}/dnats`} target="_blank" rel="noopener noreferrer">打开阿里云 DNAT 控制台</a></p>
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
  const [tab, setTab] = useState<View>('certificates');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => { const params = new URLSearchParams(window.location.search); setQuery(params.get('q') ?? ''); const view = params.get('view'); setTab(view === 'instances' || view === 'dns' || view === 'eip' || view === 'nat' ? view : 'certificates'); }, []);
  const normalizedQuery = query.trim().toLowerCase();
  const directlyMatched = new Set(filterClb(clb.instances, query).map(instance => instance.id));
  const rows = clb.instances.filter(instance => directlyMatched.has(instance.id)
    || eipIntegration.some(row => row.matches.some(match => match.instance.id === instance.id) && JSON.stringify(row.record).toLowerCase().includes(normalizedQuery))
    || dnsIntegration.some(row => row.matches.some(match => match.instance.id === instance.id) && JSON.stringify(row.record).toLowerCase().includes(normalizedQuery)));
  const eipRows = eipIntegration.filter(row => !normalizedQuery || JSON.stringify(row).toLowerCase().includes(normalizedQuery));
  const natRows = filterNat(query);
  const dnsRows = dnsIntegration.filter(row => !query.trim() || JSON.stringify(row).toLowerCase().includes(query.trim().toLowerCase()));
  const allKeys = tab === 'nat' ? natRows.map(gateway => gateway.id) : tab === 'eip' ? eipRows.flatMap(row => [row.record.key, ...row.matches.flatMap(match => match.instance.listeners.map(listener => `${row.record.key}-${match.instance.id}-${listener.protocol}-${listener.port}`))]) : tab === 'dns' ? dnsRows.flatMap(r => [r.record.id, ...r.matches.flatMap(m => m.instance.listeners.map(l => `${r.record.id}-${m.instance.id}-${l.protocol}-${l.port}`))]) : tab === 'instances' ? rows.flatMap(i => [i.id, ...i.listeners.map(l => `${i.id}-${l.protocol}-${l.port}`)]) : certificatesFromClb(clb.instances, query).flatMap(c => [c.key, ...c.instances.flatMap(i => i.listeners.map(l => `${c.key}-${i.instance.id}-${l.protocol}-${l.port}`))]);
  const onQuery = (value: string) => { setQuery(value); const params = new URLSearchParams(window.location.search); if (value) params.set('q', value); else params.delete('q'); window.history.replaceState(null, '', `${window.location.pathname}?${params}`); };
  const toggle = (key: string, open: boolean) => setExpanded(prev => ({ ...prev, [key]: open }));
  const expandAll = () => setExpanded(Object.fromEntries(allKeys.map(key => [key, true])));
  const changeTab = (next: View) => { setTab(next); setExpanded({}); const params = new URLSearchParams(window.location.search); params.set('view', next); window.history.replaceState(null, '', `${window.location.pathname}?${params}`); };
  return <Shell active="clb"><main className="clb-page"><div className="page-heading"><div><h1>CLB 全景</h1><p>上海区域 · 全部 {clb.instances.length} 个 CLB · DNS / EIP / ECS / NAT 数据联动</p></div></div>
    {!clb.available && <section className="panel clb-empty"><h2>该版本尚未采集 CLB</h2><p>在 Settings 保存统一阿里云 Cookie 并同步，或切换到包含 CLB 的版本。</p><a href="/settings">打开 Settings</a></section>}<><nav className="panel clb-tabs" aria-label="CLB 视图"><button className={tab === 'certificates' ? 'active' : ''} onClick={() => changeTab('certificates')}>域名证书</button><button className={tab === 'instances' ? 'active' : ''} onClick={() => changeTab('instances')}>CLB 实例（{clb.instances.length}）</button><button className={tab === 'dns' ? 'active' : ''} onClick={() => changeTab('dns')}>DNS 域名（{dns.records.length}）</button><button className={tab === 'eip' ? 'active' : ''} onClick={() => changeTab('eip')}>弹性公网 IP（{eip.records.length}）</button><button className={tab === 'nat' ? 'active' : ''} onClick={() => changeTab('nat')}>NAT 网关（{nat.available ? nat.gateways.length : '未采集'}）</button></nav>{tab === 'nat' ? <NatView rows={natRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'eip' ? <EipDomains rows={eipRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'dns' ? <DnsDomains rows={dnsRows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : tab === 'certificates' ? <CertificateView query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} /> : <InstanceView rows={rows} query={query} expanded={expanded} onQuery={onQuery} onExpand={expandAll} onCollapse={() => setExpanded({})} toggle={toggle} />}</>
  </main></Shell>;
}
