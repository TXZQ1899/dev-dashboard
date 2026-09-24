/**
 * DeploymentResolver (TASK-05 Steps 1-3).
 *
 * Resolves the application owning a backend endpoint using DevOps deployment
 * data:
 *
 * 1. ENDPOINT -> DEPLOYMENT (`deployment:endpoint`): match the endpoint's
 *    IP/port against DEPLOYMENT nodes. Precedence mirrors the task spec:
 *      Level 1  IP + Port + Environment  -> EXACT   (unique)
 *      Level 2  IP + Port (no env)       -> EXACT   (unique)
 *      Level 3  IP only, single deploy   -> INFERRED
 *      Level 4  IP only, multiple        -> AMBIGUOUS
 *    Port matching is strict numeric equality; an unknown/any endpoint port or
 *    a non-numeric deployment port falls back to host-wide (IP-only) matching.
 *    When the query carries an environment, only deployments of that
 *    environment are considered; an empty env-filtered pool returns no
 *    candidates rather than leaking a foreign-environment deployment.
 * 2. DEPLOYMENT -> APPLICATION (`deployment:application`): follow the reverse
 *    HAS_DEPLOYMENT edge. The APPLICATION move is intentionally not terminal so
 *    the RepositoryResolver (TASK-05 Step 4) can extend the chain to the
 *    source repository.
 *
 * The resolver scans DEPLOYMENT nodes directly (cached by IP per GraphIndex)
 * rather than relying on the endpoint's LISTENS_ON edges, because a backend
 * endpoint reached through nginx/CLB carries a concrete protocol (e.g. http)
 * while the DevOps endpoint identity uses protocol 'unknown', so the node ids
 * differ even though ip:port is the same.
 */
import { normalizeIp, normalizePort, text } from '../../common.ts';
import type { Confidence, Evidence, TopologyNode } from '../../types.ts';
import type { GraphIndex } from '../../path-explorer.ts';
import {
  RESERVED_RESOLVER_NAMES,
  type PathResolver,
  type ResolutionResult,
  type ResolverContext,
} from '../resolver.ts';

const MAX_STEP_EVIDENCE = 10;
const CONFIDENCE_RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };

function capEvidence(evidence: Evidence[], warnings: string[]): Evidence[] {
  if (evidence.length <= MAX_STEP_EVIDENCE) return evidence;
  warnings.push(`Step evidence truncated to the first ${MAX_STEP_EVIDENCE} of ${evidence.length} records.`);
  return evidence.slice(0, MAX_STEP_EVIDENCE);
}

// ---------------------------------------------------------------------------
// Deployment lookup cache (WeakMap per GraphIndex, mirroring endpoint.ts)
// ---------------------------------------------------------------------------

const deploymentsByIpCache = new WeakMap<GraphIndex, Map<string, TopologyNode[]>>();

/** DEPLOYMENT nodes grouped by normalized IP, built once per GraphIndex. */
function deploymentsByIp(index: GraphIndex, ip: string): TopologyNode[] {
  let byIp = deploymentsByIpCache.get(index);
  if (!byIp) {
    byIp = new Map<string, TopologyNode[]>();
    for (const node of index.topology.nodes) {
      if (node.type !== 'DEPLOYMENT') continue;
      const deploymentIp = normalizeIp(node.identity.ip);
      if (!deploymentIp) continue;
      const list = byIp.get(deploymentIp);
      if (list) list.push(node);
      else byIp.set(deploymentIp, [node]);
    }
    deploymentsByIpCache.set(index, byIp);
  }
  return byIp.get(normalizeIp(ip)) ?? [];
}

// ---------------------------------------------------------------------------
// Resolver
// ---------------------------------------------------------------------------

