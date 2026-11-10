import type { ArchivedConfig } from './dns-downstream';

type Archive = { files?: ArchivedConfig[]; error?: string };
const cached = new Map<string, Archive>();
const pending = new Map<string, Promise<Archive>>();
const MAX_ARCHIVES = 12;

/** Reuse successful version/asset archives in this page; failed requests can be retried. */
export async function loadNginxArchive(version: string, asset: string): Promise<Archive> {
  const key = JSON.stringify([version, asset]);
  const value = cached.get(key);
  if (value) { cached.delete(key); cached.set(key, value); return value; }
  const existing = pending.get(key);
  if (existing) return existing;
  const request = (async () => {
    const response = await fetch('/api/settings/nginx-configs?' + new URLSearchParams({ version, asset }), { cache: 'no-store' });
    const data = await response.json() as Archive;
    if (!response.ok) throw new Error(data.error || '无法读取配置归档');
    cached.set(key, data);
    if (cached.size > MAX_ARCHIVES) cached.delete(cached.keys().next().value!);
    return data;
  })();
  pending.set(key, request);
  try { return await request; }
  finally { if (pending.get(key) === request) pending.delete(key); }
}
