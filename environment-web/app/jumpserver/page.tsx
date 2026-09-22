'use client';
import { useState, useEffect } from 'react';
import { ChevronRight } from 'lucide-react';
import { ServerInspections } from '@/components/server-inspections';
import { Shell } from '@/app/page';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  jumpserver,
  topGroups,
  distribution,
  serverGroups,
} from '@/lib/jumpserver';

const colors = [
  '#24866a',
  '#60a5fa',
  '#eab85d',
  '#a78bfa',
  '#ed8796',
  '#5cbaba',
  '#81936b',
  '#d38b56',
  '#7483a0',
  '#b47cb4',
  '#a3a3a3',
];
export default function JumpServerPage() {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const search = query.trim();
  const groups = serverGroups.map((g) => ({
    ...g,
    assets: g.assets.filter((a) => a.ip.includes(search)),
  }));
  const matched = jumpserver.assets.filter((a) => a.ip.includes(search)).length;
  function updateSearch(value: string) {
    setQuery(value);
    setOpen(
      Object.fromEntries(
        serverGroups.map((g) => [
          g.id,
          !!value.trim() && g.assets.some((a) => a.ip.includes(value.trim())),
        ]),
      ),
    );
  }
  useEffect(() => {
    const search = new URLSearchParams(window.location.search).get('q');
    if (search) updateSearch(search);
  }, []);
  let angle = 0;
  const gradient = distribution
    .map((d, i) => {
      const start = angle;
      angle += (d.value / jumpserver.assets.length) * 360;
      return `${colors[i % colors.length]} ${start}deg ${angle}deg`;
    })
    .join(',');
  return (
    <Shell active="jumpserver">
      <main className="main-content jumpserver-page">
        <div className="page-heading">
          <div>
            <h1>JumpServer 纳管服务器</h1>
            <p>当前账号授权资产 · 登录状态、进程类型、运行时间与命令搜索</p>
          </div>
        </div>
        <ServerInspections />
        <details className="mt-6"><summary className="cursor-pointer p-4 font-medium">资产分组概览</summary>
        <section className="panel jump-summary">
          <div>
            <p className="text-sm text-muted-foreground">服务器总数</p>
            <div className="jump-total">
              {jumpserver.assets.length}
              <small> 台</small>
            </div>
            <p>
              {new Set(jumpserver.assets.map((a) => a.ip)).size} 个独立 IP ·{' '}
              {topGroups.length} 个一级分组
            </p>
            <p className="mt-3 text-sm text-muted-foreground">
              采集时间：
              {new Date(jumpserver.collectedAt).toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                hour12: false,
              })}
            </p>
          </div>
          <div
            className="jump-pie"
            role="img"
            aria-label={`服务器分组占比：${distribution.map((d) => `${d.name} ${d.value} 台，占 ${((d.value / jumpserver.assets.length) * 100).toFixed(1)}%`).join('；')}`}
            style={{ background: `conic-gradient(${gradient})` }}
          />
          <div className="jump-legend">
            {distribution.map((d, i) => (
              <div key={d.name}>
                <span style={{ background: colors[i % colors.length] }} />
                <span>
                  {d.name}（{d.value}）
                </span>
                <strong>
                  {((d.value / jumpserver.assets.length) * 100).toFixed(1)}%
                </strong>
              </div>
            ))}
          </div>
          <p className="jump-note">
            饼图按资产去重：跨组服务器单列，其余分组展示独占数量。下方原始分组包含子组及跨组资产，数量不可直接相加。
          </p>
        </section>
        <section className="panel environment-servers">
          <div className="panel-title">
            <div>
              <h2>按分组查看服务器 IP</h2>
              <p>点击组名展开；支持完整或部分 IP 搜索。</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 px-6 pb-5">
            <Input
              type="search"
              className="max-w-sm"
              aria-label="搜索 JumpServer 服务器 IP"
              placeholder="搜索服务器 IP，支持部分 IP"
              value={query}
              onChange={(e) => updateSearch(e.target.value)}
            />
            {query && (
              <Button variant="ghost" onClick={() => updateSearch('')}>
                清空
              </Button>
            )}
            <span role="status" className="text-sm text-muted-foreground">
              {search
                ? `匹配 ${matched} 台服务器（按资产去重）`
                : '所有分组默认折叠'}
            </span>
          </div>
          {groups
            .filter((g) => !search || g.assets.length)
            .map((g) => (
              <Collapsible
                key={g.id}
                open={!!open[g.id]}
                onOpenChange={(value) =>
                  setOpen((prev) => ({ ...prev, [g.id]: value }))
                }
              >
                <h3>
                  <CollapsibleTrigger className="environment-server-trigger">
                    <ChevronRight size={18} />
                    {g.path.replace(/^Default \/ /, '')}
                    <span>（{g.assets.length} 台服务器）</span>
                  </CollapsibleTrigger>
                </h3>
                <CollapsibleContent>
                  <div className="shared-table-scroll">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>服务器 IP</TableHead>
                          <TableHead>主机名</TableHead>
                          <TableHead>操作系统</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {g.assets.map((a) => (
                          <TableRow key={a.id}>
                            <TableCell>
                              <code>{a.ip || '无'}</code>
                            </TableCell>
                            <TableCell>{a.hostname}</TableCell>
                            <TableCell>
                              {a.os || a.platform || '未提供'}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            ))}
          {!matched && <p className="px-6 pb-6">没有匹配的服务器 IP。</p>}
        </section>
        </details>
      </main>
    </Shell>
  );
}
