/* eslint-disable typescript/no-require-imports -- CommonJS Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');

test('topology uses current public cloud evidence, retains external static zones and isolates private DNS', async () => {
  const { buildValidatedTopology } = await import('../lib/topology/topology-builder.ts');
  const r = (id, zone, value, extra = {}) => ({ id, zone, name: zone, type: 'A', value, status: '启用', ...extra });
  const input = { devops: {apps:[]}, repositories:{repos:[]}, ecs:{instances:[]}, clb:{instances:[]},
    nat:{gateways:[]}, eip:{records:[]}, jumpserver:{assets:[]},
    dns:{records:[r('static-old','example.cn','192.0.2.1'),r('static-external','folidaymall.com','192.0.2.2')]},
    cloudDns:{available:true,zones:[
      {name:'example.cn',visibility:'public',records:[r('live','example.cn','192.0.2.3',{
        resourceKey:'tenant/public/live',tenantId:'a',accountId:'1001',observedAt:'2026-10-23T01:00:00Z'})]},
      {name:'example.cn',visibility:'private',records:[r('private','example.cn','10.0.0.1')]},
    ]}};
  const {topology,validation} = buildValidatedTopology(input);
  assert.equal(validation.valid,true);
  const dnsEdges = topology.edges.filter(e=>e.type==='RESOLVES_TO');
  assert.equal(dnsEdges.length,2);
  assert.ok(!topology.nodes.some(n=>n.identity.ip==='192.0.2.1'||n.identity.ip==='10.0.0.1'));
  const evidence=dnsEdges.flatMap(e=>e.evidence).find(e=>e.sourceId==='tenant/public/live');
  assert.equal(evidence.tenantId,'a');assert.equal(evidence.accountId,'1001');
  assert.equal(evidence.observedAt,'2026-10-23T01:00:00.000Z');
});
