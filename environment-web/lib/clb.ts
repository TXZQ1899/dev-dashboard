import snapshot from './clb-snapshot.json';
export type Backend = { id: string; ip: string; port: number | null; weight: number | null; type: string };
export type Group = { id: string; name: string; kind: string; servers: Backend[] };
export type Listener = {
  protocol: string; port: number; status: string; description: string; groupId: string;
  backendPort: number | null; forwardPort: number | null; healthCheck: string;
  certificates: { id: string; name: string; domain: string; commonName: string; expiresAt: string }[];
  rules: { id: string; domain: string; path: string; groupId: string }[];
};
export type Clb = { id: string; name: string; ip: string; addressType: string; status: string; groups: Group[]; listeners: Listener[] };
export type Snapshot = { available: boolean; collectedAt: string | null; instances: Clb[] };
export const clb = snapshot as Snapshot;
export type Certificate = Listener['certificates'][number];
export type CertificateView = { key: string; certificate: Certificate; instances: Array<{ instance: Clb; listeners: Listener[] }> };
export function referencedGroups(instance: Clb) {
  return new Set(instance.listeners.filter(l => l.forwardPort == null).flatMap(l => [l.groupId, ...l.rules.map(r => r.groupId)]));
}
export function summarizeClb(instances: Clb[]) {
  const all = instances.flatMap(i => i.groups.flatMap(g => g.servers));
  const used = instances.flatMap(i => i.groups.filter(g => referencedGroups(i).has(g.id)).flatMap(g => g.servers));
  const empty = instances.flatMap(i => i.listeners.filter(l => l.forwardPort == null &&
    ![l.groupId,...l.rules.map(r => r.groupId)].some(id => i.groups.find(g => g.id === id)?.servers.length)));
  return { instances: instances.length, listeners: instances.reduce((n,i) => n+i.listeners.length,0),
    backends: new Set(all.map(s => s.id || s.ip)).size, used: new Set(used.map(s => s.id || s.ip)).size,
    memberships: all.length, empty: empty.length };
}
export function filterClb(instances: Clb[], query: string) {
  const q = query.trim().toLowerCase();
  return instances.filter(i => !q || JSON.stringify(i).toLowerCase().includes(q));
}
export function certificatesFromClb(instances: Clb[], query = ''): CertificateView[] {
  const grouped = new Map<string, CertificateView>();
  for (const instance of instances) for (const listener of instance.listeners) for (const certificate of listener.certificates) {
    const key = certificate.id || `${certificate.domain}|${certificate.commonName}`;
    const view = grouped.get(key) ?? { key, certificate, instances: [] };
    let attached = view.instances.find((entry) => entry.instance.id === instance.id);
    if (!attached) { attached = { instance, listeners: [] }; view.instances.push(attached); }
    if (!attached.listeners.some((item) => item.protocol === listener.protocol && item.port === listener.port)) attached.listeners.push(listener);
    grouped.set(key, view);
  }
  const q = query.trim().toLowerCase();
  return [...grouped.values()].flatMap((view) => {
    if (!q) return [view];
    const certificateText = JSON.stringify(view.certificate).toLowerCase();
    if (certificateText.includes(q)) return [view];
    const matchedInstances = view.instances.flatMap(({ instance, listeners }) => {
      const instanceText = JSON.stringify({
        id: instance.id,
        name: instance.name,
        ip: instance.ip,
        addressType: instance.addressType,
        status: instance.status,
      }).toLowerCase();
      if (instanceText.includes(q)) return [{ instance, listeners }];
      const matchedListeners = listeners.filter((listener) => JSON.stringify({
        listener,
        groups: instance.groups.filter((group) => [listener.groupId, ...listener.rules.map((rule) => rule.groupId)].includes(group.id)),
      }).toLowerCase().includes(q));
      return matchedListeners.length ? [{ instance, listeners: matchedListeners }] : [];
    });
    return matchedInstances.length ? [{ ...view, instances: matchedInstances }] : [];
  }).sort((a, b) => (a.certificate.domain || a.certificate.commonName).localeCompare(b.certificate.domain || b.certificate.commonName));
}
