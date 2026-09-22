import snapshot from './jumpserver-snapshot.json';

export const jumpserver = snapshot;
export const topGroups = snapshot.groups.filter((g) => g.parentKey === '1');
const buckets = new Map<string, number>();
for (const asset of snapshot.assets) {
  const matches = topGroups.filter((g) => g.assetIds.includes(asset.id));
  const name =
    matches.length > 1 ? '跨组服务器' : matches[0]?.name || '仅 Default';
  buckets.set(name, (buckets.get(name) || 0) + 1);
}
export const distribution = [...buckets]
  .map(([name, value]) => ({ name, value }))
  .sort((a, b) => b.value - a.value);
export const serverGroups: Array<
  (typeof snapshot.groups)[number] & { assets: typeof snapshot.assets }
> = snapshot.groups
  .filter((g) => g.parentKey !== null)
  .map((g) => ({
    ...g,
    assets: snapshot.assets.filter((a) => g.assetIds.includes(a.id)),
  }))
  .sort((a, b) => a.key.localeCompare(b.key, 'en', { numeric: true }));
const ungrouped = snapshot.assets.filter(
  (a) => !topGroups.some((g) => g.assetIds.includes(a.id)),
);
if (ungrouped.length)
  serverGroups.push({
    ...snapshot.groups.find((g) => g.key === '1')!,
    name: '仅 Default',
    path: '仅 Default',
    assets: ungrouped,
  });
