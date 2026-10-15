'use client';
import { useState } from 'react';
import {
  Search,
  Download,
  TriangleAlert,
  ListFilter,
  ArrowLeft,
  ArrowUpRight,
} from 'lucide-react';
import { Shell } from '../../page';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { apps, labels } from '@/lib/inventory';
import {
  ipServerState,
  ipSpecSummary,
  parseIpList,
  queryAppsByIpSet,
  type IpSetQueryRow,
} from '@/lib/process-comparison';

function csvCell(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return '"' + value.replace(/"/g, '""') + '"';
  }
  return value;
}

// IP 集查询结果导出：每个 IP 关联的每个应用一行（同一应用在该 IP 上的
// 多个环境合并展示）；未命中 IP 保留一行空应用记录并在关联状态列标注。
function exportIpSetCsv(rows: IpSetQueryRow[], inventory: typeof apps) {
  const header = [
    'IP',
    '服务器规格',
    '关联状态',
    '应用ID',
    '应用名称',
    '应用环境',
    'Git地址',
  ];
  const lines = [header.map(csvCell).join(',')];
  for (const row of rows) {
    const spec = ipSpecSummary(row.ip) || '未采集';
    if (!row.matched) {
      lines.push(
        [row.ip, spec, '未查询到应用', '', '', '', '']
          .map(csvCell)
          .join(','),
      );
      continue;
    }
    for (const hit of row.hits) {
      const repository =
        inventory.find((a) => a.id === hit.appId)?.repository || '未提供';
      lines.push(
        [
          row.ip,
          spec,
          '已关联',
          hit.appId,
          hit.appName,
          hit.envs.map((e) => labels[e]).join('、'),
          repository,
        ]
          .map(csvCell)
          .join(','),
      );
    }
  }
  const bom = '\uFEFF';
  const blob = new Blob([bom + lines.join('\r\n')], {
    type: 'text/csv;charset=utf-8;',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'ip-set-apps.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function IpSetQueryPage() {
  const [ipSetInput, setIpSetInput] = useState('');
  const [ipSetRows, setIpSetRows] = useState<IpSetQueryRow[] | null>(null);
  // 输入框收缩：默认/聚焦时 10 行，失焦且已有内容时收缩为 3 行。
  const [collapsed, setCollapsed] = useState(false);

  const runIpSetQuery = () => {
    const ips = parseIpList(ipSetInput);
    setIpSetRows(ips.length ? queryAppsByIpSet(ips, apps) : null);
  };
  const clearIpSet = () => {
    setIpSetInput('');
    setIpSetRows(null);
    setCollapsed(false);
  };
  const matchedRows = ipSetRows?.filter((r) => r.matched) ?? [];
  const unmatchedRows = ipSetRows?.filter((r) => !r.matched) ?? [];

  return (
    <Shell active="apps">
      <main>
        <a className="ip-set-back" href="/applications">
          <ArrowLeft size={14} />
          返回应用列表
        </a>
        <div className="page-heading">
          <div>
            <div className="eyebrow">IP SET QUERY</div>
            <h1>按 IP 集查询应用</h1>
            <p>
              每行输入一个 IP（也支持逗号、空格分隔），按 IP 列出关联应用；
              未关联到应用的 IP 会单独标出。
            </p>
          </div>
          <span className="subtle-tag">IP SET / APPLICATIONS</span>
        </div>
        <section className="list-panel ip-set-panel">
          <div className="ip-set-input-area">
            <Textarea
              className={
                'ip-set-textarea' + (collapsed ? ' ip-set-textarea-collapsed' : '')
              }
              aria-label="IP 地址列表"
              placeholder={'10.58.9.10\n10.58.9.11\n10.179.1.224'}
              value={ipSetInput}
              rows={collapsed ? 3 : 10}
              onFocus={() => setCollapsed(false)}
              onBlur={() => setCollapsed(!!ipSetInput.trim())}
              onChange={(e) => setIpSetInput(e.target.value)}
            />
            <div className="ip-set-actions">
              <Button
                size="default"
                onClick={runIpSetQuery}
                disabled={!ipSetInput.trim()}
              >
                <Search size={15} />
                查询
              </Button>
              <Button variant="ghost" size="default" onClick={clearIpSet}>
                清空
              </Button>
            </div>
          </div>
          {ipSetRows && (
            <div className="ip-set-result">
              <div className="result-line ip-set-summary">
                <span aria-live="polite">
                  <ListFilter size={14} />
                  共 <b>{ipSetRows.length}</b> 个 IP · 关联到应用{' '}
                  <b>{matchedRows.length}</b> 个 · 未关联{' '}
                  <b>{unmatchedRows.length}</b> 个
                </span>
                <div className="result-actions">
                  <Button
                    size="default"
                    className="export-button"
                    onClick={() => exportIpSetCsv(ipSetRows, apps)}
                    disabled={!ipSetRows.length}
                    aria-label="导出 IP 集查询结果 CSV（每个应用一行）"
                  >
                    <Download size={15} />
                    导出 CSV
                  </Button>
                </div>
              </div>
              <Table className="ip-set-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>服务器 IP</TableHead>
                    <TableHead>关联应用</TableHead>
                    <TableHead>服务器登录状态</TableHead>
                    <TableHead>规格</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ipSetRows.map((row) => (
                    <TableRow key={row.ip} className={row.matched ? '' : 'ip-miss-row'}>
                      <TableCell>
                        <code>{row.ip}</code>
                      </TableCell>
                      <TableCell>
                        {row.matched ? (
                          <ul className="ip-app-list">
                            {row.hits.map((h) => (
                              <li key={h.appId}>
                                <a
                                  href={`/applications?appId=${encodeURIComponent(h.appId)}`}
                                  className="ip-app-link"
                                  title="跳转到应用列表中该应用"
                                >
                                  {h.appName}
                                  <ArrowUpRight size={12} />
                                </a>
                                {h.envs.map((e) => (
                                  <span key={e} className="ip-env-tag">
                                    <span className={'env-dot ' + e} />
                                    {labels[e]}
                                  </span>
                                ))}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span className="ip-no-app">未查询到应用</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {
                          {
                            can_login: '可登录',
                            cannot_login: '无法登录',
                            not_collected: '未被JumpServer收录',
                          }[ipServerState(row.ip)]
                        }
                      </TableCell>
                      <TableCell>{ipSpecSummary(row.ip) || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {unmatchedRows.length > 0 && (
                <div className="ip-unmatched">
                  <TriangleAlert size={14} />
                  <span>未查询到应用的 IP（{unmatchedRows.length}）：</span>
                  <code>{unmatchedRows.map((r) => r.ip).join('、')}</code>
                </div>
              )}
            </div>
          )}
          {!ipSetRows && (
            <p className="ip-set-hint">
              输入 IP 后点击查询；同一 IP 关联多个应用时会在该 IP 行内全部列出。
            </p>
          )}
        </section>
      </main>
    </Shell>
  );
}
