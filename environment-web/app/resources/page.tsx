'use client';
import { Fragment, useState, useEffect } from 'react';
import { Shell } from '@/app/page';
import { Input } from '@/components/ui/input';
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
  comparisonRows,
  comparisonDates,
  summarizeComparison,
  filterComparison,
  emptyComparisonFilters,
  type ComparisonFilters,
} from '@/lib/resource-comparison';

const summary = summarizeComparison(comparisonRows);
const projectNames = [
  ...new Set(comparisonRows.flatMap((r) => r.projectNames)),
].sort();
const groupNames = [
  ...new Set(comparisonRows.flatMap((r) => r.jumpGroups)),
].sort();
const environmentNames: Record<string, string> = {
  TEST: '测试',
  SIMULATION: '仿真',
  PRODUCT: '生产',
};
const pageSize = 25;
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts = [];
  const normalized = text.toLowerCase();
  let start = 0;
  let match = normalized.indexOf(query);
  while (match !== -1) {
    parts.push(text.slice(start, match));
    parts.push(<mark className="resource-search-match" key={match}>{text.slice(match, match + query.length)}</mark>);
    start = match + query.length;
    match = normalized.indexOf(query, start);
  }
  parts.push(text.slice(start));
  return <>{parts}</>;
}
function date(raw: string) {
  return new Date(raw).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  });
}
export default function ResourcesPage() {
  const [filters, setFilters] = useState<ComparisonFilters>({
    ...emptyComparisonFilters,
  });
  useEffect(() => {
    const query = new URLSearchParams(window.location.search).get('q') || '';
    setFilters(previous => ({ ...previous, query }));
  }, []);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const rows = filterComparison(comparisonRows, filters);
  const search = filters.query.trim().toLowerCase();
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const shown = rows.slice((page - 1) * pageSize, page * pageSize);
  function change(patch: Partial<ComparisonFilters>) {
    setFilters((previous) => ({ ...previous, ...patch }));
    setPage(1);
  }
  function choose(patch: Partial<ComparisonFilters>) {
    setFilters({ ...emptyComparisonFilters, ...patch });
    setPage(1);
  }
  const metrics = [
    {
      label: '资源清单 IP 合计',
      count: summary.total,
      note: '按 IP 去重，点击查看全部',
      filter: {},
    },
    {
      label: '未匹配 JumpServer 的 IP',
      count: summary.missing,
      note: `比较范围 ${summary.scope} 个 DevOps / 阿里云 IP`,
      filter: { coverage: 'missing' },
    },
    {
      label: 'DevOps IP 未匹配 JumpServer',
      count: summary.devopsMissing,
      note: `DevOps 共 ${summary.devops} 个 IP`,
      filter: { coverage: 'missing', source: 'devops' },
    },
    {
      label: '阿里云 IP 未匹配 JumpServer',
      count: summary.aliyunMissing,
      note: `阿里云共 ${summary.aliyun} 个 IP`,
      filter: { coverage: 'missing', source: 'aliyun' },
    },
  ];
  return (
    <Shell active="resources">
      <main className="resources-page">
        <div className="page-heading">
          <div>
            <div className="eyebrow">RESOURCE COMPARISON</div>
            <h1>服务器资源全景</h1>
            <p>DevOps × ECS / CLB / EIP / NAT × DNS × JumpServer · 搜索全部来源的 IP 并跳转详情</p>
          </div>
        </div>
        <div className="metric-grid resource-metrics">
          {metrics.map((m) => (
            <button
              type="button"
              className="metric"
              key={m.label}
              onClick={() => choose(m.filter)}
            >
              <span>{m.label}</span>
              <strong>
                {m.count}
                <small> 个 IP</small>
              </strong>
              <footer>{m.note}</footer>
            </button>
          ))}
        </div>
        <section className="panel resource-explanation">
          <h2>
            {summary.missing
              ? 'JumpServer 尚未覆盖清单中的全部 IP'
              : 'JumpServer 已覆盖本次比较范围内的全部 IP'}
          </h2>
          <p>
            按当前账号可见清单精确匹配 IP；未收录不等于机器一定未纳管。
            {summary.alternate > 0 &&
              `有 ${summary.alternate} 个未收录 IP 的同一 ECS 实例存在其他已纳管 IP，表中单独注明。`}
          </p>
          <p>
            已纳入 CLB 后端、EIP 及备注、NAT 公网与后端映射、DNS 地址记录。DNS 暂停记录也可搜索，不代表当前生效。10.58.0.0/16 为公司机房（
            {summary.onPrem} 个 IP）；其余 {summary.unknown} 个 IP
            暂留空。阿里云仅包含当前上海区域快照。公网与内网 IP
            分别列出，数量不等于机器台数。
          </p>
          <p className="resource-dates">
            DevOps：{date(comparisonDates.devops)}　·　阿里云：
            {date(comparisonDates.aliyun)}　·　JumpServer：
            {date(comparisonDates.jumpserver)}
            　·　CLB：{comparisonDates.clb ? date(comparisonDates.clb) : '该版本未采集'}
            　·　NAT：{comparisonDates.nat ? date(comparisonDates.nat) : '该版本未采集'}
            　·　EIP 静态数据：{comparisonDates.eip}
          </p>
        </section>
        <section className="panel resource-wide-panel">
          <div className="panel-title">
            <div>
              <h2>资源关联宽表</h2>
              <p>
                同一 IP 下按应用和环境逐项对应 Git、代码组与 Push In
                时间；缺失值留空。时间排序按每个 IP 筛选后关联记录的最新时间，空时间置后。点击 IP 下方的来源标签，可在对应清单中定位该地址。
              </p>
            </div>
            <strong>{rows.length} 个 IP</strong>
          </div>
          <div className="resource-filters">
            <Input
              type="search"
              aria-label="搜索资源 IP、域名、实例、应用或 Git 地址"
              placeholder="搜索 IP、域名、实例 ID、应用、Git 地址或分组"
              value={filters.query}
              onChange={(e) => change({ query: e.target.value })}
            />
            <select
              aria-label="JumpServer 覆盖情况"
              value={filters.coverage}
              onChange={(e) => change({ coverage: e.target.value })}
            >
              <option value="">全部覆盖情况</option>
              <option value="missing">JumpServer 未收录</option>
              <option value="managed">JumpServer 已收录</option>
            </select>
            <select
              aria-label="资源类型"
              value={filters.resource}
              onChange={(e) => change({ resource: e.target.value })}
            >
              <option value="">全部资源类型</option>
              <option>阿里云</option>
              <option>阿里云 CLB</option>
              <option>阿里云 EIP</option>
              <option>NAT 映射</option>
              <option>CLB 后端</option>
              <option>DNS 记录</option>
              <option>公司机房</option>
              <option value="unknown">类型未确认</option>
            </select>
            <select
              aria-label="数据来源"
              value={filters.source}
              onChange={(e) => change({ source: e.target.value })}
            >
              <option value="">全部数据来源</option>
              <option value="devops">出现在 DevOps</option>
              <option value="aliyun">出现在阿里云</option>
              <option value="jumpserver">出现在 JumpServer</option>
              <option value="eip">出现在 EIP / 备注</option>
              <option value="nat">出现在 NAT 映射</option>
              <option value="dns">出现在 DNS</option>
              <option value="clbBackend">出现在 CLB 后端</option>
            </select>
            <select
              aria-label="阿里云项目"
              value={filters.project}
              onChange={(e) => change({ project: e.target.value })}
            >
              <option value="">全部阿里云项目</option>
              {projectNames.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
            <select
              aria-label="JumpServer 分组"
              value={filters.group}
              onChange={(e) => change({ group: e.target.value })}
            >
              <option value="">全部 JumpServer 分组</option>
              {groupNames.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
            <select
              aria-label="Push In 时间筛选"
              value={filters.pushIn}
              onChange={(e) => change({ pushIn: e.target.value })}
            >
              <option value="">全部 Push In 时间</option>
              <option value="missing">无 Push In 时间</option>
              <option value="present">有 Push In 时间</option>
            </select>
            <Button variant="ghost" onClick={() => choose({})}>
              重置
            </Button>
          </div>
          <div className="resource-wide-scroll">
            <Table className="resource-wide-table">
              <TableHeader>
                <TableRow>
                  <TableHead>IP 地址 / 来源</TableHead>
                  <TableHead>资源类型</TableHead>
                  <TableHead>阿里云项目名</TableHead>
                  <TableHead>JumpServer 覆盖</TableHead>
                  <TableHead>JumpServer 分组</TableHead>
                  <TableHead>关联 DevOps 应用：端口</TableHead>
                  <TableHead>关联 Git 地址</TableHead>
                  <TableHead>Codeup 代码组</TableHead>
                  <TableHead aria-sort={filters.pushInSort === 'asc' ? 'ascending' : filters.pushInSort === 'desc' ? 'descending' : 'none'}>
                    <Button
                      variant="ghost"
                      onClick={() => change({ pushInSort: filters.pushInSort === 'desc' ? 'asc' : 'desc' })}
                    >
                      最后更新时间（Push In）{filters.pushInSort === 'desc' ? ' ↓ 最新优先' : filters.pushInSort === 'asc' ? ' ↑ 最早优先' : ' ↕'}
                    </Button>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((row) => {
                  const matchingReferences = search
                    ? row.references.filter(ref => ref.label.toLowerCase().includes(search))
                    : [];
                  const otherReferences = row.references.filter(ref => !matchingReferences.includes(ref));
                  const previewCount = Math.max(0, 4 - matchingReferences.length);
                  const visibleReferences = [...matchingReferences, ...otherReferences.slice(0, previewCount)];
                  const hiddenReferences = otherReferences.slice(previewCount);
                  const associations = expanded[row.ip]
                    ? row.associations
                    : row.associations.slice(0, 5);
                  const entries = associations.length ? associations : [null];
                  return (
                    <Fragment key={row.ip}>
                      {entries.map((association, index) => (
                        <TableRow
                          key={association?.key || row.ip}
                          className={index === 0 ? 'resource-ip-start' : ''}
                        >
                          {index === 0 && (
                            <>
                              <TableCell
                                rowSpan={entries.length}
                                className="resource-ip-cell"
                              >
                                <code><Highlight text={row.ip} query={search} /></code>
                                <div className="resource-source-tags">
                                  {row.sources.devops && (
                                    <a
                                      href={`/applications?q=${encodeURIComponent(row.ip)}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title="在应用列表中搜索此 IP 并展开环境记录"
                                    >
                                      DevOps ↗
                                    </a>
                                  )}
                                  {row.cloudInstanceIds.length > 0 && (
                                    <a
                                      href={`/aliyun/instances?q=${encodeURIComponent(row.ip)}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title="查看包含此 IP 的阿里云实例"
                                    >
                                      阿里云 ↗
                                    </a>
                                  )}
                                  {row.clbInstanceIds.length > 0 && <a href={`/aliyun/clb?view=instances&q=${encodeURIComponent(row.ip)}`} target="_blank" rel="noopener noreferrer">CLB ↗</a>}
                                  {row.sources.jumpserver && (
                                    <a
                                      href={`/jumpserver?q=${encodeURIComponent(row.ip)}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title="查看包含此 IP 的 JumpServer 分组"
                                    >
                                      JumpServer ↗
                                    </a>
                                  )}
                                  {visibleReferences.map(ref => <a key={ref.href} href={ref.href} target="_blank" rel="noopener noreferrer"><Highlight text={ref.label} query={search} /> ↗</a>)}
                                  {hiddenReferences.length > 0 && <details><summary>更多来源（{hiddenReferences.length}）</summary>{hiddenReferences.map(ref => <a key={ref.href} href={ref.href} target="_blank" rel="noopener noreferrer"><Highlight text={ref.label} query={search} /> ↗</a>)}</details>}
                                </div>
                                {row.cloudInstanceIds.length > 1 && (
                                  <small className="resource-warning">
                                    匹配 {row.cloudInstanceIds.length} 个 ECS
                                    实例，请核对 VPC
                                  </small>
                                )}
                                {row.associations.length > 5 && (
                                  <Button
                                    variant="ghost"
                                    className="resource-expand"
                                    aria-expanded={!!expanded[row.ip]}
                                    onClick={() =>
                                      setExpanded((previous) => ({
                                        ...previous,
                                        [row.ip]: !previous[row.ip],
                                      }))
                                    }
                                  >
                                    {expanded[row.ip]
                                      ? '收起'
                                      : `展开全部 ${row.associations.length} 项关联`}
                                  </Button>
                                )}
                              </TableCell>
                              <TableCell rowSpan={entries.length}>
                                {row.resourceType}
                                {row.classificationConflict && (
                                  <small className="resource-warning">
                                    资源类型或网段重叠，请核对
                                  </small>
                                )}
                              </TableCell>
                              <TableCell rowSpan={entries.length}>
                                {row.projectNames.map((name) => (
                                  <div key={name}>{name}</div>
                                ))}
                              </TableCell>
                              <TableCell rowSpan={entries.length}>
                                <span
                                  className={
                                    row.sources.jumpserver
                                      ? 'resource-managed'
                                      : 'resource-missing'
                                  }
                                >
                                  {row.sources.jumpserver ? '已收录' : '未收录'}
                                </span>
                                {!row.sources.jumpserver &&
                                  row.otherManagedIps.length > 0 && (
                                    <small className="resource-alternate">
                                      同一 ECS 的其他 IP 已收录：
                                      {row.otherManagedIps.join('、')}
                                    </small>
                                  )}
                              </TableCell>
                              <TableCell rowSpan={entries.length}>
                                {row.jumpGroups.map((group) => (
                                  <div
                                    className="resource-group-path"
                                    key={group}
                                  >
                                    {group}
                                  </div>
                                ))}
                              </TableCell>
                            </>
                          )}
                          <TableCell>
                            {association && (
                              <>
                                <a
                                  href={`http://devops.folidaymall.com/#/project/application/info?id=${encodeURIComponent(association.appId)}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {association.name}
                                  {association.port
                                    ? `：${association.port}`
                                    : ''}
                                </a>
                                <small className="resource-env">
                                  {environmentNames[association.env] ||
                                    association.env}
                                </small>
                              </>
                            )}
                          </TableCell>
                          <TableCell className="resource-git">
                            {association?.git &&
                              (association.gitLink ? (
                                <a
                                  href={association.gitLink.replace(
                                    /\.git$/,
                                    '',
                                  )}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {association.git}
                                </a>
                              ) : (
                                association.git
                              ))}
                          </TableCell>
                          <TableCell>{association?.codeGroup}</TableCell>
                          <TableCell className="resource-push-time">
                            {association?.pushIn}
                          </TableCell>
                        </TableRow>
                      ))}
                    </Fragment>
                  );
                })}
                {!shown.length && (
                  <TableRow>
                    <TableCell colSpan={9} className="resource-empty">
                      没有匹配的 IP，请调整筛选条件。
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
          <div className="resource-pagination">
            <span>
              第 {page} / {pages} 页 · 每页 {pageSize} 个 IP
            </span>
            <Button
              variant="outline"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              disabled={page >= pages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
          </div>
        </section>
      </main>
    </Shell>
  );
}
