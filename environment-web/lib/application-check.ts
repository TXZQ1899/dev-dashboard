export type CheckConfidence = 'EXACT' | 'INFERRED' | 'AMBIGUOUS' | 'UNKNOWN';
export type CheckEndpoint = { ip: string; port: number; protocol: string };
export type CheckApplication = {
  appId: string | null;
  appName: string | null;
  confidence: CheckConfidence;
  basis: string;
  candidates: { appId: string; appName: string; confidence: CheckConfidence; basis: string }[];
};
export type CheckService = { name: string; confidence: CheckConfidence; basis: string };
export type CheckDockerMapping = {
  containerId: string; containerName: string; initPid: number;
  published: CheckEndpoint; target: CheckEndpoint; observedAt?: string; service?: CheckService;
};
export type CheckDockerTarget = {
  resolution: CheckConfidence; reason: string; mappings: CheckDockerMapping[];
  processes: { pid: number; processName: string | null }[];
  pid?: number; processName?: string; startedAt?: string; startTicks?: string;
  networkNamespace?: string; observedAt?: string;
};
export type CheckConfigEndpoint = {
  host: string; port: number; kind: string; file: string; key: string; line: number; resolvedIps?: string[];
};
export type CheckFlow = {
  local: CheckEndpoint;
  peer: CheckEndpoint;
  direction: 'upstream' | 'downstream';
  directionConfidence: CheckConfidence;
  directionBasis: string;
  state: string;
  firstSeenAt: string;
  lastSeenAt: string;
  samples: number;
  sentDelta: number | null;
  receivedDelta: number | null;
  networkActivity: 'DATA_OBSERVED' | 'NO_DATA_OBSERVED' | 'UNKNOWN';
  resolution: CheckConfidence;
  reason: string;
  service: CheckService;
  configurationMatches: CheckConfigEndpoint[];
  dockerMappings?: CheckDockerMapping[];
  proxyProcesses?: { pid: number; startedAt: string; processName: string; observedAt: string }[];
  remoteProcesses: {
    serverIp: string; pid: number; startedAt: string; processName: string;
    application: CheckApplication; service: CheckService; observedAt: string; confidence: CheckConfidence;
    observationStart?: string; observationEnd?: string;
    container?: { id: string; name: string; initPid: number };
    connection?: { published: CheckEndpoint; local: CheckEndpoint; peer: CheckEndpoint; socketId: string | null };
    dockerTargetConnection?: { published: CheckEndpoint; client: CheckEndpoint; container: CheckEndpoint; socketId: string | null };
  }[];
  peerListeningProcesses?: (CheckFlow['remoteProcesses'][number] & { endpoint: CheckEndpoint; basis: string })[];
  warnings: string[];
};
export type CheckActivity = {
  key: string; scope: 'target' | 'peer'; serverIp?: string; phase: string; stage: string;
  updatedAt: string; samples?: number;
};
export type ApplicationCheckState = {
  error?: string;
  job: {
    id: string; assetId: string; pid: number; environment: string; duration: number;
    status: string; phase: string; sourceVersion: string; startedAt: string; finishedAt?: string;
    updatedAt?: string;
    progress?: {
      samples?: number; upstream?: number; downstream?: number; elapsedSeconds?: number;
      samplingStartedAt?: string; samplingFinishedAt?: string; lastObservedAt?: string | null;
    };
    activities?: CheckActivity[];
    events?: { at: string; key: string; scope: 'target' | 'peer'; serverIp?: string; message: string }[];
  } | null;
  report: {
    status: string; taskId: string; startedAt: string; finishedAt: string; sourceVersion: string;
    error?: { stage: string; code: string; exitCode: number | null; message: string; diagnostics: string[] };
    target: { serverIp: string; pid: number; processName?: string; application?: CheckApplication; dockerTarget?: CheckDockerTarget };
    upstream: CheckFlow[];
    downstream: CheckFlow[];
    configuration?: { status: string; roots?: string[]; endpoints: CheckConfigEndpoint[]; warnings: string[] };
    coverage: {
      method: string; actualSeconds?: number; samplingElapsedSeconds?: number; limitations: string[];
      requestedSamplePeriodSeconds?: number; batchPauseSeconds?: number;
      samples?: number; averageIntervalSeconds?: number | null; maxIntervalSeconds?: number | null;
      firstObservedAt?: string | null; lastObservedAt?: string | null;
      peers?: { serverIp: string; samples: number; averageIntervalSeconds: number | null; maxIntervalSeconds: number | null;
        observationStart?: string | null; observationEnd?: string | null }[];
      listenerChecks?: { serverIp: string; status: string; samples: number; firstObservedAt: string | null; lastObservedAt: string | null }[];
    };
    warnings: string[];
  } | null;
};

export function checkEndpointText(endpoint: CheckEndpoint): string {
  const ip = endpoint.ip.includes(':') ? `[${endpoint.ip}]` : endpoint.ip;
  return `${ip}:${endpoint.port}/${endpoint.protocol.toUpperCase()}`;
}

export function displayCheckProcesses(flow: CheckFlow) {
  return flow.remoteProcesses.length ? flow.remoteProcesses : flow.peerListeningProcesses ?? [];
}

export function jumpServerNotCollected(flow: CheckFlow) {
  return flow.reason.startsWith('对端 IP 未匹配到 JumpServer 资产');
}

function uniqueValues<T>(values: T[]): T[] {
  return [...new Map(values.map((value) => [JSON.stringify(value), value])).values()];
}

function processIdentities(flow: CheckFlow) {
  return uniqueValues(displayCheckProcesses(flow).map(({ pid, processName }) => ({ pid, processName })))
    .sort((a, b) => a.pid - b.pid || a.processName.localeCompare(b.processName));
}

// Display-only grouping: the original endpoints, evidence and report stay intact.
export function aggregateCheckFlows(flows: CheckFlow[]) {
  const groups = new Map<string, CheckFlow[]>();
  for (const flow of flows) {
    // Keep different candidate sets and unresolved processes separate from known owners.
    const key = JSON.stringify([flow.peer.ip, flow.remoteProcesses.length ? 'connection' : 'listener', processIdentities(flow)]);
    const group = groups.get(key);
    if (group) group.push(flow);
    else groups.set(key, [flow]);
  }
  return [...groups].map(([key, entries]) => ({
    key,
    peerIp: entries[0].peer.ip,
    jumpServerNotCollected: entries.some(jumpServerNotCollected),
    remoteProcesses: processIdentities(entries[0]),
    listeningOnly: !entries[0].remoteProcesses.length && !!entries[0].peerListeningProcesses?.length,
    services: uniqueValues(entries.map((flow) => flow.service)),
    applications: uniqueValues(entries.flatMap((flow) => displayCheckProcesses(flow).map((process) => process.application))),
    resolutions: [...new Set(entries.map((flow) => flow.resolution))],
  }));
}
