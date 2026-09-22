const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const output = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../lib/inventory.ts'), 'utf8'),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      esModuleInterop: true,
    },
  },
).outputText;
const sandbox = { URL, exports: {}, require: () => require('../lib/snapshot.json') };
vm.runInNewContext(output, sandbox);
const { info, apps, stats, singles } = sandbox.exports;
test('duplicate static deployment is displayed once; distinct deployments and metadata remain', () => {
  const first = {ip:'10.179.1.224',deploy:'16411',config:'14703',status:'成功',error:'',port:'8001',lastPublishedAt:'2026-08-28 11:04:20'};
  const duplicate = Object.fromEntries(Object.entries(first).reverse());
  const raw = [first, duplicate, {...first,deploy:'16412'}, {...first,config:'14704'}, {...first,port:'8002'}, {...first,lastPublishedAt:'2026-08-29 11:04:20'}];
  assert.equal(sandbox.exports.uniqueDeployments(raw).length, 5);
  assert.equal(raw.length, 6);
  const actual = apps.find(a => a.name === 'static');
  if (actual) assert.equal(actual.envs.TEST.filter(r=>r.ip==='10.179.1.224'&&r.deploy==='16411').length,1);
});
const row = (ip, status = '成功') => ({
  ip,
  status,
  deploy: '1',
  config: '',
  error: '',
});
const app = (rows) => ({ envs: { PRODUCT: rows } });
test('snapshot IDs and counts remain consistent after daily updates', () => {
  const raw = require('../lib/snapshot.json');
  assert.equal(apps.length, raw.apps.length);
  assert.equal(new Set(apps.map((a) => a.id)).size, apps.length);
  for (const s of stats) {
    const ips = new Set(
      raw.apps.flatMap((a) => a.envs[s.env].map((r) => r.ip).filter(Boolean)),
    );
    assert.equal(s.servers, ips.size);
  }
  for (const a of singles) assert.equal(info(a, 'PRODUCT').ips.length, 1);
});
test('different deployments on same IP remain a single server', () => {
  const r = info(app([row('10.0.0.1'), {...row('10.0.0.1'), deploy:'2'}]), 'PRODUCT');
  assert.equal(r.ips.length, 1);
  assert.equal(r.rows.length, 2);
  assert.equal(r.single, true);
});
test('multiple distinct IPs are not single points', () => {
  assert.equal(
    info(app([row('10.0.0.1'), row('10.0.0.2')]), 'PRODUCT').single,
    false,
  );
});
test('failed, missing IP, or absent rows are unknown, not confirmed single points', () => {
  for (const rows of [
    [],
    [row('')],
    [row('10.0.0.1'), row('', '失败')],
    [row('10.0.0.1', '失败')],
  ]) {
    const r = info(app(rows), 'PRODUCT');
    assert.equal(r.unknown, true);
    assert.equal(r.single, false);
  }
});

test('shared production servers match snapshot and each application appears once per server', () => {
  const { sharedProductionServers, sharedProductionAppIds } = sandbox.exports;
  assert.ok(sharedProductionServers.every((s) => s.apps.length > 1));
  for (const server of sharedProductionServers) {
    assert.equal(
      new Set(server.apps.map((a) => a.id)).size,
      server.apps.length,
    );
    for (const app of server.apps)
      assert.equal(sharedProductionAppIds.has(app.id), true);
  }
});
test('repeated deployments do not create sharing; distinct IDs with the same name do', () => {
  const { sharedServersFor } = sandbox.exports;
  const first = {
    id: '1',
    name: 'same',
    envs: { PRODUCT: [row('10.0.0.1'), row('10.0.0.1')] },
  };
  const second = {
    id: '2',
    name: 'same',
    envs: { PRODUCT: [row('10.0.0.1')] },
  };
  assert.equal(sharedServersFor([first], 'PRODUCT').length, 0);
  assert.equal(sharedServersFor([first, second], 'PRODUCT')[0].apps.length, 2);
});

