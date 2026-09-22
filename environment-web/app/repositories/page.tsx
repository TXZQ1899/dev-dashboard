'use client';
import { Fragment, useState } from 'react';
import {
  GitBranch,
  FolderGit2,
  ChevronRight,
  Maximize,
  Search,
  ArrowUpRight,
  Info,
} from 'lucide-react';
import { PieChart, Pie, Cell } from 'recharts';
import { Shell } from '../page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ChartContainer } from '@/components/ui/chart';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  groups,
  summary,
  filterGroups,
  matchesRepository,
  periodCommitCount,
  periodLabels,
  repositoryCsv,
  unmatched,
  type Repository,
  isDailySnapshot,
  repositorySnapshot,
  differenceLabel,
  formatRepoTime,
} from '@/lib/repositories';
import './repositories.css';
import { matchGitList, batchDetailCsv } from '@/lib/repository-batch';
import { apps, inventoryDate } from '@/lib/inventory';

function Ring({
  total,
  yes,
  denied = 0,
  label,
}: {
  total: number;
  yes: number;
  denied?: number;
  label: string;
}) {
  const slices = [
    { name: '已确认可访问', value: yes, fill: '#2b987d' },
    { name: '已确认不可访问', value: denied, fill: '#dfa269' },
    {
      name: '待核实',
      value: Math.max(0, total - yes - denied),
      fill: '#e8eeec',
    },
  ];
  return (
    <div
      className="repo-ring"
      role="img"
      aria-label={`${label}：已确认可访问 ${yes}，已确认不可访问 ${denied}，待核实 ${total - yes - denied}`}
    >
      <ChartContainer config={{ value: { label } }} className="repo-chart">
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            innerRadius={48}
            outerRadius={59}
            startAngle={90}
            endAngle={-270}
            strokeWidth={0}
            isAnimationActive={false}
          >
            {slices.map((s) => (
              <Cell key={s.name} fill={s.fill} />
            ))}
          </Pie>
        </PieChart>
      </ChartContainer>
      <div className="ring-label">
        <b>
          {total ? ((yes / total) * 100).toFixed(1) : '0.0'}
          <small>%</small>
        </b>
        <span>已确认占比</span>
      </div>
    </div>
  );
}
export function AccessBadge({ value }: { value: string }) {
  const special = value === '地址不存在' || value === '暂无权限访问';
  return (
    <span
      className={`repo-badge ${value === '是' ? 'yes' : special || value === '否' ? 'no' : 'unknown'}`}
    >
      <i />
      {value === '是' ? '可访问' : value === '地址不存在' || value === '暂无权限访问' ? value : value === '否' ? '不可访问' : '待核实'}
    </span>
  );
}
export function RepositoryRows({ repos, period = '' }: { repos: Repository[]; period?: string }) {
  return (
    <Table className="repo-nested">
      <TableHeader>
        <TableRow>
          <TableHead>代码库地址 · 点击查看详情</TableHead>
          <TableHead>关联应用</TableHead>
          <TableHead>对比结果</TableHead>
          <TableHead>分支数</TableHead>
          <TableHead>合并请求</TableHead>
          <TableHead>全部提交数</TableHead>
          <TableHead>{periodLabels[period]}提交数</TableHead>
          <TableHead>最近代码提交时间</TableHead>
          <TableHead>我的访问权限</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {repos.map((r) => (
          <TableRow key={r.id}>
            <TableCell>
              <a className="repo-address" href={`/repositories/${r.id}`}>
                <GitBranch size={15} />
                <span>{r.url}</span>
                <span className={`repo-source ${r.url.includes('://code.aliyun.com/') ? 'invalid' : r.source === 'local_gitlab' || r.url.includes('://gitlab.dev.thomascook.com.cn/') ? 'local-gitlab' : 'codeup'}`}>{r.url.includes('://code.aliyun.com/') ? '地址失效' : r.source === 'local_gitlab' || r.url.includes('://gitlab.dev.thomascook.com.cn/') ? 'Local GitLab' : '云效'}</span>
                <ArrowUpRight size={14} />
              </a>
              {r.possibleCodeupMatches?.map((candidate) => (
                <a className="repo-candidate" key={candidate.id} href={candidate.url.replace(/\.git\/?$/, '')} target="_blank" rel="noopener noreferrer">
                  可能重命名：{candidate.groupName} / {candidate.name} ↗
                </a>
              ))}
            </TableCell>
            <TableCell>
              <div className="repo-app-tags">
                {!r.apps.length && <span>无 DevOps 应用引用</span>}
                {r.apps.map((a) => (
                  <a
                    key={a.id}
                    href={`/applications?appId=${encodeURIComponent(a.id)}#app-${encodeURIComponent(a.id)}`}
                  >
                    {a.name}
                  </a>
                ))}
              </div>
            </TableCell>
            <TableCell>
              <span className={`repo-diff ${r.difference || ''}`}>
                {differenceLabel(r)}
              </span>
            </TableCell>
            <TableCell>{r.branches ?? '未采集'}</TableCell>
            <TableCell>{r.mergeRequests ?? '未采集'}</TableCell>
            <TableCell>{r.commits ?? '未采集'}</TableCell>
            <TableCell>{periodCommitCount(r,period) ?? '未采集'}</TableCell>
            <TableCell>{r.commitTimeStatus === 'empty' ? '无提交' : formatRepoTime(r.lastCommittedAt)}</TableCell>
            <TableCell>
              <AccessBadge value={r.access} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
export default function Repositories() {
  const [batchText, setBatchText] = useState('');
  const [batchOpen, setBatchOpen] = useState(false);
  const batch = matchGitList(batchText, repositorySnapshot.repos);
  const batchRepos = [...new Map(batch.results.flatMap(result => result.repos).map(repo => [repo.id, repo])).values()];
  function exportBatch() {
    const csv = batchDetailCsv(batch, apps, repositorySnapshot.groups, repositorySnapshot.accessDate, inventoryDate);
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const a = document.createElement('a'); a.href = url; a.download = `代码库及应用环境详情-${repositorySnapshot.accessDate.slice(0,10)}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [period, setPeriod] = useState('');
  const [pipeline, setPipeline] = useState('');
  const [order, setOrder] = useState('total');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  const shown = filterGroups(query, filter, order, period, pipeline);
  const shownUnmatched = unmatched.filter(
    (r) =>
      matchesRepository(r, period, pipeline) && filter !== 'unused' &&
      (!query.trim() ||
        [r.name, r.url, ...r.apps.map((a) => a.name)]
          .join(' ')
          .toLowerCase()
          .includes(query.trim().toLowerCase())),
  );
  const allOpen = shown.length > 0 && shown.every((g) => open[g.id]);
  const exportRows=[...new Map([...shown.flatMap(g=>g.repos),...shownUnmatched].map(r=>[r.id,r])).values()];
  function exportFiltered() {
    const csv=repositoryCsv(exportRows,period,`搜索：${query||'全部'}；代码组筛选：${filter}；流水线：${pipeline||'全部'}；排序：${order}`);
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));
    const a=document.createElement('a');a.href=url;a.download=`代码库清单-${periodLabels[period]}-${repositorySnapshot.accessDate.slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      setError('当前浏览器不支持全屏，请使用浏览器的全屏功能。');
    }
  }
  return (
    <Shell active="repos">
      <main className="repo-dashboard">
        <div className="page-heading">
          <div>
            <div className="eyebrow">CODE REPOSITORY INTELLIGENCE</div>
            <h1>代码库全景分析</h1>
            <p>以代码组为视角，洞察资产分布、平台关联与访问权限。</p>
          </div>
          <Button variant="outline" onClick={fullscreen}>
            <Maximize />
            大屏模式
          </Button>
        </div>
        {error && <p role="status">{error}</p>}
        <div className="repo-metrics">
          <section className="panel repo-metric">
            <div>
              <span className="repo-kicker">
                <FolderGit2 size={17} />
                Codeup 代码组总数
              </span>
              <strong className="repo-number">
                {groups.length}
                <small>个</small>
              </strong>
              <p>
                含已确认可访问仓库的代码组 <b>{summary.accessibleGroups}</b>
              </p>
              <small>
                {isDailySnapshot
                  ? '当前账号的我的代码组清单'
                  : '组级权限未单独核验，其余待核实'}
              </small>
            </div>
            <Ring
              total={groups.length}
              yes={summary.accessibleGroups}
              label="代码组访问"
            />
          </section>
          <section className="panel repo-metric">
            <div>
              <span className="repo-kicker">
                <GitBranch size={17} />
                Codeup 代码库总数
              </span>
              <strong className="repo-number">
                {summary.total}
                <small>个</small>
              </strong>
              <p>
                我已确认可访问 <b>{summary.accessible}</b>
              </p>
              <small>
                不可访问 {summary.denied} · 待核实{' '}
                {summary.total - summary.accessible - summary.denied}
              </small>
            </div>
            <Ring
              total={summary.total}
              yes={summary.accessible}
              denied={summary.denied}
              label="代码库访问"
            />
          </section>
          <section className="repo-link-card">
            <span>DEVOPS 平台关联</span>
            <strong>
              {summary.linked}
              <small> / {summary.total}</small>
            </strong>
            <p>Codeup 可访问清单中被 DevOps 引用的仓库</p>
            <div className="repo-progress">
              <i
                style={{ width: `${(summary.linked / summary.total) * 100}%` }}
              />
            </div>
            <footer>
              <b>
                {((summary.linked / summary.total) * 100).toFixed(1)}% 关联率
              </b>
              <span>
                {isDailySnapshot ? 'DevOps 未使用' : '净差额'}{' '}
                {summary.total - summary.linked}
              </span>
            </footer>
          </section>
        </div>
        <div className="repo-notice">
          <Info size={17} />
          <span>
            {isDailySnapshot ? (
              <>
                当前账号可访问 {summary.accessible} 个仓库，其中{' '}
                {summary.unused} 个未被 DevOps 引用；另有 {summary.devopsOnly}{' '}
                个仓库仅在 DevOps
                清单出现（不代表已删除，可能没有权限或属于其他平台）。提交时间取现存分支可达历史中最新的 committed_date，以采集时间为筛选基准。流水线关联按 DevOps 应用仓库引用判断。
              </>
            ) : (
              <>历史快照尚未完成双向清单对比。</>
            )}
          </span>
        </div>
        <section className="panel repo-group-panel">
          <div className="panel-title">
            <div>
              <h2>
                代码组资产清单{' '}
                <span className="count-pill">
                  {shown.length} / {groups.length}
                </span>
              </h2>
              <p>展开代码组，查看已收录仓库、关联应用与访问状态。</p>
            </div>
            <Button
              variant="ghost"
              onClick={() =>
                setOpen(Object.fromEntries(shown.map((g) => [g.id, !allOpen])))
              }
              disabled={!shown.length}
            >
              {allOpen ? '全部收起' : '全部展开'}
            </Button>
          </div>
          <details className="repo-batch" open={batchOpen} onToggle={e => setBatchOpen(e.currentTarget.open)}>
            <summary>批量 Git 地址搜索与详情导出</summary>
            <p>粘贴 Git 地址列表或 Markdown 链接表格，按完整地址匹配并去重。批量结果独立于下方普通筛选，包含全部输入地址的匹配情况。</p>
            <textarea aria-label="批量 Git 地址" placeholder="每行一个 Git 地址，也可直接粘贴链接表格" rows={6} value={batchText} onChange={e => setBatchText(e.target.value)} />
            <div className="repo-batch-actions"><span>有效地址 {batch.inputs.length} · 已匹配 {batch.results.filter(r => r.repos.length).length} · 未匹配 {batch.results.filter(r => !r.repos.length).length} · 无效输入 {batch.invalid.length}</span><Button disabled={!batch.inputs.length && !batch.invalid.length} onClick={exportBatch}>导出代码库 + 应用环境详情</Button><Button variant="outline" onClick={() => setBatchText('')}>清空列表</Button></div>
            <p>CSV 包含仓库详情、关联应用、测试／仿真／生产环境部署记录，以及应用、环境和单条部署的最新成功 Push In 时间。时间缺失留空，未匹配和无关联应用也保留。</p>
            {batch.results.some(r => !r.repos.length) && <div><strong>未匹配地址</strong>{batch.results.filter(r => !r.repos.length).map(r => <p key={r.key}>{r.input}</p>)}</div>}
            {batch.invalid.length > 0 && <div><strong>无法识别的输入</strong>{batch.invalid.map(input => <p key={input}>{input}</p>)}</div>}
            {batchRepos.length > 0 && <RepositoryRows repos={batchRepos} />}
          </details>
          <div className="repo-toolbar">
            <div className="repo-search">
              <Search size={17} />
              <Input
                aria-label="搜索代码组、仓库或应用"
                placeholder="搜索代码组、仓库地址或应用名…"
                onPaste={e => { const text = e.clipboardData.getData('text'); if (text.includes('\n') || /\[[^\]]*\]\(/.test(text)) { e.preventDefault(); setBatchText(text); setBatchOpen(true); } }}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="repo-filters">
              {[
                ['all', '全部代码组'],
                ['accessible', '含可访问仓库'],
                ['gap', '有未关联差额'],
                ['conflict', '数量冲突'],
                ['unused', 'DevOps 未使用'],
                ['devops_only', '仅 DevOps 清单'],
              ].map(([v, l]) => (
                <Button
                  key={v}
                  variant={filter === v ? 'secondary' : 'ghost'}
                  aria-pressed={filter === v}
                  onClick={() => setFilter(v)}
                >
                  {l}
                </Button>
              ))}
            </div>
            <select aria-label="最近代码提交时间" value={period} onChange={e=>setPeriod(e.target.value)}>
              <option value="">全部提交时间</option><option value="week">最近 1 星期有提交</option><option value="month">最近 1 个月有提交</option><option value="quarter">最近 3 个月有提交</option><option value="half">最近半年有提交</option><option value="lastYear">最近 1 年有提交</option><option value="year">半年至 1 年前有提交</option><option value="twoYears">1 至 2 年前有提交</option><option value="older">超过 2 年未提交</option><option value="unknown">无提交或未采集</option>
            </select>
            <select aria-label="DevOps 流水线关联" value={pipeline} onChange={e=>setPipeline(e.target.value)}><option value="">全部流水线关联</option><option value="linked">有关联 DevOps 流水线</option><option value="unlinked">无关联 DevOps 流水线</option></select>
            <select
              aria-label="排序方式"
              value={order}
              onChange={(e) => setOrder(e.target.value)}
            >
              <option value="total">代码库数量 ↓</option>
              <option value="gap">未关联差额 ↓</option>
              <option value="name">代码组名称 A–Z</option>
            </select>
            <Button variant="outline" disabled={!exportRows.length} onClick={exportFiltered}>导出筛选结果（{exportRows.length}）</Button>
          </div>
          <p style={{padding:'8px 20px'}}>提交按现存分支可达历史的 SHA 去重（包含合并提交），时间范围按北京时间自然日计算。导出 CSV 包含全部符合当前筛选的仓库，不受展开状态影响；旧版本未采集的范围提交数留空。</p>
          {(period || pipeline) && <p style={{padding:'12px 20px'}}>匹配 {shown.reduce((n,g)=>n+g.repos.length,0)+shownUnmatched.length} 个仓库。代码组汇总数保留全量口径，展开后只显示符合筛选的仓库。</p>}
          <Table className="repo-group-table">
            <TableHeader>
              <TableRow>
                <TableHead>代码组</TableHead>
                <TableHead>代码库总数</TableHead>
                <TableHead>DevOps 已关联</TableHead>
                <TableHead>
                  {isDailySnapshot ? 'DevOps 未使用' : '未关联（估算）'}
                </TableHead>
                <TableHead>平台关联占比</TableHead>
                <TableHead>我可访问（已确认）</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((g) => (
                <Fragment key={g.id}>
                  <TableRow className={open[g.id] ? 'repo-open' : ''}>
                    <TableCell>
                      <Button
                        className="repo-group-toggle"
                        variant="ghost"
                        aria-expanded={!!open[g.id]}
                        aria-controls={`group-${g.id}`}
                        onClick={() =>
                          setOpen((s) => ({ ...s, [g.id]: !s[g.id] }))
                        }
                      >
                        <ChevronRight
                          size={16}
                          className={open[g.id] ? 'rotated' : ''}
                        />
                        <span className="repo-folder">
                          <FolderGit2 size={19} />
                        </span>
                        <span>
                          <b>{g.name}</b>
                          <small>
                            {g.description ||
                              g.path.split('/').slice(1).join('/')}
                          </small>
                        </span>
                      </Button>
                    </TableCell>
                    <TableCell>
                      <b>{g.total}</b>
                    </TableCell>
                    <TableCell>{g.linked}</TableCell>
                    <TableCell>
                      <span className={g.gap !== 0 ? 'repo-amber' : ''}>
                        {g.gap < 0 ? `冲突 +${-g.gap}` : g.gap}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="repo-ratio">
                        <div className="repo-progress">
                          <i
                            style={{
                              width: `${g.total ? Math.min(100, (g.linked / g.total) * 100) : 0}%`,
                            }}
                          />
                        </div>
                        <span>
                          {g.total
                            ? ((g.linked / g.total) * 100).toFixed(1) + '%'
                            : g.linked
                              ? '冲突'
                              : '—'}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <b className="repo-green">{g.accessible}</b>
                      <small className="repo-inline-note"> / {g.total}</small>
                    </TableCell>
                  </TableRow>
                  {open[g.id] && (
                    <TableRow>
                      <TableCell colSpan={6} className="repo-expanded">
                        <div id={`group-${g.id}`}>
                          <div className="repo-detail-note">
                            <span>
                              已收录 {g.repos.length} 个仓库 · 两边均有{' '}
                              {g.linked} · DevOps 未使用 {g.unused} · 仅 DevOps{' '}
                              {g.devopsOnly}
                            </span>
                            <span>
                              {isDailySnapshot
                                ? '按域名与完整仓库路径比对，不按库名猜测'
                                : '历史快照仅保留已收录地址'}
                            </span>
                          </div>
                          {g.repos.length ? (
                            <RepositoryRows
                              period={period}
                              repos={g.repos.filter(
                                (r) =>
                                  (filter !== 'unused' ||
                                    r.difference === 'codeup_only') &&
                                  (filter !== 'devops_only' ||
                                    r.difference === 'devops_only'),
                              )}
                            />
                          ) : (
                            <div className="repo-empty">
                              尚未收录该组的仓库地址，不能据此判断无访问权限。
                            </div>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
              {!shown.length && (
                <TableRow>
                  <TableCell colSpan={6}>
                    <div className="repo-empty">
                      没有匹配的代码组。
                      <Button
                        variant="link"
                        onClick={() => {
                          setQuery('');
                          setFilter('all');
                          setPeriod('');
                          setPipeline('');
                        }}
                      >
                        清除筛选
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          <div className="repo-table-footer">
            <span>
              <i className="legend-green" />
              已关联 <i className="legend-gray" />
              {isDailySnapshot ? 'DevOps 未使用' : '数量差额'}
            </span>
            <span>仓库按主机名 + 完整路径去重 · 同库多个应用仅计 1 个仓库</span>
          </div>
        </section>
        {shownUnmatched.length > 0 && (
          <details className="panel repo-unmatched">
            <summary>
              未归入我的代码组的仓库 <b>{shownUnmatched.length}</b>
              <span>未匹配当前已采集的代码组，独立展示</span>
            </summary>
            <RepositoryRows repos={shownUnmatched} period={period} />
          </details>
        )}
        {!!repositorySnapshot.invalidReferences?.length && (
          <details className="panel repo-unmatched">
            <summary>
              待核对的仓库地址{' '}
              <b>{repositorySnapshot.invalidReferences.length}</b>
              <span>地址格式异常，未参与差异统计</span>
            </summary>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>应用</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {repositorySnapshot.invalidReferences.map((r, i) => (
                  <TableRow key={`${r.appId}-${i}`}>
                    <TableCell>
                      <a
                        href={`/applications?appId=${encodeURIComponent(r.appId)}`}
                      >
                        {r.appName}
                      </a>
                    </TableCell>
                    <TableCell>{r.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </details>
        )}
        <footer className="page-footer">
          <span>
            数据快照 ·{' '}
            {formatRepoTime(
              repositorySnapshot.snapshotDate || repositorySnapshot.accessDate,
            )}
            （北京时间）
          </span>
          <span>DevOps 应用与环境 + Codeup 我的代码组 / 可访问仓库</span>
        </footer>
      </main>
    </Shell>
  );
}
