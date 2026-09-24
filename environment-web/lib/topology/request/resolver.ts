/**
 * PathResolver contract and registry (TASK-01).
 *
 * Only the framework is defined here. The DNS / NAT / CLB / Nginx /
 * Deployment / Repository resolvers are reserved by name but intentionally
 * carry no business logic yet — later tasks register implementations.
 */
import type { GraphIndex } from '../path-explorer.ts';
import type { Confidence, Evidence, TopologyEdge, TopologyNode } from '../types.ts';
import type { ResolutionStep } from './models.ts';
import type { PathQuery, RequestEnvironment } from './query.ts';

/** Names reserved for later-task resolvers. Nothing is registered under them in TASK-01. */
export const RESERVED_RESOLVER_NAMES = {
  DNS: 'DNSResolver',
  NAT: 'NatResolver',
  CLB: 'ClbResolver',
  NGINX: 'NginxResolver',
  DEPLOYMENT: 'DeploymentResolver',
  REPOSITORY: 'RepositoryResolver',
} as const;

/** A single candidate expansion produced by a resolver. */
export type ResolutionMove = {
  node: TopologyNode;
  /**
   * True when this node is a valid endpoint for the request (e.g. later tasks
   * will mark APPLICATION nodes terminal). Terminal moves stop branching.
   */
  terminal?: boolean;
};

/** Result of one resolver invocation. A resolver with no candidates returns an empty `moves` list. */
export type ResolutionResult = {
  /** Stable rule identifier recorded on the ResolutionStep. */
  rule: string;
  moves: ResolutionMove[];
  /** Confidence of the step; defaults to UNKNOWN when omitted. */
  confidence?: Confidence;
  evidence?: Evidence[];
  warnings?: string[];
};

export type ResolverContext = {
  query: PathQuery;
  /** Query-time graph index, shared by every resolver and rebuilt at most once per request. */
  index: GraphIndex;
  /** Node reached on this branch so far; null at the entry stage (no node resolved yet). */
  current: TopologyNode | null;
  /** Node ids already present on this branch, for cycle protection. */
  visited: ReadonlySet<string>;
  /** Number of resolver steps already taken on this branch. */
  depth: number;
  /** Steps already recorded on this branch, oldest first. */
  trail: readonly ResolutionStep[];
  environment: RequestEnvironment | null;
  /**
   * Environment filter over graph edges: env-specific edges must match the
   * query environment; GLOBAL edges always pass. Mirrors Path Explorer semantics.
   */
  edgeAllowed(edge: TopologyEdge): boolean;
};

export interface PathResolver {
  readonly name: string;
  /** Whether this resolver should run for the current branch state. */
  canResolve(context: ResolverContext): boolean;
  resolve(context: ResolverContext): ResolutionResult;
}

/**
 * Ordered resolver registry. Order equals registration order and drives
 * deterministic orchestration.
 */
export class ResolverRegistry {
  private readonly resolvers = new Map<string, PathResolver>();

  register(resolver: PathResolver): this {
    if (this.resolvers.has(resolver.name)) {
      throw new Error(`Resolver already registered: ${resolver.name}`);
    }
    this.resolvers.set(resolver.name, resolver);
    return this;
  }

  get(name: string): PathResolver | undefined {
    return this.resolvers.get(name);
  }

  /** All registered resolvers in deterministic registration order. */
  list(): PathResolver[] {
    return [...this.resolvers.values()];
  }

  get size(): number {
    return this.resolvers.size;
  }
}
