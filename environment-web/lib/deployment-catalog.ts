export type CatalogEnvironment = 'TEST' | 'SIMULATION' | 'PRODUCT' | 'UNIDENTIFIED';

export type CatalogSource = {
  sourceVersion: string;
  snapshotSha256: string;
  observedAt: string | null;
  importedAt: string;
  counts: { createdUnits?: number; createdInstances?: number; unresolved?: number };
};

export type CatalogUnit = {
  id: string;
  code: string;
  name: string;
  unitType: string;
  appId: string | null;
  appType?: string;
  groupingState?: string | null;
  repository?: string;
  systemName: string | null;
  domainName: string | null;
  managementStatus: string;
  sourcePresence: string;
  environments: {
    environment: CatalogEnvironment;
    configurationState: string;
    coverageState: string;
    instanceCount: number;
    serverCount: number;
    unresolvedCount: number;
  }[];
};

export type CatalogInstance = {
  id: string;
  environment: CatalogEnvironment;
  ip: string | null;
  port: string | null;
  identityQuality: string;
  presenceStatus: string;
  managementStatus: string;
  lastSeenAt: string | null;
  attributes: {
    deploy?: string;
    config?: string;
    repository?: string;
    branch?: string;
    branchSource?: string;
    portBasis?: string;
    observedEndpoints?: { ip: string; port: number; protocol: string }[];
    lastPublishedAt?: string;
    publishStatus?: string;
    publishTimeSource?: string;
    status?: string;
  };
};

export type CatalogResult = {
  source: CatalogSource | null;
  activeSnapshot: { version: string; collectedAt: string | null; matchesCatalog: boolean };
  job: { status: string; error?: string };
  units: CatalogUnit[];
  pagination: { page: number; pageSize: number; total: number; totalPages: number };
  summary: { units: number; presentUnits: number; instances: number };
};

export function isHistoricalCatalog(result: Pick<CatalogResult, 'source' | 'activeSnapshot'>): boolean {
  const current = Date.parse(result.activeSnapshot.collectedAt || '');
  const imported = Date.parse(result.source?.observedAt || '');
  return Number.isFinite(imported) && !result.activeSnapshot.matchesCatalog
    && (!Number.isFinite(current) || current < imported);
}

export function catalogNeedsPolling(result: CatalogResult): boolean {
  if (result.job.status === 'failed' || isHistoricalCatalog(result)) return false;
  return ['queued', 'running'].includes(result.job.status) || !result.source || !result.activeSnapshot.matchesCatalog;
}

export const configurationLabels: Record<string, string> = {
  UNCONFIGURED: '未配置', CONFIGURED_EMPTY: '暂无实例',
  HAS_DEPLOYMENTS: '存在部署记录', UNKNOWN: '待核实',
};
