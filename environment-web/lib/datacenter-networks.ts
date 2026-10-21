// On-premises datacenter segments confirmed by ops (2026-10-01):
// only these four /24 segments are local datacenter servers. Every other
// internal IP — including other 10.58.x segments such as 10.58.5.x —
// belongs to Alibaba Cloud or other infrastructure, not the local site.
export const LOCAL_DATACENTER_SUBNETS = [1, 9, 10, 140] as const;

export const LOCAL_DATACENTER_LABEL = '10.58.1 / 10.58.9 / 10.58.10 / 10.58.140';

export function isLocalDatacenterIp(raw: string | null | undefined): boolean {
  const value = (raw || '').trim();
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(value);
  if (!match) return false;
  const parts = match.slice(1).map(Number);
  if (parts.some((n) => n > 255)) return false;
  return (
    parts[0] === 10 &&
    parts[1] === 58 &&
    (LOCAL_DATACENTER_SUBNETS as readonly number[]).includes(parts[2])
  );
}

// 资源归属标签（2026-10-01 用户确认）：四个网段为本地机房，
// 其余内网基础设施（含 10.58.5.x 等其它 10.58 网段）一律阿里云 ECS。
export const RESOURCE_OWNER_LOCAL = '本地机房';
export const RESOURCE_OWNER_ALIYUN = '阿里云ECS';

export function resourceOwnerLabel(raw: string | null | undefined): string {
  return isLocalDatacenterIp(raw)
    ? RESOURCE_OWNER_LOCAL
    : RESOURCE_OWNER_ALIYUN;
}
