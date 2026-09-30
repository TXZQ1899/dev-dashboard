'use client';
import { useState } from 'react';
import {
  ArrowUpRight,
  ChevronRight,
  Download,
  Server,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from '@/components/ui/tooltip';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { environmentServers, labels, type App } from '@/lib/inventory';
import {
  compareServerApps,
  ipInJumpServer,
  ipServerState,
  ipSpecSummary,
  type ServerComparison,
} from '@/lib/process-comparison';
import { privateIpForPublic } from '@/lib/ecs';

// 服务器 IP 的 JumpServer 收录/登录状态标记：
// 红色=未收录，绿色=可登录，灰色=不能登录。
function ServerStateMark({ ip }: { ip: string }) {
  const meta = {
    not_collected: {
      className: 'server-state not-collected',
      label: '未被JumpServer收录',
      tooltip: '该服务器 IP 未被 JumpServer 收录',
    },
    can_login: {
      className: 'server-state can-login',
      label: '',
      tooltip: '服务器已被 JumpServer 收录，可登录',
    },
    cannot_login: {
      className: 'server-state cannot-login',
      label: '服务器不能登录',
      tooltip: '服务器已被 JumpServer 收录，但无法登录',
    },
  }[ipServerState(ip)];
  return (
    <Tooltip>
      <TooltipTrigger className={meta.className} aria-label={meta.tooltip}>
        <Server size={14} />
        {meta.label && <span>{meta.label}</span>}
      </TooltipTrigger>
      <TooltipContent>{meta.tooltip}</TooltipContent>
    </Tooltip>
  );
}

// 公网 IP（EIP）展示为「内网IP（公网IP: x.x.x.x）」，无映射时原样返回。
function displayIp(ip: string): string {
  const privateIp = privateIpForPublic(ip);
  return privateIp && privateIp !== ip
    ? `${privateIp}（公网IP: ${ip}）`
    : ip;
}

function csvCell(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return '"' + value.replace(/"/g, '""') + '"';
  }
  return value;
}

type ExportServer = {
  ip: string;
  apps: Array<App & { ports: string[] }>;
};

// 服务器登录状态：
// - 不在 JumpServer 收录列表 → 未收录
// - 在 JumpServer 但无法登录（loginStatus !== 'can_login'）→ 无法登录
// - 在 JumpServer 且可登录 → 可登录
function loginStatusText(ip: string, comp: ServerComparison | null): string {
  if (!ipInJumpServer(ip)) return '未收录';
  if (comp?.loginStatus && comp.loginStatus !== 'can_login') return '无法登录';
  return '可登录';
}

function exportEnvironmentCsv(env: string, servers: ExportServer[]) {
  const lines: string[] = [];
  const header = [
    '服务器IP',
    '服务器规格',
    '部署应用总数',
    '应用ID',
    '应用名称',
    '端口号',
    '进程匹配',
    '采集进程数',
    '匹配应用数',
    '未匹配应用数',
    '服务器登录状态',
  ];
  lines.push(header.map(csvCell).join(','));

  for (const server of servers) {
    const comparison = compareServerApps(server.ip, server.apps);
    const loginStatus = loginStatusText(server.ip, comparison);
    const shownIp = displayIp(server.ip);
    const spec = ipSpecSummary(server.ip) || '';
    const totalApps = String(server.apps.length);
    const collectedProcesses = String(comparison.processes.length);
    const matchedApps = String(
      comparison.apps.filter((a) => a.matched).length,
    );
    const unmatchedApps = String(
      comparison.apps.filter((a) => !a.matched).length,
    );

    server.apps.forEach((app, i) => {
      const ac = comparison.apps[i];
      const matched = ac?.matched ? '是' : '否';
      // 端口与列表页展示一致：未匹配到进程时不补端口；
      // 匹配到进程且原端口缺失时，附「（进程补充）」后缀的端口额外成行。
      const realPorts = app.ports.filter((p) => p && p !== '未提供端口');
      const portList = ac?.supplementedPort
        ? [...realPorts, `${ac.supplementedPort}（进程补充）`]
        : app.ports;
      const ports = portList.length ? portList : [''];
      ports.forEach((port) => {
        lines.push(
          [
            shownIp,
            spec,
            totalApps,
            app.id,
            app.name,
            port,
            matched,
            collectedProcesses,
            matchedApps,
            unmatchedApps,
            loginStatus,
          ]
            .map(csvCell)
            .join(','),
        );
      });
    });
  }

  const csv = lines.join('\r\n');
  const bom = '\uFEFF';
  const blob = new Blob([bom + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${env}-servers.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function EnvironmentServers() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState('');
  const [openEnvironments, setOpenEnvironments] = useState<
    Record<string, boolean>
  >({});
  const [compareMode, setCompareMode] = useState(true);
  const search = query.trim();
  const filteredEnvironments = environmentServers.map(({ env, servers }) => ({
    env,
    servers: servers.filter(
      (server) =>
        server.ip.includes(search) ||
        displayIp(server.ip).includes(search),
    ),
  }));
  function updateSearch(value: string) {
    setQuery(value);
    setOpenEnvironments(
      Object.fromEntries(
        environmentServers.map(({ env, servers }) => [
          env,
          !!value.trim() &&
            servers.some((server) => server.ip.includes(value.trim())),
        ]),
      ),
    );
  }
  return (
    <TooltipProvider>
      <section className="panel environment-servers">
      <div className="panel-title">
        <div>
          <h2>各环境服务器清单</h2>
          <p>按 IP 汇总，按应用 ID 去重；应用后显示端口，点击名称定位明细。</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 px-6 pb-5">
        <Input
          type="search"
          aria-label="按 IP 搜索各环境服务器"
          placeholder="搜索服务器 IP，支持部分 IP"
          className="max-w-sm"
          value={query}
          onChange={(event) => updateSearch(event.target.value)}
        />
        {query && (
          <Button variant="ghost" onClick={() => updateSearch('')}>
            清空
          </Button>
        )}
        <Button
          variant={compareMode ? 'default' : 'outline'}
          onClick={() => setCompareMode((v) => !v)}
        >
          {compareMode ? '退出进程对比' : '与 JumpServer 进程对比'}
        </Button>
        {search && (
          <span role="status" className="text-sm text-muted-foreground">
            {filteredEnvironments.some(({ servers }) => servers.length)
              ? '已展开匹配的环境，台数为搜索结果。'
              : '没有匹配的服务器 IP。'}
          </span>
        )}
        {compareMode && (
          <span className="text-sm text-muted-foreground">
            红色标记的应用未在 JumpServer 进程中发现；绿色端口为从进程命令补充。
          </span>
        )}
      </div>
      {filteredEnvironments.map(({ env, servers }) => (
        <Collapsible
          key={env}
          open={!!openEnvironments[env]}
          onOpenChange={(open) =>
            setOpenEnvironments((previous) => ({ ...previous, [env]: open }))
          }
        >
          <h3>
            <CollapsibleTrigger className="environment-server-trigger">
              <ChevronRight size={18} />
              {labels[env]}
              <span>（{servers.length} 台服务器）</span>
            </CollapsibleTrigger>
            <Button
              variant="ghost"
              className="ml-2 text-sm"
              onClick={() => exportEnvironmentCsv(env, servers)}
              disabled={!servers.length}
            >
              <Download size={14} />
              导出 CSV
            </Button>
          </h3>
          <CollapsibleContent>
            <div className="shared-table-scroll">
              <Table className="shared-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>服务器 IP</TableHead>
                    <TableHead>服务器规格</TableHead>
                    <TableHead>部署应用总数</TableHead>
                    <TableHead>应用名称：端口号</TableHead>
                    {compareMode && <TableHead>进程对比</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {servers.map((server) => {
                    const key = `${env}-${server.ip}`;
                    const comparison = compareMode
                      ? compareServerApps(server.ip, server.apps)
                      : null;
                    const spec = ipSpecSummary(server.ip);
                    return (
                      <TableRow key={server.ip}>
                        <TableCell>
                          <code>{displayIp(server.ip)}</code>
                          <ServerStateMark ip={server.ip} />
                        </TableCell>
                        <TableCell>
                          {spec ? (
                            <code className="server-spec">{spec}</code>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <strong className="shared-total">
                            {server.apps.length}
                          </strong>
                          <span className="shared-unit"> 个应用</span>
                        </TableCell>
                        <TableCell>
                          <div
                            className="shared-apps"
                            id={`environment-${key}`}
                          >
                            {(expanded[key]
                              ? server.apps
                              : server.apps.slice(0, 5)
                            ).map((app, idx) => {
                              const appComparison = comparison?.apps[idx];
                              const isUnmatched =
                                !!appComparison && !appComparison.matched;
                              const supplemented =
                                appComparison?.supplementedPort;
                              const displayPorts = supplemented
                                ? [
                                    ...app.ports.filter(
                                      (p) => p && p !== '未提供端口',
                                    ),
                                    `${supplemented}（进程补充）`,
                                  ]
                                : app.ports;
                              return (
                                <a
                                  key={app.id}
                                  className="shared-app-link"
                                  style={
                                    isUnmatched
                                      ? { color: '#dc2626', borderColor: '#dc2626', background: '#fef2f2' }
                                      : supplemented
                                        ? { borderColor: '#16a34a' }
                                        : undefined
                                  }
                                  title={
                                    appComparison?.process
                                      ? `匹配进程 PID ${appComparison.process.pid}：${appComparison.process.command}`
                                      : isUnmatched
                                        ? '未在 JumpServer 进程中发现此应用'
                                        : undefined
                                  }
                                  href={`/applications?appId=${encodeURIComponent(app.id)}&env=${env}#app-${encodeURIComponent(app.id)}`}
                                >
                                  {app.name}：{displayPorts.join(' / ')}
                                  <ArrowUpRight size={12} />
                                </a>
                              );
                            })}
                          </div>
                          {server.apps.length > 5 && (
                            <Button
                              variant="ghost"
                              className="expand-shared"
                              aria-expanded={!!expanded[key]}
                              aria-controls={`environment-${key}`}
                              onClick={() =>
                                setExpanded((previous) => ({
                                  ...previous,
                                  [key]: !previous[key],
                                }))
                              }
                            >
                              {expanded[key]
                                ? '收起'
                                : `展开全部 ${server.apps.length} 个应用（另 ${server.apps.length - 5} 个）`}
                            </Button>
                          )}
                        </TableCell>
                        {compareMode && (
                          <TableCell>
                            {comparison?.available ? (
                              <div className="text-xs space-y-1">
                                <span className="text-muted-foreground">
                                  采集进程数：{comparison.processes.length}
                                </span>
                                <br />
                                <span className="text-muted-foreground">
                                  匹配应用数：
                                  {comparison.apps.filter((a) => a.matched).length}
                                </span>
                                <br />
                                <span className="text-muted-foreground">
                                  未匹配应用数：
                                  {comparison.apps.filter((a) => !a.matched).length}
                                </span>
                              </div>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                {loginStatusText(server.ip, comparison)}
                              </span>
                            )}
                          </TableCell>
                        )}
                      </TableRow>
                    );
                  })}
                  {!servers.length && (
                    <TableRow>
                      <TableCell colSpan={compareMode ? 5 : 4}>
                        {search
                          ? '该环境没有匹配的服务器 IP。'
                          : '该环境暂无已知服务器 IP。'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </CollapsibleContent>
        </Collapsible>
      ))}
      </section>
    </TooltipProvider>
  );
}
