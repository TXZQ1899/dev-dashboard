/**
 * Request-aware path resolution framework (TASK-01).
 *
 * Framework only — DNS/NAT/CLB/Nginx/Deployment/Repository resolver business
 * logic arrives in later tasks.
 */
export { normalizeRequestQuery } from './query.ts';
export type { PathQuery, PathQueryInput, RequestEnvironment, RequestScheme } from './query.ts';
export type { RequestTrace, ResolvedPath, ResolutionStatus, ResolutionStep } from './models.ts';
export {
  ResolverRegistry,
  RESERVED_RESOLVER_NAMES,
} from './resolver.ts';
export type { PathResolver, ResolverContext, ResolutionResult, ResolutionMove } from './resolver.ts';
export {
  resolveRequestPath,
  resolveRequestPathWithIndex,
  DEFAULT_MAX_DEPTH,
  DEFAULT_MAX_PATHS,
  STOP_REASON,
} from './engine.ts';
export type { ResolveOptions } from './engine.ts';
export { DnsResolver } from './resolvers/dns.ts';
export { NatResolver } from './resolvers/nat.ts';
export { ClbResolver } from './resolvers/clb.ts';
export { NginxResolver } from './resolvers/nginx.ts';
export { parseListenPort } from './resolvers/nginx.ts';
export { DeploymentResolver } from './resolvers/deployment.ts';
export { RepositoryResolver } from './resolvers/repository.ts';
export {
  makeEndpointId,
  matchDnatRules,
  parsePortRange,
  portMatches,
  protocolMatches,
  resolveEndpointNodes,
  transportFamily,
} from './endpoint.ts';
export type { DnatRuleMatch, EndpointResolution } from './endpoint.ts';
import { ResolverRegistry } from './resolver.ts';
import { DnsResolver } from './resolvers/dns.ts';
import { NatResolver } from './resolvers/nat.ts';
import { ClbResolver } from './resolvers/clb.ts';
import { NginxResolver } from './resolvers/nginx.ts';
import { DeploymentResolver } from './resolvers/deployment.ts';
import { RepositoryResolver } from './resolvers/repository.ts';

/**
 * Built-in registry in chain order: DNS first (host entry + CNAME/A hops), then
 * NAT (external endpoint + DNAT forwarding), then CLB (listener/rule/backend
 * resolution), then nginx (host → route → upstream → backend endpoints), then
 * deployment (endpoint → deployment → application), then repository
 * (application → repository, terminal). Pass it explicitly via ResolveOptions;
 * the engine default registry stays empty so framework-level behavior is
 * unchanged.
 */
export function createDefaultResolverRegistry(): ResolverRegistry {
  return new ResolverRegistry()
    .register(new DnsResolver())
    .register(new NatResolver())
    .register(new ClbResolver())
    .register(new NginxResolver())
    .register(new DeploymentResolver())
    .register(new RepositoryResolver());
}
