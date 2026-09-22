import snapshot from './nat-snapshot.json';

export type DnatEntry = { id: string; tableId: string; name: string; externalIp: string; externalPort: string; internalIp: string; internalPort: string; protocol: string; status: string };
export type NatGateway = { id: string; name: string; status: string; vpcId: string; entries: DnatEntry[] };
export type NatSnapshot = { available: boolean; collectedAt: string | null; region: string; gateways: NatGateway[] };
export const nat = snapshot as NatSnapshot;
export const portLabel = (port: string) => port.toLowerCase() === 'any' || port === '-1' ? '全部端口' : port;
