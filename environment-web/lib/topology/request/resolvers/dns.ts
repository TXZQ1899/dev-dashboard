/**
 * DNSResolver (TASK-02 Step 2).
 *
 * Resolves the request host to a DOMAIN node (entry stage) and follows
 * `CNAME_TO` (CNAME chains) and `RESOLVES_TO` (A/AAAA records) hops forward.
 *
 * Rules implemented here:
 * - multiple DNS candidates are all emitted as moves; the engine branches per
 *   candidate, so no random pick ever happens;
 * - cycle safety and depth limits are the engine's job (visited set / maxDepth);
 * - reliably paused (disabled) records never enter the active traffic path;
 * - external/unresolved CNAME targets are preserved as unresolved branches;
 * - every hop carries the DNS record evidence from its edge, and the step
 *   confidence is always set explicitly (weakest edge confidence).
 */
import type { Confidence, Evidence, TopologyNode } from '../../types.ts';
import { resolveNodes } from '../../path-explorer.ts';
import {
  RESERVED_RESOLVER_NAMES,
  type PathResolver,
  type ResolutionMove,
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

function domainLabel(node: TopologyNode): string {
  const name = typeof node.identity.name === 'string' ? node.identity.name : '';
  return name || node.label;
}

export class DnsResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.DNS;

  canResolve(context: ResolverContext): boolean {
    return context.current === null || context.current.type === 'DOMAIN';
  }

  resolve(context: ResolverContext): ResolutionResult {
    return context.current === null ? this.resolveEntry(context) : this.resolveDomain(context);
  }

  /** Entry: the queried host must match a DOMAIN node by name; all matches branch deterministically. */
  private resolveEntry(context: ResolverContext): ResolutionResult {
    const resolution = resolveNodes(context.index, 'domain', context.query.host);
    if (resolution.candidates.length === 0) {
      return {
        rule: 'dns:entry',
        moves: [],
        warnings: [`No DOMAIN node in the topology matches host "${context.query.host}"; DNS resolution cannot start.`],
      };
    }
    const moves: ResolutionMove[] = [];
    const warnings: string[] = [];
    for (const node of resolution.candidates) {
      // A reliably disabled record must never carry the active traffic path.
      if (node.status === 'paused') {
        warnings.push(`DNS record for ${domainLabel(node)} is paused (disabled); it cannot carry active traffic.`);
        continue;
      }
      moves.push({ node });
    }
    return {
      rule: 'dns:entry',
      confidence: 'EXACT',
      evidence: capEvidence(moves.flatMap(move => move.node.evidence), warnings),
      moves,
      warnings,
    };
  }

  /** DOMAIN branch: flat move list along forward CNAME_TO / RESOLVES_TO edges. */
  private resolveDomain(context: ResolverContext): ResolutionResult {
    const current = context.current;
    if (!current || current.status === 'paused') {
      return {
        rule: 'dns:records',
        moves: [],
        warnings: current
          ? [`DNS records for ${domainLabel(current)} are paused; resolution does not continue from them.`]
          : [],
      };
    }
    const moves: ResolutionMove[] = [];
    const warnings: string[] = [];
    const evidence: Evidence[] = [];
    let weakest: Confidence = 'EXACT';
    for (const step of context.index.adjacency.get(current.id) ?? []) {
      if (step.direction !== 'forward') continue;
      if (step.edge.type !== 'CNAME_TO' && step.edge.type !== 'RESOLVES_TO') continue;
      if (!context.edgeAllowed(step.edge)) {
        warnings.push(
          `Edge ${step.edge.id} (${step.edge.environment}) is excluded by the ${context.environment ?? 'GLOBAL'} environment filter.`,
        );
        continue;
      }
      const node = context.index.nodeById.get(step.neighborId);
      if (!node) continue;
      if (node.status === 'paused') {
        warnings.push(`DNS target ${domainLabel(node)} is paused; the ${step.edge.type} hop is kept out of the active path.`);
        continue;
      }
      moves.push({ node });
      evidence.push(...step.edge.evidence);
      if (CONFIDENCE_RANK[step.edge.confidence] < CONFIDENCE_RANK[weakest]) weakest = step.edge.confidence;
    }
    return {
      rule: 'dns:records',
      confidence: weakest,
      evidence: capEvidence(evidence, warnings),
      moves,
      warnings,
    };
  }
}
