import { useSyncExternalStore } from 'react';
import { fetchAliyunTenants, type AliyunTenantSummary } from './tenants';

type TenantState = { tenants: AliyunTenantSummary[] | null; failed: boolean };
const storageKey = 'envscope:aliyun-tenant-names:v1';
const initialState: TenantState = { tenants: null, failed: false };
let state = initialState;
let initialized = false;
let pending: Promise<void> | null = null;
let lastSuccess = 0;
const listeners = new Set<() => void>();

function readCache(): AliyunTenantSummary[] | null {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(storageKey) || 'null');
    if (!Array.isArray(value) || !value.every((row) => row && typeof row.id === 'string' && typeof row.name === 'string')) return null;
    return value.map((row) => ({ id: row.id, name: row.name }));
  } catch { return null; }
}
function snapshot(): TenantState {
  if (!initialized && typeof window !== 'undefined') {
    initialized = true;
    const tenants = readCache();
    if (tenants) state = { tenants, failed: false };
  }
  return state;
}
function publish(next: TenantState) {
  state = next;
  listeners.forEach((listener) => listener());
}
function refresh() {
  if (pending || Date.now() - lastSuccess < 60_000) return;
  pending = fetchAliyunTenants().then((rows) => {
    const tenants = rows.map(({ id, name }) => ({ id, name }));
    try { window.localStorage.setItem(storageKey, JSON.stringify(tenants)); } catch { /* Storage is optional. */ }
    lastSuccess = Date.now();
    publish({ tenants, failed: false });
  }).catch(() => {
    if (!state.tenants) publish({ tenants: null, failed: true });
  }).finally(() => { pending = null; });
}
function onStorage(event: StorageEvent) {
  if (event.key !== storageKey && event.key !== null) return;
  const tenants = readCache();
  if (tenants) publish({ tenants, failed: false });
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) window.addEventListener('storage', onStorage);
  refresh();
  return () => {
    listeners.delete(listener);
    if (!listeners.size) window.removeEventListener('storage', onStorage);
  };
}
export function useAliyunTenants(): TenantState {
  return useSyncExternalStore(subscribe, snapshot, () => initialState);
}
