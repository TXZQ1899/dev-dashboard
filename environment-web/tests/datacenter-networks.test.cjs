/* eslint-disable typescript/no-require-imports -- Node test harness uses CommonJS, matching the existing suite. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const sandbox={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('lib/datacenter-networks.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,sandbox);
const {isLocalDatacenterIp}=sandbox.exports;

test('the four confirmed /24 segments classify as local datacenter',()=>{
  for(const ip of ['10.58.1.34','10.58.9.217','10.58.10.90','10.58.140.2','10.58.1.1','10.58.140.254'])
    assert.equal(isLocalDatacenterIp(ip),true);
});

test('other 10.58.x segments and other networks are not local',()=>{
  for(const ip of ['10.58.0.1','10.58.5.44','10.58.6.8','10.58.255.254','10.179.1.224','10.0.0.1','172.16.0.1','192.168.1.1','8.8.8.8'])
    assert.equal(isLocalDatacenterIp(ip),false);
});

test('boundaries and malformed inputs stay false',()=>{
  for(const ip of ['9.58.1.1','10.59.1.1','10.58.1','10.58.1.256','10.58.999.1','not-an-ip','',null,undefined])
    assert.equal(isLocalDatacenterIp(ip),false);
  assert.equal(isLocalDatacenterIp('  10.58.9.60  '),true);
});