export class DeploymentResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.DEPLOYMENT;

  canResolve(context: ResolverContext): boolean {
    const node = context.current;
    if (!node) return false;
    if (node.type === 'DEPLOYMENT') return true;
    if (node.type === 'ENDPOINT') return Boolean(normalizeIp(node.identity.ip));
    return false;
  }

  resolve(context: ResolverContext): ResolutionResult {
    const node = context.current;
    if (!node) return { rule: 'deployment:none', moves: [] };
    switch (node.type) {
      case 'ENDPOINT':
        return this.resolveEndpointToDeployment(context, node);
      case 'DEPLOYMENT':
        return this.resolveDeploymentToApplication(context, node);
      default:
        return { rule: 'deployment:none', moves: [] };
    }
  }

  // Step 1-2: ENDPOINT -> DEPLOYMENT by IP (+ port + environment precedence).
  private resolveEndpointToDeployment(context: ResolverContext, endpoint: TopologyNode): ResolutionResult {
    const ip = normalizeIp(endpoint.identity.ip);
    const candidates = deploymentsByIp(context.index, ip);
    if (candidates.length === 0) {
      return {
        rule: 'deployment:endpoint',
        moves: [],
        warnings: [`No DevOps deployment is recorded for host IP ${ip}; application cannot be resolved from endpoint ${endpoint.id}.`],
      };
    }

    const endpointPort = normalizePort(endpoint.identity.port);
    const hasNumericPort = endpointPort !== 'unknown' && endpointPort !== 'any' && /^\d+$/.test(endpointPort);

    let pool: TopologyNode[];
    let usedPortMatch: boolean;
    if (hasNumericPort) {
      // The endpoint carries a concrete arrival port (e.g. a CLB
      // BackendServerPort like 80 on a shared nginx host): only deployments on
      // that exact port are eligible. Falling back to every host deployment
      // here would explode into dozens of AMBIGUOUS branches on shared hosts
      // and mask the real nginx chain; an unmatched port is retained as an
      // unresolved gap instead.
      const exact = candidates.filter(deployment => normalizePort(deployment.identity.port) === endpointPort);
      if (exact.length === 0) {
        const unknownPortCount = candidates.filter(d => normalizePort(d.identity.port) === 'unknown').length;
        return {
          rule: 'deployment:endpoint',
          moves: [],
          warnings: [
            `No DevOps deployment on host ${ip} listens on port ${endpointPort}; ` +
              `${candidates.length} deployment(s) on this host use other ports${unknownPortCount ? ` (${unknownPortCount} with unknown ports)` : ''}, ` +
              `e.g. a shared nginx/process host; the application cannot be inferred by host IP alone.`,
          ],
        };
      }
      pool = exact;
      usedPortMatch = true;
    } else {
      // Port-unknown endpoint: host-wide (IP-only) inference is the best V1 can do.
      pool = candidates;
      usedPortMatch = false;
    }

    // Environment filter: only the requested environment is eligible.
    const queryEnv = context.environment;
    if (queryEnv) {
      const envFiltered = pool.filter(deployment => deployment.environment === queryEnv);
      if (envFiltered.length === 0) {
        return {
          rule: 'deployment:endpoint',
          moves: [],
          warnings: [
            `No DevOps deployment on ${ip}${usedPortMatch ? `:${endpointPort}` : ''} matches environment ${queryEnv}; the host has deployments in other environments only.`,
          ],
        };
      }
      pool = envFiltered;
    }

    // Confidence follows the precedence table; multiple candidates stay AMBIGUOUS.
    const confidence: Confidence =
      pool.length > 1 ? 'AMBIGUOUS' : usedPortMatch ? 'EXACT' : 'INFERRED';

    const warnings: string[] = [];
    const evidence: Evidence[] = [...endpoint.evidence];
    for (const deployment of pool) evidence.push(...deployment.evidence);

    if (pool.length > 1) {
      const detail = usedPortMatch
        ? `${pool.length} deployments on ${ip}:${endpointPort}`
        : `${pool.length} deployments on host ${ip} (port ${endpointPort} is unknown or unmatched)`;
      warnings.push(`${detail} equally match endpoint ${endpoint.id}; all are kept as AMBIGUOUS candidates.`);
    } else if (!usedPortMatch) {
      warnings.push(
        `Endpoint ${endpoint.id} port ${endpointPort} has no exact numeric port match; deployment ${pool[0].id} inferred by host IP only (INFERRED).`,
      );
    }

    return {
      rule: `deployment:endpoint:${usedPortMatch ? 'ip-port' : 'ip-only'}`,
      confidence,
      evidence: capEvidence(evidence, warnings),
      moves: pool
        .sort((a, b) => a.id.localeCompare(b.id))
        .map(deployment => ({ node: deployment })),
      warnings,
    };
  }

  // Step 3: DEPLOYMENT -> APPLICATION via reverse HAS_DEPLOYMENT edge.
  private resolveDeploymentToApplication(context: ResolverContext, deployment: TopologyNode): ResolutionResult {
    const warnings: string[] = [];
    const evidence: Evidence[] = [...deployment.evidence];
    const moves = [];
    let weakest: Confidence = 'EXACT';

    for (const step of context.index.adjacency.get(deployment.id) ?? []) {
      if (step.direction !== 'reverse') continue;
      if (step.edge.type !== 'HAS_DEPLOYMENT') continue;
      if (!context.edgeAllowed(step.edge)) continue;
      const application = context.index.nodeById.get(step.neighborId);
      if (application?.type !== 'APPLICATION') continue;
      moves.push({ node: application });
      evidence.push(...step.edge.evidence, ...application.evidence);
      if (CONFIDENCE_RANK[step.edge.confidence] < CONFIDENCE_RANK[weakest]) weakest = step.edge.confidence;
    }

    // Fallback when the HAS_DEPLOYMENT edge is absent: derive from appId identity.
    if (moves.length === 0) {
      const appId = text(deployment.identity.appId);
      if (appId) {
        const application = context.index.nodeById.get(`application:${appId}`);
        if (application?.type === 'APPLICATION') {
          moves.push({ node: application });
          evidence.push(...application.evidence);
          weakest = 'INFERRED';
          warnings.push(`Deployment ${deployment.id} has no HAS_DEPLOYMENT edge; application ${application.id} derived from appId identity (INFERRED).`);
        }
      }
    }

    if (moves.length === 0) {
      return {
        rule: 'deployment:application',
        moves: [],
        warnings: [`Deployment ${deployment.id} cannot be linked to an APPLICATION node.`],
      };
    }

    if (moves.length > 1) {
      warnings.push(`${moves.length} applications are linked to deployment ${deployment.id}; all are kept as candidates.`);
      weakest = 'AMBIGUOUS';
    }

    return {
      rule: 'deployment:application',
      confidence: weakest,
      evidence: capEvidence(evidence, warnings),
      // Not terminal: RepositoryResolver extends the chain to the repository.
      moves: moves.sort((a, b) => a.node.id.localeCompare(b.node.id)),
      warnings,
    };
  }
}
