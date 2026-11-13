'use client';
import { useState } from 'react';
import { Input } from '@/components/ui/input';
import type { Asset } from '@/lib/server-processes';

// 提权失败服务器的单独采集弹窗：可填写新的 root 密码（更新密码映射表中这一台服务器的记录）后采集，
// 或不更新密码、按原有流程直接采集（不提权，仅当前账号可见进程）。
function RootPasswordDialog({asset, busy, error, onConfirm, onDirect, onClose}: {asset: Asset; busy: boolean; error: string; onConfirm: (password: string)=>void; onDirect: ()=>void; onClose: ()=>void}) {
  const [password,setPassword]=useState('');
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="root-password-dialog-title">
    <div className="w-full max-w-md space-y-4 rounded-lg border bg-background p-6 shadow-lg">
      <h3 id="root-password-dialog-title" className="font-semibold">{asset.ip} · {asset.hostname}</h3>
      <p className="text-sm text-muted-foreground">该服务器提权失败（sudo 与 root 密码均验证错误），进程采集不完整。可填写新的 root 密码，确认后更新密码映射表中这台服务器的记录并重新采集；也可以不更新密码，按原有流程直接采集（不提权，仅采集当前账号可见进程）。</p>
      <Input type="password" aria-label="新的 root 密码" placeholder="新的 root 密码" value={password} onChange={e=>setPassword(e.target.value)}/>
      {error&&<p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <button className="border rounded p-2 text-sm" disabled={busy} onClick={onClose}>取消</button>
        <button className="border rounded p-2 text-sm" disabled={busy} onClick={onDirect}>直接采集</button>
        <button className="rounded bg-foreground p-2 text-sm font-medium text-background disabled:opacity-40" disabled={busy||!password.trim()} onClick={()=>onConfirm(password)}>确认并采集</button>
      </div>
    </div>
  </div>;
}

export function useServerInspect() {
  // 摘要行单机采集按钮状态（按 asset.id 隔离）。
  const [inspect,setInspect]=useState<Record<string,{busy:boolean;message:string;error:string}>>({});
  // 提权失败弹窗：目标资产、提交忙态与错误消息。
  const [pwTarget,setPwTarget]=useState<Asset|null>(null);
  const [pwBusy,setPwBusy]=useState(false);
  const [pwError,setPwError]=useState('');
  // 单独采集入口：提权失败的服务器先弹出 root 密码填写框，其余走原有流程。
  function requestInspect(asset: Asset) {
    if (asset.inspection?.sudoStatus==='password_error') {setPwError('');setPwTarget(asset);return;}
    void startInspect(asset);
  }
  // rootPassword 存在时，后端先更新密码映射表中这台服务器的记录再开始采集；否则按原有流程采集。
  async function startInspect(asset: Asset, rootPassword?: string) {
    setPwBusy(true);setPwError('');
    setInspect(prev=>({...prev,[asset.id]:{busy:true,message:'',error:''}}));
    try {
      const response=await fetch('/api/settings/jumpserver-inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rootPassword===undefined?{asset:asset.id}:{asset:asset.id,rootPassword})});
      const result=await response.json() as {error?:string};
      if(!response.ok)throw new Error(result.error||'操作失败');
      setInspect(prev=>({...prev,[asset.id]:{busy:false,message:rootPassword===undefined?'采集任务已启动，完成后刷新页面查看最新结果':'已更新这台服务器的 root 密码，采集任务已启动，完成后刷新页面查看最新结果',error:''}}));
      setPwTarget(null);
    } catch(e) {
      const errorMessage=e instanceof Error?e.message:'操作失败';
      setInspect(prev=>({...prev,[asset.id]:{busy:false,message:'',error:errorMessage}}));
      if(rootPassword===undefined) setPwTarget(null); else setPwError(errorMessage);
    } finally {setPwBusy(false);}
  }
  const dialog = pwTarget && <RootPasswordDialog asset={pwTarget} busy={pwBusy} error={pwError} onConfirm={password=>startInspect(pwTarget,password)} onDirect={()=>startInspect(pwTarget)} onClose={()=>setPwTarget(null)}/>;
  return { inspect, requestInspect, dialog };
}
