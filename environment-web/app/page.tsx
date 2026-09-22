'use client';
import { useState } from 'react';
import { EnvironmentServers } from '@/components/environment-servers';
import {
  Activity,
  ArrowUpRight,
  Boxes,
  LayoutDashboard,
  List,
  Route,
  Maximize,
  Server,
  ShieldAlert,
  Database,
  ArrowRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  apps,
  inventoryDate,
  stats,
  singles,
  labels,
  sharedProductionServers,
} from '@/lib/inventory';
import { repositorySnapshot } from '@/lib/repositories';
import { jumpserver } from '@/lib/jumpserver';
import { SettingsLink } from '@/components/settings-link';
import { fetchedAt as ecsDate } from '@/lib/ecs';
import { clb } from '@/lib/clb';
export function Shell({
  children,
  active = 'dashboard',
}: {
  children: React.ReactNode;
  active?: string;
}) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <a href="/" className="brand">
          <span className="brand-icon">
            <Boxes size={23} />
          </span>
          <span>
            EnvScope<small>应用环境管理</small>
          </span>
        </a>
        <div className="nav-label">工作空间</div>
        <nav>
          <a
            className={active === 'resources' ? 'active' : ''}
            href="/resources"
          >
            <Database size={18} />
            服务器资源全景
          </a>
          <a className={active === 'dashboard' ? 'active' : ''} href="/">
            <LayoutDashboard size={18} />
            环境总览
          </a>
          <a className={active === 'apps' ? 'active' : ''} href="/applications">
            <List size={18} />
            应用列表
          </a>
          <a className={active === 'topology' ? 'active' : ''} href="/topology">
            <Route size={18} />
            拓扑路径查询
          </a>
          <a
            className={active === 'repos' ? 'active' : ''}
            href="/repositories"
          >
            <Boxes size={18} />
            代码库全景
          </a>
          <a
            className={active.startsWith('ecs-') ? 'active' : ''}
            href="/aliyun/instances"
          >
            <Server size={18} />
            阿里云资源
          </a>
          <a
            className={active === 'clb' ? 'active' : ''}
            href="/aliyun/clb"
          >
            <Activity size={18} />
            CLB 全景
          </a>
          <a
            className={active === 'jumpserver' ? 'active' : ''}
            href="/jumpserver"
          >
            <Server size={18} />
            JumpServer 纳管服务器
          </a>
        </nav>
        <div className="side-note">
          <Database size={18} />
          <strong>环境资产快照</strong>
          <span>
            {active === 'topology'
              ? '实时查询'
              : active === 'clb' ? (clb.collectedAt?.replace('T', ' ').slice(0,16) ?? '该版本未采集') : active.startsWith('ecs-')
              ? ecsDate.replace('T', ' ').slice(0,16)
              : (active === 'jumpserver'
                  ? jumpserver.collectedAt
                  : active === 'repos'
                    ? repositorySnapshot.accessDate
                    : inventoryDate
                )
                  .replace('T', ' ')
                  .slice(0, 16)}
          </span>
          <small>
            {active === 'topology' ? '按当前 Topology 版本实时遍历' : '基于导出清单的静态数据'}
          </small>
        </div>
        <div className="side-bottom">
          <span className="avatar">OP</span>
          <div>
            运维工作台<small>Infrastructure workspace</small>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <SettingsLink />
          <span>
            工作空间 <b>/</b>{' '}
            {active === 'topology' ? '拓扑路径查询' : active === 'clb' ? 'CLB 全景' : active === 'resources'
              ? '服务器资源全景'
              : active === 'settings'
                ? 'Settings · 数据同步'
                : active === 'jumpserver'
                  ? 'JumpServer 纳管服务器'
                  : active === 'ecs-summary'
                    ? '阿里云资源汇总'
                    : active === 'ecs-list'
                      ? '阿里云资源列表'
                      : active === 'repos'
                        ? '代码库全景'
                        : active === 'apps'
                          ? '应用列表'
                          : '环境总览'}
          </span>
          <span className="snapshot">
            <i />
            快照模式 <b>·</b>{' '}
            {active === 'topology' ? '实时查询' : active === 'clb' ? (clb.collectedAt?.slice(0,10) ?? '未采集') : active === 'jumpserver'
              ? jumpserver.collectedAt.slice(0, 10)
              : active.startsWith('ecs-')
                ? ecsDate.slice(0,10)
                : active === 'repos'
                  ? repositorySnapshot.accessDate.slice(0, 10)
                  : inventoryDate.slice(0, 10)}
          </span>
        </header>
        {children}
      </div>
    </div>
  );
}
export default function Home() {
  const [fullError, setFullError] = useState('');
  const [expandedServers, setExpandedServers] = useState<
    Record<string, boolean>
  >({});
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      setFullError('当前浏览器不支持全屏，请使用浏览器全屏功能。');
    }
  }
  return (
    <Shell>
      <main>
        <div className="page-heading">
          <div>
            <div className="eyebrow">ENVIRONMENT OVERVIEW</div>
            <h1>应用环境总览</h1>
            <p>从应用到服务器，掌握每一个环境的部署分布。</p>
          </div>
          <Button variant="outline" onClick={fullscreen}>
            <Maximize />
            大屏模式
          </Button>
        </div>
        {fullError && <p role="status">{fullError}</p>}
        <div className="metric-grid">
          <a href="/applications" className="metric">
            <span>
              应用总数 <Boxes />
            </span>
            <strong>
              {apps.length}
              <small>个</small>
            </strong>
            <footer>
              全部纳管应用 <ArrowUpRight />
            </footer>
          </a>
          {stats.map((s) => (
            <a
              href={`/applications?env=${s.env}`}
              className={'metric ' + s.env}
              key={s.env}
            >
              <span>
                {labels[s.env]}服务器 <Server />
              </span>
              <strong>
                {s.servers}
                <small>台</small>
              </strong>
              <footer>
                {s.deployed} 个应用 · {s.instances} 条部署记录 <ArrowUpRight />
              </footer>
            </a>
          ))}
        </div>
        <div className="overview-grid">
          <section className="panel distribution">
            <div className="panel-title">
              <div>
                <h2>环境资源分布</h2>
                <p>各环境内按服务器 IP 去重统计</p>
              </div>
              <span className="subtle-tag">SERVER DISTRIBUTION</span>
            </div>
            <div className="bars">
              {stats.map((s) => (
                <div className={'bar-row ' + s.env} key={s.env}>
                  <div>
                    <span>
                      <i />
                      {labels[s.env]}
                    </span>
                    <b>
                      {s.servers}
                      <small> 台</small>
                    </b>
                  </div>
                  <div className="track">
                    <div style={{ width: `${(s.servers / 100) * 100}%` }} />
                  </div>
                  <footer>
                    <span>{s.deployed} 个已知部署应用</span>
                    <span>{s.instances} 条部署记录</span>
                  </footer>
                </div>
              ))}
            </div>
            <div className="chart-note">
              <Activity size={15} />
              共享服务器可承载多个应用，各环境服务器数不可直接视为全局去重总数。
            </div>
          </section>
          <section className="risk-panel">
            <div className="risk-top">
              <span>
                <ShieldAlert size={19} />
                生产单点关注
              </span>
              <span className="risk-badge">需关注</span>
            </div>
            <div className="risk-number">
              {singles.length}
              <span>个应用</span>
            </div>
            <p>生产环境仅部署在 1 个已知服务器 IP 上</p>
            <div className="risk-ratio">
              <span>占全部应用</span>
              <strong>
                {((singles.length / apps.length) * 100).toFixed(1)}%
              </strong>
            </div>
            <div className="risk-track">
              <div
                style={{ width: `${(singles.length / apps.length) * 100}%` }}
              />
            </div>
            <a href="/applications?filter=single" className="risk-link">
              查看单点应用 <ArrowRight size={17} />
            </a>
            <small>基于清单识别，未判断负载均衡及跨应用容灾。</small>
          </section>
        </div>
        <EnvironmentServers />
        <section className="panel shared-servers">
          <div className="panel-title">
            <div>
              <h2>
                共用服务器应用清单{' '}
                <span className="count-pill">
                  {sharedProductionServers.length} 台
                </span>
              </h2>
              <p>
                生产环境 · 按应用 ID
                去重，按部署应用总数从多到少排列。点击应用名称定位明细。
              </p>
            </div>
            <span className="shared-scope">生产环境</span>
          </div>
          <div className="shared-table-scroll">
            <Table className="shared-table">
              <TableHeader>
                <TableRow>
                  <TableHead>服务器 IP</TableHead>
                  <TableHead>部署应用总数</TableHead>
                  <TableHead>应用名称列表</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sharedProductionServers.map((server) => (
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
                      <div className="shared-apps" id={`shared-${server.ip}`}>
                        {(expandedServers[server.ip]
                          ? server.apps
                          : server.apps.slice(0, 5)
                        ).map((app) => (
                          <a
                            key={app.id}
                            href={`/applications?appId=${encodeURIComponent(app.id)}#app-${encodeURIComponent(app.id)}`}
                            className="shared-app-link"
                          >
                            {app.name}
                            <ArrowUpRight size={12} />
                          </a>
                        ))}
                      </div>
                      {server.apps.length > 5 && (
                        <Button
                          variant="ghost"
                          className="expand-shared"
                          aria-expanded={!!expandedServers[server.ip]}
                          aria-controls={`shared-${server.ip}`}
                          onClick={() =>
                            setExpandedServers((previous) => ({
                              ...previous,
                              [server.ip]: !previous[server.ip],
                            }))
                          }
                        >
                          {expandedServers[server.ip]
                            ? '收起'
                            : `展开全部 ${server.apps.length} 个应用（另 ${server.apps.length - 5} 个）`}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {!sharedProductionServers.length && (
                  <TableRow>
                    <TableCell colSpan={3}>
                      未发现生产环境共用服务器。
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </section>
        <section className="panel health">
          <div className="panel-title">
            <div>
              <h2>数据完整性</h2>
              <p>读取失败或缺少 IP 的应用环境单独标记，不计为零部署。</p>
            </div>
            <a href="/applications?filter=unknown">
              查看异常 <ArrowUpRight size={15} />
            </a>
          </div>
          <div className="health-grid">
            {stats.map((s) => (
              <a href={`/applications?filter=unknown&env=${s.env}`} key={s.env}>
                <span>{labels[s.env]}</span>
                <strong>
                  {s.unknown}
                  <small>个应用待核实</small>
                </strong>
                <div className="completion">
                  <i
                    style={{
                      width: `${((apps.length - s.unknown) / apps.length) * 100}%`,
                    }}
                  />
                </div>
                <footer>
                  数据完整率{' '}
                  {(((apps.length - s.unknown) / apps.length) * 100).toFixed(1)}
                  %<ArrowUpRight size={14} />
                </footer>
              </a>
            ))}
          </div>
        </section>
        <footer className="page-footer">
          <span>
            <i />
            清单采集时间：{inventoryDate.replace('T', ' ').slice(0, 19)}
          </span>
          <span>统计仅反映导出时状态，不代表实时运行健康度</span>
        </footer>
      </main>
    </Shell>
  );
}
