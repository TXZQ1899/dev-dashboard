/**
 * RepositoryResolver (TASK-05 Step 4).
 *
 * Extends the request chain from APPLICATION to its source REPOSITORY:
 *
 *   APPLICATION --BUILT_FROM--> REPOSITORY (terminal)
 *
 * The BUILT_FROM edge is emitted by the topology builder with EXACT
 * confidence, but the underlying DevOps→Codeup repository match may be
 * uncertain: a REPOSITORY node whose status is `unresolved` was declared only
 * by DevOps and never matched against the Codeup/GitLab inventory. Per the
 * task ("如果 DevOps/Codeup 当前 match 本身不确定：不能强制设置 EXACT"), such
 * branches are downgraded to INFERRED so confidence stays honest.
 *
 * The REPOSITORY move is terminal: it is the last hop of the
 * Endpoint→Deployment→Application→Repository chain. Applications without a
 * repository leave the branch unresolved at the APPLICATION node (the
 * application is still present in the path nodes).
 */
import type { Confidence, Evidence, TopologyNode } from '../../types.ts';
import {
  RESERVED_RESOLVER_NAMES,
  type PathResolver,
  type ResolutionResult,
  type ResolverContext,
} from '../resolver.ts';

const MAX_STEP_EVIDENCE = 10;

function capEvidence(evidence: Evidence[], warnings: string[]): Evidence[] {
  if (evidence.length <= MAX_STEP_EVIDENCE) return evidence;
  warnings.push(`Step evidence truncated to the first ${MAX_STEP_EVIDENCE} of ${evidence.length} records.`);
  return evidence.slice(0, MAX_STEP_EVIDENCE);
}

export class RepositoryResolver implements PathResolver {
  readonly name = RESERVED_RESOLVER_NAMES.REPOSITORY;

  canResolve(context: ResolverContext): boolean {
    return context.current?.type === 'APPLICATION';
  }

  resolve(context: ResolverContext): ResolutionResult {
    const application = context.current;
    if (!application) return { rule: 'repository:none', moves: [] };

    const warnings: string[] = [];
    const evidence: Evidence[] = [...application.evidence];
    const moves = [];
    let weakest: Confidence = 'EXACT';

    for (const step of context.index.adjacency.get(application.id) ?? []) {
      if (step.direction !== 'forward') continue;
      if (step.edge.type !== 'BUILT_FROM') continue;
      if (!context.edgeAllowed(step.edge)) continue;
      const repository = context.index.nodeById.get(step.neighborId);
      if (repository?.type !== 'REPOSITORY') continue;

      // A devops-only repository (no Codeup match) is uncertain: downgrade.
      const edgeConfidence: Confidence =
        repository.status === 'unresolved' ? 'INFERRED' : step.edge.confidence;
      moves.push({ node: repository, terminal: true });
      evidence.push(...step.edge.evidence, ...repository.evidence);
      weakest = rank(edgeConfidence) < rank(weakest) ? edgeConfidence : weakest;
    }

    if (moves.length === 0) {
      return {
        rule: 'repository:none',
        moves: [],
        warnings: [`Application ${application.id} has no BUILT_FROM edge to a REPOSITORY node; repository metadata is unavailable.`],
      };
    }

    if (moves.length > 1) {
      warnings.push(`${moves.length} repositories are linked to application ${application.id}; all are kept as candidates.`);
      weakest = 'AMBIGUOUS';
    }

    return {
      rule: 'repository:built-from',
      confidence: weakest,
      evidence: capEvidence(evidence, warnings),
      moves: moves.sort((a, b) => a.node.id.localeCompare(b.node.id)),
      warnings,
    };
  }
}

const CONFIDENCE_RANK: Record<Confidence, number> = { EXACT: 3, INFERRED: 2, AMBIGUOUS: 1, UNKNOWN: 0 };
function rank(confidence: Confidence): number {
  return CONFIDENCE_RANK[confidence];
}
