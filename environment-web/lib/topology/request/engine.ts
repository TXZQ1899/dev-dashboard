/**
 * Request resolution engine skeleton (TASK-01).
 *
 * Orchestrates registered PathResolvers over the existing factual Topology
 * Graph. No DNS/NAT/CLB/Nginx/Application business logic lives here: the
 * engine only provides candidate branching, cycle protection, maxDepth,
 * deterministic ordering, environment filtering and unresolved-path
 * preservation on top of the reusable Path Explorer graph index.
 */
import { buildGraphIndex, type GraphIndex } from '../path-explorer.ts';
import type { Confidence, TopologyEdge, TopologyEnvironment, TopologyGraph, TopologyNode } from '../types.ts';
import type { RequestTrace, ResolvedPath, ResolutionStatus, ResolutionStep } from './models.ts';
import { normalizeRequestQuery, type PathQuery, type PathQueryInput, type RequestEnvironment } from './query.ts';
import { ResolverRegistry, type PathResolver, type ResolutionResult } from './resolver.ts';

// The legal end-to-end ingress chain (TASK-06 acceptance criteria) spans ~15
// resolver hops (DOMAIN → … → REPOSITORY), so the request engine defaults to a
// depth that can carry the full chain; Path Explorer keeps its own default.
export const DEFAULT_MAX_DEPTH = 16;
export const DEFAULT_MAX_PATHS = 50;
const WARNING_CAP = 50;

/** Stable machine-readable stop reasons; human explanations go into warnings. */
export const STOP_REASON = {
  NO_ENTRY: 'no-resolver-entry',
  NO_CANDIDATE: 'no-candidate',
  CYCLE: 'cycle-guard',
  MAX_DEPTH: 'max-depth',
  MAX_PATHS: 'max-paths',
} as const;

const CONFIDENCE_RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };

export type ResolveOptions = {
  /** Resolvers to run; an empty registry yields a valid UNRESOLVED trace. */
  registry?: ResolverRegistry;
  maxDepth?: number;
  maxPaths?: number;
  /** Overrides the environment carried by the query itself. */
  environment?: RequestEnvironment;
};

type Branch = {
  current: TopologyNode | null;
  nodes: TopologyNode[];
  steps: ResolutionStep[];
  visited: Set<string>;
  depth: number;
};

/** Resolve a request against a topology, building the graph index once. */
export function resolveRequestPath(
  input: PathQueryInput | PathQuery | string,
  topology: TopologyGraph,
  options?: ResolveOptions,
): RequestTrace {
  return resolveRequestPathWithIndex(buildGraphIndex(topology), input, options);
}

/**
 * Resolve against a prebuilt GraphIndex. Use this entry point when several
 * requests are answered for the same topology so edges are never rescanned
 * per traversal.
 */
