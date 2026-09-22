export type NodeType =
  | 'DOMAIN'
  | 'EIP'
  | 'NAT_GATEWAY'
  | 'DNAT_RULE'
  | 'CLB'
  | 'CLB_LISTENER'
  | 'SERVER_GROUP'
  | 'HOST'
  | 'ENDPOINT'
  | 'NGINX_ROUTE'
  | 'UPSTREAM'
  | 'APPLICATION'
  | 'DEPLOYMENT'
  | 'REPOSITORY';

export type EdgeType =
  | 'CNAME_TO'
  | 'RESOLVES_TO'
  | 'BOUND_TO'
  | 'HAS_DNAT_RULE'
  | 'EXPOSES'
  | 'HAS_LISTENER'
  | 'ROUTES_TO'
  | 'FORWARDS_TO'
  | 'SERVED_BY'
  | 'USES_UPSTREAM'
  | 'HAS_DEPLOYMENT'
  | 'LISTENS_ON'
  | 'ON_HOST'
  | 'BUILT_FROM';

export type Confidence = 'EXACT' | 'INFERRED' | 'AMBIGUOUS' | 'UNKNOWN';

export type TopologyEnvironment = 'TEST' | 'SIMULATION' | 'PRODUCT' | 'GLOBAL' | 'UNKNOWN';

export type EvidenceSource =
  | 'devops'
  | 'dns'
  | 'eip'
  | 'nat'
  | 'ecs'
  | 'clb'
  | 'jumpserver'
  | 'repository';

export type Evidence = {
  /** Stable identifier in the source snapshot. */
  sourceId: string;
  /** Short source location, e.g. file name or asset/application name. */
  source: EvidenceSource;
  reference: string;
  detail: string;
  /** Original observation timestamp, normalized to ISO-8601 when possible. */
  observedAt: string | null;
};

export type TopologyNode = {
  id: string;
  type: NodeType;
  label: string;
  /** Stable identity fields used to deduplicate this node. */
  identity: Record<string, string | number | string[]>;
  status: 'active' | 'paused' | 'external' | 'unresolved' | 'unknown';
  environment: TopologyEnvironment;
  evidence: Evidence[];
  /** Source-specific data retained for diagnostics and future consumers. */
  attributes: Record<string, unknown>;
};

export type TopologyEdge = {
  id: string;
  from: string;
  to: string;
  type: EdgeType;
  environment: TopologyEnvironment;
  evidence: Evidence[];
  confidence: Confidence;
  observedAt: string | null;
};

export type TopologyGraph = {
  generatedAt: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  stats: {
    nodeCount: number;
    edgeCount: number;
    ambiguousEdges: number;
    unresolvedNodes: number;
  };
};
