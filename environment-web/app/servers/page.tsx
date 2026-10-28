'use client';
import { Cpu, MemoryStick, Server } from 'lucide-react';
import { Shell } from '../page';
import { instances, tenantSnapshots } from '@/lib/ecs';
import { localServers, summarize as summarizeLocal } from '@/lib/local-servers';
import { capacity, partitionCloud } from '@/lib/server-resources';
import './servers.css';
const colors = ['#328979', '#6286cc', '#e0a15a', '#a0a9a5'];
const format = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
export default function ServerOverview() {
  const cloud = partitionCloud(instances, tenantSnapshots);
  const local = summarizeLocal(localServers);
  const sites = [
    { name: '阿里云-上海修平', ...capacity(cloud.xiuping), href: '/servers/xiuping' },
    { name: '阿里云- Thomascookjv', ...capacity(cloud.thomascookjv), href: '/servers/thomascookjv' },
    { name: '本地机房', count: local.count, cpu: local.cpu, memory: local.memory, href: '/datacenter' },
    ...(cloud.unknown.length ? [{ name: '云账号待确认', ...capacity(cloud.unknown), href: '/aliyun/instances' }] : []),
  ];
  const totals = { count: sites.reduce((n, s) => n + s.count, 0), cpu: sites.reduce((n, s) => n + s.cpu, 0), memory: sites.reduce((n, s) => n + s.memory, 0) };
  const metrics = [
    { key: 'count' as const, label: '服务器台数', unit: '台', icon: Server },
    { key: 'cpu' as const, label: 'CPU 总量', unit: 'vCPU', icon: Cpu },
    { key: 'memory' as const, label: '内存总量', unit: 'GiB', icon: MemoryStick },
  ];
  return <Shell active="server-overview"><main className="server-overview">
    <div className="page-heading"><div><div className="eyebrow">SERVER RESOURCES</div><h1>服务器资源总览</h1><p>汇总阿里云 ECS 与本地机房服务器资源</p></div></div>
    <div className="server-overview-metrics">{metrics.map(m => <section className="panel server-overview-metric" key={m.key}><span>{m.label}<m.icon size={18} /></span><strong>{format(totals[m.key])}<small>{m.unit}</small></strong></section>)}</div>
    <section className="panel server-overview-section"><h2>机房资源分布</h2><p>按服务器台数、CPU 和内存分别统计</p><div className="server-pies">{metrics.map(m => {
      let offset = 0;
      const stops = sites.map((site, index) => { const start = offset; offset += totals[m.key] ? site[m.key] / totals[m.key] * 100 : 0; return `${colors[index]} ${start}% ${offset}%`; });
      return <div className="server-pie-card" key={m.key}><h3>{m.label}</h3><div className="server-pie" aria-hidden="true" style={{ background: totals[m.key] ? `conic-gradient(${stops.join(',')})` : '#e8eeeb' }}><div><strong>{format(totals[m.key])}</strong><small>{m.unit}</small></div></div><div className="server-legend">{sites.map((s, i) => <div key={s.name}><span><i style={{ background: colors[i] }} />{s.name}</span><b>{format(s[m.key])} {m.unit}</b></div>)}</div></div>;
    })}</div></section>
    <section className="panel server-overview-section"><h2>各机房汇总</h2><div className="server-site-grid">{sites.map((site, i) => <a className="server-site" href={site.href} key={site.name}><h3><i style={{ background: colors[i] }} />{site.name}</h3><div><span>服务器台数</span><strong>{format(site.count)} <small>台</small></strong></div><div><span>CPU 总量</span><strong>{format(site.cpu)} <small>vCPU</small></strong></div><div><span>内存总量</span><strong>{format(site.memory)} <small>GiB</small></strong></div></a>)}</div></section>
    <p className="server-overview-note">本地机房按现有清单 IP 计数，CPU 和内存仅包含已采集规格的服务器（{local.collected} / {local.count} 台）；内存使用现有标称容量口径。云资源按 ECS 实例计数。{cloud.unknown.length > 0 && ` ${cloud.unknown.length} 台云服务器的账号名称无法确认，已单独列出并计入总量；请在租户设置中核对名称并同步。`}</p>
  </main></Shell>;
}