export function resolveRequestPathWithIndex(
  index: GraphIndex,
  input: PathQueryInput | PathQuery | string,
  options?: ResolveOptions,
): RequestTrace {
  const query = normalizeRequestQuery(input);
  const registry = options?.registry ?? new ResolverRegistry();
  const maxDepth = options?.maxDepth ?? DEFAULT_MAX_DEPTH;
  const maxPaths = options?.maxPaths ?? DEFAULT_MAX_PATHS;
  const environment = options?.environment ?? query.environment ?? null;

  const allowedEnvironments = environment
    ? new Set<TopologyEnvironment>(['GLOBAL', environment])
    : null;
  const edgeAllowed = (edge: TopologyEdge) => !allowedEnvironments || allowedEnvironments.has(edge.environment);

  const warnings: string[] = [];
  const pushWarning = (message: string) => {
    if (!warnings.includes(message) && warnings.length < WARNING_CAP) warnings.push(message);
  };

  const terminalPaths: ResolvedPath[] = [];
  const unresolvedPaths: ResolvedPath[] = [];
  let truncated = false;

  const emit = (path: ResolvedPath) => {
    if (terminalPaths.length + unresolvedPaths.length >= maxPaths) {
      truncated = true;
      return false;
    }
    (path.terminalNodeId ? terminalPaths : unresolvedPaths).push(path);
    return true;
  };

  const queue: Branch[] = [{ current: null, nodes: [], steps: [], visited: new Set(), depth: 0 }];

  while (queue.length > 0) {
    if (truncated) break;
    const branch = queue.shift() as Branch;

    if (branch.depth >= maxDepth) {
      emitUnresolved(emit, pushWarning, branch, STOP_REASON.MAX_DEPTH, `Resolution stopped at maxDepth=${maxDepth}.`);
      continue;
    }

    const context = {
      query,
      index,
      current: branch.current,
      visited: branch.visited as ReadonlySet<string>,
      depth: branch.depth,
      trail: branch.steps as readonly ResolutionStep[],
      environment,
      edgeAllowed,
    };

    const invocations: { resolver: PathResolver; result: ResolutionResult }[] = [];
    for (const resolver of registry.list()) {
      if (!resolver.canResolve(context)) continue;
      invocations.push({ resolver, result: resolver.resolve(context) });
    }

    const producedMoves = invocations.some(item => item.result.moves.length > 0);
    if (!producedMoves) {
      if (invocations.length === 0) {
        emitUnresolved(
          emit,
          pushWarning,
          branch,
          STOP_REASON.NO_ENTRY,
          branch.current
            ? `No registered resolver can continue from node ${branch.current.id}.`
            : `No registered resolver could start resolution for ${query.scheme}://${query.host}:${query.port}${query.path}.`,
        );
      } else {
        for (const item of invocations) {
          for (const message of item.result.warnings ?? []) pushWarning(message);
        }
        emitUnresolved(
          emit,
          pushWarning,
          branch,
          STOP_REASON.NO_CANDIDATE,
          branch.current
            ? `Resolver(s) [${invocations.map(item => item.resolver.name).join(', ')}] produced no candidate from node ${branch.current.id}.`
            : `Resolver(s) [${invocations.map(item => item.resolver.name).join(', ')}] produced no entry candidate for host ${query.host}.`,
        );
      }
      continue;
    }

    let branchesAdded = 0;
    let cycleSkipped = 0;
    for (const { resolver, result } of invocations) {
      for (const message of result.warnings ?? []) pushWarning(message);
      if (result.moves.length === 0) continue;

      // Deterministic candidate order; duplicate candidates inside one invocation collapse.
      const uniqueMoves = new Map<string, (typeof result.moves)[number]>();
      for (const move of result.moves) uniqueMoves.set(move.node.id, move);
      const moves = [...uniqueMoves.values()].sort((a, b) => a.node.id.localeCompare(b.node.id));

      const step: ResolutionStep = {
        resolver: resolver.name,
        inputNodeIds: branch.current ? [branch.current.id] : [],
        outputNodeIds: moves.map(move => move.node.id),
        rule: result.rule,
        confidence: result.confidence ?? 'UNKNOWN',
        evidence: result.evidence ?? [],
        warnings: result.warnings ?? [],
      };

      for (const move of moves) {
        if (branch.visited.has(move.node.id)) {
          // Cycle protection: a node may appear at most once per branch.
          cycleSkipped += 1;
          pushWarning(`Cycle guard: ${resolver.name} returned node ${move.node.id} already on this branch; candidate skipped.`);
          continue;
        }
        const next: Branch = {
          current: move.node,
          nodes: [...branch.nodes, move.node],
          steps: [...branch.steps, step],
          visited: new Set(branch.visited).add(move.node.id),
          depth: branch.depth + 1,
        };
        if (move.terminal) {
          if (emit(buildPath(next, move.node.id, null, null))) branchesAdded += 1;
        } else {
          queue.push(next);
          branchesAdded += 1;
        }
      }
    }

    if (producedMoves && branchesAdded === 0) {
      // Resolvers fired but every candidate was a cycle repeat (or the output
      // cap was hit, handled separately): retain the dead-end branch.
      if (cycleSkipped > 0) {
        emitUnresolved(
          emit,
          pushWarning,
          branch,
          STOP_REASON.CYCLE,
          `All candidates from node ${branch.current?.id ?? '(entry)'} were already visited on this branch.`,
        );
      }
    }
  }

  if (truncated) pushWarning(`Path enumeration stopped at maxPaths=${maxPaths}; further paths may exist.`);

  terminalPaths.sort(compareResolvedPaths);
  unresolvedPaths.sort(compareResolvedPaths);
  const paths = [...terminalPaths, ...unresolvedPaths];

  return {
    // Echo the effective environment so callers (CLI/UI) can display which
    // filter actually applied, even when it came via options rather than the query.
    query: environment ? { ...query, environment } : query,
    paths,
    status: deriveStatus(terminalPaths, unresolvedPaths),
    warnings,
  };
}

