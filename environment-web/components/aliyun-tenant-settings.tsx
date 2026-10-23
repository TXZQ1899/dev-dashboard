'use client';
import { useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';

export type AliyunTenant = {
  id: string; name: string; accountId: string; enabled: boolean; regions: string[];
  dmsTenantId: string; credentialVersion: string; configured: boolean; accessKeyId: string;
  mode: 'cookie' | 'accesskey'; codeup: boolean; codeupOrganizationId?: string; updatedAt?: string; verifiedAt?: string;
  alert?: { message: string } | null;
};
type Draft = { id?: string; name: string; accountId: string; enabled: boolean; regions: string;
  dmsTenantId: string; accessKeyId: string; accessKeySecret: string; credentialVersion?: string;
  mode: 'cookie' | 'accesskey'; cookie: string; codeupOrganizationId: string };
const empty = (): Draft => ({ name: '', accountId: '', enabled: true, regions: 'cn-shanghai',
  dmsTenantId: '', accessKeyId: '', accessKeySecret: '', mode: 'accesskey', cookie: '', codeupOrganizationId: '' });

export function AliyunTenantSettings({ tenants, save, busy }: {
  tenants: AliyunTenant[]; save: (patch: object) => Promise<unknown>; busy: boolean;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const set = (patch: Partial<Draft>) => setDraft(current => ({ ...current!, ...patch }));
  async function submit(verify = false) {
    if (!draft) return;
    setPending(true); setError('');
    try {
      const { mode, cookie, codeupOrganizationId, ...tenant } = draft;
      await save({ aliyunTenants: [{ ...tenant, regions: draft.regions.split(/[\s,，]+/).filter(Boolean), verify,
        ...(draft.id === 'legacy-aliyun' ? { mode, cookie, codeupOrganizationId } : {}) }] });
      setDraft(null);
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setPending(false); }
  }
  return <section className="settings-tenant-panel">
    <h2>阿里云租户管理</h2>
    <p className="settings-hint">每个启用租户均采集 ECS、CLB、DMS、NAT。阿里云凭据统一在此维护；Codeup 企业 ID 在绑定租户中配置。</p>
    <div className="settings-tenant-list">
      {tenants.map(tenant => <article className="settings-tenant-card" key={tenant.id}>
        <strong>{tenant.name} · {tenant.enabled ? '已启用' : '已停用'}</strong>
        <small>租户 ID：{tenant.id}</small>
        <small>阿里云账号 UID：{tenant.accountId || '待验证'} · 地域：{tenant.regions.join('、')}</small>
        <small>{tenant.mode === 'accesskey' ? 'AccessKey' : 'Cookie'}：{tenant.configured ? `已配置${tenant.mode === 'accesskey' && tenant.accessKeyId ? ` ${tenant.accessKeyId}` : ''}` : '未配置'}{tenant.codeup ? ' · Codeup 绑定租户' : ''}</small>
        {tenant.codeup && <small>Codeup 企业 ID：{tenant.codeupOrganizationId || '自动识别'}</small>}
        {tenant.alert && <p className="settings-error" role="alert">{tenant.alert.message}</p>}
        <Button variant="outline" disabled={busy || pending} onClick={() => {
          setError(''); setDraft({ ...tenant, regions: tenant.regions.join(', '), accessKeyId: '', accessKeySecret: '',
            cookie: '', codeupOrganizationId: tenant.codeupOrganizationId ?? '' });
        }}>管理租户</Button>
      </article>)}
    </div>
    <Button variant="outline" disabled={busy || pending} onClick={() => { setDraft(empty()); setError(''); }}>新增阿里云租户</Button>
    {draft && <div className="settings-tenant-editor">
      <h3>{draft.id ? '编辑租户' : '新增租户'}</h3>
      {draft.id && <p>租户 ID：<code>{draft.id}</code></p>}
      <label htmlFor="tenant-name">租户名称<Input id="tenant-name" value={draft.name} onChange={e => set({ name: e.target.value })} /></label>
      <label htmlFor="tenant-account">阿里云账号 UID<Input id="tenant-account" value={draft.accountId} onChange={e => set({ accountId: e.target.value })} placeholder="可留空，通过 AccessKey 验证后自动填写" /></label>
      <label htmlFor="tenant-regions">采集地域<Input id="tenant-regions" value={draft.regions} onChange={e => set({ regions: e.target.value })} placeholder="cn-shanghai；多个地域用逗号分隔" /></label>
      <label htmlFor="tenant-dms">DMS Tid<Input id="tenant-dms" value={draft.dmsTenantId} onChange={e => set({ dmsTenantId: e.target.value })} placeholder="可留空，采集时自动获取" /></label>
      {draft.id === 'legacy-aliyun' && <label htmlFor="tenant-mode">采集方式
        <select id="tenant-mode" value={draft.mode} onChange={e => set({ mode: e.target.value as Draft['mode'], accessKeyId: '', accessKeySecret: '', cookie: '' })}>
          <option value="accesskey">AccessKey（OpenAPI）</option>
          <option value="cookie">Cookie（兼容方式）</option>
        </select>
      </label>}
      {draft.mode === 'accesskey' ? <>
        <label htmlFor="tenant-key">AccessKey ID<Input id="tenant-key" autoComplete="off" value={draft.accessKeyId} onChange={e => set({ accessKeyId: e.target.value })} placeholder="留空保留；更换时同时填写 Secret" /></label>
        <label htmlFor="tenant-secret">AccessKey Secret<Input id="tenant-secret" type="password" autoComplete="new-password" value={draft.accessKeySecret} onChange={e => set({ accessKeySecret: e.target.value })} placeholder="留空保留，不回显" /></label>
      </> : <label htmlFor="tenant-cookie">阿里云 Cookie<Input id="tenant-cookie" type="password" autoComplete="off" value={draft.cookie} onChange={e => set({ cookie: e.target.value })} placeholder="留空保留已保存 Cookie" /></label>}
      {draft.id === 'legacy-aliyun' && <label htmlFor="tenant-codeup-organization">云效 Codeup 企业 ID
        <Input id="tenant-codeup-organization" autoComplete="off" value={draft.codeupOrganizationId} onChange={e => set({ codeupOrganizationId: e.target.value })} placeholder="留空则从历史仓库自动识别" />
      </label>}
      <label><input type="checkbox" checked={draft.enabled} onChange={e => set({ enabled: e.target.checked })} /> 启用采集</label>
      <p className="settings-hint">更新 AccessKey 会校验账号 UID。运行中的采集继续使用启动时的配置。停用不删除历史数据；Codeup 绑定租户需启用才能执行全量同步。</p>
      {error && <p role="alert" className="settings-error">{error}</p>}
      <div className="flex flex-wrap gap-3">
        <Button disabled={pending || busy} onClick={() => void submit()}>保存租户</Button>
        {draft.mode === 'accesskey' && <Button variant="outline" disabled={pending || busy} onClick={() => void submit(true)}>保存并验证账号</Button>}
        <Button variant="outline" disabled={pending} onClick={() => setDraft(null)}>取消</Button>
      </div>
    </div>}
  </section>;
}
