'use client';
import { useCallback, useEffect, useState } from 'react';
import { Spinner } from '@/components/ui/spinner';

type Mount = { type: string; name: string; source: string; destination: string; readWrite: boolean; driver: string; propagation: string };
type Info = { name: string; id: string; image: string; imageId: string; observedAt: string; cacheHit?: boolean; mounts: Mount[] };
type Entry = { name: string; type: 'directory' | 'file' | 'symlink' | 'other'; size: number; modifiedAt: number };
type Listing = { entries: Entry[]; truncated: boolean; path: string; offset: number; warnings: string[] };

export function ContainerInfo({ asset, ip, pid, containerId }: { asset: string; ip: string; pid: number; containerId: string }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [mountIndex, setMountIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>('info');
  const [error, setError] = useState('');
  const request = useCallback(async <T,>(body: object) => {
    const response = await fetch('/api/settings/container-info', { method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ asset, pid, containerId, ...body }) });
    const result = await response.json() as T & { error?: string };
    if (!response.ok) throw new Error(result.error || '读取失败');
    return result;
  }, [asset, pid, containerId]);
  const loadInfo = useCallback(async (refresh = false) => {
    setBusy('info'); setError('');
    try { const result = await request<Info>({action:refresh ? 'refresh' : 'info'}); setInfo(result); setListing(null); setMountIndex(null); }
    catch (e) { setError(e instanceof Error ? e.message : '容器信息读取失败'); }
    finally { setBusy(null); }
  }, [request]);
  useEffect(() => { void loadInfo(); }, [loadInfo]);
  async function browse(index: number, path: string, offset = 0, indicator = `mount:${index}`) {
    const mount = info?.mounts[index]; if (!mount) return;
    setBusy(indicator); setError('');
    try { const result = await request<Listing>({action:'browse', mountIndex:index, mountSource:mount.source, mountDestination:mount.destination, path, offset});
      setMountIndex(index); setListing(result);
    } catch (e) { setError(e instanceof Error ? e.message : '目录读取失败'); }
    finally { setBusy(null); }
  }
  const loadingIcon = (indicator: string) => busy === indicator ? <Spinner aria-label="读取中" className="inline-block ml-2 align-middle"/> : null;
  const persistent = info?.mounts.filter(m => m.type === 'bind' || m.type === 'volume') || [];
  return <section className="panel p-6 space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">容器信息</h1><p className="text-sm text-muted-foreground">{ip} · PID {pid}</p></div>
      <button className="border rounded p-2" disabled={!!busy} onClick={() => void loadInfo(true)}>刷新{loadingIcon('info')}</button></div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {info && <><div className="space-y-1 break-all"><h2 className="font-medium">镜像与容器</h2>
      <p>容器：{info.name} · {info.id}</p><p>镜像：{info.image}</p><p>镜像 ID：{info.imageId}</p>
      <p className="text-sm text-muted-foreground">采集时间：{info.observedAt}{info.cacheHit ? '（缓存）' : ''}</p></div>
      <div className="space-y-3"><h2 className="font-medium">挂载信息</h2>
        <p>{persistent.length ? `发现 ${persistent.length} 个持久化挂载（绑定目录或 Docker 数据卷）` : '未发现绑定目录或 Docker 数据卷'}</p>
        {!info.mounts.length && <p className="text-sm text-muted-foreground">Docker 未报告挂载。</p>}
        {info.mounts.map((mount, index) => <div className="border rounded p-3 space-y-1 break-all" key={index}>
          <p className="font-medium">{mount.destination} · {mount.type === 'bind' ? '宿主机绑定挂载' : mount.type === 'volume' ? 'Docker 数据卷' : mount.type}</p>
          <p>来源：{mount.source || '未提供'}{mount.name ? ` · 卷名：${mount.name}` : ''}</p>
          {(mount.type === 'bind' || mount.type === 'volume') && <button className="border rounded p-2 text-sm" disabled={!!busy} onClick={() => void browse(index, '')}>查看文件列表{loadingIcon(`mount:${index}`)}</button>}
          {mountIndex === index && listing && <div className="mt-3 space-y-2"><p>目录：{mount.destination}{listing.path ? '/'+listing.path : ''}</p>
            {listing.path && <button className="border rounded px-2 py-1 text-sm" disabled={!!busy} onClick={() => void browse(index, listing.path.split('/').slice(0,-1).join('/'), 0, `up:${index}`)}>返回上级{loadingIcon(`up:${index}`)}</button>}
            {listing.warnings.map((warning, i) => <p key={i} className="text-amber-700">{warning}</p>)}
            {(listing.offset > 0 || listing.truncated) && <div className="flex gap-2 items-center text-sm">
              <button className="border rounded px-2 py-1" disabled={!!busy || listing.offset === 0} onClick={() => void browse(index, listing.path, listing.offset - 200, `prev:${index}`)}>上一页{loadingIcon(`prev:${index}`)}</button>
              <span>第 {Math.floor(listing.offset / 200) + 1} 页</span>
              <button className="border rounded px-2 py-1" disabled={!!busy || !listing.truncated || listing.offset >= 1000000} onClick={() => void browse(index, listing.path, listing.offset + 200, `next:${index}`)}>下一页{loadingIcon(`next:${index}`)}</button>
            </div>}
            {listing.offset >= 1000000 && listing.truncated && <p className="text-amber-700">目录项超过浏览上限。</p>}
            {!listing.entries.length && <p className="text-muted-foreground">目录为空。</p>}
            <ul>{listing.entries.map(entry => <li className="border-t py-1" key={entry.name}>{entry.type === 'directory'
              ? <button className="underline" disabled={!!busy} onClick={() => void browse(index, [listing.path, entry.name].filter(Boolean).join('/'), 0, `dir:${index}:${entry.name}`)}>📁 {entry.name}{loadingIcon(`dir:${index}:${entry.name}`)}</button>
              : <span>{entry.type === 'symlink' ? '链接' : entry.type === 'file' ? '文件' : '其他'} · {entry.name}{entry.type === 'file' ? ` · ${entry.size} B` : ''}</span>}</li>)}</ul>
            <p className="text-xs text-muted-foreground">仅列出实时文件元数据，不读取文件内容；符号链接不可进入。翻页期间目录变化可能影响列表顺序。</p>
          </div>}
        </div>)}
      </div></>}
  </section>;
}
