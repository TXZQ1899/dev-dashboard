/**
 * Request resolution models (TASK-01).
 *
 * These models deliberately reuse the existing topology types
 * (`TopologyNode`, `Evidence`, `Confidence`) instead of introducing a parallel
 * node system: the request resolver is an orchestration layer over the factual
 * Topology Graph.
 */
import type { Confidence, Evidence, TopologyNode } from '../types.ts';
import type { PathQuery } from './query.ts';

export type ResolutionStatus = 'RESOLVED' | 'PARTIAL' | 'AMBIGUOUS' | 'UNRESOLVED';

/**
 * One resolver invocation record. A step may fan out to several candidate
 * output nodes; the engine branches the path per candidate.
 */
export type ResolutionStep = {
  /** Matches PathResolver.name. */
  resolver: string;
  /** Node ids consumed by the resolver; empty at the request-entry stage. */
  inputNodeIds: string[];
  /** All candidate node ids produced by this invocation (pre cycle/depth pruning). */
  outputNodeIds: string[];
  /** Stable identifier of the rule the resolver applied. */
  rule: string;
  confidence: Confidence;
  evidence: Evidence[];
  warnings: string[];
};

/**
 * One candidate request path through the graph, assembled from resolver steps.
 * Unresolved paths are retained (with stoppedAt/reason) per the project's
 * "preserve unresolved topology" principle.
 */
export type ResolvedPath = {
  steps: ResolutionStep[];
  /** Nodes reached in traversal order, including the seed node. */
  nodes: TopologyNode[];
  /** Final node when a resolver marked the branch terminal, otherwise null. */
  terminalNodeId: string | null;
  /** Weakest step confidence; EXACT for paths with no steps. */
  confidence: Confidence;
  status: ResolutionStatus;
  /** Last node reached when the branch cannot continue. */
  stoppedAt?: string;
  reason?: string;
  warnings: string[];
};

export type RequestTrace = {
  query: PathQuery;
  paths: ResolvedPath[];
  status: ResolutionStatus;
  warnings: string[];
};
