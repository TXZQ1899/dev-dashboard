import { isLocalDatacenterIp } from './datacenter-networks';
import type { Asset } from './server-processes';
import type { Instance } from './ecs';

export type LoginClass = 'elevated' | 'ordinary' | 'cannot_login' | 'unchecked' | 'elevation_unknown';
export type LocationClass = 'running' | 'stopped' | 'transition' | 'status_unknown' | 'local' | 'not_in_ecs' | 'ambiguous';
export type CoverageClass = 'managed' | 'unmatched' | 'ambiguous';
export type JumpOverviewRow = { asset: Asset; login: LoginClass; location: LocationClass; instance?: Instance; candidates: Instance[]; evidence: string };
export type EcsOverviewRow = { instance: Instance; coverage: CoverageClass; status: EcsStatus; asset?: Asset; candidates: Asset[]; evidence: string };
export type EcsStatus = 'running' | 'stopped' | 'transition' | 'status_unknown';

// Only valid IPv4 addresses are used as correlation evidence. Unrecognized
// address formats remain unresolved instead of producing a false negative.
function addresses(values: (string | null | undefined)[]) {
  const parts = values.flatMap(value => (value || '').split(/[\s,]+/)).filter(Boolean);
  const ips = new Set<string>(); let invalid = false;
  for (const part of parts) {
    const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(part);
    if (!match || match.slice(1).some(n => Number(n) > 255)) { invalid = true; continue; }
    ips.add(match.slice(1).map(Number).join('.'));
  }
  return { ips: [...ips], unresolved: !parts.length || invalid };
}
export function loginClass(asset: Asset): LoginClass {
  const inspection = asset.inspection;
  if (!inspection || !['can_login', 'cannot_login'].includes(inspection.loginStatus)) return 'unchecked';
  if (inspection.loginStatus === 'cannot_login') return 'cannot_login';
  if (['root', 'passwordless', 'password'].includes(inspection.sudoStatus || '')) return 'elevated';
  if (['denied', 'password_error', 'sudo_password_error', 'password_required'].includes(inspection.sudoStatus || '')) return 'ordinary';
  return 'elevation_unknown';
}
export function ecsStatus(status: string): EcsStatus {
  if (status === 'Running') return 'running';
  if (status === 'Stopped') return 'stopped';
  if (status === 'Starting' || status === 'Stopping') return 'transition';
  return 'status_unknown';
}
export function buildJumpserverOverview(assets: Asset[], instances: Instance[]) {
  const assetIps = assets.map(asset => addresses([asset.ip]));
  const instanceIps = instances.map(instance => addresses([...(instance.privateIps || []), ...(instance.publicIps || [])]));
  const assetByIp = new Map<string, number[]>(); const ecsByIp = new Map<string, number[]>();
  assetIps.forEach((item, i) => item.ips.forEach(ip => assetByIp.set(ip, [...(assetByIp.get(ip) || []), i])));
  instanceIps.forEach((item, i) => item.ips.forEach(ip => ecsByIp.set(ip, [...(ecsByIp.get(ip) || []), i])));
  const assetMatches = assetIps.map(item => [...new Set(item.ips.flatMap(ip => ecsByIp.get(ip) || []))]);
  const instanceMatches = instanceIps.map(item => [...new Set(item.ips.flatMap(ip => assetByIp.get(ip) || []))]);
  const ambiguous = (a: number, e: number) => assetIps[a].unresolved || instanceIps[e].unresolved || assetMatches[a].length !== 1 || instanceMatches[e].length !== 1 || assetIps[a].ips.some(isLocalDatacenterIp) || instanceIps[e].ips.some(isLocalDatacenterIp);
  const jump: JumpOverviewRow[] = assets.map((asset, i) => {
    const matches = assetMatches[i], local = assetIps[i].ips.some(isLocalDatacenterIp);
    const candidates = matches.map(index => instances[index]);
    if (assetIps[i].unresolved || matches.length > 1 || (matches.length === 1 && ambiguous(i, matches[0]))) return {asset, login:loginClass(asset), location:'ambiguous', candidates, evidence: '地址缺失、格式未知、多候选或本地机房与 ECS 证据冲突'};
    if (!matches.length) return {asset, login:loginClass(asset), location: local ? 'local' : 'not_in_ecs', candidates, evidence: local ? '命中已确认本地机房网段' : '当前 ECS 清单没有匹配地址'};
    const instance = instances[matches[0]];
    return {asset, login:loginClass(asset), location:ecsStatus(instance.status), instance, candidates, evidence:'JumpServer 地址与 ECS 地址唯一匹配'};
  });
  const ecs: EcsOverviewRow[] = instances.map((instance, i) => {
    const matches = instanceMatches[i], candidates = matches.map(index => assets[index]);
    const localConflict = instanceIps[i].ips.some(isLocalDatacenterIp);
    if (instanceIps[i].unresolved || localConflict || matches.length > 1 || (matches.length === 1 && ambiguous(matches[0], i))) return {instance, status:ecsStatus(instance.status), coverage:'ambiguous', candidates, evidence:'地址缺失、格式未知、多候选或本地机房与 ECS 证据冲突'};
    if (!matches.length) return {instance, status:ecsStatus(instance.status), coverage:'unmatched', candidates, evidence:'当前账号可见的 JumpServer 清单没有匹配地址'};
    return {instance, status:ecsStatus(instance.status), coverage:'managed', asset:assets[matches[0]], candidates, evidence:'ECS 地址与 JumpServer 地址唯一匹配'};
  });
  return {jump, ecs};
}
