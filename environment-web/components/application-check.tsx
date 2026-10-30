'use client';
import { useEffect, useState } from 'react';
import { ApplicationCheckProgress } from '@/components/application-check-progress';
import { Button } from '@/components/ui/button';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { aggregateCheckFlows, checkEndpointText, displayCheckProcesses, jumpServerNotCollected, type ApplicationCheckState, type CheckApplication, type CheckFlow } from '@/lib/application-check';
import { formatBeijingTime } from '@/lib/datetime';

const confidence = { EXACT: '精确', INFERRED: '推断', AMBIGUOUS: '多候选', UNKNOWN: '未确认' };

function AppIdentity({ app }: { app: CheckApplication }) {
  if (app.appId) return <span title={app.basis}>{app.appId} · {app.appName}<small className="check-note">{confidence[app.confidence]}</small></span>;
  if (app.candidates.length) return <span title={app.basis}>多候选：{app.candidates.map((c) => `${c.appId} · ${c.appName}`).join('；')}</span>;
  return <span title={app.basis}>未确认</span>;
}

function Flows({ flows, label }: { flows: CheckFlow[]; label: string }) {
  return <div className="check-flows">
    <h3>{label} · {flows.length} 条连接</h3>
    {!flows.length ? <p>本次采样未观察到此方向的 TCP 连接。</p> : <Table>
      <TableHeader><TableRow>
        <TableHead>对端 IP / 端口</TableHead><TableHead>类型</TableHead><TableHead>对端进程 PID / 名称</TableHead>
        <TableHead>DevOps 应用 ID / 名称</TableHead><TableHead>网络活动</TableHead><TableHead>配置核对 / 连接证据</TableHead>
      </TableRow></TableHeader>
      <TableBody>{flows.map((flow, index) => <TableRow key={index}>
        <TableCell><code>{checkEndpointText(flow.peer)}</code>{jumpServerNotCollected(flow) && <small className="check-note">JumpServer 未收录</small>}<small className="check-note" title={flow.reason}>连接归属：{confidence[flow.resolution]}</small></TableCell>
        <TableCell title={flow.service.basis}>{flow.service.confidence === 'INFERRED' ? '疑似 ' : ''}{flow.service.name}</TableCell>
        <TableCell>{displayCheckProcesses(flow).length ? displayCheckProcesses(flow).map((p) => <div key={`${p.pid}-${p.startedAt}`}>
          {p.pid} · {p.processName}<small className="check-note">{p.serverIp} · {confidence[p.confidence]}</small>
          {!flow.remoteProcesses.length && <small className="check-note">端口监听（推断）</small>}
          {p.container && <small className="check-note" title={p.container.id}>容器：{p.container.name}</small>}
        </div>) : <span title={flow.reason}>未确认</span>}</TableCell>
        <TableCell>{displayCheckProcesses(flow).length ? displayCheckProcesses(flow).map((p) => <div key={`${p.pid}-${p.startedAt}`}><AppIdentity app={p.application} /></div>) : '未确认'}</TableCell>
        <TableCell>
          {flow.networkActivity === 'DATA_OBSERVED' ? '观察到数据交换' : flow.networkActivity === 'NO_DATA_OBSERVED' ? '本次未观察到数据增长' : '流量未确认'}
          <small className="check-note">发送 {flow.sentDelta ?? '未知'} B / 接收 {flow.receivedDelta ?? '未知'} B · {flow.samples} 次采样</small>
        </TableCell>
        <TableCell className="check-evidence">
          {flow.configurationMatches.map((entry, i) => <div key={i}><code>{entry.file}:{entry.line}</code><small className="check-note">{entry.key} → {entry.host}:{entry.port}</small></div>)}
          <details><summary>查看连接证据</summary>
            <p><code>{checkEndpointText(flow.local)} ↔ {checkEndpointText(flow.peer)}</code></p>
            <p>最近一次观察到的 TCP 状态：{flow.state === 'UNKNOWN' ? '未确认' : flow.state} · {formatBeijingTime(flow.lastSeenAt, '—')}</p>
            <p>方向依据：{flow.directionBasis}（{confidence[flow.directionConfidence]}）</p>
            <p>对端进程归属：{flow.reason}</p><p>观察窗口：{formatBeijingTime(flow.firstSeenAt, '—')} ～ {formatBeijingTime(flow.lastSeenAt, '—')}</p>
            {flow.proxyProcesses?.map((p) => <p key={`${p.pid}-${p.startedAt}`}>Docker 代理：{p.pid} · {p.processName}（{formatBeijingTime(p.observedAt, '—')}）</p>)}
            {flow.dockerMappings?.map((mapping) => <p key={`${mapping.containerId}-${checkEndpointText(mapping.target)}`}>
              Docker 端口映射：<code>{checkEndpointText(mapping.published)} → {checkEndpointText(mapping.target)}</code>
              <small className="check-note">容器：{mapping.containerName} · {mapping.containerId.slice(0, 12)}（配置证据）</small>
            </p>)}
            {flow.remoteProcesses.filter((p) => p.connection).map((p) => <p key={`${p.pid}-${p.startedAt}`}>
              容器内连接：<code>{checkEndpointText(p.connection!.local)} ↔ {checkEndpointText(p.connection!.peer)}</code>
              <small className="check-note">实际服务进程：{p.pid} · {p.processName} · {formatBeijingTime(p.observedAt, '—')}</small>
            </p>)}
            {flow.remoteProcesses.filter((p) => p.dockerTargetConnection).map((p) => <p key={`target-${p.pid}-${p.startedAt}`}>
              Docker 转发候选：<code>{checkEndpointText(p.dockerTargetConnection!.client)} → {checkEndpointText(p.dockerTargetConnection!.published)} → {checkEndpointText(p.dockerTargetConnection!.container)}</code>
              <small className="check-note">客户端连接与发布端口匹配；转发关系为推断</small>
            </p>)}
            {flow.peerListeningProcesses?.map((p) => <p key={`listen-${p.pid}-${p.startedAt}`}>
              监听进程：{p.pid} · {p.processName} · <code>{checkEndpointText(p.endpoint)}</code>
              <small className="check-note">{p.basis} · {formatBeijingTime(p.observedAt, '—')}</small>
            </p>)}
            {flow.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
          </details>
        </TableCell>
      </TableRow>)}</TableBody>
    </Table>}
  </div>;
}

function AggregatedFlows({ flows, label }: { flows: CheckFlow[]; label: string }) {
  const groups = aggregateCheckFlows(flows);
  return <div className="check-flows">
    <h3>{label} · {groups.length} 条聚合连接</h3>
    {!groups.length ? <p>本次采样未观察到此方向的 TCP 连接。</p> : <Table>
      <TableHeader><TableRow>
        <TableHead>对端 IP</TableHead><TableHead>类型</TableHead><TableHead>对端进程 PID / 名称</TableHead>
        <TableHead>DevOps 应用 ID / 名称</TableHead>
      </TableRow></TableHeader>
      <TableBody>{groups.map((group) => <TableRow key={group.key}>
        <TableCell><code>{group.peerIp}</code>{group.jumpServerNotCollected && <small className="check-note">JumpServer 未收录</small>}<small className="check-note">连接归属：{group.resolutions.map((value) => confidence[value]).join(' / ')}</small></TableCell>
        <TableCell>{group.services.map((service, index) => <div key={index} title={service.basis}>
          {service.confidence === 'INFERRED' ? '疑似 ' : ''}{service.name}
        </div>)}</TableCell>
        <TableCell>{group.remoteProcesses.length ? group.remoteProcesses.map((process) => <div key={process.pid+'-'+process.processName}>
          {process.pid} · {process.processName}
          {group.listeningOnly && <small className="check-note">端口监听（推断）</small>}
        </div>) : '未确认'}</TableCell>
        <TableCell>{group.applications.length ? group.applications.map((app, index) => <div key={index}><AppIdentity app={app} /></div>) : '未确认'}</TableCell>
      </TableRow>)}</TableBody>
    </Table>}
  </div>;
}

export function ApplicationCheck({ asset, pid }: {
  asset: string; pid: number;
}) {
  const [duration, setDuration] = useState(30);
  const [state, setState] = useState<ApplicationCheckState>({ job: null, report: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [syncError, setSyncError] = useState('');
  const running = state.job?.status === 'running';
  const jobId = state.job?.id;

  useEffect(() => {
    let live = true;
    fetch('/api/settings/application-check', { cache: 'no-store' }).then(async (r) => await r.json() as ApplicationCheckState).then((result) => {
      if (live && result.job?.assetId === asset && result.job.pid === pid) {
        setState((previous) => previous.job ? previous : result);
        setLastSyncedAt(Date.now());
      }
    }).catch(() => {});
    return () => { live = false; };
  }, [asset, pid]);

  useEffect(() => {
    if (!running || !jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const id = jobId;
    async function poll() {
      try {
        const response = await fetch('/api/settings/application-check?id='+encodeURIComponent(id), { cache: 'no-store', signal: AbortSignal.timeout(10000) });
        const result = await response.json() as ApplicationCheckState;
        if (!response.ok) throw new Error(result.error || '读取核验进度失败');
        if (live) { setState(result); setSyncError(''); setLastSyncedAt(Date.now()); }
      } catch (e) {
        if (live) setSyncError(e instanceof Error ? e.message : '读取核验进度失败');
      }
      if (live) timer = setTimeout(poll, 2000);
    }
    timer = setTimeout(poll, 1000);
    return () => { live = false; clearTimeout(timer); };
  }, [running, jobId]);

  async function submit(cancel = false) {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/settings/application-check'+(cancel ? '/cancel' : ''), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cancel ? { id: state.job?.id } : { asset, pid, duration }),
      });
      const result = await response.json() as ApplicationCheckState;
      if (!response.ok) throw new Error(result.error || '核验任务启动失败');
      setState(result);
      setLastSyncedAt(Date.now()); setSyncError('');
    } catch (e) { setError(e instanceof Error ? e.message : '请求失败'); }
    finally { setBusy(false); }
  }

  function download() {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `application-check-${state.job?.id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const report = state.report;
  return <section aria-label="TCP 连接检测">
    <div className="check-controls">
      <label>观察时长 <select aria-label="观察时长" value={duration} onChange={(e) => setDuration(Number(e.target.value))} disabled={running || busy}>
        {[10, 30, 60, 300].map((seconds) => <option value={seconds} key={seconds}>{seconds} 秒</option>)}
      </select></label>
      <Button onClick={() => void submit()} disabled={busy || running}>开始检测</Button>
      {running && <Button variant="outline" onClick={() => void submit(true)} disabled={busy}>取消检测</Button>}
      {report && <Button variant="outline" onClick={download}>导出报告 JSON</Button>}
    </div>
    <p className="check-note">检查选中进程的 TCP 连接，核验同机连接，并通过 JumpServer 核对全部已发现且能唯一匹配资产的远端服务器。短连接可能漏采；数据交换需结合业务日志判断。</p>
    {error && <p role="alert" className="check-error">{error}</p>}
    {state.job && <ApplicationCheckProgress job={state.job} lastSyncedAt={lastSyncedAt} syncError={syncError} />}
    {report && <>
      {report.target.dockerTarget && <div className="check-note">
        <p>Docker 代理入口：PID {report.target.pid} · {report.target.processName}；服务定位：{confidence[report.target.dockerTarget.resolution]}</p>
        {report.target.dockerTarget.mappings.map((mapping, i) => <p key={i}>
          容器：{mapping.containerName} · <code>{checkEndpointText(mapping.published)}</code> → <code>{checkEndpointText(mapping.target)}</code>
        </p>)}
        {report.target.dockerTarget.pid !== undefined && <p>实际检测进程：PID {report.target.dockerTarget.pid} · {report.target.dockerTarget.processName}；检测该进程的全部 TCP 连接。</p>}
        <p>{report.target.dockerTarget.reason}</p>
        {report.target.dockerTarget.pid === undefined && !!report.target.dockerTarget.processes.length && <p>
          监听候选：{report.target.dockerTarget.processes.map((process) => `${process.pid} · ${process.processName ?? '身份未确认'}`).join('；')}
        </p>}
      </div>}
      {report.target.application && <p>目标应用：<AppIdentity app={report.target.application} /></p>}
      <p>{report.coverage.samplingElapsedSeconds !== undefined ? '目标有效观察跨度' : '实际观察'} {report.coverage.actualSeconds ?? '未知'} 秒；
        {report.coverage.samplingElapsedSeconds !== undefined && <>采样阶段耗时 {report.coverage.samplingElapsedSeconds} 秒；</>}
        服务类型与应用归属均保留各自的证据和置信度。</p>
      {report.coverage.samples !== undefined && <p className="check-note">
        目标有效采样 {report.coverage.samples} 次；平均观察间隔 {report.coverage.averageIntervalSeconds ?? '未知'} 秒，最长 {report.coverage.maxIntervalSeconds ?? '未知'} 秒。
        {report.coverage.requestedSamplePeriodSeconds !== undefined && <>远端目标采样间隔 {report.coverage.requestedSamplePeriodSeconds} 秒，实际间隔包含查询与传输耗时。</>}
      </p>}
      {report.error && <div role="alert" className="check-error">
        <p>失败阶段：{report.error.stage} · 错误代码：{report.error.code}
          {report.error.exitCode !== null && ` · 退出码：${report.error.exitCode}`}</p>
        <p>{report.error.message}</p>
        {report.error.diagnostics.map((diagnostic, i) => <p key={i}>{diagnostic}</p>)}
      </div>}
      {report.warnings.filter((warning) => !report.error || (
        warning !== report.error.message && !warning.startsWith(report.error.message+'（阶段 ') &&
        !report.error.diagnostics.includes(warning)
      )).map((warning, i) => <p className="check-error" key={i}>{warning}</p>)}
      <Tabs defaultValue="details">
        <TabsList aria-label="上下游连接展示" variant="line">
          <TabsTrigger value="details">连接明细</TabsTrigger>
          <TabsTrigger value="aggregate">连接聚合</TabsTrigger>
        </TabsList>
        <TabsContent value="details">
          <Flows flows={report.upstream} label="上游连接" />
          <Flows flows={report.downstream} label="下游连接" />
        </TabsContent>
        <TabsContent value="aggregate">
          <p className="check-note">按对端 IP 和进程 PID / 名称聚合；未确认与多候选归属保留。</p>
          <AggregatedFlows flows={report.upstream} label="上游连接" />
          <AggregatedFlows flows={report.downstream} label="下游连接" />
        </TabsContent>
      </Tabs>
      {report.configuration && <details><summary>应用配置中的 Endpoint 声明 · {report.configuration.endpoints.length} 条</summary>
        <p>以下是配置声明，未观察到连接的条目不代表正在使用。</p>
        {report.configuration.endpoints.map((entry, i) => <p key={i}><code>{entry.host}:{entry.port}</code> · {entry.kind} · {entry.file}:{entry.line} · {entry.key}</p>)}
        {report.configuration.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
      </details>}
      <details><summary>检查覆盖范围</summary>
        {report.coverage.peers?.map((peer) => <p key={peer.serverIp}>对端 {peer.serverIp}：有效采样 {peer.samples} 次，平均观察间隔 {peer.averageIntervalSeconds ?? '未知'} 秒，最长 {peer.maxIntervalSeconds ?? '未知'} 秒。</p>)}
        {report.coverage.limitations.map((warning, i) => <p key={i}>{warning}</p>)}
      </details>
    </>}
  </section>;
}
