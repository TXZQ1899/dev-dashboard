'use client';
import { useState } from 'react';
import { ArrowUpRight, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { environmentServers, labels } from '@/lib/inventory';

export function EnvironmentServers() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState('');
  const [openEnvironments, setOpenEnvironments] = useState<
    Record<string, boolean>
  >({});
  const search = query.trim();
  const filteredEnvironments = environmentServers.map(({ env, servers }) => ({
    env,
    servers: servers.filter((server) => server.ip.includes(search)),
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
        {search && (
          <span role="status" className="text-sm text-muted-foreground">
            {filteredEnvironments.some(({ servers }) => servers.length)
              ? '已展开匹配的环境，台数为搜索结果。'
              : '没有匹配的服务器 IP。'}
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
          </h3>
          <CollapsibleContent>
            <div className="shared-table-scroll">
              <Table className="shared-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>服务器 IP</TableHead>
                    <TableHead>部署应用总数</TableHead>
                    <TableHead>应用名称：端口号</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {servers.map((server) => {
                    const key = `${env}-${server.ip}`;
                    return (
                      <TableRow key={server.ip}>
                        <TableCell>
                          <code>{server.ip}</code>
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
                            ).map((app) => (
                              <a
                                key={app.id}
                                className="shared-app-link"
                                href={`/applications?appId=${encodeURIComponent(app.id)}&env=${env}#app-${encodeURIComponent(app.id)}`}
                              >
                                {app.name}：{app.ports.join(' / ')}
                                <ArrowUpRight size={12} />
                              </a>
                            ))}
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
                      </TableRow>
                    );
                  })}
                  {!servers.length && (
                    <TableRow>
                      <TableCell colSpan={3}>
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
  );
}
