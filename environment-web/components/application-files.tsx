'use client';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatBeijingTime } from '@/lib/datetime';

type Entry = { id: string; kind: 'config' | 'log'; logGroup?: 'current' | 'history'; path: string; size: number; modifiedAt: number };
type Catalogue = { files: Entry[]; roots: { config: string[]; log: string[] }; warnings: string[]; observedAt: string };
type Content = { content: string; truncated: boolean; redacted: boolean; observedAt: string };
type Archive = { status: string; logGroup?: 'all' | 'history'; phase?: string; transferred?: number; total?: number; fileCount?: number; size?: number; warnings?: string[] };
const api = '/api/settings/application-files';
function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1048576).toFixed(1)} MiB`; }
function isCurrentLog(file: Entry) { return file.kind === 'log' && file.logGroup !== 'history' && /\.(log|out|txt)$/.test(file.path); }

export function ApplicationFiles({ asset, pid, kind }: { asset: string; pid: number; kind: 'config' | 'log' }) {
  const [session, setSession] = useState<{ asset?: string; pid?: number; id?: string; error?: string }>({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    void fetch(api, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'open', asset, pid }),
    }).then(async (response) => {
      const result = await response.json() as { id?: string; error?: string };
      if (!response.ok || !result.id) throw new Error(result.error || '应用文件会话创建失败');
      if (live) setSession({ asset, pid, id: result.id });
    }).catch((error: Error) => { if (live) setSession({ asset, pid, error: error.message }); });
    return () => { live = false; };
  }, [asset, pid, attempt]);
  const currentSession = session.asset === asset && session.pid === pid ? session : {};
  if (currentSession.error) return <div className="check-files"><p role="alert" className="check-error">{currentSession.error}</p><Button variant="outline" onClick={() => { setSession({}); setAttempt((previous) => previous + 1); }}>重试读取文件</Button></div>;
  if (!currentSession.id) return <output aria-live="polite">正在准备应用文件…</output>;
  return <ApplicationFileBrowser key={currentSession.id+'-'+kind} id={currentSession.id} kind={kind} />;
}

export function ApplicationFileBrowser({ id, kind }: { id: string; kind: 'config' | 'log' }) {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [contents, setContents] = useState<Record<string, Content>>({});
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [archive, setArchive] = useState<Archive>({ status: 'loading' });
  const running = archive.status === 'running';

  const request = useCallback(async <T,>(body: Record<string, unknown>) => {
    const response = await fetch(api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, kind, ...body }) });
    const result = await response.json() as T & { error?: string };
    if (!response.ok) throw new Error(result.error || '应用文件读取失败');
    return result;
  }, [id, kind]);

  const readFile = useCallback(async (file: string) => {
    setBusy(file); setError('');
    try { const result = await request<Content>({ action: 'read', file }); setContents((old) => ({ ...old, [file]: result })); }
    catch (e) { setError(e instanceof Error ? e.message : '读取失败'); }
    finally { setBusy(''); }
  }, [request]);

  const load = useCallback(async () => {
    setBusy('list'); setError('');
    try {
      const result = await request<Catalogue>({ action: 'list' });
      setCatalogue(result); setContents({});
      const logs = kind === 'log' ? result.files.filter(isCurrentLog) : [];
      const first = logs.find((f) => f.size > 0) || logs[0];
      setSelected(first?.id || '');
      if (first) await readFile(first.id);
    } catch (e) { setError(e instanceof Error ? e.message : '读取失败'); }
    finally { setBusy(''); }
  }, [request, readFile, kind]);

  useEffect(() => {
    if (kind === 'config') {
      const timer = setTimeout(() => void load(), 0);
      return () => clearTimeout(timer);
    }
    let live = true;
    void fetch(api + '/logs?id=' + encodeURIComponent(id), { cache: 'no-store' }).then(async (r) => {
      const result = await r.json() as Archive & { error?: string };
      if (!r.ok) throw new Error(result.error || '读取日志下载进度失败');
      return result;
    }).then((result) => {
      if (!live) return;
      setArchive(result);
      if (result.status !== 'running') void load();
    }).catch((error: Error) => { if (live) { setArchive({ status: 'idle' }); setError(error.message); } });
    return () => { live = false; };
  }, [id, load, kind]);

  useEffect(() => {
    if (!running) return;
    let live = true;
    const timer = setInterval(() => {
      void fetch(api + '/logs?id=' + encodeURIComponent(id), { cache: 'no-store' }).then(async (r) => {
        if (!r.ok) throw new Error('读取日志下载进度失败');
        const result = await r.json() as Archive;
        if (live) { setArchive(result); if (result.status !== 'running') void load(); }
      }).catch((e: Error) => { if (live) setError(e.message); });
    }, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [id, running, load]);

  async function archiveAction(cancel = false) {
    setBusy('archive'); setError('');
    try { setArchive(await request<Archive>({ action: cancel ? 'cancel-archive' : 'archive', group: 'history' })); }
    catch (e) { setError(e instanceof Error ? e.message : '日志打包失败'); }
    finally { setBusy(''); }
  }
  function downloadConfig(file: Entry, content: Content) {
    const url = URL.createObjectURL(new Blob([content.content], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = (file.path.split('/').pop() || 'config') + '.redacted.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const configs = catalogue?.files.filter((f) => f.kind === 'config') || [];
  const logs = catalogue?.files.filter(isCurrentLog) || [];
  const history = catalogue?.files.filter((f) => f.kind === 'log' && f.logGroup === 'history') || [];
  const current = contents[selected];
  const selectedFile = logs.find((file) => file.id === selected);
  return <section className="check-files" aria-label={kind === 'config' ? '应用配置文件' : '应用日志文件'}>
    <div className="check-heading"><h3>{kind === 'config' ? `当前应用的配置文件${catalogue ? ` · ${configs.length} 个` : ''}` : '应用日志'}</h3><Button variant="outline" disabled={!!busy || running} onClick={() => void load()}>{kind === 'config' ? '刷新配置文件' : '刷新日志文件'}</Button></div>
    {error && <p className="check-error" role="alert">{error}</p>}
    {busy === 'list' && <output>正在通过 JumpServer 读取文件列表…</output>}
    {kind === 'config' && <>
    <p className="check-note">点击文件展开配置详情；常见密码、令牌和私钥字段已遮盖，可下载本次读取的已脱敏内容。</p>
    {catalogue && !configs.length && <p>已检查目录中没有找到配置文件。</p>}
    <div className="check-config-list">{configs.map((file) => <details className="check-file" key={file.id} onToggle={(event) => {
      if (event.currentTarget.open && !contents[file.id] && !busy && !running) void readFile(file.id);
    }}><summary>{file.path}<small>{size(file.size)} · {formatBeijingTime(file.modifiedAt * 1000, '—')}</small></summary>
      {contents[file.id] ? <><Button variant="outline" onClick={() => downloadConfig(file, contents[file.id])}>下载已脱敏内容</Button><pre>{contents[file.id].content}</pre>{contents[file.id].truncated && <p>文件较大，仅展示和下载前 256 KiB 的已脱敏内容。</p>}</> : <Button variant="outline" disabled={!!busy || running} onClick={() => void readFile(file.id)}>{busy === file.id ? '正在读取…' : '读取配置内容'}</Button>}
    </details>)}</div>
    </>}
    {catalogue?.warnings.map((warning, i) => <p className="check-note" key={i}>{warning}</p>)}
    {kind === 'log' && <Tabs defaultValue="current" className="check-log-tabs">
      <TabsList aria-label="应用日志分类" variant="line">
        <TabsTrigger value="current">当前日志{catalogue ? `（${logs.length}）` : ''}</TabsTrigger>
        <TabsTrigger value="history">历史日志{catalogue ? `（${history.length}）` : ''}</TabsTrigger>
      </TabsList>
      <TabsContent value="current">
        <p className="check-note">仅展示 .log、.out、.txt 文本文件，默认读取最近更新的非空日志末尾 100 行。</p>
        {logs.length > 0 && <div className="check-controls"><label>日志文件 <select aria-label="日志文件" value={selected} disabled={!!busy || running} onChange={(e) => { setSelected(e.target.value); void readFile(e.target.value); }}>
          {logs.map((file) => <option value={file.id} key={file.id}>{file.path} · {size(file.size)}</option>)}
        </select></label><Button variant="outline" disabled={!!busy || running || !selected} onClick={() => void readFile(selected)}>刷新末尾 100 行</Button></div>}
        {busy === selected && selected && <p>正在读取日志…</p>}
        {current && <><pre className="check-log-content">{current.content || '文件为空'}</pre><p className="check-note">文件更新时间：{selectedFile ? formatBeijingTime(selectedFile.modifiedAt * 1000, '—') : '未知'} · 读取时间：{formatBeijingTime(current.observedAt, '—')}{current.truncated ? ' · 单次展示上限 256 KiB，超长行可能截断' : ''}</p></>}
        {catalogue && !logs.length && <p>未找到可读的当前日志文件。</p>}
      </TabsContent>
      <TabsContent value="history">
        <p className="check-note">展示 .log.gz、.log.tar.gz 等压缩日志和带数字或日期后缀的轮转日志。打包包含应用目录内全部符合条件的历史日志（含 30 天以前的文件），最多 10000 个文件、512 MiB；文件列表最多展示最近更新的 1000 项。</p>
        <div className="check-controls">
          <Button disabled={!!busy || running || !history.length} onClick={() => void archiveAction()}>打包历史日志</Button>
          {running && <Button variant="outline" disabled={!!busy} onClick={() => void archiveAction(true)}>取消下载任务</Button>}
          {archive.status === 'complete' && archive.logGroup === 'history' && <a className="check-download" href={api + '/logs/download?id=' + encodeURIComponent(id)}>下载历史日志压缩包{archive.size ? `（${size(archive.size)}）` : ''}</a>}
        </div>
        {archive.phase && <output aria-live="polite">{archive.phase}{archive.total ? ` · ${size(archive.transferred || 0)} / ${size(archive.total)}` : ''}{archive.fileCount ? ` · ${archive.fileCount} 个文件` : ''}</output>}
        {archive.warnings?.map((warning, i) => <p className="check-note" key={i}>{warning}</p>)}
        {history.length > 0 && <div className="check-log-history"><table aria-label="历史日志文件列表"><thead><tr><th>文件路径</th><th>大小</th><th>修改时间</th></tr></thead><tbody>
          {history.map((file) => <tr key={file.id}><td>{file.path}</td><td>{size(file.size)}</td><td>{formatBeijingTime(file.modifiedAt * 1000, '—')}</td></tr>)}
        </tbody></table></div>}
        {catalogue && !history.length && <p>未找到历史日志文件。</p>}
      </TabsContent>
    </Tabs>}
  </section>;
}