test('environment servers deduplicate applications and preserve IP-specific ports', () => {
  const { serversFor } = sandbox.exports;
  const first = {
    id: '1',
    name: 'pnrorder',
    port: '8001',
    envs: {
      TEST: [
        { ip: '10.0.0.1', port: '9001' },
        { ip: '10.0.0.1', port: '9001' },
        { ip: '10.0.0.1', port: '9002' },
        { ip: '10.0.0.2', port: '' },
        { ip: '' },
      ],
      SIMULATION: [],
      PRODUCT: [],
    },
  };
  const second = {
    id: '2',
    name: 'pnrorder',
    port: '',
    envs: { TEST: [{ ip: '10.0.0.1' }], SIMULATION: [], PRODUCT: [] },
  };
  const rows = serversFor([first, second], 'TEST');
  assert.equal(rows.length, 2);
  assert.equal(rows[0].apps.length, 2);
  assert.equal(JSON.stringify(rows[0].apps[0].ports), '["9001","9002"]');
  assert.equal(rows[0].apps[1].ports[0], '未提供端口');
  assert.equal(rows[1].apps[0].ports[0], '8001');
  assert.equal(serversFor([first, second], 'PRODUCT').length, 0);
});
test('environment combinations match exactly all eight possibilities',()=>{
 const envs=['TEST','SIMULATION','PRODUCT'];
 for(let mask=0;mask<8;mask++){
  const selected=envs.filter((_,i)=>mask&(1<<i));
  const app={envs:Object.fromEntries(envs.map(e=>[e,selected.includes(e)?[{ip:'10.0.0.1',status:'成功'}]:[{ip:'',status:'无环境配置'}]]))};
  assert.equal(sandbox.exports.matchesEnvironmentCombination(app,selected),true);
  assert.equal(sandbox.exports.matchesEnvironmentCombination(app,null),true);
  for(let other=0;other<8;other++)assert.equal(sandbox.exports.matchesEnvironmentCombination(app,envs.filter((_,i)=>other&(1<<i))),mask===other);
 }
 const unknown={envs:{TEST:[{status:'读取失败',ip:''}],SIMULATION:[{status:'无环境配置'}],PRODUCT:[{status:'无环境配置'}]}};
 assert.equal(sandbox.exports.matchesEnvironmentCombination(unknown,[]),false);
});
test('deployment time uses latest successful Push In across servers, ignoring newer failures',()=>{
 const app={envs:{TEST:[{publishStatus:'FAILED',lastPublishedAt:'2026-09-14 10:00:00',pushIn:[{operation:'Push In',status:'SUCCESS',endTime:'2026-09-01 10:00:00'},{operation:'Deploy',status:'SUCCESS',endTime:'2026-09-14 11:00:00'}]},{publishStatus:'SUCCESS',lastPublishedAt:'2026-09-07 12:00:00'}],PRODUCT:[],SIMULATION:[]}};
 const f=sandbox.exports.matchesDeploymentPeriod,now='2026-09-14T12:00:00+08:00';
 assert.equal(sandbox.exports.latestSuccessfulPushIn(app,'TEST'),'2026-09-07 12:00:00');
 assert.equal(f(app,'TEST','week',now),true);assert.equal(f(app,'TEST','fortnight',now),true);
 assert.equal(f(app,'PRODUCT','older',now),false);
 app.envs.TEST=[{ip:'10.0.0.1',publishStatus:'SUCCESS',lastPublishedAt:'2025-09-14 12:00:00'}];
 assert.equal(f(app,'TEST','year',now),true);assert.equal(f(app,'TEST','older',now),false);
 app.envs.TEST[0].lastPublishedAt='2025-09-14 11:59:59';assert.equal(f(app,'TEST','older',now),true);
 app.envs.TEST[0].publishStatus='FAILED';assert.equal(f(app,'TEST','older',now),true);
});

test('missing successful Push In on a known server is treated as over-year undeployed',()=>{
 const f=sandbox.exports.matchesDeploymentPeriod;
 const status=sandbox.exports.overYearUndeployed;
 const app={envs:{TEST:[{ip:'10.0.0.1',publishStatus:'无运行记录'}],PRODUCT:[],SIMULATION:[]}};
 assert.equal(status(app,'TEST','2026-09-14T12:00:00+08:00'),true);
 assert.equal(f(app,'TEST','older','2026-09-14T12:00:00+08:00'),true);
 assert.equal(status({...app,envs:{...app.envs,TEST:[{ip:'',publishStatus:'无运行记录'}]}},'TEST','2026-09-14T12:00:00+08:00'),false);
});

test('default Push In time is the latest success across all environments',()=>{
 const app={envs:{TEST:[{publishStatus:'SUCCESS',lastPublishedAt:'2026-09-14 10:00:00'}],PRODUCT:[{publishStatus:'SUCCESS',lastPublishedAt:'2026-09-15 10:00:00'}],SIMULATION:[{publishStatus:'FAILED',lastPublishedAt:'2026-09-16 10:00:00'}]}};
 assert.equal(sandbox.exports.latestSuccessfulPushIn(app),'2026-09-15 10:00:00');
 assert.equal(sandbox.exports.latestSuccessfulPushIn(app,'TEST'),'2026-09-14 10:00:00');
 assert.equal(sandbox.exports.latestSuccessfulPushIn({envs:{TEST:[],PRODUCT:[],SIMULATION:[]}}),'');
});

test('repository categories follow Git hosts for HTTP and SSH addresses',()=>{
 const classify=sandbox.exports.repositoryCategory;
 assert.equal(classify('http://gitlab.dev.thomascook.com.cn/team/repo.git'),'Local Gitlab');
 assert.equal(classify('git@gitlab.dev.thomascook.com.cn:team/repo.git'),'Local Gitlab');
 assert.equal(classify('https://codeup.aliyun.com/team/repo.git'),'阿里 云效');
 assert.equal(classify('ssh://git@code.aliyun.com/team/repo.git'),'地址失效');
 assert.equal(classify('https://codeup.aliyun.com.example.org/repo.git'),'其他');
 assert.equal(classify('cat'),'其他');
 assert.equal(classify(undefined),'未提供');
});
