import snapshot from './server-specs.json';

export type ServerSpec = {
  ip: string;
  hostname: string;
  assetId?: string;
  loginStatus?: string;
  specsCollected?: boolean;
  cpu: number;
  memoryMB: number;
  os: string;
};

export type ServerSpecsSnapshot = {
  collectedAt: string | null;
  available: boolean;
  specs: ServerSpec[];
};

const data = snapshot as unknown as ServerSpecsSnapshot;
export const serverSpecs = data;
export const fetchedAt = data.collectedAt;
export const specs: ServerSpec[] = data.specs.map((row) => ({ ...row }));

export const compareText = (a: string, b: string) =>
  a === b ? 0 : a < b ? -1 : 1;

export function summarize(rows: ServerSpec[]) {
  return {
    count: rows.length,
    cpu: rows.reduce((n, r) => n + r.cpu, 0),
    memory: rows.reduce((n, r) => n + r.memoryMB, 0),
  };
}

export function filterSpecs(rows: ServerSpec[], query: string) {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return rows;
  return rows.filter(
    (r) =>
      r.ip.toLocaleLowerCase().includes(q) ||
      r.hostname.toLocaleLowerCase().includes(q) ||
      r.os.toLocaleLowerCase().includes(q),
  );
}