function emitUnresolved(
  emit: (path: ResolvedPath) => boolean,
  pushWarning: (message: string) => void,
  branch: Branch,
  reason: string,
  message: string,
): void {
  pushWarning(message);
  emit(buildPath(branch, null, branch.current?.id ?? null, { reason, message }));
}

/**
 * Status semantics (TASK-06 Step 2):
 * - RESOLVED   — the branch reliably reached an APPLICATION node (repository
 *                missing afterwards stays a warning, not a demotion);
 * - PARTIAL    — the chain made real progress but stopped before any
 *                APPLICATION because of missing data / unsupported semantics;
 * - UNRESOLVED — no valid chain could be established from the start;
 * - AMBIGUOUS  — a step could not be uniquely interpreted (weakest confidence).
 */
function buildPath(
  branch: Branch,
  terminalNodeId: string | null,
  stoppedAt: string | null,
  unresolved: { reason: string; message: string } | null,
): ResolvedPath {
  const confidence = branch.steps.reduce<Confidence>(
    (weakest, step) => (CONFIDENCE_RANK[step.confidence] < CONFIDENCE_RANK[weakest] ? step.confidence : weakest),
    'EXACT',
  );
  const stepWarnings = branch.steps.flatMap(step => step.warnings);
  const reachedApplication = branch.nodes.some(node => node.type === 'APPLICATION');
  if (!unresolved) {
    const status: ResolutionStatus = confidence === 'AMBIGUOUS' ? 'AMBIGUOUS' : 'RESOLVED';
    return {
      steps: branch.steps,
      nodes: branch.nodes,
      terminalNodeId,
      confidence,
      status,
      warnings: [...new Set(stepWarnings)],
    };
  }
  const status: ResolutionStatus = reachedApplication
    ? 'RESOLVED'
    : branch.steps.length > 0
      ? 'PARTIAL'
      : 'UNRESOLVED';
  return {
    steps: branch.steps,
    nodes: branch.nodes,
    terminalNodeId: null,
    confidence,
    status,
    ...(stoppedAt ? { stoppedAt } : {}),
    reason: unresolved.reason,
    warnings: [...new Set([...stepWarnings, unresolved.message])],
  };
}

function deriveStatus(terminal: ResolvedPath[], unresolved: ResolvedPath[]): ResolutionStatus {
  const paths = [...terminal, ...unresolved];
  if (terminal.length > 1 && new Set(terminal.map(path => path.terminalNodeId)).size > 1) return 'AMBIGUOUS';
  if (paths.some(path => path.status === 'AMBIGUOUS')) return 'AMBIGUOUS';
  // A chain that reliably reached an Application defines the outcome (even if
  // it later stopped before a repository); dead alternative branches (e.g. a
  // speculative NAT endpoint synthesis) stay visible as retained unresolved
  // paths with their diagnostics.
  if (paths.some(path => path.status === 'RESOLVED')) return 'RESOLVED';
  return paths.some(path => path.status === 'PARTIAL') ? 'PARTIAL' : 'UNRESOLVED';
}

function compareResolvedPaths(a: ResolvedPath, b: ResolvedPath): number {
  const byConfidence = CONFIDENCE_RANK[b.confidence] - CONFIDENCE_RANK[a.confidence];
  if (byConfidence) return byConfidence;
  if (a.steps.length !== b.steps.length) return a.steps.length - b.steps.length;
  const aEnd = a.terminalNodeId ?? a.stoppedAt ?? '';
  const bEnd = b.terminalNodeId ?? b.stoppedAt ?? '';
  return aEnd.localeCompare(bEnd);
}
