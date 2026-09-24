'use client';
import { useState } from 'react';
import { Cpu, MemoryStick, Server, Boxes } from 'lucide-react';
import { Shell } from '../page';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  localServers,
  fetchedAt,
  inventoryDateStr,
  summarize,
  filterServers,
  compareText,
  type LocalServer,
} from '@/lib/local-servers';
import './datacenter.css';

function loginLabel(r: LocalServer) {
  if (r.loginStatus === 'can_login') return '可登录';
  if (r.loginStatus === 'cannot_login') return '无法登录';
  if (r.cpu > 0) return '可登录';
  return '未知';
}

function isCollected(r: LocalServer) {
  return r.specsCollected ?? r.cpu > 0;
}

function Metrics({ rows }: { rows: LocalServer[] }) {
  const s = summarize(rows);
  return (
    <div className="dc-metrics">
      <section className="panel dc-metric">
        <span>
          服务器总数 <Server size={18} />
        </span>
        <strong>
          {s.count.toLocaleString('zh-CN')}
          <small>台</small>
        </strong>
        <p>DevOps 部署 IP 减去阿里云 ECS · 已采集规格 {s.collected} 台</p>
      </section>
      <section className="panel dc-metric">
        <span>
          CPU 总量 <Cpu size={18} />
        </span>
        <strong>
          {s.cpu.toLocaleString('zh-CN')}
          <small>vCPU</small>
        </strong>
        <p>已采集规格的配置核数合计</p>
      </section>
      <section className="panel dc-metric">
        <span>
          内存总量 <MemoryStick size={18} />
        </span>
        <strong>
          {s.memory.toLocaleString('zh-CN')}
          <small>GiB</small>
        </strong>
        <p>已采集机器的标称容量合计（对齐标准档位）</p>
      </section>
      <section className="panel dc-metric">
        <span>
          部署应用 <Boxes size={18} />
        </span>
        <strong>
          {s.apps.toLocaleString('zh-CN')}
          <small>个</small>
        </strong>
        <p>本地机房部署的去重应用总数</p>
      </section>
    </div>
  );
}

export default function DatacenterPage() {
  const [query, setQuery] = useState('');
  const shown = filterServers(localServers, query);

  return (
    <Shell active="datacenter">
      <main className="dc-page">
        <div className="page-heading">
          <div>
            <div className="eyebrow">LOCAL DATACENTER · NON-ECS</div>
            <h1>本地机房资源</h1>
            <p>
              DevOps 部署 IP 中不属于阿里云 ECS 的服务器
              {fetchedAt
                ? ' · 规格采集 ' +
                  new Date(fetchedAt).toLocaleString('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    hour12: false,
                  })
                : ' · 规格未采集'}
              {' · 清单采集 ' +
                new Date(inventoryDateStr).toLocaleString('zh-CN', {
                  timeZone: 'Asia/Shanghai',
                  hour12: false,
                })}
            </p>
          </div>
        </div>
        <Metrics rows={shown} />
        <section className="panel">
          <div className="dc-section-title">
            <div>
              <h2>本地机房服务器清单</h2>
              <p>
                按部署应用数排序 · 已采集规格的显示 CPU/内存/OS，未采集的以灰色标注
              </p>
            </div>
          </div>
          <div className="dc-filters">
            <div className="dc-filter-row">
              <label className="dc-field dc-search" htmlFor="dc-search">
                <span>搜索</span>
                <Input
                  id="dc-search"
                  aria-label="搜索服务器"
                  placeholder="IP、主机名、操作系统或应用名称"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
            </div>
          </div>
          <Table className="dc-table">
            <TableHeader>
              <TableRow>
                <TableHead>服务器 IP</TableHead>
                <TableHead>主机名</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>内存</TableHead>
                <TableHead>操作系统</TableHead>
                <TableHead>登录状态</TableHead>
                <TableHead>部署应用</TableHead>
                <TableHead>环境</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((r) => {
                const uncollected = !isCollected(r);
                return (
                  <TableRow
                    key={r.ip}
                    className={uncollected ? 'dc-uncollected' : ''}
                  >
                    <TableCell className="dc-ip">{r.ip}</TableCell>
                    <TableCell className="dc-host">{r.hostname}</TableCell>
                    <TableCell>
                      {uncollected ? '—' : `${r.cpu} vCPU`}
                    </TableCell>
                    <TableCell
                      title={
                        uncollected
                          ? undefined
                          : `原始 MemTotal ${r.memoryMB} MB`
                      }
                    >
                      {uncollected ? '—' : `${r.memoryGiB} GiB`}
                    </TableCell>
                    <TableCell className="dc-os">{r.os}</TableCell>
                    <TableCell>
                      <span
                        className={`dc-login ${
                          loginLabel(r) === '可登录' ? 'dc-login-ok' : 'dc-login-fail'
                        }`}
                      >
                        {loginLabel(r)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <strong>{r.appCount}</strong>
                      <small className="dc-app-names">{r.appName}</small>
                    </TableCell>
                    <TableCell>
                      <span className="dc-env-tags">
                        {r.environments.map((e) => (
                          <span key={e} className={`dc-env dc-env-${e.toLowerCase()}`}>
                            {e}
                          </span>
                        ))}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
              {!shown.length && (
                <TableRow>
                  <TableCell colSpan={8} className="dc-empty">
                    没有符合条件的服务器。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </section>
        <footer className="page-footer">
          <span>
            <i />
            DevOps 部署 IP 减去阿里云 ECS · CPU 为 nproc 逻辑核数 · 内存从
            /proc/meminfo 换算
          </span>
          <span>静态快照 · 规格基于 JumpServer 终端采集</span>
        </footer>
      </main>
    </Shell>
  );
}
