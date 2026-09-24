/**
 * NatResolver (TASK-02 Step 3/4).
 *
 * Two stages of the ingress chain:
 *
 * 1. EIP -> external endpoint: the logical `ip:queryPort/tcp` endpoint derived
 *    from the request (scheme/port) and the DNS-resolved EIP. An existing graph
 *    ENDPOINT node is preferred; a compatible same ip:port node is second; a
 *    logical endpoint is synthesized only when the graph has neither (and is
 *    never written back into the TopologyGraph).
 * 2. external endpoint -> internal endpoint via DNAT rules matched by
 *    externalIp + externalPort + protocol (exact port, `Any`, ranges, exact
 *    protocol, `Any`). All matches fan out as moves; selection is never random.
 *
 * Moves are never marked terminal: the chain must stay open for CLB (TASK-03),
 * nginx (TASK-04) and the APPLICATION terminal stage (TASK-05). Stopping here
 * is expressed as an unresolved path with a STOP_REASON, not as a fake RESOLVED.
 */
import { normalizeIp, normalizePort } from '../../common.ts';
import type { Confidence, Evidence, TopologyNode } from '../../types.ts';
import {
  RESERVED_RESOLVER_NAMES,
  type PathResolver,
  type ResolutionMove,
  type ResolutionResult,
  type ResolverContext,
} from '../resolver.ts';
import { matchDnatRules, resolveEndpointNodes } from '../endpoint.ts';

const MAX_STEP_EVIDENCE = 10;

function capEvidence(evidence: Evidence[], warnings: string[]): Evidence[] {
  if (evidence.length <= MAX_STEP_EVIDENCE) return evidence;
  warnings.push(`Step evidence truncated to the first ${MAX_STEP_EVIDENCE} of ${evidence.length} records.`);
  return evidence.slice(0, MAX_STEP_EVIDENCE);
}

export class NatResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.NAT;

  canResolve(context: ResolverContext): boolean {
    const node = context.current;
    if (!node) return false;
    if (node.type === 'EIP') return Boolean(normalizeIp(node.identity.ip));
    // Only NAT-facing external endpoints participate in DNAT matching; nginx /
    // CLB endpoints and internal DNAT targets belong to other chain stages.
    return node.type === 'ENDPOINT' && node.attributes.natSide === 'external';
  }

  resolve(context: ResolverContext): ResolutionResult {
    const node = context.current;
    if (!node) return { rule: 'nat:none', moves: [] };
    return node.type === 'EIP' ? this.resolveExternalEndpoint(context, node) : this.resolveDnatForward(context, node);
  }

  /** EIP branch: derive the external endpoint for the request's own port/protocol. */
  private resolveExternalEndpoint(context: ResolverContext, eip: TopologyNode): ResolutionResult {
    const ip = normalizeIp(eip.identity.ip);
    if (!ip) {
      return {
        rule: 'nat:external-endpoint',
        moves: [],
        warnings: [`EIP node ${eip.id} carries no usable IP identity; no external endpoint can be derived.`],
      };
    }
    if (eip.status === 'paused') {
      return { rule: 'nat:external-endpoint', moves: [], warnings: [`EIP ${ip} is paused; it cannot carry active traffic.`] };
    }
    // V1: both http and https ride TCP at the transport layer.
    const protocol = 'tcp';
    const port = context.query.port;
    const warnings: string[] = [];
    const resolution = resolveEndpointNodes(context.index, ip, port, protocol, [...eip.evidence], 'external');
    if (resolution.synthesized) {
      warnings.push(
        `External endpoint ${ip}:${port}/${protocol} was derived from the request scheme/port and the DNS target; no matching ENDPOINT node exists in the topology graph.`,
      );
      return {
        rule: 'nat:external-endpoint',
        confidence: 'INFERRED',
        evidence: [...eip.evidence],
        moves: [{ node: resolution.synthesized }],
        warnings,
      };
    }
    if (!resolution.exact) {
      warnings.push(
        `External endpoint for ${ip}:${port} matched topology endpoint node(s) with a divergent protocol label: ${resolution.nodes.map(node => node.id).join(', ')}.`,
      );
    }
    if (resolution.nodes.length > 1) {
      warnings.push(`${resolution.nodes.length} endpoint nodes share ${ip}:${port}; all are kept as candidates (no random selection).`);
    }
    const confidence: Confidence = resolution.exact ? 'EXACT' : resolution.nodes.length > 1 ? 'AMBIGUOUS' : 'INFERRED';
    return {
      rule: 'nat:external-endpoint',
      confidence,
      evidence: [...eip.evidence],
      moves: resolution.nodes.map(node => ({ node })),
      warnings,
    };
  }

  /** External endpoint branch: fan out to internal endpoints across all matching DNAT rules. */
  private resolveDnatForward(context: ResolverContext, endpoint: TopologyNode): ResolutionResult {
    const ip = normalizeIp(endpoint.identity.ip);
    const portRaw = normalizePort(endpoint.identity.port);
    // The wanted transport comes from the request (V1: http/https ride TCP), not
    // from the endpoint's protocol label, which may be `unknown` (Any) in the graph.
    const protocol = 'tcp';
    if (!ip || !/^\d+$/.test(portRaw)) {
      return {
        rule: 'nat:dnat',
        moves: [],
        warnings: [`Endpoint ${endpoint.id} has no concrete ip:port; DNAT matching is skipped.`],
      };
    }
    const port = Number(portRaw);
    const matches = matchDnatRules(context.index, ip, port, protocol);
    if (matches.length === 0) {
      return {
        rule: 'nat:dnat',
        moves: [],
        warnings: [`No DNAT rule matches external endpoint ${ip}:${port}/${protocol}; the internal IP:port stays unresolved.`],
      };
    }
    const warnings: string[] = [];
    if (matches.length > 1) {
      warnings.push(
        `${matches.length} DNAT rules match ${ip}:${port}/${protocol} (${matches.map(match => match.rule.id).join(', ')}); all are kept as candidates.`,
      );
    }
    const evidence: Evidence[] = [];
    const moves: ResolutionMove[] = [];
    for (const match of matches) {
      evidence.push(...match.rule.evidence);
      const resolution = resolveEndpointNodes(
        context.index,
        match.internalIp,
        match.internalPort,
        match.protocol,
        [...match.rule.evidence],
        'internal',
      );
      const node = resolution.nodes[0] ?? resolution.synthesized;
      if (!node) continue;
      if (resolution.synthesized) {
        warnings.push(
          `Internal endpoint ${match.internalIp}:${match.internalPort}/${match.protocol} is not in the topology graph; a logical endpoint was derived from DNAT rule ${match.rule.id}.`,
        );
      }
      moves.push({ node });
    }
    const confidence: Confidence =
      matches.length > 1 ? 'AMBIGUOUS' : matches[0].specificity === 'exact' ? 'EXACT' : 'INFERRED';
    return { rule: 'nat:dnat', confidence, evidence: capEvidence(evidence, warnings), moves, warnings };
  }
}
