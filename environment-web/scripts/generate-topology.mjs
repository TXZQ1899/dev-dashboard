#!/usr/bin/env node
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { buildValidatedTopology } from '../lib/topology/topology-builder.ts';

const root = path.resolve(process.argv[2] || '.');
const lib = path.join(root, 'lib');
const output = path.join(root, 'outputs', 'topology', 'topology.json');

const files = {
  devops: 'snapshot.json',
  repositories: 'repositories.json',
  dns: 'dns-snapshot.json',
  eip: 'eip-snapshot.json',
  nat: 'nat-snapshot.json',
  ecs: 'ecs-snapshot.json',
  clb: 'clb-snapshot.json',
  jumpserver: 'jumpserver-snapshot.json',
};

const input = {};
for (const [key, file] of Object.entries(files)) {
  input[key] = JSON.parse(await readFile(path.join(lib, file), 'utf8'));
}

const { topology, validation } = buildValidatedTopology(input, new Date().toISOString());
await mkdir(path.dirname(output), { recursive: true });
await writeFile(output, `${JSON.stringify(topology, null, 2)}\n`, 'utf8');

console.log(`Generated ${path.relative(process.cwd(), output)}`);
console.log(`Nodes: ${topology.stats.nodeCount}`);
console.log(`Edges: ${topology.stats.edgeCount}`);
console.log(`Ambiguous edges: ${topology.stats.ambiguousEdges}`);
console.log(`Unresolved/external nodes: ${topology.stats.unresolvedNodes}`);
if (validation.errors.length) {
  console.error(`Validation failed with ${validation.errors.length} error(s):`);
  for (const issue of validation.errors.slice(0, 20)) console.error(`- ${issue.code}: ${issue.message}`);
  process.exitCode = 1;
} else {
  console.log('Validation passed');
  if (validation.warnings.length) console.log(`Warnings: ${validation.warnings.length}`);
}
