#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildValidatedTopology } from '../lib/topology/topology-builder.ts';
import { routeHeaders, frontendHeaders, routeRows, frontendRows, csv } from '../lib/nginx-export.mjs';
import { backendHeaders, backendRows } from '../lib/nginx-backend-export.mjs';
import { createTwoSheetWorkbook } from '../lib/xlsx.ts';

const [snapshot, staticData, output, sourceVersion, exportFolder] = process.argv.slice(2);
if (!snapshot || !staticData || !output || !sourceVersion) throw new Error('Missing export paths or source version');
const files = {
  devops: [snapshot, 'snapshot.json'],
  repositories: [snapshot, 'repositories.json'],
  dns: [staticData, 'dns-snapshot.json'],
  cloudDns: [snapshot, 'cloud-dns-snapshot.json', { available: false, zones: [] }],
  eip: [staticData, 'eip-snapshot.json'],
  nat: [snapshot, 'nat-snapshot.json', { available: false, gateways: [] }],
  ecs: [snapshot, 'ecs-snapshot.json'],
  clb: [snapshot, 'clb-snapshot.json', { available: false, instances: [] }],
  jumpserver: [snapshot, 'jumpserver-snapshot.json'],
  ...(exportFolder ? {
    privateDnsConfirmations: [exportFolder, 'private-dns-confirmations.json', []],
    invalidProxyEndpoints: [exportFolder, 'invalid-proxy-endpoints.json', []],
  } : {}),
};
const input = Object.fromEntries(await Promise.all(Object.entries(files).map(async ([key, [folder, name, fallback]]) => {
  try {
    return [key, JSON.parse(await readFile(path.join(folder, name), 'utf8'))];
  } catch (error) {
    if (error.code !== 'ENOENT' || fallback === undefined) throw error;
    return [key, fallback];
  }
})));

// A paused record with the same owner as an active record must not leak into
// the export via the topology builder's merged DOMAIN node.
const active = records => (records ?? []).filter(record => record.status === '启用');
input.dns.records = active(input.dns.records);
for (const zone of input.cloudDns.zones ?? []) zone.records = active(zone.records);

const { topology, validation } = buildValidatedTopology(input, new Date().toISOString());
if (validation.errors.length) throw new Error(`Snapshot topology validation failed (${validation.errors.length} errors)`);

const routes = routeRows(input.jumpserver);
const checkNginxArchive = (kind, candidates) => JSON.parse(execFileSync('python3', [
  fileURLToPath(new URL('./nginx_export_archive.py', import.meta.url)), path.dirname(snapshot), kind,
], { input: JSON.stringify(candidates), encoding: 'utf8', timeout: 30_000 }));
const backend = await backendRows(topology, input.jumpserver, input.devops, sourceVersion, {
  ecs: input.ecs, eip: input.eip, clb: input.clb, cloudDns: input.cloudDns,
  privateDnsConfirmations: input.privateDnsConfirmations, invalidProxyEndpoints: input.invalidProxyEndpoints,
  loadServerFallbacks: candidates => checkNginxArchive('fallbacks', candidates),
  loadStaticLocations: candidates => checkNginxArchive('static', candidates),
  loadNginxStops: candidates => checkNginxArchive('stops', candidates),
  loadRequestHints: candidates => checkNginxArchive('requests', candidates),
});
const frontend = [...frontendRows(input.jumpserver), ...backend.frontend];
const workbook = createTwoSheetWorkbook(
  { name: '后端应用清单', rows: [backendHeaders, ...backend.rows],
    widths: [21,42,38,45,38,51,27,23,14,30,34,51,32,22,16,17,25,30,25,26,44,17,22,42,28,26,30,30,25,25,80,17,17,20,64,80,28,52,24,28] },
  { name: '前端应用清单', rows: [frontendHeaders, ...frontend], widths: [21,42,38,45,36,58,58,27] },
);
await Promise.all([
  writeFile(path.join(output, 'nginx-routes-full.csv'), csv([routeHeaders, ...routes]), { mode: 0o600 }),
  writeFile(path.join(output, 'nginx-applications.xlsx'), workbook, { mode: 0o600 }),
]);
process.stdout.write(JSON.stringify({ routeRows: routes.length, frontendRows: frontend.length,
  proxiedFrontendRows: backend.frontend.length, ...backend.counts }));
